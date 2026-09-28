// Slice 8 dashboard. Deliberate split, matching the backend design:
// - REST polling (GET /drivers, GET /stats) drives "current state" -- the
//   map's driver markers and the stats/surge panel. Simpler and more robust
//   than incrementally reconstructing every aggregate from a raw event
//   stream, since this state only needs to be eventually fresh.
// - The WebSocket (/ws) is purely an append-only stream for the event log
//   and transient map animations (a pulse at a pickup point, a flash-line on
//   match) -- never treated as a source of truth for current state.

const POLL_INTERVAL_MS = 3000;
const MAX_LOG_ENTRIES = 200;
// Initial map view only, before any driver exists; the map re-fits to the drivers
// on the first poll that returns any. Pune, to match the demo command in
// scripts/demo.md (the simulation's own location comes from its --area-center-* flags).
const DEFAULT_CENTER = [18.5204, 73.8567];

const map = L.map("map").setView(DEFAULT_CENTER, 12);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

const driverMarkers = new Map(); // driver id -> L.CircleMarker
const transientLayer = L.layerGroup().addTo(map);

function driverColor(status) {
  return status === "available" ? "#35c46b" : "#e0553f";
}

async function refreshDrivers() {
  const res = await fetch("/drivers");
  if (!res.ok) return;
  const driversList = await res.json();

  const seen = new Set();
  for (const driver of driversList) {
    seen.add(driver.id);
    const latlng = [driver.current_lat, driver.current_lng];
    let marker = driverMarkers.get(driver.id);
    if (!marker) {
      marker = L.circleMarker(latlng, {
        radius: 6,
        color: driverColor(driver.status),
        fillColor: driverColor(driver.status),
        fillOpacity: 0.85,
        weight: 1,
      }).addTo(map);
      marker.bindTooltip(driver.status === "available" ? "Available" : "Busy");
      driverMarkers.set(driver.id, marker);
    } else {
      marker.setLatLng(latlng);
      marker.setStyle({ color: driverColor(driver.status), fillColor: driverColor(driver.status) });
    }
  }
  // Drop markers for drivers no longer returned (shouldn't normally happen,
  // drivers aren't deleted -- defensive, not load-bearing).
  for (const [id, marker] of driverMarkers) {
    if (!seen.has(id)) {
      map.removeLayer(marker);
      driverMarkers.delete(id);
    }
  }

  if (!refreshDrivers.hasFitBounds && driversList.length > 0) {
    const bounds = L.latLngBounds(driversList.map((d) => [d.current_lat, d.current_lng]));
    map.fitBounds(bounds.pad(0.2));
    refreshDrivers.hasFitBounds = true;
  }
}

function setStat(id, value) {
  document.getElementById(id).textContent = value;
}

// Presentation-only grouping of the same GET /stats payload the dashboard has
// always fetched -- no new fields, no calculation changes. The 5 "hero" ones
// are what an interviewer should be able to read at a glance; everything else
// that was already rendered before this pass stays available, just tucked
// under a collapsed <details> instead of competing for attention.
function renderHeroStats(stats) {
  const hero = document.getElementById("hero-stats");
  hero.innerHTML = "";
  const tiles = [
    ["Active rides", stats.active_rides],
    ["Completed rides", stats.completed_rides],
    ["No driver available", stats.failed_matches],
    ["Throughput /min", stats.match_throughput_per_minute],
    ["Match latency p95", `${stats.p95_latency_ms.toFixed(0)} ms`],
  ];
  for (const [label, value] of tiles) {
    const tile = document.createElement("div");
    tile.className = "hero-tile";
    tile.innerHTML = `<div class="hero-tile__value">${value}</div><div class="hero-tile__label">${label}</div>`;
    hero.append(tile);
  }
}

function renderMoreStats(stats) {
  const grid = document.getElementById("stats-grid");
  grid.innerHTML = "";
  const rows = [
    ["Available drivers", stats.available_drivers],
    ["Busy drivers", stats.busy_drivers],
    ["Cancelled rides", stats.cancelled_rides],
    ["p50 latency (ms)", stats.p50_latency_ms.toFixed(1)],
    ["p99 latency (ms)", stats.p99_latency_ms.toFixed(1)],
  ];
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    grid.append(dt, dd);
  }
}

// Stable, session-local "Zone 1", "Zone 2", ... label per H3 zone id, assigned
// the first time a zone is seen -- so the same real-world zone always reads
// as the same short label for the rest of the session, without ever showing
// the raw H3 string in the visible UI.
const zoneLabels = new Map();
function zoneLabel(zoneId) {
  if (!zoneLabels.has(zoneId)) {
    zoneLabels.set(zoneId, `Zone ${zoneLabels.size + 1}`);
  }
  return zoneLabels.get(zoneId);
}

// Highest multiplier observed at any poll so far this browser session --
// display-only client-side tracking, not a new backend metric (resets on
// page reload, same as every other piece of this dashboard's local state).
let maxSurgeSeenThisSession = 1.0;

function renderSurge(stats) {
  const activeZones = Object.entries(stats.surge_by_zone).filter(([, s]) => s.demand > 0);
  const peakNow = activeZones.length > 0 ? Math.max(...activeZones.map(([, s]) => s.multiplier)) : 1.0;
  maxSurgeSeenThisSession = Math.max(maxSurgeSeenThisSession, peakNow);

  document.getElementById("surge-summary").innerHTML = `
    <div class="surge-summary__item"><span class="surge-summary__value">${activeZones.length}</span> active zone${activeZones.length === 1 ? "" : "s"}</div>
    <div class="surge-summary__item"><span class="surge-summary__value">${peakNow.toFixed(2)}x</span> peak now</div>
    <div class="surge-summary__item"><span class="surge-summary__value">${maxSurgeSeenThisSession.toFixed(2)}x</span> highest this session</div>
  `;

  const tbody = document.querySelector("#surge-table tbody");
  tbody.innerHTML = "";
  for (const [zoneId, surge] of activeZones.sort((a, b) => b[1].multiplier - a[1].multiplier)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${zoneLabel(zoneId)}</td><td>${surge.demand}</td><td>${surge.supply}</td><td>${surge.multiplier.toFixed(2)}x</td>`;
    tbody.append(tr);
  }
}

async function refreshStats() {
  const res = await fetch("/stats");
  if (!res.ok) return;
  const stats = await res.json();
  renderHeroStats(stats);
  renderMoreStats(stats);
  renderSurge(stats);
}

// Deterministic color per ride id (a simple string hash -> hue) -- applied to
// that ride's "Ride #N" tag below, so the same ride reads as the same color
// throughout the log too, not just the same number. The id itself is never
// shown, only used to pick a hue.
function rideColor(rideId) {
  let hash = 0;
  for (let i = 0; i < rideId.length; i++) {
    hash = rideId.charCodeAt(i) + ((hash << 5) - hash);
  }
  return `hsl(${Math.abs(hash) % 360}, 70%, 60%)`;
}

// Stable, session-local "Ride #1", "Ride #2", ... label per ride_id --
// same pattern as zoneLabel() above. Assigned the first time a ride_id is
// seen in an event's data, from the real ride_id field (never from
// timestamp or arrival order), so the same ride always gets the same
// label for the rest of the session, and different rides always get
// different labels. The raw UUID is only ever used as this Map's key --
// never rendered, never put in a title/tooltip attribute.
const rideLabels = new Map();
function rideLabel(rideId) {
  if (!rideLabels.has(rideId)) {
    rideLabels.set(rideId, `Ride #${rideLabels.size + 1}`);
  }
  return rideLabels.get(rideId);
}

// Plain-language label per event type, for a demo audience that shouldn't
// need to know this project's internal event-type vocabulary. Two of the 8
// event types the backend publishes (CANDIDATES_FOUND, LOCK_ACQUIRED) are
// intentionally not given a log line at all -- both fire on every match
// attempt and add no story of their own beyond what DRIVER_ASSIGNED already
// implies, and were the biggest source of log volume. LOCK_FAILED is kept
// (renamed) because it's the one line that visibly shows two requests racing
// for the same driver -- this project's core concurrency story -- and it only
// fires on real contention, not on every request.
//
// Each entry says how to attach the "Ride #N" tag: `suffix` reads as
// "Ride #N <suffix>" (the ride-lifecycle verbs -- requested/completed/
// cancelled all read naturally straight after the tag); `clause` reads as
// "Ride #N — <clause>" (an independent statement about the ride, dash-joined).
// The wording of every label is unchanged from before this pass -- only
// "Ride requested"/"Ride completed"/"Ride cancelled" drop their now-redundant
// leading "Ride " once the "Ride #N" tag already supplies it.
const EVENT_LABELS = {
  RIDE_REQUESTED: { suffix: "requested" },
  LOCK_FAILED: { clause: "Driver busy — retrying next candidate" },
  DRIVER_ASSIGNED: { clause: "Driver matched" },
  NO_DRIVER_AVAILABLE: { clause: "No driver available" },
  RIDE_COMPLETED: { suffix: "completed" },
  RIDE_CANCELLED: { suffix: "cancelled" },
};

function formatTime(isoTimestamp) {
  const d = new Date(isoTimestamp);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function logEvent(event) {
  const entry = EVENT_LABELS[event.type];
  if (!entry) return; // CANDIDATES_FOUND / LOCK_ACQUIRED: intentionally not logged, see above

  const log = document.getElementById("event-log");
  const li = document.createElement("li");
  li.className = `event-${event.type}`;

  const rideId = event.data.ride_id;
  let text;
  if (rideId) {
    const tag = `<span class="ride-tag" style="color:${rideColor(rideId)}">${rideLabel(rideId)}</span>`;
    text = entry.suffix ? `${tag} ${entry.suffix}` : `${tag} — ${entry.clause}`;
  } else {
    // Defensive only -- every currently-rendered event type always carries a
    // ride_id in practice; falls back to the plain clause/suffix with no tag.
    text = entry.clause || entry.suffix;
  }

  li.innerHTML = `<span class="event-time">${formatTime(event.timestamp)}</span> <span class="event-label">${text}</span>`;
  // Chronological, oldest at top / newest at bottom -- a ride's lifecycle then
  // reads top-to-bottom in the order it actually happened, instead of the
  // newest-first ordering a plain prepend() gives. Trim from the top (the
  // oldest line) to match, still bounded to MAX_LOG_ENTRIES.
  log.append(li);
  while (log.children.length > MAX_LOG_ENTRIES) {
    log.removeChild(log.firstChild);
  }
  // #event-log is a fixed-height, overflow-y:auto box; appending at the
  // bottom doesn't auto-reveal itself, so pin the scroll position to the
  // newest line on every new event, keeping the live feed in view.
  log.scrollTop = log.scrollHeight;
}

function pulseAt(lat, lng, color) {
  const marker = L.circleMarker([lat, lng], {
    radius: 10,
    color,
    fillColor: color,
    fillOpacity: 0.4,
    weight: 2,
  }).addTo(transientLayer);
  setTimeout(() => transientLayer.removeLayer(marker), 2500);
}

function flashMatch(driverId, lat, lng) {
  const driverMarker = driverMarkers.get(driverId);
  if (!driverMarker) return;
  const line = L.polyline([driverMarker.getLatLng(), [lat, lng]], {
    color: "#35c46b",
    weight: 2,
    dashArray: "4,4",
  }).addTo(transientLayer);
  setTimeout(() => transientLayer.removeLayer(line), 2500);
}

function handleEvent(event) {
  logEvent(event);
  const { type, data } = event;
  if (type === "RIDE_REQUESTED") {
    pulseAt(data.pickup_lat, data.pickup_lng, "#4da3ff");
  } else if (type === "DRIVER_ASSIGNED") {
    flashMatch(data.driver_id, data.pickup_lat, data.pickup_lng);
  } else if (type === "NO_DRIVER_AVAILABLE") {
    pulseAt(data.pickup_lat, data.pickup_lng, "#e0553f");
  }
}

function connectWebSocket() {
  const statusEl = document.getElementById("connection-status");
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${location.host}/ws`);

  ws.onopen = () => {
    statusEl.textContent = "live";
    statusEl.className = "status status--open";
  };
  ws.onclose = () => {
    statusEl.textContent = "disconnected — retrying…";
    statusEl.className = "status status--closed";
    setTimeout(connectWebSocket, 2000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (msg) => handleEvent(JSON.parse(msg.data));
}

refreshDrivers();
refreshStats();
connectWebSocket();
setInterval(refreshDrivers, POLL_INTERVAL_MS);
setInterval(refreshStats, POLL_INTERVAL_MS);
