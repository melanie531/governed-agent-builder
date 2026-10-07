"""Guard regressions for the scoped global-policy release script.

Covers the two fail-closed review gates: the proposed template may differ from
the live template by exactly one reviewed IAM statement, and an evaluated
change set may contain only the reviewed role/function updates plus the benign
reference cascade a Lambda code update causes on ApiGatewayV2 integrations and
authorizers. All identifiers below are synthetic.
"""
import copy

import pytest

from scripts.scoped_global_policy_release import (
    FUNCTIONS,
    reviewed_evaluated_changes,
    reviewed_template_change,
)

ACCOUNT = "123456789012"  # AWS documentation placeholder


def live_template():
    return {
        "Resources": {
            "BusinessRole": {
                "Properties": {
                    "Policies": [
                        {"PolicyName": "Logs", "PolicyDocument": {"Statement": []}},
                        {"PolicyName": "PlatformAdministration", "PolicyDocument": {"Statement": [
                            {"Effect": "Allow", "Action": ["dynamodb:Query"], "Resource": ["*"]},
                            {"Effect": "Allow", "Action": ["bedrock:InvokeModel"], "Resource": ["*"]},
                        ]}},
                    ]
                }
            },
            "Worker": {"Properties": {}},
        }
    }


def reviewed_statement():
    return {"Effect": "Allow", "Action": ["bedrock:GetInferenceProfile"],
            "Resource": [f"arn:aws:bedrock:*:{ACCOUNT}:inference-profile/*"]}


def proposed_template():
    body = live_template()
    statements = body["Resources"]["BusinessRole"]["Properties"]["Policies"][1]["PolicyDocument"]["Statement"]
    statements.insert(1, reviewed_statement())
    return body


class TestReviewedTemplateChange:
    def test_exact_reviewed_statement_accepted(self):
        assert reviewed_template_change(live_template(), proposed_template(), ACCOUNT) is True

    def test_any_additional_delta_rejected(self):
        proposed = proposed_template()
        proposed["Resources"]["Worker"]["Properties"]["Timeout"] = 900
        with pytest.raises(ValueError, match="more than the exact reviewed IAM statement"):
            reviewed_template_change(live_template(), proposed, ACCOUNT)

    def test_statement_for_wrong_account_rejected(self):
        before = live_template()
        proposed = copy.deepcopy(before)
        statements = proposed["Resources"]["BusinessRole"]["Properties"]["Policies"][1]["PolicyDocument"]["Statement"]
        statements.insert(1, {"Effect": "Allow", "Action": ["bedrock:GetInferenceProfile"],
                              "Resource": ["arn:aws:bedrock:*:111122223333:inference-profile/*"]})
        with pytest.raises(ValueError, match="more than the exact reviewed IAM statement"):
            reviewed_template_change(before, proposed, ACCOUNT)

    def test_already_present_statement_rejected(self):
        before = proposed_template()
        with pytest.raises(ValueError, match="already contains the reviewed statement"):
            reviewed_template_change(before, copy.deepcopy(before), ACCOUNT)


def modify(name, resource_type, details=None, action="Modify", replacement="False"):
    return {"ResourceChange": {"LogicalResourceId": name, "ResourceType": resource_type,
                               "Action": action, "Replacement": replacement,
                               "Details": details if details is not None else []}}


def cascade_detail(causing_entity, change_source="ResourceAttribute", recreation="Never"):
    return {"Target": {"Attribute": "Properties", "Name": "IntegrationUri",
                       "RequiresRecreation": recreation},
            "Evaluation": "Dynamic", "ChangeSource": change_source,
            "CausingEntity": causing_entity}


def reviewed_change_set(extra=None):
    changes = [modify("BusinessRole", "AWS::IAM::Role")]
    changes += [modify(fn, "AWS::Lambda::Function") for fn in sorted(FUNCTIONS)]
    changes += [
        modify("AuthIntegration", "AWS::ApiGatewayV2::Integration", [cascade_detail("Auth.Arn")]),
        modify("SessionAuthorizer", "AWS::ApiGatewayV2::Authorizer", [cascade_detail("Authorizer.Arn")]),
    ]
    if extra is not None:
        changes.append(extra)
    return {"Status": "CREATE_COMPLETE", "Changes": changes}


class TestReviewedEvaluatedChanges:
    def test_reviewed_updates_with_reference_cascade_accepted(self):
        assert reviewed_evaluated_changes(reviewed_change_set()) == sorted({"BusinessRole"} | FUNCTIONS)

    def test_unknown_replacement_value_accepted_only_when_absent(self):
        change = reviewed_change_set()
        change["Changes"][0]["ResourceChange"]["Replacement"] = None
        assert reviewed_evaluated_changes(change) == sorted({"BusinessRole"} | FUNCTIONS)

    def test_incomplete_change_set_rejected(self):
        with pytest.raises(ValueError, match="not complete"):
            reviewed_evaluated_changes({"Status": "CREATE_IN_PROGRESS", "Changes": []})
        with pytest.raises(ValueError, match="not complete"):
            reviewed_evaluated_changes({"Status": "CREATE_COMPLETE", "NextToken": "more", "Changes": []})

    def test_unknown_resource_rejected(self):
        extra = modify("UserPool", "AWS::Cognito::UserPool")
        with pytest.raises(ValueError, match="Unreviewed change"):
            reviewed_evaluated_changes(reviewed_change_set(extra))

    def test_non_modify_action_rejected(self):
        extra = modify("Worker", "AWS::Lambda::Function", action="Add")
        with pytest.raises(ValueError, match="Unreviewed change"):
            reviewed_evaluated_changes(reviewed_change_set(extra))

    def test_replacement_rejected(self):
        extra = modify("Worker", "AWS::Lambda::Function", replacement="True")
        with pytest.raises(ValueError, match="Unreviewed change"):
            reviewed_evaluated_changes(reviewed_change_set(extra))

    def test_conditional_replacement_rejected_everywhere(self):
        for extra in (modify("Worker", "AWS::Lambda::Function", replacement="Conditional"),
                      modify("BusinessIntegration", "AWS::ApiGatewayV2::Integration",
                             [cascade_detail("Business.Arn")], replacement="Conditional")):
            with pytest.raises(ValueError, match="Unreviewed change"):
                reviewed_evaluated_changes(reviewed_change_set(extra))

    def test_cascade_resource_with_non_modify_action_rejected(self):
        extra = modify("BusinessIntegration", "AWS::ApiGatewayV2::Integration",
                       [cascade_detail("Business.Arn")], action="Add")
        with pytest.raises(ValueError, match="Unreviewed change"):
            reviewed_evaluated_changes(reviewed_change_set(extra))

    def test_cascade_with_mixed_valid_and_invalid_details_rejected(self):
        # Pins the every-Detail requirement: one benign detail must not excuse
        # a direct modification in the same resource change.
        extra = modify("BusinessIntegration", "AWS::ApiGatewayV2::Integration",
                       [cascade_detail("Business.Arn"),
                        cascade_detail("Business.Arn", change_source="DirectModification")])
        with pytest.raises(ValueError, match="Unreviewed change"):
            reviewed_evaluated_changes(reviewed_change_set(extra))

    def test_cascade_with_empty_details_rejected(self):
        extra = modify("BusinessIntegration", "AWS::ApiGatewayV2::Integration", [])
        with pytest.raises(ValueError, match="Unreviewed change"):
            reviewed_evaluated_changes(reviewed_change_set(extra))

    def test_cascade_direct_modification_rejected(self):
        extra = modify("BusinessIntegration", "AWS::ApiGatewayV2::Integration",
                       [cascade_detail("Business.Arn", change_source="DirectModification")])
        with pytest.raises(ValueError, match="Unreviewed change"):
            reviewed_evaluated_changes(reviewed_change_set(extra))

    def test_cascade_from_unreviewed_entity_rejected(self):
        extra = modify("BusinessIntegration", "AWS::ApiGatewayV2::Integration",
                       [cascade_detail("SomethingElse.Arn")])
        with pytest.raises(ValueError, match="Unreviewed change"):
            reviewed_evaluated_changes(reviewed_change_set(extra))

    def test_cascade_requiring_recreation_rejected(self):
        extra = modify("BusinessIntegration", "AWS::ApiGatewayV2::Integration",
                       [cascade_detail("Business.Arn", recreation="Always")])
        with pytest.raises(ValueError, match="Unreviewed change"):
            reviewed_evaluated_changes(reviewed_change_set(extra))

    def test_cascade_type_not_in_allowlist_rejected(self):
        extra = modify("StateTable", "AWS::DynamoDB::Table", [cascade_detail("Worker.Arn")])
        with pytest.raises(ValueError, match="Unreviewed change"):
            reviewed_evaluated_changes(reviewed_change_set(extra))

    def test_missing_role_or_function_updates_rejected(self):
        no_role = {"Status": "CREATE_COMPLETE",
                   "Changes": [modify(fn, "AWS::Lambda::Function") for fn in sorted(FUNCTIONS)]}
        with pytest.raises(ValueError, match="missing the reviewed role or code updates"):
            reviewed_evaluated_changes(no_role)
        no_code = {"Status": "CREATE_COMPLETE", "Changes": [modify("BusinessRole", "AWS::IAM::Role")]}
        with pytest.raises(ValueError, match="missing the reviewed role or code updates"):
            reviewed_evaluated_changes(no_code)
