import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from src.http_api import make_handler
from src.repository import Repository
from src.service import Service


class HttpApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "http.db"))
        service = Service(self.repo)
        handler = make_handler(service,
                               str(Path(__file__).resolve().parent.parent / "static"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.repo.close()
        self.tmp.cleanup()

    def _request(self, method, path, payload=None, role="radiation_officer",
                 actor="tester"):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = json.dumps(payload) if payload is not None else None
        headers = {"X-Actor": actor, "X-Role": role}
        if body:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        conn.close()
        return response.status, data

    def test_measurement_endpoints(self):
        status, data = self._request("POST", "/api/items", {
            "title": "http item", "description": "via api", "severity": "high",
            "quantity": 1, "threshold": 10, "external_ref": "HTTP-1"},
            role="dosimetrist")
        self.assertEqual(status, 201)
        item_id = data["id"]

        status, data = self._request(
            "POST", f"/api/items/{item_id}/measurements",
            {"dose": 11, "conclusion": "exceeded", "measured_by": "lab-a"})
        self.assertEqual(status, 201)
        self.assertEqual(data["conclusion"], "exceeded")

        status, data = self._request(
            "GET", f"/api/items/{item_id}/measurements", role="viewer")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["measurements"]), 1)

        status, data = self._request("GET", "/api/items", role="viewer")
        self.assertEqual(status, 200)
        first = data["items"][0]
        self.assertIn("remaining_hours", first)
        self.assertEqual(first["latest_conclusion"], "exceeded")

        # viewer无权登记检测
        status, data = self._request(
            "POST", f"/api/items/{item_id}/measurements",
            {"dose": 1, "conclusion": "normal", "measured_by": "lab"},
            role="viewer")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
