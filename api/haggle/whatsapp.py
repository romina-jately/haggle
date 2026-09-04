"""WhatsApp Business Cloud API client.

There is no "WhatsApp Shopping API". What exists is:

  Commerce Manager / Commerce API   holds the product catalog
  WhatsApp Cloud API                sends messages that reference it
  Catalog is linked to a WABA phone number, once, in setup

So a "storefront" is a Meta catalog plus a phone number that knows how to
talk about it. Products are shared as single-product, multi-product (up to
30 items), or catalog messages. There is no native checkout: the cart is
handed back to you as an `order` webhook and you send a payment link.

Docs: developers.facebook.com/documentation/business-messaging/whatsapp/catalogs
"""

from __future__ import annotations

import hashlib
import hmac
import os

import httpx

GRAPH = "https://graph.facebook.com/v21.0"


def _cfg(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"{name} is not set")
    return v


async def _post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{GRAPH}/{path}",
            json=payload,
            headers={"Authorization": f"Bearer {_cfg('META_ACCESS_TOKEN')}"},
        )
    if r.status_code >= 300:
        raise RuntimeError(f"graph {r.status_code}: {r.text[:300]}")
    return r.json()


# ----------------------------- catalog ------------------------------ #

async def upsert_product(*, retail_id: str, name: str, description: str,
                         price_cents: int, image_url: str, currency: str = "USD",
                         url: str = "", availability: str = "in stock") -> dict:
    """Create or update one item in the Meta catalog.

    `retail_id` is your SKU and the key Meta dedupes on. It is also what a
    product message references, so keep it stable for the life of a listing.
    """
    return await _post(
        f"{_cfg('META_CATALOG_ID')}/items_batch",
        {
            "item_type": "PRODUCT_ITEM",
            "requests": [
                {
                    "method": "UPDATE",
                    "retailer_id": retail_id,
                    "data": {
                        "name": name[:150],
                        "description": description[:9999],
                        "price": price_cents,
                        "currency": currency,
                        "image_url": image_url,
                        "url": url or f"https://wa.me/{os.environ.get('WA_PHONE_E164','')}",
                        "availability": availability,
                        "condition": "used",
                    },
                }
            ],
        },
    )


# ----------------------------- messages ----------------------------- #

async def send_text(to: str, body: str) -> dict:
    return await _post(
        f"{_cfg('WA_PHONE_NUMBER_ID')}/messages",
        {"messaging_product": "whatsapp", "to": to, "type": "text",
         "text": {"body": body[:4096], "preview_url": False}},
    )


async def send_product(to: str, retail_id: str, body: str) -> dict:
    """Single-product message: renders as a product detail card in-thread."""
    return await _post(
        f"{_cfg('WA_PHONE_NUMBER_ID')}/messages",
        {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "product",
                "body": {"text": body[:1024]},
                "action": {
                    "catalog_id": _cfg("META_CATALOG_ID"),
                    "product_retailer_id": retail_id,
                },
            },
        },
    )


async def send_product_list(to: str, header: str, body: str,
                            sections: list[dict]) -> dict:
    """Multi-product message. Up to 30 items across up to 10 sections.

    sections: [{"title": "Outerwear", "product_items": [{"product_retailer_id": "..."}]}]
    """
    return await _post(
        f"{_cfg('WA_PHONE_NUMBER_ID')}/messages",
        {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "product_list",
                "header": {"type": "text", "text": header[:60]},
                "body": {"text": body[:1024]},
                "action": {
                    "catalog_id": _cfg("META_CATALOG_ID"),
                    "sections": sections[:10],
                },
            },
        },
    )


# ----------------------------- webhook ------------------------------ #

def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """Meta signs every webhook with your app secret. Check it or you are
    accepting negotiation messages from anyone who finds the URL."""
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(
        _cfg("META_APP_SECRET").encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(digest, header.split("=", 1)[1])


def parse_inbound(payload: dict) -> list[dict]:
    """Flatten Meta's nested webhook into the few things we care about.

    The one field that makes routing possible is the product context: when a
    buyer messages from a product or catalog card, Meta stamps the inbound with
    the item it came from. We surface it as `product_retailer_id` (our listing
    id) so `main.py` can route without guessing. See docs/ROUTING.md.
    """
    out: list[dict] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for m in value.get("messages", []):
                kind = m.get("type")
                item = {"from": m.get("from"), "wa_id": m.get("id"), "type": kind}
                # A message sent from a product/catalog card carries the item
                # it references. This is the listing pin.
                referred = m.get("context", {}).get("referred_product", {})
                if referred.get("product_retailer_id"):
                    item["product_retailer_id"] = referred["product_retailer_id"]
                if kind == "text":
                    item["text"] = m.get("text", {}).get("body", "")
                elif kind == "order":
                    order = m.get("order", {})
                    item["catalog_id"] = order.get("catalog_id")
                    item["items"] = order.get("product_items", [])
                    item["text"] = order.get("text", "")
                    # A single-item cart pins the listing the same way.
                    if len(item["items"]) == 1:
                        item["product_retailer_id"] = item["items"][0].get("product_retailer_id")
                elif kind == "interactive":
                    item["text"] = str(m.get("interactive", {}))
                out.append(item)
    return out
