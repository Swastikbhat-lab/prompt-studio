# Use Prompt Studio inside Claude, ChatGPT, and other MCP clients

Prompt Studio ships two surfaces over the same local engine:

| Surface | Best for | What it can do |
|---|---|---|
| **Skill** (`skills/prompt-studio/`) | Claude Code, claude.ai | Gate + deterministic packaging + local writer, called as a script |
| **MCP server** (`mcp_server.py`) | Claude Code/Desktop, ChatGPT, Cursor, any MCP client | `prompt_gate`, `prompt_build`, `prompt_models` tools |

Nothing here changes the privacy boundary: the gate and packaging run in-process
with no network, and the optional writer only talks to `127.0.0.1:11434`.

---

## Claude Code

### Option A — plugin marketplace (one-time, from GitHub)

Inside Claude Code:

```text
/plugin marketplace add Swastikbhat-lab/prompt-studio
/plugin install prompt-studio@prompt-studio
```

The plugin exposes the skill and registers the MCP server from `.mcp.json`.
In a shell the same commands are `claude plugin marketplace add …` and
`claude plugin install …`.

### Option B — personal skill (works offline, no marketplace)

```bash
git clone https://github.com/Swastikbhat-lab/prompt-studio
cd prompt-studio
python scripts/install_skill.py
```

This copies a self-contained skill to `~/.claude/skills/prompt-studio` (engine
included). Start a new Claude Code session; the skill is discovered from the
`SKILL.md` frontmatter.

### Option C — register the MCP server directly

```bash
claude mcp add --scope user prompt-studio -- python /absolute/path/to/prompt-studio/mcp_server.py
claude mcp list        # should show: prompt-studio … ✔ Connected
```

Remove it later with `claude mcp remove --scope user prompt-studio`.

Verify the tools without starting a session:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | python mcp_server.py
```

---

## claude.ai (web and desktop app)

1. Build the skill zip:

   ```bash
   python scripts/package_skill.py
   # dist/prompt-studio-skill.zip
   ```

2. In Claude: **Customize → Skills → + → Create skill → Upload a skill**, and
   select the zip. The archive contains one top-level folder, `prompt-studio/`,
   with `SKILL.md`, `references/`, and a self-contained `scripts/prompt_studio`.

3. Ask Claude to "improve this prompt with prompt-studio".

**Limits in claude.ai:** the sandbox has no access to your Ollama instance, so
the skill can run the gate and deterministic packaging (`engine:
deterministic`) but cannot call the local writer. If the gate returns `clarify`,
Claude will ask the questions and rebuild; a forced rewrite will report that no
local model is available. That is expected, not a failure.

---

## Claude Desktop

Edit `claude_desktop_config.json`:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "prompt-studio": {
      "command": "python",
      "args": ["C:/absolute/path/to/prompt-studio/mcp_server.py"]
    }
  }
}
```

Restart Claude Desktop; the tools appear under the tools icon. If `python` is
not on the app's PATH, use the absolute interpreter path.

---

## ChatGPT

### Desktop app (local, no hosting)

ChatGPT desktop supports local STDIO MCP servers:

1. **Settings → MCP servers → Add server → STDIO**.
2. Command: `python`
3. Arguments: `C:/absolute/path/to/prompt-studio/mcp_server.py`
4. Save, then restart ChatGPT.

### Web (remote MCP)

ChatGPT on the web cannot launch a local process. It needs a public HTTPS MCP
endpoint, or OpenAI's **Secure MCP Tunnel** pointing at a local server.

Run the HTTP transport locally:

```bash
export PROMPT_STUDIO_MCP_TOKEN="a-long-random-string"   # PowerShell: $env:...
python mcp_server.py --http --port 4180
```

Then expose only `http://127.0.0.1:4180/mcp` through a tunnel (Secure MCP
Tunnel, Cloudflare Tunnel, ngrok) and add it in ChatGPT:

1. **Plugins → + → Add custom MCP server**.
2. Connection: the public HTTPS URL ending in `/mcp`, or the tunnel.
3. Authentication: configure it to match your tunnel/proxy. ChatGPT's connector
   UI documents OAuth and unauthenticated modes; it does not offer a plain
   "paste an API key" field. The bearer token in `PROMPT_STUDIO_MCP_TOKEN` is
   meant for a tunnel/proxy that injects `Authorization: Bearer …`, so a
   private deployment is not open to anyone who learns the URL.
4. **Create as a plugin**, review the discovered tools, install, then type `@`
   in a conversation and pick it.

Plan availability for custom MCP connections varies (Business/Enterprise for
full support; Pro is limited). Check the current OpenAI help page before
relying on it.

**What we verified here:** the HTTP endpoint answers both the current stateless
MCP shape (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, `Accept:
application/json, text/event-stream`) and the legacy `initialize` handshake.
The ChatGPT web UI step itself was not exercised in this repository.

---

## Cursor / other MCP clients

Add to `.cursor/mcp.json` (project) or the global equivalent:

```json
{
  "mcpServers": {
    "prompt-studio": {
      "command": "python",
      "args": ["/absolute/path/to/prompt-studio/mcp_server.py"]
    }
  }
}
```

Any client that speaks JSON-RPC 2.0 over stdio or streamable HTTP can use it.

---

## Tools exposed

| Tool | Arguments | Returns |
|---|---|---|
| `prompt_gate` | `request`, optional `context`, `draft_now`, `answered`, `rounds` | route, specificity, ambiguity, context bloat, constraints, probabilities, measured latency |
| `prompt_build` | `request`, optional `context`, `model`, `strategy`, `force_rewrite`, `draft_now`, `rounds` | `prompt`, `summary`, `assumptions`, `questions`, `engine`, `decision` |
| `prompt_models` | none | installed Ollama models |

A typical agent flow: call `prompt_gate` first. If it returns `package`, call
`prompt_build` and use the prompt; no model is invoked. If it returns `clarify`,
ask the user the returned questions and rebuild. If it returns `draft`, show the
assumptions alongside the prompt.

## Security notes

- The HTTP server binds loopback only; it does not implement TLS.
- When `PROMPT_STUDIO_MCP_TOKEN` is unset, HTTP requests are unauthenticated —
  only do that on loopback.
- The server never logs prompts or request bodies.
- The writer cannot reach anything except `http://127.0.0.1:11434`.
