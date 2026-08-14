"""HTTP surface.

Route map
---------
POST /listings                 name + description + image -> structured listing
POST /listings/{id}/publish    push to the Meta catalog
PUT  /listings/{id}/policy     set floor, style, weights
POST /threads                  open a negotiation with one buyer
POST /threads/{id}/message     buyer says something, agent replies
POST /threads/{id}/override    seller takes the wheel
GET  /threads/{id}             seller's view, including belief state
GET  /export/events            the training set
POST /webhooks/whatsapp        inbound from Meta
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response

from . import llm, store, whatsapp
from .bargaining import Negotiation, Policy
from .models import AgentTurn, BuyerTurn, ListingIn, ListingOut, PolicyIn


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A restart must not drop open negotiations. Every thread is
    # reconstructable from its event log, so rebuild them all before the
    # first request. See CLAUDE.md build order item 1.
    rehydrate()
    yield


app = FastAPI(title="Arina", version="0.1.0", lifespan=lifespan)
api = APIRouter()

# Threads live in memory between requests and are rehydrated from the event
# log on restart. Swap for Redis when there is more than one worker.
LIVE: dict[str, Negotiation] = {}

OFFER = re.compile(r"\$?\s?(\d{2,6})(?:\s?(?:bucks|dollars|usd))?", re.IGNORECASE)


def read_offer(text: str) -> float | None:
    m = OFFER.search(text.replace(",", ""))
    if not m:
        return None
    n = float(m.group(1))
    return n if 0 < n < 1_000_000 else None


# ------------------------------ listings ----------------------------- #

@api.post("/listings", response_model=ListingOut)
async def create_listing(body: ListingIn):
    fields = await llm.read_listing(body.name, body.description)
    lid = store.new_id("lst")
    out = ListingOut(
        id=lid,
        name=body.name,
        description=fields.get("blurb") or body.description,
        image_url=body.image_url,
        price=body.price or 0.0,
        size=fields.get("size"),
        condition=fields.get("condition"),
        era=fields.get("era"),
        material=fields.get("material"),
        tags=fields.get("tags") or [],
        retail_id=lid,
    )
    with store.conn() as c:
        c.execute(
            "INSERT INTO listings(id, retail_id, seller, payload, created) VALUES (?,?,?,?,?)",
            (lid, lid, "me", out.model_dump_json(), __import__("time").time()),
        )
    store.log("listing_created", listing_id=lid, inferred=fields)
    return out


@api.post("/listings/{listing_id}/publish")
async def publish(listing_id: str):
    with store.conn() as c:
        row = c.execute("SELECT payload FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no such listing")
    l = ListingOut(**json.loads(row["payload"]))
    if not l.image_url:
        raise HTTPException(400, "Meta requires an image_url for catalog items")
    res = await whatsapp.upsert_product(
        retail_id=l.retail_id,
        name=l.name,
        description=l.description,
        price_cents=round(l.price * 100),
        image_url=l.image_url,
    )
    store.log("listing_published", listing_id=listing_id, meta=res)
    return {"ok": True, "retail_id": l.retail_id}


@api.put("/listings/{listing_id}/policy")
def set_policy(listing_id: str, body: PolicyIn):
    Policy(**body.model_dump())  # validate before persisting
    with store.conn() as c:
        c.execute(
            "INSERT INTO policies(listing_id, payload, updated) VALUES (?,?,?) "
            "ON CONFLICT(listing_id) DO UPDATE SET payload=excluded.payload, updated=excluded.updated",
            (listing_id, body.model_dump_json(), __import__("time").time()),
        )
    store.log("policy_set", listing_id=listing_id, style=body.style)
    return {"ok": True}


def _policy_for(listing_id: str) -> Policy:
    with store.conn() as c:
        row = c.execute("SELECT payload FROM policies WHERE listing_id = ?", (listing_id,)).fetchone()
    if not row:
        raise HTTPException(400, "set a policy on this listing first")
    return Policy(**json.loads(row["payload"]))


# ----------------------------- negotiation --------------------------- #

@api.post("/threads")
def open_thread(listing_id: str, buyer: str):
    policy = _policy_for(listing_id)
    n = Negotiation(policy=policy)
    tid = store.new_id("thr")
    LIVE[tid] = n
    with store.conn() as c:
        c.execute(
            "INSERT INTO threads(id, listing_id, buyer, state, status, updated) VALUES (?,?,?,?,?,?)",
            (tid, listing_id, buyer, "{}", "open", __import__("time").time()),
        )
    # Snapshot the policy into the event log. A thread negotiates under the
    # policy in force when it opened; a later edit to the listing's policy
    # must not change how an in-flight thread replays on restart.
    store.log(
        "thread_opened",
        listing_id=listing_id,
        thread_id=tid,
        buyer=buyer,
        policy=dataclasses.asdict(policy),
    )
    return {"thread_id": tid, "list_price": n.policy.list_price}


def _thread(tid: str) -> Negotiation:
    n = LIVE.get(tid)
    if not n:
        raise HTTPException(404, "no such thread, or the process restarted")
    return n


@api.post("/threads/{tid}/message", response_model=AgentTurn)
async def message(tid: str, body: BuyerTurn):
    n = _thread(tid)
    if n.status != "open":
        raise HTTPException(409, f"thread is {n.status}")

    offer = read_offer(body.text)
    turn = n.step(offer)

    history = [f"{e.action}:{e.agent_price}" for e in n.turns[-6:]]
    text = await llm.write_message(
        item=f"listed at ${n.policy.list_price:.0f}",
        history=history,
        buyer_message=body.text,
        action=turn.action,
        number=None if turn.action == "answer" else turn.agent_price,
        first=len(n.turns) == 1,
    )

    store.log(
        "turn", thread_id=tid, round=turn.round, buyer_offer=offer,
        action=turn.action, quoted=turn.agent_price, belief=turn.belief,
    )
    if n.status == "closed":
        store.log("close", thread_id=tid, price=n.agent_price, capture=n.capture, rounds=n.round)
    if n.status == "walked":
        store.log("walk", thread_id=tid, last_offer=offer, rounds=n.round)

    return AgentTurn(
        text=text,
        round=turn.round,
        status=n.status,
        quoted_price=None if turn.action == "answer" else turn.agent_price,
        action=turn.action,
        seller_view={"rationale": turn.rationale, "belief": turn.belief},
    )


def _apply_override(n: Negotiation, action: str) -> float:
    """Apply a seller override to a thread and return the agent price from
    before it. A pure state transition, shared by the override endpoint and
    by replay so a rehydrated overridden thread lands where the live one did.
    Raises ValueError on a bad action; the endpoint maps that to a 400."""
    before = n.agent_price
    if action == "accept_now":
        if not n.offers:
            raise ValueError("nothing on the table")
        n.status = "closed"
        n.agent_price = n.offers[-1]
    elif action == "hold_firm":
        n.agent_price = min(n.policy.list_price, before + 0.25 * (before - n.policy.floor))
    elif action == "concede":
        n.agent_price = max(n.policy.floor, before - 0.30 * (before - n.policy.floor))
    else:
        raise ValueError("action must be accept_now, hold_firm, or concede")
    return before


@api.post("/threads/{tid}/override")
def override(tid: str, action: str):
    """The seller disagrees with the policy. This is the most valuable
    event in the system: it is priced, timestamped, and has full context."""
    n = _thread(tid)
    try:
        before = _apply_override(n, action)
    except ValueError as e:
        raise HTTPException(400, str(e))

    store.log(
        "override", thread_id=tid, action=action, round=n.round,
        agent_wanted=before, seller_set=n.agent_price,
        delta=n.agent_price - before, belief=n.belief.summary(),
    )
    return {"ok": True, "agent_price": n.agent_price, "status": n.status}


@api.get("/threads/{tid}")
def thread_state(tid: str):
    n = _thread(tid)
    return {
        "status": n.status, "round": n.round, "agent_price": n.agent_price,
        "offers": n.offers, "capture": n.capture, "belief": n.belief.summary(),
        "turns": [t.__dict__ for t in n.turns],
    }


@api.get("/export/events")
def export(kind: str | None = None):
    return store.export_events(kind)


# ---------------------------- rehydration ---------------------------- #

def _rebuild(events: list[dict]) -> Negotiation | None:
    """Reconstruct one thread by replaying its event log.

    The engine is deterministic: given the opening policy and the same
    sequence of buyer offers and seller overrides, it returns to exactly the
    state it held before the restart, posterior included. Events are already
    in chronological order; overrides and turns are replayed interleaved
    because an override moves the price the next counter builds on.
    """
    opened = next((e for e in events if e["kind"] == "thread_opened"), None)
    if opened is None or "policy" not in opened:
        # Opened before policy snapshots existed: nothing to replay exactly
        # against, so skip rather than guess with a possibly-edited policy.
        return None
    n = Negotiation(policy=Policy(**opened["policy"]))
    for e in events:
        if n.status != "open":
            break
        if e["kind"] == "turn":
            n.step(e.get("buyer_offer"))
        elif e["kind"] == "override":
            try:
                _apply_override(n, e["action"])
            except ValueError:
                continue  # a malformed override in the log should not abort replay
    return n


def rehydrate() -> None:
    """Rebuild every thread into LIVE from the event log. Runs on startup so
    a process restart does not lose open negotiations. Deterministic replay
    means a closed thread comes back closed and an open one resumes mid-haggle."""
    LIVE.clear()
    for tid in store.thread_ids():
        n = _rebuild(store.thread_events(tid))
        if n is not None:
            LIVE[tid] = n


# ------------------------------ webhook ------------------------------ #

@app.get("/webhooks/whatsapp")
def verify(request: Request):
    q = request.query_params
    if q.get("hub.verify_token") == os.environ.get("WA_VERIFY_TOKEN"):
        return Response(content=q.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "bad verify token")


@app.post("/webhooks/whatsapp")
async def inbound(request: Request):
    raw = await request.body()
    if not whatsapp.verify_signature(raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "bad signature")
    for m in whatsapp.parse_inbound(json.loads(raw)):
        store.log("inbound", **m)
        # Route to the right thread and call message() from here. Left as a
        # deliberate seam: buyer-to-thread mapping is a product decision, not
        # a transport one. See CLAUDE.md, build order item 3.
    return {"received": True}


app.include_router(api)
