"""Native Snowflake managed-MCP creation and existing Gateway target integration."""
import json
import re
import time
from uuid import NAMESPACE_URL, uuid5

import boto3
import httpx
from botocore.config import Config

from foundation_harness.config import digest
from foundation_harness.journey_mcp import GatewayMCP
from .mcp_servers import specification, validate_profile

NO_RETRIES = Config(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=30)


class McpCloud:
    def __init__(self, settings, *, secrets=None, control=None, transport=None, session=None):
        self.settings = settings
        self.session = session or boto3.Session(region_name=settings["region"])
        self.secrets = secrets or self.session.client("secretsmanager", config=NO_RETRIES)
        self.control = control or self.session.client("bedrock-agentcore-control", config=NO_RETRIES)
        self.transport = transport
        self.gateway_arn = f"arn:aws:bedrock-agentcore:{settings['region']}:{settings['account']}:gateway/{settings['gateway_id']}"

    def sql(self, profile, statement, request_id):
        profile = validate_profile(profile, self.settings)
        value = self.secrets.get_secret_value(SecretId=profile["provisioning_secret_arn"])
        try:
            pat = json.loads(value["SecretString"])["pat"]
            if not isinstance(pat, str) or not pat or "\n" in pat or "\r" in pat:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("MCP_PROVISIONING_CREDENTIAL_INVALID") from None
        base = "https://" + profile["host"] + "/api/v2/statements"
        try:
            with httpx.Client(timeout=35, follow_redirects=False, transport=self.transport) as client:
                headers = {"Authorization": "Bearer " + pat, "Content-Type": "application/json",
                           "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN"}
                response = client.post(base, params={"requestId": str(uuid5(NAMESPACE_URL, request_id))}, headers=headers,
                                       json={"statement": statement, "timeout": 30, "warehouse": profile["warehouse"],
                                             "database": profile["database"], "schema": profile["schema"], "role": profile["creator_role"]})
                for _ in range(15):
                    if response.status_code != 202:
                        break
                    handle = response.json().get("statementHandle", "")
                    if not re.fullmatch(r"[0-9a-f-]{36}", handle):
                        raise RuntimeError("SNOWFLAKE_STATEMENT_HANDLE_INVALID")
                    time.sleep(1)
                    # Do not follow a service-supplied URL or replay the POST.
                    response = client.get(base + "/" + handle, headers=headers)
                if response.status_code != 200:
                    raise RuntimeError("SNOWFLAKE_STATEMENT_NOT_CONFIRMED")
                result = response.json()
                columns = [c["name"].lower() for c in result["resultSetMetaData"]["rowType"]]
                return [dict(zip(columns, row)) for row in result.get("data", [])]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise RuntimeError("SNOWFLAKE_RESPONSE_UNAVAILABLE") from None
        finally:
            pat = None
            value = None

    @staticmethod
    def object_name(state, profile):
        if not re.fullmatch(r"STUDIO_[A-F0-9]{20}", state["server_name"]):
            raise ValueError("Invalid owned MCP server name")
        return ".".join((profile["database"], profile["schema"], state["server_name"]))

    def server(self, state, profile):
        name = self.object_name(state, profile)
        found = self.sql(profile, f"SHOW MCP SERVERS LIKE '{state['server_name']}' IN SCHEMA {profile['database']}.{profile['schema']}",
                         state["id"] + ":show-server:" + str(time.time_ns()))
        # SHOW LIKE treats underscore as a wildcard; only the exact name counts.
        found = [row for row in found if row.get("name") == state["server_name"]]
        if not found:
            return None
        rows = self.sql(profile, "DESCRIBE MCP SERVER " + name, state["id"] + ":describe:" + str(time.time_ns()))
        if len(rows) != 1:
            raise RuntimeError("MCP_NATIVE_IDENTITY_MISMATCH")
        row = rows[0]
        if (row.get("name") != state["server_name"] or row.get("database_name") != profile["database"]
                or row.get("schema_name") != profile["schema"] or row.get("owner") != profile["creator_role"]):
            raise RuntimeError("MCP_NATIVE_OWNERSHIP_MISMATCH")
        try:
            actual = json.loads(row["server_spec"])
            wanted = specification(state, profile)
            if sorted(actual["tools"], key=lambda t: t["name"]) != sorted(wanted["tools"], key=lambda t: t["name"]):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("MCP_NATIVE_SPECIFICATION_MISMATCH") from None
        return {"server_name": state["server_name"], "specification_digest": digest(wanted)}

    def gateway(self):
        result = self.control.get_gateway(gatewayIdentifier=self.settings["gateway_id"])
        if (result["gatewayId"] != self.settings["gateway_id"] or result["gatewayArn"] != self.gateway_arn
                or result["gatewayUrl"] != self.settings["gateway_url"] or result["authorizerType"] != "AWS_IAM"
                or result["status"] != "READY"):
            raise RuntimeError("MCP_GATEWAY_BINDING_CHANGED")

    @staticmethod
    def target_config(state, profile):
        return {"targetConfiguration": {"mcp": {"mcpServer": {"endpoint": state["endpoint"]}}},
                "credentialProviderConfigurations": [{"credentialProviderType": "API_KEY", "credentialProvider": {
                    "apiKeyCredentialProvider": {"providerArn": profile["credential_provider_arn"],
                                                "credentialParameterName": "Authorization", "credentialPrefix": "Bearer",
                                                "credentialLocation": "HEADER"}}}]}

    def target(self, state, profile):
        self.gateway()
        items, token = [], None
        while True:
            result = self.control.list_gateway_targets(gatewayIdentifier=self.settings["gateway_id"],
                                                        **({"nextToken": token} if token else {}))
            items.extend(r for r in result["items"] if r["name"] == state["target_name"])
            token = result.get("nextToken")
            if not token:
                break
        if not items:
            return None
        if len(items) != 1:
            raise RuntimeError("MCP_GATEWAY_TARGET_AMBIGUOUS")
        current = self.control.get_gateway_target(gatewayIdentifier=self.settings["gateway_id"], targetId=items[0]["targetId"])
        if (current["name"] != state["target_name"] or current["gatewayArn"] != self.gateway_arn
                or any(current.get(k) != v for k, v in self.target_config(state, profile).items())
                or current.get("metadataConfiguration") or current.get("privateEndpoint")):
            raise RuntimeError("MCP_GATEWAY_TARGET_CHANGED")
        if state.get("gateway_target_id") and current["targetId"] != state["gateway_target_id"]:
            raise RuntimeError("MCP_GATEWAY_TARGET_ID_CHANGED")
        return current

    def read(self, stage, state, profile):
        validate_profile(profile, self.settings)
        if stage == "create":
            return self.server(state, profile)
        if stage == "grant":
            if not self.server(state, profile):
                raise RuntimeError("MCP_NATIVE_SERVER_MISSING")
            rows = self.sql(profile, "SHOW GRANTS ON MCP SERVER " + self.object_name(state, profile),
                            state["id"] + ":show-grants:" + str(time.time_ns()))
            return {"granted": True} if any(r.get("privilege") == "USAGE" and r.get("grantee_name") == profile["reader_role"]
                                           and r.get("granted_to") == "ROLE" for r in rows) else None
        if stage == "connect":
            target = self.target(state, profile)
            return {"target_id": target["targetId"]} if target else None
        raise ValueError("Unsupported native MCP operation")

    def write(self, stage, state, profile):
        validate_profile(profile, self.settings)
        if stage == "create":
            payload = json.dumps(specification(state, profile), separators=(",", ":"))
            return self.sql(profile, f"CREATE MCP SERVER {self.object_name(state, profile)} FROM SPECIFICATION $${payload}$$",
                            state["id"] + ":create")
        if stage == "grant":
            return self.sql(profile, f"GRANT USAGE ON MCP SERVER {self.object_name(state, profile)} TO ROLE {profile['reader_role']}",
                            state["id"] + ":grant")
        if stage == "connect":
            self.gateway()
            return self.control.create_gateway_target(gatewayIdentifier=self.settings["gateway_id"], name=state["target_name"],
                                                       clientToken=digest([state["id"], "connect"]),
                                                       **self.target_config(state, profile))
        raise ValueError("Unsupported native MCP operation")

    def discover(self, state, profile):
        # Revalidate the native server and reader grant before publication.
        if self.read("grant", state, profile) is None:
            raise RuntimeError("MCP_READER_GRANT_MISSING")
        target = self.target(state, profile)
        if not target:
            raise RuntimeError("MCP_GATEWAY_TARGET_MISSING")
        if target["status"] in {"FAILED", "UPDATE_FAILED", "DELETE_FAILED"}:
            raise RuntimeError("MCP_GATEWAY_TARGET_FAILED")
        if target["status"] != "READY":
            return None
        prefix = state["target_name"] + "___"
        return [t for t in GatewayMCP(self.session, self.settings["gateway_url"], timeout=30).discover()
                if t["name"].startswith(prefix)]
