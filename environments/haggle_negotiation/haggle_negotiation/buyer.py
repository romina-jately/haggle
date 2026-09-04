"""The simulated counterparty and the outcome of a negotiation.

The buyer is deterministic on purpose. A negotiation policy has to be scored
against a counterpart whose behaviour is fixed and knowable, or the reward is
noise. The concession model is the same one `beliefs.py` assumes the engine is
inferring against: a buyer opens around `open_ratio` of their reservation
price and closes the gap toward it as rounds pass, and never offers above what
they would actually pay.

The point of separating this from the engine is that the engine never sees the
buyer's reservation price — it infers a posterior over it. Here we *know* it,
because we are the ones simulating the buyer, which is exactly what lets us
compute `reachable` in the reward exactly rather than estimating it.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field


@dataclass
class BuyerProfile:
    reservation: float          # r_b, the most this buyer will pay. Hidden from the agent.
    open_ratio: float = 0.62    # fraction of r_b the buyer opens at
    concede_rate: float = 0.30  # how fast the buyer closes the gap to r_b
    patience: int = 8           # rounds the buyer will haggle before walking

    def willingness(self, round_index: int) -> float:
        """What the buyer would accept at this round. Rises from open_ratio*r_b
        toward r_b, never above it."""
        closed = 1.0 - math.exp(-self.concede_rate * max(0, round_index - 1))
        frac = self.open_ratio + (1.0 - self.open_ratio) * closed
        return min(self.reservation, frac * self.reservation)


@dataclass
class Move:
    """One agent decision, parsed from its message."""
    action: str                 # counter | accept | walk | answer
    price: float | None = None


@dataclass
class Outcome:
    status: str                 # closed | walked | open
    final_price: float | None
    rounds: int
    offers: list[float] = field(default_factory=list)


# --------------------------- parsing the agent ----------------------- #

_JSON = re.compile(r"\{.*\}", re.DOTALL)
_NUM = re.compile(r"\$?\s?(\d{2,6})(?:\.\d+)?")
_ACCEPT = re.compile(r"\b(deal|accept|sold|take it|you got it|agreed)\b", re.IGNORECASE)
_WALK = re.compile(r"\b(can't do|cannot do|no deal|walk away|pass on|too low)\b", re.IGNORECASE)


def parse_move(text: str) -> Move:
    """Read one agent turn into a Move.

    The agent is asked to answer as JSON ({"action","price"}), and we honour
    that when it does. When it does not — a plain sentence — we fall back to the
    same defensive reading the service uses: a number in the text is the counter,
    accept/walk phrases set the action. A message with no number and no signal is
    an `answer` that holds price.
    """
    if not text:
        return Move("answer")
    m = _JSON.search(text)
    if m:
        try:
            obj = json.loads(m.group(0))
            action = str(obj.get("action", "")).lower().strip()
            price = obj.get("price")
            price = float(price) if isinstance(price, (int, float)) else None
            if action in {"counter", "accept", "walk", "answer"}:
                return Move(action, price)
        except (ValueError, TypeError):
            pass
    # fallback: read the sentence
    if _ACCEPT.search(text):
        n = _NUM.search(text)
        return Move("accept", float(n.group(1)) if n else None)
    if _WALK.search(text):
        return Move("walk")
    n = _NUM.search(text)
    if n:
        return Move("counter", float(n.group(1)))
    return Move("answer")


# ----------------------------- the buyer ----------------------------- #

class Buyer:
    """A running conversation, seen from the buyer's side. `opening()` starts it;
    each `respond()` reacts to one agent Move and returns the buyer's message.
    """

    def __init__(self, profile: BuyerProfile, list_price: float):
        self.p = profile
        self.list_price = list_price
        self.round = 0
        self.status = "open"
        self.final_price: float | None = None
        self.offers: list[float] = []

    def opening(self) -> str:
        self.round = 1
        offer = round(self.p.willingness(1))
        self.offers.append(float(offer))
        return f"Hi! I'm interested in this. Would you take ${offer}?"

    def respond(self, move: Move) -> str:
        # The agent takes the buyer's standing offer.
        if move.action == "accept":
            self.status = "closed"
            self.final_price = move.price if move.price is not None else self.offers[-1]
            return "Great — it's a deal. Thanks!"
        if move.action == "walk":
            self.status = "walked"
            return "No worries, thanks anyway."
        if move.action == "answer" or move.price is None:
            # No number moved; the buyer restates their standing offer.
            return f"Okay. I'm still at ${round(self.offers[-1])} though."

        # A counter. Would the buyer take it at this round?
        self.round += 1
        want = self.p.willingness(self.round)
        if move.price <= want:
            self.status = "closed"
            self.final_price = float(move.price)
            return f"Alright, ${round(move.price)} works. Deal."
        if self.round > self.p.patience:
            self.status = "walked"
            return "That's more than I can go to. I'll pass, thanks."
        # Concede upward toward willingness and counter back.
        nxt = round(max(self.offers[-1], want))
        self.offers.append(float(nxt))
        return f"That's a bit high for me — can you do ${nxt}?"

    def outcome(self) -> Outcome:
        return Outcome(self.status, self.final_price, self.round, list(self.offers))


def play(profile: BuyerProfile, list_price: float, moves: list[Move],
         deadline_rounds: int) -> Outcome:
    """Replay a sequence of agent moves against a fresh buyer. Deterministic, so
    the reward can reconstruct an episode's outcome exactly from its trace."""
    buyer = Buyer(profile, list_price)
    buyer.opening()
    for move in moves:
        if buyer.status != "open":
            break
        buyer.respond(move)
        if buyer.round >= deadline_rounds and buyer.status == "open":
            buyer.status = "walked"
            break
    return buyer.outcome()
