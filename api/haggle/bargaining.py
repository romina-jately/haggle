"""The negotiation engine.

Every number a buyer sees is computed here. The language model is handed a
number and asked to write a sentence around it. It never receives the
floor, never receives the belief state, and never decides a price. That
separation is a security property: a reservation price that is not in the
context window cannot be extracted from it, by any prompt.

The engine is a weighted combination of four concession tactics, which is
the structure from Faratin, Sierra and Jennings (1998) on negotiation
decision functions, extended with a belief-driven tactic that the classic
formulation does not have.

    time        concede as the clock runs out
    behaviour   mirror the buyer's own concessions
    scarcity    concede less when other buyers are active
    belief      aim just above where the buyer probably tops out

Each tactic proposes a price in [floor, list]. The engine takes a weighted
mean, then enforces two invariants that keep the agent coherent:

    monotone    never counter above your own previous counter
    dominance   never counter below what is already on the table

Acceptance is not a threshold. It is a comparison of expected values:
take the offer now, or make one more counter and risk the buyer leaving.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .beliefs import BuyerBelief

# beta < 1 concedes late (Boulware), beta > 1 concedes early (Conceder).
# discount is per-round patience: how much of a deal's value the seller
# loses by dragging it out one more round. Style sets both, because a
# posture that concedes slowly but panics on time is incoherent.
STYLES = {
    "firm": {"beta": 0.35, "discount": 0.995},
    "balanced": {"beta": 1.0, "discount": 0.97},
    "eager": {"beta": 2.6, "discount": 0.90},
}

DEFAULT_WEIGHTS = {"time": 0.40, "behaviour": 0.25, "scarcity": 0.10, "belief": 0.25}


@dataclass
class Policy:
    """What the seller sets once, at listing time."""

    list_price: float
    floor: float
    style: str = "balanced"
    deadline_rounds: int = 10
    # Per-round patience. Defaults to the style's own value; set it
    # explicitly to decouple patience from concession shape.
    discount: float | None = None
    # Fraction of the spread the agent opens below list. 0 means open at list.
    opening_concession: float = 0.0
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    competing_buyers: int = 0

    def __post_init__(self) -> None:
        if self.floor <= 0 or self.list_price <= self.floor:
            raise ValueError("need 0 < floor < list_price")
        if self.style not in STYLES:
            raise ValueError(f"unknown style {self.style!r}")
        if self.deadline_rounds < 1:
            raise ValueError("deadline_rounds must be >= 1")
        if self.discount is None:
            self.discount = STYLES[self.style]["discount"]
        if not 0 < self.discount <= 1:
            raise ValueError("discount must be in (0, 1]")
        total = sum(self.weights.values())
        if total <= 0:
            raise ValueError("weights must sum to something positive")
        self.weights = {k: v / total for k, v in self.weights.items()}

    @property
    def beta(self) -> float:
        return STYLES[self.style]["beta"]

    @property
    def spread(self) -> float:
        return self.list_price - self.floor


@dataclass
class Turn:
    round: int
    buyer_offer: float | None
    agent_price: float
    action: str  # counter | accept | answer | walk
    rationale: str
    belief: dict


@dataclass
class Negotiation:
    """One thread with one buyer. Holds all state the engine needs."""

    policy: Policy
    belief: BuyerBelief = field(init=False)
    agent_price: float = field(init=False)
    round: int = 0
    offers: list[float] = field(default_factory=list)
    counters: list[float] = field(default_factory=list)
    status: str = "open"
    turns: list[Turn] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.belief = BuyerBelief(list_price=self.policy.list_price)
        self.agent_price = self.policy.list_price - (
            self.policy.opening_concession * self.policy.spread
        )

    # ----------------------------- tactics ----------------------------- #

    def _time_target(self) -> float:
        """Faratin time-dependent concession.

        alpha rises from 0 to 1 across the deadline; beta shapes the curve.
        The price walks from list down to floor along it.
        """
        t = min(self.round, self.policy.deadline_rounds)
        frac = t / self.policy.deadline_rounds
        alpha = frac ** (1.0 / self.policy.beta)
        return self.policy.list_price - alpha * self.policy.spread

    def _behaviour_target(self) -> float:
        """Relative tit-for-tat. Mirror the buyer's last concession.

        A buyer who moves a lot gets met; a buyer who moves an inch gets an
        inch. With fewer than two offers there is nothing to mirror, so
        this tactic abstains by returning the current price.
        """
        if len(self.offers) < 2:
            return self.agent_price
        moved = self.offers[-1] - self.offers[-2]
        if moved <= 0:
            return self.agent_price
        return self.agent_price - moved

    def _scarcity_target(self) -> float:
        """Other live buyers are leverage. Concede less when they exist."""
        pull = 1.0 / (1.0 + self.policy.competing_buyers)
        return self.policy.floor + (self.agent_price - self.policy.floor) * (
            0.5 + 0.5 * (1 - pull)
        )

    def _belief_target(self) -> float:
        """Aim just under where the buyer probably tops out.

        p75 rather than the mean: overshooting costs a round, undershooting
        costs money permanently.
        """
        return self.belief.quantile(0.75)

    def _blend(self) -> float:
        w = self.policy.weights
        raw = (
            w.get("time", 0) * self._time_target()
            + w.get("behaviour", 0) * self._behaviour_target()
            + w.get("scarcity", 0) * self._scarcity_target()
            + w.get("belief", 0) * self._belief_target()
        )
        # Invariants.
        price = min(raw, self.agent_price)          # monotone non-increasing
        price = max(price, self.policy.floor)        # never below the floor
        if self.offers:
            price = max(price, self.offers[-1] + 1)  # never below the table
        return min(price, self.policy.list_price)

    # --------------------------- acceptance ---------------------------- #

    def _should_accept(self, offer: float, counter: float) -> tuple[bool, str]:
        """Take it now, or hold out for more?

        The comparison is not against our own next counter. If we have
        priced ourselves somewhere the buyer will never reach, that counter
        is worth nothing, and comparing against it would make a stubborn
        agent cave the moment it got ambitious.

        The right benchmark is the best we could still do: the ask that
        maximizes expected revenue under the current posterior, discounted
        by one round of the seller's patience.
        """
        if offer >= counter:
            return True, "offer meets or beats the counter"
        if offer < self.policy.floor:
            return False, "offer is below the floor"

        best_ask, ev = self.belief.optimal_price(
            floor=self.policy.floor, ceiling=self.policy.list_price
        )
        value_next = ev * self.policy.discount
        if offer >= value_next:
            return True, (
                f"best remaining ask {best_ask:.0f} is worth {value_next:.0f} "
                f"in expectation; {offer:.0f} on the table beats it"
            )
        return False, (
            f"best remaining ask {best_ask:.0f} worth {value_next:.0f}, holding"
        )

    def _should_walk(self, offer: float) -> tuple[bool, str]:
        """Close the thread when the buyer almost certainly cannot clear."""
        if self.round < 3:
            return False, ""
        p = self.belief.prob_at_least(self.policy.floor)
        if p < 0.12:
            return True, f"P(clears floor) = {p:.2f}"
        if self.round >= self.policy.deadline_rounds and offer < self.policy.floor:
            return True, "deadline reached below floor"
        return False, ""

    # ------------------------------ step ------------------------------- #

    def step(self, buyer_offer: float | None) -> Turn:
        """Advance one round. `None` means the buyer said something that
        was not an offer, so we answer without moving price."""
        if self.status != "open":
            raise RuntimeError(f"negotiation is {self.status}")

        self.round += 1

        if buyer_offer is None:
            turn = Turn(
                round=self.round,
                buyer_offer=None,
                agent_price=self.agent_price,
                action="answer",
                rationale="no number on the table, holding price",
                belief=self.belief.summary(),
            )
            self.turns.append(turn)
            return turn

        self.offers.append(buyer_offer)
        self.belief.observe(buyer_offer, self.round)

        walk, why_walk = self._should_walk(buyer_offer)
        if walk:
            self.status = "walked"
            turn = Turn(self.round, buyer_offer, self.agent_price, "walk", why_walk, self.belief.summary())
            self.turns.append(turn)
            return turn

        counter = self._blend()
        accept, why = self._should_accept(buyer_offer, counter)

        if accept:
            self.status = "closed"
            self.agent_price = buyer_offer
            turn = Turn(self.round, buyer_offer, buyer_offer, "accept", why, self.belief.summary())
            self.turns.append(turn)
            return turn

        self.agent_price = counter
        self.counters.append(counter)
        turn = Turn(self.round, buyer_offer, counter, "counter", why, self.belief.summary())
        self.turns.append(turn)
        return turn

    # ----------------------------- outcome ----------------------------- #

    @property
    def capture(self) -> float | None:
        """Share of the floor-to-list spread the seller kept.

        This is the quantity worth optimizing. Close rate alone teaches the
        agent to dump at the floor; price alone teaches it never to sell.
        """
        if self.status != "closed":
            return 0.0 if self.status == "walked" else None
        return (self.agent_price - self.policy.floor) / self.policy.spread
