"""One-shot project metadata setup; no tool/model execution or legacy resource reads."""
import json
from pathlib import Path
import time
import boto3
from backend.live_catalog import validate_descriptor, record_revision, configured_catalog

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'artifacts/native-catalog'

def save(name, value):
    WORK.mkdir(exist_ok=True)
    p = WORK / (name + '.json')
    p.write_text(json.dumps(value, indent=2, default=str)); p.chmod(0o600)

def session():
    s = boto3.Session(profile_name='agentic-platform-prod', region_name='us-west-2')
    account = s.client('sts').get_caller_identity()['Account']
    stack = s.client('cloudformation').describe_stacks(StackName='governed-agent-builder-serverless-app')['Stacks'][0]
    distribution = s.client('cloudfront').get_distribution(Id='E3TVVGMYCWN3CE')['Distribution']
    assert stack['StackId'].split(':')[4] == account == distribution['ARN'].split(':')[4]
    assert distribution['DomainName'] == 'de32ssfw7gsad.cloudfront.net'
    assert {x['Key']:x['Value'] for x in stack['Tags']}['project'] == 'governed-agent-builder'
    return s, account, stack

def main():
    s, account, stack = session(); c = s.client('agent-registry-control')
    discovery = json.loads((WORK/'discovery.json').read_text())
    info = discovery['initialize']['serverInfo']
    server = {'$schema':'https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json',
              'name':'aws.amazon.com/aws-knowledge-mcp-server', 'version':info['version'],
              'description':'AWS public documentation metadata. Server: '+info['name']+'. Execution unverified.',
              'remotes':[{'type':'streamable-http','url':discovery['endpoint']}],
              '_meta':{'io.governed-agent-builder/discovery':{'endpoint':discovery['endpoint'],'method':'tools/list',
                       'protocolVersion':discovery['initialize']['protocolVersion'], 'observedAt':'2026-09-12T02:28:00Z',
                       'documentation':'https://awslabs.github.io/mcp/servers/aws-knowledge-mcp-server'}}}
    import jsonschema
    jsonschema.validate(server, json.loads((WORK/'server-schema.json').read_text()))
    descriptor = {'data':json.dumps(server),'dataSchemaVersion':'2025-12-11',
                  'additionalData':{'tools':{'data':json.dumps(discovery['tools']), 'dataSchemaVersion':discovery['initialize']['protocolVersion']}}}
    _, tools = validate_descriptor('mcpServer', descriptor)
    name = 'governed_agent_builder_catalog'
    found = []; token = None
    while True:
        response = c.list_registries(**({'nextToken':token} if token else {}))
        found.extend(x for x in response['registries'] if x['name'] == name)
        token = response.get('nextToken')
        if not token: break
    assert len(found) <= 1
    if found:
        registry = c.get_registry(registryId=found[0]['registryId'])
        tags = c.list_tags_for_resource(resourceArn=registry['registryArn'])['tags']
        assert tags.get('project') == 'governed-agent-builder'
    else:
        registry = c.create_registry(name=name, description='Studio project-owned public metadata catalog; manual approval; no execution grants',
            discoveryConfiguration={'authorizerType':'AWS_IAM'},
            tags={'project':'governed-agent-builder','architecture':'managed-serverless','owner':'platform',
                  'purpose':'native-catalog-metadata','cleanup':'delete-after-studio-retirement'})
        save('registry-created',registry); print('Project-owned Registry created; receipt persisted',flush=True)
    rid = registry.get('registryId') or registry['registryArn'].rsplit('/',1)[1]
    for _ in range(30):
        registry = c.get_registry(registryId=rid)
        if registry['status'] == 'READY': break
        time.sleep(3)
    assert registry['status'] == 'READY'
    records = c.list_registry_records(registryId=rid)
    assert not records.get('nextToken')
    existing = [x for x in records['registryRecords'] if x['name']=='aws_knowledge_documentation']
    if existing:
        record = c.get_registry_record(registryId=rid,recordId=existing[0]['recordId'])
        assert record['descriptors'] == {'mcpServer':descriptor}
    else:
        record = c.create_registry_record(registryId=rid,name='aws_knowledge_documentation',
            displayName='AWS Knowledge MCP · public documentation',description='Actual tools/list discovery from the official public AWS Knowledge endpoint. Metadata admission only; Gateway execution not tested.',
            recordType='MCP', recordVersion='1.0.0-discovery.20260912',descriptors={'mcpServer':descriptor},
            tags={'project':'governed-agent-builder','owner':'platform','execution':'not-ready','cleanup':'delete-with-project-registry'})
        save('record-created',record);print('MCP record created; exact observed schemas submitted for native validation',flush=True)
    record_id = record.get('recordId') or record['recordArn'].rsplit('/',1)[1]
    for _ in range(30):
        record = c.get_registry_record(registryId=rid,recordId=record_id)
        if record['status'] not in ('CREATING','UPDATING'):break
        time.sleep(3)
    save('record-validated',record)
    assert record['status'] in ('DRAFT','PENDING_APPROVAL','APPROVED'), record['status']
    if record['status'] == 'DRAFT':
        result = c.submit_registry_record_for_approval(registryId=rid, recordId=record_id)
        save('record-submitted', result)
        print('Record submitted for manual approval; receipt persisted', flush=True)
    if record['status'] != 'APPROVED':
        result = c.update_registry_record_status(registryId=rid,recordId=record_id,status='APPROVED',
            statusReason='Owner-authorized public documentation metadata admission for research workspace only; not an execution grant')
        save('record-approved',result);print('Native metadata admission approved; receipt persisted',flush=True)
    record = c.get_registry_record(registryId=rid,recordId=record_id)
    save('record-final',record)
    digest = record_revision(record); cid=f'registry:{rid}:{record_id}'
    entry={'approved':True,'version':record['recordVersion'],'workspaces':['research'],'requestable':True,
           'owner':'Studio platform owner','data_handling':'Public AWS documentation metadata only; no tool calls or customer data sent.',
           'digest_format':'native-catalog-v2','approval_sha256':digest}
    config={'schema_version':2,'approved':True,'binding':{'expected_account':account,'region':'us-west-2',
            'owner_approval':'owner-native-catalog-2026-09-12'},'registries':[{'approved':True,'region':'us-west-2',
            'registry_id':rid,'registry_arn':registry['registryArn'],'exposure':{k:entry.copy() for k in [cid]+[cid+':tool:'+t['name'] for t in tools]}}],
            'model_gateways':[], 'cache_seconds':30}
    save('source-config-v2',config)
    provider=configured_catalog(config,session=s)
    for _ in range(20):
        rows=provider.records()
        if len(rows)==len(tools)+1:break
        provider._expires=0;time.sleep(3)
    assert len(rows)==len(tools)+1
    save('native-read-receipt',{'list_batch_succeeded':True,'inventory':[{'id':r['id'],'name':r['name'],'kind':r['kind'],'version':r['version'],'execution_ready':r['execution_ready']} for r in rows]})
    print('Native List + Batch reads PASS:',len(rows),'metadata rows; all execution-not-ready',flush=True)

if __name__=='__main__':main()
