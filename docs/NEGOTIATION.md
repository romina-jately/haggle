# How the negotiation agent works

The short version: a language model reads and writes English. It never
decides a price. Every number a buyer sees comes out of `bargaining.py`,
computed from the seller's policy and a posterior over what the buyer will
pay.

This is not a stylistic preference. A reservation price that is never in a
context window cannot be extracted from one, by any prompt, from any buyer,
in any language. If you move pricing into the model you lose that property
and gain nothing.

---

## 1. What the seller sets

| Field | Meaning |
| --- | --- |
| `list_price` | The ask |
| `floor` | Never go below. Never leaves the server. |
| `style` | `firm`, `balanced`, `eager`. Sets both concession shape and patience. |
| `deadline_rounds` | When the time tactic reaches the floor |
| `discount` | Per-round patience. Defaults from style. |
| `weights` | How much each tactic counts |

Style setting both concession shape and patience is deliberate. A posture
that concedes slowly but panics on time is incoherent, and an early version
of this engine had exactly that bug: all three styles closed at the same
price because acceptance ignored style entirely.

---

## 2. Four tactics, weighted

The structure is from Faratin, Sierra and Jennings (1998), *Negotiation
decision functions for autonomous agents*, extended with a belief-driven
tactic the original does not have. Each tactic proposes a price in
`[floor, list]`; the engine takes a weighted mean.

### Time

```
alpha(t) = (t / deadline) ** (1 / beta)
price    = list - alpha(t) * (list - floor)
```

`beta < 1` is Boulware: hold near list, concede late. `beta > 1` is
Conceder: give ground early. `firm` is 0.35, `balanced` is 1.0 (linear),
`eager` is 2.6.

### Behaviour

Relative tit-for-tat. Mirror the size of the buyer's last concession. A
buyer who jumps $50 gets met; a buyer who moves $2 gets $2. With fewer
than two offers there is nothing to mirror, so the tactic abstains rather
than guessing.

### Scarcity

Other live buyers on the same listing are leverage. More competing threads
means less concession. This is the one tactic that reaches outside a single
conversation, and it is why the agent is worth more than a per-thread
chatbot: it knows what else is happening.

### Belief

Aim at the 75th percentile of the posterior over the buyer's reservation
price. Not the mean: overshooting costs a round, undershooting costs money
permanently, so the asymmetry belongs in the quantile.

### Invariants

After blending, two rules hold unconditionally:

- **Monotone.** Never counter above your own previous counter. An agent
  that walks its price back up is not negotiating, it is malfunctioning.
- **Dominance.** Never counter below what is already on the table.

Both are enforced in `_blend` and both are covered by tests.

---

## 3. The belief filter

`beliefs.py` maintains a posterior over the buyer's reservation price
`r_b` on a grid of a few hundred points. Grid rather than closed form
because the likelihood is not conjugate to anything convenient and because
the acceptance rule needs tail probabilities, not a point estimate.

Two assumptions drive the likelihood:

1. **A buyer never offers above their reservation price.** So any
   hypothesis below the observed offer dies. This is a hard truncation and
   it is why a single high offer is so informative: it wipes out the entire
   bottom of the distribution at once.

2. **Buyers concede toward their reservation price over time.** The ratio
   `offer / r_b` is modeled as rising from about 0.62 toward 1 as rounds
   pass. A hypothesis is scored by how close the observed ratio is to the
   expected one.

Both assumptions are standard in the alternating-offers literature and both
are testable against logged negotiations, which is the point of shipping the
event log rather than just the agent.

Constants worth calibrating on real data rather than trusting: `open_ratio`,
`concede_rate`, and `noise`.

---

## 4. Acceptance is an optimal-stopping problem

Not a threshold. The question is whether the offer on the table beats what
the agent could still get.

```
value_now  = buyer_offer
value_next = max over asks v in [floor, list] of ( v * P(r_b >= v) ) * discount
accept if value_now >= value_next
```

The inner maximization is monopoly pricing with a Bayesian demand estimate:
the posterior *is* a demand curve, `P(r_b >= v)` is the chance a single
buyer clears `v`, and expected revenue is `v * P(v)`.

The first version of this compared the offer against the agent's own next
counter, which was wrong in an instructive way. An agent that had priced
itself somewhere unreachable judged its own continuation worthless and
caved immediately, so `firm` closed *lower* than `eager`. The test suite
caught it. The benchmark has to be the best price still available, not the
price you happen to be asking.

---

## 5. Walking away

After round three, if `P(r_b >= floor) < 0.12`, close the thread warmly.
Also close at the deadline if the standing offer is below the floor.

Walking is a real outcome with a real cost, which is why it is in the
reward rather than treated as a non-event.

---

## 6. What to train on

The event log is the training set. `close` labels outcome, `walk` labels
failure, `override` labels seller judgment.

The obvious reward is **close rate**. Train on it and the agent learns to
take the first offer above the floor every time. It closes everything and
costs the seller money.

The opposite is **price achieved**. Train on it and the agent never
concedes, holds at list, and closes nothing.

Neither is wrong exactly. They are each half a reward, and either alone
produces a confidently broken agent. The usable objective scores the whole
episode:

```
capture = (final_price - floor) / (list_price - floor)
reward  = closed * capture - walk_penalty * reachable
```

`reachable` asks whether a better policy could have closed that buyer at
all, estimated from the posterior. Without it the agent gets punished for
buyers who were never going to pay, which teaches it to chase people down.

**Reported, never trained on:** gross merchandise value. It is what the
business cares about and it is confounded by listing quality, category,
season, and price level. A model optimizing GMV learns to push sellers
toward cheap fast sales. Keep it on the dashboard at weight zero.

**Overrides** are not a separate reward term. They are corrections to an
episode's label. If the seller held firm and the deal still closed higher,
the policy was too eager for that segment, and the update belongs in the
concession parameters for that segment rather than in the objective.

---

## 7. Where the model actually is

```
seller notes ──► llm.read_listing ──► structured fields ──► catalog
buyer text ───► regex offer parse ──► bargaining.step ──► action + number
                                              │
                                              ▼
                            llm.write_message(action, number) ──► sentence
```

Offer extraction is a regex, not a model call. It is the highest-frequency
path in the system and a number is a number. When it becomes the accuracy
bottleneck it should become a small trained classifier, and by then the
event log will have the labels to train it.
