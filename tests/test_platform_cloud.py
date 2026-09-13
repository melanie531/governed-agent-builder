from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from backend.platform_cloud import PlatformCloud


def cloud():
    adapter = PlatformCloud({"account": "123456789012", "region": "us-west-2"}, session=Mock())
    client = Mock()
    adapter.client = Mock(return_value=client)
    return adapter, client


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
