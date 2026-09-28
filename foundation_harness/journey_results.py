"""Catalog-selected projections of native tool responses before model limits."""
import json


def model_result(tool, text):
    adapter = tool.get("response_adapter")
    if not adapter:
        return text[:16000]
    if adapter != "snowflake-cortex-agent":
        raise ValueError("Unknown Catalog tool response adapter")
    try:
        response = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError("Cortex Agent returned an invalid response") from None
    if (not isinstance(response, dict) or response.get("schema_version") not in ("v1", "v2")
            or response.get("role") != "assistant" or response.get("status") != "completed"
            or not isinstance(response.get("content"), list)
            or any(not isinstance(block, dict) for block in response["content"])):
        raise ValueError("Cortex Agent did not return a completed response")
    blocks = response["content"]
    last_tool = max((i for i, block in enumerate(blocks)
                     if block.get("type") in ("tool_use", "tool_result")), default=-1)
    final = [block for block in blocks[last_tool + 1:]
             if block.get("type") == "text" and isinstance(block.get("text"), str)]
    answer = "\n".join(block["text"] for block in final).strip()
    if not answer:
        raise ValueError("Cortex Agent did not return a final answer")
    citations = []
    for block in final:
        for annotation in block.get("annotations", []):
            if not isinstance(annotation, dict):
                continue
            citation = {key: annotation[key] for key in ("type", "doc_id", "doc_title", "text")
                        if isinstance(annotation.get(key), str)}
            if citation and citation not in citations:
                citations.append(citation)
    outcomes = []
    for block in blocks:
        result = block.get("tool_result")
        if block.get("type") == "tool_result" and isinstance(result, dict):
            outcomes.append({key: result[key] for key in ("name", "status")
                             if isinstance(result.get(key), str)})
    projected = json.dumps({"source": "Snowflake Cortex Agent", "status": "completed",
                            "answer": answer, "citations": citations, "tool_results": outcomes})
    # Never turn a truncated JSON envelope or lost final answer into successful evidence.
    if len(projected) > 16000:
        raise ValueError("Cortex Agent answer exceeds the configured evidence limit")
    return projected
