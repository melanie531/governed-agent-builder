"""Streamable HTTP MCP at the AgentCore Runtime protocol's /mcp endpoint."""
import logging
import os

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel
from starlette.responses import JSONResponse

from .database import Database, Settings, readonly_sql


class TokenValidation(BaseModel):
    authorized: bool


def create_server(database=None, *, database_factory=None, settings=None, auth_source=None, bearer_validator=None):
    if database is None and database_factory is None:
        settings = settings or Settings.environment()
        source = auth_source or os.environ.get("SNOWFLAKE_AUTH_SOURCE", "runtime")
        if source == "gateway":
            from .gateway_token import supplied_token
            token = supplied_token
        elif source == "runtime":
            # Retained only for explicitly deployed older Runtime versions.
            from .identity import OAuthSettings, UserAuthorization
            token = UserAuthorization(OAuthSettings.environment()).token
        else:
            raise ValueError("Unsupported Snowflake authentication source")
        database_factory = lambda headers: Database(settings, access_token=token(headers))
    # Connector diagnostics may include SQL/session details. Tool failures are
    # sanitized at the database boundary; do not log request or result bodies.
    for name in ("snowflake.connector", "botocore", "urllib3"):
        logging.getLogger(name).disabled = True
        logging.getLogger(name).setLevel(logging.CRITICAL)
    server = FastMCP("Snowflake read-only tools", host="0.0.0.0", port=8000,
                     stateless_http=True, json_response=True)
    limiter = anyio.CapacityLimiter(4)
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

    if bearer_validator is not None:
        @server.tool(annotations=annotations)
        async def _studio_validate_token(ctx: Context) -> TokenValidation:
            """Validate the private bearer token for the hosting authorization boundary."""
            try:
                request = ctx.request_context.request
                valid = await anyio.to_thread.run_sync(
                    lambda: bearer_validator(request.headers if request else {}), limiter=limiter)
                return TokenValidation(authorized=valid is True)
            except Exception:
                return TokenValidation(authorized=False)

    async def run(method, context, *args):
        def invoke():
            source = database
            if source is None:
                request = context.request_context.request
                source = database_factory(request.headers if request else {})
            return getattr(source, method)(*args)
        return await anyio.to_thread.run_sync(invoke, limiter=limiter)

    @server.tool(annotations=annotations)
    async def test_connection(ctx: Context) -> dict:
        """Verify your Snowflake user, active read role and warehouse."""
        return await run("test_connection", ctx)

    @server.tool(annotations=annotations)
    async def list_databases(ctx: Context) -> dict:
        """List up to 200 databases visible to your authorized Snowflake role."""
        return await run("list_databases", ctx)

    @server.tool(annotations=annotations)
    async def list_schemas(database: str, ctx: Context) -> dict:
        """List schemas visible in a database. Use exact names returned by list_databases."""
        return await run("list_schemas", ctx, database)

    @server.tool(annotations=annotations)
    async def list_tables(database: str, schema: str, ctx: Context) -> dict:
        """List readable tables visible in a schema, with names and metadata."""
        return await run("list_tables", ctx, database, schema)

    @server.tool(annotations=annotations)
    async def list_views(database: str, schema: str, ctx: Context) -> dict:
        """List views visible in a schema, including secure views granted to your role."""
        return await run("list_views", ctx, database, schema)

    @server.tool(annotations=annotations)
    async def describe_table(database: str, schema: str, table: str, ctx: Context) -> dict:
        """Describe table or view columns and types before writing a SELECT query."""
        return await run("describe_table", ctx, database, schema, table)

    @server.tool(annotations=annotations)
    async def query(sql: str, ctx: Context, max_rows: int = 200) -> dict:
        """Execute one read-only Snowflake SELECT (CTEs allowed). Use fully qualified
        tables and approved built-in functions. No DDL, DML, UDFs or role changes.
        At most 200 rows and 64KB are returned; truncated indicates an incomplete result.
        Queries have a 20-second Snowflake statement timeout."""
        readonly_sql(sql)
        return await run("query", ctx, sql, max_rows)

    @server.custom_route("/ping", methods=["GET"])
    async def ping(request):
        return JSONResponse({"status": "Healthy"})

    return server
