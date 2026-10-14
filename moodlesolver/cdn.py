import http.server
import os
import socketserver

def serve_dumps(dumps_dir, port=8931):
    os.chdir(dumps_dir)

    class H(http.server.SimpleHTTPRequestHandler):

        def log_message(self, *a):
            pass
    with socketserver.TCPServer(('0.0.0.0', port), H) as httpd:
        httpd.serve_forever()
