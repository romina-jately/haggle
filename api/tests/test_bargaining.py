import pytest

from haggle.bargaining import Negotiation, Policy
from haggle.beliefs import BuyerBelief


def pol(**kw):
    base = dict(list_price=240, floor=160, style="balanced")
    base.update(kw)
    return Policy(**base)


# ------------------------------- policy ------------------------------- #

def test_policy_rejects_floor_above_list():
    with pytest.raises(ValueError):
        Policy(list_price=100, floor=120)


def test_weights_are_normalized():
    p = pol(weights={"time": 2, "belief": 2})
    assert sum(p.weights.values()) == pytest.approx(1.0)


def test_unknown_style_rejected():
    with pytest.raises(ValueError):
        pol(style="aggressive")


# ------------------------------ invariants ---------------------------- #

def test_counter_never_below_floor():
    n = Negotiation(policy=pol(deadline_rounds=3))
    for offer in (60, 70, 80, 90, 95):
        if n.status != "open":
            break
        t = n.step(offer)
        assert t.agent_price >= n.policy.floor


def test_counters_are_monotone_non_increasing():
    n = Negotiation(policy=pol())
    prices = []
    for offer in (120, 140, 155, 165):
        if n.status != "open":
            break
        prices.append(n.step(offer).agent_price)
    counters = [p for p, t in zip(prices, n.turns) if t.action == "counter"]
    assert counters == sorted(counters, reverse=True)


def test_counter_never_below_the_table():
    n = Negotiation(policy=pol())
    t = n.step(200)
    if t.action == "counter":
        assert t.agent_price > 200


def test_stepping_a_finished_negotiation_raises():
    n = Negotiation(policy=pol())
    n.step(240)
    assert n.status == "closed"
    with pytest.raises(RuntimeError):
        n.step(250)


# ------------------------------- styles ------------------------------- #

def test_firm_concedes_less_than_eager():
    seq = (150, 158, 166, 172)

    def run(style):
        n = Negotiation(policy=pol(style=style, weights={"time": 1.0}))
        last = None
        for o in seq:
            if n.status != "open":
                break
            last = n.step(o).agent_price
        return last

    assert run("firm") > run("eager")


def test_time_pressure_moves_price_toward_floor():
    n = Negotiation(policy=pol(style="balanced", deadline_rounds=6, weights={"time": 1.0}))
    early = n.step(100).agent_price
    for o in (110, 120, 130, 140):
        if n.status != "open":
            break
        n.step(o)
    assert n.agent_price < early


# ------------------------------ behaviour ----------------------------- #

def test_behaviour_tactic_mirrors_a_big_concession():
    n = Negotiation(policy=pol(weights={"behaviour": 1.0}))
    n.step(120)
    before = n.agent_price
    n.step(170)  # buyer jumped 50
    assert before - n.agent_price > 20


def test_behaviour_tactic_holds_against_a_stubborn_buyer():
    n = Negotiation(policy=pol(weights={"behaviour": 1.0}))
    n.step(150)
    before = n.agent_price
    n.step(150)  # buyer did not move
    assert n.agent_price == pytest.approx(before)


def test_competing_buyers_make_the_agent_hold_higher():
    def run(competitors):
        n = Negotiation(policy=pol(weights={"scarcity": 1.0}, competing_buyers=competitors))
        for o in (140, 150, 158):
            if n.status != "open":
                break
            n.step(o)
        return n.agent_price

    assert run(4) > run(0)


# ------------------------------ acceptance ---------------------------- #

def test_accepts_an_offer_at_or_above_list():
    n = Negotiation(policy=pol())
    t = n.step(240)
    assert t.action == "accept"
    assert n.capture == pytest.approx(1.0)


def test_never_accepts_below_floor():
    n = Negotiation(policy=pol())
    for o in (100, 120, 140, 155, 159):
        if n.status != "open":
            break
        t = n.step(o)
        assert t.action != "accept"


def test_impatience_makes_the_agent_close_sooner():
    def run(discount):
        n = Negotiation(policy=pol(discount=discount, deadline_rounds=12))
        rounds = 0
        for o in (150, 162, 170, 176, 180, 184, 187):
            if n.status != "open":
                break
            n.step(o)
            rounds += 1
        return n.status, rounds

    patient_status, patient_rounds = run(0.995)
    hasty_status, hasty_rounds = run(0.80)
    assert hasty_rounds <= patient_rounds


def test_walks_away_from_a_buyer_who_cannot_clear_the_floor():
    n = Negotiation(policy=pol())
    for o in (40, 44, 47, 49, 50, 51):
        if n.status != "open":
            break
        n.step(o)
    assert n.status == "walked"
    assert n.capture == 0.0


# ------------------------------- beliefs ------------------------------ #

def test_offer_truncates_hypotheses_below_it():
    b = BuyerBelief(list_price=240)
    b.observe(200, round_index=1)
    assert b.prob_at_least(200) == pytest.approx(1.0, abs=1e-9)


def test_posterior_rises_with_a_higher_opening_offer():
    low = BuyerBelief(list_price=240)
    low.observe(120, 1)
    high = BuyerBelief(list_price=240)
    high.observe(200, 1)
    assert high.mean > low.mean


def test_posterior_narrows_as_offers_accumulate():
    b = BuyerBelief(list_price=240)
    b.observe(150, 1)
    wide = b.quantile(0.9) - b.quantile(0.1)
    for i, o in enumerate((165, 178, 186, 191), start=2):
        b.observe(o, i)
    assert b.quantile(0.9) - b.quantile(0.1) < wide


def test_posterior_stays_valid_when_evidence_is_impossible():
    b = BuyerBelief(list_price=240)
    b.observe(10_000, 1)  # above the top of the grid
    assert sum(b.post) == pytest.approx(1.0)


def test_expected_ratio_rises_toward_one():
    b = BuyerBelief(list_price=240)
    ratios = [b.expected_ratio(t) for t in range(1, 12)]
    assert ratios == sorted(ratios)
    assert ratios[-1] < 1.0


# ------------------------------ end to end ---------------------------- #

def test_a_normal_negotiation_closes_inside_the_spread():
    n = Negotiation(policy=pol())
    for o in (150, 168, 180, 190, 200, 210):
        if n.status != "open":
            break
        n.step(o)
    assert n.status == "closed"
    assert 0.0 <= n.capture <= 1.0
    assert n.policy.floor <= n.agent_price <= n.policy.list_price


def test_non_offer_message_does_not_move_price():
    n = Negotiation(policy=pol())
    n.step(150)
    before = n.agent_price
    t = n.step(None)
    assert t.action == "answer"
    assert n.agent_price == before
