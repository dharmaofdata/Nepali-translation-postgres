#!/usr/bin/env python3
"""fake-postgres: line protocol on a TCP port.  'tx' -> 'ok', 'role' -> role."""

import argparse
import socketserver


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--role", required=True)
    a = ap.parse_args()

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            for line in self.rfile:
                cmd = line.strip()
                if cmd == b"tx":
                    self.wfile.write(b"ok\n")
                elif cmd == b"role":
                    self.wfile.write(a.role.encode() + b"\n")
                else:
                    self.wfile.write(b"err\n")
                self.wfile.flush()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", a.port), H) as srv:
        srv.serve_forever()


if __name__ == "__main__":
    main()
