"""Send-path gate: the ONLY real dispatch entry. STRICT ORDERING (哥哥 sharpening):
  (a) verify identity + admission window + cost-basis FIRST;
  (b) in the SAME serializable transaction, reserve the total budget AND
      create+claim the ticket (persistent consumption);
  (c) only AFTER that persistent consumption succeeds may the actual send proceed.
If any step (a) check fails there is no reservation, no ticket, no send. If the
persistent claim (b) fails (e.g. a concurrent loser), there is no send.

At-most-once / no TOCTOU: the reserve+claim is one committed store.tx(); two racers
cannot both create+claim the same ticket key, so at most one caller ever reaches the
dispatch in (c). The live Bedrock call remains UNVERIFIED-pending-哥哥-IAM/Cedar-apply
(no permissions applied here, no cloud resource created, no Ready flag written).

The caller supplies an already-authorized transport (post(endpoint, body, headers,
timeout)) whose identity/authority is verified upstream, never from a request payload.
"""
from scripts.opus_capture_ticket import reserve_and_claim_capture, finish_capture


class SendGateDenied(RuntimeError):
    """Raised when step (a) or the atomic claim (b) fails; NO dispatch may occur."""


def guarded_capture_send(store, ticket, *, role, workspace, request_digest,
                         deadline, now, costs, cap_usd,
                         transport, endpoint, body, headers, timeout=60):
    """STRICT-ORDERED send: pre-checks -> atomic reserve+claim -> then dispatch once.

    Returns a dict describing the committed claim + the raw transport outcome.
    Raises SendGateDenied (wrapping the underlying ValueError) if identity/admission/
    cost-basis fails OR the persistent claim fails (concurrent loser / already
    consumed / invalid). In every such case NO dispatch is performed.
    """
    # STEP (a)+(b): pre-checks then single atomic reserve+claim. If this raises,
    # execution never reaches the transport call below -> guaranteed zero sends.
    try:
        claim = reserve_and_claim_capture(
            store, ticket, role=role, workspace=workspace,
            request_digest=request_digest, deadline=deadline, now=now,
            costs=costs, cap_usd=cap_usd)
    except ValueError as exc:
        # Failed identity/admission/cost-basis OR lost the claim race => no send.
        raise SendGateDenied(str(exc)) from exc

    # STEP (c): persistent consumption succeeded. Dispatch exactly once.
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
        # A real provider response would flip this once IAM/Cedar are applied and a
        # live identity is observed through the reviewed transport; until then it
        # stays UNVERIFIED. No Ready flag is ever set here.
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
