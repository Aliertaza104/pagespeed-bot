"""Local browser dashboard for the Python PageSpeed bot."""
import argparse
import hashlib
import json
import math
import os
import secrets
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from pagespeed_bot import normalize

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / 'reports' / 'ui'
TOKEN = secrets.token_urlsafe(32)
JOBS = {}
LOCK = threading.Lock()


def read_json(path, default):
    try:
        # Release the file handle before parsing a potentially large response.
        # On Windows an open reader can briefly block the writer's replacement.
        text = path.read_text(encoding='utf-8')
        return json.loads(text)
    except (OSError, ValueError):
        return default


def run_job(job_id, url, strategy, limit):
    folder = REPORTS / job_id
    folder.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(ROOT / 'pagespeed_bot.py'), url, '--strategy', strategy,
               '--max-pages', str(limit), '--output', str(folder), '--env-file', str(ROOT / '.env')]
    try:
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        for line in process.stdout:
            with LOCK:
                JOBS[job_id]['message'] = line.strip()
                if line.startswith('Auditing '):
                    JOBS[job_id]['phase'] = 'auditing'
        code = process.wait()
        with LOCK:
            JOBS[job_id]['status'] = 'completed' if code == 0 else 'failed'
            JOBS[job_id]['phase'] = 'finished'
    except Exception:
        with LOCK:
            JOBS[job_id].update(status='failed', phase='finished', message='Could not start the audit process.')


def key_configured():
    key = os.environ.get('PAGESPEED_API_KEY')
    if key is None:
        try:
            for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
                if line.strip().startswith('PAGESPEED_API_KEY='):
                    key = line.split('=', 1)[1].strip().strip("\"'")
        except OSError:
            pass
    return bool(key and key != 'replace_with_your_google_api_key')


def paginated(rows, query):
    page = max(1, int(query.get('page', ['1'])[0]))
    size = min(100, max(1, int(query.get('size', ['10'])[0])))
    search = query.get('search', [''])[0].lower()
    device = query.get('device', ['all'])[0]
    filtered = [r for r in rows if search in r.get('url', '').lower() and (device == 'all' or r.get('strategy') == device)]
    pages = max(1, math.ceil(len(filtered) / size))
    page = min(page, pages)
    return dict(items=filtered[(page - 1) * size:page * size], total=len(filtered), page=page, pages=pages, size=size)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, data, content_type='application/json', attachment=None):
        body = json.dumps(data).encode() if content_type == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path in ('/', '/app.js', '/style.css', '/insights.css'):
            name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/insights.css': 'insights.css'}[path.path]
            mime = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'style.css': 'text/css; charset=utf-8', 'insights.css': 'text/css; charset=utf-8'}[name]
            return self.respond(200, (ROOT / 'ui' / name).read_bytes(), mime)
        if path.path == '/api/config':
            return self.respond(200, dict(token=TOKEN, configured=key_configured()))
        if path.path == '/api/jobs':
            with LOCK:
                jobs = [dict(id=k, **v) for k, v in reversed(list(JOBS.items()))]
            return self.respond(200, jobs)
        parts = path.path.strip('/').split('/')
        if len(parts) < 3 or parts[:2] != ['api', 'jobs']:
            return self.respond(404, dict(error='Not found'))
        job_id = parts[2]
        with LOCK:
            job = JOBS.get(job_id)
            job = dict(job) if job else None
        if not job:
            return self.respond(404, dict(error='Audit not found'))
        folder = REPORTS / job_id
        rows = read_json(folder / 'results.json', [])
        discovery = read_json(folder / 'discovery.json', {})
        action = parts[3] if len(parts) > 3 else 'status'
        query = parse_qs(path.query)
        if action == 'status':
            scores = [r['scores']['performance'] for r in rows if r.get('scores', {}).get('performance') is not None]
            expected = len(set(discovery.get('pages', []) + discovery.get('missingRequiredPages', []))) * (2 if job['strategy'] == 'both' else 1)
            return self.respond(200, dict(id=job_id, **job, discovered=len(discovery.get('pages', [])), audited=len(rows), expected=expected,
                                         average=round(sum(scores) / len(scores)) if scores else None,
                                         errors=sum(bool(r.get('error')) for r in rows), discovery=discovery))
        if action == 'results':
            try:
                return self.respond(200, paginated(rows, query))
            except ValueError:
                return self.respond(400, dict(error='Invalid pagination'))
        if action == 'detail':
            url, strategy = query.get('url', [''])[0], query.get('strategy', [''])[0]
            filename = hashlib.sha256(f'{url}|{strategy}'.encode()).hexdigest() + '.json'
            data = read_json(folder / 'raw' / filename, None)
            return self.respond(200 if data else 404, data or dict(error='No raw audit available'))
        if action in ('csv', 'json', 'comparison'):
            filename = 'performance_comparison.csv' if action == 'comparison' else 'results.' + action
            try:
                return self.respond(200, (folder / filename).read_bytes(), 'text/csv; charset=utf-8' if action in ('csv', 'comparison') else 'application/octet-stream', filename)
            except OSError:
                return self.respond(404, dict(error='Results are not available yet'))
        return self.respond(404, dict(error='Not found'))

    def do_POST(self):
        if self.path != '/api/jobs':
            return self.respond(404, dict(error='Not found'))
        if self.headers.get('X-Session-Token') != TOKEN:
            return self.respond(403, dict(error='Reload the dashboard and try again'))
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            url = normalize(data.get('url'))
            strategy = data.get('strategy', 'both')
            limit = int(data.get('maxPages', 100))
            if not url or strategy not in ('both', 'mobile', 'desktop') or not 1 <= limit <= 10000:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            return self.respond(400, dict(error='Enter a valid HTTP(S) URL and a page limit from 1 to 10000'))
        if not key_configured():
            return self.respond(400, dict(error='Add PAGESPEED_API_KEY to .env, then try again'))
        with LOCK:
            if any(job['status'] == 'running' for job in JOBS.values()):
                return self.respond(409, dict(error='An audit is already running. Wait for it to finish.'))
            job_id = uuid.uuid4().hex
            JOBS[job_id] = dict(url=url, strategy=strategy, maxPages=limit, status='running', phase='discovering',
                                message='Finding public pages…', createdAt=datetime.now(timezone.utc).isoformat())
            folder = REPORTS / job_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'job.json').write_text(json.dumps(JOBS[job_id]), encoding='utf-8')
        threading.Thread(target=run_job, args=(job_id, url, strategy, limit), daemon=True).start()
        return self.respond(202, dict(id=job_id))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    REPORTS.mkdir(parents=True, exist_ok=True)
    for folder in sorted(REPORTS.iterdir(), key=lambda p: p.name):
        job = read_json(folder / 'job.json', None)
        if job:
            rows = read_json(folder / 'results.json', [])
            job.update(status='saved', phase='finished', message='Saved audit. Start a new audit for fresh measurements.')
            JOBS[folder.name] = job
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'PageSpeed dashboard: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
