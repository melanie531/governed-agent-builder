"""Real OTel lifecycle contracts with synthetic spans and offline transports."""
from decimal import Decimal

import pytest
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from foundation_harness.budget import Budget
from foundation_harness.config import Limits
from foundation_harness.telemetry import CloudWatchExporter, ExecutionSpans, Telemetry
from tests.test_foundation_executor import config


@pytest.fixture
def telemetry(monkeypatch):
    limits = Limits(**{**config()['limits'], 'maxModelCalls': 1})
    exporter = CloudWatchExporter(None, budget=Budget(limits, reservation_usd=Decimal('0.01')))
    monkeypatch.setattr(exporter.transport, 'send', lambda *args: (None, {}))
    value = Telemetry(exporter)
    yield value
    value.provider.shutdown()


def test_real_provider_flush_requires_successful_export(telemetry):
    assert isinstance(telemetry.provider, TracerProvider)
    assert isinstance(telemetry.record, ExecutionSpans)
    assert telemetry.provider.force_flush() is True
    assert telemetry.flush() is False  # No export yet.
    with telemetry.span('run', {'status': 'SUCCEEDED'}):
        pass
    assert telemetry.provider.force_flush() is True
    assert telemetry.flush() is True
    assert telemetry.exporter.exported == 1
    assert telemetry.exporter.failed == 0
    assert len(telemetry.execution_record()['spans']) == 1


@pytest.mark.parametrize('failure', ['partial', 'timeout'])
def test_export_failure_remains_false_despite_simple_processor_flush(telemetry, monkeypatch, failure):
    with telemetry.span('run'):
        pass
    assert telemetry.flush() is True
    attempts = []
    def send(url, data, headers, timeout, service):
        attempts.append(timeout)
        if failure == 'timeout':
            raise TimeoutError('synthetic network timeout')
        return {'partialSuccess': {'rejectedSpans': 1}}, {}
    monkeypatch.setattr(telemetry.exporter.transport, 'send', send)
    with telemetry.span('run'):
        pass
    assert len(attempts) == 1 and 0 < attempts[0] <= 5
    assert SimpleSpanProcessor(telemetry.exporter).force_flush() is True
    assert telemetry.provider.force_flush() is True
    assert telemetry.flush() is False
    assert telemetry.exporter.exported == telemetry.exporter.failed == 1


@pytest.mark.parametrize('limit', ['count', 'bytes', 'deadline', 'reservation'])
def test_export_limits_still_fail_closed(telemetry, monkeypatch, limit):
    with telemetry.span('run'):
        pass
    assert telemetry.flush() is True
    exporter = telemetry.exporter
    if limit == 'count':
        for _ in range(4):
            with telemetry.span('run'):
                pass
        assert exporter.attempts == 5
    elif limit == 'bytes':
        exporter.bytes_sent = 262144
    elif limit == 'deadline':
        exporter.budget.deadline = 0
    else:
        exporter.budget.reservation_usd = None
    sent = []
    monkeypatch.setattr(exporter.transport, 'send', lambda *args: sent.append(args))
    with telemetry.span('run'):
        pass
    assert not sent
    assert exporter.failed == 1
    assert telemetry.provider.force_flush() is True
    assert telemetry.flush() is False


def test_failed_processor_keeps_provider_and_telemetry_false(telemetry):
    calls = []
    class FailedProcessor(SpanProcessor):
        def force_flush(self, timeout_millis=30000):
            calls.append(timeout_millis)
            return False
    telemetry.provider.add_span_processor(FailedProcessor())
    with telemetry.span('run'):
        pass
    assert telemetry.exporter.exported == 1
    assert telemetry.provider.force_flush() is False
    assert telemetry.flush() is False
    assert len(calls) == 2 and all(0 <= value <= 30000 for value in calls)


@pytest.mark.parametrize('timeout', [0, -1])
def test_provider_rejects_expired_budget_even_with_synchronous_record(telemetry, timeout):
    with telemetry.span('run'):
        pass
    assert telemetry.record.force_flush(timeout_millis=timeout) is True
    assert telemetry.provider.force_flush(timeout_millis=timeout) is False
    assert telemetry.flush() is True


def test_provider_skips_later_processor_when_shared_deadline_expires(telemetry, monkeypatch):
    calls = []
    class LaterProcessor(SpanProcessor):
        def force_flush(self, timeout_millis=30000):
            calls.append(timeout_millis)
            return True
    telemetry.provider.add_span_processor(LaterProcessor())
    # Deadline, SimpleSpanProcessor, ExecutionSpans, then the later processor.
    ticks = iter([0, 1000000, 2000000, 5000000])
    monkeypatch.setattr('opentelemetry.sdk.trace.time_ns', lambda: next(ticks))
    assert telemetry.provider.force_flush(timeout_millis=5) is False
    assert calls == []


def test_shutdown_preserves_completed_private_record_and_export_counts(telemetry):
    with telemetry.span('run') as span:
        telemetry.content(span, {'gen_ai.input.messages': [{'content': 'SYNTHETIC_PRIVATE_INPUT'}]})
    before = telemetry.execution_record()
    assert telemetry.flush() is True
    telemetry.provider.shutdown()
    telemetry.record.shutdown()
    assert telemetry.execution_record() == before
    assert telemetry.record.force_flush() is True
    assert telemetry.exporter.exported == 1 and telemetry.exporter.failed == 0


def test_flush_and_shutdown_never_export_private_content_or_exception(telemetry, monkeypatch):
    bodies = []
    monkeypatch.setattr(telemetry.exporter.transport, 'send',
                        lambda url, data, *args: (bodies.append(data), {}))
    with pytest.raises(RuntimeError, match='SYNTHETIC_PRIVATE_EXCEPTION'):
        with telemetry.span('run', {'status': 'FAILED'}) as span:
            telemetry.content(span, {'gen_ai.input.messages': [{'content': 'SYNTHETIC_PRIVATE_INPUT'}]})
            raise RuntimeError('SYNTHETIC_PRIVATE_EXCEPTION')
    with pytest.raises(ValueError, match='TELEMETRY_FIELD_DENIED'):
        with telemetry.span('model', {'prompt': 'SYNTHETIC_PRIVATE_PROMPT'}):
            pass
    assert telemetry.flush() is True
    telemetry.provider.shutdown()
    assert len(bodies) == 2
    for body in bodies:
        assert b'SYNTHETIC_PRIVATE' not in body
        message = ExportTraceServiceRequest()
        message.ParseFromString(body)
        for resource in message.resource_spans:
            for scope in resource.scope_spans:
                assert all(not span.events for span in scope.spans)
    record = telemetry.execution_record()
    assert 'SYNTHETIC_PRIVATE_INPUT' in str(record['content'])
    assert 'SYNTHETIC_PRIVATE' not in str(record['spans'])
