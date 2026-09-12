"""Administrator-owned enrollment, shipped only in the protected backend package.

No HTTP route writes this file. Lambda code-update permission is the trust boundary;
Cognito signatures remain mandatory. A QA session is marked in its random cookie
before hashing, so deleting an enrollment also denies already-issued QA sessions.
"""
import json
from pathlib import Path
import time

from fastapi import HTTPException

REGISTRY = Path(__file__).with_name('qa_enrollments.json')


def approved(auth, claims, required=False):
    data = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {'enrollments': []}
    entries = [e for e in data['enrollments'] if e.get('subject') == claims.get('sub')]
    if not entries:
        if required:
            raise HTTPException(403, 'QA enrollment is unavailable or revoked')
        return False
    if len(entries) != 1:
        raise HTTPException(403, 'Ambiguous QA enrollment')
    e = entries[0]
    policy = auth.membership(claims)
    if (e.get('enabled') is not True or e.get('expires', 0) <= time.time()
            or e.get('issuer') != auth.issuer or e.get('client') != auth.client_id
            or e.get('origin') != auth.public_url or not e.get('approval')
            or not e.get('enrolled_by') or not e.get('enrolled_at')
            or e.get('group') not in ('studio-research', 'studio-admin')
            or claims.get('cognito:groups') != [e.get('group')]
            or e.get('role') != policy['role'] or e.get('workspace') != policy['workspace']):
        raise HTTPException(403, 'QA enrollment does not match approved membership')
    return True
