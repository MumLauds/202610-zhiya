"""Run the firmware protocol core and the H05 loopback HTTP/TLS integration."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest

from mock_lab import LabServer, issue_credentials

HERE = Path(__file__).resolve().parent
COMPONENT = HERE.parents[1] / "firmware/components/device_api"


@contextmanager
def run_server(*tls):
    with LabServer("127.0.0.1", 0, *tls) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


def exchange(port, path, *, context=None, hostname="h08.test", payload=None, scenario=None):
    body = b"" if payload is None else json.dumps(payload).encode()
    method = "GET" if payload is None else "POST"
    headers = [f"{method} {path} HTTP/1.1", f"Host: {hostname}", "Connection: close"]
    if body:
        headers.extend((f"Content-Length: {len(body)}", "Content-Type: application/json"))
    if scenario:
        headers.append(f"X-Mock-Error: {scenario}")
    with socket.create_connection(("127.0.0.1", port), timeout=3) as raw:
        stream = context.wrap_socket(raw, server_hostname=hostname) if context else raw
        with stream:
            stream.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body)
            return b"".join(iter(lambda: stream.recv(4096), b""))


class NetworkTest(unittest.TestCase):
    executable: Path
    wifi: Path
    transport: Path
    temporary: Path

    def parse(self, raw, kind, expected, code):
        path = self.temporary / "wire.bin"
        path.write_bytes(raw)
        result = subprocess.check_output([str(self.executable), kind, str(path)], text=True)
        self.assertTrue(result.startswith(f"{expected} {code} "), result)
        return result

    def test_protocol_core(self):
        subprocess.run([str(self.executable)], check=True)

    def test_wifi_lifecycle_with_sdk_shim(self):
        subprocess.run([str(self.wifi)], check=True)

    def test_transport_faults_with_sdk_shim(self):
        subprocess.run([str(self.transport)], check=True)

    def test_h05_http_books_and_error_envelopes(self):
        with run_server() as port:
            self.parse(exchange(port, "/api/v1/storybooks"), "shelf", "OK", 200)
            self.parse(exchange(port, "/api/v1/story-versions/2"), "detail", "OK", 200)
            for scenario, status, code in (("AUTH_REQUIRED", "UNAUTHORIZED", 401), ("FORBIDDEN", "FORBIDDEN", 403),
                                           ("RESOURCE_NOT_FOUND", "NOT_FOUND", 404),
                                           ("DEPENDENCY_UNAVAILABLE", "UNAVAILABLE", 503)):
                result = self.parse(exchange(port, "/api/v1/storybooks", scenario=scenario), "get", status, code)
                self.assertIn(scenario, result)
            for scenario in ("IDEMPOTENCY_CONFLICT", "SESSION_CONFLICT"):
                result = self.parse(exchange(port, "/api/v1/reading-sessions/1/events", payload={},
                                             scenario=scenario), "post", scenario, 409)
                self.assertIn(scenario, result)
            pending = exchange(port, "/api/v1/reading-sessions/1/events", payload={},
                               scenario="H08_REQUEST_IN_PROGRESS")
            self.assertIn(b"Retry-After: 2\r\n", pending)
            self.parse(pending, "post", "REQUEST_IN_PROGRESS", 409)

    def test_h05_write_commits_before_504_and_original_key_replays(self):
        body = {"childProfileId": 1, "storyVersionId": 2, "mode": "PARENT_CHILD", "clientRequestId": "lab-original-01"}
        path = "/api/v1/reading-sessions"
        with run_server() as port:
            before = json.loads(exchange(port, path).split(b"\r\n\r\n", 1)[1])["data"]["items"]
            existing = {item["readingSessionId"] for item in before}
            unknown = exchange(port, path, payload=body, scenario="H08_COMMITTED_504")
            self.parse(unknown, "post", "REQUEST_OUTCOME_UNKNOWN", 504)
            sessions = json.loads(exchange(port, path).split(b"\r\n\r\n", 1)[1])["data"]["items"]
            self.assertEqual(len(sessions), len(before) + 1)
            committed, = {item["readingSessionId"] for item in sessions} - existing
            self.parse(exchange(port, f"{path}/{committed}"), "session", "OK", 200)
            replay = exchange(port, path, payload=body)
            self.parse(replay, "created", "OK", 200)
            response = json.loads(replay.split(b"\r\n\r\n", 1)[1])["data"]
            self.assertEqual(response["readingSessionId"], committed)
            self.assertEqual(len(json.loads(exchange(port, path).split(b"\r\n\r\n", 1)[1])["data"]["items"]), len(sessions))

    def test_tls_uses_trust_and_hostname(self):
        ca, certificate, key = issue_credentials(self.temporary / "valid-tls", "h08.test", "127.0.0.1")
        with run_server(certificate, key) as port:
            trusted = ssl.create_default_context(cafile=str(ca))
            self.parse(exchange(port, "/api/v1/storybooks", context=trusted), "shelf", "OK", 200)
            self.parse(exchange(port, "/api/v1/story-versions/2", context=trusted, hostname="127.0.0.1"),
                       "detail", "OK", 200)
            with self.assertRaises(ssl.SSLCertVerificationError):
                exchange(port, "/api/v1/storybooks", context=trusted, hostname="other.test")
            with self.assertRaises(ssl.SSLCertVerificationError):
                exchange(port, "/api/v1/storybooks", context=ssl.create_default_context())

    def test_tls_rejects_expired_leaf(self):
        ca, certificate, key = issue_credentials(self.temporary / "expired-tls", "h08.test", expired=True)
        with run_server(certificate, key) as port:
            with self.assertRaises(ssl.SSLCertVerificationError):
                exchange(port, "/api/v1/storybooks", context=ssl.create_default_context(cafile=str(ca)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--idf", type=Path, default=os.environ.get("IDF_PATH"))
    args = parser.parse_args()
    if args.idf is None:
        parser.error("Pass the pinned ESP-IDF directory with --idf or IDF_PATH")
    components = args.idf.resolve() / "components"
    http, json_source = components / "http_parser", components / "json/cJSON"
    with tempfile.TemporaryDirectory(prefix="zhiya-h08-test-") as temporary:
        scratch = Path(temporary)
        executable = scratch / "network_regression.exe"
        shims = scratch / "sdk"
        for header in (
            "esp_err.h", "esp_event.h", "esp_netif.h", "esp_netif_sntp.h", "esp_random.h", "esp_timer.h",
            "esp_wifi.h", "freertos/FreeRTOS.h", "freertos/queue.h", "freertos/task.h", "nvs_flash.h",
            "mbedtls/platform_util.h", "esp_crt_bundle.h", "esp_tls.h", "lwip/netdb.h", "lwip/sockets.h",
            "sdkconfig.h",
        ):
            file = shims / header
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('#include "esp_host.h"\n', encoding="utf-8")
        command = [args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                   *[f"-I{p}" for p in (COMPONENT, COMPONENT / "include", http, json_source, HERE, shims)]]
        protocol_sources = [http / "http_parser.c", json_source / "cJSON.c", COMPONENT / "http.c", COMPONENT / "dto.c"]

        def compile_test(name, sources):
            output = scratch / f"{name}.exe"
            subprocess.run([*command, *map(str, sources), str(HERE / f"{name}.c"), "-lm", "-o", str(output)],
                           check=True)
            return output

        executable = compile_test("network_regression", protocol_sources)
        NetworkTest.wifi = compile_test("wifi_regression", [])
        NetworkTest.transport = compile_test("transport_regression", protocol_sources)
        NetworkTest.executable, NetworkTest.temporary = executable, scratch
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NetworkTest))
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
