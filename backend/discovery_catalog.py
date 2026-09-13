"""Governed Bedrock foundation-model discovery catalog.

Data source of record for the Studio Models tab. Rows are built ONLY from real
bedrock:ListFoundationModels fields (or a backend-cached snapshot of that exact
API response) — never a hand-curated / guessed model list.

What is real and passed through unchanged from ListFoundationModels:
  modelId, modelName, providerName, inputModalities[], outputModalities[],
  responseStreamingSupported, inferenceTypesSupported[], modelLifecycle.status
\nWhat is DERIVED (deterministically, from the real modality fields, not guessed):
  category  <- input/output modalities (see derive_category)

Recency (last-6-months) is a REAL rolling-window filter over VERIFIED launch
dates, not a lifecycle proxy. ListFoundationModels itself carries no launch
date, so each row is joined by EXACT modelId against a reviewer-verified
launch-date map (backend/model_launch_dates.json, each entry sourced from an
official AWS model card with source_url + content hash). See backend.model_recency.
A model is `recency == 'recent'` ONLY IF its verified launch_date is within
[today - 6 months, today] (window computed server-side). Missing date, future
date, unmatched id, or a conflict -> `recency == 'pending_verification'`.

modelLifecycle.status (ACTIVE|LEGACY) is passed through as `lifecycle` for
lifecycle-info display ONLY. It is NOT used for recency.

Discovery != callable. Every discovered row is `discoverable: True` but
`execution_ready: False` with an unverified execution binding: a model appearing
in the discovery catalog is NOT proof it is wired, granted, or callable. Grant /
readiness state continues to come exclusively from real Entry/status fields.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .model_recency import apply_recency, load_launch_date_map

VALID_CATEGORIES = ('text', 'multimodal', 'image', 'embeddings', 'speech')


def derive_category(input_modalities, output_modalities):
    """Map real Bedrock modality lists to a display category. Pure function of
    the API's inputModalities/outputModalities — no per-model overrides."""
    inputs = {m for m in (input_modalities or []) if isinstance(m, str)}
    outputs = {m for m in (output_modalities or []) if isinstance(m, str)}
    if 'EMBEDDING' in outputs:
        return 'embeddings'
    if 'IMAGE' in outputs or 'VIDEO' in outputs:
        return 'image'
    if 'SPEECH' in inputs or 'SPEECH' in outputs:
        return 'speech'
    if inputs <= {'TEXT'} and outputs <= {'TEXT'}:
        return 'text'
    if 'IMAGE' in inputs or 'VIDEO' in inputs:
        return 'multimodal'
    return 'text'


def normalize_model(summary):
    """Turn one ListFoundationModels summary into a governed catalog row.

    Only real fields are read; unknown/malformed summaries are rejected so a bad
    snapshot can never silently fabricate a row."""
    model_id = summary.get('modelId')
    provider = summary.get('providerName')
    if (not isinstance(model_id, str) or not model_id
            or not isinstance(provider, str) or not provider):
        raise ValueError('Invalid foundation model summary')
    inputs = summary.get('inputModalities') or []
    outputs = summary.get('outputModalities') or []
    if (not isinstance(inputs, list) or not isinstance(outputs, list)
            or any(not isinstance(x, str) for x in inputs + outputs)):
        raise ValueError('Invalid model modalities')
    lifecycle = (summary.get('modelLifecycle') or {}).get('status')
    if lifecycle not in ('ACTIVE', 'LEGACY', None):
        raise ValueError('Unexpected model lifecycle status')
    inference_types = summary.get('inferenceTypesSupported') or []
    if not isinstance(inference_types, list) or any(not isinstance(x, str) for x in inference_types):
        raise ValueError('Invalid inference types')
    streaming = summary.get('responseStreamingSupported')
    name = summary.get('modelName') or model_id
    category = derive_category(inputs, outputs)
    return {
        'id': f'discovery:bedrock:{model_id}',
        'record_id': f'discovery:bedrock:{model_id}',
        'kind': 'model',
        'name': name,
        'provider': provider,
        'description': f'{provider} foundation model discovered via bedrock:ListFoundationModels. '
                       f'Listed for discovery only; execution binding is not verified.',
        'capabilities': [],
        'version': '1',
        # Real, pass-through fields:
        'model_id': model_id,
        'native_model_id': model_id,
        'category': category,
        'lifecycle': lifecycle,
        'streaming': bool(streaming),
        'inference_types': list(inference_types),
        'input_modalities': list(inputs),
        'output_modalities': list(outputs),
        # Governance / provenance:
        'provenance': 'bedrock:ListFoundationModels',
        'origin': 'Bedrock foundation-model discovery',
        'source_type': 'discovery_catalog',
        'external': False,
        'approved': True,
        'discoverable': True,
        'discoverable_workspaces': ['research', 'operations', 'platform'],   # discovery visible to all demo workspaces
        'requestable': False,              # discovery is read-only; not a grant path
        'fixture': False,
        'protocol': 'discovery-metadata',
        'supported': True,
        # Discovery is NOT callable. Readiness/binding are honest-negative here;
        # any real execution state comes from a separate wired route, not this.
        'integration_ready': False,
        'execution_ready': False,
        'execution_binding': {'status': 'unverified', 'last_checked': None},
        'data_handling': 'Discovery metadata only; no execution or data flow',
        'owner': 'Platform (Bedrock account catalog)',
        'refreshed_at': time.time(),
    }


@dataclass
class DiscoveryFoundationCatalog:
    """CatalogProvider backed by bedrock:ListFoundationModels or a cached snapshot.

    - client:   a bedrock (control-plane) client exposing list_foundation_models.
                When None, snapshot_path must be provided (backend-cached copy).
    - snapshot_path: path to a JSON file holding a real ListFoundationModels
                response ({"modelSummaries":[...]}). Used as a backend-maintained
                cache; still real data, not a guessed list.
    - region:   expected region label (recorded, not used to fabricate data).
    """
    client: object = None
    snapshot_path: str | None = None
    region: str = 'us-west-2'
    launch_date_map_path: str | None = None
    _cache: list = None
    _expires: float = 0
    ttl: float = 60

    def _summaries(self):
        if self.client is not None:
            response = self.client.list_foundation_models()
            if not isinstance(response, dict) or not isinstance(response.get('modelSummaries'), list):
                raise ValueError('Invalid ListFoundationModels envelope')
            return response['modelSummaries']
        if self.snapshot_path:
            raw = Path(self.snapshot_path).read_text()
            data = json.loads(raw)
            summaries = data.get('modelSummaries') if isinstance(data, dict) else None
            if not isinstance(summaries, list):
                raise ValueError('Invalid cached ListFoundationModels snapshot')
            return summaries
        raise ValueError('DiscoveryFoundationCatalog requires a client or snapshot_path')

    def records(self):
        now = time.monotonic()
        if self._cache is not None and now < self._expires:
            return list(self._cache)
        rows = []
        seen = set()
        for summary in self._summaries():
            if not isinstance(summary, dict):
                raise ValueError('Invalid model summary')
            row = normalize_model(summary)
            if row['model_id'] in seen:
                continue
            seen.add(row['model_id'])
            rows.append(row)
        if len(rows) > 500:
            raise ValueError('Discovery catalog too large')
        # Enrich with VERIFIED launch-date recency (real rolling window), NOT
        # a lifecycle proxy. Missing/future/out-of-window/unmatched -> pending.
        launch_map = load_launch_date_map(self.launch_date_map_path)
        apply_recency(rows, launch_map)
        self._cache = list(rows)
        self._expires = now + max(0, min(300, self.ttl))
        return rows
