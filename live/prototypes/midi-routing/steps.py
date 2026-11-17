"""Throwaway host driver. Every call submits one explicit fixed Native operation."""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent

def step(op, payload=None, label=None):
    result = subprocess.run([sys.executable, str(ROOT/'probe_cli.py'), op,
                             json.dumps(payload or {})], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    data = json.loads(result.stdout)
    answer = data['result']
    if label:
        (ROOT/(label+'.json')).write_text(json.dumps(data, indent=2)+'\n')
    with (ROOT/'host-steps.jsonl').open('a') as log:
        log.write(json.dumps({'time':time.time(),'op':op,'payload':payload,'result':answer})+'\n')
    return answer

def inspect(*names, label=None):
    data=step('inspect',{'tracks':list(names)} if names else {},label)
    return {track['name']:track for track in data['observation']['tracks']}

def route(track, prop, target, label=None):
    observed=inspect(track)[track]['routing'][prop]['available']
    choices=[r for r in observed if r['display_name']==target]
    if len(choices)!=1:
        raise ValueError(f'{track} {prop} target {target} unavailable/ambiguous: {observed}')
    chosen=choices[0]
    return step('route',{'track':track,'property':prop,'identifier':chosen['identifier'],
                         'display_name':chosen['display_name']},label)

def track(name, prop, value):
    return step('track',{'track':name,'property':prop,'value':value})

def mixer(name, value, prop='volume', index=None):
    payload={'track':name,'property':prop,'value':value}
    if index is not None:payload['index']=index
    return step('mixer',payload)
