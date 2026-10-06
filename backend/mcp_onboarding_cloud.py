"""Generic authenticated Gateway targets and current AWS Agent Registry records."""
import json

import boto3
from botocore.config import Config

from foundation_harness.config import digest
from foundation_harness.journey_mcp import GatewayMCP, USER_TOKEN_HEADER
from .mcp_onboarding import configuration, endpoint_allowed
from .mcp_gateway_oauth import gateway_for, target_configuration

NO_RETRIES = Config(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=30)

def user_metadata(connection):
    return {"allowedRequestHeaders": [USER_TOKEN_HEADER]} if (
        connection.get("user_authorization") and connection["user_authorization"].get("mode") != "gateway") else {}


class OnboardingCloud:
    def __init__(self, settings, *, session=None, control=None, registry=None, transport=None):
        self.settings = settings
        self.session = session or boto3.Session(region_name=settings["region"])
        self.control = control or self.session.client("bedrock-agentcore-control", config=NO_RETRIES)
        self.registry = registry or self.session.client("agent-registry-control", config=NO_RETRIES)
        self.transport = transport or GatewayMCP(self.session, settings["gateway_url"], timeout=65)
        self.gateway_arn = f"arn:aws:bedrock-agentcore:{settings['region']}:{settings['account']}:gateway/{settings['gateway_id']}"

    def routing(self, state, config):
        connection = next(c for c in config["connections"] if c["id"] == state["connection_id"])
        route = gateway_for(connection, config, self.settings)
        return {**route, "arn": f"arn:aws:bedrock-agentcore:{self.settings['region']}:{self.settings['account']}:gateway/{route['gateway_id']}"}

    def validate(self, state, config):
        config = configuration(config, self.settings)
        connection = next(c for c in config["connections"] if c["id"] == state["connection_id"])
        if not endpoint_allowed(connection, state["endpoint"]):
            raise ValueError("Endpoint is outside the credential connection")
        route = self.routing(state, config)
        native_oauth = connection.get("user_authorization", {}).get("mode") == "gateway"
        gateway = self.control.get_gateway(gatewayIdentifier=route["gateway_id"])
        if (gateway["gatewayArn"] != route["arn"] or gateway["gatewayUrl"] != route["gateway_url"]
                or gateway["status"] != "READY" or gateway["authorizerType"] != ("CUSTOM_JWT" if native_oauth else "AWS_IAM")):
            raise ValueError("Gateway identity or authorization changed")
        if native_oauth and (not state.get("tool_schema") or gateway.get("authorizerConfiguration") != {
            "customJWTAuthorizer": {"discoveryUrl": route["issuer"] + "/.well-known/openid-configuration",
                                   "allowedClients": [route["client_id"]], "allowedScopes": ["openid"]}}):
            raise ValueError("Cognito Gateway binding or supplied tool schema changed")
        registry = self.registry.get_registry(registryId=config["registry_id"])
        if (registry["registryArn"] != config["registry_arn"] or registry["status"] != "READY"
                or registry.get("approvalConfiguration", {}).get("autoApprovalRules")):
            raise ValueError("Registry identity or readiness changed")
        return connection

    def target(self, state, config, *, retiring=False):
        connection = self.validate(state, config)
        route = self.routing(state, config)
        found, token = [], None
        for _ in range(20):
            result = self.control.list_gateway_targets(gatewayIdentifier=route["gateway_id"],
                                                       **({"nextToken": token} if token else {}))
            found.extend(t for t in result["items"] if t["name"] == state["target_name"])
            token = result.get("nextToken")
            if not token:
                break
        else:
            raise ValueError("Incomplete Gateway target inventory")
        if not found:
            return None
        if len(found) != 1:
            raise ValueError("Ambiguous Gateway target")
        target = self.control.get_gateway_target(gatewayIdentifier=route["gateway_id"], targetId=found[0]["targetId"])
        expected = target_configuration(state)
        actual = json.loads(json.dumps(target["targetConfiguration"]))
        mode = actual.get("mcp", {}).get("mcpServer", {}).pop("listingMode", "DEFAULT")
        if (target["name"] != state["target_name"] or target["gatewayArn"] != route["arn"]
                or actual != expected or mode != "DEFAULT"
                or target["credentialProviderConfigurations"] != [connection["configuration"]]
                or {k: v for k, v in (target.get("metadataConfiguration") or {}).items() if v} != user_metadata(connection)
                or target.get("privateEndpoint")
                or (state.get("gateway_target_id") and target["targetId"] != state["gateway_target_id"])):
            raise ValueError("Gateway target binding changed")
        if not retiring and target["status"] in ("FAILED", "UPDATE_FAILED", "DELETE_FAILED", "DELETING"):
            raise ValueError("Gateway target failed")
        return target

    @staticmethod
    def descriptors(state):
        server = {"$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
                  "name": "studio/" + state["catalog_id"], "description": (state["description"] or state["name"])[:100],
                  "version": "1.0.0", "remotes": [{"type": "streamable-http", "url": state["endpoint"]}]}
        return {"mcpServer": {"data": json.dumps(server, sort_keys=True), "dataSchemaVersion": "2025-12-11",
                             "additionalData": {"tools": {"data": json.dumps({"tools": state["tools"]}, sort_keys=True),
                                                          "dataSchemaVersion": "2025-03-26"}}}}

    def record(self, state, config, *, retiring=False):
        self.validate(state, config)
        record_id = state.get("registry_record_id")
        if not record_id:
            found, token = [], None
            for _ in range(20):
                page = self.registry.list_registry_records(registryId=config["registry_id"],
                                                           **({"nextToken": token} if token else {}))
                found.extend(r for r in page["registryRecords"] if r["name"] == state["catalog_id"])
                token = page.get("nextToken")
                if not token:
                    break
            else:
                raise ValueError("Incomplete Registry inventory")
            if not found:
                return None
            if len(found) != 1:
                raise ValueError("Ambiguous Registry record")
            record_id = found[0]["recordId"]
        from .mcp_credentials import CredentialCloud
        record = CredentialCloud.missing(lambda: self.registry.get_registry_record(
            registryId=config["registry_id"], recordId=record_id))
        if not record:
            return None
        if record["status"] == "CREATING":
            if retiring:
                raise ValueError("Registry creation must finish before deletion")
            return None
        if (record["recordArn"] != config["registry_arn"] + "/record/" + record_id
                or record["name"] != state["catalog_id"] or record["recordVersion"] != "1.0.0"
                or record["recordType"] != "MCP" or record["descriptors"] != self.descriptors(state)
                or record["status"] not in (("DRAFT", "PENDING_APPROVAL", "APPROVED", "DELETING") if retiring else
                                            ("DRAFT", "PENDING_APPROVAL", "APPROVED"))):
            raise ValueError("Registry record changed or failed validation")
        return record

    def read(self, stage, state, config):
        if stage == "retire_registry":
            if not state["change"]["native_registry"] or not state.get("tools"):
                return {"absent": True, "pending": False}
            record = self.record(state, config, retiring=True)
            return {"absent": record is None, "pending": bool(record and record["status"] == "DELETING")}
        if stage == "retire_target":
            target = self.target(state, config, retiring=True)
            return {"absent": target is None, "pending": bool(target and target["status"] == "DELETING")}
        if stage == "connect":
            target = self.target(state, config)
            return {"target_id": target["targetId"]} if target else None
        record = self.record(state, config)
        if not record:
            return None
        if stage == "register":
            return {"record_id": record["recordId"], "record_arn": record["recordArn"]}
        if stage == "submit" and record["status"] in ("PENDING_APPROVAL", "APPROVED"):
            return {"status": record["status"]}
        if stage == "approve" and record["status"] == "APPROVED":
            return {"status": "APPROVED"}
        return None

    def write(self, stage, state, config):
        connection = self.validate(state, config)
        route = self.routing(state, config)
        if stage == "retire_registry":
            record = self.record(state, config, retiring=True)
            if record:
                return self.registry.delete_registry_record(registryId=config["registry_id"], recordId=record["recordId"])
            return None
        if stage == "retire_target":
            target = self.target(state, config, retiring=True)
            if target:
                return self.control.delete_gateway_target(gatewayIdentifier=route["gateway_id"], targetId=target["targetId"])
            return None
        request_token = digest([state["id"], stage] + ([state["revision"]] if state.get("revision", 1) > 1 else []))
        if stage == "connect":
            return self.control.create_gateway_target(
                gatewayIdentifier=route["gateway_id"], name=state["target_name"],
                description="Studio MCP onboarding " + state["id"],
                clientToken=request_token,
                targetConfiguration=target_configuration(state),
                credentialProviderConfigurations=[connection["configuration"]],
                **({"metadataConfiguration": user_metadata(connection)} if user_metadata(connection) else {}))
        if stage == "register":
            return self.registry.create_registry_record(
                registryId=config["registry_id"], name=state["catalog_id"], displayName=state["name"],
                description=state["description"] or state["name"], recordType="MCP", recordVersion="1.0.0",
                descriptors=self.descriptors(state), clientToken=request_token,
                tags={"auto-delete": "no", "project": "governed-agent-builder", "purpose": "mcp-onboarding"})
        record = self.record(state, config)
        if not record:
            raise ValueError("Registry record is not ready")
        params = {"registryId": config["registry_id"], "recordId": state["registry_record_id"]}
        if stage == "submit" and record["status"] == "DRAFT":
            return self.registry.submit_registry_record_for_approval(**params)
        if stage == "approve" and record["status"] == "PENDING_APPROVAL":
            return self.registry.update_registry_record_status(
                **params, status="APPROVED", statusReason="Studio administrator reviewed discovered tools and selected workspace publication.")
        raise ValueError("Unexpected Registry transition")

    def discover(self, state, config):
        target = self.target(state, config)
        if not target:
            raise ValueError("Gateway target disappeared")
        if target["status"] != "READY":
            return None
        prefix = state["target_name"] + "___"
        if state.get("tool_schema"):
            return [{**t, "name": prefix + t["name"]} for t in state["tool_schema"]]
        return [t for t in self.transport.discover() if t["name"].startswith(prefix)]

    def verify(self, state, config):
        record = self.record(state, config)
        if not record or record["status"] != "APPROVED":
            raise ValueError("An approved Registry record is required")
        return self.discover(state, config)
