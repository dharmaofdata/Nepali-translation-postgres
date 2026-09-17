"""pg_auto_failover: the first HA mechanism.  The monitor decides roles; the
executor only keeps membership."""

import pathlib
import shutil
import tempfile
import unittest

from controller import executor
from tests.util import FIXTURES, catalog, needs_nsnode, needs_postgres

SCEN = FIXTURES / "scenarios" / "pgaf-primary-loss-short.yaml"


@needs_nsnode
@needs_postgres
class PgafPrimaryLoss(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        _, _, cls.rec = executor.run(SCEN, "laptop-nsnode", catalog(), cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def events(self, name, **match):
        return [e for e in self.rec["events"] if e["event"] == name
                and all(e.get(k) == v for k, v in match.items())]

    def test_1_verdict(self):
        self.assertEqual(self.rec["run_status"], "completed", self.rec["error"])
        self.assertEqual(self.rec["verdict"], "PASS",
                         [(c["name"], c["evidence"]) for c in self.rec["checks"]
                          if c["result"] not in ("PASS",)])

    def test_2_executor_never_restored_a_role(self):
        # The role a returning member gets back is not one the scenario owns.
        returned = self.events("node_returned")
        self.assertEqual([e["restoring"] for e in returned], ["member"])
        self.assertEqual([e["role"] for e in self.events("service_deployed")
                          if e["node"] == returned[0]["node"]][-1], "member")

    def test_3_the_role_moved_and_the_scenario_named_nobody(self):
        before = self.rec["probes"]["before_fault"]
        end = self.rec["probes"]["end"]
        was = [n for n, p in before.items() if p.get("writable") is True]
        now = [n for n, p in end.items() if p.get("writable") is True]
        self.assertEqual(len(was), 1)
        self.assertEqual(len(now), 1)
        self.assertNotEqual(was, now, "no failover happened")
        killed = self.events("fault_injected", action="node.off")[0]["node"]
        self.assertEqual(was, [killed])
        # The scenario said {role: primary} and {node_of: 1}, never a node.
        faults = self.rec["scenario"]["content"]["faults"]
        self.assertEqual([f["target"] for f in faults],
                         [{"role": "primary"}, {"node_of": 1}])

    def test_4_old_primary_came_back_not_writable(self):
        killed = self.events("fault_injected", action="node.off")[0]["node"]
        p = self.rec["probes"]["end"][killed]
        self.assertEqual(p["role"], "replica")
        self.assertFalse(p["writable"])

    def test_5_monitor_is_a_service_on_a_node_of_its_own(self):
        svc = self.rec["services"]
        self.assertEqual(svc["monitor"]["members"], ["n4"])
        self.assertEqual(svc["monitor"]["role_source"], "declared")
        self.assertEqual(svc["db"]["role_source"], "observed")
        self.assertEqual(svc["db"]["members"], ["n1", "n2", "n3"])
        # The monitor's view and the nodes' own view agree at the end.
        monitor = self.rec["probes"]["end"]["n4"]["nodes"]
        roles = {e["node"]: e["reported"] for e in monitor}
        for n in svc["db"]["members"]:
            observed = self.rec["probes"]["end"][n]["role"]
            self.assertEqual(roles[n], "primary" if observed == "primary" else "secondary",
                             f"{n}: monitor says {roles[n]}, node reports {observed}")

    def test_6_write_outage_is_measured(self):
        # Not a check: the length of the outage is a measurement, and the
        # numbers belong in the record where they can be compared later.
        t0 = self.events("workload_started")[0]["t"]
        killed_at = self.events("fault_injected", action="node.off")[0]["t"] - t0
        per_second = self.events("workload_stopped")[0]["per_second"]
        after = per_second[int(killed_at) + 1:]
        self.assertIn(0, after, "the workload never stopped; nothing was lost?")
        resumed = next((i for i, n in enumerate(after) if n > 0 and i > 0), None)
        self.assertIsNotNone(resumed, f"writes never resumed: {per_second}")
        self.assertGreater(self.rec["workload"]["acked"], 1000)
