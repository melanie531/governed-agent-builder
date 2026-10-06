"""Edit these non-secret settings, then build and upload the complete ZIP."""
import os

from snowflake_mcp.database import Settings
from snowflake_mcp.gateway_token import supplied_token
from snowflake_mcp.server import create_server
from snowflake_mcp.token_validation import validate_token

SNOWFLAKE_ACCOUNT = "your-organization-your-account"
SNOWFLAKE_ROLE = "MCP_READER"
SNOWFLAKE_WAREHOUSE = "READ_WH"


def create_package_server():
    # Environment overrides preserve older operator-managed deployments.
    # Studio ZIP uploads need no provider environment variables.
    settings = Settings(
        os.getenv("SNOWFLAKE_ACCOUNT", SNOWFLAKE_ACCOUNT),
        os.getenv("SNOWFLAKE_ROLE", SNOWFLAKE_ROLE),
        os.getenv("SNOWFLAKE_WAREHOUSE", SNOWFLAKE_WAREHOUSE),
    )
    source = os.getenv("SNOWFLAKE_AUTH_SOURCE") or ("runtime" if os.getenv("OAUTH_PROVIDER_NAME") else "gateway")
    return create_server(settings=settings, auth_source=source,
        bearer_validator=lambda headers: validate_token(settings, supplied_token(headers)))


if __name__ == "__main__":
    create_package_server().run(transport="streamable-http")
