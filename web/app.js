/* Arina seller dashboard.
 *
 * Nothing here computes a price. The dashboard asks the server for state and
 * draws it. The belief posterior in particular is read straight off
 * GET /threads/{id}; the browser only renders the distribution the engine
 * already inferred. See web/README.md and CLAUDE.md build order item 2.
 */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const money = (n) => (n == null ? "—" : `$${Math.round(n)}`);
const pct = (n) => (n == null ? "—" : `${Math.round(n * 100)}%`);

// ------------------------------- API -------------------------------- //

async function api(method, path, { body, query } = {}) {
  const qs = query ? "?" + new URLSearchParams(query).toString() : "";
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["content-type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path + qs, opts);
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

// ------------------------------ toast ------------------------------- //

let toastTimer;
function toast(msg, bad = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("bad", bad);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

// ------------------------------ state ------------------------------- //

const store = {
  get listing() { return JSON.parse(localStorage.getItem("arina:listing") || "null"); },
  set listing(v) { localStorage.setItem("arina:listing", JSON.stringify(v)); },
  get threads() { return JSON.parse(localStorage.getItem("arina:threads") || "[]"); },
  set threads(v) { localStorage.setItem("arina:threads", JSON.stringify(v)); },
  convo(tid) { return JSON.parse(localStorage.getItem("arina:convo:" + tid) || "[]"); },
  pushConvo(tid, entry) {
    const c = this.convo(tid);
    c.push(entry);
    localStorage.setItem("arina:convo:" + tid, JSON.stringify(c));
  },
};

let current = null; // current thread id

// ---------------------------- listings ------------------------------ //

$("#l-img").addEventListener("input", (e) => {
  const url = e.target.value.trim();
  const img = $("#l-preview");
  if (url) { img.src = url; img.hidden = false; } else { img.hidden = true; }
});

$("#l-create").addEventListener("click", async () => {
  const name = $("#l-name").value.trim();
  if (!name) return toast("A listing needs a name.", true);
  try {
    const listing = await api("POST", "/listings", {
      body: {
        name,
        description: $("#l-desc").value.trim(),
        image_url: $("#l-img").value.trim() || null,
        price: Number($("#l-price").value) || 0,
      },
    });
    store.listing = listing;
    $("#l-result").innerHTML =
      `Created <span class="pill">${listing.id}</span> — ` +
      `<span class="muted">“${listing.description}”</span>` +
      (listing.tags.length ? ` · tags: ${listing.tags.join(", ")}` : "");
    // seed the policy panel from the listing price
    $("#p-list").value = Math.round(listing.price) || 240;
    revealListingUI();
    toast("Listing created.");
  } catch (e) {
    toast("Create failed: " + e.message, true);
  }
});

function revealListingUI() {
  $("#policy-card").hidden = false;
  $("#open-card").hidden = false;
}

// ----------------------------- policy ------------------------------- //

function weights() {
  const w = {};
  $$("#weights input[data-w]").forEach((s) => { w[s.dataset.w] = Number(s.value); });
  return w;
}
function renderWeightOutputs() {
  const w = weights();
  const total = Object.values(w).reduce((a, b) => a + b, 0) || 1;
  $$("#weights .wrow").forEach((row) => {
    const key = row.querySelector("input").dataset.w;
    row.querySelector("output").textContent = (w[key] / total).toFixed(2);
  });
}
$$("#weights input[data-w]").forEach((s) => s.addEventListener("input", renderWeightOutputs));
renderWeightOutputs();

$("#p-save").addEventListener("click", async () => {
  const l = store.listing;
  if (!l) return toast("Create a listing first.", true);
  const list_price = Number($("#p-list").value);
  const floor = Number($("#p-floor").value);
  if (!(floor > 0 && floor < list_price)) return toast("Need 0 < floor < list price.", true);
  try {
    await api("PUT", `/listings/${l.id}/policy`, {
      body: {
        list_price, floor,
        style: $("#p-style").value,
        deadline_rounds: Number($("#p-deadline").value) || 10,
        weights: weights(),
      },
    });
    toast("Policy saved.");
  } catch (e) {
    toast("Policy rejected: " + e.message, true);
  }
});

$("#p-publish").addEventListener("click", async () => {
  const l = store.listing;
  if (!l) return toast("Create a listing first.", true);
  try {
    await api("POST", `/listings/${l.id}/publish`);
    toast("Published to catalog.");
  } catch (e) {
    toast("Publish: " + e.message, true);
  }
});

// ----------------------------- threads ------------------------------ //

$("#t-open").addEventListener("click", async () => {
  const l = store.listing;
  if (!l) return toast("Create a listing and save a policy first.", true);
  const buyer = $("#t-buyer").value.trim() || "buyer_" + Math.random().toString(36).slice(2, 6);
  try {
    const r = await api("POST", "/threads", { query: { listing_id: l.id, buyer } });
    const threads = store.threads;
    threads.unshift({ id: r.thread_id, buyer });
    store.threads = threads;
    $("#t-buyer").value = "";
    renderThreadList();
    selectThread(r.thread_id);
    toast(`Thread open with ${buyer}.`);
  } catch (e) {
    toast("Open failed: " + e.message + " — save a policy first?", true);
  }
});

$("#refresh").addEventListener("click", () => { renderThreadList(); if (current) loadThread(current); });

function renderThreadList() {
  const box = $("#thread-list");
  const threads = store.threads;
  if (!threads.length) {
    box.innerHTML = `<span class="muted small">No threads yet. Create a listing, set a policy, open one.</span>`;
    return;
  }
  box.innerHTML = "";
  threads.forEach((t) => {
    const chip = document.createElement("div");
    chip.className = "chip" + (t.id === current ? " active" : "");
    chip.innerHTML = `<span>${t.buyer}</span><span class="id">${t.id.slice(0, 10)}</span>`;
    chip.addEventListener("click", () => selectThread(t.id));
    box.appendChild(chip);
  });
}

function selectThread(tid) {
  current = tid;
  renderThreadList();
  $("#thread-card").hidden = false;
  $("#belief-card").hidden = false;
  loadThread(tid);
}

async function loadThread(tid) {
  let s;
  try {
    s = await api("GET", `/threads/${tid}`);
  } catch (e) {
    toast("Could not load thread: " + e.message, true);
    return;
  }
  $("#th-id").textContent = tid.slice(0, 12);
  const badge = $("#th-status");
  badge.textContent = s.status;
  badge.className = "pill " + s.status;

  const b = s.belief.summary;
  $("#th-stats").innerHTML = [
    stat("list", money(s.policy.list_price)),
    stat("floor", money(s.policy.floor)),
    stat("agent ask", money(s.agent_price)),
    stat("round", s.round),
    stat("capture", pct(s.capture)),
    stat("buyer p50", money(b.p50)),
    stat("optimal ask", money(s.belief.optimal_ask)),
  ].join("");

  renderConvo(tid, s);
  drawDensity(s);
  drawRounds(s);
}

function stat(k, v) {
  return `<div class="stat"><span class="k">${k}</span><span class="v">${v}</span></div>`;
}

// --------------------------- conversation --------------------------- //

function renderConvo(tid, s) {
  const box = $("#convo");
  const saved = store.convo(tid);
  box.innerHTML = "";
  if (saved.length) {
    saved.forEach((m) => box.appendChild(msgEl(m)));
  } else if (s.turns.length) {
    // Rehydrated thread with no local transcript: synthesize from the turns.
    s.turns.forEach((t) => {
      if (t.buyer_offer != null) box.appendChild(msgEl({ who: "buyer", text: `Offer ${money(t.buyer_offer)}` }));
      box.appendChild(msgEl({
        who: "agent", text: synthetic(t), action: t.action,
        quoted: t.action === "counter" || t.action === "accept" ? t.agent_price : null,
        rationale: t.rationale,
      }));
    });
  } else {
    box.innerHTML = `<div class="empty">No messages yet. Send the buyer's first message below.</div>`;
  }
  box.scrollTop = box.scrollHeight;
}

function synthetic(t) {
  if (t.action === "counter") return `Countered at ${money(t.agent_price)}.`;
  if (t.action === "accept") return `Accepted at ${money(t.agent_price)}.`;
  if (t.action === "walk") return `Walked away.`;
  return `Answered.`;
}

function msgEl(m) {
  const el = document.createElement("div");
  el.className = "msg " + (m.who === "buyer" ? "buyer" : "agent");
  let html = `<div class="who">${m.who}</div><div>${escapeHtml(m.text)}</div>`;
  if (m.quoted != null) html += ``; // number already in the text, kept mono via .q if present
  if (m.rationale) html += `<div class="rationale">${escapeHtml(m.rationale)}</div>`;
  el.innerHTML = html;
  return el;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

$("#msg-send").addEventListener("click", sendMessage);
$("#msg-text").addEventListener("keydown", (e) => { if (e.key === "Enter") sendMessage(); });

async function sendMessage() {
  if (!current) return;
  const text = $("#msg-text").value.trim();
  if (!text) return;
  $("#msg-text").value = "";
  store.pushConvo(current, { who: "buyer", text });
  try {
    const turn = await api("POST", `/threads/${current}/message`, { body: { text } });
    const sv = turn.seller_view || {};
    store.pushConvo(current, {
      who: "agent", text: turn.text, action: turn.action,
      quoted: turn.quoted_price, rationale: sv.rationale,
    });
    loadThread(current);
  } catch (e) {
    store.pushConvo(current, { who: "agent", text: "⚠ " + e.message });
    loadThread(current);
    toast("Message failed: " + e.message, true);
  }
}

// ---------------------------- overrides ----------------------------- //

$$("[data-ov]").forEach((btn) => btn.addEventListener("click", async () => {
  if (!current) return;
  const action = btn.dataset.ov;
  try {
    const r = await api("POST", `/threads/${current}/override`, { query: { action } });
    store.pushConvo(current, { who: "agent", text: `[seller override: ${action}] agent ask now ${money(r.agent_price)}`, rationale: "seller took the wheel" });
    loadThread(current);
    toast(`Override: ${action}.`);
  } catch (e) {
    toast("Override: " + e.message, true);
  }
}));

// ------------------------------ charts ------------------------------ //
// Plain SVG. x maps a price to a pixel; y maps a probability/price to one.

function svg(w, h, inner) {
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" preserveAspectRatio="xMidYMid meet">${inner}</svg>`;
}
const lerp = (a, b, t) => a + (b - a) * t;

function drawDensity(s) {
  const W = 580, H = 190, padL = 8, padR = 8, padT = 12, padB = 26;
  const grid = s.belief.grid, post = s.belief.post;
  if (!grid || !grid.length) { $("#belief-chart").innerHTML = ""; return; }
  const xmin = grid[0], xmax = grid[grid.length - 1];
  const ymax = Math.max(...post) || 1;
  const X = (v) => padL + (W - padL - padR) * (v - xmin) / (xmax - xmin || 1);
  const Y = (p) => H - padB - (H - padT - padB) * (p / ymax);

  // area under the posterior
  let d = `M ${X(xmin)} ${H - padB}`;
  grid.forEach((v, i) => { d += ` L ${X(v).toFixed(1)} ${Y(post[i]).toFixed(1)}`; });
  d += ` L ${X(xmax)} ${H - padB} Z`;

  const vline = (v, color, label, dash = "") => {
    if (v == null || v < xmin || v > xmax) return "";
    const x = X(v).toFixed(1);
    return `<line x1="${x}" y1="${padT}" x2="${x}" y2="${H - padB}" stroke="${color}" stroke-width="1.5" ${dash ? `stroke-dasharray="${dash}"` : ""}/>` +
           `<text x="${x}" y="${padT - 2}" fill="${color}" font-size="10" text-anchor="middle">${label}</text>`;
  };

  const b = s.belief.summary;
  const inner =
    `<path d="${d}" fill="#4c9be833" stroke="#4c9be8" stroke-width="1.5"/>` +
    vline(s.policy.floor, "#f2727f", "floor") +
    vline(s.policy.list_price, "#e6edf3", "list") +
    vline(s.belief.optimal_ask, "#7ee0c0", "ask", "3 3") +
    vline(b.p50, "#9aa7b4", "p50", "2 2") +
    // x ticks
    [0, 0.5, 1].map((t) => {
      const v = lerp(xmin, xmax, t);
      return `<text x="${X(v).toFixed(1)}" y="${H - padB + 15}" fill="#6b7885" font-size="10" text-anchor="middle">${money(v)}</text>`;
    }).join("") +
    `<line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="#2a323d"/>`;
  $("#belief-chart").innerHTML = svg(W, H, inner);
}

function drawRounds(s) {
  const W = 580, H = 190, padL = 34, padR = 10, padT = 12, padB = 24;
  const rows = s.turns
    .map((t) => ({ r: t.round, ...t.belief, offer: t.buyer_offer }))
    .filter((r) => r.p50 != null);
  const host = $("#rounds-chart");
  if (rows.length < 1) { host.innerHTML = `<div class="empty">Send an offer to watch the posterior tighten.</div>`; return; }

  const maxR = Math.max(...rows.map((r) => r.r), 1);
  const vals = rows.flatMap((r) => [r.p10, r.p90, r.offer]).filter((v) => v != null)
    .concat([s.policy.floor, s.policy.list_price]);
  const lo = Math.min(...vals) * 0.96, hi = Math.max(...vals) * 1.04;
  const X = (r) => padL + (W - padL - padR) * (maxR === 1 ? 0.5 : (r - 1) / (maxR - 1));
  const Y = (v) => H - padB - (H - padT - padB) * (v - lo) / (hi - lo || 1);

  // p10-p90 band
  let up = "", dn = "";
  rows.forEach((r, i) => { up += `${i ? "L" : "M"} ${X(r.r).toFixed(1)} ${Y(r.p90).toFixed(1)} `; });
  for (let i = rows.length - 1; i >= 0; i--) dn += `L ${X(rows[i].r).toFixed(1)} ${Y(rows[i].p10).toFixed(1)} `;
  const band = rows.length > 1 ? `<path d="${up}${dn}Z" fill="#4c9be822" stroke="none"/>` : "";

  // p50 line + points
  let p50 = "";
  rows.forEach((r, i) => { p50 += `${i ? "L" : "M"} ${X(r.r).toFixed(1)} ${Y(r.p50).toFixed(1)} `; });
  const p50line = `<path d="${p50}" fill="none" stroke="#e6edf3" stroke-width="1.5"/>`;
  const dots = rows.map((r) =>
    (r.offer != null ? `<circle cx="${X(r.r).toFixed(1)}" cy="${Y(r.offer).toFixed(1)}" r="3" fill="#e3b341"/>` : "")
  ).join("");

  // floor / list guides + y labels
  const guide = (v, color, label) =>
    `<line x1="${padL}" y1="${Y(v).toFixed(1)}" x2="${W - padR}" y2="${Y(v).toFixed(1)}" stroke="${color}" stroke-width="1" stroke-dasharray="3 3" opacity="0.5"/>` +
    `<text x="2" y="${Y(v) + 3}" fill="${color}" font-size="9">${money(v)}</text>`;

  const xticks = rows.map((r) => `<text x="${X(r.r).toFixed(1)}" y="${H - padB + 14}" fill="#6b7885" font-size="9" text-anchor="middle">${r.r}</text>`).join("");

  host.innerHTML = svg(W, H,
    guide(s.policy.floor, "#f2727f", "") + guide(s.policy.list_price, "#e6edf3", "") +
    band + p50line + dots + xticks +
    `<text x="${(padL + W) / 2}" y="${H - 2}" fill="#6b7885" font-size="9" text-anchor="middle">round</text>`);
}

// -------------------------- whatsapp feed --------------------------- //

const KIND = {
  inbound: ["inbound", "in"],
  inbound_routed: ["routed", "routed"],
  inbound_ambiguous: ["ambiguous", "ambiguous"],
  inbound_unrouted: ["unrouted", "unrouted"],
  outbound: ["outbound", "out"],
  outbound_skipped: ["outbound", "out·skip"],
  order: ["order", "order"],
};

function feedBody(e) {
  const id = (s) => `<span class="id">${(s || "").slice(0, 10)}</span>`;
  switch (e.kind) {
    case "inbound":
      return `<b>${escapeHtml(e.buyer)}</b> ${e.product_retailer_id ? `· pinned ${id(e.product_retailer_id)} ` : ""}“${escapeHtml((e.text || "").slice(0, 60))}”`;
    case "inbound_routed":
      return `→ ${escapeHtml(e.buyer)} routed to ${id(e.thread_id)} · ${e.action}`;
    case "inbound_ambiguous":
      return `${escapeHtml(e.buyer)} has ${(e.open_threads || []).length} open threads — asked which item`;
    case "inbound_unrouted":
      return `${escapeHtml(e.buyer)} not routed — ${escapeHtml(e.reason || "")}`;
    case "outbound":
      return `↩ ${escapeHtml(e.buyer)} “${escapeHtml((e.text || "").slice(0, 60))}”`;
    case "outbound_skipped":
      return `↩ ${escapeHtml(e.buyer)} — no WABA, reply not sent`;
    case "order":
      return `cart from ${escapeHtml(e.buyer)}`;
    default:
      return e.kind;
  }
}

function hhmmss(ts) {
  try { return new Date(ts * 1000).toLocaleTimeString([], { hour12: false }); }
  catch { return ""; }
}

async function loadInbox() {
  let evs;
  try { evs = await api("GET", "/inbox"); } catch { return; }
  const box = $("#wa-feed");
  if (!evs.length) { box.innerHTML = `<div class="empty">No inbound messages yet.</div>`; return; }
  box.innerHTML = evs.map((e) => {
    const [cls, label] = KIND[e.kind] || ["inbound", e.kind];
    return `<div class="ev"><span class="k ${cls}">${label}</span><span class="body">${feedBody(e)}</span><time>${hhmmss(e.ts)}</time></div>`;
  }).join("");
}

$("#wa-refresh").addEventListener("click", loadInbox);

$("#wa-send").addEventListener("click", async () => {
  const from = $("#wa-from").value.trim();
  if (!from) return toast("Enter a buyer phone.", true);
  const msg = { from, text: $("#wa-text").value.trim() };
  const product = $("#wa-product").value.trim();
  if (product) msg.product_retailer_id = product;
  msg.wa_id = "sim_" + Math.random().toString(36).slice(2, 8);
  try {
    const r = await api("POST", "/dev/inbound", { body: msg });
    $("#wa-text").value = "";
    // an auto-created thread won't be in our local list; adopt it
    if (r.outcome === "routed" && r.thread_id && !store.threads.some((t) => t.id === r.thread_id)) {
      const threads = store.threads;
      threads.unshift({ id: r.thread_id, buyer: from });
      store.threads = threads;
      renderThreadList();
    }
    toast(`Inbound ${r.outcome}${r.action ? " · " + r.action : ""}.`);
    loadInbox();
    if (r.thread_id) selectThread(r.thread_id);
    else if (current) loadThread(current);
  } catch (e) {
    toast("Inbound failed: " + e.message, true);
  }
});

// ------------------------------- init ------------------------------- //

async function init() {
  try {
    const h = await api("GET", "/health");
    $("#mode").innerHTML = h.offline
      ? `mode: <b>offline</b> — templated replies`
      : `mode: <b>live model</b>`;
    if (h.dev_inbound) $("#wa-sim").hidden = false;
  } catch {
    $("#mode").textContent = "API unreachable";
  }
  if (store.listing) { revealListingUI(); $("#l-result").innerHTML = `Active listing <span class="pill">${store.listing.id}</span>`; $("#p-list").value = Math.round(store.listing.price) || 240; }
  renderThreadList();
  loadInbox();
  if (store.threads[0]) selectThread(store.threads[0].id);
}
init();
