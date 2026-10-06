"""The /files/ proxy: relays to a stand-in FileBrowser, behind the panel's own checks."""

import base64
import hashlib
import http.client
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import app
import auth


class FakeFileBrowser(BaseHTTPRequestHandler):
    """Answers with what it received, so tests can see exactly what was forwarded."""
    release = threading.Event()

    def _echo(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if self.path == "/files/api/events":  # an event stream: first event now, the next later
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: first\n\n")
            self.wfile.flush()
            FakeFileBrowser.release.wait(5)
            self.wfile.write(b"data: second\n\n")
            return
        if self.path.startswith("/files/api/raw"):  # a stored file, served inline as Quantum does
            page = b"<form action=/players method=post><button>win</button></form>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Disposition", "inline")
            self.send_header("Content-Security-Policy", "script-src 'none'")
            self.send_header("X-Frame-Options", "ALLOWALL")  # upstream's say must not count
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        out = json.dumps({"method": self.command, "path": self.path, "headers": dict(self.headers),
                          "sha": hashlib.sha256(body).hexdigest(), "len": len(body)}).encode()
        self.send_response(201 if self.command == "POST" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "fb=1; Path=/files/; HttpOnly")
        self.send_header("Content-Security-Policy", "script-src 'self'")  # FileBrowser's own
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(out)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _echo

    def log_message(self, *a):
        pass


class FilesProxy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fb = ThreadingHTTPServer(("127.0.0.1", 0), FakeFileBrowser)
        threading.Thread(target=cls.fb.serve_forever, daemon=True).start()
        cls.saved = dict(app.CFG)
        app.CFG.update(files_upstream=f"http://127.0.0.1:{cls.fb.server_address[1]}", files_url="/files/",
                       password_hash="", allowed_hosts="")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.fb.shutdown()
        app.CFG.clear()
        app.CFG.update(cls.saved)

    def req(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"localhost:{self.port}", **(headers or {})}
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in h.items():
            c.putheader(k, v)
        if body is not None:
            c.putheader("Content-Length", str(len(body)))
        c.endheaders(body)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_get_is_relayed_with_status_headers_and_body(self):
        r, data = self.req("GET", "/files/api/resources?source=minecraft")
        self.assertEqual(r.status, 200)
        seen = json.loads(data)
        self.assertEqual(seen["path"], "/files/api/resources?source=minecraft")
        self.assertEqual(seen["headers"]["Host"], f"localhost:{self.port}")
        self.assertEqual(seen["headers"]["X-Forwarded-For"], "127.0.0.1")
        self.assertEqual(r.getheader("Set-Cookie"), "fb=1; Path=/files/; HttpOnly")

    def assert_unframeable(self, r):
        self.assertEqual(r.headers.get_all("X-Frame-Options"), ["DENY"])
        self.assertEqual(r.headers.get_all("X-Content-Type-Options"), ["nosniff"])
        self.assertIn("frame-ancestors 'none'", r.headers.get_all("Content-Security-Policy"))

    def test_the_file_manager_cannot_be_framed_by_any_site(self):
        """N-02: a page anywhere could iframe http://localhost:8080/files/ and
        clickjack a file manager that has no login of its own."""
        for path in ("/files/", "/files/api/resources?path=/", "/files/api/raw?files=/x.html"):
            r, _ = self.req("GET", path, {"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "cross-site"})
            self.assertEqual(r.status, 200, path)
            self.assert_unframeable(r)
        r, _ = self.req("GET", "/files")  # the proxy's own answers too
        self.assert_unframeable(r)

    def test_file_managers_own_csp_is_kept_alongside(self):
        r, _ = self.req("GET", "/files/")
        self.assertIn("script-src 'self'", r.headers.get_all("Content-Security-Policy"))
        self.assertNotIn("sandbox", r.headers.get_all("Content-Security-Policy"))  # the UI needs its scripts

    def test_stored_files_are_sandboxed(self):
        """N-02: a page dropped into /data and opened via the file manager must
        not act as the panel's origin (e.g. a one-click form to /players)."""
        r, body = self.req("GET", "/files/api/raw?files=/plugins/readme.html&inline=true")
        self.assertIn(b"<form", body)                   # still viewable...
        self.assertEqual(r.headers["Content-Disposition"], "inline")
        csp = r.headers.get_all("Content-Security-Policy")
        self.assertIn("sandbox", csp)                   # ...but in an origin of its own
        self.assertIn("script-src 'none'", csp)
        self.assert_unframeable(r)
        r, _ = self.req("GET", "/files/api/resources?path=/")
        self.assertIn("sandbox", r.headers.get_all("Content-Security-Policy"))

    def test_the_panels_login_is_not_passed_on(self):
        app.CFG["password_hash"] = auth.hash_password("correct horse battery")
        self.addCleanup(app.CFG.update, password_hash="")
        cred = "Basic " + base64.b64encode(b"admin:correct horse battery").decode()
        r, data = self.req("GET", "/files/", {"Authorization": cred})
        self.assertEqual(r.status, 200)
        self.assertNotIn("Authorization", json.loads(data)["headers"])

    def test_the_file_manager_needs_the_panel_login(self):
        app.CFG["password_hash"] = auth.hash_password("correct horse battery")
        self.addCleanup(app.CFG.update, password_hash="")
        self.assertEqual(self.req("GET", "/files/")[0].status, 401)

    def test_a_rebound_host_never_reaches_the_file_manager(self):
        """The F-02 attack: rebinding used to give a page write access to /data."""
        rebound = {"Host": f"attacker.example:{self.port}", "Origin": f"http://attacker.example:{self.port}",
                   "Sec-Fetch-Site": "same-origin"}
        for method in ("GET", "POST", "PUT", "DELETE"):
            r, _ = self.req(method, "/files/api/resources?path=/plugins/evil.jar", rebound,
                            b"x" if method in ("POST", "PUT") else None)
            self.assertEqual(r.status, 403, method)

    def test_cross_site_writes_are_refused(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            r, _ = self.req(method, "/files/api/resources", {"Sec-Fetch-Site": "cross-site"}, b"x")
            self.assertEqual(r.status, 403, method)

    def test_a_large_upload_arrives_intact(self):
        blob = bytes(range(256)) * (5 * 4096)  # 5 MiB
        r, data = self.req("POST", "/files/api/resources?path=/world.zip",
                           {"Sec-Fetch-Site": "same-origin", "Origin": f"http://localhost:{self.port}"}, blob)
        self.assertEqual(r.status, 201)
        seen = json.loads(data)
        self.assertEqual((seen["len"], seen["sha"]), (len(blob), hashlib.sha256(blob).hexdigest()))

    def test_put_delete_and_head_are_relayed(self):
        same = {"Sec-Fetch-Site": "same-origin"}
        self.assertEqual(json.loads(self.req("PUT", "/files/api/x", same, b"hi")[1])["method"], "PUT")
        self.assertEqual(json.loads(self.req("DELETE", "/files/api/x", same)[1])["method"], "DELETE")
        r, data = self.req("HEAD", "/files/")
        self.assertEqual((r.status, data), (200, b""))

    def test_an_event_stream_is_relayed_as_it_arrives(self):
        FakeFileBrowser.release.clear()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/files/api/events", headers={"Host": f"localhost:{self.port}"})
        r = c.getresponse()
        t0 = time.monotonic()
        self.assertEqual(r.read1(100), b"data: first\n\n")  # before the upstream has finished
        self.assertLess(time.monotonic() - t0, 2)
        FakeFileBrowser.release.set()
        self.assertEqual(r.read(), b"data: second\n\n")
        c.close()

    def test_files_without_slash_redirects(self):
        r, _ = self.req("GET", "/files")
        self.assertEqual((r.status, r.getheader("Location")), (308, "/files/"))

    def test_a_chunked_upload_is_refused_not_buffered(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest("PUT", "/files/api/x", skip_host=True)
        for k, v in {"Host": f"localhost:{self.port}", "Sec-Fetch-Site": "same-origin",
                     "Transfer-Encoding": "chunked"}.items():
            c.putheader(k, v)
        c.endheaders(b"0\r\n\r\n")
        self.assertEqual(c.getresponse().status, 411)
        c.close()

    def test_a_down_file_manager_is_a_502_not_a_hang(self):
        app.CFG["files_upstream"] = "http://127.0.0.1:9"  # discard port: nothing listens
        self.addCleanup(app.CFG.update, files_upstream=f"http://127.0.0.1:{self.fb.server_address[1]}")
        r, data = self.req("GET", "/files/")
        self.assertEqual(r.status, 502)
        self.assertIn(b"not reachable", data)
        self.assert_unframeable(r)

    def test_without_an_upstream_files_is_not_served(self):
        app.CFG["files_upstream"] = ""
        self.addCleanup(app.CFG.update, files_upstream=f"http://127.0.0.1:{self.fb.server_address[1]}")
        self.assertEqual(self.req("GET", "/files/")[0].status, 404)
        self.assertEqual(self.req("PUT", "/files/x", {"Sec-Fetch-Site": "same-origin"}, b"x")[0].status, 405)

    def test_panel_pages_still_refuse_put(self):
        self.assertEqual(self.req("PUT", "/settings", {"Sec-Fetch-Site": "same-origin"}, b"x")[0].status, 405)


if __name__ == "__main__":
    unittest.main()
