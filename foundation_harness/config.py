"""Owned harness.json schema; configuration is data, never execution authority."""
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer
from jsonschema import Draft202012Validator


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class Frozen(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Ref(Frozen):
    id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')


# Reviewed dedicated-Gateway Messages target paths. '/bedrockrt/v1/messages' is
# the inbound path of the existing HTTP passthrough target named 'bedrockrt'
# (real read-only GetGatewayTarget evidence; Gateway forwards to Runtime
# /anthropic/v1/messages). Transport dispatch admits both reviewed paths;
# manifest validation still binds each protocol to exactly one path via
# exact_endpoint, so a legacy manifest can never carry the passthrough path.
MESSAGES_DISPATCH_PATHS = ('/inference/v1/messages', '/bedrockrt/v1/messages')


def exact_endpoint(value, *paths):
    host = (r'https://gab-foundation-[a-z0-9-]+\.gateway\.bedrock-agentcore'
            r'\.us-west-2\.amazonaws\.com')
    if not any(re.fullmatch(host + re.escape(path), value) for path in paths):
        raise ValueError('DEDICATED_GATEWAY_ENDPOINT_REQUIRED')
    return value


def endpoint(value, path):
    # Shared transport dispatch guard. The legacy Messages dispatch literal
    # admits every reviewed Messages target path; any other path stays exact.
    if path == '/inference/v1/messages':
        return exact_endpoint(value, *MESSAGES_DISPATCH_PATHS)
    return exact_endpoint(value, path)


class Model(Frozen):
    id: str
    version: str
    endpoint: str
    route: str
    provider: Literal['bedrock']
    protocol: Literal['messages', 'messages-passthrough']
    targetDigest: str = Field(pattern=r'^[a-f0-9]{64}$')
    transport: Literal['inference-provider', 'runtime-passthrough'] = Field(default='inference-provider', exclude=True)
    responseModels: tuple[str, ...] = Field(default=(), exclude=True)
    requestModel: str | None = Field(default=None, exclude=True)
    responseModelAllowlist: tuple[str, ...] = Field(default=(), exclude=True)

    @model_serializer(mode='wrap')
    def serialized_binding(self, handler, info):
        data = handler(self)
        if self.protocol == 'messages-passthrough':
            data['requestModel'] = self.requestModel
            data['responseModelAllowlist'] = list(self.responseModelAllowlist) if info.mode == 'json' else self.responseModelAllowlist
        elif self.transport == 'runtime-passthrough':
            data['transport'] = self.transport
            data['requestModel'] = self.requestModel
            data['responseModels'] = list(self.responseModels) if info.mode == 'json' else self.responseModels
        return data

    @model_validator(mode='after')
    def check(self):
        if self.protocol == 'messages-passthrough':
            if self.transport != 'inference-provider' or self.responseModels:
                raise ValueError('AMBIGUOUS_MESSAGES_BINDING')
            exact_endpoint(self.endpoint, '/bedrockrt/v1/messages')
            if self.requestModel != 'us.anthropic.claude-opus-5' or self.route != self.requestModel:
                raise ValueError('EXACT_OPUS_PASSTHROUGH_BINDING_REQUIRED')
            # Empty allowlist == no verified response-identity evidence yet. Such a
            # manifest is loadable/registerable-for-review, but the codec fail-closes
            # (opus_messages.read_response rejects every response) and admission blocks
            # it via UNVERIFIED_RESPONSE_IDENTITY. Never inject a synthetic id to satisfy
            # loading: the allowlist is populated ONLY from real forensic evidence.
            if (len(self.responseModelAllowlist) > 4
                    or len(set(self.responseModelAllowlist)) != len(self.responseModelAllowlist)
                    or any(not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}', x) for x in self.responseModelAllowlist)):
                raise ValueError('EXPLICIT_RESPONSE_ID_ALLOWLIST_REQUIRED')
        elif self.transport == 'runtime-passthrough':
            if not re.fullmatch(r'[A-Za-z0-9-]+/[a-zA-Z0-9:._-]+', self.route):
                raise ValueError('EXPLICIT_RUNTIME_MODEL_IDENTITIES_REQUIRED')
            target, model = self.route.split('/', 1)
            exact_endpoint(self.endpoint, '/' + target + '/v1/messages')
            if (self.requestModel != model or self.responseModelAllowlist
                    or model != 'us.anthropic.claude-haiku-4-5-20251001-v1:0'
                    or not self.responseModels or len(set(self.responseModels)) != len(self.responseModels)
                    or any(not re.fullmatch(r'anthropic\.claude-[a-zA-Z0-9:._-]+', x) for x in self.responseModels)):
                raise ValueError('EXPLICIT_EXISTING_HAIKU_BINDING_REQUIRED')
        else:
            exact_endpoint(self.endpoint, '/inference/v1/messages')
            if (not re.fullmatch(r'claude/anthropic\.claude-[a-zA-Z0-9:._-]+', self.route)
                    or self.requestModel is not None or self.responseModelAllowlist):
                raise ValueError('LEGACY_MESSAGES_BINDING_INVALID')
        return self


class Tool(Frozen):
    name: str = Field(pattern=r'^[A-Za-z0-9_-]+___[A-Za-z0-9_-]+$')
    version: str
    endpoint: str
    description: str = Field(max_length=200)
    inputSchema: dict
    schemaDigest: str = Field(pattern=r'^[a-f0-9]{64}$')
    readOnly: Literal[True]

    @model_validator(mode='after')
    def check(self):
        endpoint(self.endpoint, '/mcp')
        Draft202012Validator.check_schema(self.inputSchema)
        # No network schema resolution, including recursively nested references.
        def local(value):
            if isinstance(value, dict):
                if any(k in value for k in ('$ref', '$dynamicRef', '$id')):
                    raise ValueError('SCHEMA_REFERENCES_UNSUPPORTED')
                for v in value.values():
                    local(v)
            elif isinstance(value, list):
                for v in value:
                    local(v)
        local(self.inputSchema)
        if self.schemaDigest != digest(self.inputSchema) or self.inputSchema.get('type') != 'object':
            raise ValueError('TOOL_SCHEMA_DIGEST_MISMATCH')
        return self


class Skill(Ref):
    instructions: str = Field(min_length=1, max_length=2048)

    @model_validator(mode='after')
    def check(self):
        if digest(self.instructions) != self.digest:
            raise ValueError('SKILL_DIGEST_MISMATCH')
        return self


class Prompt(Frozen):
    text: str = Field(min_length=1, max_length=2048)


class Evaluation(Frozen):
    dataset: Ref
    rubric: Ref


class Limits(Frozen):
    maxIterations: int = Field(ge=1, le=8, strict=True)
    maxModelCalls: int = Field(ge=1, le=4, strict=True)
    maxToolCalls: int = Field(ge=0, le=4, strict=True)
    maxInputTokens: int = Field(ge=1, le=2000, strict=True)
    maxOutputTokens: int = Field(ge=1, le=256, strict=True)
    timeoutSeconds: int = Field(ge=1, le=60, strict=True)


class HarnessConfig(Frozen):
    schemaVersion: Literal['owned-foundation-v1']
    name: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
    version: str
    foundation: Ref
    model: Model
    systemPrompt: tuple[Prompt, ...] = Field(min_length=1, max_length=4)
    tools: tuple[Tool, ...] = Field(max_length=4)
    allowedTools: tuple[str, ...]
    skills: tuple[Skill, ...] = Field(max_length=4)
    evaluation: Evaluation
    limits: Limits

    @model_validator(mode='after')
    def check(self):
        if self.model.protocol == 'messages-passthrough':
            if (self.tools or self.allowedTools or self.skills
                    or self.limits.maxIterations != 1 or self.limits.maxModelCalls != 1
                    or self.limits.maxToolCalls != 0):
                raise ValueError('OPUS_FIRST_SINGLE_CALL_NO_TOOLS_REQUIRED')
        names = [t.name for t in self.tools]
        if (len(set(names)) != len(names) or sorted(names) != sorted(self.allowedTools)
                or len({(s.id, s.version) for s in self.skills}) != len(self.skills)):
            raise ValueError('EXACT_UNIQUE_SELECTION_REQUIRED')
        if len({t.endpoint for t in self.tools}) > 1:
            raise ValueError('ONE_DEDICATED_TOOLS_GATEWAY_REQUIRED')
        if any(t.endpoint.split('/')[2] == self.model.endpoint.split('/')[2] for t in self.tools):
            raise ValueError('DISTINCT_GATEWAYS_REQUIRED')
        return self


def load_config(raw, expected_digest):
    if len(canonical(raw)) > 32768 or digest(raw) != expected_digest:
        raise ValueError('MANIFEST_DIGEST_MISMATCH')
    return HarnessConfig.model_validate_json(canonical(raw))
