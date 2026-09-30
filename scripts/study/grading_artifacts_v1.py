"""Immutable local grading evidence; no active-protocol writes."""
import hashlib
import json
from .common import ROOT, STUDY


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_lines(path):
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def require_disabled_gates():
    active = json.loads((STUDY/'manifests/active_protocol.json').read_text())
    for key in ('live_enabled','historical_replay_enabled','confirmatory_evaluation_enabled','math_holdout_enabled'):
        if active[key] is not False:
            raise ValueError('unexpected_enabled_gate:'+key)


def save_or_verify(base, artifacts, inputs, code, write=False):
    def encode(value):
        return (json.dumps(value,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode('utf-8')
    payload = {}
    for name,value in artifacts.items():
        if name.endswith('.jsonl'):
            payload[name] = ''.join(json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(',',':'))+'\n' for row in value).encode('utf-8')
        else:payload[name]=encode(value)
    payload['manifest.json'] = encode({'version':base.name,'inputs':{p.relative_to(ROOT).as_posix():file_sha(p) for p in inputs},
        'code':{p:file_sha(ROOT/p) for p in code},'files':{p:hashlib.sha256(data).hexdigest() for p,data in payload.items()},
        'active_protocol_modified':False,'confirmatory_evaluation_enabled':False})
    for name,data in payload.items():
        path=base/name
        if path.exists() and path.read_bytes()!=data:
            raise ValueError('immutable_artifact_differs:'+str(path))
        if not path.exists() and not write:
            raise ValueError('missing_artifact:'+str(path))
    if write:
        base.mkdir(parents=True,exist_ok=True)
        for name,data in payload.items():
            path=base/name
            if not path.exists():path.write_bytes(data)
    return {p:hashlib.sha256(data).hexdigest() for p,data in payload.items()}
