import pathlib
import shutil
import tempfile
import unittest

from controller import fidelity
from controller.model import ROOT, Catalog, ModelError, load_scenario, \
    scenario_actions, scenario_requirements
from tests.util import SMOKE, catalog, write

BASE = """name: s
nodes: {count: 3}
services: {app: {type: fake, artifact: fake-service, placement: {nodes: [n1, n2, n3]}}}
workload: {type: synthetic, duration: 10s}
faults:
  - {at: 5s, action: node.off, target: {role: primary}}
checks:
  - {name: target_processes_gone, mandatory: true}
"""


class ScenarioParse(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.cat = catalog()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def parse(self, text):
        return load_scenario(write(self.tmp / "s.yaml", text), self.cat)

    def test_smoke_parses(self):
        s = load_scenario(SMOKE, self.cat)
        self.assertEqual(s["nodes"]["count"], 3)
        self.assertEqual(s["faults"][0]["at"], 5.0)
        self.assertEqual(s["faults"][0]["target"], {"role": "primary"})
        self.assertEqual(s["workload"]["duration"], 10.0)

    def test_scenarios_name_no_backend_and_no_fault_targets_by_node(self):
        # Placement and initial state may name nodes - that is the scenario's
        # own topology.  Faults may not: what they hit is decided at run time
        # from observed state, or from an earlier fault.
        for path in sorted((ROOT / "scenarios").glob("*.yaml")):
            text = path.read_text()
            with self.subTest(path.name):
                for word in ("backend", "laptop", "pi5", "10.3.14", "/var/", "5432"):
                    self.assertNotIn(word, text)
                sc = load_scenario(path, self.cat)
                for f in sc["faults"]:
                    self.assertNotIn("node", f["target"], f)

    def test_rejections(self):
        bad = {
            "unknown key": BASE + "extra: 1\n",
            "unknown check": BASE.replace("target_processes_gone", "nope"),
            "no mandatory check": BASE.replace("mandatory: true", "mandatory: false"),
            "bad selector": BASE.replace("{role: primary}", "{host: x}"),
            "fault after end": BASE.replace("at: 5s", "at: 11s"),
            "bad duration": BASE.replace("at: 5s", "at: soon"),
            "unknown relaxation": BASE + "fidelity:\n  laptop-nsnode: {allow: [warp]}\n",
            "unknown backend in fidelity": BASE + "fidelity:\n  mars: strict\n",
            "unknown service type": BASE.replace("type: fake", "type: oracle"),
            "placement outside nodes.count": BASE.replace("[n1, n2, n3]", "[n1, n9]"),
            "no placement": BASE.replace(", placement: {nodes: [n1, n2, n3]}", ""),
            "bad role_source": BASE.replace("type: fake,", "type: fake, role_source: magic,"),
            "initial.primary not a member": BASE.replace("type: fake,",
                                                         "type: fake, initial: {primary: n9},"),
        }
        for what, text in bad.items():
            with self.subTest(what), self.assertRaises(ModelError):
                self.parse(text)

    def test_fidelity_policy(self):
        s = self.parse(BASE + "fidelity:\n  laptop-nsnode: {allow: [fast_kill]}\n  pi5: strict\n")
        from controller.model import allowed_relaxations
        self.assertEqual(allowed_relaxations(s, "laptop-nsnode"), ["fast_kill"])
        self.assertEqual(allowed_relaxations(s, "pi5"), [])
        self.assertEqual(allowed_relaxations(s, "laptop-vm"), [])


class BackendCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "backends", self.tmp / "backends")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_four_backends(self):
        cat = catalog()
        self.assertEqual(sorted(cat.backends),
                         ["laptop-docker", "laptop-nsnode", "laptop-vm", "pi5"])
        for name, b in cat.backends.items():
            if b["status"] == "stub":
                self.assertEqual(b["provides"], [], name)

    def test_stub_schema_enforced(self):
        cases = {
            "stub with provides": "name: x\nstatus: stub\nprovides: [processes_gone]\n",
            "stub with implementation": "name: x\nstatus: stub\nimplementation: a:b\n",
            "implemented w/o impl": "name: x\nstatus: implemented\nprovides: []\n",
            "unknown semantics": "name: x\nstatus: stub\nplanned_provides: [telepathy]\n",
            "name != file": "name: y\nstatus: stub\n",
            "unknown key": "name: x\nstatus: stub\nfallback: laptop-nsnode\n",
            "actions as list": "name: x\nstatus: stub\nplanned_actions: [node.off]\n",
            "bad target_state": ("name: x\nstatus: stub\nplanned_actions: "
                                 "{node.off: {target_state: asleep}}\n"),
        }
        for what, text in cases.items():
            d = self.tmp / what.replace(" ", "_")
            write(d / "x.yaml", text)
            with self.subTest(what), self.assertRaises(ModelError):
                catalog(backend_dirs=[d])

    def test_new_stub_is_data_only(self):
        # A new backend appears through a descriptor alone: catalog, portability
        # and stub rejection work with no code change.
        write(self.tmp / "backends" / "laptop-qemu-arm.yaml",
              "name: laptop-qemu-arm\nstatus: stub\nkernel: own\nprovides: []\n"
              "planned_provides: [processes_gone, arm64_native]\n"
              "planned_actions: {node.off: {target_state: available}}\n")
        cat = catalog(backend_dirs=[self.tmp / "backends"])
        s = load_scenario(SMOKE, cat)
        st, _ = fidelity.portability(scenario_requirements(s, cat), scenario_actions(s),
                                     cat.backend("laptop-qemu-arm"))
        self.assertEqual(st, "PLANNED")
        from controller.model import StubBackend
        with self.assertRaisesRegex(StubBackend,
                                    "backend laptop-qemu-arm is declared but not implemented"):
            cat.require_implemented("laptop-qemu-arm")


class NoBackendNamesInCore(unittest.TestCase):
    def test_grep(self):
        names = [p.stem for p in (ROOT / "backends").glob("*.yaml")]
        for py in (ROOT / "controller").glob("*.py"):   # backends/ impls excluded
            text = py.read_text()
            for n in names:
                self.assertNotIn(f'"{n}"', text, f"{py.name} names backend {n}")
                self.assertNotIn(f"'{n}'", text, f"{py.name} names backend {n}")


if __name__ == "__main__":
    unittest.main()


class WorkloadBinding(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.cat = catalog()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_workload_must_match_service(self):
        text = BASE.replace("type: synthetic", "type: sql")
        with self.assertRaisesRegex(ModelError, "exactly one service it can drive"):
            load_scenario(write(self.tmp / "s.yaml", text), self.cat)

    def test_unknown_workload(self):
        text = BASE.replace("type: synthetic", "type: tpcc")
        with self.assertRaises(ModelError):
            load_scenario(write(self.tmp / "s.yaml", text), self.cat)

    def test_scenario_does_not_name_effective_config(self):
        text = (ROOT / "scenarios" / "pg-node-off.yaml").read_text()
        for word in ("postgresql.conf", "primary_conninfo", "pg_hba"):
            self.assertNotIn(word, text)
