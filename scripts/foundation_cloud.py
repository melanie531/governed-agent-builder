"""Safe-default scoped CloudFormation driver; every write rechecks Studio."""
import argparse
import json

from botocore.exceptions import ClientError
from foundation_harness.config import canonical, digest
from infra.foundation import template
from .foundation_target import StudioTarget, PROJECT, STACK, sanitized

INITIAL_TEMPLATE_DIGEST = '509b16774fa4960215fb493df417617f18618fe1577f4aa1a452df8e55757187'
REPAIR_SET = {'ModelRole', 'FoundationRole', 'ModelGateway', 'ModelTarget', 'ToolPolicy'}
FAILED_REPLACEMENTS = {'ModelTarget', 'ToolPolicy'}


def inspect_change_set(response, expected, repair=False):
    if (response.get('Status') != 'CREATE_COMPLETE' or response.get('ExecutionStatus') != 'AVAILABLE'
            or response.get('StackName') != STACK):
        raise RuntimeError('CHANGESET_NOT_EXECUTABLE')
    changes = [x['ResourceChange'] for x in response.get('Changes', [])]
    selected = {x['LogicalResourceId'] for x in changes}
    if (response.get('NextToken') or len(selected) != len(changes) or not changes
            or (not repair and selected != set(expected['Resources']))
            or (repair and not selected <= set(expected['Resources']))):
        raise RuntimeError('CHANGESET_RESOURCE_SET_MISMATCH')
    for change in changes:
        replacement_allowed = (repair and change['LogicalResourceId'] in FAILED_REPLACEMENTS)
        if (change['Action'] not in (('Add', 'Modify') if repair else ('Add',))
                or (repair and change['Action'] == 'Add' and not replacement_allowed)
                or change.get('Replacement') not in ((None, 'False', 'True') if replacement_allowed else (None, 'False'))
                or change['ResourceType'] != expected['Resources'][change['LogicalResourceId']]['Type']):
            raise RuntimeError('ONLY_OWNED_NEW_RESOURCES_ALLOWED')
        if repair and change['LogicalResourceId'] not in REPAIR_SET:
            # CREATE_FAILED recovery reports unchanged tags/attribute dependencies.
            # No direct property modification outside the reviewed correction.
            details = change.get('Details', [])
            if not details or any(
                d.get('Target', {}).get('RequiresRecreation') != 'Never'
                or not (d.get('Target', {}).get('Attribute') == 'Tags'
                        or d.get('ChangeSource') == 'ResourceAttribute')
                for d in details
            ):
                raise RuntimeError('UNREVIEWED_REPAIR_PROPERTY_CHANGE')
    return [{'action': x['Action'], 'logical_id': x['LogicalResourceId'], 'type': x['ResourceType']} for x in changes]


def main(action):
    body = template()
    fingerprint = digest(body)
    repair = action.endswith('-repair')
    name = 'foundation-' + fingerprint[:32] + ('-repair' if repair else '')
    if action == 'template':
        return body
    target = StudioTarget()
    target.verify()
    cf = target.client('cloudformation')
    action = action.removesuffix('-repair')
    if repair:
        stack = cf.describe_stacks(StackName=STACK)['Stacks'][0]
        tags = {x['Key']: x['Value'] for x in stack.get('Tags', [])}
        current = cf.get_template(StackName=STACK)['TemplateBody']
        if isinstance(current, str):
            current = json.loads(current)
        if (stack['StackStatus'] != 'CREATE_FAILED' or tags.get('project') != PROJECT
                or tags.get('scope') != 'foundation-m0' or digest(current) != INITIAL_TEMPLATE_DIGEST):
            raise RuntimeError('EXACT_FAILED_OWNED_STACK_REPAIR_ONLY')
        # Only the observed initial template and exact four-resource correction.
        differences = {k for k in body['Resources'] if current['Resources'].get(k) != body['Resources'][k]}
        if set(current['Resources']) != set(body['Resources']) or not differences <= REPAIR_SET:
            raise RuntimeError('REPAIR_TEMPLATE_SCOPE_CHANGED')
        inventory = cf.list_stack_resources(StackName=STACK)
        failed = {r['LogicalResourceId'] for r in inventory['StackResourceSummaries']
                  if r['ResourceStatus'] == 'CREATE_FAILED'}
        if inventory.get('NextToken') or failed != FAILED_REPLACEMENTS:
            raise RuntimeError('FAILED_RESOURCE_REPLACEMENT_BINDING_CHANGED')
    if action == 'prepare' and not repair:
        try:
            stack = cf.describe_stacks(StackName=STACK)['Stacks'][0]
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'ValidationError' or 'does not exist' not in exc.response['Error']['Message']:
                raise
        else:
            # Do not adopt/update any preexisting or failed stack.
            raise RuntimeError('STACK_ALREADY_EXISTS: inspect owned change set/status; no update/adoption')
        target.verify()
        cf.create_change_set(StackName=STACK, ChangeSetName=name, ChangeSetType='CREATE',
            TemplateBody=canonical(body).decode(), Capabilities=['CAPABILITY_NAMED_IAM'],
            ClientToken=fingerprint, Tags=[{'Key': 'project', 'Value': PROJECT},
                                          {'Key': 'scope', 'Value': 'foundation-m0'}],
            Description='New isolated M0 definitions; no inference/provider permission')
        return {'change_set': name, 'template_digest': fingerprint, 'stage': 'INSPECT_PENDING'}
    if action == 'prepare' and repair:
        target.verify()
        cf.create_change_set(StackName=STACK, ChangeSetName=name, ChangeSetType='UPDATE',
            TemplateBody=canonical(body).decode(), Capabilities=['CAPABILITY_NAMED_IAM'],
            ClientToken=fingerprint + '-repair', Tags=stack['Tags'],
            Description='Repair two failed owned placeholders; preserve tags and deny inference')
        return {'change_set': name, 'template_digest': fingerprint, 'stage': 'REPAIR_INSPECT_PENDING'}
    if action in ('inspect', 'execute'):
        response = cf.describe_change_set(StackName=STACK, ChangeSetName=name)
        if repair and {x['Key']: x['Value'] for x in response.get('Tags', [])} != tags:
            raise RuntimeError('REPAIR_MUST_PRESERVE_STACK_TAGS')
        saved = cf.get_template(StackName=STACK, ChangeSetName=name)['TemplateBody']
        if isinstance(saved, str):
            saved = json.loads(saved)
        if digest(saved) != fingerprint:
            raise RuntimeError('CHANGESET_TEMPLATE_CHANGED')
        changes = inspect_change_set(response, body, repair=repair)
        if action == 'execute':
            target.verify()
            cf.execute_change_set(StackName=STACK, ChangeSetName=name, ClientRequestToken=fingerprint,
                                  DisableRollback=True)
        return {'change_set': name, 'template_digest': fingerprint, 'changes': changes,
                'stage': 'SUBMITTED' if action == 'execute' else 'INSPECTED'}
    if action == 'status':
        stack = cf.describe_stacks(StackName=STACK)['Stacks'][0]
        response = cf.list_stack_resources(StackName=STACK)
        resources = response['StackResourceSummaries']
        events = cf.describe_stack_events(StackName=STACK)['StackEvents'][:30]
        return {'status': stack['StackStatus'], 'resources': [
            {'logical_id': x['LogicalResourceId'], 'type': x['ResourceType'], 'status': x['ResourceStatus']}
            for x in resources], 'recent_failures': [
                {'logical_id': e['LogicalResourceId'],
                 'reason': sanitized(RuntimeError(e.get('ResourceStatusReason', '')))}
                for e in events if 'FAILED' in e['ResourceStatus']]}
    raise ValueError('UNKNOWN_ACTION')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='template',
                        choices=['template', 'prepare', 'inspect', 'execute', 'status',
                                 'prepare-repair', 'inspect-repair', 'execute-repair'])
    args = parser.parse_args()
    try:
        print(json.dumps(main(args.action), indent=2))
    except Exception as exc:
        print(sanitized(exc))
        raise SystemExit(1) from None
