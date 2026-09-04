# Results

Build order item 6 — the table the rest of the project is setup for. It scores
a model in the seller's negotiating seat against the deterministic engine, on
the objective from [`NEGOTIATION.md`](NEGOTIATION.md) section 6, using the
`verifiers` environment in
[`environments/arina_negotiation`](../environments/arina_negotiation).

## Setup

- 32 held-out buyers from the environment's eval split. The buyers are
  deterministic (seed 0), so **every policy faced the identical 32 counterparts**
  — the comparison is like-for-like, not luck.
- Listing $240, floor $160, `firm` policy. The floor is never in any model's
  prompt.
- Models called through the OpenAI API; the engine runs locally.

## The table

| Policy | Capture | Close rate | Sold below floor | Floor leaked | Rounds | Latency / negotiation | Cost / negotiation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Engine** (`bargaining.py`) | **0.309** | 0.656 | **0.000** | 0.000 | 5.41 | **~6 ms** | **$0.00** |
| `gpt-4o-mini` | 0.208 | 0.656 | 0.031 | 0.000 | 5.69 | ~2 s | $0.00031 |
| `gpt-4o` | 0.200 | 0.781 | **0.188** | 0.000 | 5.06 | ~2 s | $0.00472 |

`Capture` is the share of the floor→list spread the seller keeps — the quantity
the objective optimizes. `Sold below floor` is the fraction of closes under the
seller's own floor. Both models' `capture_vs_engine` is negative (−0.10 for
mini, −0.11 for gpt-4o): **on the same buyers, neither model matches the
engine.**

## What it says

- **The engine captures the most value, at zero marginal cost and ~300× the
  speed.** 0.309 vs 0.208 vs 0.200 — a hand-built Bayesian policy beats a small
  model and a frontier model at pricing.
- **Closing more is not doing better.** `gpt-4o` closes the most deals (0.781 vs
  the engine's 0.656) and still captures the least, because it closes by
  *conceding*. This is exactly the failure mode section 6 warns about: reward
  close rate and the agent learns to dump at the floor.
- **Handed pricing authority, the models give money away.** `gpt-4o` sold below
  the seller's floor in **19%** of its closes; `gpt-4o-mini` in 3%. The engine
  did it **zero** times — not because it is careful but because it *cannot*: the
  floor is an invariant in `bargaining.py`, enforced and tested, not a line in a
  prompt the model may or may not honor.
- **Neither model leaked the floor** here — but that is the model choosing not
  to, every turn, forever. The engine removes the possibility: a number that is
  never in a context window cannot be talked out of one.

That is the thesis, measured. Pricing belongs in the engine; language belongs in
the model. The model that priced things was more expensive, slower, and worse —
and occasionally sold below the line the seller drew.

## Honest caveats

- **n = 32.** Enough to separate the policies cleanly on capture, but the
  below-floor rates (1/32 and 6/32) are small counts — directionally strong,
  not tight estimates. Re-run with `--env.taskset.num-eval 128` for a firmer
  number.
- **Simulated buyers.** The counterpart concedes mechanically toward a hidden
  reservation price (`buyer.py`), the same model `beliefs.py` assumes. Real
  buyers are noisier; the held-out set here is synthetic-held-out, and the eval
  becomes *real*-held-out once the event log has a few hundred logged
  negotiations to draw its time-tail from.
- **Off-policy for the engine.** The engine baseline runs against the same
  buyer trajectories; a buyer reacting live to the engine's specific counters
  could differ. The direction — engine ahead on capture, models selling below
  floor — is not subtle enough for that to overturn.

## Reproduce

```bash
cd environments/arina_negotiation && uv pip install -e .
export OPENAI_API_KEY=...   # or point --client.base-url at another provider
uv run eval arina-negotiation -m gpt-4o-mini -n 32 --no-push \
  --env.agent.runtime.type subprocess \
  --client.base-url https://api.openai.com/v1 --client.api-key-var OPENAI_API_KEY \
  --env.taskset.split eval --env.taskset.num-eval 32
```

The engine column is model-free — see the env README's offline self-check.
