"""Pure admission/compiler: no SDK client, network, Registry discovery or loop.

A compiled plan is evidence of source composition, NEVER execution authority.
Re-admit against fresh trusted catalogs/grants on every future invocation.
"""
import hashlib
import json
from typing import Literal
from .domain_harness_schema import (
    AuthorizationContext, Catalogs, DomainHarnessDefinition, FoundationDefinition,
    FoundationLibrary, Frozen, ModelRoute, ToolBinding, Skill, EvaluationArtifact,
)


def digest(value):
    if isinstance(value, Frozen):
        value = value.model_dump(mode='json')
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class AdmissionDenied(ValueError):
    """Static codes only: no raw prompts, catalog values or credentials."""


class CompiledPlan(Frozen):
    schema_version: Literal[1] = 1
    compiler_contract: Literal['domain-harness-v2'] = 'domain-harness-v2'
    domain: DomainHarnessDefinition
    domain_digest: str
    foundation: FoundationDefinition
    model_route: ModelRoute
    tool_bindings: tuple[ToolBinding, ...]
    allowed_tools: tuple[str, ...]
    skills: tuple[Skill, ...]
    dataset: EvaluationArtifact
    rubric: EvaluationArtifact
    memory_namespace_digest: str
    release_binding_digest: str
    authorization_digest: str
    readiness: tuple[str, ...]
    execution_ready: Literal[False] = False

    @property
    def version_digest(self):
        return digest(self)

    def preview(self):
        """Safe metadata only. No prompt, source locations or native config."""
        return {'schema_version': self.schema_version, 'version_digest': self.version_digest,
                'domain_version': self.domain.ref.version,
                'foundation_source_digest': self.foundation.source_digest,
                'foundation_version': self.foundation.ref.version,
                'release_binding_digest': self.release_binding_digest,
                'identity_required': True, 'observability_required': True,
                'model_protocol': self.model_route.protocol,
                'capabilities': sorted({t.capability for t in self.tool_bindings}),
                'tool_count': len(self.tool_bindings), 'skill_count': len(self.skills),
                'readiness': list(self.readiness), 'execution_ready': False,
                'acceptance_scope': 'compiler-only'}


def compile_plan(domain: DomainHarnessDefinition, *, foundations: FoundationLibrary,
                 authorization: AuthorizationContext, catalogs: Catalogs) -> CompiledPlan:
    if (domain.owner, domain.workspace) != (authorization.subject, authorization.workspace):
        raise AdmissionDenied('CROSS_WORKSPACE_OR_OWNER')
    if (authorization.session.owner, authorization.session.workspace) != (domain.owner, domain.workspace):
        raise AdmissionDenied('SESSION_OWNERSHIP_DENIED')
    if digest(domain) != authorization.approved_domain_digest:
        raise AdmissionDenied('DOMAIN_VERSION_NOT_APPROVED')
    grants = {(g.kind, g.ref.id, g.ref.version): g for g in authorization.grants}
    if len(grants) != len(authorization.grants):
        raise AdmissionDenied('DUPLICATE_GRANT')

    def resolve(kind, ref, entries):
        keys = [(e.ref.id, e.ref.version) for e in entries]
        if len(set(keys)) != len(keys):
            raise AdmissionDenied('AMBIGUOUS_CATALOG')
        item = next((e for e in entries if e.ref == ref), None)
        if item is None or not item.approved:
            raise AdmissionDenied('UNKNOWN_OR_UNAPPROVED_' + kind.upper())
        if kind != 'foundation' and item.workspace != authorization.workspace:
            raise AdmissionDenied('CROSS_WORKSPACE_CATALOG')
        grant = grants.get((kind, ref.id, ref.version))
        if not grant or grant.epoch != authorization.epoch or grant.entry_digest != digest(item):
            raise AdmissionDenied('MISSING_OR_STALE_GRANT')
        return item

    foundation = resolve('foundation', domain.foundation, foundations.versions)
    model = resolve('model', domain.model_route, catalogs.model_routes)
    tools = tuple(resolve('tool', r, catalogs.tool_bindings) for r in domain.tool_bindings)
    skills = tuple(resolve('skill', r, catalogs.skills) for r in domain.skills)
    dataset = resolve('dataset', domain.dataset, catalogs.datasets)
    rubric = resolve('rubric', domain.rubric, catalogs.rubrics)
    if authorization.role_binding != foundation.identity.role_binding:
        raise AdmissionDenied('IDENTITY_ROLE_BINDING_DENIED')
    if model.model_gateway not in foundation.identity.model_gateways:
        raise AdmissionDenied('IDENTITY_MODEL_GATEWAY_DENIED')
    if any(t.tool_gateway not in foundation.identity.tool_gateways for t in tools):
        raise AdmissionDenied('IDENTITY_TOOL_GATEWAY_DENIED')
    for refs in (domain.tool_bindings, domain.skills):
        if len(set(refs)) != len(refs):
            raise AdmissionDenied('DUPLICATE_SELECTION')
    if any(t.capability not in foundation.capabilities for t in tools):
        raise AdmissionDenied('FOUNDATION_CAPABILITY_DENIED')
    if any(v > getattr(foundation.limits, k) for k, v in domain.limits.model_dump().items()):
        raise AdmissionDenied('FOUNDATION_LIMIT_EXCEEDED')
    if any(not s.immutable_approved for s in (*skills, dataset, rubric)):
        raise AdmissionDenied('MUTABLE_CONTENT_NOT_APPROVED')
    allowed = tuple(sorted(f'@{t.gateway_name}/{t.operation}' for t in tools))
    if len(set(allowed)) != len(allowed):
        raise AdmissionDenied('AMBIGUOUS_TOOL_ALLOWLIST')
    names = {}
    for tool in tools:
        if tool.gateway_name in names and names[tool.gateway_name] != tool.tool_gateway:
            raise AdmissionDenied('AMBIGUOUS_GATEWAY_NAME')
        names[tool.gateway_name] = tool.tool_gateway
    readiness = ['BLOCKED_NATIVE_INTEGRATION', 'BLOCKED_EVALUATION_GATEWAY_ROUTING',
                 'BLOCKED_IDENTITY_RUNTIME_BINDING', 'BLOCKED_OBSERVABILITY_PIPELINE',
                 'BLOCKED_MEMORY_ISOLATION', 'BLOCKED_EVALUATION_RELEASE_EVIDENCE',
                 'BLOCKED_EXECUTION_LIMIT_ENFORCEMENT']
    if not (model.adapter and model.gateway_auth != 'unverified' and model.auth_evidence_digest):
        readiness.append('BLOCKED_MODEL_GATEWAY_AUTH')
    if any(not t.policy_verified for t in tools):
        readiness.append('BLOCKED_TOOL_GATEWAY_POLICY')
    if any(t.capability == 'browser' and not (t.adapter and t.adapter_verified) for t in tools):
        readiness.append('BLOCKED_BROWSER_GATEWAY_ADAPTER')
    if skills and not foundation.native_skill_loading_verified:
        readiness.append('BLOCKED_NATIVE_SKILL_LOADING')
    return CompiledPlan(domain=domain, domain_digest=digest(domain), foundation=foundation,
                        model_route=model, tool_bindings=tools, allowed_tools=allowed,
                        skills=skills, dataset=dataset, rubric=rubric,
                        memory_namespace_digest=digest([domain.owner, domain.workspace, authorization.session.id]),
                        release_binding_digest=digest({
                            'foundation': digest(foundation), 'domain': digest(domain),
                            'baseline': foundation.evaluation.model_dump(mode='json'),
                            'dataset': digest(dataset), 'rubric': digest(rubric),
                            'model': digest(model), 'tools': [digest(t) for t in tools],
                            'skills': [digest(s) for s in skills]}),
                        authorization_digest=digest(authorization), readiness=tuple(sorted(readiness)))
