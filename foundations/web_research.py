"""Approved source artifact, not an assertion of deployed service readiness."""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


SKILLS = {
    'cited-brief': {'version': '1.0.0', 'formats': ['text', 'json'],
                    'instruction': 'Write a concise evidence-grounded brief. Distinguish observations from uncertainty. Cite each supported claim.'},
    'comparison': {'version': '1.0.0', 'formats': ['text', 'json'],
                   'instruction': 'Compare the supplied sources, explicitly state disagreements and gaps, and cite each comparison.'},
}
FOUNDATION = {'id': 'web-research', 'name': 'Web research · NOT CONFIGURED', 'version': '1.0.0',
              'approved': True, 'integration_ready': False, 'integration_status': 'NOT_CONFIGURED',
              'description': 'Versioned HTTPS evidence → Gateway model report. Source artifact only; no AWS services connected.',
              'models': [], 'tools': [], 'skills': [], 'capabilities': ['text', 'citations'],
              'mandatory_defaults': ['allowlisted public HTTPS only', 'fetch first, report second', 'untrusted source isolation', 'judge incomplete blocks release'],
              'config_schema': {'question': 'string', 'urls': '1–5 public HTTPS URLs', 'report_format': ['text', 'json']},
              'builder': 'runtime.web_research.app:invoke'}


def skill_binding(skill_id):
    skill = SKILLS[skill_id]
    return {'version': skill['version'], 'artifact_digest': digest(skill), 'approved': True}
