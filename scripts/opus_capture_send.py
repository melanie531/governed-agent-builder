"""Send-path gate: the ONLY real dispatch entry consumes the capture ticket and
reserves budget atomically BEFORE any network I/O, so a live call sends AT MOST
ONCE. Isolated module; the live Bedrock call remains UNVERIFIED until 哥哥 applies
IAM/Cedar (no permissions are applied here and no cloud resource is created).

Atomicity contract (no TOCTOU window):
  * consume_capture() performs claim-ticket + hold-budget as a SINGLE serializable
    store.tx() that is committed before this function performs any transport I/O.
  * The ticket's held_usd (integer micro-USD) IS the reserved budget; the same
    committed CLAIM state-transition is both the ticket claim and the reservation.
  * Two racing callers cannot both pass: exactly one wins the CLAIM; the loser
    gets CAPTURE_TICKET_CONSUMED and NEVER reaches dispatch. Proven by the
    concurrency regression test (only one caller sends).

This module constructs no clients and creates no cloud resources. The caller
supplies an already-authorized transport (post(endpoint, body, headers, timeout))
whose identity/authority is verified upstream, never from a request payload.
"""
from scripts.opus_capture_ticket import consume_capture, finish_capture


class SendGateDenied(RuntimeError):
    """Raised when the atomic ticket claim fails; no dispatch may occur."""


def guarded_capture_send(store, ticket, *, role, workspace, request_digest, now,
                         transport, endpoint, body, headers, timeout=60):
    """Atomically claim the ticket + reserve budget, THEN dispatch at most once.

    Returns a dict with the claimed reservation and the raw transport outcome.
    Raises SendGateDenied (wrapping the claim ValueError) if the ticket cannot be
    claimed (already consumed, expired, identity mismatch, non-finite time) — in
    which case NO dispatch is performed and no budget is consumed by this caller.
    """
    # STEP 1 (atomic, committed before any I/O): claim ticket + hold budget.
    # This is the single point that guarantees at-most-once and closes TOCTOU.
    try:
        claim = consume_capture(store, ticket, role=role, workspace=workspace,
                                request_digest=request_digest, now=now)
    except ValueError as exc:
        # Loser of a race / expired / invalid time => fail closed, never dispatch.
        raise SendGateDenied(str(exc)) from exc

    # STEP 2: only the sole CLAIM winner reaches here. Dispatch exactly once.
    # The live call cannot succeed until 哥哥 applies IAM/Cedar; mark accordingly.
    live_call_status = 'UNVERIFIED-pending-哥哥-apply'
    response = None
    error = None
    try:
        value, _metadata = transport.post(
            endpoint, body,
            {'Accept': 'application/json', 'anthropic-version': '2023-06-01',
             **(headers or {})},
            timeout)
        response = value
        # A real provider response would flip this once IAM/Cedar are applied and
        # a live identity is observed through the reviewed transport; until then it
        # stays UNVERIFIED. We never set any Ready flag here.
        live_call_status = 'OBSERVED-live-response' if isinstance(value, dict) else live_call_status
    except Exception as exc:  # transport/auth failure => still one claim spent
        error = repr(exc)
    finally:
        # Unknown outcome retains the held reservation (no refund, no retry).
        finish_capture(store, ticket,
                       outcome='CAPTURED' if error is None and response is not None else 'UNKNOWN')

    return {
        'ticket': ticket,
        'held_usd': claim['held_usd'],
        'held_micros': claim['held_micros'],
        'dispatched': True,  # this caller performed the single dispatch
        'live_call_status': live_call_status,
        'response': response,
        'error': error,
        'sets_ready_flag': False,
    }
