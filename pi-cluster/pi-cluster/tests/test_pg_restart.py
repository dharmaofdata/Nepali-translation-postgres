"""Node lifecycle with real PostgreSQL: node.off, node.on, crash recovery."""

import pathlib
import shutil
import tempfile
import unittest

from controller import executor
from tests.util import FIXTURES, catalog, needs_nsnode, needs_postgres

SCEN = FIXTURES / "scenarios" / "pg-node-restart-short.yaml"


@needs_nsnode
@needs_postgres
class PgNodeRestart(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        _, _, cls.rec = executor.run(SCEN, "laptop-nsnode", catalog(), cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def results(self):
        return {c["name"]: c["result"] for c in self.rec["checks"]}

    def test_1_verdict(self):
        self.assertEqual(self.rec["run_status"], "completed", self.rec["error"])
        self.assertEqual(self.rec["verdict"], "PASS",
                         [(c["name"], c["evidence"]) for c in self.rec["checks"]
                          if c["result"] != "PASS"])

    def test_2_node_died_and_came_back_on_its_own_storage(self):
        ev = [e["event"] for e in self.rec["events"]]
        for a, b in [("fault_injected", "node_returned"),
                     ("node_returned", "service_deployed")]:
            self.assertIn(a, ev)
            self.assertIn(b, ev)
        deploys = [e for e in self.rec["events"] if e["event"] == "service_deployed"]
        first = [e for e in deploys if e["node"] == "n1"][0]
        again = [e for e in deploys if e["node"] == "n1"][-1]
        self.assertFalse(first["reused_state"])
        self.assertTrue(again["reused_state"], "restart rebuilt PGDATA")
        boots = [e for e in self.rec["events"]
                 if e["event"] == "bench_node_powered_on" and e["node"] == "n1"]
        self.assertEqual([e["boot"] for e in boots], [1, 2])

    def test_3_selector_named_no_node(self):
        # node.on targets whatever node.off hit, by fault index.
        faults = self.rec["scenario"]["content"]["faults"]
        self.assertEqual(faults[1]["target"], {"node_of": 1})
        injected = [e for e in self.rec["events"] if e["event"] == "fault_injected"]
        self.assertEqual({e["action"]: e["node"] for e in injected},
                         {"node.off": "n1", "node.on": "n1"})

    def test_4_nothing_of_the_dead_node_stayed_on_the_host(self):
        for name in ("no_ipc_leak", "no_dev_shm_leak"):
            c = next(c for c in self.rec["checks"] if c["name"] == name)
            self.assertEqual(c["result"], "PASS", c["evidence"])
            self.assertTrue(c["evidence"]["after_fault"]["n1"]["holds"])

    def test_5_acked_writes_survived_and_replication_resumed(self):
        acked = self.rec["workload"]["acked"]
        self.assertGreater(acked, 100)
        log = self.rec["probes"]["end"]["n1"]["log"]
        # The id after the last acked one may have committed without being
        # acknowledged; nothing before it may be missing.
        self.assertGreaterEqual(log["max_id"], acked)
        self.assertLessEqual(log["max_id"], acked + 1)
        self.assertEqual(log["max_id"], log["count"])
        self.assertEqual(len(self.rec["probes"]["end"]["n1"]["replication"]), 2)

    def test_6_same_cluster_after_recovery(self):
        before = self.rec["probes"]["before_fault"]
        end = self.rec["probes"]["end"]
        ids = {n: p["system_identifier"] for n, p in end.items()}
        self.assertEqual(len(set(ids.values())), 1, ids)
        self.assertEqual(before["n1"]["system_identifier"], end["n1"]["system_identifier"])
        self.assertNotEqual(before["n1"]["postmaster_start"], end["n1"]["postmaster_start"])

    def test_7_client_resumes_after_reconciling(self):
        ev = [e for e in self.rec["events"]
              if e["event"] in ("node_returned", "workload_stalled",
                                "workload_resumed", "workload_resynced")]
        names = [e["event"] for e in ev]
        self.assertEqual(names[:2], ["workload_stalled", "node_returned"], ev)
        self.assertIn("workload_resumed", names)
        # Writes continued after the restart, not just before the fault.
        stopped = next(e for e in self.rec["events"] if e["event"] == "workload_stopped")
        restart_sec = int(next(e["t"] for e in self.rec["events"]
                               if e["event"] == "node_returned")
                          - next(e["t"] for e in self.rec["events"]
                                 if e["event"] == "workload_started"))
        self.assertTrue(any(n > 0 for n in stopped["per_second"][restart_sec + 2:]),
                        stopped["per_second"])
        # At most one write can be in doubt, so a resync can only find the
        # server one id ahead.
        for r in self.rec["workload"]["resyncs"]:
            self.assertLessEqual(r["server_max"] - r["client_acked"], 1, r)


if __name__ == "__main__":
    unittest.main()
