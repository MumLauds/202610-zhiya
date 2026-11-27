"""Optional private H08 TLS and fault wrapper for the unmodified H05 mock."""
import argparse
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
import ipaddress
from pathlib import Path
import re
import ssl
import sys
from time import sleep
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mock"))
from server import DEFAULT_FIXTURES_DIR, MockRequestHandler, MockState, error_payload


PRIVATE_V4 = tuple(ipaddress.ip_network(net) for net in ("127.0.0.0/8", "10.0.0.0/8",
                                                         "172.16.0.0/12", "192.168.0.0/16"))


def private_ip(value):
    address = ipaddress.ip_address(value)
    if not any(address in network for network in PRIVATE_V4):
        raise ValueError("Use an explicit private IPv4 or loopback address")
    return address


def issue_credentials(directory, hostname, ip=None, *, expired=False):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,126}", hostname):
        raise ValueError("Invalid DNS name")
    sans = [x509.DNSName(hostname)]
    if ip:
        sans.append(x509.IPAddress(private_ip(ip)))
    directory.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    server_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Zhiya ephemeral H08 demo CA")])
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=3)).not_valid_after(now + timedelta(days=7))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    cert = (x509.CertificateBuilder().subject_name(server_name).issuer_name(ca_name)
            .public_key(server_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2))
            .not_valid_after(now + timedelta(days=-1 if expired else 2))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, None, None), critical=True)
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    ca_path, cert_path, key_path = (directory / name for name in ("ca.pem", "server.pem", "server.key"))
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))
    return ca_path, cert_path, key_path


class LabHandler(MockRequestHandler):
    def _dispatch(self, method):
        scenario = self.headers.get("X-Mock-Error")
        path = urlsplit(self.path).path
        self.committed_504 = (scenario == "H08_COMMITTED_504" and method == "POST" and
                              path == "/api/v1/reading-sessions")
        if self.committed_504 or scenario == "H08_SLOW":
            del self.headers["X-Mock-Error"]
        if scenario == "H08_SLOW":
            sleep(9)
        try:
            super()._dispatch(method)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError):
            pass  # The diagnostic is allowed to cancel or time out while the lab waits.

    def _send_json(self, status, payload, extra_headers=None):
        if self.committed_504 and status == 201:
            status = 504
            payload = error_payload("REQUEST_OUTCOME_UNKNOWN", "Lab hides the committed response",
                                    False, self.request_id)
        super()._send_json(status, payload, extra_headers)


class LabServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, host, port, certificate=None, key=None):
        private_ip(host)
        self.state = MockState(DEFAULT_FIXTURES_DIR)
        self.quiet = True
        if bool(certificate) != bool(key):
            raise ValueError("Both TLS certificate and key are required")
        tls = None
        if certificate:
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(certificate, key)
        super().__init__((host, port), LabHandler)
        if tls:
            self.socket = tls.wrap_socket(self.socket, server_side=True, do_handshake_on_connect=False)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(12)
        return connection, address

    def handle_error(self, request, address):
        if not isinstance(sys.exc_info()[1], (ssl.SSLError, BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, address)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cert = sub.add_parser("cert")
    cert.add_argument("--out", type=Path, required=True)
    cert.add_argument("--name", required=True)
    cert.add_argument("--ip")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--cert", type=Path)
    serve.add_argument("--key", type=Path)
    args = parser.parse_args()
    if args.command == "cert":
        for path in issue_credentials(args.out, args.name, args.ip):
            print(path)
    else:
        with LabServer(args.host, args.port, args.cert, args.key) as server:
            protocol = "https" if args.cert else "http"
            print(f"H08 DEMO {protocol}://{args.host}:{server.server_port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
