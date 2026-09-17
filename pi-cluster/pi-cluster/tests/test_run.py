"""End-to-end runs on laptop-nsnode (root only)."""

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from controller import executor
from tests.util import FIXTURES, SMOKE, catalog, needs_nsnode

RECORD_KEYS = {"experiment_id", "git_commit", "scenario", "backend_placement",
               "backend_provides", "allowed_relaxations", "effective_relaxations",
               "instruments", "events", "checks", "verdict"}


@needs_nsnode
class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def assert_clean(self):
        ns = subprocess.run(["ip", "netns", "list"], capture_output=True, text=True).stdout
        self.assertNotIn("pic-", ns)

    def test_smoke_pass_and_record(self):
        _, path, rec = executor.run(SMOKE, "laptop-nsnode", catalog(), self.tmp)
        self.assertTrue(path.exists())
        self.assertLessEqual(RECORD_KEYS, set(json.loads(path.read_text())))
        self.assertEqual((rec["run_status"], rec["verdict"]), ("completed", "PASS"), rec["error"])
        ev = [e["event"] for e in rec["events"]]
        for a, b in [("services_ready", "workload_started"),
                     ("workload_started", "fault_resolved"),
                     ("fault_resolved", "fault_injected"),
                     ("fault_injected", "check_evaluated"),
                     ("check_evaluated", "verdict")]:
            self.assertLess(ev.index(a), ev.index(b), f"{a} before {b}")
        fr = next(e for e in rec["events"] if e["event"] == "fault_resolved")
        ready = next(e for e in rec["events"] if e["event"] == "service_ready")
        self.assertEqual(fr["node"],
                         next(n for n, r in ready["roles"].items() if r == "primary"))
        stall = next(e for e in rec["events"] if e["event"] == "workload_stalled")
        self.assertEqual(stall["reason"], "timeout")
        self.assert_clean()

    def test_not_meaningful_makes_inconclusive(self):
        scen = FIXTURES / "scenarios" / "smoke-hw-timing.yaml"
        _, _, rec = executor.run(scen, "laptop-nsnode", catalog(), self.tmp)
        got = {c["name"]: c["result"] for c in rec["checks"]}
        self.assertEqual(got, {"target_processes_gone": "PASS",
                               "test_requires_hw_timing": "NOT_MEANINGFUL"})
        nm = next(c for c in rec["checks"] if c["name"] == "test_requires_hw_timing")
        self.assertEqual(nm["evidence"], {"missing_semantics": ["hw_timing"]})
        self.assertEqual(rec["verdict"], "INCONCLUSIVE")
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
