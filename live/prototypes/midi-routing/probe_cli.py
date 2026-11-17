#!/usr/bin/env python3
"""Submit exactly one nonce-bound fixed probe operation. Never auto-retry."""
import argparse
import json
from pathlib import Path
import sys
import time

from LightpadMidiRoutingProbe.mailbox import atomic_json
from LightpadMidiRoutingProbe.probe import OPERATIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=sorted(OPERATIONS)+['status'])
    parser.add_argument('payload', nargs='?', default='{}', help='JSON object, fixed operation fields only')
    parser.add_argument('--directory', type=Path, default=Path(__file__).parent/'mailbox')
    parser.add_argument('--wait', type=float, default=8.0)
    args = parser.parse_args()
    directory = args.directory.resolve()
    state = json.loads((directory/'status.json').read_text())
    if args.operation == 'status':
        print(json.dumps(state, indent=2)); return
    payload = json.loads(args.payload)
    if not isinstance(payload, dict) or set(payload)&{'session', 'seq', 'expires_at', 'op'}:
        raise ValueError('payload must be an object without mailbox envelope fields')
    if state.get('status') != 'ready' or args.operation not in state.get('operations', []):
        raise ValueError('operation not advertised by ready probe')
    request = directory/'request.json'
    if request.exists():
        old = json.loads(request.read_text())
        if old.get('session') == state['session'] and old.get('seq', 0) > state['last_seq']:
            raise ValueError('prior request outstanding; inspect status without replacing or replaying')
    seq = state['last_seq']+1
    command = dict(payload, session=state['session'], seq=seq, expires_at=time.time()+15,
                   op=args.operation)
    atomic_json(request, command)
    deadline = time.monotonic()+min(max(args.wait, .1), 20)
    while time.monotonic() < deadline:
        result = json.loads((directory/'status.json').read_text())
        if result.get('session') != state['session']:
            raise ValueError('probe session changed; do not replay')
        if result.get('last_seq', 0) >= seq:
            print(json.dumps(result, indent=2))
            return 0 if result.get('result', {}).get('ok') else 1
        time.sleep(.1)
    print(json.dumps({'pending': True, 'seq': seq, 'message': 'Inspect status; do not replay'}))
    return 3


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
