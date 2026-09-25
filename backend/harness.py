"""Portable deterministic fixture runner. No LLM, eval(), network, or arbitrary code.

Prompt controls are intentionally limited and visible: 'uppercase', 'cite sources',
'no citations', 'refuse unknown', 'do not refuse'. All other prose is preserved but
not semantically interpreted. Expected answers are NEVER inputs to execution.
"""
import json

DOCUMENTS = [
    {"id": "synthetic:aurora", "keywords": ["aurora", "launch"], "text": "Aurora launches in October with a small pilot before wider release.", "tool": "synthetic-search"},
    {"id": "synthetic:support", "keywords": ["support", "response"], "text": "The support response target is four hours during business days.", "tool": "synthetic-search"},
    {"id": "synthetic:strategy", "keywords": ["strategy", "insights"], "text": "The synthetic strategy prioritizes retention before expansion.", "tool": "restricted-insights"},
]

# Two governed tool-integration patterns behind a local stand-in for the Tool Gateway
# MCP tools/call boundary. Authority is the saved manifest (tools + pinned versions),
# re-checked against current grants by the platform before every run. Arguments never
# carry identity: role, tenant_id or similar fields are rejected, not interpreted.
SPECIALIST_AGENTS = {
    # Agent-as-tool: a published specialist agent for multi-step business reasoning.
    "agent-risk-analyst": {"agent": "synthetic-risk-analyst", "version": "1", "steps": [
        {"step": "gather_signals", "finding": "Two open synthetic incidents and one delayed synthetic renewal."},
        {"step": "weigh_signals", "finding": "Open incidents plus a delayed renewal meet the synthetic elevated-risk rule."},
        {"step": "recommend", "finding": "Schedule an account review before the renewal date."}]},
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
}
# Deterministic fixture routing, like DOCUMENTS keywords: (tool, keywords, arguments).
TOOL_ROUTES = [
    ("agent-risk-analyst", ["risk"], lambda question: {"question": question}),
    ("snowflake-approved-views", ["account health"], lambda question: {"query_id": "account_health_summary"}),
    ("snowflake-approved-views", ["incident"], lambda question: {"query_id": "open_incident_counts"}),
]


class ToolDenied(ValueError):
    pass


def call_tool(definition: dict, tool_id: str, arguments: dict) -> dict:
    """Local MCP tools/call boundary. Only manifest-selected, version-pinned tools run."""
    if tool_id not in definition["tools"] or tool_id not in {**SPECIALIST_AGENTS, **SNOWFLAKE_VIEWS}:
        raise ToolDenied("TOOL_NOT_IN_MANIFEST")
    if tool_id in SPECIALIST_AGENTS:
        agent = SPECIALIST_AGENTS[tool_id]
        if definition["component_versions"].get(tool_id) != agent["version"]:
            raise ToolDenied("AGENT_VERSION_NOT_PINNED")
        if not isinstance(arguments, dict) or set(arguments) != {"question"} or not isinstance(arguments["question"], str):
            raise ToolDenied("AGENT_TOOL_ARGUMENTS_REJECTED")
        return {"agent": agent["agent"], "agent_version": agent["version"], "steps": [dict(s) for s in agent["steps"]],
                "answer": "Synthetic risk review: elevated. " + agent["steps"][-1]["finding"]}
    connector = SNOWFLAKE_VIEWS[tool_id]
    if definition["component_versions"].get(tool_id) != connector["version"]:
        raise ToolDenied("CONNECTOR_VERSION_NOT_PINNED")
    if not isinstance(arguments, dict) or set(arguments) != {"query_id"} or arguments["query_id"] not in connector["queries"]:
        raise ToolDenied("SNOWFLAKE_QUERY_NOT_WHITELISTED")
    query = connector["queries"][arguments["query_id"]]
    return {"query_id": arguments["query_id"], "view": query["view"], "rows": [dict(r) for r in query["rows"]], "masked": True}


def tool_evidence(definition: dict, question: str):
    evidence, calls = [], []
    for tool_id, keywords, arguments in TOOL_ROUTES:
        if tool_id not in definition["tools"] or not any(k in question.lower() for k in keywords):
            continue
        try:
            result = call_tool(definition, tool_id, arguments(question))
        except ToolDenied as exc:
            calls.append({"tool": tool_id, "denied": str(exc)})
            continue
        if "agent" in result:
            evidence.append({"id": f"agent:{result['agent']}@{result['agent_version']}", "text": result["answer"]})
        else:
            evidence.append({"id": f"snowflake:{result['query_id']}", "text": f"Approved view {result['view']} returned {len(result['rows'])} masked synthetic rows."})
        calls.append({"tool": tool_id, "source": evidence[-1]["id"]})
    return evidence, calls


def run_case(definition: dict, question: str) -> dict:
    prompt = definition["prompt"].lower()
    matches = [d for d in DOCUMENTS if d["tool"] in definition["tools"] and any(k in question.lower() for k in d["keywords"])]
    evidence, calls = tool_evidence(definition, question)
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

def evaluate_case(definition: dict, case: dict) -> dict:
    result = run_case(definition, case["input"])
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

def evaluate(definition: dict, policy: dict | None = None) -> dict:
    policy = policy or {"minimum_score": 1, "require_judge": False}
    cases = [evaluate_case(definition, c) for c in definition["dataset"]]
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
