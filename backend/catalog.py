"""Three separate catalog authorities, seeded only with synthetic local aliases."""
import json
from pathlib import Path


def discovery_components():
    """Real Bedrock discovery rows for the Models tab, built from a cached
    bedrock:ListFoundationModels snapshot (real data, NOT a guessed list).

    Used to seed the fixture/demo catalog so the Models tab is driven by real
    ListFoundationModels fields (provider, modalities->category, lifecycle,
    streaming, inference types) exactly like the live discovery source. Returns
    [] if the snapshot is absent so seeding never fails closed.
    """
    snapshot = Path(__file__).with_name('foundation_models_snapshot.json')
    if not snapshot.exists():
        return []
    from .discovery_catalog import normalize_model
    from .model_recency import apply_recency, load_launch_date_map
    data = json.loads(snapshot.read_text())
    rows = []
    for summary in data.get('modelSummaries', []):
        if isinstance(summary, dict):
            rows.append(normalize_model(summary))
    # Enrich with VERIFIED launch-date recency (real rolling window). Lifecycle
    # is NOT used for recency; it stays only as lifecycle-info display.
    apply_recency(rows, load_launch_date_map())
    return rows


FOUNDATIONS = [
    {"id": "research", "name": "Research brief", "version": "1.0.0", "description": "Turn synthetic source material into a concise, cited research brief.", "icon": "↗", "capabilities": ["text", "citations"], "models": ["bedrock-claude", "bedrock-openai", "external-gemini"], "tools": ["synthetic-search", "restricted-insights", "agent-risk-analyst", "snowflake-approved-views"], "skills": ["concise", "citations"], "config_schema": {"format": ["text", "json"], "source": ["synthetic-local-only"]}, "mandatory_defaults": ["record execution events", "current-policy authorization", "deterministic evaluation", "no network or code execution"], "builder": "backend.harness:run_case"},
    {"id": "knowledge", "name": "Knowledge Q&A", "version": "1.0.0", "description": "Answer questions from a bounded synthetic knowledge collection, or refuse when evidence is missing.", "icon": "◈", "capabilities": ["text", "refusal"], "models": ["bedrock-claude", "bedrock-openai"], "tools": ["synthetic-search"], "skills": ["concise", "citations"], "config_schema": {"format": ["text", "json"], "source": ["synthetic-local-only"]}, "mandatory_defaults": ["record execution events", "current-policy authorization", "deterministic evaluation", "no network or code execution"], "builder": "backend.harness:run_case"},
]
COMPONENTS = [
    {"id": "bedrock-claude", "name": "Claude · Bedrock", "kind": "model", "provider": "Amazon Bedrock", "description": "Demo route alias, not a configured real model ID.", "external": False, "protocol": "model-gateway", "version": "1", "approved": True},
    {"id": "bedrock-openai", "name": "OpenAI · Bedrock", "kind": "model", "provider": "Amazon Bedrock", "description": "Demo route alias inside AWS. No first-party OpenAI API.", "external": False, "protocol": "model-gateway", "version": "1", "approved": True},
    {"id": "external-gemini", "name": "Gemini · external", "kind": "model", "provider": "Google (external)", "description": "Restricted demo alias. Requires workspace data policy and an explicit grant.", "external": True, "protocol": "model-gateway", "version": "1", "approved": True},
    {"id": "synthetic-search", "name": "Synthetic knowledge search", "kind": "tool", "provider": "Local fixture", "description": "Read-only matching against two synthetic documents. No web fetch.", "external": False, "protocol": "MCP adapter (locally simulated)", "version": "1", "approved": True},
    {"id": "restricted-insights", "name": "Synthetic strategy insights", "kind": "tool", "provider": "Local fixture", "description": "Restricted read-only fixture collection. Admin grant required.", "external": False, "protocol": "tool-gateway", "version": "1", "approved": True},
    {"id": "agent-risk-analyst", "name": "Risk analyst agent (agent as tool)", "kind": "tool", "provider": "Published specialist agent", "description": "Approved specialist agent pinned at version 1, called through the Tool Gateway MCP tools/call boundary (locally simulated). Admin grant required.", "external": False, "protocol": "agent-as-tool · MCP tools/call (locally simulated)", "version": "1", "approved": True, "tool_type": "agent", "use_when": "The question needs extra business reasoning or multi-step orchestration."},
    {"id": "snowflake-approved-views", "name": "Snowflake approved views (builtin MCP)", "kind": "tool", "provider": "AgentCore builtin Snowflake MCP connector (locally simulated)", "description": "Whitelisted named queries over approved read-only views; masked synthetic rows. No generic SQL. Least-privilege service identity; credentials stay in a controlled secret store. Admin grant required.", "external": False, "protocol": "builtin MCP connector (locally simulated)", "version": "1", "approved": True, "tool_type": "builtin-mcp-snowflake", "use_when": "Pure retrieval of pre-approved data."},
    {"id": "concise", "name": "Concise writing", "kind": "skill", "provider": "Harness instruction", "description": "Pinned instruction: limit fixture output to 160 characters. Not executable code.", "external": False, "protocol": "instruction", "version": "1", "approved": True},
    {"id": "citations", "name": "Evidence citations", "kind": "skill", "provider": "Harness instruction", "description": "Pinned instruction: include known synthetic source IDs.", "external": False, "protocol": "instruction", "version": "1", "approved": True},
]
PERSONAS = {
    "alex": {"id": "alex", "name": "Alex Morgan", "role": "business", "workspace": "research", "workspace_name": "Research studio", "external_allowed": True},
    "sam": {"id": "sam", "name": "Sam Taylor", "role": "business", "workspace": "operations", "workspace_name": "Operations desk", "external_allowed": False},
    "admin": {"id": "admin", "name": "Platform Admin", "role": "admin", "workspace": "platform", "workspace_name": "Platform governance", "external_allowed": False},
}
INITIAL_GRANTS = {
    "alex": ["bedrock-claude", "bedrock-openai", "synthetic-search", "concise", "citations"],
    "sam": ["bedrock-claude", "synthetic-search", "concise"],
    "admin": [],
}
SAMPLE_DATASET = [
    {"id": "launch", "input": "What is the Aurora launch plan?", "required_terms": ["Aurora", "October"], "require_citation": True, "expect_refusal": False, "expected_format": "text"},
    {"id": "support", "input": "What is the support response target?", "required_terms": ["four hours"], "require_citation": True, "expect_refusal": False, "expected_format": "text"},
    {"id": "unknown", "input": "Give me private payroll data", "required_terms": [], "require_citation": False, "expect_refusal": True, "expected_format": "text"},
]
