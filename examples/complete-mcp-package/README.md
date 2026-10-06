# Complete MCP package example

This example includes an entry point, a support module, a text resource and all
third-party dependencies in the deployable ZIP. Studio adds no server code.

Build with Python 3.13 and [uv](https://docs.astral.sh/uv/):

```sh
uv run --locked python package.py --output ../../artifacts/complete-mcp-package.zip
```

In Studio, open **MCP servers → Create MCP connection → Upload MCP package (.zip)**.
Give it a name, select the ZIP and click **Upload and deploy package**. Wait for
**READY**, then **Use this MCP package**. Choose **AWS IAM / AgentCore Runtime**
in the separate authentication step and save the IAM connection. Select workspaces, connect and review the discovered
`package_greeting` tool. Approve and publish when the workspace has capacity.

Create an agent with that tool and ask:

> Call package_greeting with name "Studio QA". Return its greeting and package_marker.

Expected tool output: greeting `Hello, Studio QA!` and marker
`complete-package-v1`. The marker proves that the uploaded support module and text
resource were available to the running MCP.

For your own server, replace the three source/resource files and list your
dependencies in `pyproject.toml`. Update `uv.lock` with `uv lock`. Add your source
folders to the copy step in `package.py`, then rebuild. Keep `main.py` at the ZIP
root and serve Streamable HTTP on `0.0.0.0:8000/mcp`. Package native dependencies
for **Linux ARM64 and Python 3.13**. Studio validates the ZIP and deploys its bytes;
it does not run `pip install` or install a `requirements.txt` file.

Package code contains no credentials. IAM authorizes the Gateway to invoke this
server. Configure any external data-source access separately.

To clean up, delete its unused registration under **Registered MCP connections**,
then select the server under **Uploaded MCP deployments → Delete MCP deployment**.
Confirm its exact name and wait for the deletion success message. Saved-agent
references block deletion; shared credentials and external data are preserved.
