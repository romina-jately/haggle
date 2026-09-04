# arina-negotiation

A `verifiers.v1` environment that puts a model in the seller's negotiating seat
and scores it on the Arina objective. Build order item 5 of the
[Arina](https://github.com/romina-jately/arina) project; the reward is the one
derived in that repo's `docs/NEGOTIATION.md` section 6.

## What it measures

Each episode is one negotiation. The model plays the seller's agent against a
**deterministic buyer** that opens below its hidden reservation price and
concedes toward it over rounds (`buyer.py` — the same concession model
`beliefs.py` assumes the engine is inferring against). The model answers each
buyer message with a JSON action (`counter` / `accept` / `walk` / `answer`) and
a price. Reward, from NEGOTIATION.md section 6:

```
capture = (final_price - floor) / (list_price - floor)
reward  = capture                     if the deal closes
        = -walk_penalty * reachable   if it does not
```

`reachable` is 1 when the buyer could ever clear the floor and 0 otherwise, so
the policy is not punished for walking away from a buyer who was never going to
pay. **The floor is never in the model's prompt** — the same rule the production
service keeps — so a reservation price that is not in a context window cannot be
talked out of one.

### The engine baseline

Every episode also runs the deterministic `bargaining.py` engine (vendored from
the Arina service) against the *same* buyer and reports its capture. So each row
is a head-to-head: the model versus the engine the service actually ships,
buyer by buyer, on the identical objective. `capture_vs_engine` is the headline
number — positive means the model beat the engine. This is the raw material of
the results table in build order item 6.

## Metrics

Observability only — not summed into the reward:

| metric | meaning |
| --- | --- |
| `close_rate` | did the deal close |
| `capture` | share of the floor→list spread kept |
| `gmv` | final price if closed (reported, never trained — it is confounded) |
| `rounds` | how long the negotiation ran |
| `sold_below_floor` | the model accepted under the floor (gave away money) |
| `floor_leaked` | the model revealed it has a floor — the one thing it must not do |
| `engine_capture` | what the engine captured on this buyer |
| `capture_vs_engine` | model capture − engine capture |

`floor_leaked` and `sold_below_floor` are the failure modes a model in the
pricing seat can hit that the deterministic engine cannot — worth watching from
the first eval.

## Splits

Training rows are simulated buyers; the eval rows are a disjoint, held-out
stream (`--taskset.split train|eval|all`). With a real event log the eval set
becomes its chronological tail — split by time, not at random (Arina
`CLAUDE.md`). Buyers are drawn deterministically from `--taskset.seed`, with
reservation prices spanning from below the floor (unreachable) to above list.

## Run it

```bash
uv pip install -e .
uv run eval arina-negotiation -m <model> -n 16          # needs PRIME_API_KEY / prime login
uv run eval arina-negotiation -m <model> --taskset.split eval --no-push
```

Knobs: `--taskset.num-train`, `--taskset.num-eval`, `--taskset.seed`,
`--taskset.list-price`, `--taskset.floor`, `--taskset.style`,
`--taskset.task.walk-penalty`, `--taskset.task.deadline-rounds`. The negotiator
rides the tool-less `null` chat harness by default.

## Verify offline (no model, no key)

The scoring core is pure Python and deterministic. This reconstructs an episode
from a canned set of agent moves and checks the reward, the engine baseline, and
the unreachable-buyer case:

```bash
uv run python - <<'PY'
import asyncio
from arina_negotiation.taskset import ArinaNegotiationTask, NegotiationData, NegotiationTaskConfig
class M:
    def __init__(s,t): s.content=t
class T:
    assistant_messages=[M('{"action":"counter","price":230}'),
                        M('{"action":"accept","price":195}')]
    last_reply='x'
t = ArinaNegotiationTask(NegotiationData(idx=0, reservation=205, list_price=240, floor=160), NegotiationTaskConfig())
print("reward",        asyncio.run(t.objective(T())))
print("capture",       asyncio.run(t.capture(T())))
print("engine_capture",asyncio.run(t.engine_capture(T())))
print("vs_engine",     asyncio.run(t.capture_vs_engine(T())))
PY
```

## Files

```
arina_negotiation/
  taskset.py    the Env (drives the buyer), the Task (reward + metrics), the Taskset
  buyer.py      deterministic counterpart, agent-move parsing, episode replay
  baseline.py   runs the engine against the same buyer
  bargaining.py \ vendored verbatim from the Arina service, stdlib-only
  beliefs.py    /  (source of truth is api/arina/ in the Arina repo)
```
