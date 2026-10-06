"""Export this server's actual MCP schemas without credentials or network calls."""
import argparse
import asyncio
import json
from pathlib import Path

from snowflake_mcp.server import create_server


def unavailable(_):
    raise RuntimeError("Schema export cannot execute a data operation")


async def export(destination):
    tools = await create_server(database_factory=unavailable).list_tools()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"tools": [
        tool.model_dump(include={"name", "description", "inputSchema"}, exclude_none=True)
        for tool in tools]}, indent=2) + "\n")
    print(f"Exported {len(tools)} tool schemas to {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(export(parser.parse_args().output))
