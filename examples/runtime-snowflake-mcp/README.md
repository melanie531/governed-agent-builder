# Optional Snowflake MCP on AgentCore Runtime

This independent example hosts seven read-only Snowflake tools using per-user
Snowflake OAuth 3LO. Agent Studio installation never invokes it automatically.

Start with [Snowflake setup and credential retrieval](../../docs/snowflake-oauth-setup.md).
It provides the SQL to find your account identifier, prepare a warehouse and
reader role, create the OAuth integration, and retrieve the values used in Studio.
Then follow the [Studio user guide](../../docs/start-here.md) to publish tools and
create an agent through the portal.

For Studio package upload, edit the non-secret `SNOWFLAKE_ACCOUNT`,
`SNOWFLAKE_ROLE` and `SNOWFLAKE_WAREHOUSE` constants in `main.py`, then build the
complete ZIP below. Upload the ZIP through **MCP servers → Create MCP connection
→ Upload MCP package (.zip)**. The form asks for the name and file; all Snowflake
configuration belongs to this package. Do not upload only `main.py` or a folder
of instructions.

After **READY → Use this MCP package**, select **User sign-in (OAuth 3LO)**.
Configure or reuse the OAuth provider, review the seven business tools and
publish when the workspace has capacity. Complete user consent before a fresh
agent query. **Service credentials (OAuth 2LO)** has its own setup step and needs
a compatible client-credentials provider; native Snowflake OAuth uses 3LO.

`mcp-package.json` declares an internal read-only token-validation tool. The
shared hosting bridge calls it privately before forwarding requests. Its
Snowflake-specific validation is in `snowflake_mcp/token_validation.py`; it is
not published as an agent tool. Credentials are never packaged in the ZIP.

For a separate operator-managed AWS deployment, follow the
[Runtime deployment guide](../../docs/runtime-snowflake-mcp.md).
The example has its own `pyproject.toml`, `uv.lock`, CDK infrastructure, deployment
receipts, tests and Linux ARM64 package builder. Its dependencies do not enter the
platform's Lambda package.

Run the following commands from this example's directory, which contains
`pyproject.toml`, `uv.lock` and `package.py`. From the repository root:

```bash
cd examples/runtime-snowflake-mcp
uv sync --locked
uv run --locked pytest -q
uv run --locked python package.py --output artifacts/runtime.zip
```

If you copied the example to another folder, change into that folder instead.
The commands create and use the example's own `.venv`; running `uv sync` at the
repository root targets the platform project instead. Upload the resulting
`artifacts/runtime.zip` from the example folder.

To remove the server, first delete unused registrations, then use **Uploaded
MCP deployments → Delete MCP deployment** and confirm its name. Runtime and
uploaded code are removed; credentials, Snowflake data and shared infrastructure
remain.

The separate operator-managed AWS deployment needs an explicitly populated nonsecret configuration file; see the
guide before running `oauth_setup.py` and `deploy.py`. Use `verify.py` for IAM MCP
discovery and identity-denial checks, then complete consent and query verification
through Studio's **My connections** and **Try agent** screens.
