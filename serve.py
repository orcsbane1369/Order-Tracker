"""Production launcher (Waitress). Settings come from environment variables; see README.md."""
import os

from waitress import serve

from app import app

if __name__ == "__main__":
    host = os.environ.get("OT_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    serve(app, host=host, port=port, threads=int(os.environ.get("OT_THREADS", "4")))
