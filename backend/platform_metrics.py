"""Administrator operational projections. Never return agent content or traces."""
import json
import math
import time

def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def overview(db, hours=24):
    since = time.time() - hours * 3600
    settings = {row["key"]: json.loads(row["body"]) for row in db.select("settings")}
    jobs = [value for key, value in settings.items()
            if key.startswith("journey-job:") and value.get("kind") == "invoke"
            and value.get("created", 0) >= since]
    versions = {(row["agent"], row["version"]): json.loads(row["body"]) for row in db.select("versions")}
    agents = []
    for row in db.select("agents"):
        definition = versions.get((row["id"], row["current_version"]), {})
        if definition.get("catalog_mode") != "journey":
            continue
        deployment = settings.get(f"journey-deployment:{row['id']}:{row['current_version']}", {})
        deletion = settings.get("journey-deletion:" + row["id"], {})
        if deletion.get("status") == "DELETED":
            continue
        samples = [job for job in jobs if job["agent"] == row["id"]]
        succeeded = sum(job["phase"] == "SUCCEEDED" for job in samples)
        failed = sum(job["phase"] in ("FAILED", "ERROR", "STALE") for job in samples)
        unknown = sum(job["phase"] == "UNKNOWN" for job in samples)
        latencies = [job["latency_ms"] for job in samples if job["phase"] == "SUCCEEDED"
                     and isinstance(job.get("latency_ms"), (int, float)) and job["latency_ms"] >= 0]
        agents.append({
            "id": row["id"], "name": definition.get("name", row["id"]), "workspace": row["workspace"],
            "version": row["current_version"], "model_id": definition.get("resolved_model_id"),
            "status": deletion.get("status") or deployment.get("status", "NOT_DEPLOYED"),
            "runtime_arn": (deployment.get("binding") or {}).get("arn"),
            "invocations": len(samples), "succeeded": succeeded, "failed": failed, "unknown": unknown,
            "pending": len(samples) - succeeded - failed - unknown,
            "error_rate": failed / (succeeded + failed) if succeeded + failed else None,
            "latency_samples": len(latencies), "latency_p50_ms": percentile(latencies, .5),
            "latency_p95_ms": percentile(latencies, .95),
        })
    requests = [value for key, value in settings.items() if key.startswith("tool-request:")]
    return {"generated_at": time.time(), "hours": hours,
            "scope": "Studio chat/API jobs for retained agents. Deployment smoke tests, evaluation runs and direct Runtime API calls are excluded.",
            "latency_scope": "Successful Runtime executions; queue wait is excluded. Error rate is failed / (succeeded + failed); unknown and pending outcomes are shown separately.",
            "agents": agents,
            "pending_tool_requests": sum(row["status"] in ("SUBMITTED", "IN_REVIEW") for row in requests),
            "pending_access_requests": sum(row["status"] == "PENDING" for row in db.select("requests"))}
