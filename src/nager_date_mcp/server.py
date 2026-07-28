"""FastMCP server exposing Nager.Date public-holiday endpoints as MCP tools."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "nager-date",
    instructions="Public holidays for 200+ countries via Nager.Date API",
)


def main() -> None:
    """Entry point for running the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
