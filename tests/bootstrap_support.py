"""Stateful, provider-neutral AWS boundaries for installer recovery tests."""
import copy
import io
import json
from pathlib import Path

from botocore.exceptions import ClientError
from botocore.session import get_session
from botocore.validate import validate_parameters

from scripts import journey_platform

BINDING = {"account": "123456789012", "region": "us-east-1", "profile": "test-only"}
GATEWAY = {"id": "gab-journey-tools-test",
           "arn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/gab-journey-tools-test",
           "url": "https://gab-journey-tools-test.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"}
GATEWAY_IDENTITY = "arn:aws:bedrock-agentcore:us-east-1:123456789012:workload-identity-directory/default/workload-identity/gab-journey-tools-test"
OUTPUTS = {"GatewayRole": "arn:aws:iam::123456789012:role/gateway-test"}


class Target:
    operations_key = "platformOperations"

    def __init__(self):
        self.binding = copy.deepcopy(BINDING)
        self.state = {"target": self.binding}
        self.saves = []

    def save(self, key, value):
        self.state[key] = copy.deepcopy(value)
        self.saves.append(copy.deepcopy(self.state))


class Control:
    """Validate SDK requests and simulate accepted writes with lost acknowledgements."""
    def __init__(self, target):
        self.target = target
        self.provider = self.gw = self.target_resource = None
        self.calls, self.lose, self.tags = [], set(), {}
        self.model = get_session().get_service_model("bedrock-agentcore-control")

    def dispatch(self, operation, request, journal_name):
        validate_parameters(request, self.model.operation_model(operation).input_shape)
        record = self.target.state[self.target.operations_key][journal_name]
        assert record["status"] == "INTENT"
        if "clientToken" in request:
            assert request["clientToken"] == record["request_token"]
        self.calls.append((operation, copy.deepcopy(request)))

    def ack(self, operation, result):
        if operation in self.lose:
            raise TimeoutError("lost acknowledgement")
        return copy.deepcopy(result)

    def list_tags_for_resource(self, resourceArn):
        return {"tags": copy.deepcopy(self.tags[resourceArn])}

    def list_gateways(self, **request):
        return {"items": [self.gw] if self.gw else []}

    def create_gateway(self, **request):
        self.dispatch("CreateGateway", request, "gateway")
        self.gw = {**request, "gatewayArn": GATEWAY["arn"], "gatewayId": GATEWAY["id"],
                   "gatewayUrl": GATEWAY["url"], "status": "READY",
                   "workloadIdentityDetails": {"workloadIdentityArn": GATEWAY_IDENTITY}}
        self.tags[GATEWAY["arn"]] = copy.deepcopy(request["tags"])
        self.tags[GATEWAY_IDENTITY] = {}
        return self.ack("CreateGateway", self.gw)

    def tag_resource(self, **request):
        self.dispatch("TagResource", request, "gateway-identity-tags")
        self.tags[request["resourceArn"]].update(request["tags"])
        return self.ack("TagResource", {})

    def get_gateway(self, gatewayIdentifier):
        assert gatewayIdentifier == GATEWAY["id"]
        return copy.deepcopy(self.gw)

    def create_api_key_credential_provider(self, **request):
        raise AssertionError("Initial installation must not create provider credentials")

    def create_gateway_target(self, **request):
        raise AssertionError("Initial installation must not create remote targets")


class S3:
    def __init__(self, data=None):
        self.data, self.head, self.writes = data, None, 0
        self.lose = False

    def head_object(self, **request):
        if self.head is None:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return copy.deepcopy(self.head)

    def get_object(self, **request):
        return {"Body": io.BytesIO(self.data)}

    def upload_file(self, filename, bucket, key, *, ExtraArgs, Config):
        self.writes += 1
        self.data = Path(filename).read_bytes()
        self.head = {"ContentLength": len(self.data), "VersionId": "test-version",
                     "ServerSideEncryption": "AES256", "Metadata": ExtraArgs["Metadata"]}
        if self.lose:
            raise TimeoutError("lost upload acknowledgement")


class CloudFormation:
    def __init__(self, target):
        self.target, self.live, self.body, self.writes = target, None, None, []
        self.lose = False

    def describe_stacks(self, **request):
        if self.live is None:
            raise ClientError({"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}}, "DescribeStacks")
        return {"Stacks": [copy.deepcopy(self.live)]}

    def get_template(self, **request):
        return {"TemplateBody": copy.deepcopy(self.body)}

    def validate_template(self, **request):
        return {}

    def create_stack(self, **request):
        assert request["ClientRequestToken"] == self.target.state[self.target.operations_key]["stack"]["request_token"]
        self.writes.append(copy.deepcopy(request))
        self.body = json.loads(request["TemplateBody"])
        self.live = {"StackId": f"arn:aws:cloudformation:us-east-1:123456789012:stack/{journey_platform.STACK}/test",
                     "StackStatus": "CREATE_COMPLETE", "Tags": request["Tags"], "Parameters": request["Parameters"],
                     "Outputs": [{"OutputKey": key, "OutputValue": value} for key, value in {
                         **OUTPUTS, "RuntimeRole": "arn:aws:iam::123456789012:role/runtime-test",
                         "EvidenceBucket": "test-evidence", "TraceLogGroup": "/test/traces"}.items()
                         if key in self.body["Outputs"]]}
        if self.lose:
            raise TimeoutError("lost stack acknowledgement")
        return {"StackId": self.live["StackId"]}
