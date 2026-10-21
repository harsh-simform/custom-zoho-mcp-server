import sys

from zoho_axi.mcp_server.server import main

if __name__ == "__main__":
    # stderr only: stdout belongs to the MCP stdio protocol.
    print("note: `python -m zoho_mcp` is deprecated; use `zoho-axi` (this now runs the six-tool zoho-axi MCP server).",
          file=sys.stderr)
    main()
