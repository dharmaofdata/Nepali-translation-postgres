"""Real PostgreSQL adapter: primary via initdb, standby via pg_basebackup.

No HA: roles come from the controller and are never changed here.  What the
adapter reports as `role` is observed (pg_is_in_recovery), not assumed.

Node-local facts handled here, not by the controller:
  * unix_socket_directories under the node state dir.  The default
    (/var/run/postgresql) is shared by every node on a backend without a
    mount namespace, and all nodes use the same port.
  * the server must not run as root (main.c: "root" execution ... not
    permitted), so binaries run as params.run_as.
"""

import grp
import os
import pwd
import shutil
import subprocess
import time

from .base import Service

INCLUDE = "picluster.conf"


class PostgresService(Service):
    proc = None
    reused = None

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.pgdata = self.state_dir / "pgdata"
        self.rundir = self.state_dir / "run"
        self.logfile = self.state_dir / "postgres.log"
        self.user = self.params["run_as"]
        self.su = self.params["superuser"]
        self.db = self.params["database"]
        pw = pwd.getpwnam(self.user)
        self.uid, self.gid = pw.pw_uid, pw.pw_gid
        self.groups = [g.gr_gid for g in grp.getgrall() if self.user in g.gr_mem]

    # --- helpers ---------------------------------------------------------

    def run(self, *argv, **kw):
        r = subprocess.run([str(self.bindir / argv[0]), *map(str, argv[1:])],
                           capture_output=True, text=True, user=self.uid, group=self.gid,
                           extra_groups=self.groups, env=self.env(), **kw)
        if r.returncode:
            raise RuntimeError(f"{argv[0]}: {(r.stderr or r.stdout).strip()[-500:]}")
        return r.stdout

    def env(self):
        e = dict(os.environ, PGHOST=str(self.rundir), PGPORT=str(self.port),
                 PGUSER=self.su, PGDATABASE=self.db, HOME=str(self.state_dir))
        libdir = self.bindir.parent / "lib"
        if libdir.is_dir():
            e["LD_LIBRARY_PATH"] = str(libdir)
        return e

    def control(self, field):
        for line in self.run("pg_controldata", "-D", self.pgdata).splitlines():
            name, _, value = line.partition(":")
            if name.strip() == field:
                return value.strip()
        return None

    def sql(self, query, db=None):
        return self.run("psql", "-Atq", "-d", db or self.db, "-c", query).strip()

    def chown_tree(self, path):
        os.chown(path, self.uid, self.gid)
        for root, dirs, files in os.walk(path):
            for n in dirs + files:
                os.chown(os.path.join(root, n), self.uid, self.gid)

    # --- lifecycle -------------------------------------------------------

    def deploy(self):
        """Idempotent: an initialized PGDATA is reused, so a node that comes
        back after node.off performs crash recovery on its own data instead
        of being rebuilt.  The socket directory is not persistent state and
        is always recreated."""
        reused = self.reused = (self.pgdata / "PG_VERSION").exists()
        shutil.rmtree(self.rundir, ignore_errors=True)
        self.rundir.mkdir(parents=True)
        if not reused:
            shutil.rmtree(self.pgdata, ignore_errors=True)
            self.pgdata.mkdir(parents=True)
            os.chmod(self.pgdata, 0o700)
        for d in (self.state_dir, self.rundir, self.pgdata):
            os.chown(d, self.uid, self.gid)

        if not reused:
            if self.role == "primary":
                self.run("initdb", "-D", self.pgdata, "-U", self.su, "--auth=trust",
                         "-E", "UTF8", "--no-sync")
            else:
                host = self.peers["primary"]
                self.run("pg_basebackup", "-h", host, "-p", self.port, "-U", self.su,
                         "-D", self.pgdata, "-X", "stream", "-R", "-c", "fast")
            with open(self.pgdata / "pg_hba.conf", "a") as f:
                f.write(f"\nhost all {self.su} 0.0.0.0/0 trust\n"
                        f"host replication {self.su} 0.0.0.0/0 trust\n")
        # Rendered config lives in its own file, included once, so that
        # rendering again on restart cannot append a second copy.
        conf = self.render_conf()
        (self.pgdata / INCLUDE).write_text(conf)
        main = self.pgdata / "postgresql.conf"
        line = f"include '{INCLUDE}'"
        if line not in main.read_text():
            with open(main, "a") as f:
                f.write(f"\n# picluster\n{line}\n")
        self.chown_tree(self.pgdata)
        return {"effective_config": conf, "pgdata": str(self.pgdata),
                "reused_pgdata": reused,
                "version": self.run("postgres", "--version").strip()}

    def render_conf(self):
        guc = dict(self.params["guc"])
        guc["port"] = str(self.port)
        guc["unix_socket_directories"] = f"'{self.rundir}'"
        lines = [f"{k} = {v}" for k, v in sorted(guc.items())]
        if self.role != "primary":
            # pg_basebackup -R already wrote primary_conninfo and standby.signal.
            lines.append(f"# standby of {self.peers['primary']}")
        return "\n".join(lines) + "\n"

    def start(self):
        self.proc = subprocess.Popen(
            [str(self.bindir / "postgres"), "-D", str(self.pgdata)],
            stdout=open(self.logfile, "ab"), stderr=subprocess.STDOUT,
            user=self.uid, group=self.gid, extra_groups=self.groups, env=self.env())
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"postgres exited {self.proc.returncode}; "
                                   f"see {self.logfile}")
            r = subprocess.run([str(self.bindir / "pg_isready"), "-h", str(self.rundir),
                                "-p", str(self.port), "-q"], env=self.env())
            if r.returncode == 0:
                if self.role == "primary":
                    self.sql(f"select 1", db="postgres")
                    if self.sql("select count(*) from pg_database where datname = "
                                f"'{self.db}'", db="postgres") == "0":
                        self.run("createdb", "-O", self.su, self.db)
                return {}
            time.sleep(0.1)
        raise TimeoutError("postgres did not become ready")

    def status(self):
        if self.proc is None:
            return {"state": "stopped"}
        if self.proc.poll() is not None:
            return {"state": "exited", "code": self.proc.returncode}
        try:
            recovery = self.sql("select pg_is_in_recovery()", db="postgres")
        except RuntimeError:
            return {"state": "starting", "port": self.port, "pid": self.proc.pid}
        return {"state": "running", "port": self.port, "pid": self.proc.pid,
                "role": "replica" if recovery == "t" else "primary"}

    def probe(self):
        """Observations checks need: replication state on a primary, replay
        position and visible rows on a standby."""
        out = {"role": self.status().get("role"),
               # system_identifier proves a restarted node came back on the
               # same storage instead of being rebuilt from scratch.
               "system_identifier": self.control("Database system identifier"),
               "postmaster_start": self.sql("select pg_postmaster_start_time()",
                                            db="postgres"),
               "last_checkpoint": self.control("Latest checkpoint location"),
               "reused_pgdata": self.reused}
        if out["role"] == "primary":
            rows = self.sql("select application_name || ' ' || state || ' ' || "
                            "coalesce(sent_lsn::text, '-') from pg_stat_replication "
                            "order by 1", db="postgres")
            out["replication"] = [dict(zip(("peer", "state", "sent_lsn"), line.split()))
                                  for line in rows.splitlines() if line]
        else:
            out["last_replay_lsn"] = self.sql("select coalesce("
                                              "pg_last_wal_replay_lsn()::text, '-')",
                                              db="postgres")
        try:
            # One statement, one snapshot: max and count taken separately
            # disagree while the workload is running, which would make a
            # gap check report gaps that never existed.
            mx, cnt = self.sql("select coalesce(max(id), 0) || ' ' || count(*) "
                               "from pic_log").split()
            out["log"] = {"max_id": int(mx), "count": int(cnt)}
        except RuntimeError as e:
            out["log"] = {"error": str(e)[-200:]}
        return out
