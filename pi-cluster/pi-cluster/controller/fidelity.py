"""Check validity and experiment verdict (Developer Guide §29)."""

PASS, FAIL, NOT_MEANINGFUL = "PASS", "FAIL", "NOT_MEANINGFUL"


def effective_provides(backend, effective_relaxations, catalog):
    broken = set()
    for r in effective_relaxations:
        broken |= set(catalog.relaxations[r]["breaks"])
    return set(backend["provides"]) - broken


def missing_semantics(check, eff_provides):
    return sorted(set(check["requires"]) - eff_provides)


def verdict(results):
    mandatory = [r for r in results if r["mandatory"]]
    if not mandatory:
        return "INCONCLUSIVE"
    if any(r["result"] == FAIL for r in mandatory):
        return "FAIL"
    if any(r["result"] == NOT_MEANINGFUL for r in mandatory):
        return "INCONCLUSIVE"
    return "PASS"


def portability(scenario_req, scenario_act, backend):
    """SUPPORTED / PLANNED / UNSUPPORTED from data only."""
    prov, act = set(backend["provides"]), set(backend["actions"])
    if backend["status"] == "implemented" and scenario_req <= prov and scenario_act <= act:
        return "SUPPORTED", []
    pprov = prov | set(backend["planned_provides"])
    pact = act | set(backend["planned_actions"])
    if scenario_req <= pprov and scenario_act <= pact:
        return "PLANNED", []
    missing = sorted(scenario_req - pprov) + sorted("action:" + a for a in scenario_act - pact)
    return "UNSUPPORTED", missing
