# Working on Arina

Read `README.md` and `docs/NEGOTIATION.md` first. This file is the build
order and the list of things not to undo.

## The rule

The language model reads English and writes English. It never decides a
price. `bargaining.py` computes every number from the seller's policy and
the posterior in `beliefs.py`.

If you are about to put a price target, a floor, or a belief summary into a
prompt, stop. That is the whole security model: a reservation price that is
never in a context window cannot be extracted from one.

## Invariants covered by tests. Do not break them.

- Counters never go below the floor.
- Counters are monotone non-increasing within a thread.
- Counters never land below the offer already on the table.
- Acceptance compares the offer against the *best remaining ask* under the
  posterior, not against the agent's own next counter. Getting this wrong
  made `firm` close lower than `eager`. See NEGOTIATION.md section 4.
- Style sets both concession shape and patience. Decoupling them made all
  three postures behave identically.

Run `pytest` before and after any change to `bargaining.py` or
`beliefs.py`. 23 tests, no network, under a second.

## Build order

1. **Persist live threads.** `LIVE` in `main.py` is an in-process dict, so a
   restart loses every open negotiation. Rehydrate from the event log:
   replay `turn` events through a fresh `Negotiation` to rebuild the
   posterior. The engine is deterministic, so replay is exact.

2. **Seller dashboard** (`web/`). Listing intake with image upload, the
   policy panel with tactic weight sliders, live threads, and the belief
   state as a chart. The belief posterior over rounds is the most
   interesting thing in the system and nothing currently renders it.

3. **Map inbound WhatsApp to threads.** `main.py:inbound` logs and stops on
   purpose. Buyer phone plus listing implies a thread, but the product
   decision is what happens when one buyer messages about three listings.
   Decide that before writing the routing.

4. **Buyer-side chat** so the flow is testable without a WABA. Same
   endpoints, web transport.

5. **The training environment.** A `verifiers` environment that replays
   logged negotiations and scores a candidate policy on the objective in
   NEGOTIATION.md section 6. Split by time, not at random. Simulated
   counterparties for training, held-out real episodes for evaluation.

6. **The results table.** Capture rate, close rate, latency, and cost per
   negotiation for a small post-trained model against a frontier baseline.
   This table is the point of the project. Everything above is setup.

## Calibration

`beliefs.py` has three constants fit by hand: `open_ratio`, `concede_rate`,
`noise`. They are plausible, not measured. Once there are a few hundred
logged negotiations, fit them to real offer sequences and report the
before-and-after. That is a small, honest, publishable result.

## Style

- Errors say what happened and what to do next. No apologies, no "oops".
- Anything that is a number in the UI is monospaced.
- `seller_view` on `AgentTurn` must be stripped before anything reaches a
  buyer. It carries the rationale and the belief summary.

## Gotchas

- `llm.py` carves JSON out of model output because the model occasionally
  wraps it. Keep that defensive.
- Offer extraction is a regex on purpose. It is the highest-frequency path
  and a number is a number. Replace it with a trained classifier only when
  it is measurably the accuracy bottleneck, and by then the event log has
  the labels.
- Meta requires `image_url` on catalog items. `publish` returns 400 without
  one rather than failing inside Graph.
- Verify the webhook signature. Without it anyone who finds the URL can
  drive a negotiation.
