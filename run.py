"""Entry point for the RoleQuill web service."""
import os
import socket
import sys

from app import create_app

HOST, PORT = "127.0.0.1", 5000


def _port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


app = create_app()

if __name__ == "__main__":
    # Only guard on the FIRST launch — the debug reloader re-runs this file in a
    # child process (WERKZEUG_RUN_MAIN=true); skip the check there or it can
    # false-trip and kill the server on every reload.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true" and _port_in_use(HOST, PORT):
        print("\n" + "!" * 70)
        print(f"  Port {PORT} is ALREADY IN USE — another RoleQuill server is running.")
        print("  You are probably viewing that stale instance (started before your")
        print("  .env changes). Stop it first: close the other terminal or press")
        print("  Ctrl+C there, then run `python run.py` again.")
        print("!" * 70 + "\n")
        sys.exit(1)
    print(f"[RoleQuill] Serving on http://{HOST}:{PORT}  (Ctrl+C to stop)")
    # threaded=True so background search threads + request handling coexist
    app.run(host=HOST, port=PORT, debug=True, threaded=True)
