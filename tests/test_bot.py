import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from io import BytesIO
from email.message import Message
from pagespeed_bot import normalize, extract_links, summarize, save_results, discover, save_json, replace_report, required_urls


class Response(BytesIO):
    def __init__(self, url, body, kind):
        super().__init__(body.encode())
        self.url = url
        self.headers = Message()
        self.headers['Content-Type'] = kind


class BotTests(unittest.TestCase):
    def test_report_columns_units_dates_and_device_pairing(self):
        data = {'lighthouseResult': {'fetchTime': '2026-10-12T06:00:00Z', 'categories': {'performance': {'score': .85}},
                'audits': {k: {'numericValue': v} for k, v in [('first-contentful-paint', 2486), ('largest-contentful-paint', 4065), ('speed-index', 6206), ('total-blocking-time', 519.5), ('cumulative-layout-shift', 0)]}}}
        mobile = summarize(data, 'https://www.nationalag.com/', 'mobile')
        desktop = summarize(data, 'https://www.nationalag.com/', 'desktop')
        self.assertEqual(mobile['audit_date'], '2026-10-12')
        self.assertEqual(mobile['pagespeed_link_type'], 'launch_new_analysis')
        self.assertIsNone(mobile['saved_analysis_url'])
        with tempfile.TemporaryDirectory() as directory:
            save_results(Path(directory), [mobile, desktop])
            with (Path(directory) / 'performance_comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row['Mobile FCP'], '2.5 s')
            self.assertEqual(row['Mobile SI'], '6.2 s')
            self.assertEqual(row['Mobile TBT'], '520 ms')
            self.assertEqual(row['Mobile CLS'], '0')
            self.assertEqual(row['Desktop Performance'], '85')
            self.assertEqual(row['Desktop Audit Date'], '2026-10-12')

    def test_required_naf_pages_and_other_sites(self):
        urls = required_urls('https://www.nationalag.com/')
        self.assertEqual(len(urls), 14)
        self.assertIn('https://www.nationalag.com/financial-solutions/farm-input-loan', urls)
        self.assertEqual(required_urls('https://example.com/'), [])
        self.assertEqual(len(required_urls('https://staging.example.com/', 'naf')), 14)

    @patch('pagespeed_bot.time.sleep')
    def test_missing_required_page_is_explicit(self, _):
        site = 'https://example.com'
        def fake(url, api=False):
            if url.endswith('/robots.txt'):
                return Response(url, 'User-agent: *\nDisallow: /private\n', 'text/plain')
            if url.endswith('/sitemap.xml'):
                return Response(url, '<urlset/>', 'application/xml')
            if url.endswith('/missing'):
                raise RuntimeError('HTTP 404')
            return Response(url, '<html></html>', 'text/html')
        with patch('pagespeed_bot.request', side_effect=fake):
            result = discover(site + '/', 10, required=[site + '/missing', site + '/private'])
        self.assertEqual(result['missingRequiredPages'], [site + '/missing', site + '/private'])
        with self.assertRaises(RuntimeError):
            with patch('pagespeed_bot.request', side_effect=fake):
                discover(site + '/', 1, required=[site + '/missing', site + '/private'])

    @patch('pagespeed_bot.time.sleep')
    def test_report_replacement_retries_temporary_windows_lock(self, sleep):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            save_json(output / 'results.json', {'old': True})
            original = Path.replace
            attempts = []
            def locked_once(source, target):
                attempts.append(target)
                if len(attempts) == 1:
                    self.assertEqual(json.loads(target.read_text()), {'old': True})
                    raise PermissionError(13, 'Access denied')
                return original(source, target)
            with patch.object(Path, 'replace', locked_once):
                save_json(output / 'results.json', {'new': True})
            self.assertEqual(json.loads((output / 'results.json').read_text()), {'new': True})
            self.assertEqual(len(attempts), 2)
            sleep.assert_called_once_with(0.05)

    @patch('pagespeed_bot.time.sleep')
    def test_persistent_lock_preserves_old_report_and_pending_data(self, sleep):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            save_json(output / 'results.json', {'old': True})
            with patch.object(Path, 'replace', side_effect=PermissionError(13, 'Access denied')) as replace:
                with self.assertRaises(PermissionError):
                    save_json(output / 'results.json', {'new': True})
            self.assertEqual(replace.call_count, 12)
            self.assertEqual(json.loads((output / 'results.json').read_text()), {'old': True})
            self.assertEqual(json.loads((output / 'results.json.tmp').read_text()), {'new': True})

    @patch('pagespeed_bot.time.sleep')
    def test_csv_replacement_retries_and_other_errors_do_not(self, sleep):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            original = Path.replace
            blocked = False
            def locked_csv(source, target):
                nonlocal blocked
                if target.suffix == '.csv' and not blocked:
                    blocked = True
                    raise PermissionError(13, 'Access denied')
                return original(source, target)
            with patch.object(Path, 'replace', locked_csv):
                save_results(output, [{'url': 'https://example.com', 'strategy': 'mobile'}])
            self.assertTrue((output / 'results.csv').exists())
            with patch.object(Path, 'replace', side_effect=OSError(28, 'Disk full')) as replace:
                with self.assertRaises(OSError):
                    replace_report(output / 'missing.tmp', output / 'results.json')
            self.assertEqual(replace.call_count, 1)

    def test_normalization(self):
        self.assertEqual(normalize('/shop?utm_source=x&page=2#top', 'https://example.com'), 'https://example.com/shop?page=2')
        self.assertIsNone(normalize('mailto:a@example.com'))
        self.assertIsNone(normalize(None, 'https://example.com'))

    def test_html_base(self):
        self.assertEqual(extract_links('<base href="/docs/"><a href="guide">Guide</a>', 'https://example.com'), ['https://example.com/docs/guide'])

    def test_metrics_and_csv(self):
        row = summarize({'lighthouseResult': {'categories': {'performance': {'score': .83}, 'seo': {'score': None}}, 'audits': {'largest-contentful-paint': {'numericValue': 2000}}}}, 'https://example.com', 'mobile')
        self.assertEqual(row['scores']['performance'], 83)
        self.assertIsNone(row['scores']['seo'])
        self.assertEqual(row['metrics']['largest-contentful-paint'], 2000)
        with self.assertRaises(RuntimeError):
            summarize({}, '', 'mobile')
        with tempfile.TemporaryDirectory() as directory:
            save_results(Path(directory), [{'url': '=formula', 'strategy': 'mobile', 'error': 'a,"b"'}])
            with (Path(directory) / 'results.csv').open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[1][0], "'=formula")
            self.assertEqual(rows[1][-1], 'a,"b"')

    @patch('pagespeed_bot.time.sleep')
    def test_discovery_robots_sitemap_and_limit(self, _):
        site = 'https://example.com'
        fixtures = {
            '/robots.txt': ('User-agent: *\nDisallow: /private\n', 'text/plain'),
            '/sitemap.xml': ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/about</loc></url></urlset>', 'application/xml'),
            '/': ('<a href="/private">Private</a><a href="/contact">Contact</a><a href="https://other.com">Other</a>', 'text/html'),
            '/about': ('<a href="/contact">Contact</a>', 'text/html')}
        def fake(url, api=False):
            body, kind = fixtures[url.removeprefix(site)]
            return Response(url, body, kind)
        with patch('pagespeed_bot.request', side_effect=fake):
            result = discover(site + '/', 2)
        self.assertEqual(result['pages'], [site + '/', site + '/about'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['warnings'], [])


if __name__ == '__main__':
    unittest.main()
