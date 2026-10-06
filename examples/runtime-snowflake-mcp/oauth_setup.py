"""Create the separate OAuth prerequisites; never register an MCP in Studio."""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from deploy import Deployment
from oauth_infrastructure import names, oauth_template


class OAuthDeployment(Deployment):
    def template(self):
        return oauth_template(self.config["prefix"], self.config["account"], self.config["region"],
                              self.config["snowflake_account"], self.config["oauth"])

    def plan(self):
        template = self.template()
        (self.directory / "oauth-template.json").write_text(json.dumps(template, indent=2))
        self.client("cloudformation").validate_template(TemplateBody=json.dumps(template))
        checks = {"no_literal_secret": "ClientSecret" not in template["Resources"]["Provider"]["Properties"]
                  ["Oauth2ProviderConfigInput"]["CustomOauth2ProviderConfig"],
                  "no_unscoped_iam": not any(v["Type"].startswith("AWS::IAM") for v in template["Resources"].values()),
                  "no_application_registration": True}
        (self.directory / "preflight.json").write_text(json.dumps({"pass": all(checks.values()), "checks": checks}, indent=2))
        if not all(checks.values()):
            raise ValueError("OAuth prerequisite security checks failed")
        print("OAuth prerequisite template validated; exact account checked; no AWS writes.", flush=True)

    def prepare(self):
        self.plan()
        outputs = self.stack("oauth", self.template(), {"OAuthClientId": "setup-required"})
        print(json.dumps({"callback_url": outputs["CallbackUrl"], "client_secret_arn": outputs["ClientSecretArn"],
                          "status": "SNOWFLAKE_CONFIGURATION_REQUIRED"}), flush=True)

    def configure(self, client_id, client_secret):
        if not client_id or client_id == "setup-required" or not client_secret:
            raise ValueError("Provide the actual Snowflake integration client ID and client secret")
        operation = self.state["operations"].get("oauth")
        intent = self.state.get("configuration")
        if not intent and (not operation or operation["phase"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE")):
            raise ValueError("Reconcile the prerequisite stack before configuring credentials")
        secret_arn = self.state["oauth"]["outputs"]["ClientSecretArn"]
        signature = hashlib.sha256(client_secret.encode()).hexdigest()
        if intent:
            if intent["client_id"] != client_id or intent["secret_digest"] != signature:
                raise ValueError("The retained configuration has different inputs; do not replace an uncertain credential write")
            present = intent["version_id"] in self.client("secretsmanager").describe_secret(
                SecretId=secret_arn)["VersionIdsToStages"]
            if intent["phase"] == "CONFIGURED":
                if not present:
                    raise ValueError("The configured secret version was removed; operator recovery is required")
                self.audit()
                return
        else:
            intent = {"phase": "WRITING_SECRET", "client_id": client_id, "secret_digest": signature, "version_id": str(uuid4())}
            self.state["configuration"] = intent
            self.save()
            present = False
        if not present:
            # Re-running configure is an explicit same-version retry after the
            # metadata read above. Never retry a write inside the same call.
            self.client("secretsmanager").put_secret_value(SecretId=secret_arn,
                ClientRequestToken=intent["version_id"], SecretString=json.dumps({"client_secret": client_secret}))
        self.state["configuration"]["phase"] = "SECRET_WRITTEN"
        parameters = {"OAuthClientId": client_id}
        expected = hashlib.sha256(json.dumps([self.template(), parameters], sort_keys=True).encode()).hexdigest()
        if operation and operation["digest"] != expected:
            if operation["phase"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                raise ValueError("A different CloudFormation operation is unresolved")
            self.state.setdefault("operation_history", []).append(self.state["operations"].pop("oauth"))
        self.save()
        self.stack("oauth", self.template(), parameters, update=True)
        self.state["configuration"]["phase"] = "CONFIGURED"
        self.save()
        self.audit()

    def audit(self):
        outputs = self.state["oauth"]["outputs"]
        workload, provider_name = names(self.config["oauth"])
        control = self.client("bedrock-agentcore-control")
        identity = control.get_workload_identity(name=workload)
        provider = control.get_oauth2_credential_provider(name=provider_name)
        secret = self.client("secretsmanager").describe_secret(SecretId=outputs["ClientSecretArn"])
        expected_tags = {"auto-delete": "no", "project": "governed-agent-builder",
                         "deployment": self.config["oauth"]["studio_prefix"]}
        checks = {"callback_bound": identity["allowedResourceOauth2ReturnUrls"] == [self.config["oauth"]["origin"] + "/oauth/callback"],
                  "provider_ready": provider.get("status", "READY") == "READY",
                  "client_configured": provider["oauth2ProviderConfigOutput"]["customOauth2ProviderConfig"]["clientId"] != "setup-required"}
        for name, arn in (("workload", outputs["WorkloadArn"]), ("provider", outputs["ProviderArn"])):
            tags = control.list_tags_for_resource(resourceArn=arn)["tags"]
            checks[name + "_tags"] = all(tags.get(k) == v for k, v in expected_tags.items())
        tags = {t["Key"]: t["Value"] for t in secret.get("Tags", [])}
        checks["secret_tags"] = all(tags.get(k) == v for k, v in expected_tags.items())
        checks["external_prefixed_secret"] = (provider.get("clientSecretSource") == "EXTERNAL"
            and provider.get("clientSecretArn") == {"secretArn": outputs["ClientSecretArn"]}
            and provider.get("clientSecretJsonKey") == "client_secret")
        result = {"pass": all(checks.values()), "checks": checks}
        (self.directory / "oauth-security.json").write_text(json.dumps(result, indent=2))
        if not result["pass"]:
            raise RuntimeError("OAuth setup is incomplete; see oauth-security.json")
        print("OAuth provider, callback, external secret and tags verified.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "prepare", "configure", "audit"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    deployment = OAuthDeployment(json.loads(args.config.read_text()), args.state_dir.resolve())
    if args.action == "configure":
        deployment.configure(getpass.getpass("Snowflake OAuth client ID: ").strip(),
                             getpass.getpass("Snowflake OAuth client secret: ").strip())
    else:
        getattr(deployment, args.action)()
