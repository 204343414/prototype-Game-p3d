"""Evidence-bounded fight::Sequencer child selection.

This module models only the two-pass selector at prototypeenginef.dll
0x10A7E250.  Candidate eligibility/weight probes remain injected because their
concrete bank/node implementations are not all decoded yet.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Generic, Iterable, Optional, TypeVar

T = TypeVar("T")

@dataclass(frozen=True)
class ProbeResult(Generic[T]):
    candidate: Optional[T]
    weight: float = 0.0

Probe = Callable[[object, int, bool], ProbeResult[T]]

@dataclass(frozen=True)
class SelectionTrace(Generic[T]):
    selected: Optional[T]
    mode: str
    total_positive_weight: float
    random_threshold: Optional[float]
    first_pass: tuple[ProbeResult[T], ...]
    second_pass: tuple[ProbeResult[T], ...]


def select_child(probes: Iterable[Probe[T]], branch: object, priority: int,
                 random_unit: float) -> SelectionTrace[T]:
    """Reproduce the demonstrated 0x10A7E250 selection control flow.

    `random_unit` substitutes for the engine RNG call and must be in [0, 1).
    A returned candidate with non-positive weight wins immediately in source
    order. Otherwise positive weights are summed and a second probe pass makes
    a weighted choice. No claim is made here about how a bank computes its
    candidate or weight.
    """
    if not 0.0 <= random_unit < 1.0:
        raise ValueError("random_unit must be in [0, 1)")
    ps = tuple(probes)
    first: list[ProbeResult[T]] = []
    total = 0.0
    for probe in ps:
        r = probe(branch, priority, True)
        first.append(r)
        if r.candidate is None:
            continue
        if r.weight <= 0.0:
            return SelectionTrace(r.candidate, "immediate-nonpositive-weight",
                                  total, None, tuple(first), ())
        total += r.weight
    if total <= 0.0:
        return SelectionTrace(None, "none", total, None, tuple(first), ())

    threshold = random_unit * total
    cumulative = 0.0
    second: list[ProbeResult[T]] = []
    for probe in ps:
        r = probe(branch, priority, True)
        second.append(r)
        if r.candidate is None or r.weight <= 0.0:
            continue
        cumulative += r.weight
        if cumulative >= threshold:
            return SelectionTrace(r.candidate, "positive-weight-lottery", total,
                                  threshold, tuple(first), tuple(second))
    return SelectionTrace(None, "positive-weight-fallthrough", total, threshold,
                          tuple(first), tuple(second))
