# Arina

An agentic marketplace for people who sell things one at a time.

Listing takes three minutes. Answering the next forty messages takes three
weeks. Arina does the second part.

Two flows:

**Storefront.** Product name, description, and a photo go in. A structured
listing comes out, gets pushed to a Meta catalog, and becomes shoppable
inside WhatsApp as a product message.

**Haggle.** The seller sets a floor, a posture, and tactic weights once. An
agent then negotiates every interested buyer in parallel, holding a floor it
will not reveal because the floor is never in a prompt.

---

## The one design rule

The language model reads English and writes English. It never decides a
price.

Every number a buyer sees is computed in `bargaining.py` from the seller's
policy and a Bayesian posterior over what that buyer will pay. The floor,
the belief state, and the tactic weights never enter a context window. A
reservation price that is not in the prompt cannot be talked out of the
agent, in any language, by any buyer, however clever the message.

Full derivation in [`docs/NEGOTIATION.md`](docs/NEGOTIATION.md).

---

## Quickstart

```bash
cd api
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest                          # 23 tests, no network, no keys needed
cp .env.example .env            # fill in for the LLM and WhatsApp paths
uvicorn arina.main:app --reload
```

The negotiation engine has no dependencies beyond the standard library and
runs offline. You only need keys for listing extraction and for WhatsApp.

Watch the three postures diverge against the same buyer:

```python
from arina.bargaining import Negotiation, Policy

for style in ("firm", "balanced", "eager"):
    n = Negotiation(policy=Policy(list_price=240, floor=160, style=style))
    for offer in (140, 158, 172, 183, 191, 197, 202, 206):
        if n.status != "open":
            break
        n.step(offer)
    print(f"{style:>9}: {n.status} at {n.agent_price:.0f}, "
          f"{n.round} rounds, capture {n.capture:.0%}")
```

```
     firm: closed at 197  rounds 6  capture 46%
 balanced: closed at 183  rounds 4  capture 29%
    eager: closed at 172  rounds 3  capture 15%
```

That spread is the product. Firm holds out and keeps 46% of the room
between floor and list; eager closes in half the time and keeps 15%. Which
one a seller wants depends on whether they need the money this week.

---

## API

| Route | Does |
| --- | --- |
| `POST /listings` | Name, description, image in. Structured listing out. |
| `POST /listings/{id}/publish` | Push to the Meta catalog |
| `PUT /listings/{id}/policy` | Floor, style, deadline, weights |
| `POST /threads` | Open a negotiation with one buyer |
| `POST /threads/{id}/message` | Buyer says something, agent replies |
| `POST /threads/{id}/override` | Seller takes the wheel |
| `GET /threads/{id}` | Seller's view, including the belief state |
| `GET /export/events` | The training set |
| `/webhooks/whatsapp` | Inbound from Meta, signature-verified |

---

## WhatsApp, accurately

There is no "WhatsApp Shopping API". What exists:

- A **catalog** lives in Meta Commerce Manager and is populated through the
  Commerce API.
- The catalog is **linked to a WABA phone number** once, during setup.
- The **Cloud API** sends single-product, multi-product (up to 30 items),
  and catalog messages that reference it.
- There is **no native checkout**. A completed cart arrives as an `order`
  webhook and you reply with a payment link.

`whatsapp.py` covers catalog upsert, all three message types, webhook
signature verification, and inbound parsing. Setup steps are in
[`docs/WHATSAPP_SETUP.md`](docs/WHATSAPP_SETUP.md).

---

## Layout

```
api/
  arina/
    bargaining.py   the engine: tactics, invariants, acceptance rule
    beliefs.py      grid posterior over the buyer's reservation price
    llm.py          the only place a model is called. language only.
    whatsapp.py     Cloud API client, catalog sync, webhook verification
    store.py        append-only event log. this is the training set.
    models.py       wire types
    main.py         routes
  tests/            23 tests over invariants, beliefs, and outcomes
docs/
  NEGOTIATION.md    the math, and one bug worth reading about
  WHATSAPP_SETUP.md
web/                seller dashboard and buyer chat
```

---

## Status

The engine and the service are real and tested. Live threads persist across
restarts by replaying the event log, the seller dashboard is built (listing
intake, the policy panel, live threads, and the belief posterior as a
chart), and inbound WhatsApp routing is implemented — see
[`docs/ROUTING.md`](docs/ROUTING.md) for the product decision behind it. The
WhatsApp *send* path still needs a verified business account to run end to
end; a dev inbound endpoint exercises routing without one, and with no
`ANTHROPIC_API_KEY` the language paths fall back to templates so the whole
thing runs offline.

Not yet built, in order: buyer-side chat, and the training environment that
consumes `/export/events`. See `CLAUDE.md`.
