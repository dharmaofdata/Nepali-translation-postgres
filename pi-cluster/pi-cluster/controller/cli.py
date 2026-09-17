import argparse
import sys

from . import fidelity
from .executor import prepare, run
from .model import ROOT, Catalog, ModelError, StubBackend, load_scenario, load_yaml, \
    scenario_actions, scenario_requirements

EXIT = {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 3}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="picluster")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "portability", "run"):
        p = sub.add_parser(name)
        p.add_argument("scenario")
        p.add_argument("--checks-dir", action="append", default=[],
                       help="extra check descriptors (tests)")
        if name in ("validate", "run"):
            p.add_argument("--backend",
                           default=load_yaml(ROOT / "configs/defaults.yaml")["default_backend"])
        if name == "run":
            p.add_argument("--experiments", default=str(ROOT / "experiments"))
    a = ap.parse_args(argv)

    try:
        cat = Catalog(check_dirs=a.checks_dir)
        if a.cmd == "validate":
            sc, b, art = prepare(a.scenario, a.backend, cat)
            print(f"OK {sc['name']} on {a.backend}")
            for name, svc in sc["services"].items():
                m = art[svc["artifact"]]
                print(f"  {name:14} {svc['type']:10} roles={svc['role_source']:8} "
                      f"{','.join(svc['members']):12} {m['name']} "
                      f"sha256={m['sha256'][:12]}")
            return 0
        if a.cmd == "portability":
            sc = load_scenario(a.scenario, cat)
            req, act = scenario_requirements(sc, cat), scenario_actions(sc)
            print(f"requires: {sorted(req)}  actions: {sorted(act)}")
            for name, b in sorted(cat.backends.items()):
                st, missing = fidelity.portability(req, act, b)
                print(f"{name:15} {b['status']:12} {st:12} {' '.join(missing)}")
            return 0
        exp_id, path, rec = run(a.scenario, a.backend, cat, a.experiments)
    except StubBackend as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except ModelError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    for c in rec["checks"]:
        print(f"{c['result']:15} {c['name']}{' (mandatory)' if c['mandatory'] else ''}")
    print(f"run_status={rec['run_status']} verdict={rec['verdict']} record={path}")
    if rec["run_status"] != "completed":
        print(f"ERROR: {rec['error']}", file=sys.stderr)
        return 2
    return EXIT[rec["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
