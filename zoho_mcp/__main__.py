import sys

from .server import main

if __name__ == "__main__":
    # stderr only: stdout belongs to the MCP stdio protocol. T13 rewires this to the new MCP entry.
    print("note: `python -m zoho_mcp` is deprecated; use `zoho-axi` (the MCP server entry arrives in a later release).",
          file=sys.stderr)
    main()
