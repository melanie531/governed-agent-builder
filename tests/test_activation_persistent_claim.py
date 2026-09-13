"""Existing durable ledger negative probe; moto DynamoDB, not live authorization."""
import json
import pytest
from backend import foundation_runs as runs
from backend.dynamo_store import DynamoStore
from foundation_harness.context import Denied
from tests.test_foundation_wiring import reserved
from tests.test_foundation_admission import event
from tests.test_serverless import cloud


def test_model_claim_survives_store_reopen_and_never_retries(cloud,payload,tmp_path):
    store,_,row=reserved(cloud,payload,tmp_path)
    principal=event(row)['requestContext']['authorizer']['iam']['userArn']
    body=json.loads(event(row)['body'])
    with store.tx() as db:runs.exchange(db,principal_arn=principal,body=body)
    with store.tx() as db:runs.exchange(db,principal_arn=principal,body={**body,'operation':'model','call_id':'model-1'})
    # Simulate the caller disappearing after claim, before a provider outcome.
    reopened=DynamoStore(store.table.name,resource=__import__('boto3').resource('dynamodb',region_name=store.table.meta.client.meta.region_name))
    for call_id,reason in [('model-1','CALL_ALREADY_CLAIMED'),('model-2','PERSISTENT_CALL_CAP')]:
        with pytest.raises(Denied,match=reason),reopened.tx() as db:
            runs.exchange(db,principal_arn=principal,body={**body,'operation':'model','call_id':call_id})
    with reopened.tx() as db:
        saved=runs.get(db,'foundation-run:'+row['run_ref'])
        assert saved['calls']=={'model:model-1':'CLAIMED'}
        assert saved['settled'] is False
        assert runs.get(db,'foundation-budget:account')['held_usd']==row['reservation_usd']
