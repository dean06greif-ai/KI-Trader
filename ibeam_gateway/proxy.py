"""Token-geschützter Reverse-Proxy vor dem IBKR Client-Portal-Gateway (IBeam).

Nur Python-Standardbibliothek (läuft im voyz/ibeam-Image ohne Zusatzpakete).

Env:
  PORT                  Render-Port, auf dem der Proxy lauscht (Default 10000)
  GATEWAY_TOKEN         Shared-Secret; Clients senden es als Header
                        X-Gateway-Token (oder ?token=...). Leer = KEIN Schutz
                        (nur für lokale Tests!).
  GATEWAY_UPSTREAM      Default https://localhost:5000  (Client-Portal-Gateway)
  HEALTH_UPSTREAM       Default http://localhost:5001   (IBeam-Health-Server)
"""
import http.client
import json
import os
import ssl
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "10000"))
TOKEN = (os.environ.get("GATEWAY_TOKEN") or "").strip()
GATEWAY = urllib.parse.urlparse(os.environ.get("GATEWAY_UPSTREAM", "https://localhost:5000"))
HEALTH = urllib.parse.urlparse(os.environ.get("HEALTH_UPSTREAM", "http://localhost:5001"))
HEALTH_PATHS = ("/livez", "/readyz")
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "host", "x-gateway-token",
               "content-length", "upgrade"}
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def _conn(target):
    if target.scheme == "https":
        return http.client.HTTPSConnection(target.hostname, target.port or 443,
                                           timeout=25, context=SSL_CTX)
    return http.client.HTTPConnection(target.hostname, target.port or 80, timeout=25)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ibeam-token-proxy/1.0"

    def log_message(self, fmt, *args):  # kompaktes Log ohne Token
        sys.stdout.write("proxy %s %s\n" % (self.command, self.path.split("?")[0]))

    def _send(self, status, body: bytes, ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authorized(self) -> bool:
        if not TOKEN:
            return True
        if self.headers.get("X-Gateway-Token", "") == TOKEN:
            return True
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (q.get("token") or [""])[0] == TOKEN

    def _handle(self):
        path = urllib.parse.urlparse(self.path).path
        if path in HEALTH_PATHS:
            return self._forward(HEALTH)
        if not self._authorized():
            return self._send(401, json.dumps({"error": "invalid gateway token"}).encode())
        return self._forward(GATEWAY)

    def _forward(self, target):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        headers["Host"] = f"{target.hostname}:{target.port}"
        try:
            c = _conn(target)
            c.request(self.command, self.path, body=body, headers=headers)
            r = c.getresponse()
            data = r.read()
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() not in HOP_HEADERS:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)
            c.close()
        except Exception as e:  # Gateway (noch) nicht erreichbar
            self._send(503, json.dumps({"error": f"upstream unavailable: {e}"}).encode())

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = _handle


if __name__ == "__main__":
    print(f"ibeam-token-proxy: listening on 0.0.0.0:{PORT} -> {GATEWAY.geturl()} "
          f"(token {'ON' if TOKEN else 'OFF – unsicher!'})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
