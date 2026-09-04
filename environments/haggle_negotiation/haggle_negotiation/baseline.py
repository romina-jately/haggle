"""The engine baseline.

Every episode also runs the deterministic `bargaining.py` engine against the
same buyer, so each row reports what the hand-built Bayesian policy would have
captured on that exact counterpart. That is the comparison the results table
(build order item 6) is made of: a trained model in the negotiator seat versus
the engine that the production system actually uses — measured head to head,
buyer by buyer, on the identical objective.
"""

from __future__ import annotations

from .bargaining import Negotiation, Policy
from .buyer import Buyer, BuyerProfile, Move, Outcome


def engine_outcome(profile: BuyerProfile, policy: Policy,
                   deadline_rounds: int) -> Outcome:
    """Run the engine as the seller's agent against `profile`'s buyer.

    The engine reads the buyer's offer, returns a counter (or accepts/walks),
    the buyer reacts, and its next offer feeds the next step — the same loop the
    live service runs, against the simulated counterpart used for training.
    """
    n = Negotiation(policy=policy)
    buyer = Buyer(profile, policy.list_price)
    buyer.opening()
    offer = buyer.offers[-1]

    while buyer.status == "open" and n.status == "open":
        turn = n.step(offer)
        price = None if turn.action in ("answer", "walk") else turn.agent_price
        buyer.respond(Move(turn.action, price))
        if buyer.status != "open":
            break
        if buyer.round >= deadline_rounds:
            buyer.status = "walked"
            break
        offer = buyer.offers[-1]

    return buyer.outcome()
