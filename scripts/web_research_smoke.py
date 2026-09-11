#!/usr/bin/env python3
"""Offline by default. Live needs an approved packaged manifest/config AND explicit flag."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from foundations.web_research import FOUNDATION, digest, skill_binding
from tools.web_fetch.handler import evidence
from runtime.web_research.gateways import ModelGateway, ToolGateway
from runtime.web_research.harness import run


def example_manifest():
    d = {'foundation_id': 'web-research', 'foundation_version': '1.0.0',
         'foundation_manifest_digest': digest(FOUNDATION), 'version': 1,
         'model_id': 'approved-claude', 'tools': ['approved-fetch'], 'skills': ['cited-brief'],
         'component_versions': {'approved-claude': '1', 'approved-fetch': '1', 'cited-brief': '1.0.0'},
         'prompt': 'Explain the supplied measurements and cite the evidence.',
         'source': 'approved-public-web',
         'research': {'question': 'What measurement does the source report?', 'urls': ['https://example.com/report'], 'report_format': 'text'},
         'dataset': [{'id': 'case', 'input': 'What was measured?', 'required_terms': ['NEVER_MODEL_INPUT']}],
         'rubric': {'profile': 'llm-required', 'criteria': 'Ground claims in evidence'}}
    for key in ('prompt', 'dataset', 'rubric'):
        d[key + '_ref'] = 'sha256:' + digest(d[key])
    d['digest'] = digest(d)
    return {'schema': 'web-research-manifest-v1', 'definition': d, 'bindings': {
        'cited-brief': skill_binding('cited-brief'),
        'approved-fetch': {'version': '1', 'approved': True, 'read_only': True, 'operation': 'web_fetch',
                           'allowed_hosts': ['example.com'], 'tool_name': 'research___web_fetch',
                           'endpoint': 'https://stub-tool.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp'},
        'approved-claude': {'version': '1', 'approved': True, 'provider': 'Amazon Bedrock',
                            'operation': '/v1/messages', 'route': 'bedrock/anthropic.claude-sonnet-4-6',
                            'endpoint': 'https://stub-model.gateway.bedrock-agentcore.us-west-2.amazonaws.com/inference/v1/messages'}}}


class StubTransport:
    """Explicit test adapter. Captures real adapter protocol requests; never performs HTTP."""
    def __init__(self, source_text, model_response):
        self.source_text, self.model_response = source_text, model_response
        self.requests = []

    def post(self, url, body, headers=None):
        self.requests.append({'url': url, 'body': body, 'headers': headers})
        if body.get('method') == 'tools/call':
            ev = evidence(body['params']['arguments']['url'], self.source_text.encode(), 'text/plain', '2026-09-11T00:00:00+00:00')
            return {'jsonrpc': '2.0', 'id': body['id'], 'result': {'content': [{'type': 'text', 'text': json.dumps(ev)}]}}, {}
        return {'type': 'message', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': self.model_response}]}, {}


def offline(source_text='Observed temperature was 17 degrees.', model_response=None):
    m = example_manifest()
    ev = evidence(m['definition']['research']['urls'][0], source_text.encode(), 'text/plain')
    supplied = model_response or json.dumps({'report': 'The measured temperature is 17 degrees [' + ev['citationID'] + '].',
                                           'citations': [{'citationID': ev['citationID'], 'URL': ev['finalURL']}]})
    stub = StubTransport(source_text, supplied)
    bindings = m['bindings']
    tool = ToolGateway(stub, bindings['approved-fetch']['endpoint'], bindings['approved-fetch']['tool_name'], 'us-west-2')
    model = ModelGateway(stub, bindings['approved-claude']['endpoint'], bindings['approved-claude']['route'], 'us-west-2', 512)
    return {'result': run(m, tool, model), 'captured_requests': stub.requests,
            'evidence_kind': 'OFFLINE STUB GATEWAYS, not live acceptance'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live', action='store_true')
    p.add_argument('--human-approval', action='store_true')
    p.add_argument('--manifest', type=Path)
    p.add_argument('--config', type=Path)
    p.add_argument('--source-text', type=Path)
    p.add_argument('--model-response', type=Path)
    args = p.parse_args()
    try:
        if args.live:
            if not args.human_approval or not args.manifest or not args.config:
                raise ValueError('Explicit live authority required')
            from runtime.web_research.app import configured_adapters
            m, c = json.loads(args.manifest.read_text()), json.loads(args.config.read_text())
            tool, model = configured_adapters(m, c, approved=True)
            result = run(m, tool, model, mode='live-gateways')
            # Don't print user prompts, endpoints, sources, reports or credentials in live mode.
            print(json.dumps({'mode': result['mode'], 'production_ready': False, 'judge': 'NOT_CONFIGURED'}))
        else:
            print(json.dumps(offline(args.source_text.read_text() if args.source_text else 'Observed temperature was 17 degrees.',
                                     args.model_response.read_text() if args.model_response else None), indent=2))
        return 0
    except Exception:
        print('Research smoke blocked; no fixture fallback or error detail export', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
