"""stdio transport (moved from mcp_server.main, issue #116): one process = one caller, fixed when it starts."""
import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


def run(server: Server) -> None:
    async def _run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(_run)
