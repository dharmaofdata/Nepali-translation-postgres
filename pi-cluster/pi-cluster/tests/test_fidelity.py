import unittest

from controller import fidelity
from controller.fidelity import FAIL, NOT_MEANINGFUL, PASS
from controller.model import load_scenario, scenario_actions, scenario_requirements
from controller.selector import resolve
from controller.model import ModelError
from tests.util import SMOKE, catalog


def r(result, mandatory=True):
    return {"result": result, "mandatory": mandatory}


class Capability(unittest.TestCase):
    def setUp(self):
        self.cat = catalog()

    def test_effective_provides_subtracts_breaks(self):
        b = self.cat.backend("laptop-nsnode")
        self.assertIn("peers_see_silence", fidelity.effective_provides(b, [], self.cat))
        eff = fidelity.effective_provides(b, ["fast_kill"], self.cat)
        self.assertNotIn("peers_see_silence", eff)
        self.assertIn("processes_gone", eff)

    def test_missing_semantics(self):
        b = self.cat.backend("laptop-nsnode")
        eff = fidelity.effective_provides(b, [], self.cat)
        self.assertEqual(fidelity.missing_semantics(
            self.cat.checks["target_processes_gone"], eff), [])
        self.assertEqual(fidelity.missing_semantics(
            self.cat.checks["test_requires_hw_timing"], eff), ["hw_timing"])

    def test_portability(self):
        s = load_scenario(SMOKE, self.cat)
        req, act = scenario_requirements(s, self.cat), scenario_actions(s)
        got = {n: fidelity.portability(req, act, b)[0] for n, b in self.cat.backends.items()}
        self.assertEqual(got, {"laptop-nsnode": "SUPPORTED", "laptop-vm": "PLANNED",
                               "laptop-docker": "PLANNED", "pi5": "PLANNED"})
        st, missing = fidelity.portability({"hw_timing"}, set(),
                                           self.cat.backend("laptop-docker"))
        self.assertEqual((st, missing), ("UNSUPPORTED", ["hw_timing"]))


class Verdict(unittest.TestCase):
    def test_rules(self):
        table = [
            ([r(PASS)], "PASS"),
            ([r(PASS), r(NOT_MEANINGFUL)], "INCONCLUSIVE"),
            ([r(NOT_MEANINGFUL), r(FAIL)], "FAIL"),
            ([r(PASS), r(FAIL, mandatory=False)], "PASS"),
            ([r(PASS), r(NOT_MEANINGFUL, mandatory=False)], "PASS"),
            ([r(PASS, mandatory=False)], "INCONCLUSIVE"),     # nothing mandatory
            ([], "INCONCLUSIVE"),
        ]
        for results, want in table:
            with self.subTest(results=results):
                self.assertEqual(fidelity.verdict(results), want)


class Selector(unittest.TestCase):
    OBS = {"n1": {"role": "replica", "available": True},
           "n2": {"role": "primary", "available": True},
           "n3": {"role": "replica", "available": True}}

    def test_role(self):
        self.assertEqual(resolve({"role": "primary"}, self.OBS), "n2")

    def test_unavailable_not_selected(self):
        obs = {**self.OBS, "n2": {"role": "primary", "available": False}}
        with self.assertRaises(ModelError):
            resolve({"role": "primary"}, obs)

    def test_ambiguous_rejected(self):
        with self.assertRaises(ModelError):
            resolve({"role": "replica"}, self.OBS)

    def test_node(self):
        self.assertEqual(resolve({"node": "n3"}, self.OBS), "n3")


if __name__ == "__main__":
    unittest.main()
