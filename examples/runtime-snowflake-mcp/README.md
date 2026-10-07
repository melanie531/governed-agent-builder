# Python Snowflake MCP source

For Snowflake prerequisites, package upload, OAuth configuration, publication and
agent testing, follow the single [Snowflake setup guide](../../docs/snowflake-setup.md).
Choose **C. Uploaded Python MCP with user sign-in**. The same guide contains an
operator appendix for separately managed AWS stacks.

This example implements seven read-only tools. It owns its `pyproject.toml`,
`uv.lock`, tests, Linux ARM64 package builder and optional CDK deployment code.
Its dependencies do not enter the platform's Lambda package.

## Developer checks

From the repository root:

```bash
cd examples/runtime-snowflake-mcp
uv sync --locked
uv run --locked pytest -q
uv run --locked python package.py --output artifacts/runtime.zip
uv run --locked python export_schema.py --output artifacts/tools.json
```

These commands use the example's `.venv`. The package builder includes supporting
modules and locked production dependencies; the schema exporter makes no data
requests. Configure the non-secret constants in `main.py` as described in the
setup guide before building a package for upload.

`mcp-package.json` declares an internal read-only token-validation tool. The
hosting bridge calls it privately before forwarding requests. The validator
lives in `snowflake_mcp/token_validation.py` and is not published as an agent
tool. Credentials are never packaged in the ZIP.
