# MCP endpoint visibility

The authenticated MCP servers page lists uploaded deployments without displaying
their endpoints. The upload flow exposes an endpoint only after Use this MCP
package and an authentication choice. The guide incorrectly treats this hidden
field as an adequate way to retrieve a deployed server's URL.

## Scope

- Show a READY deployment's endpoint and a copy button when its row is selected
  under Uploaded MCP deployments.
- Show the same information immediately after a ZIP upload reaches READY, before
  authentication setup.
- Prefer the provider-token endpoint for OAuth or PAT. Identify the separate AWS
  IAM endpoint explicitly when both are available.
- Preserve deployment, authentication, registration and deletion behavior.
- Correct START-HERE.md and the generic onboarding guide to match the rendered
  controls. Keep current hosted behavior separate from a prepared change.

## Verification seam

The existing Playwright MCP browser suite exercises the deployment API response
through the real rendered components. Add a failing regression for opening a
saved deployment and copying the full URL without creating a connection.
Then cover READY upload output and IAM-only deployments through the same UI.
Retain the existing public API fixture contract; no backend behavior changes.

## Execution

1. Record the current hosted page's missing deployment endpoint.
2. Add and run the saved-deployment regression before changing production code.
3. Add the smallest reusable endpoint display using Cloudscape's copy control.
4. Add and run the READY upload regression, then use the display in that result.
5. Run the affected browser suites, TypeScript/Vite build and diff checks.
6. Inspect desktop and narrow-viewport screenshots, console and page errors.
7. Prepare a scoped frontend release and rollback artifact. Publishing over the
   accepted application requires the explicit approval specified by AGENTS.md.
8. After authorized publication, verify the controls against the existing
   user-owned deployment without uploading, registering or deleting resources.

The missing render output is deterministic. No bisection or added production
instrumentation is needed; the browser regression supplies the failing signal.
