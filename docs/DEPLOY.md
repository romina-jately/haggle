# Deploy Haggle & put it on your site

Haggle is a small web server (FastAPI) that serves both the API and the web
app. To make it clickable from your personal website, you host it somewhere
public, then **link or embed** that URL on your site. Two steps.

## 1. Try the container locally (optional)

If you have Docker:

```bash
docker build -t haggle .
docker run -p 8000:8000 haggle
```

Open http://localhost:8000/ — the landing page, with a sample listing already
in the shop (the `HAGGLE_SEED` env var). It runs fully offline; no key needed.

## 2. Host it (get a public URL)

Any container host works — the repo ships a `Dockerfile`. Free options:

- **Render** — New → Web Service → connect this repo → it detects the
  Dockerfile → Create. You get `https://haggle-xxxx.onrender.com`.
  (Free tier sleeps when idle, so the first hit after a nap is slow.)
- **Hugging Face Spaces** — New Space → SDK: **Docker** → push this repo.
  You get `https://<user>-haggle.hf.space`.
- **Railway / Fly.io / Google Cloud Run** — same idea: point them at the repo
  or the Dockerfile.

None of these need an API key — the app runs in offline mode. Set your own
`ANTHROPIC_API_KEY` in the host's environment variables only if you want the
agent's phrasing to come from a model instead of templates. Never paste a key
into the repo.

**Data:** the event log lives at `HAGGLE_DB`. On a free host the disk is
ephemeral, so listings reset when the instance restarts — fine for a demo (the
seed listing comes back). Attach a persistent volume at `/app/data` to keep it.

## 3. Put it on your personal website

Once it's live at, say, `https://haggle-xxxx.onrender.com`:

**Simplest — a link/button.** On your site, add a button that opens it:

```html
<a href="https://haggle-xxxx.onrender.com/" target="_blank">Try Haggle →</a>
```

**Embedded — an iframe.** Drop the live app right into a page:

```html
<iframe
  src="https://haggle-xxxx.onrender.com/app/chat.html"
  style="width:100%; height:820px; border:2px solid #111; border-radius:0;"
  title="Haggle — shop and haggle with the agent">
</iframe>
```

- On **Squarespace/Wix/Webflow**, add a **Code block / Embed block** and paste
  the iframe (these builders can't run Python themselves, which is why the app
  lives on the host above and the page just embeds it).
- On a **static site** (GitHub Pages, plain HTML), paste the same iframe into
  your page.
- Point the `src` at `/app/chat.html` for the buyer shop, `/app/` for the
  landing page, or `/app/dashboard.html` for the seller side.

That's it: the backend runs on the host, your website frames it.
