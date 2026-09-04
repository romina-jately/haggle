# Vendored verbatim from the Haggle service (api/haggle/beliefs.py). Source of truth
# lives there; this copy keeps the training environment self-contained and
# stdlib-only. Re-copy if the engine changes.
"""Belief state over the buyer's reservation price.

The agent never observes what a buyer would actually pay. It observes a
sequence of offers, and each one is evidence. This module maintains a
posterior over the buyer's reservation price `r_b` using a grid filter.

Why a grid rather than a closed form: the likelihood is not conjugate to
anything convenient, the support is bounded and small (a few hundred
points), and a grid gives us the whole posterior rather than a point
estimate. We need the whole posterior because the acceptance rule below
depends on tail probabilities, not means.

Likelihood model
----------------
Two facts drive it:

  1. A rational buyer never offers above their reservation price. So any
     hypothesis r_b < o_t is dead. This is a hard truncation and it is
     what makes early offers so informative: a single high offer wipes
     out the bottom of the distribution.

  2. Buyers concede toward their reservation price over time. Early
     offers are far below it, later offers approach it. We model the
     ratio o_t / r_b as rising toward 1 with round number, and score a
     hypothesis by how close the observed ratio is to the expected one.

Neither assumption is exotic. Both are standard in the alternating-offers
bargaining literature, and both are testable against logged negotiations,
which is the point of shipping the event log.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def _normal_pdf(x: float, mu: float, sigma: float) -> float:
    z = (x - mu) / sigma
    return math.exp(-0.5 * z * z) / (sigma * math.sqrt(2 * math.pi))


@dataclass
class BuyerBelief:
    """Posterior over the buyer's reservation price."""

    list_price: float
    grid_points: int = 240
    # How far above list a buyer could conceivably be willing to go.
    upper_multiple: float = 1.35
    # Expected fraction of their reservation price a buyer opens at.
    open_ratio: float = 0.62
    # How fast buyers close the gap to their own reservation price.
    concede_rate: float = 0.30
    # Spread of the likelihood. Larger means we trust each offer less.
    noise: float = 0.11

    grid: list[float] = field(default_factory=list)
    post: list[float] = field(default_factory=list)
    observations: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        lo = self.list_price * 0.05
        hi = self.list_price * self.upper_multiple
        step = (hi - lo) / (self.grid_points - 1)
        self.grid = [lo + i * step for i in range(self.grid_points)]
        # Prior: buyers cluster somewhat below list, with a long tail up.
        mu = math.log(self.list_price * 0.85)
        sigma = 0.45
        raw = [
            _normal_pdf(math.log(max(v, 1e-6)), mu, sigma) / max(v, 1e-6)
            for v in self.grid
        ]
        self.post = _normalize(raw)

    # ---------------------------------------------------------------- #

    def expected_ratio(self, round_index: int) -> float:
        """Fraction of their reservation price a buyer offers at round t."""
        gap = 1.0 - self.open_ratio
        closed = 1.0 - math.exp(-self.concede_rate * max(0, round_index - 1))
        return min(0.995, self.open_ratio + gap * closed)

    def observe(self, offer: float, round_index: int) -> None:
        """Fold one buyer offer into the posterior."""
        self.observations.append(offer)
        expected = self.expected_ratio(round_index)
        lik = []
        for r in self.grid:
            if r < offer:
                # A buyer does not offer more than they are willing to pay.
                lik.append(0.0)
                continue
            ratio = offer / r
            lik.append(_normal_pdf(ratio, expected, self.noise))
        post = [p * l for p, l in zip(self.post, lik)]
        total = sum(post)
        # If the evidence is impossible under every hypothesis, keep the
        # prior rather than dividing by zero. Happens when a buyer offers
        # above the top of the grid.
        self.post = _normalize(post) if total > 0 else self.post

    # ---------------------------------------------------------------- #

    @property
    def mean(self) -> float:
        return sum(v * p for v, p in zip(self.grid, self.post))

    def quantile(self, q: float) -> float:
        acc = 0.0
        for v, p in zip(self.grid, self.post):
            acc += p
            if acc >= q:
                return v
        return self.grid[-1]

    def prob_at_least(self, price: float) -> float:
        """P(the buyer would pay at least `price`)."""
        return sum(p for v, p in zip(self.grid, self.post) if v >= price)

    def optimal_price(self, floor: float, ceiling: float) -> tuple[float, float]:
        """The revenue-maximizing ask under the current posterior.

        The posterior is a demand curve: `prob_at_least(v)` is the chance a
        single buyer clears `v`. Expected revenue at `v` is `v * P(v)`, and
        the best ask is the argmax. This is the standard monopoly-pricing
        problem with a Bayesian demand estimate rather than a known one.

        Returns (price, expected_revenue).
        """
        best_v, best_ev = floor, floor * self.prob_at_least(floor)
        for v in self.grid:
            if v < floor or v > ceiling:
                continue
            ev = v * self.prob_at_least(v)
            if ev > best_ev:
                best_v, best_ev = v, ev
        return best_v, best_ev

    def summary(self) -> dict:
        return {
            "mean": round(self.mean, 2),
            "p10": round(self.quantile(0.10), 2),
            "p50": round(self.quantile(0.50), 2),
            "p90": round(self.quantile(0.90), 2),
            "observations": len(self.observations),
        }


def _normalize(xs: list[float]) -> list[float]:
    total = sum(xs)
    if total <= 0:
        n = len(xs)
        return [1.0 / n] * n
    return [x / total for x in xs]
