"""Independent capture control, never a product approval or readiness writer.

Only a trusted server may reserve tickets; consume inputs must come from verified
workload identity and immutable request authority, not user payload. Store.tx must
be serializable. Call consume and EXIT its transaction before any network I/O.
No inference entrypoint is enabled by this module.
"""
from decimal import Decimal, InvalidOperation
import re
from backend.foundation_runs import get, put

SERVICES = frozenset({'model_input','model_output','gateway_policy','runtime_lifetime','storage','telemetry'})


def _money(value):
    if not isinstance(value,str):raise ValueError('COST_UNKNOWN')
    try:amount=Decimal(value)
    except InvalidOperation:raise ValueError('COST_INVALID') from None
    if not amount.is_finite() or amount<0:raise ValueError('COST_INVALID')
    return amount


def _key(ticket):
    if not isinstance(ticket,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',ticket):
        raise ValueError('TICKET_INVALID')
    return 'opus-capture:'+ticket


def reserve_capture(store,ticket,*,role,workspace,request_digest,deadline,now,costs,cap_usd):
    key=_key(ticket)
    if not role or not workspace or not re.fullmatch(r'[a-f0-9]{64}',request_digest):
        raise ValueError('CAPTURE_BINDING_REQUIRED')
    if not now<deadline<=now+60*60:raise ValueError('CAPTURE_DEADLINE_INVALID')
    if not isinstance(costs,dict) or not SERVICES<=costs.keys():raise ValueError('COST_COVERAGE_REQUIRED')
    cap=_money(cap_usd)
    if not 0<cap<=5:raise ValueError('CAPTURE_CAP_INVALID')
    total=Decimal(0)
    for item in costs.values():
        if not isinstance(item,dict) or not isinstance(item.get('basis'),str) or not item['basis'].strip():
            raise ValueError('COST_BASIS_REQUIRED')
        total+=_money(item.get('usd'))
    if total<=0 or total>cap:raise ValueError('CAPTURE_COST_EXCEEDS_CAP')
    row=dict(role=role,workspace=workspace,request_digest=request_digest,deadline=deadline,
             state='RESERVED',held_usd=str(cap),costs=costs,attempts=0)
    with store.tx() as db:
        if get(db,key) is not None:raise ValueError('CAPTURE_TICKET_EXISTS')
        put(db,key,row)
    return row


def consume_capture(store,ticket,*,role,workspace,request_digest,now):
    with store.tx() as db:
        row=get(db,_key(ticket))
        if row is None:raise ValueError('CAPTURE_TICKET_NOT_FOUND')
        if row['state']!='RESERVED':raise ValueError('CAPTURE_TICKET_CONSUMED')
        if (role,workspace,request_digest)!=(row['role'],row['workspace'],row['request_digest']):
            raise ValueError('CAPTURE_IDENTITY_DENIED')
        if now>=row['deadline']:raise ValueError('CAPTURE_EXPIRED')
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
