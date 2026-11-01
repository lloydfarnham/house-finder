/* House Hunt — static front end. Listings come from data/listings.json (written by the
   GitHub Action). Saved properties, notes and hidden items live in Supabase so Lloyd and
   Meg see the same thing; without Supabase configured they fall back to this browser. */
(() => {
  "use strict";

  const CFG = window.HOUSE_CONFIG || {};
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => (n == null ? "?" : "£" + Number(n).toLocaleString("en-GB"));
  const ls = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } },
  };

  const STATUSES = [
    ["shortlisted", "Shortlisted"],
    ["contacted", "Contacted agent"],
    ["viewing", "Viewing booked"],
    ["viewed", "Viewed"],
    ["applied", "Applied / offered"],
    ["secured", "Secured 🎉"],
    ["rejected", "Not for us"],
  ];
  const STATUS_LABEL = Object.fromEntries(STATUSES);

  const state = {
    data: null,
    saved: [],          // [{id, listing, status, viewing_at, rating_lloyd, rating_meg, notes:[]}]
    hidden: new Set(),
    who: ls.get("hh.who", null),
    tab: ls.get("hh.tab", "properties"),
    filters: Object.assign({ area: "all", sort: "new", price: "2000", cycle: "99", tube: false, outdoor: false, jan: false, early: false, hidden: false }, ls.get("hh.filters", {})),
    lastVisit: ls.get("hh.lastVisit", null),
    photoIdx: {},
  };

  // ------------------------------------------------------------------ storage

  const hasSupabase = !!(CFG.supabaseUrl && CFG.supabaseKey);

  function sbHeaders(extra = {}) {
    const h = { apikey: CFG.supabaseKey, "Content-Type": "application/json", ...extra };
    if (!CFG.supabaseKey.startsWith("sb_")) h.Authorization = "Bearer " + CFG.supabaseKey;
    return h;
  }
  async function sb(path, { method = "GET", body, prefer } = {}) {
    const res = await fetch(CFG.supabaseUrl.replace(/\/$/, "") + "/rest/v1/" + path, {
      method,
      headers: sbHeaders(prefer ? { Prefer: prefer } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(`Supabase ${res.status}: ${(await res.text()).slice(0, 200)}`);
    return res.status === 204 ? null : res.json().catch(() => null);
  }

  const remote = {
    async load() {
      const [saved, hidden] = await Promise.all([
        sb("saved?select=*,notes(*)&order=created_at.desc"),
        sb("hidden?select=id"),
      ]);
      saved.forEach((s) => s.notes.sort((a, b) => a.created_at.localeCompare(b.created_at)));
      return { saved, hidden: hidden.map((h) => h.id) };
    },
    save: (row) => sb("saved?on_conflict=id", { method: "POST", body: row, prefer: "resolution=merge-duplicates,return=representation" }),
    patch: (id, fields) => sb("saved?id=eq." + encodeURIComponent(id), { method: "PATCH", body: { ...fields, updated_at: new Date().toISOString() } }),
    remove: (id) => sb("saved?id=eq." + encodeURIComponent(id), { method: "DELETE" }),
    addNote: (note) => sb("notes", { method: "POST", body: note, prefer: "return=representation" }).then((r) => r[0]),
    delNote: (id) => sb("notes?id=eq." + id, { method: "DELETE" }),
    hide: (id) => sb("hidden?on_conflict=id", { method: "POST", body: { id, created_by: state.who }, prefer: "resolution=merge-duplicates" }),
    unhide: (id) => sb("hidden?id=eq." + encodeURIComponent(id), { method: "DELETE" }),
  };

  const local = {
    _get() { return ls.get("hh.local", { saved: [], hidden: [] }); },
    _put(v) { ls.set("hh.local", v); },
    async load() { return this._get(); },
    async save(row) { const v = this._get(); v.saved = v.saved.filter((s) => s.id !== row.id); v.saved.unshift({ notes: [], ...row }); this._put(v); },
    async patch(id, fields) { const v = this._get(); const s = v.saved.find((x) => x.id === id); if (s) Object.assign(s, fields); this._put(v); },
    async remove(id) { const v = this._get(); v.saved = v.saved.filter((s) => s.id !== id); this._put(v); },
    async addNote(n) { const v = this._get(); const note = { ...n, id: Date.now(), created_at: new Date().toISOString() }; const s = v.saved.find((x) => x.id === n.listing_id); if (s) s.notes.push(note); this._put(v); return note; },
    async delNote(id) { const v = this._get(); v.saved.forEach((s) => (s.notes = s.notes.filter((n) => n.id !== id))); this._put(v); },
    async hide(id) { const v = this._get(); if (!v.hidden.includes(id)) v.hidden.push(id); this._put(v); },
    async unhide(id) { const v = this._get(); v.hidden = v.hidden.filter((h) => h !== id); this._put(v); },
  };
  const store = hasSupabase ? remote : local;

  async function loadShared() {
    try {
      const { saved, hidden } = await store.load();
      state.saved = saved;
      state.hidden = new Set(hidden);
    } catch (e) {
      console.error(e);
      toast("Couldn't load saved list — " + e.message);
    }
  }

  // ------------------------------------------------------------------ helpers

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), 2600);
  }

  function ago(iso) {
    if (!iso) return "";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    const d = Math.round(s / 86400);
    return d === 1 ? "yesterday" : d + " days ago";
  }
  const fmtDateTime = (iso) => new Date(iso).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  function isNew(l) {
    const t = new Date(l.firstSeen).getTime();
    if (state.lastVisit && t > new Date(state.lastVisit).getTime()) return true;
    return Date.now() - t < 24 * 3600 * 1000;
  }
  const isSaved = (id) => state.saved.some((s) => s.id === id);
  const hasOutdoor = (l) => (l.tags || []).some((t) => ["Garden", "Balcony", "Terrace", "Patio"].includes(t));

  function availHtml(a) {
    if (!a) return "";
    if (a.flag === "ok") return `<span class="avail ok">${esc(a.text)}</span>`;
    if (a.kind === "now") return `<span class="avail early">Available now (may be let before Jan)</span>`;
    if (a.flag === "early") return `<span class="avail early">${esc(a.text)} (before Jan)</span>`;
    return `<span class="avail unknown">Availability not stated — ask agent</span>`;
  }

  function photoHtml(l, key) {
    const imgs = (l.images && l.images.length ? l.images : [l.image]).filter(Boolean);
    const i = Math.min(state.photoIdx[key] || 0, Math.max(imgs.length - 1, 0));
    const img = imgs.length
      ? `<img src="${esc(imgs[i])}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'noimg',textContent:'🏠'}))">`
      : `<div class="noimg">🏠</div>`;
    const nav = imgs.length > 1
      ? `<button class="nav prev" data-photo="${esc(key)}" data-dir="-1" aria-label="Previous photo">‹</button>
         <button class="nav next" data-photo="${esc(key)}" data-dir="1" aria-label="Next photo">›</button>
         <span class="idx">${i + 1}/${imgs.length}</span>` : "";
    return { html: img + nav, count: imgs.length };
  }

  function factsHtml(l) {
    const st = l.station || {};
    const lines = (st.lines || []).join(", ");
    let walk = st.walkMins ? `<strong>${st.walkMins} min walk</strong> to ${esc(st.name)} <span class="meta">(${esc(lines)})</span>` : `Near ${esc(st.name || l.area)} <span class="meta">(exact location hidden)</span>`;
    const tube = l.tube;
    if (st.mode === "overground" && tube && tube.walkMins != null) {
      walk += `<br><span class="meta">Nearest tube: ${esc(tube.name)}, ${tube.walkMins} min walk (${esc((tube.lines || []).join(", "))})</span>`;
    }
    const rooms = [l.beds != null ? `${l.beds} bed` : null, l.baths ? `${l.baths} bath` : null, l.sizeSqft ? `${l.sizeSqft} sq ft` : null].filter(Boolean).join(" · ");
    return `
      <div class="facts">
        <div class="fact"><span class="i">🚇</span><span>${walk}</span></div>
        ${l.cycleMins ? `<div class="fact"><span class="i">🚲</span><span>~${l.cycleMins} min cycle to Euston</span></div>` : ""}
        <div class="fact"><span class="i">📅</span>${availHtml(l.availability)}</div>
        ${rooms ? `<div class="fact"><span class="i">🛏️</span><span>${esc(rooms)}${l.propertyType ? " · " + esc(l.propertyType) : ""}</span></div>` : ""}
      </div>`;
  }

  function tagsHtml(l) {
    const good = ["Garden", "Balcony", "Terrace", "Patio", "Pets OK", "Bills incl.", "Separate lounge"];
    const tags = (l.tags || []).map((t) => `<span class="tag ${good.includes(t) ? "good" : ""}">${esc(t)}</span>`);
    if (l.deposit) tags.push(`<span class="tag">Deposit ${esc(l.deposit)}</span>`);
    return tags.length ? `<div class="tags">${tags.join("")}</div>` : "";
  }

  function linksHtml(l) {
    return `<div class="links">${(l.links || []).map((k) => `<a href="${esc(k.url)}" target="_blank" rel="noopener">${esc(k.label)} ↗</a>`).join("")}</div>`;
  }

  // ------------------------------------------------------------------ properties tab

  function filtered() {
    if (!state.data) return [];
    const f = state.filters;
    let list = state.data.listings.filter((l) => {
      if (!f.hidden && state.hidden.has(l.id)) return false;
      if (f.area === "pref" && l.tier !== 1) return false;
      if (f.area.startsWith("a:") && l.area !== f.area.slice(2)) return false;
      if (l.cycleMins != null && l.cycleMins > Number(f.cycle)) return false;
      if (f.tube && !(l.tube && l.tube.walkMins != null && l.tube.walkMins <= ((state.data.criteria || {}).maxWalkMins || 10))) return false;
      if (l.price > Number(f.price)) return false;
      if (f.outdoor && !hasOutdoor(l)) return false;
      if (f.jan && (l.availability || {}).flag !== "ok") return false;
      if (!f.early && (l.availability || {}).flag === "early") return false;
      return true;
    });
    const by = {
      new: (a, b) => b.firstSeen.localeCompare(a.firstSeen),
      pref: (a, b) => (a.tier || 2) - (b.tier || 2) || b.firstSeen.localeCompare(a.firstSeen),
      price: (a, b) => a.price - b.price,
      walk: (a, b) => ((a.station || {}).walkMins ?? 99) - ((b.station || {}).walkMins ?? 99),
      cycle: (a, b) => (a.cycleMins ?? 999) - (b.cycleMins ?? 999),
    }[f.sort];
    return list.sort(by);
  }

  function propertyCard(l) {
    const p = photoHtml(l, l.id);
    const badges = [];
    if (isNew(l)) badges.push(`<span class="badge new">New</span>`);
    if (l.reducedFrom) badges.push(`<span class="badge drop">↓ ${money(l.reducedFrom - l.price)}</span>`);
    badges.push(`<span class="badge tier">${esc(l.area)}</span>`);
    const saved = isSaved(l.id);
    const hidden = state.hidden.has(l.id);
    return `
      <article class="card ${hidden ? "is-hidden" : ""}" data-id="${esc(l.id)}">
        <div class="photo">${p.html}<div class="badges">${badges.join("")}</div></div>
        <div class="body">
          <div class="price">${money(l.price)} <small>pcm</small>${l.reducedFrom ? `<s>${money(l.reducedFrom)}</s>` : ""}</div>
          <p class="title">${esc(l.title)}</p>
          <p class="addr">${esc(l.address)}</p>
          ${factsHtml(l)}
          ${tagsHtml(l)}
          ${l.description ? `<details class="desc"><summary>Description</summary><p>${esc(l.description.slice(0, 900))}${l.description.length > 900 ? "…" : ""}</p></details>` : ""}
          <div class="meta">${esc(l.agent || "")}${l.agent ? " · " : ""}found ${ago(l.firstSeen)}</div>
        </div>
        <div class="foot">
          ${linksHtml(l)}
          <div class="actions">
            <button class="btn small ghost" data-act="${hidden ? "unhide" : "hide"}" title="${hidden ? "Show again" : "Hide from this list for both of us"}">${hidden ? "Unhide" : "Hide"}</button>
            <button class="btn small ${saved ? "saved" : ""}" data-act="${saved ? "goto-saved" : "save"}">${saved ? "✓ Saved" : "♡ Save"}</button>
          </div>
        </div>
      </article>`;
  }

  function renderProperties() {
    const d = state.data;
    const list = filtered();
    $("#grid").innerHTML = list.map(propertyCard).join("");
    const empty = $("#empty");
    empty.hidden = list.length > 0;
    if (!list.length) empty.textContent = d ? (d.listings.length ? "Nothing matches these filters right now. January availability usually starts appearing from early December." : "No matching properties right now — we'll ping you when one appears.") : "";
    const hiddenCount = d ? d.listings.filter((l) => state.hidden.has(l.id)).length : 0;
    const earlyCount = d ? d.listings.filter((l) => (l.availability || {}).flag === "early" && !state.hidden.has(l.id)).length : 0;
    $("#count-properties").textContent = d ? d.listings.length - hiddenCount - (state.filters.early ? 0 : earlyCount) : 0;
    if (d) {
      const c = d.criteria || {};
      $("#criteria").textContent = `East & North London · ${c.minBeds}+ bed · ≤ ${money(c.maxPricePcm)} pcm · ≤ ${c.maxWalkMins} min walk to a station` + (c.maxCycleMins ? ` · ≤ ${c.maxCycleMins} min cycle to Euston` : "");
      fillAreas(d);
      $("#updated").innerHTML = `Last searched <strong>${ago(d.generatedAt)}</strong> · checks hourly 8am–7pm` +
        (CFG.repo ? ` · <a href="https://github.com/${esc(CFG.repo)}/actions/workflows/scrape.yml" target="_blank" rel="noopener">run a search now</a>` : "");
      $("#sources").innerHTML = Object.entries(d.sources || {}).map(([name, s]) =>
        s.ok ? `<span class="src" title="Checked ${esc(ago(s.at))}">${esc(name)} ✓ ${s.found}</span>`
             : `<span class="src fail" title="${esc(s.error || "")}">${esc(name)} ✗ ${s.usingResultsFrom ? "using earlier results" : "unavailable this run"}</span>`).join("");
      const ex = d.excluded || {};
      const exTxt = Object.entries(ex).map(([k, v]) => `${v} ${k}`).join(", ");
      $("#footnote").innerHTML = `Walk times are estimates from the listing's map pin (~5 km/h, allowing for streets); cycle times assume ~18 km/h. ` +
        `Properties available after ${new Date(c.moveInLatest).toLocaleDateString("en-GB", { day: "numeric", month: "long" })} are left out.` +
        (exTxt ? ` Filtered out this run: ${esc(exTxt)}.` : "") + (hiddenCount ? ` ${hiddenCount} hidden.` : "") +
        (earlyCount && !state.filters.early ? ` <strong>${earlyCount} more are available now or before mid-December</strong> (likely to be let before January) — tick “Include ones available before mid-Dec” to see them.` : "");
    }
  }

  function fillAreas(d) {
    const sel = $("#f-area");
    const counts = {};
    d.listings.forEach((l) => { if (l.area) counts[l.area] = (counts[l.area] || 0) + 1; });
    const want = Object.keys(counts).sort();
    const have = $$("option", sel).filter((o) => o.value.startsWith("a:")).map((o) => o.value.slice(2));
    if (want.join("|") === have.join("|")) return;
    $$("option", sel).filter((o) => o.value.startsWith("a:")).forEach((o) => o.remove());
    want.forEach((a) => sel.add(new Option(`${a} (${counts[a]})`, "a:" + a)));
    const cur = state.filters.area;
    sel.value = [...sel.options].some((o) => o.value === cur) ? cur : "all";
    if (sel.value !== cur) { state.filters.area = "all"; ls.set("hh.filters", state.filters); }
  }

  // ------------------------------------------------------------------ saved tab

  function stars(item, who) {
    const field = who === "Lloyd" ? "rating_lloyd" : "rating_meg";
    const v = item[field] || 0;
    return `<span class="stars" aria-label="${who}'s rating">${[1, 2, 3, 4, 5].map((n) =>
      `<button class="${n <= v ? "on" : ""}" data-act="rate" data-field="${field}" data-val="${n === v ? 0 : n}" aria-label="${n} star">★</button>`).join("")}</span>`;
  }

  function savedCard(item) {
    const l = item.listing || {};
    const live = state.data && state.data.listings.find((x) => x.id === item.id);
    const cur = live || l;
    const p = photoHtml(cur, "s:" + item.id);
    const viewingVal = item.viewing_at ? new Date(new Date(item.viewing_at).getTime() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "";
    return `
      <article class="saved-card" data-id="${esc(item.id)}">
        <div class="photo">${p.html}</div>
        <div class="saved-main">
          <div class="saved-head">
            <div>
              <div class="price">${money(cur.price)} <small>pcm</small>${live && live.price !== l.price && l.price ? `<s>${money(l.price)}</s>` : ""}</div>
              <p class="title">${esc(cur.title)}</p>
              <p class="addr">${esc(cur.address)}</p>
              ${state.data && !live ? `<div class="gone">No longer showing in search results — may be let</div>` : ""}
            </div>
            ${linksHtml(cur)}
          </div>
          ${factsHtml(cur)}
          <div class="controls">
            <label class="field"><span>Progress</span>
              <select data-act="status">${STATUSES.map(([k, v]) => `<option value="${k}" ${item.status === k ? "selected" : ""}>${v}</option>`).join("")}</select>
            </label>
            <label class="field"><span>Viewing</span>
              <input type="datetime-local" data-act="viewing" value="${viewingVal}">
            </label>
            <div class="field"><span>Lloyd</span>${stars(item, "Lloyd")}</div>
            <div class="field"><span>Meg</span>${stars(item, "Meg")}</div>
          </div>
          <div class="notes">
            ${(item.notes || []).map((n) => `
              <div class="note"><div class="by">${esc(n.author || "?")} · ${ago(n.created_at)}</div>${esc(n.body)}
                <button class="del" data-act="del-note" data-note="${esc(n.id)}" aria-label="Delete note">×</button></div>`).join("")}
            <form class="note-form" data-act="add-note">
              <textarea name="body" rows="1" placeholder="Add a note${state.who ? " as " + esc(state.who) : ""}…" required></textarea>
              <button class="btn small primary" type="submit">Add</button>
            </form>
          </div>
          <div class="meta">Saved by ${esc(item.created_by || "?")} ${ago(item.created_at)}
            · <a href="#" data-act="unsave">Remove</a></div>
        </div>
      </article>`;
  }

  function renderSaved() {
    $("#count-saved").textContent = state.saved.filter((s) => s.status !== "rejected").length;
    const wrap = $("#saved-list");
    $("#saved-empty").hidden = state.saved.length > 0;
    // keep focus/typing safe: don't re-render while typing a note
    const active = document.activeElement;
    if (active && active.closest && active.closest("#saved-list") && (active.tagName === "TEXTAREA" || active.tagName === "INPUT") && active.value) return;
    const order = STATUSES.map(([k]) => k);
    const upcoming = state.saved.filter((s) => s.viewing_at && new Date(s.viewing_at) > new Date()).sort((a, b) => a.viewing_at.localeCompare(b.viewing_at));
    let html = "";
    if (upcoming.length) {
      html += `<div class="group"><h2>📅 Upcoming viewings</h2>${upcoming.map((s) =>
        `<div class="meta" style="font-size:14px;margin-bottom:4px"><strong>${fmtDateTime(s.viewing_at)}</strong> — ${esc((s.listing || {}).address)} (${money((s.listing || {}).price)})</div>`).join("")}</div>`;
    }
    for (const st of order) {
      const items = state.saved.filter((s) => (s.status || "shortlisted") === st);
      if (!items.length) continue;
      html += `<div class="group"><h2>${STATUS_LABEL[st]} <span class="count">${items.length}</span></h2>${items.map(savedCard).join("")}</div>`;
    }
    wrap.innerHTML = html;
  }

  function render() {
    renderProperties();
    renderSaved();
  }

  // ------------------------------------------------------------------ actions

  function snapshot(l) {
    const keep = ["id", "title", "address", "price", "beds", "baths", "image", "images", "links", "station", "tube", "cycleMins", "availability", "tags", "area", "tier", "agent", "deposit", "sizeSqft", "propertyType"];
    return Object.fromEntries(keep.filter((k) => l[k] !== undefined).map((k) => [k, k === "images" ? (l.images || []).slice(0, 8) : l[k]]));
  }

  async function act(fn, ok) {
    try { await fn(); if (ok) toast(ok); } catch (e) { console.error(e); toast("Something went wrong: " + e.message); }
    await loadShared();
    render();
  }

  function requireWho() {
    if (state.who) return true;
    toast("Pick who you are first (top right)");
    $("#who").animate([{ transform: "scale(1)" }, { transform: "scale(1.08)" }, { transform: "scale(1)" }], { duration: 400 });
    return false;
  }

  document.addEventListener("click", async (e) => {
    const nav = e.target.closest("[data-photo]");
    if (nav) {
      const key = nav.dataset.photo;
      const card = nav.closest("[data-id]");
      const id = card.dataset.id;
      const l = (state.data && state.data.listings.find((x) => x.id === id)) || ((state.saved.find((s) => s.id === id) || {}).listing) || {};
      const n = Math.max(1, (l.images || []).length);
      state.photoIdx[key] = ((state.photoIdx[key] || 0) + Number(nav.dataset.dir) + n) % n;
      const p = photoHtml(l, key);
      const photo = nav.closest(".photo");
      const badges = photo.querySelector(".badges");
      photo.innerHTML = p.html;
      if (badges) photo.appendChild(badges);
      return;
    }
    const tab = e.target.closest(".tab");
    if (tab) return showTab(tab.dataset.tab);
    const who = e.target.closest("[data-who]");
    if (who) { state.who = who.dataset.who; ls.set("hh.who", state.who); renderWho(); renderSaved(); return; }

    const btn = e.target.closest("[data-act]");
    if (!btn || btn.tagName === "SELECT" || btn.tagName === "INPUT" || btn.tagName === "FORM") return;
    const card = btn.closest("[data-id]");
    const id = card && card.dataset.id;
    const listing = state.data && state.data.listings.find((x) => x.id === id);
    switch (btn.dataset.act) {
      case "save":
        if (!requireWho()) return;
        btn.disabled = true;
        return act(() => store.save({ id, listing: snapshot(listing), status: "shortlisted", created_by: state.who }), "Saved ♡");
      case "goto-saved":
        return showTab("saved");
      case "hide":
        return act(() => store.hide(id), "Hidden for both of you");
      case "unhide":
        return act(() => store.unhide(id));
      case "unsave":
        e.preventDefault();
        if (!confirm("Remove this from saved? Notes will be deleted too.")) return;
        return act(() => store.remove(id), "Removed");
      case "rate":
        return act(() => store.patch(id, { [btn.dataset.field]: Number(btn.dataset.val) || null }));
      case "del-note":
        if (!confirm("Delete this note?")) return;
        return act(() => store.delNote(btn.dataset.note));
    }
  });

  document.addEventListener("change", (e) => {
    const el = e.target;
    const card = el.closest("[data-id]");
    if (el.dataset.act === "status") return act(() => store.patch(card.dataset.id, { status: el.value }), "Updated");
    if (el.dataset.act === "viewing") {
      const v = el.value ? new Date(el.value).toISOString() : null;
      return act(() => store.patch(card.dataset.id, { viewing_at: v, ...(v ? { status: "viewing" } : {}) }), v ? "Viewing saved" : "Viewing cleared");
    }
    const fid = el.id && el.id.startsWith("f-") ? el.id.slice(2) : null;
    if (fid) {
      state.filters[fid] = el.type === "checkbox" ? el.checked : el.value;
      ls.set("hh.filters", state.filters);
      renderProperties();
    }
  });

  document.addEventListener("submit", (e) => {
    const form = e.target.closest("[data-act='add-note']");
    if (!form) return;
    e.preventDefault();
    if (!requireWho()) return;
    const body = form.body.value.trim();
    if (!body) return;
    const id = form.closest("[data-id]").dataset.id;
    form.body.value = "";
    act(() => store.addNote({ listing_id: id, author: state.who, body }));
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && e.target.matches(".note-form textarea")) {
      e.target.form.requestSubmit();
    }
  });

  function showTab(t) {
    state.tab = t;
    ls.set("hh.tab", t);
    $$(".tab").forEach((b) => { const on = b.dataset.tab === t; b.classList.toggle("active", on); b.setAttribute("aria-selected", on); });
    $("#tab-properties").hidden = t !== "properties";
    $("#tab-saved").hidden = t !== "saved";
    window.scrollTo({ top: 0 });
  }

  function renderWho() {
    $$("[data-who]").forEach((b) => b.classList.toggle("active", b.dataset.who === state.who));
  }

  // ------------------------------------------------------------------ data loading

  async function loadListings() {
    const res = await fetch("data/listings.json?t=" + Date.now(), { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    state.data = await res.json();
  }

  async function refresh(manual) {
    const b = $("#refresh");
    b.disabled = true;
    b.classList.add("spin");
    const before = state.data ? state.data.generatedAt : null;
    try {
      await Promise.all([loadListings(), loadShared()]);
      render();
      if (manual) toast(before && before === state.data.generatedAt ? "Up to date — next search within the hour" : "Loaded the latest search");
    } catch (e) {
      console.error(e);
      if (!state.data) $("#updated").textContent = "No search results yet — the first search runs shortly.";
      if (manual) toast("Couldn't refresh: " + e.message);
    } finally {
      b.disabled = false;
      b.classList.remove("spin");
    }
  }

  function init() {
    // restore filter UI
    for (const [k, v] of Object.entries(state.filters)) {
      const el = $("#f-" + k);
      if (!el) continue;
      if (el.type === "checkbox") el.checked = !!v; else el.value = v;
    }
    renderWho();
    showTab(state.tab);
    if (!hasSupabase) {
      const b = $("#banner");
      b.hidden = false;
      b.innerHTML = "<strong>Heads up:</strong> shared storage isn't connected yet, so saved properties and notes are only kept on this device. Add the Supabase details to <code>docs/config.js</code> to share them with each other.";
    }
    $("#refresh").addEventListener("click", () => refresh(true));
    refresh(false).then(() => {
      // remember this visit (used for "New since last visit" badges next time)
      window.addEventListener("pagehide", () => ls.set("hh.lastVisit", new Date().toISOString()));
    });
    // keep the shared list fresh while the page is open
    setInterval(() => { if (!document.hidden) loadShared().then(renderSaved).then(() => renderProperties()); }, 30000);
    setInterval(() => { if (!document.hidden) loadListings().then(renderProperties).catch(() => {}); }, 10 * 60000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(false); });
  }

  init();
})();
