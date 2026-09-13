"""Add reviewed discovery to an existing native config without changing other sources.
The caller supplies existing authorized workspace scope. No cloud writes.
"""
import copy
from pathlib import Path

def with_discovery(existing, workspaces, cache_path=None):
    if not workspaces or any(not isinstance(x,str) or not x for x in workspaces):
        raise ValueError('Explicit approved workspace scope required')
    result=copy.deepcopy(existing)
    source={'approved':True,'source_id':'reviewed-model-discovery',
        'cache_path':str(cache_path or Path(__file__).resolve().parents[1]/'backend/reviewed_model_discovery_cache.json'),
        'scope':{'workspaces':list(workspaces),'owner':'Platform','data_handling':'Model metadata only; execution remains separately authorized'}}
    others=[s for s in result.get('discovery_sources',[]) if s.get('source_id')!=source['source_id']]
    result['discovery_sources']=others+[source]
    return result
