"""Haggle negotiation environment (build order item 5).

A `verifiers.v1` environment that puts a model in the seller's negotiating
seat and scores it on the objective from `docs/NEGOTIATION.md` section 6:

    capture = (final_price - floor) / (list_price - floor)
    reward  = capture                     if the deal closes
            = -walk_penalty * reachable   if it does not

`reachable` is 1 when the buyer could ever have cleared the floor and 0 when
they could not, so the policy is not punished for walking away from a buyer who
was never going to pay. The floor is never shown to the model — the same rule
the production service keeps — so a reservation price that is not in the prompt
cannot be talked out of it.

The counterpart is a deterministic buyer (`buyer.py`) that concedes toward a
hidden reservation price. Every episode also runs the hand-built `bargaining.py`
engine against the identical buyer and reports its capture, so each row is a
head-to-head of the model against the engine the service actually ships — the
raw material of the results table in build order item 6.

Splits: training rows are simulated buyers; the eval rows are a disjoint,
held-out stream. With a real event log the eval set becomes its chronological
tail (`docs/CLAUDE.md`: split by time, not at random).
"""

from __future__ import annotations

import random
import re

import verifiers.v1 as vf

from .bargaining import Policy
from .baseline import engine_outcome
from .buyer import Buyer, BuyerProfile, Outcome, parse_move, play

AGENT_SYS = """You are the seller's negotiating agent for a used item listed at ${list_price:.0f}. \
A buyer will message you, usually with an offer.

Hold out for a strong price without losing the sale: concede slowly, mirror the \
buyer's own movement, and never counter above your own previous counter.

Reply to EVERY buyer message with a JSON object and nothing else:
{{"action": "counter" | "accept" | "walk" | "answer", "price": <number or null>, "message": "<one short line to the buyer>"}}

- "counter": name your asking price in `price`.
- "accept": take the buyer's current offer; put that number in `price`.
- "walk": end the conversation warmly; price null.
- "answer": reply without changing the price; price null.

Never mention a floor, a minimum, or the least you would accept."""


# ------------------------------- data -------------------------------- #

class NegotiationData(vf.TaskData):
    """One negotiation: a listing and a hidden buyer. The reservation price and
    concession shape are the buyer's; they never reach the model's prompt."""

    reservation: float
    list_price: float
    floor: float
    style: str = "firm"
    open_ratio: float = 0.62
    concede_rate: float = 0.30
    patience: int = 8
    split: str = "train"


class NegotiationTaskConfig(vf.TaskConfig):
    walk_penalty: float = 0.5
    """Cost of failing to close a reachable buyer (NEGOTIATION.md section 6)."""
    deadline_rounds: int = 10
    """Rounds before the negotiation times out unresolved."""


def _text(m) -> str:
    c = getattr(m, "content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join((getattr(p, "text", "") or "") for p in c)
    return str(c or "")


class HaggleNegotiationTask(vf.Task[NegotiationData, vf.State, NegotiationTaskConfig]):
    """Scores one negotiation. The reward reconstructs the episode deterministically
    from the agent's own turns in the trace, replayed against the same buyer the
    env drove, so scoring never depends on state smuggled out of the rollout."""

    # ---- deterministic reconstruction from the trace ---- #

    def _profile(self) -> BuyerProfile:
        d = self.data
        return BuyerProfile(d.reservation, d.open_ratio, d.concede_rate, d.patience)

    def _moves(self, trace: vf.Trace):
        return [parse_move(_text(m)) for m in trace.assistant_messages]

    def _outcome(self, trace: vf.Trace) -> Outcome:
        return play(self._profile(), self.data.list_price, self._moves(trace),
                    self.config.deadline_rounds)

    def _capture(self, out: Outcome) -> float:
        spread = self.data.list_price - self.data.floor
        if out.status != "closed" or out.final_price is None or spread <= 0:
            return 0.0
        return (out.final_price - self.data.floor) / spread

    # ------------------------- reward --------------------------- #

    @vf.reward(weight=1.0)
    async def objective(self, trace: vf.Trace) -> float:
        out = self._outcome(trace)
        if out.status == "closed":
            return self._capture(out)  # may be negative if the model sold below floor
        reachable = 1.0 if self.data.reservation >= self.data.floor else 0.0
        return -self.config.walk_penalty * reachable

    # ------------------------- metrics -------------------------- #
    # Observability only; not summed into the reward. Together they are the
    # per-episode row of the results table (item 6).

    @vf.metric
    async def close_rate(self, trace: vf.Trace) -> float:
        return 1.0 if self._outcome(trace).status == "closed" else 0.0

    @vf.metric
    async def capture(self, trace: vf.Trace) -> float:
        return self._capture(self._outcome(trace))

    @vf.metric
    async def gmv(self, trace: vf.Trace) -> float:
        """Gross merchandise value. Reported, never trained on — it is confounded
        by price level and category (NEGOTIATION.md section 6)."""
        out = self._outcome(trace)
        return float(out.final_price) if out.status == "closed" and out.final_price else 0.0

    @vf.metric
    async def rounds(self, trace: vf.Trace) -> float:
        return float(self._outcome(trace).rounds)

    @vf.metric
    async def sold_below_floor(self, trace: vf.Trace) -> float:
        """The model gave away money it did not have to — accepted under the floor."""
        out = self._outcome(trace)
        below = out.status == "closed" and out.final_price is not None and out.final_price < self.data.floor
        return 1.0 if below else 0.0

    @vf.metric
    async def floor_leaked(self, trace: vf.Trace) -> float:
        """Did the model reveal it has a floor? The one thing it must never do,
        and a thing a model in the pricing seat can do that the engine cannot."""
        blob = " ".join(_text(m) for m in trace.assistant_messages).lower()
        leaked = re.search(r"\bfloor\b|\bminimum\b|lowest i (can|could)|reservation price|the least", blob)
        return 1.0 if leaked else 0.0

    @vf.metric
    async def engine_capture(self, trace: vf.Trace) -> float:
        """What the deterministic engine would have captured on this exact buyer."""
        policy = Policy(list_price=self.data.list_price, floor=self.data.floor,
                        style=self.data.style, deadline_rounds=self.config.deadline_rounds)
        return self._capture(engine_outcome(self._profile(), policy, self.config.deadline_rounds))

    @vf.metric
    async def capture_vs_engine(self, trace: vf.Trace) -> float:
        """Model capture minus engine capture on the same buyer. Positive means the
        model beat the engine; over a taskset this is the headline number."""
        return await self.capture(trace) - await self.engine_capture(trace)


# ---------------------------- environment ---------------------------- #

class HaggleNegotiationEnvConfig(vf.EnvConfig):
    # The negotiator only chats — no shell, no tools — so it defaults to the
    # tool-less `null` harness rather than the `bash` fallback a plain taskset
    # would otherwise get. Override with `--env.agent.harness.id`.
    agent: vf.AgentConfig = vf.AgentConfig(harness={"id": "null"})
    """The one seat: the negotiating policy under evaluation or training."""


class HaggleNegotiationEnv(vf.Env[HaggleNegotiationEnvConfig]):
    """Drives a deterministic buyer against the model. The env is the control
    flow; the buyer's moves are computed in Python (no second model), so training
    signal comes only from the negotiator's own turns."""

    async def run(self, task: vf.Task, agents: vf.Agents) -> None:
        d = task.data
        deadline = task.config.deadline_rounds
        buyer = Buyer(self._profile(d), d.list_price)
        async with agents.agent.interaction(task) as agent:
            seg = await agent.turn(buyer.opening())
            while not seg.terminated and buyer.status == "open":
                reply = buyer.respond(parse_move(seg.last_reply))
                if buyer.status != "open":
                    break
                if buyer.round >= deadline:
                    buyer.status = "walked"
                    break
                seg = await agent.turn(reply)

    @staticmethod
    def _profile(d: NegotiationData) -> BuyerProfile:
        return BuyerProfile(d.reservation, d.open_ratio, d.concede_rate, d.patience)


# ------------------------------ taskset ------------------------------ #

class HaggleNegotiationConfig(vf.TasksetConfig):
    num_train: int = 64
    num_eval: int = 32
    split: str = "all"          # train | eval | all
    seed: int = 0
    list_price: float = 240.0
    floor: float = 160.0
    style: str = "firm"
    task: NegotiationTaskConfig = NegotiationTaskConfig()


class HaggleNegotiationTaskset(vf.Taskset[HaggleNegotiationTask, HaggleNegotiationConfig]):
    def _make(self, idx: int, rng: random.Random, split: str) -> HaggleNegotiationTask:
        c = self.config
        # Reservation spans from below the floor (unreachable — tests `reachable`)
        # to comfortably above list (an easy close).
        reservation = round(rng.uniform(c.floor * 0.75, c.list_price * 1.15), 2)
        data = NegotiationData(
            idx=idx,
            prompt=None,  # the buyer opens the conversation
            system_prompt=AGENT_SYS.format(list_price=c.list_price),
            reservation=reservation,
            list_price=c.list_price,
            floor=c.floor,
            style=c.style,
            open_ratio=round(rng.uniform(0.55, 0.70), 3),
            concede_rate=round(rng.uniform(0.20, 0.45), 3),
            patience=rng.randint(5, 10),
            split=split,
        )
        return HaggleNegotiationTask(data, c.task)

    def load(self) -> list[HaggleNegotiationTask]:
        c = self.config
        tasks: list[HaggleNegotiationTask] = []
        if c.split in ("train", "all"):
            rng = random.Random(c.seed)
            tasks += [self._make(i, rng, "train") for i in range(c.num_train)]
        if c.split in ("eval", "all"):
            # A disjoint stream is the held-out set; with a real event log this is
            # instead the chronological tail (split by time, not at random).
            rng = random.Random(c.seed + 10_000)
            base = c.num_train
            tasks += [self._make(base + i, rng, "eval") for i in range(c.num_eval)]
        return tasks


__all__ = ["HaggleNegotiationTaskset", "HaggleNegotiationEnv"]
