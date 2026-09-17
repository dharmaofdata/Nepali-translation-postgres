import unittest

from checks.target_processes_gone import evaluate
from tests.util import check_ctx


def obs(holds):
    return {"holds": holds, "evidence": {}}


def ctx(before, after, faulted=("n1",), probes=None, workload=None):
    """before/after: {node: holds} around each node.off"""
    def snap(d):
        return {n: {"processes_gone": obs(h)} for n, h in d.items()}
    nodes = sorted(set(before) | set(after))
    faults = [{"action": "node.off", "node": n,
               "before": snap({m: before.get(m, False) for m in nodes}),
               "after": snap({m: after.get(m, False) for m in nodes})}
              for n in faulted]
    return check_ctx(lambda n, s: obs(after.get(n, False)), nodes, faults,
                        probes or {"before_fault": {}, "end": {}}, workload or {})


class TargetProcessesGone(unittest.TestCase):
    def test_pass(self):
        res, _ = evaluate(ctx({"n1": False, "n2": False}, {"n1": True, "n2": False}))
        self.assertEqual(res, "PASS")

    def test_fail_cases(self):
        cases = {
            "survivor": ({"n1": False, "n2": False}, {"n1": False, "n2": False}, ("n1",)),
            "vacuous: already gone": ({"n1": True, "n2": False},
                                      {"n1": True, "n2": False}, ("n1",)),
            "global kill": ({"n1": False, "n2": False}, {"n1": True, "n2": True}, ("n1",)),
            "no fault executed": ({}, {"n1": False, "n2": False}, ()),
        }
        for what, (before, after, faulted) in cases.items():
            with self.subTest(what):
                res, ev = evaluate(ctx(before, after, faulted))
                self.assertEqual(res, "FAIL", ev)


if __name__ == "__main__":
    unittest.main()


class ReplicationChecks(unittest.TestCase):
    """Unit tests for the PostgreSQL checks: probe contents are opaque to the
    core, so the checks themselves must be tested against synthetic probes."""

    def ctx(self, before_fault, end, acked=100, faulted=("n1",), gone=True):
        nodes = sorted(set(before_fault) | set(end) | set(faulted))
        faults = [{"action": "node.off", "node": n,
                   "before": {m: {"processes_gone": obs(False)} for m in nodes},
                   "after": {m: {"processes_gone": obs(gone if m == n else False)}
                             for m in nodes}} for n in faulted]
        return check_ctx(lambda n, s: obs(gone), nodes, faults,
                            {"before_fault": before_fault, "end": end},
                            {"acked": acked})

    PRIMARY = {"role": "primary",
               "replication": [{"peer": "walreceiver", "state": "streaming"},
                               {"peer": "walreceiver", "state": "streaming"}]}

    def test_streaming_pass(self):
        from checks.replication_streaming import evaluate
        c = self.ctx({"n1": self.PRIMARY, "n2": {"role": "replica"},
                      "n3": {"role": "replica"}}, {})
        self.assertEqual(evaluate(c)[0], "PASS")

    def test_streaming_fail_cases(self):
        from checks.replication_streaming import evaluate
        catchup = {"role": "primary",
                   "replication": [{"peer": "walreceiver", "state": "streaming"},
                                   {"peer": "walreceiver", "state": "catchup"}]}
        cases = {
            "one standby not streaming": {"n1": catchup, "n2": {"role": "replica"},
                                          "n3": {"role": "replica"}},
            "no standby (vacuous)": {"n1": {"role": "primary", "replication": []}},
            "two primaries": {"n1": self.PRIMARY, "n2": self.PRIMARY},
            "no probe": {},
        }
        for what, probes in cases.items():
            with self.subTest(what):
                self.assertEqual(evaluate(self.ctx(probes, {}))[0], "FAIL")

    def test_prefix_pass(self):
        from checks.standby_prefix_consistent import evaluate
        end = {"n2": {"role": "replica", "log": {"max_id": 90, "count": 90}},
               "n3": {"role": "replica", "log": {"max_id": 100, "count": 100}}}
        self.assertEqual(evaluate(self.ctx({}, end))[0], "PASS")

    def test_prefix_fail_cases(self):
        from checks.standby_prefix_consistent import evaluate
        ok = {"role": "replica", "log": {"max_id": 90, "count": 90}}
        cases = {
            "gap": ({"n2": {"role": "replica", "log": {"max_id": 90, "count": 80}}}, 100, True),
            "ahead of acked": ({"n2": {"role": "replica",
                                       "log": {"max_id": 101, "count": 101}}}, 100, True),
            "unreadable": ({"n2": {"role": "replica", "log": {"error": "no table"}}}, 100, True),
            "no standby (vacuous)": ({}, 100, True),
            "no writes (vacuous)": ({"n2": ok}, 0, True),
            "target still running": ({"n2": ok}, 100, False),
        }
        for what, (end, acked, gone) in cases.items():
            with self.subTest(what):
                res, ev = evaluate(self.ctx({}, end, acked=acked, gone=gone))
                self.assertEqual(res, "FAIL", ev)


class DeclarationsAreBinding(unittest.TestCase):
    """A check may only read observations it declared; the executor hands it
    a filtered view, so an undeclared read cannot silently skip a guard."""

    def test_view_is_filtered_to_declared_semantics(self):
        from controller.model import Catalog
        cat = Catalog()
        faults = [{"action": "node.off", "node": "n1",
                   "before": {"n1": {"processes_gone": obs(False),
                                     "private_dev_shm": obs(True)}},
                   "after": {"n1": {"processes_gone": obs(True),
                                    "private_dev_shm": obs(True)}}}]
        full = check_ctx(lambda n, s: obs(True), ["n1"], faults,
                            {"before_fault": {}, "end": {}}, {})
        view = full.for_check(cat.checks["target_processes_gone"])
        self.assertEqual(set(view.faulted[0]["after"]["n1"]), {"processes_gone"})
        view2 = full.for_check(cat.checks["node_returns"])
        self.assertEqual(set(view2.faulted[0]["after"]["n1"]), {"processes_gone"})

    def test_every_observation_a_check_reads_is_declared(self):
        # The guard is only as good as the declaration: a check that reads an
        # undeclared semantics must not pass quietly.
        from checks.node_returns import evaluate
        faults = [{"action": "node.on", "node": "n1", "before": {"n1": {}},
                   "after": {"n1": {}}}]
        ctx = check_ctx(lambda n, s: obs(True), ["n1"], faults,
                           {"before_fault": {}, "end": {"n1": {"role": "primary"}}}, {})
        res, ev = evaluate(ctx)
        self.assertEqual(res, "FAIL", ev)
        self.assertIn("was not observed", " ".join(ev["reasons"]))


class ReplicationExpectedSet(unittest.TestCase):
    """Who is expected to be streaming comes from the inventory and the
    faults, never from the probes."""

    def ctx(self, end, faults, inventory=("n1", "n2", "n3")):
        return check_ctx(lambda n, s: obs(True), list(inventory), faults,
                            {"before_fault": {}, "end": end}, {})

    OFF_ON = [{"action": "node.off", "node": "n1", "before": {}, "after": {}},
              {"action": "node.on", "node": "n1", "before": {}, "after": {}}]

    def primary(self, n):
        return {"role": "primary",
                "replication": [{"peer": "w", "state": "streaming"}] * n}

    def test_dead_standby_cannot_hide(self):
        from checks.replication_streaming_again import evaluate
        end = {"n1": self.primary(1), "n2": {"role": "replica"},
               "n3": {"error": "psql: connection refused"}}
        res, ev = evaluate(self.ctx(end, self.OFF_ON))
        self.assertEqual(res, "FAIL", ev)

    def test_missing_probe_cannot_hide(self):
        from checks.replication_streaming_again import evaluate
        end = {"n1": self.primary(1), "n2": {"role": "replica"}}
        res, ev = evaluate(self.ctx(end, self.OFF_ON))
        self.assertEqual(res, "FAIL", ev)
        self.assertEqual(ev["missing_probe"], ["n3"])

    def test_all_up_and_streaming(self):
        from checks.replication_streaming_again import evaluate
        end = {"n1": self.primary(2), "n2": {"role": "replica"},
               "n3": {"role": "replica"}}
        self.assertEqual(evaluate(self.ctx(end, self.OFF_ON))[0], "PASS")

    def test_node_left_down_is_not_expected(self):
        from checks.replication_streaming_again import evaluate
        faults = [{"action": "node.off", "node": "n3", "before": {}, "after": {}}]
        end = {"n1": self.primary(1), "n2": {"role": "replica"}}
        res, ev = evaluate(self.ctx(end, faults))
        self.assertEqual((res, ev["down_at_end"]), ("PASS", ["n3"]))


class PausedNodeIsNotDead(unittest.TestCase):
    def ctx(self, faults):
        return check_ctx(lambda n, s: obs(True), ["n1", "n2"], faults,
                            {"before_fault": {}, "end": {}}, {})

    def fault(self, action, node, before, after):
        def snap(paused, gone):
            return {node: {"peers_see_no_progress": obs(paused),
                           "processes_gone": obs(gone)}}
        return {"action": action, "node": node, "before": snap(*before),
                "after": snap(*after)}

    def evaluate(self, faults):
        from checks.paused_node_is_not_dead import evaluate
        return evaluate(self.ctx(faults))

    def test_pass(self):
        faults = [self.fault("process.pause", "n1", (False, False), (True, False)),
                  self.fault("process.resume", "n1", (True, False), (False, False))]
        self.assertEqual(self.evaluate(faults)[0], "PASS")

    def test_fail_cases(self):
        cases = {
            "pause killed the node":
                [self.fault("process.pause", "n1", (False, False), (False, True))],
            "pause did nothing":
                [self.fault("process.pause", "n1", (False, False), (False, False))],
            "already paused (vacuous)":
                [self.fault("process.pause", "n1", (True, False), (True, False))],
            "no pause executed":
                [self.fault("node.off", "n1", (False, False), (False, True))],
            "resume left it paused":
                [self.fault("process.pause", "n1", (False, False), (True, False)),
                 self.fault("process.resume", "n1", (True, False), (True, False))],
        }
        for what, faults in cases.items():
            with self.subTest(what):
                res, ev = self.evaluate(faults)
                self.assertEqual(res, "FAIL", ev)


class HaChecks(unittest.TestCase):
    """The HA checks scope themselves to the members of the HA-managed
    service and to who is supposed to be up, never to who answered."""

    SERVICES = {"monitor": {"members": ["n4"], "role_source": "declared",
                            "initial": {"primary": "n4"}},
                "db": {"members": ["n1", "n2", "n3"], "role_source": "observed",
                       "initial": {"primary": "n1"}}}

    def ctx(self, probes, faults=()):
        return check_ctx(lambda n, s: obs(True), ["n1", "n2", "n3", "n4"],
                         list(faults), probes, {"acked": 10}, self.SERVICES)

    def node(self, role, writable):
        return {"role": role, "writable": writable}

    def off(self, node, gone=True):
        return {"action": "node.off", "node": node,
                "before": {node: {"processes_gone": obs(False)}},
                "after": {node: {"processes_gone": obs(gone)}}}

    def on(self, node):
        return {"action": "node.on", "node": node,
                "before": {node: {"processes_gone": obs(True)}},
                "after": {node: {"processes_gone": obs(False)}}}

    HEALTHY = {"n1": {"role": "primary", "writable": True},
               "n2": {"role": "replica", "writable": False},
               "n3": {"role": "replica", "writable": False}}
    AFTER = {"n1": {"role": "replica", "writable": False},
             "n2": {"role": "primary", "writable": True},
             "n3": {"role": "replica", "writable": False},
             "n4": {"role": "monitor", "nodes": [{"node": "n1", "reported": "secondary"},
                                                 {"node": "n2", "reported": "primary"},
                                                 {"node": "n3", "reported": "secondary"}]}}

    def test_never_two_writable(self):
        from checks.never_two_writable import evaluate
        good = self.ctx({"before_fault": self.HEALTHY, "end": self.AFTER})
        self.assertEqual(evaluate(good)[0], "PASS")
        split = dict(self.AFTER, n1={"role": "primary", "writable": True})
        bad = self.ctx({"before_fault": self.HEALTHY, "end": split})
        res, ev = evaluate(bad)
        self.assertEqual(res, "FAIL", ev)
        # A promoted primary that is not writable yet is not a violation.
        nobody = {"n1": {}, "n2": {"role": "primary", "writable": False},
                  "n3": {"role": "replica", "writable": False}}
        self.assertEqual(evaluate(self.ctx({"before_fault": self.HEALTHY,
                                            "after_fault_1": nobody}))[0], "PASS")
        # One phase cannot show a failover.
        self.assertEqual(evaluate(self.ctx({"end": self.AFTER}))[0], "FAIL")

    def test_exactly_one_primary_at_the_end(self):
        from checks.exactly_one_primary import evaluate
        self.assertEqual(evaluate(self.ctx({"end": self.AFTER},
                                           [self.off("n1"), self.on("n1")]))[0], "PASS")
        # A member that should be up but did not answer is a failure here.
        missing = {k: v for k, v in self.AFTER.items() if k != "n3"}
        res, ev = evaluate(self.ctx({"end": missing}, [self.off("n1"), self.on("n1")]))
        self.assertEqual((res, ev["unreachable"]), ("FAIL", ["n3"]), ev)
        # A member left down is not expected to answer.
        left_down = {k: v for k, v in self.AFTER.items() if k != "n1"}
        self.assertEqual(evaluate(self.ctx({"end": left_down}, [self.off("n1")]))[0],
                         "PASS")

    def test_failover_promoted_survivor(self):
        from checks.failover_promoted_survivor import evaluate
        good = self.ctx({"before_fault": self.HEALTHY, "end": self.AFTER}, [self.off("n1")])
        self.assertEqual(evaluate(good)[0], "PASS")
        cases = {
            "nothing moved": ({"before_fault": self.HEALTHY, "end": self.HEALTHY},
                              [self.off("n1")]),
            "the node never died": ({"before_fault": self.HEALTHY, "end": self.AFTER},
                                    [self.off("n1", gone=False)]),
            "killed a replica (vacuous)": ({"before_fault": self.HEALTHY,
                                            "end": self.AFTER}, [self.off("n2")]),
            "no node.off": ({"before_fault": self.HEALTHY, "end": self.AFTER}, []),
        }
        for what, (probes, faults) in cases.items():
            with self.subTest(what):
                self.assertEqual(evaluate(self.ctx(probes, faults))[0], "FAIL")

    def test_old_primary_returns_as_replica(self):
        from checks.old_primary_returns_as_replica import evaluate
        faults = [self.off("n1"), self.on("n1")]
        good = self.ctx({"before_fault": self.HEALTHY, "end": self.AFTER}, faults)
        self.assertEqual(evaluate(good)[0], "PASS")
        for what, n1 in {
                "came back as primary": {"role": "primary", "writable": True},
                # The two are read separately, so they can disagree; a member
                # that says replica while it accepts writes is the dangerous
                # one, and the role alone would not catch it.
                "replica that accepts writes": {"role": "replica", "writable": True},
        }.items():
            with self.subTest(what):
                res, ev = evaluate(self.ctx(
                    {"before_fault": self.HEALTHY, "end": dict(self.AFTER, n1=n1)},
                    faults))
                self.assertEqual(res, "FAIL", ev)
        # The decision maker still calling it a primary is a disagreement
        # worth failing on, even when the node itself says replica.
        stale = dict(self.AFTER)
        stale["n4"] = {"role": "monitor",
                       "nodes": [{"node": "n1", "reported": "primary"}]}
        self.assertEqual(evaluate(self.ctx({"before_fault": self.HEALTHY,
                                            "end": stale}, faults))[0], "FAIL")
