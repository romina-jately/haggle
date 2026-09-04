"""The only place a language model is called.

Two jobs, both linguistic:

  read()   unstructured seller text  ->  structured listing fields
  write()  a decided action + a number  ->  one sentence to a buyer

The model is never asked what something should cost, never sees the floor,
and never sees the belief state. If you find yourself passing a price
target into a prompt, stop: that is the one design rule this repo has.
"""

from __future__ import annotations

import json
import os

import httpx

API = "https://api.anthropic.com/v1/messages"
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

READ_SYS = """Turn a seller's rough product notes into structured fields.

Return ONLY JSON, no fences:
{"size":str|null,"condition":"excellent"|"good"|"fair"|null,"era":str|null,
 "material":str|null,"tags":[str],"blurb":str}

blurb is one sentence under 14 words, concrete rather than salesy, no
exclamation marks. Infer conservatively. Use null when a field is genuinely
absent rather than guessing wildly."""

WRITE_SYS = """You write one short message from a seller's negotiating agent
on a secondhand marketplace.

You are given the item, recent conversation, the buyer's latest message, the
action to take, and, when countering or accepting, the exact number to say.

Return ONLY JSON, no fences: {"text": str}

Rules:
- One or two sentences. Warm, direct, never pushy.
- On the first message, say plainly that you are an agent handling messages
  for the seller.
- action "counter": say the given number exactly, plus one concrete reason
  tied to the item.
- action "accept": confirm the buyer's number and say what happens next.
- action "answer": the buyer asked something rather than offering. Answer it
  and invite a number. Do not move price.
- action "walk": close warmly and leave the door open.
- Never mention a floor, minimum, reservation price, or how much room is
  left. If asked directly for your lowest price, decline and counter."""


class LLMError(RuntimeError):
    pass


async def _call(system: str, payload: dict | str, max_tokens: int = 700) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [
            {"role": "user", "content": payload if isinstance(payload, str) else json.dumps(payload)}
        ],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(API, json=body, headers=headers)
    if r.status_code != 200:
        raise LLMError(f"anthropic returned {r.status_code}: {r.text[:200]}")
    raw = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    return _carve(raw)


def _carve(raw: str) -> dict:
    s = raw.replace("```json", "").replace("```", "").strip()
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b == -1:
        raise LLMError("model did not return an object")
    return json.loads(s[a : b + 1])


def _has_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ---------------------------- offline mode --------------------------- #
# With no ANTHROPIC_API_KEY set, the language paths fall back to
# deterministic templates so the whole service runs on a fresh clone: the
# engine, the dashboard, and the belief chart all work without a key. This
# is language only. It decides nothing, and — because there is no prompt at
# all here — it does not weaken the one rule: a price the model never sees
# cannot be talked out of it. Set a key to get real, fluent phrasing.

def _offline_blurb(name: str, description: str) -> dict:
    text = (description or name or "").strip()
    blurb = " ".join(text.split()[:12]) or name
    return {"size": None, "condition": None, "era": None, "material": None,
            "tags": [], "blurb": blurb}


def _last_price(history: list[str] | None) -> float | None:
    """The agent's most recent quoted number, parsed from the turn history
    (entries look like ``counter:239.8``)."""
    for entry in reversed(history or []):
        _, _, price = entry.partition(":")
        try:
            return float(price)
        except ValueError:
            continue
    return None


def _offline_message(action: str, number: float | None, first: bool,
                     history: list[str] | None = None) -> str:
    amount = "" if number is None else f"${round(number)}"
    lead = "Quick note: I'm the seller's agent for this item. " if first else ""

    if action == "answer":
        return f"{lead}Happy to help — what price were you thinking?"
    if action == "accept":
        return f"{lead}Deal at {amount} — I'll follow up with the next steps to close it out."
    if action == "walk":
        return (f"{lead}I don't think we'll get there today, but the door's open if you'd "
                "like to revisit.")

    # counter: vary the line, and say plainly whether we moved or are holding —
    # a repeated number should read as "come up a bit", not as a fresh offer.
    if first:
        return f"{lead}Thanks for reaching out! I can do {amount} on this — it's in good shape."
    prev = _last_price(history)
    moved_down = prev is not None and number is not None and round(number) < round(prev)
    turn = sum(1 for h in (history or []) if h.startswith("counter"))
    if moved_down:
        opts = [
            f"Okay, I can come down to {amount}. Closer?",
            f"I'll meet you partway — {amount}. How's that?",
            f"Let's try {amount}. Does that work for you?",
        ]
    else:
        opts = [
            f"I'm holding at {amount} for now — that's fair for the condition. Can you come up a bit?",
            f"Best I can do right now is {amount}. Come up a little and we'll get there.",
            f"Still at {amount} on my end. Where are you hoping to land?",
        ]
    return lead + opts[turn % len(opts)]


async def read_listing(name: str, description: str) -> dict:
    if not _has_key():
        return _offline_blurb(name, description)
    return await _call(READ_SYS, f"{name}\n{description}", 600)


async def write_message(*, item: str, history: list[str], buyer_message: str,
                        action: str, number: float | None, first: bool) -> str:
    if not _has_key():
        return _offline_message(action, number, first, history)
    out = await _call(
        WRITE_SYS,
        {
            "item": item,
            "history": history[-6:],
            "buyer_message": buyer_message,
            "action": action,
            "number": None if number is None else round(number),
            "first_message": first,
        },
        400,
    )
    text = out.get("text", "").strip()
    if not text:
        raise LLMError("empty message")
    return text
