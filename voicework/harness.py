"""Local JSON client: python3 -m voicework.harness --request request.json.

Uses the running app's reviewed profile. It never reads an unrelated repository,
discovers sources, saves lessons, or publishes the generated writing.
"""
import argparse
import http.client
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


def local_address(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise ValueError('Use a loopback HTTP origin, such as http://127.0.0.1:8765')
    return parsed.hostname, parsed.port or 80


def request_json(address, path, body=None, token=None):
    connection = http.client.HTTPConnection(*address, timeout=30)
    headers = {'Origin': 'http://%s:%d' % address}
    if address[1] == 80:
        headers['Origin'] = 'http://' + address[0]
    if body is not None:
        headers.update({'Content-Type': 'application/json', 'X-Voicework-Token': token or ''})
    try:
        connection.request('POST' if body is not None else 'GET', path,
                           None if body is None else json.dumps(body, allow_nan=False), headers)
        response = connection.getresponse()
        raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('Voicework returned an oversized response')
        result = json.loads(raw)
        if response.status >= 300:
            raise ValueError(result.get('error', 'Voicework request failed'))
        return result
    finally:
        connection.close()


def run(request, url='http://127.0.0.1:8765', check=False, timeout=3600):
    if not isinstance(request, dict):
        raise ValueError('The request must be a JSON object')
    address = local_address(url)
    token = request_json(address, '/api/session')['token']
    job = request_json(address, '/api/document-checks' if check else '/api/documents', request, token)
    deadline = time.monotonic() + timeout
    while job['status'] in ('pending', 'running'):
        if time.monotonic() >= deadline:
            raise ValueError('Client wait timed out; the local job may still be running. Job: ' + job['id'])
        time.sleep(.5)
        job = request_json(address, '/api/documents/' + job['id'])
    if job['status'] != 'complete':
        raise ValueError(job.get('error', 'Document job failed'))
    return job['result']


def main(argv=None):
    parser = argparse.ArgumentParser(description='Rewrite or check a document using your running local Voicework profile')
    parser.add_argument('--request', required=True, help='JSON request path, or - for stdin')
    parser.add_argument('--output', help='New JSON result path; defaults to stdout')
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--check', action='store_true', help='Check source against supplied text without rewriting')
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.timeout <= 14400:
            raise ValueError('Timeout must be between 1 and 14400 seconds')
        if args.output and Path(args.output).exists():
            raise ValueError('Output already exists; choose a new result path')
        if args.request == '-':
            raw = sys.stdin.read(1024 * 1024 + 1)
        else:
            with Path(args.request).open(encoding='utf-8') as handle:
                raw = handle.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Request exceeds the 1 MiB client limit')
        result = run(json.loads(raw), args.url, args.check, args.timeout)
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
        if args.output:
            # Exclusive creation avoids overwriting source documents. Private mode
            # is set at creation, rather than after writing sensitive content.
            import os
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(rendered)
        else:
            sys.stdout.write(rendered)
        return 0 if result.get('checks', {}).get('status') == 'checked' else 3
    except (ValueError, KeyError, OSError, http.client.HTTPException) as exc:
        parser.exit(2, 'Voicework harness: %s\n' % exc)


if __name__ == '__main__':
    sys.exit(main())
