"""Model Context Protocol server for Prompt Studio.

One implementation, two transports:

* ``stdio`` — newline-delimited JSON-RPC. Used by Claude Code, Claude Desktop,
  Cursor and the ChatGPT desktop app.
* ``http`` — ``POST /mcp`` with either the legacy ``initialize`` handshake or
  the current stateless request shape. Used by remote connectors.

Only the Python standard library is required. The server never calls a cloud
provider; the optional writer talks to loopback Ollama.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .decisions import PromptGate
from .provider import OllamaProvider, ProviderError
from .workflow import PromptWorkflow

SERVER_NAME = "prompt-studio"
SERVER_VERSION = "0.4.0"
MAX_BODY_BYTES = 200_000
LEGACY_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
CURRENT_VERSIONS = ("2026-07-28",)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "prompt_gate",
        "description": (
            "Run the Jev System-1 gate on a request before spending any model call. "
            "Returns route (package | clarify | draft), specificity, ambiguity, context "
            "bloat, extracted constraints, probabilities and measured latency. Use this "
            "to decide whether a request is specific enough to compile deterministically."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "The task in the user's own words."},
                "context": {"type": "string", "description": "Optional task-only context, at most 8,000 characters."},
                "draft_now": {"type": "boolean", "description": "Finish now and state assumptions instead of asking."},
                "answered": {"type": "boolean", "description": "Treat the request as already clarified."},
                "rounds": {"type": "integer", "minimum": 0, "maximum": 2, "description": "Clarification rounds already used."},
            },
            "required": ["request"],
            "additionalProperties": False,
        },
    },
    {
        "name": "prompt_build",
        "description": (
            "Compile a reviewable prompt locally. The gate decides first: a specific "
            "request is packaged deterministically with no model call; an ambiguous one "
            "may return bounded questions or a draft with visible assumptions. Set "
            "force_rewrite to always use the local Ollama writer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "The task in the user's own words."},
                "context": {"type": "string", "description": "Optional task-only context, at most 8,000 characters."},
                "model": {"type": "string", "description": "Installed Ollama model; defaults to the first available."},
                "strategy": {
                    "type": "string",
                    "enum": ["original", "brief", "coding", "research", "writing", "decision"],
                    "description": "Fixed response-guidance pattern. Use 'original' to add nothing.",
                },
                "force_rewrite": {"type": "boolean", "description": "Always call the local writer."},
                "draft_now": {"type": "boolean", "description": "Do not ask questions; state assumptions."},
                "rounds": {"type": "integer", "minimum": 0, "maximum": 2},
            },
            "required": ["request"],
            "additionalProperties": False,
        },
    },
    {
        "name": "prompt_models",
        "description": "List the Ollama models installed on this machine.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class ToolInputError(ValueError):
    """A tools/call argument was missing or invalid."""


def _string(args: dict[str, Any], key: str, *, required: bool = False, limit: int = 20_000) -> str:
    value = args.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ToolInputError(f"'{key}' must be a string.")
    if required and not value.strip():
        raise ToolInputError(f"'{key}' is required.")
    if len(value) > limit:
        raise ToolInputError(f"'{key}' is limited to {limit:,} characters.")
    return value


def _rounds(args: dict[str, Any]) -> int:
    value = args.get("rounds", 0)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2:
        raise ToolInputError("'rounds' must be an integer between 0 and 2.")
    return value


def _first_model() -> str:
    try:
        models = OllamaProvider().list_models()
    except ProviderError:
        return ""
    return models[0].name if models else ""


def call_tool(name: str, args: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Return (payload, is_error) for one tool call."""

    if not isinstance(args, dict):
        raise ToolInputError("Tool arguments must be an object.")
    if name == "prompt_gate":
        request = _string(args, "request", required=True)
        context = _string(args, "context", limit=8_000)
        decision = PromptGate().evaluate(
            request,
            context=context,
            answers=[{"question": "previously-clarified", "answer": "yes"}] if args.get("answered") else [],
            draft_now=bool(args.get("draft_now", False)),
            rounds=_rounds(args),
        )
        return {"decision": decision.to_dict(), "network_used": False}, False

    if name == "prompt_build":
        request = _string(args, "request", required=True)
        context = _string(args, "context", limit=8_000)
        strategy = args.get("strategy", "brief")
        if strategy not in ("original", "brief", "coding", "research", "writing", "decision"):
            raise ToolInputError("'strategy' must be one of original, brief, coding, research, writing, decision.")
        force_rewrite = bool(args.get("force_rewrite", False))
        draft_now = bool(args.get("draft_now", False))
        rounds = _rounds(args)
        gate = PromptGate()
        decision = gate.evaluate(request, context=context, draft_now=draft_now, rounds=rounds)
        model = _string(args, "model", limit=200)
        needs_writer = force_rewrite or decision.route != "package"
        if needs_writer and not model:
            model = _first_model()
        if needs_writer and not model:
            return {
                "error": (
                    "No local Ollama model is available. Install one with "
                    "`ollama pull gemma2:9b`, or use prompt_gate plus a package-only request."
                ),
                "decision": decision.to_dict(),
            }, True
        result = PromptWorkflow(OllamaProvider(), gate).create_prompt(
            request,
            [],
            model,
            draft_now=draft_now,
            context=context,
            rounds=rounds,
            strategy=strategy,
            force_rewrite=force_rewrite,
        )
        return {
            "prompt": result.draft.prompt,
            "summary": result.draft.summary,
            "assumptions": result.draft.assumptions,
            "questions": [{"question": item.question, "reason": item.reason} for item in result.draft.questions],
            "engine": result.engine,
            "model": result.model,
            "elapsed_seconds": round(result.elapsed_seconds, 2),
            "decision": result.decision.to_dict() if result.decision else None,
            "network_used": result.network_used,
        }, False

    if name == "prompt_models":
        try:
            models = OllamaProvider().list_models()
        except ProviderError as exc:
            return {"models": [], "error": str(exc)}, True
        return {"models": [{"name": model.name, "size_bytes": model.size_bytes} for model in models]}, False

    raise ToolInputError(f"Unknown tool: {name}")


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_message(message: Any) -> dict[str, Any] | None:
    """Handle one JSON-RPC message; return a response, or None for notifications."""

    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid request: expected a JSON object.")
    if message.get("jsonrpc") != "2.0" or "method" not in message:
        return _error(message.get("id"), -32600, "Invalid request: jsonrpc 2.0 and method are required.")
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, -32602, "Invalid params: expected an object.")

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in LEGACY_VERSIONS + CURRENT_VERSIONS else "2025-06-18"
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _error(request_id, -32602, "Invalid params: tool name is required.")
        try:
            payload, is_error = call_tool(name, params.get("arguments") or {})
        except ToolInputError as exc:
            return _result(request_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
        except (ValueError, ProviderError) as exc:
            return _result(request_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
        return _result(request_id, {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "isError": is_error,
        })

    return _error(request_id, -32601, f"Method not found: {method}")


def serve_stdio() -> None:
    """Newline-delimited JSON-RPC over stdin/stdout. Logs go to stderr only."""

    # MCP is UTF-8 on every platform; Windows defaults to a legacy code page.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", newline="\n")
            except (ValueError, OSError):
                pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response: Any = _error(None, -32700, f"Parse error: {exc.msg}")
        else:
            if isinstance(message, list):
                responses = [item for item in (handle_message(entry) for entry in message) if item is not None]
                response = responses or None
            else:
                response = handle_message(message)
        if response is not None:
            try:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except BrokenPipeError:
                return


def _authorised(header: str | None) -> bool:
    expected = os.environ.get("PROMPT_STUDIO_MCP_TOKEN", "")
    if not expected:
        return True
    if not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:].strip(), expected)


class MCPHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}-mcp/{SERVER_VERSION}"
    protocol_version = "HTTP/1.1"

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/health", "/healthz"):
            self._json(200, {"status": "ok", "server": SERVER_NAME, "version": SERVER_VERSION})
        else:
            self._json(405, {"error": "Use POST /mcp for MCP requests."})

    def do_POST(self) -> None:
        if self.path not in ("/mcp", "/mcp/"):
            self._json(404, {"error": "Not found. POST to /mcp."})
            return
        if not _authorised(self.headers.get("Authorization")):
            self._json(401, {"error": "Missing or invalid bearer token."})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "Send application/json."})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._json(413, {"error": "Request body is missing or too large."})
            return
        try:
            message = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            self._json(400, _error(None, -32700, f"Parse error: {exc.msg}"))
            return

        if isinstance(message, list):
            responses = [item for item in (handle_message(entry) for entry in message) if item is not None]
            if not responses:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._json(200, responses)
            return

        response = handle_message(message)
        if response is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json(200, response)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Never log prompts or request bodies.
        super().log_message(fmt, *args)


def serve_http(port: int = 4180, host: str = "127.0.0.1") -> None:
    server = ThreadingHTTPServer((host, port), MCPHandler)
    server.daemon_threads = True
    print(f"Prompt Studio MCP at http://{host}:{port}/mcp (POST only)", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="prompt-studio-mcp", description="MCP server for Prompt Studio.")
    parser.add_argument("--http", action="store_true", help="Serve HTTP on /mcp instead of stdio.")
    parser.add_argument("--port", type=int, default=4180)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    if args.http:
        serve_http(args.port, args.host)
    else:
        serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
