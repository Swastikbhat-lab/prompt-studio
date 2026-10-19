"""Loopback-only HTTP surface for Prompt Studio."""

from __future__ import annotations

import argparse
import json
import mimetypes
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from prompt_studio.decisions import PromptGate
from prompt_studio.provider import OllamaProvider, ProviderError
from prompt_studio.workflow import PromptWorkflow

WEB = Path(__file__).with_name("web")
MAX_BODY_BYTES = 100_000


class PromptHandler(BaseHTTPRequestHandler):
    server_version = "PromptStudio/0.3"

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _host_is_local(self) -> bool:
        host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("http://" + host)
            address = ipaddress.ip_address(parsed.hostname or "")
            return address.is_loopback and parsed.port == self.server.server_port
        except (ValueError, TypeError):
            return False

    def _origin_is_local(self) -> bool:
        origin = self.headers.get("Origin", "")
        try:
            parsed = urlsplit(origin)
            address = ipaddress.ip_address(parsed.hostname or "")
            return (
                parsed.scheme == "http" and address.is_loopback
                and parsed.port == self.server.server_port
                and not parsed.username and not parsed.password
                and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
            )
        except (ValueError, TypeError):
            return False

    def do_GET(self) -> None:
        if not self._host_is_local():
            self._json(403, {"error": "Only loopback requests are allowed."})
            return
        if self.path == "/" or self.path == "/index.html":
            self._serve_file(WEB / "index.html")
        elif self.path in ("/app.js", "/styles.css", "/compiler.js"):
            self._serve_file(WEB / self.path.lstrip("/"))
        elif self.path == "/api/models":
            try:
                models = OllamaProvider().list_models()
                self._json(200, {"models": [{"name": m.name, "size_bytes": m.size_bytes} for m in models]})
            except ProviderError as exc:
                self._json(503, {"error": str(exc), "models": []})
        else:
            self._json(404, {"error": "Not found."})

    def _serve_file(self, path: Path) -> None:
        if not path.is_file() or path.parent != WEB:
            self._json(404, {"error": "Not found."})
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",):
            content_type += "; charset=utf-8"
        self._headers(200, content_type, len(body))
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._host_is_local() or not self._origin_is_local():
            self._json(403, {"error": "Request rejected: expected a same-origin loopback browser request."})
            return
        if self.path not in ("/api/generate", "/api/decision"):
            self._json(404, {"error": "Not found."})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "Send application/json."})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._json(413, {"error": "Request body is missing or exceeds 100 KB."})
            return
        try:
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object.")
            request = data.get("request", "")
            model = data.get("model", "")
            answers = data.get("answers", [])
            draft_now = data.get("draft_now", False)
            context = data.get("context", "")
            rounds = data.get("rounds", 0)
            strategy = data.get("strategy", "brief")
            force_rewrite = data.get("force_rewrite", False)
            if not isinstance(context, str) or len(context) > 8000:
                raise ValueError("Selected context must be text of at most 8,000 characters.")
            if not isinstance(request, str) or not isinstance(model, str) or not isinstance(answers, list) or not isinstance(draft_now, bool):
                raise ValueError("Request, model, answers or draft-now flag have invalid types.")
            if not isinstance(rounds, int) or isinstance(rounds, bool) or not 0 <= rounds <= 2:
                raise ValueError("Clarification rounds must be an integer between 0 and 2.")
            if not isinstance(strategy, str) or not isinstance(force_rewrite, bool):
                raise ValueError("Strategy or force-rewrite flag have invalid types.")
            if not all(isinstance(answer, dict) for answer in answers):
                raise ValueError("Each answer must be an object.")
            if len(answers) > 6:
                raise ValueError("Answer at most six clarifications across two rounds.")
            if any(not isinstance(answer.get(key, ""), str) for answer in answers for key in ("question", "answer")):
                raise ValueError("Clarification question and answer fields must be strings.")
            if any(len(answer.get("question", "")) > 2000 or len(answer.get("answer", "")) > 2000 for answer in answers):
                raise ValueError("Clarification questions and answers are limited to 2,000 characters each.")
            if self.path == "/api/decision":
                decision = PromptGate().evaluate(
                    request.strip(), context=context,
                    answers=[answer for answer in answers if answer.get("answer")],
                    draft_now=draft_now, rounds=rounds,
                )
                self._json(200, {"decision": decision.to_dict(), "network_used": False})
                return
            result = PromptWorkflow(OllamaProvider()).create_prompt(
                request, answers, model,
                draft_now=draft_now, context=context, rounds=rounds,
                strategy=strategy, force_rewrite=force_rewrite,
            )
            self._json(200, {
                "summary": result.draft.summary,
                "assumptions": result.draft.assumptions,
                "questions": [{"question": q.question, "reason": q.reason} for q in result.draft.questions],
                "prompt": result.draft.prompt,
                "model": result.model,
                "engine": result.engine,
                "decision": result.decision.to_dict() if result.decision else None,
                "elapsed_seconds": round(result.elapsed_seconds, 2),
                "answered_questions": result.answered_questions,
                "network_used": result.network_used,
            })
        except (ValueError, ProviderError, json.JSONDecodeError) as exc:
            self._json(400 if isinstance(exc, (ValueError, json.JSONDecodeError)) else 503, {"error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:
        # Avoid writing request bodies, prompts or source text to server logs.
        super().log_message(fmt, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local-only prompt engine UI.")
    parser.add_argument("--port", type=int, default=4177)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PromptHandler)
    server.daemon_threads = True
    print(f"Prompt Studio at http://127.0.0.1:{args.port} (loopback only; Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
