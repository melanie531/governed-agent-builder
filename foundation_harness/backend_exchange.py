"""SigV4 workload exchange; endpoint/manifest are immutable package configuration.

SDK headers/session IDs are not authentication. API Gateway verifies the Runtime's
unique execution role. The backend binds that role + manifest + single-use run to
an exact Runtime version whose invoke permission is backend-only. This code must
not be enabled until those IAM/resource policies have independent cloud proof.
"""
import re
from decimal import Decimal

from .config import Limits
from .context import Binding, Denied, ResolvedEntry
from .transport import IAMTransport


class BackendExchange:
    def __init__(self, session, endpoint, manifest_digest):
        if not re.fullmatch(r'https://[a-z0-9]+\.execute-api\.us-west-2\.amazonaws\.com/internal/foundation/exchange', endpoint):
            raise Denied('APPROVED_EXCHANGE_ENDPOINT_REQUIRED')
        self.transport = IAMTransport(session)
        self.endpoint, self.manifest_digest = endpoint, manifest_digest
        self.entry = None
        self.handle = None
        self.amount_usd = None

    def request(self, run_ref, operation, **extra):
        from .config import canonical
        value, _ = self.transport.send(self.endpoint, canonical({
            'run_ref': run_ref, 'manifest_digest': self.manifest_digest,
            'operation': operation, **extra}), {'Accept': 'application/json'}, 5, 'execute-api')
        if not isinstance(value, dict) or value.get('reservation_handle') != run_ref:
            raise Denied('BACKEND_RESPONSE_BINDING_DENIED')
        if self.entry is not None and Binding(**value['binding']) != self.entry:
            raise Denied('BACKEND_RESPONSE_BINDING_DENIED')
        return value

    def resolve(self, run_ref, context):
        # Deliberately ignore context headers and payload identity.
        value = self.request(run_ref, 'redeem')
        binding = Binding(**value['binding'])
        if binding.run_ref != run_ref or binding.manifest_digest != self.manifest_digest:
            raise Denied('BACKEND_RESPONSE_BINDING_DENIED')
        self.entry, self.handle = binding, value['reservation_handle']
        self.amount_usd = Decimal(value['reservation_usd'])
        return ResolvedEntry(binding, value['stored_input'], Limits(**value['limits']), self)

    def redeem(self, run_ref, authenticated_entry):
        if self.entry is None or authenticated_entry != self.entry or self.entry.run_ref != run_ref:
            raise Denied('RESOLVED_ENTRY_REQUIRED')
        return self.entry

    def authorize(self, binding, operation, resource):
        if binding != self.entry:
            raise Denied('RESOLVED_ENTRY_REQUIRED')
        self.request(binding.run_ref, 'authorize', capability=operation, resource=resource)

    def claim(self, operation, call_id):
        self.request(self.handle, operation, call_id=call_id)

    def settle(self, usage):
        self.request(self.handle, 'settle', usage=usage)

    def finish(self, binding):
        # Final persistent settlement occurs after telemetry export. This recheck
        # still precedes publication of execution output.
        self.authorize(binding, 'finish', '')
