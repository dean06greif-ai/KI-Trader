import http.server, os, sys
ROOT = "/app/ki-trader/frontend/build"
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=ROOT, **k)
    def send_head(self):
        p = self.translate_path(self.path)
        if not os.path.exists(p): self.path = "/index.html"
        return super().send_head()
http.server.ThreadingHTTPServer(("0.0.0.0", 3000), H).serve_forever()
