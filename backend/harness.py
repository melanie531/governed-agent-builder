"""Portable deterministic fixture runner. No LLM, eval(), network, or arbitrary code.

Prompt controls are intentionally limited and visible: 'uppercase', 'cite sources',
'no citations', 'refuse unknown', 'do not refuse'. All other prose is preserved but
not semantically interpreted. Expected answers are NEVER inputs to execution.
"""
import json
import re

DOCUMENTS = [
    {"id": "synthetic:aurora", "keywords": ["aurora", "launch"], "text": "Aurora launches in October with a small pilot before wider release.", "tool": "synthetic-search"},
    {"id": "synthetic:support", "keywords": ["support", "response"], "text": "The support response target is four hours during business days.", "tool": "synthetic-search"},
    {"id": "synthetic:strategy", "keywords": ["strategy", "insights"], "text": "The synthetic strategy prioritizes retention before expansion.", "tool": "restricted-insights"},
]

# Two governed tool-integration patterns, both MCP tools served through the Tool Gateway
# (locally simulated): discovered with tools/list, invoked with tools/call. There is no
# agent-to-agent (A2A) path. Authority is the saved manifest (tools + pinned versions),
# re-checked against current grants by the platform before every run. Arguments never
# carry identity: role, tenant_id or similar fields are rejected, not interpreted.
GATEWAY = "mcp-tool-via-gateway"
SPECIALIST_AGENTS = {
    # Platform-curated specialist (admin-owned catalog entry) exposed as an MCP tool via
    # Gateway. Builder-created Studio agents are directly usable and are not listed here.
    # Nested governance: the specialist's OWN manifest and service identity govern its inner
    # tools; the caller's grant only decides whether the caller may call this tool at all.
    # Caller tool grants are never inherited into the specialist's internals, but every call,
    # inner calls included, stays confined to the CALLER's grant scope (see require_scope).
    "agent-risk-analyst": {"agent": "synthetic-risk-analyst", "version": "1", "curation": "platform-curated",
        "service_identity": "synthetic-svc-risk-analyst", "operation": "risk_review",
        "manifest": {"tools": ["snowflake-approved-views"], "component_versions": {"snowflake-approved-views": "1"}},
        "inner_calls": [("snowflake-approved-views", {"query_id": "open_incident_counts"})],
        "steps": [
        {"step": "gather_signals", "finding": "Two open synthetic incidents and one delayed synthetic renewal."},
        {"step": "weigh_signals", "finding": "Open incidents plus a delayed renewal meet the synthetic elevated-risk rule."},
        {"step": "recommend", "finding": "Schedule an account review before the renewal date."}]},
    # ALPR misread-plate investigation specialists: READ-ONLY reviews over governed Snowflake
    # views (backend/alpr.py). The case id comes from the question and is passed to the connector
    # as a validated bind value. Findings are recommendations; no refund, void or notice is issued.
    **{tool_id: {"agent": agent, "version": "1", "curation": "platform-curated", "service_identity": f"synthetic-svc-{agent}",
                 "operation": operation, "case_scoped": True,
                 "manifest": {"tools": ["snowflake-alpr-views"], "component_versions": {"snowflake-alpr-views": "1"}},
                 "inner_calls": [("snowflake-alpr-views", {"query_id": q}) for q in queries]}
       for tool_id, agent, operation, queries in [
           ("agent-alpr-account-vehicle", "alpr-account-vehicle", "alpr_ownership_review", ["ownership_at_event"]),
           ("agent-alpr-billing-notice", "alpr-billing-notice", "alpr_billing_review", ["charges_notices_payments"]),
           ("agent-alpr-remediation", "alpr-remediation", "alpr_remediation_review",
            ["ownership_at_event", "charges_notices_payments", "remediation_status", "policy_at_event"])]},
}
SNOWFLAKE_VIEWS = {
    # Builtin MCP Snowflake connector: whitelisted named queries over approved read-only
    # views only. Rows are synthetic and already masked; there is no SQL argument.
    "snowflake-approved-views": {"version": "1", "queries": {
        "account_health_summary": {"view": "SYNTHETIC_DB.APPROVED_VIEWS.ACCOUNT_HEALTH_V", "rows": [
            {"account": "SYN-ACCT-001", "health": "amber", "contact_email": "***MASKED***"},
            {"account": "SYN-ACCT-002", "health": "green", "contact_email": "***MASKED***"}]},
        "open_incident_counts": {"view": "SYNTHETIC_DB.APPROVED_VIEWS.OPEN_INCIDENTS_V", "rows": [
            {"region": "SYN-REGION-A", "open_incidents": 2},
            {"region": "SYN-REGION-B", "open_incidents": 0}]}}},
    # Case-scoped connector over the secure ALPR views (SYNTHETIC rows). Rows come from
    # backend/alpr.py: a verbatim snapshot of the live views by default, or live Snowflake as the
    # read-only role. Arguments are query_id plus a validated case_id; still no SQL argument.
    "snowflake-alpr-views": {"version": "1", "case_scoped": True, "queries": {
        q: {"view": f"GAB_DEMO_DB.ALPR_INVESTIGATION_APPROVED.{view}"} for q, view in [
            ("ownership_at_event", "VW_OWNERSHIP_AT_EVENT"), ("charges_notices_payments", "VW_CHARGES_NOTICES_PAYMENTS"),
            ("remediation_status", "VW_REMEDIATION_STATUS"), ("policy_at_event", "VW_POLICY_AT_EVENT")]}},
}
# Deterministic fixture routing, like DOCUMENTS keywords: (tool, keywords, arguments).
TOOL_ROUTES = [
    ("agent-risk-analyst", ["risk"], lambda question: {"question": question}),
    ("snowflake-approved-views", ["account health"], lambda question: {"query_id": "account_health_summary"}),
    ("snowflake-approved-views", ["incident"], lambda question: {"query_id": "open_incident_counts"}),
    ("agent-alpr-account-vehicle", ["owner", "plate"], lambda question: {"question": question}),
    ("agent-alpr-billing-notice", ["billed", "notice"], lambda question: {"question": question}),
    ("agent-alpr-remediation", ["alpr", "refund", "remediation"], lambda question: {"question": question}),
]
ALPR_CASE_ID = r"^ALPR-C\d{3}$"


READ_VIEW = "read_approved_view"


class ToolDenied(ValueError):
    pass


def tool_scope(tool_id: str):
    """Every business operation and data view a tool can reach. None: the tool is unscoped.

    A caller's grant scope is a server-held subset of this; it is never read from the definition.
    """
    if tool_id in SPECIALIST_AGENTS:
        agent = SPECIALIST_AGENTS[tool_id]
        views = {SNOWFLAKE_VIEWS[t]["queries"][a["query_id"]]["view"] for t, a in agent["inner_calls"]}
        return {"operations": sorted({agent["operation"], READ_VIEW}), "data": sorted(views)}
    if tool_id in SNOWFLAKE_VIEWS:
        return {"operations": [READ_VIEW], "data": sorted(q["view"] for q in SNOWFLAKE_VIEWS[tool_id]["queries"].values())}
    return None


def require_scope(scope, operation: str, view: str | None = None):
    """Server-side caller boundary, distinct from the specialist's own manifest and identity."""
    if not isinstance(scope, dict):
        raise ToolDenied("CALLER_SCOPE_REQUIRED")
    if operation not in scope.get("operations", []):
        raise ToolDenied("CALLER_OPERATION_OUT_OF_SCOPE")
    if view is not None and view not in scope.get("data", []):
        raise ToolDenied("CALLER_DATA_OUT_OF_SCOPE")


def list_tools(definition: dict) -> list:
    """Local MCP tools/list via Gateway. Only manifest-selected tools are listed."""
    tools = []
    for tool_id in definition["tools"]:
        if tool_id in SPECIALIST_AGENTS:
            schema = {"question": {"type": "string"}}
        elif tool_id in SNOWFLAKE_VIEWS:
            schema = {"query_id": {"type": "string", "enum": sorted(SNOWFLAKE_VIEWS[tool_id]["queries"])}}
            if SNOWFLAKE_VIEWS[tool_id].get("case_scoped"):
                schema["case_id"] = {"type": "string", "pattern": ALPR_CASE_ID}
        else:
            continue
        tools.append({"name": tool_id, "via": GATEWAY, "inputSchema": {"type": "object", "properties": schema,
                      "required": list(schema), "additionalProperties": False}})
    return tools


def call_tool(definition: dict, tool_id: str, arguments: dict, scope: dict | None = None) -> dict:
    """Local MCP tools/call via Gateway. Only manifest-selected, version-pinned tools run, and
    only within the caller's grant scope (approved operations + data), which inner calls inherit."""
    if tool_id not in definition["tools"] or tool_id not in {**SPECIALIST_AGENTS, **SNOWFLAKE_VIEWS}:
        raise ToolDenied("TOOL_NOT_IN_MANIFEST")
    if tool_id in SPECIALIST_AGENTS:
        agent = SPECIALIST_AGENTS[tool_id]
        if definition["component_versions"].get(tool_id) != agent["version"]:
            raise ToolDenied("AGENT_VERSION_NOT_PINNED")
        if not isinstance(arguments, dict) or set(arguments) != {"question"} or not isinstance(arguments["question"], str):
            raise ToolDenied("AGENT_TOOL_ARGUMENTS_REJECTED")
        require_scope(scope, agent["operation"])
        case_id = None
        if agent.get("case_scoped"):
            case_id = re.search(r"\bALPR-C\d{3}\b", arguments["question"].upper())
            if case_id is None:
                raise ToolDenied("ALPR_CASE_ID_REQUIRED")
            case_id = case_id.group(0)
        inner_calls, found = [], {}
        for inner_tool, inner_arguments in agent["inner_calls"]:
            if case_id:
                inner_arguments = {**inner_arguments, "case_id": case_id}
            # Tools come from the specialist's own manifest, never the caller's; operations and
            # data stay within the caller's scope. Any denial fails the whole call closed.
            try:
                inner = call_tool(agent["manifest"], inner_tool, dict(inner_arguments), scope)
            except ToolDenied as exc:
                raise ToolDenied(str(exc) if str(exc).startswith(("CALLER_", "ALPR_")) else "SPECIALIST_INNER_TOOL_DENIED") from exc
            inner_calls.append({"tool": inner_tool, "via": GATEWAY, "identity": agent["service_identity"], "source": f"snowflake:{inner['query_id']}",
                                **({"snowflake_query_ids": inner["snowflake_query_ids"]} if inner.get("snowflake_query_ids") else {})})
            found[inner["query_id"]] = inner["rows"]
        if case_id:
            from backend import alpr
            findings = alpr.review(agent["operation"], case_id, found)
            return {"agent": agent["agent"], "agent_version": agent["version"], "via": GATEWAY, "steps": findings.pop("steps"),
                    "inner_calls": inner_calls, "answer": findings.pop("answer"), "findings": findings, "read_only": True, "actions_executed": []}
        return {"agent": agent["agent"], "agent_version": agent["version"], "via": GATEWAY, "steps": [dict(s) for s in agent["steps"]],
                "inner_calls": inner_calls, "answer": "Synthetic risk review: elevated. " + agent["steps"][-1]["finding"]}
    connector = SNOWFLAKE_VIEWS[tool_id]
    if definition["component_versions"].get(tool_id) != connector["version"]:
        raise ToolDenied("CONNECTOR_VERSION_NOT_PINNED")
    expected = {"query_id", "case_id"} if connector.get("case_scoped") else {"query_id"}
    if not isinstance(arguments, dict) or set(arguments) != expected or arguments["query_id"] not in connector["queries"]:
        raise ToolDenied("SNOWFLAKE_QUERY_NOT_WHITELISTED")
    query = connector["queries"][arguments["query_id"]]
    require_scope(scope, READ_VIEW, query["view"])
    if connector.get("case_scoped"):
        if not isinstance(arguments["case_id"], str) or not re.match(ALPR_CASE_ID, arguments["case_id"]):
            raise ToolDenied("SNOWFLAKE_QUERY_NOT_WHITELISTED")
        try:
            # Lazy: the exported single-file harness has no backend package and fails closed here.
            from backend import alpr
        except ImportError:
            raise ToolDenied("ALPR_VIEW_UNAVAILABLE") from None
        try:
            with alpr.query_evidence():
                rows = alpr.rows(arguments["query_id"], arguments["case_id"])
                query_ids = alpr.consume_query_ids()
        except alpr.ViewUnavailable as exc:
            raise ToolDenied(str(exc)) from None
        # Owner names are synthetic placeholders, not masked values.
        return {"query_id": arguments["query_id"], "case_id": arguments["case_id"], "view": query["view"], "via": GATEWAY,
                "rows": rows, "masked": False, "synthetic": True,
                # Real Snowflake query ids (cursor.sfqid) for this call; [] on the snapshot path.
                "snowflake_query_ids": query_ids}
    return {"query_id": arguments["query_id"], "view": query["view"], "via": GATEWAY, "rows": [dict(r) for r in query["rows"]], "masked": True}


def tool_evidence(definition: dict, question: str, scopes: dict):
    evidence, calls = [], []
    for tool_id, keywords, arguments in TOOL_ROUTES:
        if tool_id not in definition["tools"] or not any(k in question.lower() for k in keywords):
            continue
        try:
            result = call_tool(definition, tool_id, arguments(question), scopes.get(tool_id))
        except ToolDenied as exc:
            calls.append({"tool": tool_id, "via": GATEWAY, "denied": str(exc)})
            continue
        if "agent" in result:
            evidence.append({"id": f"agent:{result['agent']}@{result['agent_version']}", "text": result["answer"]})
            calls.append({"tool": tool_id, "via": GATEWAY, "source": evidence[-1]["id"], "inner_calls": result["inner_calls"]})
        else:
            evidence.append({"id": f"snowflake:{result['query_id']}", "text": f"Approved view {result['view']} returned {len(result['rows'])} masked synthetic rows."})
            calls.append({"tool": tool_id, "via": GATEWAY, "source": evidence[-1]["id"]})
    return evidence, calls


def run_case(definition: dict, question: str, scopes: dict | None = None) -> dict:
    prompt = definition["prompt"].lower()
    matches = [d for d in DOCUMENTS if d["tool"] in definition["tools"] and any(k in question.lower() for k in d["keywords"])]
    evidence, calls = tool_evidence(definition, question, scopes or {})
    matches += evidence
    sources = [d["id"] for d in matches]
    refused = not matches and "refuse unknown" in prompt and "do not refuse" not in prompt
    text = " ".join(d["text"] for d in matches) if matches else ("I cannot answer without evidence in the synthetic collection." if refused else "No matching synthetic evidence was found.")
    if "concise" in definition["skills"]:
        text = text[:160]
    if "uppercase" in prompt:
        text = text.upper()
    cite = ("cite sources" in prompt or "citations" in definition["skills"]) and "no citations" not in prompt
    citations = sources if cite else []
    if definition["output_format"] == "json":
        output = json.dumps({"answer": text, "sources": citations, "refused": refused})
    else:
        output = text + (" " + " ".join(f"[{s}]" for s in citations) if citations else "")
    return {"output": output, "sources": sources, "citations": citations, "refused": refused, "runner": "fixture runner, no live LLM", "trace": [{"type": "fixture_lookup", "matched_sources": sources}, *([{"type": "tools_call", "calls": calls}] if calls else []), {"type": "format_output", "format": definition["output_format"]}]}

def evaluate_case(definition: dict, case: dict, scopes: dict | None = None) -> dict:
    result = run_case(definition, case["input"], scopes)
    output = result["output"]
    checks = []
    for term in case["required_terms"]:
        checks.append({"check": f"Required term: {term}", "passed": term.lower() in output.lower(), "evidence": output})
    if case["require_citation"]:
        checks.append({"check": "Source citation and retrieved evidence", "passed": bool(result["citations"]) and set(result["citations"]).issubset(result["sources"]), "evidence": result["citations"]})
    checks.append({"check": "Expected refusal behavior", "passed": result["refused"] == case["expect_refusal"], "evidence": result["refused"]})
    try:
        parsed = json.loads(output)
        is_json = isinstance(parsed, dict) and "answer" in parsed
    except (ValueError, TypeError):
        is_json = False
    checks.append({"check": f"Output format: {case['expected_format']}", "passed": is_json == (case["expected_format"] == "json"), "evidence": output[:200]})
    score = sum(c["passed"] for c in checks) / len(checks)
    return {"id": case["id"], "input": case["input"], **result, "checks": checks, "score": score, "passed": all(c["passed"] for c in checks)}

def evaluate(definition: dict, policy: dict | None = None, scopes: dict | None = None) -> dict:
    policy = policy or {"minimum_score": 1, "require_judge": False}
    cases = [evaluate_case(definition, c, scopes) for c in definition["dataset"]]
    score = sum(c["score"] for c in cases) / len(cases)
    missing = definition["rubric"]["profile"] == "llm-required" or policy["require_judge"]
    threshold = max(policy["minimum_score"], definition["rubric"]["minimum_score"])
    safety_ok = all(check["passed"] for case in cases for check in case["checks"] if check["check"] in ("Expected refusal behavior", "Source citation and retrieved evidence"))
    passed = score >= threshold and safety_ok and not missing
    return {"cases": cases, "score": score, "threshold": threshold, "passed": passed, "safety_evidence_complete": safety_ok, "judge": "LLM judge not executed locally", "missing_required_judge": missing, "gate": "Local checks passed" if passed else "Needs changes", "production_ready": False, "mode": "LOCAL SIMULATION", "runner": "fixture runner, no live LLM"}

if __name__ == "__main__":
    import pathlib
    import sys
    definition = json.loads(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "definition.json").read_text())
    print(json.dumps(evaluate(definition, definition.get("evaluation_policy")), indent=2))
