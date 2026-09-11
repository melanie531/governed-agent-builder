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


class IdentityProfile(Frozen):
    provider: Literal['agentcore_identity'] = 'agentcore_identity'
    subject_workspace: Literal['verified_server_context'] = 'verified_server_context'
    session_ownership: Literal['owner_workspace_session'] = 'owner_workspace_session'
    role_binding: Ref
    model_gateways: tuple[Ref, ...] = Field(min_length=1)
    tool_gateways: tuple[Ref, ...] = Field(min_length=1)
    outbound_credentials_policy: Ref
    policy_enforcement: Literal['gateway_required'] = 'gateway_required'
    jwt_to_iam_conversion: Literal[False] = False
    client_actor_override: Literal[False] = False


TRACE_FIELDS = ('trace_id', 'request_id', 'run_id', 'foundation_version',
                'domain_version', 'release_binding_digest', 'model_ref', 'tool_ref',
                'gateway_ref', 'browser_ref', 'event_type', 'latency_ms',
                'token_usage_when_available', 'error_category', 'denial_category')
EVENT_TYPES = ('admission', 'model_call', 'tool_call', 'browser_operation',
               'evaluation', 'error', 'denial', 'completion')


class ObservabilityProfile(Frozen):
    provider: Literal['agentcore_observability'] = 'agentcore_observability'
    cloudwatch_required: Literal[True] = True
    otel_required: Literal[True] = True
    audit_required: Literal[True] = True
    audit_separate_from_reasoning: Literal[True] = True
    raw_credentials: Literal[False] = False
    full_prompt_default: Literal[False] = False
    trace_fields: tuple[str, ...] = TRACE_FIELDS
    event_types: tuple[str, ...] = EVENT_TYPES
    splunk_export: Ref | None = None
    splunk_export_approval_digest: Digest | None = None

    @model_validator(mode='after')
    def fixed_redaction_contract(self):
        if self.trace_fields != TRACE_FIELDS or self.event_types != EVENT_TYPES:
            raise ValueError('Mandatory telemetry allowlist cannot be overridden')
        if (self.splunk_export is None) != (self.splunk_export_approval_digest is None):
            raise ValueError('Optional Splunk export requires explicit approval evidence')
        return self


class MemoryProfile(Frozen):
    default_policy: Literal['disabled'] = 'disabled'
    namespace: Literal['owner_workspace_session'] = 'owner_workspace_session'
    cross_namespace_access: Literal[False] = False


class EvaluationProfile(Frozen):
    baseline_dataset: Ref
    baseline_dataset_digest: Digest
    baseline_rubric: Ref
    baseline_rubric_digest: Digest
    domain_layer: Literal['baseline_and_domain_rubric'] = 'baseline_and_domain_rubric'
    release_evidence_required: Literal[True] = True


class ExecutionProfile(Frozen):
    max_retries: Annotated[int, Field(strict=True, ge=0, le=3)]
    retry_scope: Literal['transient_idempotent_only'] = 'transient_idempotent_only'
    retry_on_denial: Literal[False] = False
    total_timeout_includes_retries: Literal[True] = True


class FoundationDefinition(Frozen):
    ref: Ref
    source_digest: Digest
    approved: bool
    gateway_only: Literal[True] = True
    capabilities: tuple[Literal['browser', 'readonly_mcp'], ...]
    limits: Limits
    native_skill_loading_verified: bool = False
    identity: IdentityProfile
    observability: ObservabilityProfile
    memory: MemoryProfile
    evaluation: EvaluationProfile
    execution: ExecutionProfile


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


class TrustedSession(Frozen):
    id: Id
    owner: Id
    workspace: Id


class AuthorizationContext(Frozen):
    subject: Id
    workspace: Id
    epoch: Annotated[int, Field(strict=True, ge=1)]
    approved_domain_digest: Digest
    grants: tuple[Grant, ...]
    session: TrustedSession
    role_binding: Ref
