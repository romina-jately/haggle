# web

Seller dashboard (build order item 2). Buyer chat (item 4) is still to come.

Zero build step: three static files — `index.html`, `styles.css`, `app.js` —
served by the API at `/app` (the API mounts this directory), so there is no
CORS to configure and one `uvicorn` runs both. Open http://localhost:8000/
and it redirects to the dashboard.

Nothing here computes a price. It asks the server and renders what comes
back. The belief posterior in particular is read straight off
`GET /threads/{id}`; the browser only draws the distribution the engine
already inferred. Numbers are monospaced, per `CLAUDE.md`.

What it does:

- **Listing intake** — name, notes, price, image URL (with preview) →
  `POST /listings`. Publish → `POST /listings/{id}/publish`.
- **Policy panel** — list, floor, style, deadline, and the four tactic-weight
  sliders → `PUT /listings/{id}/policy`.
- **Live threads** — open a buyer, send messages, watch the agent counter,
  and drive seller overrides (accept / hold firm / concede).
- **Belief chart** — the posterior over the buyer's reservation price as a
  density with floor / list / optimal-ask markers, plus a fan of p10–p50–p90
  tightening over rounds against the buyer's climbing offers.

With no `ANTHROPIC_API_KEY`, the service phrases replies with offline
templates (see `llm.py`), so the whole dashboard — belief chart included —
works on a fresh clone with no key. Set a key for fluent phrasing.
