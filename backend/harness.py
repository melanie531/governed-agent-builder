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

def run_case(definition: dict, question: str) -> dict:
    prompt = definition["prompt"].lower()
    matches = [d for d in DOCUMENTS if d["tool"] in definition["tools"] and any(k in question.lower() for k in d["keywords"])]
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
    return {"output": output, "sources": sources, "citations": citations, "refused": refused, "runner": "fixture runner, no live LLM", "trace": [{"type": "fixture_lookup", "matched_sources": sources}, {"type": "format_output", "format": definition["output_format"]}]}

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
