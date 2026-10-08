"""Retention tags for supported CloudFormation resources in the managed stacks."""
import copy

# CloudFormation uses maps for HTTP APIs/stages and a distinct Cognito property.
# Policies, routes, permissions and the other untaggable resources are omitted.
TAG_PROPERTIES = {
    "AWS::ApiGatewayV2::Api": ("Tags", dict),
    "AWS::ApiGatewayV2::Stage": ("Tags", dict),
    "AWS::Cognito::UserPool": ("UserPoolTags", dict),
    **{name: ("Tags", list) for name in (
        "AWS::S3::Bucket", "AWS::DynamoDB::Table", "AWS::SQS::Queue",
        "AWS::CloudFront::Distribution", "AWS::CloudFront::Function",
        "AWS::CloudWatch::Alarm", "AWS::IAM::Role", "AWS::Lambda::Function",
        "AWS::Lambda::EventSourceMapping", "AWS::Logs::LogGroup",
    )},
}


def apply_resource_tags(resources):
    """Add the invariant while preserving unrelated tags and template properties."""
    for resource in resources.values():
        tag_property = TAG_PROPERTIES.get(resource["Type"])
        if tag_property is None:
            continue
        field, shape = tag_property
        props = resource["Properties"]
        if shape is dict:
            props[field] = {**props.get(field, {}), "auto-delete": "no"}
        else:
            props[field] = [
                tag for tag in props.get(field, []) if tag["Key"] != "auto-delete"
            ] + [{"Key": "auto-delete", "Value": "no"}]
    return resources


def retain_legacy_pool_owner_tag(previous, proposed):
    """Carry one legacy Cognito ``owner`` tag from a verified live template.

    Earlier installations tagged the user pool with a personal ``owner``
    entry that the provider-neutral rendering no longer emits. Updating with
    the new rendering would silently strip that live tag, and the strict
    template-identity reviews rightly reject the difference. This copies
    exactly ``Resources/Pool/Properties/UserPoolTags/owner`` — a plain
    string only — from the live template read from the already identity
    checked target stack into a deep copy of the proposed rendering. It
    never adopts any other live content, never mutates its inputs, and adds
    nothing when the live template has no owner tag (fresh installations).
    The tag is retained for compatibility only; it grants nothing.
    """
    value = (previous.get("Resources", {}).get("Pool", {}).get("Properties", {})
             .get("UserPoolTags", {}).get("owner")) if isinstance(previous, dict) else None
    pool = proposed.get("Resources", {}).get("Pool", {}).get("Properties", {})
    if (not isinstance(value, str) or not isinstance(pool.get("UserPoolTags"), dict)
            or "owner" in pool["UserPoolTags"]):
        return proposed
    retained = copy.deepcopy(proposed)
    retained["Resources"]["Pool"]["Properties"]["UserPoolTags"]["owner"] = value
    return retained


def validate_resource_tags(resources):
    """Reject missing, conflicting or incorrectly shaped tags without mutation."""
    for name, resource in resources.items():
        tag_property = TAG_PROPERTIES.get(resource["Type"])
        if tag_property is None:
            continue
        field, shape = tag_property
        tags = resource.get("Properties", {}).get(field)
        valid = isinstance(tags, shape)
        if valid and shape is dict:
            valid = tags.get("auto-delete") == "no"
        elif valid:
            valid = (
                all(isinstance(tag, dict) and set(tag) == {"Key", "Value"} for tag in tags)
                and [tag["Value"] for tag in tags if tag["Key"] == "auto-delete"] == ["no"]
            )
        if not valid:
            raise RuntimeError(f"{name}: {field} must contain auto-delete=no in its CloudFormation tag shape")
