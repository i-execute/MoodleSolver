import base64
import http.server
import os
import re
import socketserver
import hashlib
import threading

_MASK_RE = re.compile(r'/img_([0-9a-f]{20})_moodlesolver\.(\w+)$')
_lock = threading.Lock()


def _hash_of(fname):
    raw = fname.rsplit('.', 1)[0]
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _resolve(h):
    with _lock:
        for f in os.listdir('.'):
            if not (f.endswith('.b64') or f.endswith('.png')
                    or f.endswith('.jpg') or f.endswith('.jpeg')):
                continue
            if _hash_of(f) == h:
                return f
    return None


class _H(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        m = _MASK_RE.search(self.path)
        real = None
        if m:
            real = _resolve(m.group(1))
        else:
            parts = [p for p in self.path.split('/') if p]
            if parts and os.path.exists(parts[-1]):
                real = parts[-1]
        if not real:
            self.send_error(404)
            return
        raw = open(real, 'rb').read()
        if real.endswith('.b64'):
            try:
                raw = base64.b64decode(raw)
            except Exception:
                pass
        ct = 'image/png'
        if raw[:3] == b'\xff\xd8\xff':
            ct = 'image/jpeg'
        self.send_response(200)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def serve_dumps(dumps_dir, port=8931):
    os.chdir(dumps_dir)
    with socketserver.ThreadingTCPServer(('0.0.0.0', port), _H) as httpd:
        httpd.serve_forever()


def clear_cache(dumps_dir='moodlesolver_dumps', keep=('result_',)):
    removed = 0
    if os.path.isdir(dumps_dir):
        for f in os.listdir(dumps_dir):
            if f.startswith(keep):
                continue
            p = os.path.join(dumps_dir, f)
            if os.path.isfile(p):
                os.unlink(p)
                removed += 1
    return removed
