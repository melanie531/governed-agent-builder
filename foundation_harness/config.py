"""Owned harness.json schema; configuration is data, never execution authority."""
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
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


def endpoint(value, path):
    pattern = (r'https://gab-foundation-[a-z0-9-]+\.gateway\.bedrock-agentcore'
               r'\.us-west-2\.amazonaws\.com' + re.escape(path))
    if not re.fullmatch(pattern, value):
        raise ValueError('DEDICATED_GATEWAY_ENDPOINT_REQUIRED')
    return value


class Model(Frozen):
    id: str
    version: str
    endpoint: str
    route: str = Field(pattern=r'^claude/anthropic\.claude-[a-zA-Z0-9:._-]+$')
    provider: Literal['bedrock']
    protocol: Literal['messages']
    targetDigest: str = Field(pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def check(self):
        endpoint(self.endpoint, '/inference/v1/messages')
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
