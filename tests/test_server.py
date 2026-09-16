"""Basic smoke tests for the MCP server module (era shape).

Shape-forced port: the legacy pair asserted ``mcp.name == "nager-date"`` and
``"Nager.Date" in mcp.instructions`` on the old framework object, which no
longer exists post-migration. The covered fact — server identity — survives
in the era constants: SERVER_INFO name (D4 freeze), ERA_VERSION, TOOLS count.
"""


def test_import_server_module():
    from nager_date_mcp import server

    assert server.SERVER_INFO["name"] == "nager-date"


def test_era_constants():
    from nager_date_mcp.server import ERA_VERSION, TOOLS

    assert ERA_VERSION == "2026-07-28"
    assert len(TOOLS) == 7
