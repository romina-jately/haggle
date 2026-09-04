/* Haggle buyer chat (build order item 4).
 *
 * The buyer side of the same negotiation, over the web instead of WhatsApp.
 * It talks only to /catalog and /buyer/message — endpoints that never carry
 * the floor, the belief state, or the agent's rationale. There is nothing
 * here to leak, by construction: the server strips the seller view before it
 * reaches this page. See CLAUDE.md, and models.BuyerReply.
 */

const $ = (s) => document.querySelector(s);
const money = (n) => (n == null ? "—" : `$${Math.round(n)}`);

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

let toastTimer;
function toast(msg, bad = false) {
  const el = $("#toast");
  el.textContent = msg; el.classList.toggle("bad", bad); el.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers["content-type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const d = data && data.detail ? data.detail : `${res.status}`;
    throw new Error(typeof d === "string" ? d : JSON.stringify(d));
  }
  return data;
}

// buyer identity: stable per browser, editable-free for now
function buyerId() {
  let id = localStorage.getItem("haggle:buyer");
  if (!id) { id = "web_" + Math.random().toString(36).slice(2, 8); localStorage.setItem("haggle:buyer", id); }
  return id;
}

// per-(buyer,listing) transcript
const chatKey = (lid) => `haggle:buyerchat:${buyerId()}:${lid}`;
function loadChat(lid) { return JSON.parse(localStorage.getItem(chatKey(lid)) || '{"messages":[],"status":"open"}'); }
function saveChat(lid, state) { localStorage.setItem(chatKey(lid), JSON.stringify(state)); }

let current = null; // current listing object

// ------------------------------ catalog ----------------------------- //

async function loadCatalog() {
  let items;
  try { items = await api("GET", "/catalog"); }
  catch { $("#grid").innerHTML = `<div class="empty">Couldn't reach the shop.</div>`; return; }
  const grid = $("#grid");
  if (!items.length) {
    grid.innerHTML = `<div class="empty">No listings yet. Create one in the seller dashboard, then come back.</div>`;
    return;
  }
  grid.innerHTML = "";
  items.forEach((it) => {
    const card = document.createElement("div");
    card.className = "product";
    const ph = it.image_url
      ? `<div class="ph" style="background-image:url('${encodeURI(it.image_url)}')"></div>`
      : `<div class="ph">no photo</div>`;
    card.innerHTML = ph + `<div class="info">
        <span class="nm">${escapeHtml(it.name)}</span>
        <span class="desc">${escapeHtml(it.description || "")}</span>
        <div class="foot">
          <span class="pr">${money(it.price)}</span>
          <span class="pill ${it.negotiable ? "open" : ""}">${it.negotiable ? "negotiable" : "fixed price"}</span>
        </div>
      </div>`;
    card.addEventListener("click", () => openChat(it));
    grid.appendChild(card);
  });
}

// ------------------------------- chat -------------------------------- //

function openChat(item) {
  current = item;
  $("#catalog-view").hidden = true;
  $("#chat-view").hidden = false;
  $("#c-name").textContent = item.name;
  $("#c-price").textContent = `listed at ${money(item.price)}`;
  const thumb = $("#c-thumb");
  thumb.style.backgroundImage = item.image_url ? `url('${encodeURI(item.image_url)}')` : "";
  renderThread();
  if (!item.negotiable) {
    setComposer(false, `<div class="banner walked">This item is a fixed price — the seller hasn't opened it to offers.</div>`);
  }
}

$("#back").addEventListener("click", () => {
  $("#chat-view").hidden = true;
  $("#catalog-view").hidden = false;
  loadCatalog();
});

function bubble(m) {
  const el = document.createElement("div");
  el.className = "bub " + (m.who === "me" ? "me" : "them");
  el.innerHTML = escapeHtml(m.text);
  return el;
}

function renderThread() {
  const state = loadChat(current.id);
  const box = $("#thread");
  box.innerHTML = "";
  if (!state.messages.length) {
    box.innerHTML = `<div class="empty">Say hi, or make an offer. The seller's agent will reply.</div>`;
  } else {
    state.messages.forEach((m) => box.appendChild(bubble(m)));
  }
  // outcome banner + composer state
  if (state.status === "closed") setComposer(false, `<div class="banner deal">Deal agreed${state.dealPrice ? ` at ${money(state.dealPrice)}` : ""}. The seller will follow up to finish up.</div>`);
  else if (state.status === "walked") setComposer(false, `<div class="banner walked">The seller couldn't meet this one — but you can still browse other items.</div>`);
  else if (current.negotiable) setComposer(true, "");
  box.scrollTop = box.scrollHeight;
}

function setComposer(enabled, bannerHtml) {
  $("#c-banner").innerHTML = bannerHtml || "";
  $("#composer").style.display = enabled ? "flex" : "none";
}

async function send() {
  if (!current) return;
  const input = $("#c-text");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  const state = loadChat(current.id);
  state.messages.push({ who: "me", text });
  saveChat(current.id, state);
  renderThread();

  // typing indicator
  const box = $("#thread");
  const typing = document.createElement("div");
  typing.className = "typing"; typing.textContent = "the seller's agent is typing…";
  box.appendChild(typing); box.scrollTop = box.scrollHeight;

  try {
    const reply = await api("POST", "/buyer/message", { buyer: buyerId(), listing_id: current.id, text });
    const s = loadChat(current.id);
    s.messages.push({ who: "them", text: reply.text });
    s.status = reply.status;
    if (reply.status === "closed") s.dealPrice = reply.quoted_price;
    saveChat(current.id, s);
    renderThread();
  } catch (e) {
    typing.remove();
    toast("Message failed: " + e.message, true);
  }
}

$("#c-send").addEventListener("click", send);
$("#c-text").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

// ------------------------------- init ------------------------------- //

$("#who").textContent = "you: " + buyerId();
loadCatalog();
