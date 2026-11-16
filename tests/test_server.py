import http.client
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from thync import server


class HostCheck(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def status(self, host: str) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        conn.request("GET", "/", headers={"Host": host})
        status = conn.getresponse().status
        conn.close()
        return status

    def test_local_and_tailscale_names_pass(self):
        for host in ("127.0.0.1:8765", "localhost", "[::1]:8765", "mini.tail1234.ts.net", "Mini.Tail1234.TS.NET:443"):
            self.assertNotEqual(self.status(host), 403, host)

    def test_other_names_are_refused(self):
        for host in ("evil.example", "ts.net", "evilts.net", "mini.ts.net.evil.example", ""):
            self.assertEqual(self.status(host), 403, host)


class TailscaleUsers(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(server, "TOKEN", "t"), mock.patch.object(server, "TAILSCALE_USERS", {"me@example.com"}),
                  mock.patch.object(server, "TAILSCALE_NAME", "mini.tail1234.ts.net"),
                  mock.patch.object(server, "build_graph", lambda days: {"days": days})):
            p.start()
            self.addCleanup(p.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def status(self, headers: dict, host: str = "mini.tail1234.ts.net", page: bool = True) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        sent = {"Host": host, **({"X-Thync-Token": ""} if page else {}), **headers}
        conn.request("GET", "/graph.json", headers=sent)
        status = conn.getresponse().status
        conn.close()
        return status

    def test_a_listed_login_needs_no_token(self):
        self.assertEqual(self.status({"Tailscale-User-Login": "me@example.com"}), 200)
        self.assertEqual(self.status({"Tailscale-User-Login": "Me@Example.com"}), 200)
        self.assertEqual(self.status({"Tailscale-User-Login": "=?utf-8?q?me=40example.com?="}), 200)  # RFC 2047

    def test_others_still_need_the_token(self):
        self.assertEqual(self.status({"Tailscale-User-Login": "someone@example.com"}), 401)
        self.assertEqual(self.status({"Tailscale-User-Login": ""}), 401)
        self.assertEqual(self.status({"Tailscale-User-Login": "=?bogus?q?me=40example.com?="}), 401)
        self.assertEqual(self.status({}), 401)
        self.assertEqual(self.status({"X-Thync-Token": "t"}), 200)

    def test_a_listed_login_needs_the_page_header(self):
        """A cross-site request cannot send X-Thync-Token without a preflight (CSRF)."""
        self.assertEqual(self.status({"Tailscale-User-Login": "me@example.com"}, page=False), 401)

    def test_a_listed_login_needs_this_machines_name(self):
        """Another *.ts.net name could be rebound to 127.0.0.1 by a hostile resolver."""
        for host in ("evil.tail9999.ts.net", "other.tail1234.ts.net", "127.0.0.1:8765", "localhost"):
            self.assertEqual(self.status({"Tailscale-User-Login": "me@example.com"}, host=host), 401, host)
        with mock.patch.object(server, "TAILSCALE_NAME", ""):
            self.assertEqual(self.status({"Tailscale-User-Login": "me@example.com"}), 401)

    def test_no_login_passes_when_none_is_listed(self):
        with mock.patch.object(server, "TAILSCALE_USERS", set()):
            self.assertEqual(self.status({"Tailscale-User-Login": "me@example.com"}), 401)
            self.assertEqual(self.status({"Tailscale-User-Login": ""}), 401)


if __name__ == "__main__":
    unittest.main()
