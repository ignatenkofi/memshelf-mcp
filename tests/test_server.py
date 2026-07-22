import pytest

pytest.importorskip("mcp")


def test_server_module_imports_and_registers_tool():
    # Importing runs the @mcp.tool decorators; this catches wiring errors even
    # without standing up the stdio transport.
    from memshelf_mcp import server

    assert server.mcp is not None
    assert callable(server.main)
    assert callable(server.memshelf_shelve)
