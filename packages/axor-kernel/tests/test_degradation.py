from axor_kernel.degradation import Level, compute_level
from axor_kernel.events import Fact


def _fact(fid: str, sev: int) -> Fact:
    return Fact(fact_id=fid, fact_type="denial", severity=sev)


def _attest(fid: str, covers: tuple[str, ...], revokes: str | None = None) -> Fact:
    return Fact(fact_id=fid, fact_type="operator_attestation", severity=0,
                covers=covers, revokes=revokes, operator="op", reason="r", sig="s")


def test_level_is_max_uncovered() -> None:
    facts = {f.fact_id: f for f in [_fact("f1", 1), _fact("f2", 2)]}
    assert compute_level(facts) is Level.RESTRICTED


def test_attestation_descends_level() -> None:
    facts = {f.fact_id: f for f in [_fact("f1", 2), _attest("a1", ("f1",))]}
    assert compute_level(facts) is Level.NORMAL


def test_partial_coverage_partial_descent() -> None:
    facts = {f.fact_id: f for f in [_fact("f1", 2), _fact("f2", 1), _attest("a1", ("f1",))]}
    assert compute_level(facts) is Level.CAUTIOUS


def test_revocation_restores_level() -> None:
    facts = {f.fact_id: f for f in [
        _fact("f1", 2), _attest("a1", ("f1",)), _attest("a2", (), revokes="a1")]}
    assert compute_level(facts) is Level.RESTRICTED


def test_stopped_is_absorbing() -> None:
    from axor_kernel.state import DesiredState
    s = DesiredState(version=1, stopped=True)
    later = DesiredState(version=2, paused=True)
    merged = s.merge(later)
    assert merged.stopped and not merged.paused and merged.version == 2
