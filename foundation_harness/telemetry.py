"""Manual OTel only: allowlisted metadata, no prompt/output/SDK auto-capture."""
from contextlib import contextmanager
import re

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
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

    def export(self, spans):
        try:
            self.budget.require_reservation(self.transport)
            if self.exported + self.failed >= 5:
                raise ValueError('TRACE_EXPORT_COUNT_CAP')
            body = encode_spans(spans).SerializeToString()
            if len(body) > 65536 or self.bytes_sent + len(body) > 262144:
                raise ValueError('TRACE_BYTE_CAP')
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


class Telemetry:
    def __init__(self, exporter=None):
        self.exporter = exporter
        self.provider = TracerProvider(resource=Resource({
            'service.name': 'gab-foundation-m0',
            'aws.log.group.names': '/governed-agent-builder/foundation-m0'}))
        if exporter:
            self.provider.add_span_processor(SimpleSpanProcessor(exporter))
        self.tracer = self.provider.get_tracer('owned-foundation', '1')
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
            yield span

    def headers(self):
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return {}
        return {'traceparent': f'00-{context.trace_id:032x}-{context.span_id:016x}-01'}

    def flush(self):
        self.provider.force_flush(timeout_millis=5000)
        return bool(self.exporter and self.exporter.exported and not self.exporter.failed)
