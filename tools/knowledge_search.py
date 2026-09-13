"""Synthetic, versioned knowledge behind an AgentCore Gateway Lambda target."""
DOCUMENTS = (
    {"id": "aurora-launch", "title": "Aurora launch plan",
     "text": "Aurora launches in October with a small pilot before wider release."},
    {"id": "aurora-support", "title": "Support response policy",
     "text": "The support response target is four hours during business days."},
    {"id": "aurora-data", "title": "Data access policy",
     "text": "Payroll and individual customer records are not included in this knowledge collection."},
)

SCHEMA = {
    "name": "search", "description": "Search the Aurora synthetic knowledge collection for launch, support and data policies. Returns cited documents.",
    # Gateway Lambda inline schemas support a subset of JSON Schema.
    "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Knowledge search query, at most 2,000 characters."}},
                    "required": ["query"]},
}


def handler(event, context):
    import re
    query = event.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("Provide a nonempty knowledge query of at most 2,000 characters")
    words = set(re.findall(r"[a-z]{3,}", query.lower())) - {"the", "what", "with", "and", "for", "source", "search"}
    scored = [(len(words & set(re.findall(r"[a-z]{3,}", (doc["title"] + " " + doc["text"]).lower()))), doc)
              for doc in DOCUMENTS]
    return {"source": "Aurora synthetic knowledge v1", "documents": [doc for score, doc in sorted(scored, key=lambda x: -x[0]) if score > 0]}
