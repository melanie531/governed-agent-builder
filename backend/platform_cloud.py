"""Bounded native sources for the platform administrator console."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException

from foundation_harness.config import digest


def registry_descriptor(item):
    descriptor = {"schema": "gab.catalog.v1", "capability_id": item["id"], "kind": item["kind"],
                  "version": item["version"], "binding_digest": item["binding_digest"]}
    if item["kind"] == "model":
        descriptor["model_id"] = item["binding"]["model_id"]
    elif item["kind"] == "mcp_server":
        descriptor.update(gateway_id=item["binding"]["gateway_id"], target_id=item["binding"]["target_id"])
    elif item["kind"] == "skill":
        descriptor["instructions"] = item["binding"]["instructions"]
    else:
        raise HTTPException(422, "Register the MCP server that owns this tool")
    return descriptor


class PlatformCloud:
    def __init__(self, settings, session=None):
        self.settings = settings
        self.session = session or boto3.Session(region_name=settings["region"])
        self.cache = {}

    def client(self, name):
        return self.session.client(name, config=Config(connect_timeout=3, read_timeout=15,
                                                       retries={"total_max_attempts": 2}))

    def cached(self, key, ttl, operation):
        previous = self.cache.get(key)
        if previous and time.time() - previous[0] < ttl:
            return previous[1]
        value = operation()
        self.cache[key] = (time.time(), value)
        return value

    def models(self):
        def read():
            client = self.client("bedrock")
            rows = []
            for model in client.list_foundation_models(byOutputModality="TEXT")["modelSummaries"]:
                if (model.get("modelLifecycle", {}).get("status") == "ACTIVE"
                        and "ON_DEMAND" in model.get("inferenceTypesSupported", [])):
                    rows.append({"id": model["modelId"], "name": model["modelName"],
                                 "provider": model["providerName"], "type": "Foundation model"})
            token = None
            for _ in range(10):
                page = client.list_inference_profiles(**({"nextToken": token} if token else {}))
                rows.extend({"id": model["inferenceProfileId"], "name": model["inferenceProfileName"],
                             "provider": "Amazon Bedrock", "type": "Inference profile"}
                            for model in page["inferenceProfileSummaries"] if model["status"] == "ACTIVE")
                token = page.get("nextToken")
                if not token:
                    return sorted(rows, key=lambda item: item["name"])
            raise HTTPException(503, "Model discovery exceeded the page limit")
        return self.cached("models", 300, read)

    def registry_id(self):
        value = self.settings.get("registry_arn")
        prefix = f"arn:aws:bedrock-agentcore:{self.settings['region']}:{self.settings['account']}:registry/"
        if not value or not value.startswith(prefix) or "/" in value[len(prefix):]:
            raise HTTPException(503, "The platform Registry is not configured")
        return value

    def native_registry(self):
        config = self.settings.get("mcp_onboarding", {})
        registry_id = config.get("registry_id", "")
        expected = f"arn:aws:agent-registry:{self.settings['region']}:{self.settings['account']}:registry/{registry_id}"
        if not registry_id or "/" in registry_id or config.get("registry_arn") != expected:
            raise HTTPException(503, "The platform Registry is not configured")
        return registry_id, expected

    def native_record_id(self, arn):
        registry_id, registry_arn = self.native_registry()
        prefix = registry_arn + "/record/"
        if not arn.startswith(prefix) or not arn[len(prefix):] or "/" in arn[len(prefix):]:
            raise HTTPException(409, "Record is outside the configured AWS Agent Registry")
        return {"registryId": registry_id, "recordId": arn[len(prefix):]}

    def registry(self):
        if self.settings.get("mcp_onboarding"):
            registry_id, registry_arn = self.native_registry()
            registry = self.client("agent-registry-control").get_registry(registryId=registry_id)
            if registry["registryArn"] != registry_arn:
                raise HTTPException(409, "Registry identity changed")
            return {key: registry[key] for key in ("registryArn", "name", "status", "approvalConfiguration") if key in registry}
        registry = self.client("bedrock-agentcore-control").get_registry(registryId=self.registry_id())
        return {key: registry[key] for key in ("registryArn", "name", "status", "approvalConfiguration") if key in registry}

    def register(self, item):
        if item.get("registry", {}).get("descriptor_type") == "mcpServer":
            raise HTTPException(409, "This MCP record is managed through MCP server onboarding")
        # CUSTOM model records carry typed metadata. MCP records point only at
        # this platform's previously verified Gateway bindings.
        descriptor = registry_descriptor(item)
        if item["kind"] == "mcp_server" and item["binding"]["gateway_id"] != self.settings["gateway_id"]:
            raise HTTPException(409, "MCP server is outside the platform Gateway")
        # A validated platform descriptor is CUSTOM, never misrepresented as a
        # native MCP server.json or Agent Skill format.
        if self.settings.get("mcp_onboarding"):
            registry_id, registry_arn = self.native_registry()
            response = self.client("agent-registry-control").create_registry_record(
                registryId=registry_id, name=item["id"], description=item["description"][:4096],
                recordType="CUSTOM", recordVersion=item["version"],
                descriptors={"custom": {"data": json.dumps(descriptor)}},
                clientToken=digest([registry_arn, item["id"], item["version"], item["binding_digest"]]),
                tags={"auto-delete": "no", "project": "governed-agent-builder"})
            self.native_record_id(response["recordArn"])
            return {"arn": response["recordArn"], "status": response["status"],
                    "version": item["version"], "binding_digest": item["binding_digest"],
                    "descriptor_type": "custom"}
        response = self.client("bedrock-agentcore-control").create_registry_record(
            registryId=self.registry_id(), name=item["id"], description=item["description"][:4096],
            descriptorType="CUSTOM", recordVersion=item["version"],
            descriptors={"custom": {"inlineContent": json.dumps(descriptor)}},
            clientToken=digest([self.registry_id(), item["id"], item["version"], item["binding_digest"]]))
        return {"arn": response["recordArn"], "status": response["status"],
                "version": item["version"], "binding_digest": item["binding_digest"]}

    def record(self, binding):
        if binding.get("descriptor_type") in {"mcpServer", "custom"}:
            return self.client("agent-registry-control").get_registry_record(
                **self.native_record_id(binding["arn"]))
        prefix = self.registry_id() + "/record/"
        if not binding["arn"].startswith(prefix) or "/" in binding["arn"][len(prefix):]:
            raise HTTPException(409, "Record is outside the platform Registry")
        return self.client("bedrock-agentcore-control").get_registry_record(
            registryId=self.registry_id(), recordId=binding["arn"])

    def submit(self, binding):
        self.record(binding)
        if binding.get("descriptor_type") in {"mcpServer", "custom"}:
            return self.client("agent-registry-control").submit_registry_record_for_approval(
                **self.native_record_id(binding["arn"]))["status"]
        return self.client("bedrock-agentcore-control").submit_registry_record_for_approval(
            registryId=self.registry_id(), recordId=binding["arn"])["status"]

    def decide(self, binding, approve, reason):
        self.record(binding)
        if binding.get("descriptor_type") in {"mcpServer", "custom"}:
            return self.client("agent-registry-control").update_registry_record_status(
                **self.native_record_id(binding["arn"]),
                status="APPROVED" if approve else "REJECTED", statusReason=reason)["status"]
        return self.client("bedrock-agentcore-control").update_registry_record_status(
            registryId=self.registry_id(), recordId=binding["arn"],
            status="APPROVED" if approve else "REJECTED", statusReason=reason)["status"]

    def validate_model(self, model_id):
        client = self.session.client("bedrock-runtime", config=Config(
            connect_timeout=3, read_timeout=20, retries={"total_max_attempts": 1}))
        try:
            response = client.converse(
                modelId=model_id, messages=[{"role": "user", "content": [{"text": "Reply with ready."}]}],
                inferenceConfig={"maxTokens": 64},
                toolConfig={"tools": [{"toolSpec": {"name": "connection_check",
                    "description": "A connection validation tool; no tool will be executed.",
                    "inputSchema": {"json": {"type": "object", "properties": {}}}}}]})
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ValidationException":
                raise HTTPException(422, "Bedrock rejected this model ID: "
                                    f"{error.response['Error'].get('Message', '')} "
                                    "Register the model's inference profile from discovery instead.")
            raise
        if not response.get("output", {}).get("message", {}).get("content"):
            raise HTTPException(409, "The model did not return a valid Converse response")
        return {"request_id": response["ResponseMetadata"]["RequestId"], "validated_at": time.time()}

    def model_metrics(self, model_ids):
        def read():
            end = datetime.now(timezone.utc)
            queries, mapping = [], {}
            specs = [("invocations", "Invocations", "Sum"), ("client_errors", "InvocationClientErrors", "Sum"),
                     ("server_errors", "InvocationServerErrors", "Sum"), ("throttles", "InvocationThrottles", "Sum"),
                     ("latency_p50_ms", "InvocationLatency", "p50"), ("latency_p95_ms", "InvocationLatency", "p95"),
                     ("input_tokens", "InputTokenCount", "Sum"), ("output_tokens", "OutputTokenCount", "Sum")]
            for index, model in enumerate(model_ids[:30]):
                for field, metric, stat in specs:
                    key = f"m{index}_{field}"
                    mapping[key] = (model, field)
                    queries.append({"Id": key, "MetricStat": {"Metric": {"Namespace": "AWS/Bedrock",
                        "MetricName": metric, "Dimensions": [{"Name": "ModelId", "Value": model}]},
                        "Period": 86400, "Stat": stat}, "ReturnData": True})
            rows = {model: {"model_id": model} for model in model_ids[:30]}
            if queries:
                # A complete UTC day keeps percentiles over the same period;
                # percentiles must never be averaged across daily buckets.
                end = end.replace(hour=0, minute=0, second=0, microsecond=0)
                result = self.client("cloudwatch").get_metric_data(
                    MetricDataQueries=queries, StartTime=end - timedelta(days=1), EndTime=end)
                if result.get("NextToken") or result.get("Messages"):
                    raise HTTPException(503, "Model metrics are incomplete")
                for series in result.get("MetricDataResults", []):
                    model, field = mapping[series["Id"]]
                    rows[model][field] = (series["Values"][0] if series.get("StatusCode") == "Complete"
                                         and len(series.get("Values", [])) == 1 else None)
            return {"source": "Amazon CloudWatch", "scope": "Account-wide Bedrock usage for Catalog model IDs; includes callers outside this platform.",
                    "start": (end - timedelta(days=1)).isoformat(), "end": end.isoformat(),
                    "generated_at": time.time(), "models": list(rows.values())}
        return self.cached("metrics:" + digest(model_ids), 300, read)

    def costs(self):
        def read():
            end = datetime.now(timezone.utc).date()
            start = end - timedelta(days=30)
            client = self.client("ce")
            tags = client.list_cost_allocation_tags(TagKeys=["project"]).get("CostAllocationTags", [])
            if not any(tag["TagKey"] == "project" and tag["Status"] == "Active" for tag in tags):
                return {"status": "UNAVAILABLE", "reason": "The project cost allocation tag is not active."}
            result = client.get_cost_and_usage(
                TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                Granularity="DAILY", Metrics=["UnblendedCost"],
                Filter={"And": [{"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [self.settings["account"]]}},
                                {"Tags": {"Key": "project", "Values": ["governed-agent-builder"]}}]},
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}])
            if result.get("NextPageToken"):
                raise HTTPException(503, "Cost results exceeded the page limit")
            services, daily, estimated = {}, [], False
            for day in result["ResultsByTime"]:
                total = Decimal(0)
                estimated = estimated or day.get("Estimated", False)
                for group in day["Groups"]:
                    metric = group["Metrics"]["UnblendedCost"]
                    if metric["Unit"] != "USD":
                        raise HTTPException(503, "Unexpected billing currency")
                    amount = Decimal(metric["Amount"])
                    services[group["Keys"][0]] = services.get(group["Keys"][0], Decimal(0)) + amount
                    total += amount
                daily.append({"date": day["TimePeriod"]["Start"], "amount": str(total) if day["Groups"] else None})
            return {"status": "AVAILABLE" if services else "NO_DATA", "source": "AWS Cost Explorer",
                    "account": self.settings["account"], "scope": "Resources billed with project=governed-agent-builder; untagged inference and shared resources are not allocated.",
                    "start": start.isoformat(), "end": end.isoformat(), "currency": "USD", "estimated": estimated,
                    "generated_at": time.time(), "total": str(sum(services.values(), Decimal(0))) if services else None,
                    "services": [{"service": key, "amount": str(value)} for key, value in sorted(services.items())],
                    "daily": daily if services else []}
        return self.cached("costs", 3600, read)
