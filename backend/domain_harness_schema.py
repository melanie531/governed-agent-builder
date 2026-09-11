"""Immutable compiler inputs. Only DomainHarnessDefinition is user-authored.

FoundationLibrary, Catalogs and AuthorizationContext MUST be supplied by a
trusted server-side authority, never deserialized from an HTTP request body.
"""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_.:-]{0,127}$')]
Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class Frozen(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Ref(Frozen):
    id: Id
    version: Annotated[int, Field(strict=True, ge=1)]


class Limits(Frozen):
    max_iterations: Annotated[int, Field(strict=True, ge=1, le=100)]
    max_tokens: Annotated[int, Field(strict=True, ge=1, le=100000)]
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=3600)]
    eval_cases: Annotated[int, Field(strict=True, ge=1, le=1000)]


class FoundationDefinition(Frozen):
    ref: Ref
    source_digest: Digest
    approved: bool
    gateway_only: Literal[True] = True
    capabilities: tuple[Literal['browser', 'readonly_mcp'], ...]
    limits: Limits
    native_skill_loading_verified: bool = False


class FoundationLibrary(Frozen):
    """Deliberately not a Registry catalog entry."""
    versions: tuple[FoundationDefinition, ...]


class DomainHarnessDefinition(Frozen):
    schema_version: Literal[1] = 1
    ref: Ref
    owner: Id
    workspace: Id
    foundation: Ref
    model_route: Ref
    tool_bindings: tuple[Ref, ...] = ()
    skills: tuple[Ref, ...] = ()
    system_prompt: Annotated[str, Field(min_length=1, max_length=8000)]
    dataset: Ref
    rubric: Ref
    limits: Limits


class CatalogEntry(Frozen):
    ref: Ref
    workspace: Id
    approved: bool


class ModelRoute(CatalogEntry):
    provider: Literal['bedrock-claude', 'bedrock-openai']
    protocol: Literal['messages', 'chat_completions', 'responses']
    model_gateway: Ref
    target_model: Id
    adapter: Ref | None = None
    gateway_auth: Literal['unverified', 'native_iam', 'renewable_oauth'] = 'unverified'
    auth_evidence_digest: Digest | None = None


class ToolBinding(CatalogEntry):
    capability: Literal['browser', 'readonly_mcp']
    tool_gateway: Ref
    gateway_name: Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')]
    operation: Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,127}$')]
    adapter: Ref | None = None
    policy_verified: bool = False
    adapter_verified: bool = False

    @model_validator(mode='after')
    def restricted_operation(self):
        if self.operation.lower() in {'shell', 'file_operations', 'invokeagentruntimecommand'}:
            raise ValueError('Forbidden operation')
        return self


class Skill(CatalogEntry):
    content_digest: Digest
    source_ref: Annotated[str, Field(min_length=1, max_length=500)]
    immutable_approved: bool
    requires_shell: Literal[False] = False


class EvaluationArtifact(CatalogEntry):
    content_digest: Digest
    source_ref: Annotated[str, Field(min_length=1, max_length=500)]
    immutable_approved: bool


class Catalogs(Frozen):
    model_routes: tuple[ModelRoute, ...]
    tool_bindings: tuple[ToolBinding, ...]
    skills: tuple[Skill, ...]
    datasets: tuple[EvaluationArtifact, ...]
    rubrics: tuple[EvaluationArtifact, ...]


class Grant(Frozen):
    kind: Literal['foundation', 'model', 'tool', 'skill', 'dataset', 'rubric']
    ref: Ref
    entry_digest: Digest
    epoch: Annotated[int, Field(strict=True, ge=1)]


class AuthorizationContext(Frozen):
    subject: Id
    workspace: Id
    epoch: Annotated[int, Field(strict=True, ge=1)]
    approved_domain_digest: Digest
    grants: tuple[Grant, ...]
