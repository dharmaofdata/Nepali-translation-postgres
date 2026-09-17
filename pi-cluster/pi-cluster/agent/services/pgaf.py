"""pg_auto_failover adapters: a monitor service and an HA-managed postgres
service.

pg_autoctl supervises its own PostgreSQL, so the adapter's job is narrow:
create the node once, then run pg_autoctl as the service process and report
what the role actually is.  Nothing here promotes, demotes or decides a role -
that is the monitor's business, and the point of the slice is to watch it
decide.
"""

import grp
import os
import pwd
import shutil
import subprocess
import time

from .base import Service


class PgafBase(Service):
    proc = None
    reused = None

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.pgdata = self.state_dir / "pgdata"
        self.pgbin = self.bindir / "pg" / "bin"
        self.pgafbin = self.bindir / "pgaf" / "bin"
        self.logfile = self.state_dir / "pg_autoctl.log"
        self.user = self.params["run_as"]
        self.addr = self.peers["self"]
        pw = pwd.getpwnam(self.user)
        self.uid, self.gid = pw.pw_uid, pw.pw_gid
        self.groups = [g.gr_gid for g in grp.getgrall() if self.user in g.gr_mem]

    # --- helpers ---------------------------------------------------------

    def env(self):
        # pg_autoctl needs the PostgreSQL binaries next to it, and it keeps
        # its own configuration under $HOME, which here is node storage and
        # therefore survives node.off.
        # A fixed PATH, not the inherited one: pg_autoctl scans every entry
        # for pg_ctl and treats a directory it may not read (root's, since it
        # runs as another user) as a fatal error.
        return dict(os.environ, HOME=str(self.state_dir),
                    PATH=f"{self.pgbin}:{self.pgafbin}:/usr/bin:/bin",
                    LD_LIBRARY_PATH=str(self.pgbin.parent / "lib"),
                    XDG_CONFIG_HOME=str(self.state_dir / "config"),
                    XDG_DATA_HOME=str(self.state_dir / "data"),
                    XDG_RUNTIME_DIR=str(self.state_dir / "run"),
                    PGDATA=str(self.pgdata))

    def run(self, prog, *argv, timeout=300):
        exe = self.pgafbin / prog if prog == "pg_autoctl" else self.pgbin / prog
        r = subprocess.run([str(exe), *map(str, argv)], capture_output=True,
                           text=True, user=self.uid, group=self.gid,
                           extra_groups=self.groups, env=self.env(), timeout=timeout)
        if r.returncode:
            raise RuntimeError(f"{prog}: {(r.stderr or r.stdout).strip()[-800:]}")
        return r.stdout

    # The monitor database knows pg_auto_failover's own roles, not ours.
    psql_user = None

    def sql(self, query, db, user=None):
        return self.run("psql", "-Atq", "-h", "/run/postgresql", "-p", self.port,
                        "-U", user or self.psql_user, "-d", db, "-c", query).strip()

    def prepare_dirs(self):
        self.reused = (self.pgdata / "PG_VERSION").exists()
        if not self.reused:
            shutil.rmtree(self.pgdata, ignore_errors=True)
        for sub in ("config", "data", "run"):
            (self.state_dir / sub).mkdir(exist_ok=True)
            os.chown(self.state_dir / sub, self.uid, self.gid)
        for d in (self.state_dir, self.pgdata.parent):
            os.chown(d, self.uid, self.gid)
        if self.reused:
            os.chown(self.pgdata, self.uid, self.gid)
        return self.reused

    def ensure_client_hba(self):
        """pg_autoctl writes HBA entries for the nodes it knows about, and
        the experiment's client is not one of them.  Adding the line is
        config intent, so it is idempotent and survives a rebuild."""
        line = self.params.get("client_hba")
        if not line:
            return None
        hba = self.pgdata / "pg_hba.conf"
        text = hba.read_text() if hba.exists() else ""
        if line not in text:
            with open(hba, "a") as f:
                f.write(f"\n# picluster: the experiment's workload\n{line}\n")
            os.chown(hba, self.uid, self.gid)
        return line

    def start_autoctl(self):
        self.proc = subprocess.Popen(
            [str(self.pgafbin / "pg_autoctl"), "run", "--pgdata", str(self.pgdata)],
            stdout=open(self.logfile, "ab"), stderr=subprocess.STDOUT,
            user=self.uid, group=self.gid, extra_groups=self.groups, env=self.env())

    def wait_ready(self, timeout, db):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"pg_autoctl exited {self.proc.returncode}; "
                                   f"see {self.logfile}")
            try:
                self.sql("select 1", db=db)
                return
            except Exception as e:              # not up yet
                last = e
                time.sleep(0.3)
        raise TimeoutError(f"{db} not ready: {last}")

    def base_status(self):
        if self.proc is None:
            return {"state": "stopped"}
        if self.proc.poll() is not None:
            return {"state": "exited", "code": self.proc.returncode}
        return {"state": "running", "port": self.port, "pid": self.proc.pid}


class PgafMonitorService(PgafBase):
    """The monitor: a PostgreSQL with the pgautofailover extension, which
    decides the roles of the database nodes."""

    psql_user = "postgres"      # created by pg_autoctl, not by us

    def deploy(self):
        reused = self.prepare_dirs()
        if not reused:
            self.run("pg_autoctl", "create", "monitor", "--pgdata", self.pgdata,
                     "--auth", "trust", "--no-ssl", "--hostname", self.addr,
                     "--pgport", self.port)
        return {"reused_pgdata": reused, "uri": self.uri(),
                "version": self.run("pg_autoctl", "--version").splitlines()[0]}

    def uri(self):
        return (f"postgres://{self.params['monitor_user']}@{self.addr}:{self.port}"
                f"/{self.params['monitor_database']}?sslmode=prefer")

    def start(self):
        self.start_autoctl()
        self.wait_ready(60, self.params["monitor_database"])
        return {}

    def status(self):
        st = self.base_status()
        if st["state"] == "running":
            st["role"] = "monitor"
        return st

    def probe(self):
        """The monitor's own view of the cluster: what it believes each node
        is.  This is the decision maker's opinion, not an observation of the
        nodes themselves - checks compare the two."""
        out = {"role": "monitor"}
        try:
            rows = self.sql("select nodename || ' ' || reportedstate || ' ' || "
                            "goalstate from pgautofailover.node order by nodename",
                            db=self.params["monitor_database"])
            out["nodes"] = [dict(zip(("node", "reported", "goal"), line.split()))
                            for line in rows.splitlines() if line]
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[-300:]}"
        return out


class PgafKeeperService(PgafBase):
    """A database node under pg_auto_failover.  Its role is whatever the
    monitor made it: the adapter reports pg_is_in_recovery(), never sets it."""

    @property
    def psql_user(self):
        return self.params["superuser"]

    def monitor_uri(self):
        svc = self.params["monitor_service"]
        peers = self.peers.get(svc) or {}
        addrs = peers.get("nodes") or {}
        if not addrs:
            raise RuntimeError(f"no address for the monitor service {svc!r}")
        host = sorted(addrs.values())[0]
        return (f"postgres://{self.params['monitor_user']}@{host}:{self.port}"
                f"/{self.params['monitor_database']}?sslmode=prefer")

    def deploy(self):
        reused = self.prepare_dirs()
        if not reused:
            # The monitor decides whether this node becomes single/primary or
            # secondary; pg_autoctl does the initdb or the pg_basebackup.
            self.run("pg_autoctl", "create", "postgres", "--pgdata", self.pgdata,
                     "--auth", "trust", "--no-ssl", "--hostname", self.addr,
                     "--pgport", self.port, "--name", self.node_id,
                     "--dbname", self.params["database"],
                     "--username", self.params["superuser"],
                     "--monitor", self.monitor_uri())
        return {"reused_pgdata": reused, "monitor": self.monitor_uri(),
                "client_hba": self.ensure_client_hba()}

    def start(self):
        self.start_autoctl()
        self.wait_ready(120, self.params["database"])
        return {}

    def status(self):
        st = self.base_status()
        if st["state"] != "running":
            return st
        try:
            recovery = self.sql("select pg_is_in_recovery()", db="postgres")
        except Exception:
            return {**st, "state": "starting"}
        st["role"] = "replica" if recovery == "t" else "primary"
        return st

    def probe(self):
        out = {"role": self.status().get("role")}
        try:
            out["system_identifier"] = self.run(
                "pg_controldata", "-D", self.pgdata).split(
                "Database system identifier:")[1].split("\n")[0].strip()
            out["in_recovery"] = self.sql("select pg_is_in_recovery()", db="postgres")
            out["writable"] = self.sql("select not pg_is_in_recovery() and "
                                       "not current_setting('transaction_read_only')"
                                       "::bool", db="postgres") == "t"
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[-300:]}"
            return out
        if out["role"] == "primary":
            rows = self.sql("select application_name || ' ' || state from "
                            "pg_stat_replication order by 1", db="postgres")
            out["replication"] = [dict(zip(("peer", "state"), line.split()))
                                  for line in rows.splitlines() if line]
            # Who this primary is waiting for before it can acknowledge a
            # commit: a promoted primary whose sync standby is gone is a
            # primary that accepts no writes.
            out["synchronous_standby_names"] = self.sql(
                "select current_setting('synchronous_standby_names')", db="postgres")
        else:
            out["last_replay_lsn"] = self.sql(
                "select coalesce(pg_last_wal_replay_lsn()::text, '-')", db="postgres")
        try:
            mx, cnt = self.sql("select coalesce(max(id), 0) || ' ' || count(*) "
                               "from pic_log", db=self.params["database"]).split()
            out["log"] = {"max_id": int(mx), "count": int(cnt)}
        except Exception as e:
            out["log"] = {"error": str(e)[-200:]}
        return out
