"""Guard regressions for the scoped second-region recovery script.

Covers the fail-closed review gate: the proposed template may differ from the
live CREATE_FAILED template by exactly the three region-scoped CloudFront
names (OAC, SPA, Headers), and the live values must still be the pre-fix
global names. All identifiers below are synthetic.
"""
import copy

import pytest

from scripts.scoped_second_region_recovery import reviewed_template_change


def live_template():
    return {
        "Resources": {
            "OAC": {"Properties": {"OriginAccessControlConfig": {
                "Name": {"Fn::Sub": "${AWS::StackName}-s3"}, "SigningProtocol": "sigv4"}}},
            "SPA": {"Properties": {
                "Name": {"Fn::Sub": "${AWS::StackName}-spa"}, "AutoPublish": True}},
            "Headers": {"Properties": {"ResponseHeadersPolicyConfig": {
                "Name": {"Fn::Sub": "${AWS::StackName}-headers"}, "SecurityHeadersConfig": {}}}},
            "Worker": {"Properties": {"Timeout": 60}},
        }
    }


def proposed_template():
    body = live_template()
    body["Resources"]["OAC"]["Properties"]["OriginAccessControlConfig"]["Name"] = {"Fn::Sub": "${AWS::StackName}-s3-${AWS::Region}"}
    body["Resources"]["SPA"]["Properties"]["Name"] = {"Fn::Sub": "${AWS::StackName}-spa-${AWS::Region}"}
    body["Resources"]["Headers"]["Properties"]["ResponseHeadersPolicyConfig"]["Name"] = {"Fn::Sub": "${AWS::StackName}-headers-${AWS::Region}"}
    return body


class TestReviewedTemplateChange:
    def test_exact_three_name_diff_accepted(self):
        assert reviewed_template_change(live_template(), proposed_template()) is True

    def test_any_additional_delta_rejected(self):
        proposed = proposed_template()
        proposed["Resources"]["Worker"]["Properties"]["Timeout"] = 900
        with pytest.raises(ValueError, match="more than the three reviewed CloudFront names"):
            reviewed_template_change(live_template(), proposed)

    def test_partial_rename_rejected(self):
        proposed = proposed_template()
        proposed["Resources"]["SPA"]["Properties"]["Name"] = {"Fn::Sub": "${AWS::StackName}-spa"}
        with pytest.raises(ValueError, match="more than the three reviewed CloudFront names"):
            reviewed_template_change(live_template(), proposed)

    @pytest.mark.parametrize("logical", ["OAC", "SPA", "Headers"])
    def test_live_name_not_matching_expected_old_value_rejected(self, logical):
        before = live_template()
        unexpected = {"Fn::Sub": "${AWS::StackName}-other"}
        if logical == "OAC":
            before["Resources"]["OAC"]["Properties"]["OriginAccessControlConfig"]["Name"] = unexpected
        elif logical == "SPA":
            before["Resources"]["SPA"]["Properties"]["Name"] = unexpected
        else:
            before["Resources"]["Headers"]["Properties"]["ResponseHeadersPolicyConfig"]["Name"] = unexpected
        with pytest.raises(ValueError, match="not the expected pre-fix global name"):
            reviewed_template_change(before, proposed_template())

    def test_already_region_scoped_live_template_rejected(self):
        before = proposed_template()
        with pytest.raises(ValueError, match="not the expected pre-fix global name"):
            reviewed_template_change(before, copy.deepcopy(before))
