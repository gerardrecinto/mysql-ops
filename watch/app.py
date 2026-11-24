"""replication-watch: tiny HTTP service that reports MySQL replica health.

GET /healthz      200 when the process is up (liveness and readiness).
GET /replication  200 when the replica is healthy, 503 with a reason when it is not.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

import pymysql

MAX_LAG_SECONDS = int(os.environ.get("MAX_LAG_SECONDS", "30"))


def read_status():
    conn = pymysql.connect(host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
                           port=int(os.environ.get("MYSQL_PORT", "3306")),
                           user=os.environ.get("MYSQL_USER", "root"),
                           password=os.environ.get("MYSQL_PASSWORD", ""),
                           connect_timeout=3, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW REPLICA STATUS")
            return cur.fetchone()
    finally:
        conn.close()


def evaluate(row, max_lag=MAX_LAG_SECONDS):
    """Turn a SHOW REPLICA STATUS row into a verdict. Pure function, easy to test."""
    if not row:
        return {"healthy": False, "reason": "replication is not configured"}
    io, sql = row["Replica_IO_Running"], row["Replica_SQL_Running"]
    lag = row["Seconds_Behind_Source"]
    out = {"io_running": io, "sql_running": sql, "seconds_behind": lag,
           "last_error": row.get("Last_SQL_Error") or row.get("Last_IO_Error") or ""}
    if io != "Yes":
        return {**out, "healthy": False, "reason": "IO thread is not running"}
    if sql != "Yes":
        return {**out, "healthy": False, "reason": "SQL thread is not running"}
    if lag is None:
        return {**out, "healthy": False, "reason": "lag is unknown"}
    if lag > max_lag:
        return {**out, "healthy": False, "reason": f"lag {lag}s is over {max_lag}s"}
    return {**out, "healthy": True, "reason": "ok"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            return self._send(200, {"status": "ok"})
        if self.path == "/replication":
            try:
                verdict = evaluate(read_status())
            except Exception as e:  # database unreachable is an unhealthy replica
                verdict = {"healthy": False, "reason": f"cannot reach MySQL: {e.__class__.__name__}"}
            return self._send(200 if verdict["healthy"] else 503, verdict)
        self._send(404, {"error": "not found"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
