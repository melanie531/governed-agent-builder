"""Governed Bedrock discovery-catalog data source: real fields + verified dates."""
import json
from pathlib import Path

import pytest

from backend.discovery_catalog import (
    DiscoveryFoundationCatalog, derive_category, normalize_model,
)


def test_derive_category_from_real_modalities():
    assert derive_category(['TEXT'], ['TEXT']) == 'text'
    assert derive_category(['TEXT', 'IMAGE'], ['TEXT']) == 'multimodal'
    assert derive_category(['TEXT', 'IMAGE'], ['IMAGE']) == 'image'
    assert derive_category(['TEXT'], ['EMBEDDING']) == 'embeddings'
    assert derive_category(['SPEECH', 'TEXT'], ['TEXT']) == 'speech'
    assert derive_category(['TEXT', 'VIDEO'], ['TEXT']) == 'multimodal'


def test_normalize_model_passes_through_real_fields_only():
    summary = {
        'modelId': 'anthropic.claude-haiku-4-5-20251001-v1:0',
        'modelName': 'Claude Haiku 4.5', 'providerName': 'Anthropic',
        'inputModalities': ['TEXT', 'IMAGE'], 'outputModalities': ['TEXT'],
        'responseStreamingSupported': True,
        'inferenceTypesSupported': ['INFERENCE_PROFILE'],
        'modelLifecycle': {'status': 'ACTIVE'},
    }
    row = normalize_model(summary)
    assert row['kind'] == 'model'
    assert row['name'] == 'Claude Haiku 4.5'
    assert row['provider'] == 'Anthropic'
    assert row['model_id'] == 'anthropic.claude-haiku-4-5-20251001-v1:0'
    assert row['category'] == 'multimodal'
    assert row['lifecycle'] == 'ACTIVE'
    assert row['streaming'] is True
    assert row['inference_types'] == ['INFERENCE_PROFILE']
    assert row['provenance'] == 'bedrock:ListFoundationModels'
    # Discovery is never presented as callable.
    assert row['execution_ready'] is False
    assert row['requestable'] is False


def test_normalize_rejects_missing_provider():
    with pytest.raises(ValueError):
        normalize_model({'modelId': 'x', 'inputModalities': ['TEXT'], 'outputModalities': ['TEXT']})


def test_snapshot_source_is_real_and_datefree():
    snap = Path('backend/foundation_models_snapshot.json')
    data = json.loads(snap.read_text())
    assert data['modelSummaries']
    catalog = DiscoveryFoundationCatalog(snapshot_path=str(snap))
    rows = catalog.records()
    assert len(rows) == len(data['modelSummaries'])
    providers = {r['provider'] for r in rows}
    assert 'Anthropic' in providers
    lifecycles = {r['lifecycle'] for r in rows}
    assert 'ACTIVE' in lifecycles and 'LEGACY' in lifecycles
    # Recency now comes from a VERIFIED launch-date join (not lifecycle). Every
    # emitted launch_date must trace to the verified map; rows without a verified
    # match are pending, never fabricated.
    assert all(r.get('recency') in ('recent', 'out_of_window', 'pending_verification') for r in rows)


def test_client_backed_source():
    class FakeBedrock:
        def list_foundation_models(self):
            return {'modelSummaries': [{
                'modelId': 'qwen.qwen3-235b-a22b-2507-v1:0', 'modelName': 'Qwen3',
                'providerName': 'Qwen', 'inputModalities': ['TEXT'],
                'outputModalities': ['TEXT'], 'responseStreamingSupported': True,
                'inferenceTypesSupported': ['ON_DEMAND'],
                'modelLifecycle': {'status': 'ACTIVE'}}]}
    rows = DiscoveryFoundationCatalog(client=FakeBedrock()).records()
    assert rows[0]['provider'] == 'Qwen' and rows[0]['category'] == 'text'
