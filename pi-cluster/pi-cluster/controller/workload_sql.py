"""SQL workload: sequential inserts against the current primary, one row per
transaction, each id acknowledged before the next is sent.  The acked ids are
therefore 1..acked with no gaps, which is what the prefix check relies on."""

import threading
import time

import psycopg2

SETUP = ["create table if not exists pic_log (id bigint primary key, "
         "ts timestamptz not null default now())",
         "truncate pic_log"]


class SqlWorkload(threading.Thread):
    def __init__(self, endpoint, timeout, emit):
        super().__init__(daemon=True)
        self.endpoint, self.timeout, self.emit = endpoint, timeout, emit
        self.stop_ev = threading.Event()
        self.ok = self.acked = 0
        self.resyncs = []
        self.per_second = {}
        self.t0 = None

    def connect(self):
        host, port = self.endpoint()
        ms = max(1000, int(self.timeout * 1000))
        # statement_timeout is enforced by the server, so it cannot expire
        # once the server is gone; a blocked INSERT then waits in the kernel
        # for as long as TCP retransmits.  tcp_user_timeout and keepalives
        # are what make a client notice silence.
        c = psycopg2.connect(host=host, port=port, user="pic", dbname="pic",
                             connect_timeout=max(1, int(self.timeout)),
                             tcp_user_timeout=ms, keepalives=1, keepalives_idle=1,
                             keepalives_interval=1, keepalives_count=2,
                             options=f"-c statement_timeout={ms}")
        c.autocommit = True
        return c

    def setup(self):
        with self.connect() as c, c.cursor() as cur:
            for stmt in SETUP:
                cur.execute(stmt)

    def resync(self, cur):
        """After an uncertain outcome, ask the server what it actually has.

        The write in flight when the primary vanished may well have
        committed; retrying the same id then fails on the primary key
        forever.  Whatever the server now reports as committed is
        acknowledged - late, but acknowledged."""
        cur.execute("select coalesce(max(id), 0) from pic_log")
        server_max = cur.fetchone()[0]
        if server_max != self.acked:
            self.emit("workload_resynced", client_acked=self.acked,
                      server_max=server_max)
            self.resyncs.append({"client_acked": self.acked, "server_max": server_max})
            self.acked = max(self.acked, server_max)

    def run(self):
        self.t0 = time.monotonic()
        stalled, conn, cur = False, None, None
        while not self.stop_ev.is_set():
            try:
                if conn is None:
                    conn = self.connect()
                    cur = conn.cursor()
                    if stalled:
                        self.resync(cur)
                cur.execute("insert into pic_log (id) values (%s)", (self.acked + 1,))
                self.acked += 1          # committed and acknowledged
                self.ok += 1
                sec = int(time.monotonic() - self.t0)
                self.per_second[sec] = self.per_second.get(sec, 0) + 1
                if stalled:
                    stalled = False
                    self.emit("workload_resumed")
            except Exception as e:       # psycopg2 raises many subclasses
                if not stalled:
                    stalled = True
                    self.emit("workload_stalled", reason=_reason(e))
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                conn = cur = None
                self.stop_ev.wait(0.2)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def stop(self):
        self.stop_ev.set()
        if self.ident is not None:      # setup may have failed before start()
            self.join(timeout=10)
        self.hung = self.is_alive()

    def summary(self):
        # `acked` is the number of ids the server confirmed committed.  The
        # id after it may or may not be committed: that request was in doubt
        # when the primary vanished.
        return {"acked": self.acked, "hung": getattr(self, "hung", None),
                "resyncs": self.resyncs}


def _reason(e):
    text = str(e).strip().replace("\n", " ")
    if isinstance(e, LookupError):
        return f"no_target: {text}"
    if ("timeout expired" in text or "statement timeout" in text
            or "Connection timed out" in text or "timeout" in text.lower()):
        return "timeout"
    if "server closed the connection" in text or "EOF" in text:
        return "eof"
    if "Connection refused" in text:
        return "refused"
    return f"{type(e).__name__}: {text[:120]}"
