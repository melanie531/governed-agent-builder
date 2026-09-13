"""Independent capture control, never a product approval or readiness writer.

Only a trusted server may reserve tickets; consume inputs must come from verified
workload identity and immutable request authority, not user payload. Store.tx must
be serializable. Call consume and EXIT its transaction before any network I/O.
No inference entrypoint is enabled by this module.
"""
from decimal import Decimal, InvalidOperation, localcontext
import math
import re


def _clock(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise ValueError('CAPTURE_TIME_INVALID')
    return value


def _sum_money(values):
    # Inputs are bounded to 27 significant digits; up to 64 entries sum exactly.
    with localcontext() as ctx:
        ctx.prec = 64
        return sum(values, Decimal(0))
from backend.foundation_runs import get, put

SERVICES = frozenset({'model_input','model_output','gateway_policy','runtime_lifetime','storage','telemetry'})


def _money(value):
    if not isinstance(value,str):raise ValueError('COST_UNKNOWN')
    if not re.fullmatch(r'(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,18})?',value):
        raise ValueError('COST_PRECISION_INVALID')
    try:amount=Decimal(value)
    except InvalidOperation:raise ValueError('COST_INVALID') from None
    if not amount.is_finite() or amount<0:raise ValueError('COST_INVALID')
    return amount


def _key(ticket):
    if not isinstance(ticket,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',ticket):
        raise ValueError('TICKET_INVALID')
    return 'opus-capture:'+ticket


def reserve_capture(store,ticket,*,role,workspace,request_digest,deadline,now,costs,cap_usd,
                    admission_check=None,budget_scopes=()):
    key=_key(ticket)
    if not role or not workspace or not re.fullmatch(r'[a-f0-9]{64}',request_digest):
        raise ValueError('CAPTURE_BINDING_REQUIRED')
    _clock(now);_clock(deadline)
    if not now<deadline<=now+60*60:raise ValueError('CAPTURE_DEADLINE_INVALID')
    if not isinstance(costs,dict) or not SERVICES<=costs.keys() or len(costs)>64:raise ValueError('COST_COVERAGE_REQUIRED')
    cap=_money(cap_usd)
    if not 0<cap<=5:raise ValueError('CAPTURE_CAP_INVALID')
    amounts=[]
    for item in costs.values():
        if not isinstance(item,dict) or not isinstance(item.get('basis'),str) or not item['basis'].strip():
            raise ValueError('COST_BASIS_REQUIRED')
        amounts.append(_money(item.get('usd')))
    total=_sum_money(amounts)
    if total<=0 or total>cap:raise ValueError('CAPTURE_COST_EXCEEDS_CAP')
    row=dict(role=role,workspace=workspace,request_digest=request_digest,deadline=deadline,
             state='RESERVED',held_usd=str(cap),costs=costs,attempts=0)
    with store.tx() as db:
        if get(db,key) is not None:raise ValueError('CAPTURE_TICKET_EXISTS')
        row['dispatch_admitted']=False
        if admission_check is not None:
            if (not callable(admission_check) or not isinstance(budget_scopes,tuple)
                    or not {'account','workspace:'+workspace} <= set(budget_scopes)
                    or len(set(budget_scopes))!=len(budget_scopes) or len(budget_scopes)>8):
                raise ValueError('CAPTURE_TOTAL_BUDGET_REQUIRED')
            admission_check(db,role,workspace,request_digest)
            for scope in budget_scopes:
                if not isinstance(scope,str) or not scope or len(scope)>200:
                    raise ValueError('CAPTURE_BUDGET_SCOPE_INVALID')
                bucket='foundation-budget:'+scope
                ledger=get(db,bucket) or {'held_usd':'0','estimated_usd':'0'}
                held=_sum_money([_money(ledger['held_usd']),cap])
                if _sum_money([held,_money(ledger['estimated_usd'])])>5:
                    raise ValueError('CAPTURE_TOTAL_BUDGET_EXCEEDED')
                put(db,bucket,{**ledger,'held_usd':str(held)})
            row.update(dispatch_admitted=True,budget_scopes=list(budget_scopes))
        elif budget_scopes:
            raise ValueError('CAPTURE_ADMISSION_REQUIRED')
        put(db,key,row)
    return row


def consume_capture(store,ticket,*,role,workspace,request_digest,now,admission_check=None):
    _clock(now)
    with store.tx() as db:
        row=get(db,_key(ticket))
        if row is None:raise ValueError('CAPTURE_TICKET_NOT_FOUND')
        if row['state']!='RESERVED':raise ValueError('CAPTURE_TICKET_CONSUMED')
        if (role,workspace,request_digest)!=(row['role'],row['workspace'],row['request_digest']):
            raise ValueError('CAPTURE_IDENTITY_DENIED')
        _clock(row['deadline'])
        if now>=row['deadline']:raise ValueError('CAPTURE_EXPIRED')
        if row.get('dispatch_admitted'):
            if not callable(admission_check):raise ValueError('CAPTURE_ADMISSION_REQUIRED')
            admission_check(db,role,workspace,request_digest)
        elif admission_check is not None:
            raise ValueError('CAPTURE_ADMISSION_REQUIRED')
        row.update(state='CLAIMED',attempts=1)
        put(db,_key(ticket),row)
    return row


def dispatch_capture(store,ticket,*,role,workspace,request,transport,admission_check,clock=None):
    """Internal sender; authenticated role/project must be injected by the server.

    No public HTTP route is registered. Caller persists restricted returned evidence.
    Production client allowlists/readiness are never modified by this diagnostic.
    """
    import time
    import copy
    from foundation_harness.config import digest, exact_endpoint
    from foundation_harness.opus_messages import build_request
    if not callable(admission_check):raise ValueError('CAPTURE_ADMISSION_REQUIRED')
    clock=clock or time.time
    request=copy.deepcopy(request)
    if set(request)!={'endpoint','system','prompt','max_tokens'}:
        raise ValueError('CAPTURE_REQUEST_INVALID')
    exact_endpoint(request['endpoint'],'/bedrockrt/v1/messages')
    body=build_request('us.anthropic.claude-opus-5',request['system'],request['prompt'],request['max_tokens'])
    row=consume_capture(store,ticket,role=role,workspace=workspace,
        request_digest=digest(request),now=clock(),admission_check=admission_check)
    try:
        remaining=row['deadline']-_clock(clock())
        if remaining<=0:raise ValueError('CAPTURE_EXPIRED')
        result=transport.post(request['endpoint'],body,
            {'Accept':'application/json','anthropic-version':'2023-06-01'},min(60,remaining))
    except Exception:
        finish_capture(store,ticket,outcome='UNKNOWN')
        raise
    finish_capture(store,ticket,outcome='CAPTURED')
    return result


def finish_capture(store,ticket,*,outcome):
    if outcome not in {'CAPTURED','UNKNOWN'}:raise ValueError('CAPTURE_OUTCOME_INVALID')
    with store.tx() as db:
        row=get(db,_key(ticket))
        if row is None or row['state']!='CLAIMED':raise ValueError('CAPTURE_NOT_CLAIMED')
        # Neither success nor uncertainty releases dollars automatically.
        row['state']=outcome
        put(db,_key(ticket),row)
