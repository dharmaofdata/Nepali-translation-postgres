"""Real PostgreSQL primary + standby on laptop-nsnode: one end-to-end run."""

import unittest

from controller import executor
from tests.util import FIXTURES, catalog, needs_nsnode, needs_postgres
import pathlib
import shutil
import tempfile

SCEN = FIXTURES / "scenarios" / "pg-node-off-short.yaml"


@needs_nsnode
@needs_postgres
class PgNodeOff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        _, _, cls.rec = executor.run(SCEN, "laptop-nsnode", catalog(), cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_1_run_completed(self):
        self.assertEqual(self.rec["run_status"], "completed", self.rec["error"])
        self.assertEqual(self.rec["verdict"], "PASS",
                         [(c["name"], c["result"], c["evidence"]) for c in self.rec["checks"]])

    def test_2_roles_are_observed_not_assumed(self):
        # The adapter reports pg_is_in_recovery(), so a standby that came up
        # as a primary would fail the run rather than be recorded as one.
        probes = self.rec["probes"]["before_fault"]
        self.assertEqual({n: p["role"] for n, p in probes.items()},
                         {"n1": "primary", "n2": "replica", "n3": "replica"})
        self.assertEqual(len(probes["n1"]["replication"]), 2)
        self.assertTrue(all(r["state"] == "streaming" for r in probes["n1"]["replication"]))

    def test_3_real_writes_and_prefix(self):
        acked = self.rec["workload"]["acked"]
        self.assertGreater(acked, 100, "workload did too little to prove anything")
        self.assertFalse(self.rec["workload"]["hung"])
        end = self.rec["probes"]["end"]
        self.assertEqual(sorted(end), ["n2", "n3"])     # primary is gone
        for n, p in end.items():
            log = p["log"]
            self.assertEqual(log["max_id"], log["count"], f"{n}: gap")
            self.assertLessEqual(log["max_id"], acked, f"{n}: ahead of acked")

    def test_4_client_saw_silence_not_a_reset(self):
        ev = [e for e in self.rec["events"] if e["event"] == "workload_stalled"]
        self.assertEqual([e["reason"] for e in ev], ["timeout"], ev)

    def test_5_effective_config_is_rendered_per_node(self):
        eff = self.rec["services"]["db"]["effective"]
        self.assertEqual(sorted(eff), ["n1", "n2", "n3"])
        for n, d in eff.items():
            conf = d["effective_config"]
            self.assertIn("port = 5432", conf)          # same port on every node
            self.assertIn(f"/{n}/disk/run", conf)       # node-local socket dir
            self.assertIn("16.15", d["version"])


if __name__ == "__main__":
    unittest.main()
