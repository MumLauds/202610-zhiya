"""Run with python -m unittest discover -s docs/testing/g15/local-https."""
import json
import unittest
from contextlib import redirect_stdout
from http.client import HTTPConnection
from http.server import HTTPServer
from io import StringIO
from threading import Thread

from provider import Provider


class SupplierLogTests(unittest.TestCase):
    def test_logs_success_and_failures_without_request_content(self):
        prompt = "本次共读阅读了第 1 页。小园把一只纸船放进水里。"
        cases = [
            ("/report", {"messages": [{"role": "user", "content": prompt}]},
             {"call": "report", "status": 200, "visitedPages": [1]}),
            ("/report", {"messages": []}, {"call": "report", "status": 422}),
            ("/private-test-value", {"unused": "x" * 512_000}, {"call": "unknown", "status": 404}),
        ]
        with HTTPServer(("127.0.0.1", 0), Provider) as server:
            for path, payload, expected in cases:
                with self.subTest(path=path), redirect_stdout(StringIO()) as output:
                    worker = Thread(target=server.handle_request)
                    worker.start()
                    client = HTTPConnection(*server.server_address, timeout=5)
                    try:
                        client.request("POST", path, json.dumps(payload))
                        response = client.getresponse()
                        self.assertEqual(response.status, expected["status"])
                        response.read()
                    finally:
                        client.close()
                        worker.join(timeout=5)
                    self.assertEqual(json.loads(output.getvalue()), expected)


if __name__ == "__main__":
    unittest.main()
