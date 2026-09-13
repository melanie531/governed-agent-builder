"""Explicit assumption scenario, NOT enforced upper bound or approved budget."""
import json
from decimal import Decimal, localcontext


def estimate():
    # Rates captured in price evidence; quantities below are proposals, not usage.
    specs=[
        ('model_input','2000','5.50','1000000','tokens','proposed; provider token bound NOT VERIFIED'),
        ('model_output','256','27.50','1000000','tokens','per-request cap; permission unapplied'),
        ('runtime_cpu','75','0.0895','3600','vCPU-seconds','assumes1vCPU for60s+15s termination; allocation/session count NOT VERIFIED'),
        ('runtime_memory','150','0.00945','3600','GB-seconds','assumes2GB for75s; memory peak NOT VERIFIED'),
        ('gateway','10','0.005','1000','requests','diagnostic+negative controls assumption'),
        ('policy','10','0.000025','1','authorization requests','same assumption; page-only price basis'),
        ('logs_ingest','0.001','0.50','1','GB','1MB example; platform logs not bounded'),
        ('logs_store','0.001','0.03','1','GB-month','one-month pricing scenario; NOT actual retention'),
        ('dynamo_writes','100','0.625','1000000','WRU','transaction-amplified units assumption'),
        ('dynamo_reads','100','0.125','1000000','RRU','unit assumption not number of HTTP requests'),
        ('http_api','20','1','1000000','requests','admission/control count assumption'),
    ]
    with localcontext() as ctx:
        ctx.prec=40
        rows=[{'component':n,'quantity':q,'quantity_unit':unit,'rate_usd':rate,'rate_per_units':div,'cost_usd':str(Decimal(q)*Decimal(rate)/Decimal(div)),'assumption':note} for n,q,rate,div,unit,note in specs]
        subtotal=sum(Decimal(r['cost_usd']) for r in rows)
    return {'kind':'CONDITIONAL_PARTIAL_ESTIMATE_NOT_UPPER_BOUND','currency':'USD','rows':rows,'priced_scenario_subtotal_usd':str(subtotal),
        'excluded_unknown_not_zero':['network NAT/endpoints/egress','applicable Lambda ARM compute and fanout','S3 versioned artifact/evidence retention+requests','KMS if used','trace/span ingest/index and custom metrics','cold start/session respawn/longer memory lifetime'],
        'full_service_total_usd':None,'approved_reservation_usd':None,'rates_basis':'PRICE-REVIEW-CORRECTIONS.md and captured regional supporting-service price evidence','quantities_enforced':False}

if __name__=='__main__':print(json.dumps(estimate(),indent=2))
