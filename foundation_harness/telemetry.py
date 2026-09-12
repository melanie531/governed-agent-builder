"""Manual OTel only: allowlisted metadata, no prompt/output/SDK auto-capture."""
from contextlib import contextmanager
import re
import json
import copy

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult, SpanProcessor
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

from .transport import IAMTransport

ALLOWED = {'manifest_digest', 'foundation_digest', 'route', 'provider', 'provider_model',
           'tool', 'request_id', 'input_tokens', 'output_tokens', 'status'}
OPERATIONS = {'run', 'admission', 'model', 'tool'}


class CloudWatchExporter(SpanExporter):
    """Real OTLP/protobuf, signed once, synchronous bounded send, no retries."""
    def __init__(self, session, *, budget):
        self.transport = IAMTransport(session)
        self.budget = budget
        self.exported = self.failed = 0
        self.bytes_sent = 0
        self.attempts = 0

    def export(self, spans):
        try:
            self.budget.require_reservation(self.transport)
            if self.attempts >= 5:
                raise ValueError('TRACE_EXPORT_COUNT_CAP')
            body = encode_spans(spans).SerializeToString()
            if len(body) > 65536 or self.bytes_sent + len(body) > 262144:
                raise ValueError('TRACE_BYTE_CAP')
            if self.budget.reservation:
                self.budget.reservation.claim('export', f'export-{self.attempts + 1}')
            self.attempts += 1
            self.bytes_sent += len(body)
            result, _ = self.transport.send('https://xray.us-west-2.amazonaws.com/v1/traces', body,
                                           {'Content-Type': 'application/x-protobuf', 'Accept': 'application/json',
                                            'x-aws-log-group': '/governed-agent-builder/foundation-m0',
                                            'x-aws-log-stream': 'spans'},
                                           min(5, self.budget.remaining()), 'xray')
            if result and result.get('partialSuccess'):
                raise ValueError('OTLP_PARTIAL_REJECTION')
            self.exported += len(spans)
            return SpanExportResult.SUCCESS
        except Exception:
            self.failed += len(spans)
            return SpanExportResult.FAILURE


class ExecutionSpans(SpanProcessor):
    """Private invocation record of actual ended spans; never a log exporter."""
    def __init__(self):
        self.spans = []

    def on_end(self, span):
        if len(self.spans) >= 100:
            raise ValueError('EXECUTION_SPAN_CAP')
        self.spans.append({
            'traceId': format(span.context.trace_id, '032x'),
            'spanId': format(span.context.span_id, '016x'),
            **({'parentSpanId': format(span.parent.span_id, '016x')} if span.parent else {}),
            'name': span.name, 'kind': span.kind.name,
            'scope': {'name': span.instrumentation_scope.name, 'version': span.instrumentation_scope.version},
            'startTimeUnixNano': span.start_time, 'endTimeUnixNano': span.end_time,
            'attributes': dict(span.attributes)})

    def force_flush(self, timeout_millis=30000):
        # on_end captures synchronously; there is no pending work to flush.
        return True

    def shutdown(self):
        # Keep completed records available for the private invocation response.
        pass


class Telemetry:
    def __init__(self, exporter=None):
        self.exporter = exporter
        self.provider = TracerProvider(resource=Resource({
            'service.name': 'gab-foundation-m0',
            'aws.log.group.names': '/governed-agent-builder/foundation-m0'}))
        if exporter:
            self.provider.add_span_processor(SimpleSpanProcessor(exporter))
        self.record = ExecutionSpans()
        self.provider.add_span_processor(self.record)
        self.private = {}
        self.session_id = None
        self.tracer = self.provider.get_tracer('opentelemetry.instrumentation.owned_foundation', '1')
        self.trace_id = None

    @classmethod
    def local(cls):
        return cls()

    def attributes(self, span, attributes):
        for key, value in attributes.items():
            if key not in ALLOWED:
                raise ValueError('TELEMETRY_FIELD_DENIED')
            if isinstance(value, str):
                if len(value) > 200 or not re.fullmatch(r'[a-zA-Z0-9_.:/-]+', value) or re.search(r'\d{12}', value):
                    continue
            elif type(value) is not int:
                continue
            span.set_attribute('foundation.' + key, value)

    @contextmanager
    def span(self, operation, attributes=None):
        if operation not in OPERATIONS:
            raise ValueError('TELEMETRY_OPERATION_DENIED')
        # Never let OTel record exceptions/stack traces containing content.
        with self.tracer.start_as_current_span(operation, record_exception=False,
                                               set_status_on_exception=False) as span:
            self.trace_id = format(span.get_span_context().trace_id, '032x')
            self.attributes(span, attributes or {})
            span.set_attribute('gen_ai.operation.name', {'run': 'invoke_agent', 'model': 'chat', 'tool': 'execute_tool', 'admission': 'admission'}[operation])
            if self.session_id:
                span.set_attribute('session.id', self.session_id)
            yield span

    def content(self, span, attributes):
        # Only the private Invoke response carries these documented attributes.
        key = format(span.get_span_context().span_id, '016x')
        value = copy.deepcopy(attributes)
        # Reject, rather than silently redact and misrepresent, credential-bearing
        # content. Headers/transport credentials never enter this record.
        serialized = json.dumps(value)
        if re.search(r'(?i)(authorization|api[_-]?key|access[_-]?token|secret[_-]?access[_-]?key|password|client[_-]?secret)\\?"\s*:', serialized) or re.search(r'(?i)bearer\s+[a-z0-9._~+/-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[A-Z0-9]{16}', serialized):
            raise ValueError('PRIVATE_CREDENTIAL_CONTENT_DENIED')
        if len(serialized.encode()) > 32768:
            raise ValueError('PRIVATE_CONTENT_CAP')
        self.private.setdefault(key, {}).update(value)

    def execution_record(self):
        return {'spans': copy.deepcopy(self.record.spans), 'content': copy.deepcopy(self.private)}

    def headers(self):
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return {}
        return {'traceparent': f'00-{context.trace_id:032x}-{context.span_id:016x}-01'}

    def flush(self):
        try:
            flushed = self.provider.force_flush(timeout_millis=5000)
            return bool(flushed and self.exporter and getattr(self.exporter, 'exported', 0)
                        and not getattr(self.exporter, 'failed', 0))
        except Exception:
            return False
