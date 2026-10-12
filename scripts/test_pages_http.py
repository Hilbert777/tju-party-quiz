"""Exercise HTTP acceptance checks against working and deliberately broken servers."""
import gzip
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from verify_pages_http import verify


BODY = ("<!DOCTYPE html><html><body>入党申请人复习 " + "题库内容 " * 1000 + "</body></html>").encode("utf-8")
PACKED = gzip.compress(BODY, mtime=0)
MODIFIED = "Mon, 12 Oct 2026 01:00:00 GMT"


class HttpChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class FixtureHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                mode = self.path.strip("/")
                encoded = self.headers.get("Accept-Encoding") == "gzip"
                etag = 'W/"fixture-gzip"' if encoded else '"fixture-identity"'
                tag = self.headers.get("If-None-Match")
                since = self.headers.get("If-Modified-Since")
                status = 304 if (tag == etag or (not tag and since == MODIFIED)) else 200
                if mode == "no-304":
                    status = 200
                if mode == "wrong-precedence" and since == MODIFIED:
                    status = 304
                if mode == "404":
                    status = 404
                body = PACKED if encoded else BODY
                if mode == "no-gzip":
                    body = BODY
                if mode == "wrong-gzip" and encoded:
                    body = gzip.compress(b"different page", mtime=0)
                if mode == "corrupt-gzip" and encoded:
                    body = PACKED[:10]
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("ETag", etag)
                self.send_header("Last-Modified", MODIFIED)
                self.send_header("Cache-Control", "no-store" if mode == "no-store" else "max-age=600")
                if mode != "no-vary":
                    self.send_header("Vary", "Accept-Encoding")
                if encoded and mode != "no-gzip":
                    self.send_header("Content-Encoding", "gzip")
                self.end_headers()
                if status != 304:
                    self.wfile.write(body)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_working_site_and_matching_release(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "index.html"
            expected.write_bytes(BODY)
            result = verify(self.url, expected)
        self.assertTrue(result["passed"], result["checks"])
        self.assertEqual(result["requests"]["gzip_etag"]["status"], 304)
        self.assertEqual(result["requests"]["outdated_etag"]["status"], 200)

    def test_rejects_broken_compression_and_cache(self):
        for mode in ("404", "no-gzip", "wrong-gzip", "corrupt-gzip", "no-vary", "no-store", "no-304", "wrong-precedence"):
            with self.subTest(mode=mode):
                self.assertFalse(verify(self.url + mode)["passed"])

    def test_rejects_an_old_deployed_release(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "index.html"
            expected.write_bytes(b"new release")
            result = verify(self.url, expected)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"][-1]["passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
