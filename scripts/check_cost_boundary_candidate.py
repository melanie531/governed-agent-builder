"""Offline preflight only: no AWS writes, no approved-budget inference."""
import json
from pathlib import Path


def missing_parameters(c):
    missing=[]
    network=c['runtime']['networkConfiguration']['networkModeConfig']
    for field in ('subnets','securityGroups'):
        if not network[field]:missing.append('runtime.network.'+field)
    if not c['runtime']['roleArn']:missing.append('runtime.roleArn')
    if c['runtime']['session_count_enforcement']!='VERIFIED':missing.append('runtime.session_count_enforcement')
    if c['input']['provider_token_upper_bound'] is None:missing.append('input.provider_token_upper_bound')
    for field in ('bucket','evidence_prefix'):
        if not c['storage'][field]:missing.append('storage.'+field)
    for field in ('logGroupName','ingestion_byte_cap'):
        if c['logs'][field] is None:missing.append('logs.'+field)
    for field in ('approved_reservation_usd','full_service_upper_bound_usd'):
        if c['budget'][field] is None:missing.append('budget.'+field)
    return missing


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    c=json.loads((root/'docs/model-invocation/cost-boundary-candidate.json').read_text())
    missing=missing_parameters(c)
    print(json.dumps({'status':'BLOCKED' if missing else 'REQUIRES_SEPARATE_REVIEW',
                     'missing':missing,'aws_calls':0,'deployment_authorized':False},indent=2))
    raise SystemExit(2 if missing else 0)
