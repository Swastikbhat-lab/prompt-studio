"""Launcher for the Prompt Studio MCP server.

    python mcp_server.py            # stdio (Claude Code, Desktop, Cursor)
    python mcp_server.py --http     # POST /mcp for remote connectors
"""

from prompt_studio.mcp import main

if __name__ == "__main__":
    raise SystemExit(main())
