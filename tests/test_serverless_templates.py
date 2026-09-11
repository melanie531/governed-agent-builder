import json
from infra.serverless import template, artifacts_template
from scripts.serverless_deploy import safety


def test_no_vpc_dependencies_or_shared_resources():
    body = template()
    safety(body)
    assert all(not r["Type"].startswith("AWS::EC2") for r in body["Resources"].values())
    assert "AdministratorAccess" not in json.dumps(body)
    assert "AWS::CloudFront::VpcOrigin" not in json.dumps(body)
    for name in ("Web", "Exports"):
        assert all(body["Resources"][name]["Properties"]["PublicAccessBlockConfiguration"].values())
    assert body["Resources"]["State"]["Properties"]["SSESpecification"]["SSEEnabled"]
    assert body["Resources"]["State"]["Properties"]["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"]


def test_business_authorizer_and_separate_auth_routes():
    r = template()["Resources"]
    auth = r["SessionAuthorizer"]["Properties"]
    assert auth["AuthorizerResultTtlInSeconds"] == 0
    routes = [x["Properties"] for x in r.values() if x["Type"] == "AWS::ApiGatewayV2::Route"]
    for route in routes:
        if "/api" in route["RouteKey"]:
            assert route["AuthorizationType"] == "CUSTOM"
        else:
            assert route["RouteKey"] in ("GET /auth/login", "GET /auth/callback", "GET /studio-config.json")
            assert "AuthIntegration" in str(route["Target"])
    assert r["Pool"]["Properties"]["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"]
    assert not r["Client"]["Properties"]["GenerateSecret"]


def test_scoped_roles_and_durable_queue():
    r = template()["Resources"]
    for name in ("Auth", "Authorizer"):
        statements = r[name+"Role"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        keys = [s["Condition"]["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"] for s in statements if "Condition" in s]
        assert keys and all("agents" not in allowed and "versions" not in allowed for allowed in keys)
    assert r["WorkerMapping"]["Properties"]["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
    assert r["Jobs"]["Properties"]["VisibilityTimeout"] >= r["Worker"]["Properties"]["Timeout"] * 6
    assert r["StreamMapping"]["Properties"]["DestinationConfig"]["OnFailure"]
    assert r["DeadLettersAlarm"] and r["DispatchFailuresAlarm"]
    assert artifacts_template()["Resources"]["Releases"]["DeletionPolicy"] == "Retain"
