"""Production Opus response-identity validation is fail-closed on BOTH empty and
unknown. No synthetic id is ever admitted on the production path; the synthetic
manifest is test-only. Additive to the frozen opus tests."""
import pytest
from fastapi import HTTPException

from foundation_harness.config import load_config, digest
from foundation_harness.opus_messages import read_response
from backend.foundation_approval import registered_model
from tests.test_opus_modelclient import opus_config, message


def _good(model):
    return {'type': 'message', 'model': model, 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 3, 'output_tokens': 2},
            'content': [{'type': 'text', 'text': 'hi'}]}


def test_production_model_rejects_empty_response_allowlist():
    # Empty allowlist must NOT load on the production path (fail-closed on empty).
    raw = opus_config(); raw['model']['responseModelAllowlist'] = []
    with pytest.raises(ValueError, match='EXPLICIT_RESPONSE_ID_ALLOWLIST_REQUIRED'):
        load_config(raw, digest(raw))


def test_codec_fail_closed_on_empty_and_unknown():
    # Empty allowlist: reject (fail-closed on empty).
    with pytest.raises(ValueError, match='EXPLICIT_RESPONSE_ID_ALLOWLIST_REQUIRED'):
        read_response(_good('anything'), (), 256)
    # Unknown response id (not in a pinned allowlist): reject.
    with pytest.raises(ValueError, match='OPUS_RESPONSE_IDENTITY_MISMATCH'):
        read_response(_good('some-unpinned-id'), ('pinned-evidence-id',), 256)
    # Known/pinned id: accepted.
    assert read_response(_good('pinned-evidence-id'), ('pinned-evidence-id',), 256) == 'hi'


def test_admission_defense_in_depth_rejects_empty_allowlist_unverified():
    # Even if an empty-allowlist model object were constructed, admission fails closed.
    raw = opus_config()  # test-only synthetic value present so load succeeds
    cfg = load_config(raw, digest(raw))
    m = cfg.model.model_copy(update={'responseModelAllowlist': ()})
    source = {'config': raw, 'platform': {'model': m.model_dump(mode='json')}}

    class Shim:
        model = m
        limits = cfg.limits
        tools = cfg.tools
        allowedTools = cfg.allowedTools
        skills = cfg.skills
    import backend.foundation_approval as fa
    orig = fa.load_config
    fa.load_config = lambda *a, **k: Shim()
    try:
        with pytest.raises(HTTPException, match='UNVERIFIED_RESPONSE_IDENTITY'):
            registered_model(source, m.id)
    finally:
        fa.load_config = orig
