"""Foundation Library composition approval, independent of discovery/execution."""
from fastapi import HTTPException
from .live_catalog import projection


BINDING_MESSAGE = 'Platform owner must approve exact native_bindings IDs, versions and source revisions in this Foundation Library manifest. No fixture alias conversion.'


def binding(foundation, item, model_id=None):
    contract = foundation.get('native_bindings', {})
    if contract.get('approved') is not True:
        return 'binding_missing'
    entry = next((x for x in contract.get('components', []) if x.get('id') == item['id']), None)
    if not entry:
        return 'binding_missing'
    if entry.get('version') != item['version'] or not item.get('source_revision') or entry.get('source_revision') != item['source_revision']:
        return 'binding_stale'
    if item['kind'] != 'model' and 'compatible_model_ids' in entry and model_id not in entry['compatible_model_ids']:
        return 'model_incompatible'
    return None


def choices(db, persona, foundation, records, model_id=None):
    result = {'models': [], 'tools': [], 'skills': []}
    for item in records:
        public = projection(db, persona, item)
        if not public or item['kind'] + 's' not in result:
            continue
        issue = binding(foundation, item, model_id)
        # No inferred compatibility. Catalog still exposes unbound discoverable items.
        if issue:
            continue
        public.update(binding_status='bound', draft_selectable=True,
                      deployable=False, readiness_message='Save a version to resolve verified deployment bindings')
        result[item['kind'] + 's'].append(public)
    return result


def public_foundation(foundation, choices):
    result = {k: v for k, v in foundation.items() if k != 'native_bindings'}
    result.update({kind: [x['id'] for x in rows] for kind, rows in choices.items()})
    result.update(composition_mode='live', binding_message=BINDING_MESSAGE,
                  execution_status='Deployment readiness is resolved per saved version')
    return result


def assess(db, persona, definition, foundation, records, previous=None, deployment_issues=None):
    """Unknown new selections are forbidden; owned historical pins remain editable."""
    selected = [x for x in [definition['model_id'], *definition['tools'], *definition['skills']] if x]
    if len(set(selected)) != len(selected) or set(selected) != set(definition['component_versions']):
        raise HTTPException(422, 'Selected components and pinned version keys must match exactly')
    previous_ids = set(previous.get('component_versions', {})) if previous else set()
    visible = {x['id']: x for x in records if projection(db, persona, x)}
    issues = []
    if not foundation.get('approved') or foundation['version'] != definition['foundation_version']:
        issues.append({'code': 'foundation_changed', 'message': 'Foundation approval/version changed; revise and retest'})
    if not definition['model_id']:
        issues.append({'code': 'model_missing', 'message': 'Select a native model after Foundation Library binding approval'})
    for cid in selected:
        item = visible.get(cid)
        if item is None:
            if cid not in previous_ids:
                raise HTTPException(422, 'Capability unavailable; new hidden, unknown or fixture selections are forbidden')
            issues.append({'code': 'selection_unavailable', 'component_id': cid, 'message': 'Previously selected capability is no longer visible; remove or replace it'})
            continue
        expected = 'model' if cid == definition['model_id'] else 'tool' if cid in definition['tools'] else 'skill'
        if item['kind'] != expected:
            raise HTTPException(422, 'Selected capability kind mismatch')
        public = projection(db, persona, item)
        codes = []
        if item['version'] != definition['component_versions'][cid]:
            codes.append('version_changed')
        reason = binding(foundation, item, definition['model_id'])
        if reason:
            codes.append(reason)
        if item.get('external') and not persona.get('external_allowed'):
            codes.append('data_policy_denied')
        if not public['granted']:
            codes.append('grant_required')
        if (not item.get('execution_ready') or not item.get('integration_ready')
                or not item.get('supported', True)
                or item.get('execution_binding', {}).get('status') != 'verified'):
            codes.append('execution_not_ready')
        issues.extend({'code': code, 'component_id': cid, 'message': BINDING_MESSAGE if code.startswith('binding_') else code.replace('_', ' ')} for code in codes)
    issues.extend(deployment_issues if deployment_issues is not None else [
        {'code': 'deployment_driver_missing', 'message': 'Configure the server-owned Foundation deployment driver; no fixture fallback'}])
    return {'deployable': not issues, 'issues': issues}
