from infra.edge import template as edge
from infra.identity import template as identity


def test_edge_private_s3_and_tls_origin():
    r = edge()["Resources"]
    assert all(r["WebBucket"]["Properties"]["PublicAccessBlockConfiguration"].values())
    assert r["VpcOrigin"]["Properties"]["VpcOriginEndpointConfig"]["OriginProtocolPolicy"] == "https-only"
    d = r["Distribution"]["Properties"]["DistributionConfig"]
    assert "CustomErrorResponses" not in d
    assert {b["PathPattern"] for b in d["CacheBehaviors"]} == {"/api", "/api/*", "/auth", "/auth/*", "/studio-config.json"}
    for b in d["CacheBehaviors"]:
        assert b["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
        assert "FunctionAssociations" not in b
        assert b["ViewerProtocolPolicy"] == "https-only"
    assert r["WebPolicy"]["Properties"]["PolicyDocument"]["Statement"][0]["Condition"]["StringEquals"]["AWS:SourceArn"]


def test_isolated_identity_no_self_registration_or_password_api():
    r = identity("https://studio.example.test")["Resources"]
    assert r["Pool"]["Properties"]["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"]
    c = r["Client"]["Properties"]
    assert c["AllowedOAuthFlows"] == ["code"]
    assert c["CallbackURLs"] == ["https://studio.example.test/auth/callback"]
    assert not c["GenerateSecret"]
    assert c["ExplicitAuthFlows"] == ["ALLOW_REFRESH_TOKEN_AUTH"]
    assert c["AccessTokenValidity"] == 15
    assert not any(x["Type"] in ("AWS::IAM::Role", "AWS::Cognito::UserPoolUser") for x in r.values())
    assert {x["Properties"]["GroupName"] for x in r.values() if x["Type"] == "AWS::Cognito::UserPoolGroup"} == {"studio-research", "studio-operations", "studio-admin"}
