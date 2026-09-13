"""Independent capture control, never a product approval or readiness writer.

Only a trusted server may reserve tickets; consume inputs must come from verified
workload identity and immutable request authority, not user payload. Store.tx must
be serializable. Call consume and EXIT its transaction before any network I/O.
No inference entrypoint is enabled by this module.

Fail-closed hardening (哥哥 review of 789293c, additive fixes):
  * Defect 1: non-finite time values (inf/-inf/NaN) must never bypass the
    expiry check. NaN comparisons are always False, so `now>=deadline` with a
    NaN silently reads as "not expired". Every deadline/now value is validated
    as a finite real number BEFORE any comparison; non-finite is invalid and,
    at consume time, is treated as EXPIRED (never "not expired").
  * Defect 2: budget summation is done in EXACT integer micro-USD (no float, no
    lossy rounding). Any amount with sub-micro-USD precision is rejected. The
    cap check is a strict exact integer comparison: total_micros > cap_micros is
    rejected; total == cap is admitted (boundary defined and tested).
"""
from decimal import Decimal, InvalidOperation
import math
import re
from backend.foundation_runs import get, put

SERVICES = frozenset({'model_input','model_output','gateway_policy','runtime_lifetime','storage','telemetry'})

# All budget accounting is in integer micro-USD (1 USD == 1_000_000 micros).
# Integer minor units make the cap check exact: no float, no lossy Decimal round.
MICROS_PER_USD = 1_000_000
CAP_CEILING_MICROS = 5 * MICROS_PER_USD  # hard 5 USD ceiling, exact.


def _finite(value):
    """Return value only if it is a finite real number; else fail closed.

    Booleans and non-real types are rejected. inf/-inf/NaN are rejected. This
    guard MUST run before any ordering comparison so a NaN (whose comparisons are
    always False) can never read as "not expired" / "within deadline".
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('TIME_VALUE_NOT_FINITE')
    if not math.isfinite(value):
        raise ValueError('TIME_VALUE_NOT_FINITE')
    return value


def _micros(value):
    """Parse a USD string to EXACT integer micro-USD or fail closed.

    Rejects non-str, non-finite, negative, and any value carrying precision finer
    than one micro-USD (so no sub-micro dust can accumulate past the cap). This is
    exact integer accounting; no float and no lossy rounding is ever performed.
    """
    if not isinstance(value, str):
        raise ValueError('COST_UNKNOWN')
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError('COST_INVALID') from None
    if not amount.is_finite() or amount < 0:
        raise ValueError('COST_INVALID')
    scaled = amount * MICROS_PER_USD
    micros = int(scaled)
    if scaled != micros:  # sub-micro-USD precision is not representable exactly
        raise ValueError('COST_SUB_MICRO_PRECISION')
    return micros


def _key(ticket):
    if not isinstance(ticket,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',ticket):
        raise ValueError('TICKET_INVALID')
    return 'opus-capture:'+ticket


def reserve_capture(store,ticket,*,role,workspace,request_digest,deadline,now,costs,cap_usd):
    key=_key(ticket)
    if not role or not workspace or not re.fullmatch(r'[a-f0-9]{64}',request_digest):
        raise ValueError('CAPTURE_BINDING_REQUIRED')
    # Validate finiteness BEFORE any ordering comparison (Defect 1 fail-closed).
    now=_finite(now); deadline=_finite(deadline)
    if not now<deadline<=now+60*60:raise ValueError('CAPTURE_DEADLINE_INVALID')
    if not isinstance(costs,dict) or not SERVICES<=costs.keys():raise ValueError('COST_COVERAGE_REQUIRED')
    cap_micros=_micros(cap_usd)
    if not 0<cap_micros<=CAP_CEILING_MICROS:raise ValueError('CAPTURE_CAP_INVALID')
    cap_amount=Decimal(cap_usd)  # exact; held_usd keeps the reviewer's USD string
    # Exact integer micro-USD summation (Defect 2). No float, no lossy rounding.
    total_micros=0
    for item in costs.values():
        if not isinstance(item,dict) or not isinstance(item.get('basis'),str) or not item['basis'].strip():
            raise ValueError('COST_BASIS_REQUIRED')
        total_micros+=_micros(item.get('usd'))
    # Strict exact comparison: any total OVER cap is rejected; == cap is admitted.
    if total_micros<=0 or total_micros>cap_micros:raise ValueError('CAPTURE_COST_EXCEEDS_CAP')
    row=dict(role=role,workspace=workspace,request_digest=request_digest,deadline=deadline,
             state='RESERVED',held_usd=str(cap_amount),held_micros=cap_micros,
             costs=costs,attempts=0)
    with store.tx() as db:
        if get(db,key) is not None:raise ValueError('CAPTURE_TICKET_EXISTS')
        put(db,key,row)
    return row


def consume_capture(store,ticket,*,role,workspace,request_digest,now):
    # Validate finiteness BEFORE any expiry comparison (Defect 1 fail-closed):
    # a non-finite `now` must never read as "not expired".
    now=_finite(now)
    with store.tx() as db:
        row=get(db,_key(ticket))
        if row is None:raise ValueError('CAPTURE_TICKET_NOT_FOUND')
        if row['state']!='RESERVED':raise ValueError('CAPTURE_TICKET_CONSUMED')
        if (role,workspace,request_digest)!=(row['role'],row['workspace'],row['request_digest']):
            raise ValueError('CAPTURE_IDENTITY_DENIED')
        # Stored deadline must also be finite; a corrupted non-finite deadline is
        # treated as expired (fail-closed), never "not expired".
        deadline=_finite(row['deadline'])
        if now>=deadline:raise ValueError('CAPTURE_EXPIRED')
        row.update(state='CLAIMED',attempts=1)
        put(db,_key(ticket),row)
    return row


def finish_capture(store,ticket,*,outcome):
    if outcome not in {'CAPTURED','UNKNOWN'}:raise ValueError('CAPTURE_OUTCOME_INVALID')
    with store.tx() as db:
        row=get(db,_key(ticket))
        if row is None or row['state']!='CLAIMED':raise ValueError('CAPTURE_NOT_CLAIMED')
        # Neither success nor uncertainty releases dollars automatically.
        row['state']=outcome
        put(db,_key(ticket),row)
