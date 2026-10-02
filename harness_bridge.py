"""Local queue I/O only. Codex writes decisions directly; this calls no model."""
import argparse
import json
import urllib.request
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def exchange(action, value=None):
    token = (Path(__file__).resolve().parent / '.cache/harness-token').read_text().strip()
    request = urllib.request.Request('http://127.0.0.1:8095/internal/harness/' + action,
        data=json.dumps(value).encode() if value is not None else None,
        headers={'X-DBS-Harness': token, 'Content-Type': 'application/json'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=25) as response:
        return json.load(response)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['next', 'reply'])
    parser.add_argument('--id')
    parser.add_argument('--decision', help='JSON decision composed directly in-session')
    args = parser.parse_args()
    value = {'id': args.id, 'decision': json.loads(args.decision)} if args.action == 'reply' else None
    print(json.dumps(exchange(args.action, value), ensure_ascii=False))
