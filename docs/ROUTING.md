# Routing inbound WhatsApp to threads

Build order item 3. `main.py:inbound` used to verify the signature, log, and
stop — on purpose. The transport was never the hard part. This is.

## The problem

A WhatsApp conversation is one phone number. A seller has many listings. So
`from` alone does not name a negotiation. When a buyer texts "would you take
180?", 180 for *which* item?

Guessing is the tempting wrong answer. If a buyer has an open haggle on a
jacket and another on a pair of boots, and a bare "180" gets applied to
whichever thread was touched last, a concession meant for one item silently
moves the price on the other. Two negotiations bleed into each other and the
belief state for both is now wrong. A marketplace that does that is not a
product, it is a bug with a phone number.

## The decision

**A thread is keyed by `(buyer_phone, listing)`.** A phone is not a thread. A
phone *and a specific item* is. Every inbound message has to resolve to a
listing before it can move a price, and there are exactly three ways that
goes:

1. **The message names the listing.** WhatsApp attaches a product context in
   two cases we care about:
   - the buyer taps *Message about this product* on a single- or multi-product
     message, and the inbound carries `context.referred_product.product_retailer_id`;
   - the buyer sends a cart, and the `order` webhook carries a
     `product_retailer_id` per line item.

   `retailer_id` is our listing id (set once in `create_listing`, kept stable
   for the life of the listing — see `whatsapp.upsert_product`). So this case
   is unambiguous: find the open `(phone, listing)` thread or open one, and
   step it.

2. **Bare text, one open thread.** No product context, but the buyer has
   exactly one open negotiation. The message is about that item. Route it.

3. **Bare text, more than one open thread.** This is the case that makes the
   decision. We do **not** guess. The agent replies asking which item —
   ideally as a multi-product message of the buyer's own active listings —
   and the inbound is logged `inbound_ambiguous`. The buyer's next tap carries
   the product context from case 1 and resolves it. One cheap round trip buys
   correctness; a guess spends correctness to save it.

And two dead ends that must not crash the webhook:

- **Bare text, no open thread.** A cold message with no item. Logged
  `inbound_unrouted`. We never open a negotiation without a listing.
- **A listing with no policy.** A thread needs a floor and a posture to
  negotiate. Without a saved policy there is nothing to run, so the message is
  logged `inbound_unrouted` rather than opening an unpriced thread.

## Why this shape

- **The listing is data on the message, not a guess about the human.** The
  only reliable signal for *which item* comes from Meta's product context. When
  it is there we trust it; when it is not, one open thread is safe and many is
  not.
- **Ambiguity is a real state with a real reply,** not an error to swallow. It
  is logged, so the rate of it is measurable, and if it turns out to be common
  the fix is a product one (always attach a product card), not a heuristic.
- **The webhook never trusts its way into a crash.** Every branch ends in a
  logged event, so `/export/events` reconstructs exactly how each message was
  routed, the same way it reconstructs every negotiation.

## What this does not do yet

- No dedupe on Meta's `wa_id`. Meta can redeliver a webhook; a production
  build keys on `wa_id` to make routing idempotent. The seam is logged
  (`wa_id` is on every routed event) but the check is not written.
- The disambiguation reply is a plain text question when Meta credentials are
  present, not yet a multi-product message of the buyer's active listings.
  The routing decision above is what had to be settled first.
