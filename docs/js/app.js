"use strict";

// ---- Data loading -------------------------------------------------------

const DATA = { shows: [], bands: [], venues: [] };
const byBandSlug = new Map();
const byVenueSlug = new Map();
const showsByBand = new Map();
const showsByVenue = new Map();
const showsByYear = new Map();

async function loadData() {
  const [shows, bands, venues] = await Promise.all([
    fetch("data/shows.json").then((r) => r.json()),
    fetch("data/bands.json").then((r) => r.json()),
    fetch("data/venues.json").then((r) => r.json()),
  ]);
  DATA.shows = shows;
  DATA.bands = bands;
  DATA.venues = venues;

  bands.forEach((b) => byBandSlug.set(b.slug, b));
  venues.forEach((v) => byVenueSlug.set(v.slug, v));
  shows.forEach((s) => {
    push(showsByBand, s.band_slug, s);
    if (s.venue_slug) push(showsByVenue, s.venue_slug, s);
    if (s.year) push(showsByYear, s.year, s);
  });
}

function push(map, key, val) {
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(val);
}

// ---- Helpers ------------------------------------------------------------

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function byDate(a, b) { return (a.date || "").localeCompare(b.date || ""); }

function initials(name) {
  return (name || "?").split(/\s+/).slice(0, 2)
    .map((w) => w[0] || "").join("").toUpperCase();
}

function thumbHtml(wiki, name) {
  if (wiki && wiki.thumbnail) {
    return `<img class="thumb" src="${esc(wiki.thumbnail)}" alt="" loading="lazy">`;
  }
  return `<span class="thumb placeholder">${esc(initials(name))}</span>`;
}

// Render a list of shows. `context` is "band" or "venue" to decide which
// column to emphasize.
function showListHtml(shows, context) {
  const rows = shows.slice().sort(byDate).map((s) => {
    const primary = context === "band"
      ? (s.venue_slug
          ? `<a href="#/venue/${esc(s.venue_slug)}">${esc(s.venue || "Unknown venue")}</a>`
          : esc(s.venue || "Unknown venue"))
      : `<a href="#/band/${esc(s.band_slug)}">${esc(s.band)}</a>`;
    // Only the year view needs the venue appended; band/venue pages don't.
    const secondary = context === "year" && s.venue
      ? ` <span class="where">@ ${esc(s.venue)}</span>` : "";
    return `<div class="show-row">
      <span class="date">${esc(s.date || "—")}</span>
      <span class="who">${primary}${secondary}</span>
      <span class="listen"><a href="${esc(s.url)}" target="_blank" rel="noopener">listen ↗</a></span>
    </div>`;
  }).join("");
  return `<div class="show-list">${rows}</div>`;
}

function setView(node) {
  const app = document.getElementById("app");
  app.replaceChildren(node);
  window.scrollTo(0, 0);
}

// ---- Views --------------------------------------------------------------

// Shared stats overview shown on the Bands landing page and the Timeline.
function statsHtml() {
  const years = [...showsByYear.keys()].filter(Boolean).sort();
  const first = years[0] || "—";
  const last = years[years.length - 1] || "—";
  return `<div class="stats">
    <div class="stat"><div class="num">${DATA.shows.length}</div><div class="label">Shows</div></div>
    <div class="stat"><div class="num">${DATA.bands.length}</div><div class="label">Bands</div></div>
    <div class="stat"><div class="num">${DATA.venues.length}</div><div class="label">Venues</div></div>
    <div class="stat"><div class="num">${first}–${last}</div><div class="label">Years</div></div>
  </div>`;
}

// Top-N bands (by show count) for a given year, richest first.
function topBandsForYear(year, n) {
  const counts = new Map();
  for (const s of showsByYear.get(year) || []) {
    counts.set(s.band_slug, (counts.get(s.band_slug) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([slug]) => byBandSlug.get(slug))
    .filter(Boolean);
}

function miniThumb(band) {
  if (band.wikipedia && band.wikipedia.thumbnail) {
    return `<img class="mini-thumb" src="${esc(band.wikipedia.thumbnail)}" alt="" loading="lazy">`;
  }
  return `<span class="mini-thumb placeholder">${esc(initials(band.name))}</span>`;
}

function renderTimeline() {
  const years = [...showsByYear.keys()].filter(Boolean).sort();
  const maxCount = Math.max(...years.map((y) => showsByYear.get(y).length), 1);

  const cards = years.map((y) => {
    const n = showsByYear.get(y).length;
    const pct = Math.max((n / maxCount) * 100, 1.5);
    const chips = topBandsForYear(y, 6).map((b) =>
      `<a class="mini-band" href="#/band/${esc(b.slug)}" title="${esc(b.name)}">
        ${miniThumb(b)}<span class="mini-name">${esc(b.name)}</span>
      </a>`).join("");
    return `<div class="year-card">
      <div class="year-card-head">
        <a class="year-num" href="#/year/${esc(y)}">${esc(y)}</a>
        <span class="year-count">${n} show${n === 1 ? "" : "s"}</span>
      </div>
      <div class="bar-track"><span class="bar-fill" style="width:${pct.toFixed(1)}%"></span></div>
      <div class="year-bands">${chips}</div>
    </div>`;
  }).join("");

  setView(el(`<div>
    <h1>Timeline</h1>
    <p class="muted">${DATA.shows.length} recorded shows, ${years.length} years. Featuring each year's most-recorded artists.</p>
    ${statsHtml()}
    <div class="year-cards">${cards}</div>
  </div>`));
}

function renderYear(year) {
  const shows = showsByYear.get(year) || [];
  setView(el(`<div>
    <div class="crumb"><a href="#/timeline">Timeline</a> › ${esc(year)}</div>
    <h1>${esc(year)}</h1>
    <p class="muted">${shows.length} show${shows.length === 1 ? "" : "s"}</p>
    ${showListHtml(shows, "year")}
  </div>`));
}

function renderBands() {
  const view = el(`<div>
    <h1>Live Music Archive</h1>
    <p class="muted">Browse ${DATA.bands.length} artists from the Aadam Jacobs collection.</p>
    ${statsHtml()}
    <h2>Bands</h2>
    <div class="controls">
      <input id="band-search" type="search" placeholder="Search bands…" autocomplete="off">
      <select id="band-sort">
        <option value="count">Most shows</option>
        <option value="name">Name (A–Z)</option>
      </select>
    </div>
    <div class="band-grid" id="band-grid"></div>
  </div>`);
  setView(view);

  const grid = view.querySelector("#band-grid");
  const search = view.querySelector("#band-search");
  const sort = view.querySelector("#band-sort");

  function draw() {
    const q = search.value.trim().toLowerCase();
    let list = DATA.bands.filter((b) => !q || b.name.toLowerCase().includes(q));
    if (sort.value === "name") {
      list = list.slice().sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    }
    grid.replaceChildren(...list.slice(0, 600).map((b) => el(
      `<a class="band-card" href="#/band/${esc(b.slug)}">
        ${thumbHtml(b.wikipedia, b.name)}
        <span>
          <span class="name">${esc(b.name)}</span><br>
          <span class="sub">${b.count} show${b.count === 1 ? "" : "s"}</span>
        </span>
      </a>`)));
    if (list.length > 600) {
      grid.append(el(`<p class="muted">Showing first 600 of ${list.length}. Refine your search.</p>`));
    }
  }
  search.addEventListener("input", draw);
  sort.addEventListener("change", draw);
  draw();
}

function renderBand(slug) {
  const band = byBandSlug.get(slug);
  if (!band) return renderNotFound("band");
  const shows = showsByBand.get(slug) || [];
  const w = band.wikipedia;

  const facts = [];
  if (band.genres) facts.push(`<span><strong>Genre:</strong> ${esc(band.genres)}</span>`);
  if (band.origin) facts.push(`<span><strong>Origin:</strong> ${esc(band.origin)}</span>`);
  if (band.formed_year) facts.push(`<span><strong>Formed:</strong> ${esc(band.formed_year)}</span>`);

  const head = w
    ? `<div class="detail-head">
        ${w.thumbnail ? `<img src="${esc(w.thumbnail)}" alt="">` : ""}
        <div class="meta">
          <h1>${esc(band.name)}</h1>
          ${band.origin || band.genres || band.formed_year ? `<div class="facts">${facts.join("")}</div>` : ""}
          <p class="extract">${esc(w.extract || "")}</p>
          ${w.url ? `<a href="${esc(w.url)}" target="_blank" rel="noopener">Read on Wikipedia ↗</a>` : ""}
        </div>
      </div>`
    : `<div class="detail-head"><div class="meta">
        <h1>${esc(band.name)}</h1>
        <p class="muted">No matching Wikipedia article was found for this artist.</p>
      </div></div>`;

  setView(el(`<div>
    <div class="crumb"><a href="#/bands">Bands</a> › ${esc(band.name)}</div>
    ${head}
    <h2>${shows.length} show${shows.length === 1 ? "" : "s"} in the collection</h2>
    ${showListHtml(shows, "band")}
  </div>`));
}

function renderVenue(slug) {
  const venue = byVenueSlug.get(slug);
  if (!venue) return renderNotFound("venue");
  const shows = showsByVenue.get(slug) || [];
  const w = venue.wikipedia;

  const head = w
    ? `<div class="detail-head">
        ${w.thumbnail ? `<img src="${esc(w.thumbnail)}" alt="">` : ""}
        <div class="meta">
          <h1>${esc(venue.name)}</h1>
          ${venue.location ? `<div class="facts"><span><strong>Location:</strong> ${esc(venue.location)}</span></div>` : ""}
          <p class="extract">${esc(w.extract || "")}</p>
          ${w.url ? `<a href="${esc(w.url)}" target="_blank" rel="noopener">Read on Wikipedia ↗</a>` : ""}
        </div>
      </div>`
    : `<div class="detail-head"><div class="meta">
        <h1>${esc(venue.name)}</h1>
        <p class="muted">No matching Wikipedia article was found for this venue.</p>
      </div></div>`;

  setView(el(`<div>
    <div class="crumb"><a href="#/timeline">Timeline</a> › ${esc(venue.name)}</div>
    ${head}
    <h2>${shows.length} show${shows.length === 1 ? "" : "s"} here</h2>
    ${showListHtml(shows, "venue")}
  </div>`));
}

function renderNotFound(kind) {
  setView(el(`<div>
    <h1>Not found</h1>
    <p class="muted">That ${esc(kind)} isn't in the collection. <a href="#/">Back to the bands</a>.</p>
  </div>`));
}

// ---- Router -------------------------------------------------------------

function route() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [section, param] = hash.split("/").map(decodeURIComponent);
  switch (section) {
    case "timeline": return renderTimeline();
    case "band": return renderBand(param);
    case "venue": return renderVenue(param);
    case "year": return renderYear(param);
    case "bands":
    default: return renderBands();
  }
}

window.addEventListener("hashchange", route);

(async function init() {
  try {
    await loadData();
    route();
  } catch (err) {
    document.getElementById("app").replaceChildren(
      el(`<p class="muted">Could not load the collection data. ${esc(err.message || err)}</p>`));
  }
})();
