"""Basic smoke tests for the MCP server module."""


def test_import_mcp_instance():
    from nager_date_mcp.server import mcp

    assert mcp.name == "nager-date"


def test_server_has_instructions():
    from nager_date_mcp.server import mcp

    assert "Nager.Date" in (mcp.instructions or "")
