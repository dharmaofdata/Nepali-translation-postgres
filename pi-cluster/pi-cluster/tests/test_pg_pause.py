"""process.pause: the primary stops making progress but stays on the network."""

import pathlib
import shutil
import tempfile
import unittest

from controller import executor
from tests.util import FIXTURES, catalog, needs_nsnode, needs_postgres

SCEN = FIXTURES / "scenarios" / "pg-primary-pause-short.yaml"


@needs_nsnode
@needs_postgres
class PgPrimaryPause(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        _, _, cls.rec = executor.run(SCEN, "laptop-nsnode", catalog(), cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def at(self, event, **match):
        return [e for e in self.rec["events"] if e["event"] == event
                and all(e.get(k) == v for k, v in match.items())]

    def test_1_verdict(self):
        self.assertEqual(self.rec["run_status"], "completed", self.rec["error"])
        self.assertEqual(self.rec["verdict"], "PASS",
                         [(c["name"], c["evidence"]) for c in self.rec["checks"]
                          if c["result"] != "PASS"])

    def test_2_paused_not_killed(self):
        c = next(c for c in self.rec["checks"] if c["name"] == "paused_node_is_not_dead")
        after = c["evidence"]["faults"][0]["after"]
        self.assertTrue(after["peers_see_no_progress"]["holds"])
        self.assertGreater(after["peers_see_no_progress"]["evidence"]["pids"], 0)
        self.assertTrue(after["peers_see_no_progress"]["evidence"]["answers_ping"])
        self.assertFalse(after["processes_gone"]["holds"])

    def test_3_client_saw_no_error_at_all(self):
        # The frozen node's kernel keeps acknowledging, so the connection never
        # breaks: unlike node.off, the client gets no error, just no answer.
        self.assertEqual(self.at("workload_stalled"), [])
        self.assertEqual(self.at("workload_resumed"), [])
        self.assertEqual(self.rec["workload"]["resyncs"], [])

    def test_4_throughput_stopped_for_the_pause(self):
        t0 = self.at("workload_started")[0]["t"]
        pause = self.at("fault_injected", action="process.pause")[0]["t"] - t0
        resume = self.at("fault_injected", action="process.resume")[0]["t"] - t0
        per_second = self.at("workload_stopped")[0]["per_second"]
        window = per_second[int(pause) + 1:int(resume)]
        self.assertTrue(window and all(n == 0 for n in window), per_second)
        self.assertTrue(any(n > 0 for n in per_second[int(resume) + 2:]), per_second)

    def test_5_controller_lost_and_regained_the_node(self):
        # Heartbeats stop while the agent is frozen and resume afterwards on
        # the same connection: no re-registration, no redeploy.
        self.assertTrue(self.at("node_unavailable", node="n1"))
        self.assertTrue(self.at("node_available", node="n1"))
        self.assertTrue(self.at("node_resumed", node="n1"))
        self.assertEqual(self.at("node_returned"), [])
        self.assertEqual(len(self.at("service_deployed", node="n1")), 1)


if __name__ == "__main__":
    unittest.main()
