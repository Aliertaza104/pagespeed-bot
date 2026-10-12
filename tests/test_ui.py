import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import ui_server


class DashboardTests(unittest.TestCase):
    def test_pagination_filters_and_bounds(self):
        rows = [dict(url=f'https://example.com/page/{i}', strategy='mobile') for i in range(23)]
        rows.append(dict(url='https://example.com/desktop', strategy='desktop'))
        result = ui_server.paginated(rows, {'page':['3'], 'size':['10'], 'device':['mobile']})
        self.assertEqual((len(result['items']), result['total'], result['pages']), (3, 23, 3))
        result = ui_server.paginated(rows, {'page':['999'], 'search':['desktop']})
        self.assertEqual((result['page'], result['total']), (1, 1))

    def test_http_job_creation_results_and_detail(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ui_server, 'REPORTS', Path(directory)), patch.object(ui_server, 'JOBS', {}), patch.object(ui_server, 'key_configured', return_value=True), patch.object(ui_server, 'run_job') as worker:
            server = ThreadingHTTPServer(('127.0.0.1', 0), ui_server.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base + '/') as response:
                    self.assertIn(b'PageSpeed Studio', response.read())
                request = Request(base + '/api/jobs', data=json.dumps({'url':'https://example.com', 'maxPages':30}).encode(), headers={'Content-Type':'application/json'})
                with self.assertRaises(HTTPError) as error:
                    urlopen(request)
                self.assertEqual(error.exception.code, 403)
                request.add_header('X-Session-Token', ui_server.TOKEN)
                with urlopen(request) as response:
                    self.assertEqual(response.status, 202)
                    job_id = json.load(response)['id']
                worker.assert_called_once()
                folder = Path(directory) / job_id
                rows = [dict(url='https://example.com/', strategy='mobile', scores={'performance':85})]
                (folder / 'results.json').write_text(json.dumps(rows))
                (folder / 'discovery.json').write_text(json.dumps({'pages':['https://example.com/']}))
                with urlopen(base + f'/api/jobs/{job_id}/results?page=1&size=10') as response:
                    self.assertEqual(json.load(response)['total'], 1)
                with urlopen(base + f'/api/jobs/{job_id}') as response:
                    status = json.load(response)
                    self.assertEqual((status['average'], status['expected']), (85, 2))
                with self.assertRaises(HTTPError) as error:
                    urlopen(request)
                self.assertEqual(error.exception.code, 409)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
