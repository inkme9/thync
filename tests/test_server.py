import http.client
import threading
import unittest
from http.server import ThreadingHTTPServer

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


if __name__ == "__main__":
    unittest.main()
