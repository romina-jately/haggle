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
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import llm, store, whatsapp
from .bargaining import Negotiation, Policy
from .models import (
    AgentTurn,
    BuyerMessage,
    BuyerReply,
    BuyerTurn,
    ListingIn,
    ListingOut,
    PolicyIn,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A restart must not drop open negotiations. Every thread is
    # reconstructable from its event log, so rebuild them all before the
    # first request. See CLAUDE.md build order item 1.
    rehydrate()
    yield


app = FastAPI(title="Haggle", version="0.1.0", lifespan=lifespan)
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
            (lid, lid, "me", out.model_dump_json(), time.time()),
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
            (listing_id, body.model_dump_json(), time.time()),
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

def _set_thread_status(tid: str, status: str) -> None:
    with store.conn() as c:
        c.execute("UPDATE threads SET status = ?, updated = ? WHERE id = ?",
                  (status, time.time(), tid))


def _open_thread(listing_id: str, buyer: str, policy: Policy) -> str:
    """Create a thread and snapshot its policy into the event log. Shared by
    the /threads endpoint and by inbound WhatsApp routing (docs/ROUTING.md)."""
    n = Negotiation(policy=policy)
    tid = store.new_id("thr")
    LIVE[tid] = n
    with store.conn() as c:
        c.execute(
            "INSERT INTO threads(id, listing_id, buyer, state, status, updated) VALUES (?,?,?,?,?,?)",
            (tid, listing_id, buyer, "{}", "open", time.time()),
        )
    # A thread negotiates under the policy in force when it opened; a later
    # edit to the listing's policy must not change how it replays on restart.
    store.log("thread_opened", listing_id=listing_id, thread_id=tid, buyer=buyer,
              policy=dataclasses.asdict(policy))
    return tid


@api.post("/threads")
def open_thread(listing_id: str, buyer: str):
    tid = _open_thread(listing_id, buyer, _policy_for(listing_id))
    return {"thread_id": tid, "list_price": LIVE[tid].policy.list_price}


def _thread(tid: str) -> Negotiation:
    n = LIVE.get(tid)
    if not n:
        raise HTTPException(404, "no such thread, or the process restarted")
    return n


async def _advance(tid: str, buyer_text: str) -> AgentTurn:
    """One negotiation round: parse an offer, step the engine, ask the model
    for a sentence, log the turn. The engine decides; the model only phrases.
    Shared by the message endpoint and by inbound WhatsApp routing so both
    transports drive the exact same logic."""
    n = _thread(tid)
    if n.status != "open":
        raise HTTPException(409, f"thread is {n.status}")

    offer = read_offer(buyer_text)
    turn = n.step(offer)

    history = [f"{e.action}:{e.agent_price}" for e in n.turns[-6:]]
    text = await llm.write_message(
        item=f"listed at ${n.policy.list_price:.0f}",
        history=history,
        buyer_message=buyer_text,
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
        _set_thread_status(tid, "closed")
    if n.status == "walked":
        store.log("walk", thread_id=tid, last_offer=offer, rounds=n.round)
        _set_thread_status(tid, "walked")

    return AgentTurn(
        text=text,
        round=turn.round,
        status=n.status,
        quoted_price=None if turn.action == "answer" else turn.agent_price,
        action=turn.action,
        seller_view={"rationale": turn.rationale, "belief": turn.belief},
    )


@api.post("/threads/{tid}/message", response_model=AgentTurn)
async def message(tid: str, body: BuyerTurn):
    return await _advance(tid, body.text)


# ------------------------------- buyer ------------------------------- #
# The buyer-facing surface (build order item 4): the same negotiation engine,
# reached over the web instead of WhatsApp, so the whole flow is testable
# without a business account. The buyer never sees the seller view — that is
# enforced by the response type, not by remembering to strip a field.

@api.get("/catalog")
def catalog():
    """The shoppable listings, buyer-safe. No floors, no policies, no beliefs —
    only what a product card shows. `negotiable` says whether a policy is set,
    i.e. whether the agent will haggle."""
    with store.conn() as c:
        rows = c.execute(
            "SELECT l.payload, "
            "(SELECT 1 FROM policies p WHERE p.listing_id = l.id) AS negotiable "
            "FROM listings l ORDER BY l.created DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = json.loads(r["payload"])
        out.append({
            "id": d["id"], "name": d["name"], "description": d["description"],
            "price": d["price"], "currency": d.get("currency", "USD"),
            "image_url": d.get("image_url"), "tags": d.get("tags", []),
            "negotiable": bool(r["negotiable"]),
        })
    return out


@api.post("/buyer/message", response_model=BuyerReply)
async def buyer_message(body: BuyerMessage):
    """A buyer's turn from the web chat. Resolves to the buyer's open thread on
    this listing (opening one on the first message), exactly like inbound
    WhatsApp with a product pin. Returns only the buyer-facing reply."""
    tid = _thread_for_listing(body.buyer, body.listing_id)
    if tid is None:
        raise HTTPException(400, "this item isn't taking offers yet")
    turn = await _advance(tid, body.text)
    return BuyerReply(
        text=turn.text, round=turn.round, status=turn.status,
        quoted_price=turn.quoted_price, action=turn.action,
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
    if n.status != "open":
        _set_thread_status(tid, n.status)
    return {"ok": True, "agent_price": n.agent_price, "status": n.status}


def _belief_payload(n: Negotiation) -> dict:
    """The seller's-eye view of the posterior, for the dashboard chart.

    This is a seller endpoint and the belief state is the seller's to see.
    It must still never reach a buyer or a prompt (CLAUDE.md, the one rule),
    which is exactly why it is assembled here in the service and not in
    llm.py. The grid and posterior are what let the dashboard draw the whole
    distribution rather than a handful of quantiles.
    """
    ask, ev = n.belief.optimal_price(floor=n.policy.floor, ceiling=n.policy.list_price)
    return {
        "grid": [round(v, 2) for v in n.belief.grid],
        "post": n.belief.post,
        "optimal_ask": round(ask, 2),
        "optimal_ev": round(ev, 2),
        "summary": n.belief.summary(),
    }


@api.get("/threads/{tid}")
def thread_state(tid: str):
    n = _thread(tid)
    return {
        "status": n.status, "round": n.round, "agent_price": n.agent_price,
        "offers": n.offers, "capture": n.capture,
        "policy": {
            "list_price": n.policy.list_price, "floor": n.policy.floor,
            "style": n.policy.style, "weights": n.policy.weights,
            "deadline_rounds": n.policy.deadline_rounds,
        },
        "belief": _belief_payload(n),
        "turns": [t.__dict__ for t in n.turns],
    }


@api.get("/export/events")
def export(kind: str | None = None):
    return store.export_events(kind)


@api.get("/health")
def health():
    """Cheap liveness plus one bit the dashboard wants: whether the language
    paths have a key or are running the offline templates."""
    return {"ok": True, "offline": not llm._has_key(), "live_threads": len(LIVE),
            "dev_inbound": not os.environ.get("META_APP_SECRET")}


_INBOX_KINDS = {"inbound", "inbound_routed", "inbound_ambiguous", "inbound_unrouted",
                "outbound", "outbound_skipped", "order"}


@api.get("/inbox")
def inbox(limit: int = 40):
    """Recent WhatsApp activity for the dashboard: how each inbound message
    was routed, newest first. Read straight off the event log."""
    evs = [e for e in store.export_events() if e["kind"] in _INBOX_KINDS]
    return list(reversed(evs[-limit:]))


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


# --------------------------- inbound routing ------------------------- #
# The product decision behind all of this is written up in docs/ROUTING.md.
# A thread is keyed by (buyer, listing). Every message must resolve to a
# listing before it can move a price, and we never guess when it can't.

def _policy_or_none(listing_id: str) -> Policy | None:
    with store.conn() as c:
        row = c.execute("SELECT payload FROM policies WHERE listing_id = ?", (listing_id,)).fetchone()
    return Policy(**json.loads(row["payload"])) if row else None


def _buyer_threads(buyer: str) -> list[dict]:
    """Every thread for a buyer, newest first, with live status. Status comes
    from LIVE (accurate after rehydrate), not the threads row, which is only a
    convenience mirror."""
    with store.conn() as c:
        rows = c.execute(
            "SELECT id, listing_id FROM threads WHERE buyer = ? ORDER BY updated DESC", (buyer,)
        ).fetchall()
    return [
        {"tid": r["id"], "listing_id": r["listing_id"],
         "status": LIVE[r["id"]].status if r["id"] in LIVE else "unknown"}
        for r in rows
    ]


def _open_threads(buyer: str) -> list[dict]:
    return [t for t in _buyer_threads(buyer) if t["status"] == "open"]


def _thread_for_listing(buyer: str, listing_id: str) -> str | None:
    """The buyer's open thread on this listing, opening one if needed. Returns
    None only when the listing has no policy, so there is nothing to negotiate."""
    for t in _open_threads(buyer):
        if t["listing_id"] == listing_id:
            return t["tid"]
    policy = _policy_or_none(listing_id)
    if policy is None:
        return None
    return _open_thread(listing_id, buyer, policy)


async def _reply(buyer: str, text: str) -> None:
    """Best-effort outbound. Only the buyer-facing sentence goes out, never
    the seller view. Without Meta credentials (dev) this is a no-op that logs
    the intent rather than crashing the webhook."""
    try:
        await whatsapp.send_text(buyer, text)
        store.log("outbound", buyer=buyer, text=text)
    except Exception as e:  # noqa: BLE001 - transport is best-effort here
        store.log("outbound_skipped", buyer=buyer, text=text, error=str(e)[:200])


async def route_inbound(msg: dict) -> dict:
    """Resolve one inbound message to a thread and advance it, or record why
    it could not be routed. Never raises: every branch ends in a logged event
    so /export/events reconstructs how each message was handled. Returns a
    small outcome dict for callers that want it (the dev endpoint)."""
    buyer = msg.get("from")
    if not buyer:
        return {"outcome": "ignored"}
    text = msg.get("text", "") or ""
    listing_id = msg.get("product_retailer_id")

    if listing_id:
        tid = _thread_for_listing(buyer, listing_id)
        if tid is None:
            store.log("inbound_unrouted", buyer=buyer, wa_id=msg.get("wa_id"),
                      listing_id=listing_id, reason="listing has no policy")
            return {"outcome": "unrouted", "reason": "listing has no policy"}
    else:
        opens = _open_threads(buyer)
        if len(opens) == 1:
            tid = opens[0]["tid"]
        elif not opens:
            store.log("inbound_unrouted", buyer=buyer, wa_id=msg.get("wa_id"),
                      reason="no product context and no open thread")
            return {"outcome": "unrouted", "reason": "no product context and no open thread"}
        else:
            store.log("inbound_ambiguous", buyer=buyer, wa_id=msg.get("wa_id"),
                      open_threads=[t["tid"] for t in opens],
                      listings=[t["listing_id"] for t in opens])
            await _reply(buyer, "You've got a few items going with me — which one is "
                                "this about? Tap the product and send again and I'll pick "
                                "up right there.")
            return {"outcome": "ambiguous", "open_threads": [t["tid"] for t in opens]}

    turn = await _advance(tid, text)
    store.log("inbound_routed", buyer=buyer, wa_id=msg.get("wa_id"),
              thread_id=tid, listing_id=listing_id, action=turn.action)
    await _reply(buyer, turn.text)
    return {"outcome": "routed", "thread_id": tid, "action": turn.action}


@app.post("/webhooks/whatsapp")
async def inbound(request: Request):
    raw = await request.body()
    if not whatsapp.verify_signature(raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "bad signature")
    for m in whatsapp.parse_inbound(json.loads(raw)):
        store.log("inbound", buyer=m.get("from"), wa_id=m.get("wa_id"),
                  msg_type=m.get("type"), product_retailer_id=m.get("product_retailer_id"),
                  text=m.get("text", ""))
        await route_inbound(m)
    return {"received": True}


# In development (no Meta app secret to verify a real webhook signature),
# expose a signature-free way to inject an inbound message so the routing
# above can be exercised end to end from the dashboard. Never mounted when a
# secret is configured.
if not os.environ.get("META_APP_SECRET"):
    @app.post("/dev/inbound")
    async def dev_inbound(msg: dict):
        return await route_inbound(msg)


app.include_router(api)


# ------------------------------ dashboard ---------------------------- #
# Serve the seller dashboard (build order item 2) from the same origin as
# the API, so the browser needs no CORS grant and one `uvicorn` runs both.
_WEB = Path(__file__).resolve().parents[2] / "web"
if _WEB.is_dir():
    @app.get("/")
    def _root() -> RedirectResponse:
        return RedirectResponse("/app/")

    app.mount("/app", StaticFiles(directory=str(_WEB), html=True), name="app")
