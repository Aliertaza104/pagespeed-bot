"""Discover public website pages and audit them with PageSpeed Insights."""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

AGENT = 'PageSpeedBot/1.0'
CATEGORIES = ('performance', 'accessibility', 'best-practices', 'seo')
METRICS = ('first-contentful-paint', 'largest-contentful-paint', 'total-blocking-time', 'cumulative-layout-shift', 'speed-index')
NAF_PAGES = {
    '/': 'Home Page', '/financial-solutions/farm-real-estate-loan': 'Farm Real Estate Loan',
    '/financial-solutions/farm-operating-loans': 'Farm Operating Loan',
    '/financial-solutions/farm-input-loan': 'Farm Input Loans', '/farm-loan-calculator': 'Farm Loan Calculator',
    '/blogs': 'Blog - Listing page', '/blogs/farm-mortgage-rates-what-moves-them-and-how-to-plan-your-purchase': 'Blogs/farm-mortgage-rates-what-moves-them-and-how-to-plan-your-purchase',
    '/blogs/operating-loan-vs-input-loan-which-fits-your-season': 'Blogs/operating-loan-vs-input-loan-which-fits-your-season',
    '/blogs/buying-the-neighbor-s-acres-financing-expansion-without-straining-cash-flow': 'Blogs/buying-the-neighbor-s-acres-financing-expansion-without-straining-cash-flow',
    '/about': 'About', '/about/team': 'Team', '/about/careers': 'Careers', '/contact-us': 'Contact', '/early-access': 'Early Access'
}


def report_metadata(url, strategy, measured_at=None):
    path = urlsplit(url).path.rstrip('/') or '/'
    return dict(page_name=NAF_PAGES.get(path, path.strip('/').replace('-', ' ').title() or 'Home Page'),
                website=origin(url), audit_timestamp=measured_at,
                audit_date=measured_at[:10] if measured_at else None,
                pagespeed_url='https://pagespeed.web.dev/analysis?' + urlencode({'url': url, 'form_factor': strategy}),
                pagespeed_link_type='launch_new_analysis', saved_analysis_url=None)


def required_urls(start, profile='auto'):
    host = urlsplit(start).hostname
    enabled = profile == 'naf' or (profile == 'auto' and host in ('nationalag.com', 'www.nationalag.com'))
    return [origin(start) + path for path in NAF_PAGES] if enabled else []


def normalize(value, base=''):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        u = urlsplit(urljoin(base, value.strip()))
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
            return None
        port = u.port
        host = u.hostname.lower()
        if ':' in host:
            host = '[' + host + ']'
        if port and port != {'http': 80, 'https': 443}[u.scheme]:
            host += ':' + str(port)
        query = sorted((k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not re.match(r'^(utm_|fbclid$|gclid$)', k, re.I))
        return urlunsplit((u.scheme, host, u.path or '/', urlencode(query), ''))
    except ValueError:
        return None


def origin(url):
    u = urlsplit(url)
    return u.scheme + '://' + u.netloc


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.base = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'base' and self.base is None:
            self.base = attrs.get('href')
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])


def extract_links(html, url):
    parser = Links()
    parser.feed(html)
    base = normalize(parser.base, url) or url
    return [link for href in parser.links if (link := normalize(href, base))]


def request(url, api=False):
    for attempt in range(4):
        try:
            return urlopen(Request(url, headers={'User-Agent': AGENT}), timeout=180 if api else 30)
        except HTTPError as error:
            status, retry = error.code, error.headers.get('Retry-After', '')
            error.close()
            if (status != 429 and status < 500) or attempt == 3:
                raise RuntimeError(f'HTTP {status}') from None
            delay = min(60, int(retry)) if retry.isdigit() else 2 ** (attempt + 1)
        except (URLError, OSError, TimeoutError):
            if attempt == 3:
                raise RuntimeError('Network request failed after retries') from None
            delay = 2 ** (attempt + 1)
        time.sleep(delay)


def replace_report(temporary, destination):
    """Retry Windows sharing violations while readers release the old report.

    Keep atomic replacement: readers always see a complete old or new report.
    Leave the temporary file intact if a persistent permission error occurs.
    """
    for attempt in range(12):
        try:
            temporary.replace(destination)
            return
        except OSError as error:
            transient = isinstance(error, PermissionError) or getattr(error, 'winerror', None) in (5, 32, 33)
            if not transient or attempt == 11:
                raise
            time.sleep(min(0.05 * 2 ** attempt, 1.0))


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    replace_report(temporary, path)


def discover(start, limit, extras=(), required=()):
    site = origin(start)
    robots = RobotFileParser(site + '/robots.txt')
    try:
        with request(robots.url) as response:
            text = response.read().decode('utf-8', errors='replace')
    except RuntimeError as error:
        if str(error) != 'HTTP 404':
            raise RuntimeError(f'Cannot read robots.txt: {error}') from None
        text = ''
    robots.parse(text.splitlines())
    queue, known, pages, warnings = deque(), set(), [], []
    truncated = False

    def eligible(url):
        return url and origin(url) == site and robots.can_fetch(AGENT, url) and not re.search(r'\.(pdf|png|jpe?g|gif|webp|svg|zip|mp4|css|js|woff2?|ico)(?:\?|$)', url, re.I)

    def add(value, base=start):
        nonlocal truncated
        url = normalize(value, base)
        if not eligible(url) or url in known:
            return
        if len(known) >= limit:
            truncated = True
            return
        known.add(url)
        queue.append(url)

    required = list(dict.fromkeys(normalize(url, start) for url in required))
    required = [url for url in required if url]
    if len(required) > limit:
        raise RuntimeError(f'Page limit must be at least {len(required)} to include all required report pages')
    add(start)
    for url in required:
        add(url)
    for url in extras:
        add(url)
    sitemaps = deque(dict.fromkeys([site + '/sitemap.xml', *(robots.site_maps() or [])]))
    seen = set()
    while sitemaps and len(seen) < 100:
        url = normalize(sitemaps.popleft(), start)
        if not eligible(url) or url in seen:
            continue
        seen.add(url)
        try:
            with request(url) as response:
                if origin(response.url) != site:
                    continue
                root = ET.fromstring(response.read())
            kind = root.tag.rsplit('}', 1)[-1]
            for node in root:
                loc = next((child.text for child in node if child.tag.rsplit('}', 1)[-1] == 'loc'), None)
                if kind == 'sitemapindex' and loc:
                    sitemaps.append(loc)
                elif kind == 'urlset':
                    add(loc)
        except (RuntimeError, ET.ParseError) as error:
            warnings.append(f'Sitemap {url}: {error}')
    if sitemaps:
        warnings.append('Sitemap discovery stopped at 100 files.')
    while queue:
        url = queue.popleft()
        print(f'Discovering: {url}', flush=True)
        try:
            with request(url) as response:
                if not eligible(normalize(response.url)) or response.headers.get_content_type() != 'text/html':
                    continue
                html = response.read().decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
                pages.append(url)
                for link in extract_links(html, response.url):
                    add(link)
        except (RuntimeError, LookupError) as error:
            warnings.append(f'Page {url}: {error}')
        time.sleep(max(0.25, robots.crawl_delay(AGENT) or 0))
    missing = [url for url in required if url not in pages]
    warnings.extend(f'Required report page unavailable or excluded: {url}' for url in missing)
    return dict(start=start, maxPages=limit, truncated=truncated, pages=pages, warnings=warnings,
                requiredPages=required, missingRequiredPages=missing)


def summarize(data, url, strategy):
    lighthouse = data.get('lighthouseResult') or {}
    if not lighthouse or lighthouse.get('runtimeError'):
        raise RuntimeError('No usable Lighthouse result returned')
    audits = lighthouse.get('audits', {})
    scores = {k: round(v['score'] * 100) if v.get('score') is not None else None for k, v in lighthouse.get('categories', {}).items()}
    issues = [v for v in audits.values() if isinstance(v.get('score'), (int, float)) and v['score'] < 1 and v.get('scoreDisplayMode') not in ('notApplicable', 'manual', 'informative')]
    return dict(**report_metadata(url, strategy, lighthouse.get('fetchTime') or data.get('analysisUTCTimestamp')), url=url, strategy=strategy, finalUrl=lighthouse.get('finalDisplayedUrl') or lighthouse.get('finalUrl'), fetchTime=lighthouse.get('fetchTime'), scores=scores,
                metrics={k: audits.get(k, {}).get('numericValue') for k in METRICS}, issues=issues, warnings=lighthouse.get('runWarnings', []),
                fieldData=data.get('loadingExperience'), originFieldData=data.get('originLoadingExperience'))


def save_results(output, rows):
    save_json(output / 'results.json', rows)
    columns = ('url', 'strategy', *CATEGORIES, *METRICS, 'page_name', 'website', 'audit_timestamp', 'audit_date', 'pagespeed_url', 'pagespeed_link_type', 'saved_analysis_url', 'error')
    temporary = output / 'results.csv.tmp'
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        for row in rows:
            flattened = {**row.get('scores', {}), **row.get('metrics', {}), **row}
            values = []
            for column in columns:
                value = flattened.get(column)
                if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
                    value = "'" + value
                values.append(value)
            writer.writerow(values)
    replace_report(temporary, output / 'results.csv')
    save_comparison(output, rows)


def save_comparison(output, rows):
    """One row per URL, with the workbook's mobile/desktop metric order."""
    metrics = [('Performance', 'performance'), ('FCP', 'first-contentful-paint'), ('SI', 'speed-index'),
               ('TBT', 'total-blocking-time'), ('LCP', 'largest-contentful-paint'), ('CLS', 'cumulative-layout-shift')]
    columns = ['Page', 'URL', 'Website']
    for device in ('Mobile', 'Desktop'):
        columns += [f'{device} {label}' for label, _ in metrics]
        columns += [f'{device} Audit Date', f'{device} Audit Timestamp', f'{device} PageSpeed Link', f'{device} Link Type', f'{device} Error', f'{device} Status']
    grouped = {}
    for row in rows:
        grouped.setdefault(row['url'], {})[row['strategy']] = row
    temporary = output / 'performance_comparison.csv.tmp'
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for url, devices in grouped.items():
            metadata = report_metadata(url, 'mobile')
            result = {'Page': metadata['page_name'], 'URL': url, 'Website': metadata['website']}
            for device in ('Mobile', 'Desktop'):
                row = devices.get(device.lower(), {})
                for label, key in metrics:
                    value = row.get('scores', {}).get(key) if label == 'Performance' else row.get('metrics', {}).get(key)
                    if value is not None:
                        if label in ('FCP', 'SI', 'LCP'):
                            value = f'{value / 1000:.1f} s'
                        elif label == 'TBT':
                            value = f'{value:.0f} ms'
                    result[f'{device} {label}'] = value
                result[f'{device} Audit Date'] = row.get('audit_date')
                result[f'{device} Audit Timestamp'] = row.get('audit_timestamp')
                result[f'{device} PageSpeed Link'] = row.get('pagespeed_url')
                result[f'{device} Link Type'] = row.get('pagespeed_link_type')
                result[f'{device} Error'] = row.get('error')
                result[f'{device} Status'] = 'failed' if row.get('error') else 'complete' if row else 'not audited'
            for key, value in result.items():
                if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
                    result[key] = "'" + value
            writer.writerow(result)
    replace_report(temporary, output / 'performance_comparison.csv')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--max-pages', type=int, default=100)
    parser.add_argument('--strategy', choices=('both', 'mobile', 'desktop'), default='both')
    parser.add_argument('--output', type=Path, default=Path('reports'))
    parser.add_argument('--urls-file', type=Path)
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--discover-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--report-profile', choices=('auto', 'naf', 'none'), default='auto')
    args = parser.parse_args(argv)
    start = normalize(args.url)
    if not start or args.max_pages < 1:
        parser.error('Provide an HTTP(S) URL and a positive page limit')
    if args.env_file.exists():
        for line in args.env_file.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                name, value = line.split('=', 1)
                os.environ.setdefault(name.strip(), value.strip().strip("\"'"))
    key = os.environ.get('PAGESPEED_API_KEY')
    if not args.discover_only and (not key or key == 'replace_with_your_google_api_key'):
        parser.error('Set PAGESPEED_API_KEY in .env first')
    args.output.mkdir(parents=True, exist_ok=True)
    raw = args.output / 'raw'
    raw.mkdir(exist_ok=True)
    extras = args.urls_file.read_text(encoding='utf-8-sig').splitlines() if args.urls_file else []
    required = required_urls(start, args.report_profile)
    discovery = discover(start, args.max_pages, extras, required)
    save_json(args.output / 'discovery.json', discovery)
    (args.output / 'urls.txt').write_text('\n'.join(discovery['pages']), encoding='utf-8')
    print(f"Discovered {len(discovery['pages'])} pages. Limit reached: {discovery['truncated']}")
    if args.discover_only:
        return 0 if discovery['pages'] else 1
    strategies = ('mobile', 'desktop') if args.strategy == 'both' else (args.strategy,)
    rows, failures = [], 0
    targets = list(dict.fromkeys([*discovery['pages'], *discovery['missingRequiredPages']]))
    for url in targets:
        for strategy in strategies:
            print(f'Auditing {url} ({strategy})', flush=True)
            filename = raw / (hashlib.sha256(f'{url}|{strategy}'.encode()).hexdigest() + '.json')
            try:
                if url in discovery['missingRequiredPages']:
                    raise RuntimeError('Required report page could not be discovered as robots-allowed HTML; see discovery warnings')
                data = None
                if args.resume and filename.exists():
                    try:
                        data = json.loads(filename.read_text(encoding='utf-8'))
                        summarize(data, url, strategy)
                    except (ValueError, RuntimeError):
                        data = None
                if data is None:
                    params = [('url', url), ('key', key), ('strategy', strategy), *[('category', c) for c in CATEGORIES]]
                    with request('https://www.googleapis.com/pagespeedonline/v5/runPagespeed?' + urlencode(params), api=True) as response:
                        data = json.load(response)
                    summarize(data, url, strategy)
                    save_json(filename, data)
                rows.append(summarize(data, url, strategy))
            except (RuntimeError, ValueError) as error:
                failures += 1
                message = str(error).replace(key, '[redacted]')
                rows.append(dict(**report_metadata(url, strategy), attempted_at=datetime.now(timezone.utc).isoformat(), url=url, strategy=strategy, error=message))
                print(message, file=sys.stderr)
            save_results(args.output, rows)
            time.sleep(1)
    print(f'Saved {len(rows)} audits to {args.output.resolve()}; {failures} failed.')
    return 1 if failures or not rows else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Stopped. Completed audits are saved; use --resume to continue.', file=sys.stderr)
        sys.exit(130)
