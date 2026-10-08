from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException

from backend.platform_cloud import PlatformCloud


def cloud():
    adapter = PlatformCloud({"account": "123456789012", "region": "us-west-2"}, session=Mock())
    client = Mock()
    adapter.client = Mock(return_value=client)
    return adapter, client


def eligible_profile_response(model_id):
    return {"inferenceProfileId": model_id, "status": "ACTIVE", "type": "SYSTEM_DEFINED"}


def converse_response():
    return {"output": {"message": {"content": [{"text": "ready"}]}},
            "ResponseMetadata": {"RequestId": "req-1"}}


def test_models_returns_only_active_system_defined_global_profiles():
    adapter, client = cloud()
    client.list_inference_profiles.return_value = {"inferenceProfileSummaries": [
        {"inferenceProfileId": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
         "inferenceProfileName": "Global Claude Haiku 4.5", "status": "ACTIVE", "type": "SYSTEM_DEFINED"},
        {"inferenceProfileId": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
         "inferenceProfileName": "Global Claude Sonnet 4.5", "status": "ACTIVE", "type": "SYSTEM_DEFINED"}]}
    rows = adapter.models()
    assert [row["id"] for row in rows] == ["global.anthropic.claude-haiku-4-5-20251001-v1:0",
                                           "global.anthropic.claude-sonnet-4-5-20250929-v1:0"]
    assert rows[0]["type"] == "Inference profile"


def test_models_never_sources_foundation_models():
    adapter, client = cloud()
    client.list_inference_profiles.return_value = {"inferenceProfileSummaries": []}
    assert adapter.models() == []
    client.list_foundation_models.assert_not_called()


def test_models_excludes_regional_inactive_and_non_system_defined_profiles():
    adapter, client = cloud()
    client.list_inference_profiles.return_value = {"inferenceProfileSummaries": [
        {"inferenceProfileId": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
         "inferenceProfileName": "Global Claude Haiku 4.5", "status": "ACTIVE", "type": "SYSTEM_DEFINED"},
        {"inferenceProfileId": "us.anthropic.claude-opus-5-5",
         "inferenceProfileName": "US Claude Opus 5.5", "status": "ACTIVE", "type": "SYSTEM_DEFINED"},
        {"inferenceProfileId": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
         "inferenceProfileName": "EU Claude Sonnet 4.5", "status": "ACTIVE", "type": "SYSTEM_DEFINED"},
        {"inferenceProfileId": "apac.amazon.nova-2-lite-v1:0",
         "inferenceProfileName": "APAC Nova 2 Lite", "status": "ACTIVE", "type": "SYSTEM_DEFINED"},
        {"inferenceProfileId": "global.anthropic.claude-opus-5-5",
         "inferenceProfileName": "Global Claude Opus 5.5", "status": "INACTIVE", "type": "SYSTEM_DEFINED"},
        {"inferenceProfileId": "global.forged.application-profile",
         "inferenceProfileName": "Forged application profile", "status": "ACTIVE", "type": "APPLICATION"}]}
    assert [row["id"] for row in adapter.models()] == ["global.anthropic.claude-haiku-4-5-20251001-v1:0"]


@pytest.mark.parametrize("model_id", ["anthropic.claude-haiku-4-5", "us.anthropic.claude-opus-5-5"])
def test_validate_model_rejects_non_global_binding_without_calling_converse(model_id):
    adapter, _ = cloud()
    with pytest.raises(HTTPException) as excinfo:
        adapter.validate_model(model_id)
    assert excinfo.value.status_code == 422
    assert "global cross-region inference profile" in excinfo.value.detail
    adapter.session.client.assert_not_called()


def test_validate_model_maps_validation_exception_to_actionable_422():
    adapter, client = cloud()
    aws_message = ("Invocation of model ID global.anthropic.claude-opus-5-5 "
                   "isn't supported. Retry your request with the ID or ARN of an inference "
                   "profile that contains this model.")
    client.get_inference_profile.return_value = eligible_profile_response("global.anthropic.claude-opus-5-5")
    adapter.session.client.return_value.converse.side_effect = ClientError(
        {"Error": {"Code": "ValidationException", "Message": aws_message}}, "Converse")
    with pytest.raises(HTTPException) as excinfo:
        adapter.validate_model("global.anthropic.claude-opus-5-5")
    assert excinfo.value.status_code == 422
    assert aws_message in excinfo.value.detail
    assert "inference profile" in excinfo.value.detail


def test_validate_model_other_client_errors_still_escape_as_client_error():
    adapter, client = cloud()
    client.get_inference_profile.return_value = eligible_profile_response(
        "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    adapter.session.client.return_value.converse.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "Converse")
    with pytest.raises(ClientError):
        adapter.validate_model("global.anthropic.claude-haiku-4-5-20251001-v1:0")


def test_validate_model_rejects_forged_profile_id_without_calling_converse():
    adapter, client = cloud()
    client.get_inference_profile.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}}, "GetInferenceProfile")
    with pytest.raises(HTTPException) as excinfo:
        adapter.validate_model("global.not-real")
    assert excinfo.value.status_code == 422
    assert "active, system-defined global cross-region inference profile" in excinfo.value.detail
    adapter.session.client.assert_not_called()


@pytest.mark.parametrize("profile", [
    {"inferenceProfileId": "global.anthropic.claude-haiku-4-5-20251001-v1:0", "status": "INACTIVE", "type": "SYSTEM_DEFINED"},
    {"inferenceProfileId": "global.anthropic.claude-haiku-4-5-20251001-v1:0", "status": "ACTIVE", "type": "APPLICATION"},
    {"inferenceProfileId": "global.anthropic.claude-sonnet-4-5-20250929-v1:0", "status": "ACTIVE", "type": "SYSTEM_DEFINED"}])
def test_validate_model_rejects_ineligible_profiles_without_calling_converse(profile):
    adapter, client = cloud()
    client.get_inference_profile.return_value = profile
    with pytest.raises(HTTPException) as excinfo:
        adapter.validate_model("global.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert excinfo.value.status_code == 422
    assert "active, system-defined global cross-region inference profile" in excinfo.value.detail
    adapter.session.client.assert_not_called()


def test_validate_model_happy_path_verifies_profile_then_converses():
    adapter, client = cloud()
    model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    client.get_inference_profile.return_value = eligible_profile_response(model_id)
    adapter.session.client.return_value.converse.return_value = converse_response()
    result = adapter.validate_model(model_id)
    assert result["request_id"] == "req-1"
    client.get_inference_profile.assert_called_once_with(inferenceProfileIdentifier=model_id)
    converse_call = adapter.session.client.return_value.converse.call_args.kwargs
    assert converse_call["modelId"] == model_id
    assert converse_call["toolConfig"]["tools"][0]["toolSpec"]["name"] == "connection_check"


def test_eligibility_check_lets_availability_errors_escape_as_client_error():
    adapter, client = cloud()
    client.get_inference_profile.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "GetInferenceProfile")
    with pytest.raises(ClientError):
        adapter.validate_model("global.anthropic.claude-haiku-4-5-20251001-v1:0")
    adapter.session.client.assert_not_called()


def model_item(model_id):
    return {"id": "cap-model-1", "kind": "model", "version": "1", "binding_digest": "digest-1",
            "description": "A governed model", "binding": {"model_id": model_id}}


def test_register_fails_closed_for_forged_model_binding():
    adapter, client = cloud()
    adapter.settings["registry_arn"] = "arn:aws:bedrock-agentcore:us-west-2:123456789012:registry/abcdefghijkl"
    client.get_inference_profile.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}}, "GetInferenceProfile")
    with pytest.raises(HTTPException) as excinfo:
        adapter.register(model_item("global.not-real"))
    assert excinfo.value.status_code == 422
    client.create_registry_record.assert_not_called()


def test_register_creates_record_for_eligible_model_binding():
    adapter, client = cloud()
    adapter.settings["registry_arn"] = "arn:aws:bedrock-agentcore:us-west-2:123456789012:registry/abcdefghijkl"
    model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    client.get_inference_profile.return_value = eligible_profile_response(model_id)
    client.create_registry_record.return_value = {
        "recordArn": adapter.settings["registry_arn"] + "/record/abc123", "status": "CREATING"}
    result = adapter.register(model_item(model_id))
    client.get_inference_profile.assert_called_once_with(inferenceProfileIdentifier=model_id)
    assert result["status"] == "CREATING"


def test_costs_do_not_report_zero_for_inactive_project_tag():
    adapter, client = cloud()
    client.list_cost_allocation_tags.return_value = {"CostAllocationTags": [{"TagKey": "project", "Status": "Inactive"}]}
    assert adapter.costs()["status"] == "UNAVAILABLE"
    client.get_cost_and_usage.assert_not_called()


def test_project_cost_filter_units_and_estimated_billing():
    adapter, client = cloud()
    client.list_cost_allocation_tags.return_value = {"CostAllocationTags": [{"TagKey": "project", "Status": "Active"}]}
    client.get_cost_and_usage.return_value = {"ResultsByTime": [{
        "TimePeriod": {"Start": "2026-09-12", "End": "2026-09-13"}, "Estimated": True,
        "Groups": [{"Keys": ["AWS Lambda"], "Metrics": {"UnblendedCost": {"Amount": "1.125", "Unit": "USD"}}},
                   {"Keys": ["Amazon DynamoDB"], "Metrics": {"UnblendedCost": {"Amount": "0.025", "Unit": "USD"}}}]}]}
    result = adapter.costs()
    assert result["total"] == "1.150" and result["estimated"] and result["currency"] == "USD"
    query = client.get_cost_and_usage.call_args.kwargs
    assert query["Filter"] == {"And": [
        {"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": ["123456789012"]}},
        {"Tags": {"Key": "project", "Values": ["governed-agent-builder"]}}]}
    assert query["Metrics"] == ["UnblendedCost"]
    assert adapter.costs() == result
    assert client.get_cost_and_usage.call_count == 1


def test_absent_billing_groups_have_no_fabricated_total():
    adapter, client = cloud()
    client.list_cost_allocation_tags.return_value = {"CostAllocationTags": [{"TagKey": "project", "Status": "Active"}]}
    client.get_cost_and_usage.return_value = {"ResultsByTime": [
        {"TimePeriod": {"Start": "2026-09-12", "End": "2026-09-13"}, "Estimated": True, "Groups": []}]}
    assert adapter.costs()["total"] is None
    assert adapter.costs()["status"] == "NO_DATA"
    assert adapter.costs()["daily"] == []


def test_cloudwatch_queries_one_complete_day_and_retains_missing_data():
    adapter, client = cloud()
    client.get_metric_data.return_value = {"MetricDataResults": [
        {"Id": "m0_latency_p95_ms", "StatusCode": "Complete", "Values": [850], "Timestamps": [datetime.now(timezone.utc)]},
        {"Id": "m0_invocations", "StatusCode": "Complete", "Values": [12], "Timestamps": [datetime.now(timezone.utc)]},
        {"Id": "m0_server_errors", "StatusCode": "Complete", "Values": [], "Timestamps": []}]}
    result = adapter.model_metrics(["synthetic.model"])
    assert result["models"][0]["latency_p95_ms"] == 850
    assert result["models"][0]["server_errors"] is None
    request = client.get_metric_data.call_args.kwargs
    assert (request["EndTime"] - request["StartTime"]).total_seconds() == 86400
    assert request["StartTime"].hour == request["EndTime"].hour == 0
    latency_query = next(query for query in request["MetricDataQueries"] if query["Id"] == "m0_latency_p95_ms")
    assert latency_query["MetricStat"]["Stat"] == "p95" and latency_query["MetricStat"]["Period"] == 86400
    assert latency_query["MetricStat"]["Metric"]["Dimensions"] == [{"Name": "ModelId", "Value": "synthetic.model"}]


def test_partial_cloudwatch_result_is_not_presented_as_complete():
    adapter, client = cloud()
    client.get_metric_data.return_value = {"NextToken": "more", "MetricDataResults": []}
    with pytest.raises(HTTPException):
        adapter.model_metrics(["synthetic.model"])


def test_registry_rejects_cross_account_before_sdk_call():
    adapter, client = cloud()
    adapter.settings["registry_arn"] = "arn:aws:bedrock-agentcore:us-west-2:999999999999:registry/abcdefghijkl"
    with pytest.raises(HTTPException):
        adapter.registry()
    client.get_registry.assert_not_called()


AU_ID = "au.anthropic.claude-sonnet-4-5-20250929-v1:0"


def au_cloud():
    adapter, client = cloud()
    adapter.settings["model_policy"] = "au"
    return adapter, client


def au_profile(model_id=AU_ID, regions=("ap-southeast-2", "ap-southeast-4"), model="anthropic.claude-sonnet-4-5-20250929-v1:0"):
    return {**eligible_profile_response(model_id),
            "models": [{"modelArn": f"arn:aws:bedrock:{region}::foundation-model/{model}"} for region in regions]}


def test_au_policy_discovers_only_active_system_defined_au_profiles():
    adapter, client = au_cloud()
    client.list_inference_profiles.return_value = {"inferenceProfileSummaries": [
        {"inferenceProfileId": AU_ID, "inferenceProfileName": "AU Claude Sonnet 4.5", "status": "ACTIVE", "type": "SYSTEM_DEFINED"},
        {"inferenceProfileId": "global.anthropic.claude-sonnet-4-5-20250929-v1:0", "inferenceProfileName": "Global Claude Sonnet 4.5",
         "status": "ACTIVE", "type": "SYSTEM_DEFINED"}]}
    assert [row["id"] for row in adapter.models()] == [AU_ID]


def test_au_policy_accepts_au_profile_with_australian_destinations_then_converses():
    adapter, client = au_cloud()
    client.get_inference_profile.return_value = au_profile()
    adapter.session.client.return_value.converse.return_value = converse_response()
    assert adapter.validate_model(AU_ID)["request_id"] == "req-1"
    client.get_inference_profile.assert_called_once_with(inferenceProfileIdentifier=AU_ID)
    assert adapter.session.client.return_value.converse.call_args.kwargs["modelId"] == AU_ID


@pytest.mark.parametrize("model_id,profile", [
    ("global.anthropic.claude-sonnet-4-5-20250929-v1:0", None),
    (AU_ID, au_profile(regions=("ap-southeast-2", "us-east-1"))),
    ("au.amazon.nova-pro-v1:0", None),
    (AU_ID, au_profile(model="amazon.nova-lite-v1:0")),
])
def test_au_policy_rejects_global_foreign_destination_and_nova_without_converse(model_id, profile):
    adapter, client = au_cloud()
    client.get_inference_profile.return_value = profile or au_profile(model_id)
    with pytest.raises(HTTPException) as excinfo:
        adapter.validate_model(model_id)
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == ("Platform policy requires an active, system-defined Australia cross-region "
        "inference profile (au.*) with Australian destination models; this model is not eligible.")
    adapter.session.client.assert_not_called()


def test_legacy_settings_without_model_policy_keep_accepting_global_profiles():
    adapter, client = cloud()
    assert "model_policy" not in adapter.settings
    model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    client.get_inference_profile.return_value = eligible_profile_response(model_id)
    adapter.session.client.return_value.converse.return_value = converse_response()
    assert adapter.validate_model(model_id)["request_id"] == "req-1"
    with pytest.raises(HTTPException) as excinfo:
        adapter.validate_model(AU_ID)
    assert "global cross-region inference profile (global.*)" in excinfo.value.detail


def test_unknown_model_policy_fails_closed_as_unavailable():
    adapter, client = cloud()
    adapter.settings["model_policy"] = "us"
    for call in (adapter.models, lambda: adapter.validate_model(AU_ID)):
        with pytest.raises(HTTPException) as excinfo:
            call()
        assert excinfo.value.status_code == 503
    client.get_inference_profile.assert_not_called()
    adapter.session.client.assert_not_called()
