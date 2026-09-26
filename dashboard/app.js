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
const DEFAULT_CENTER = [37.7749, -122.4194]; // matches this project's own convention

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
      marker.bindTooltip(`driver ${driver.id.slice(0, 8)} — ${driver.status}`);
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

async function refreshStats() {
  const res = await fetch("/stats");
  if (!res.ok) return;
  const stats = await res.json();

  const grid = document.getElementById("stats-grid");
  grid.innerHTML = "";
  const rows = [
    ["Available drivers", stats.available_drivers],
    ["Busy drivers", stats.busy_drivers],
    ["Active rides", stats.active_rides],
    ["Completed rides", stats.completed_rides],
    ["Cancelled rides", stats.cancelled_rides],
    ["Failed matches", stats.failed_matches],
    ["Throughput (matches/min)", stats.match_throughput_per_minute],
    ["p50 latency (ms)", stats.p50_latency_ms.toFixed(1)],
    ["p95 latency (ms)", stats.p95_latency_ms.toFixed(1)],
    ["p99 latency (ms)", stats.p99_latency_ms.toFixed(1)],
  ];
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    grid.append(dt, dd);
  }

  const tbody = document.querySelector("#surge-table tbody");
  tbody.innerHTML = "";
  for (const [zoneId, surge] of Object.entries(stats.surge_by_zone)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${zoneId.slice(0, 10)}</td><td>${surge.demand}</td><td>${surge.supply}</td><td>${surge.multiplier.toFixed(2)}x</td>`;
    tbody.append(tr);
  }
}

// Deterministic color per ride id (a simple string hash -> hue), so every
// event belonging to the same ride reads as the same color in the log --
// lets you visually trace one ride's full sequence (RIDE_REQUESTED ->
// CANDIDATES_FOUND -> LOCK_ACQUIRED/LOCK_FAILED* -> DRIVER_ASSIGNED, etc.)
// even while other rides' events are interleaved in between it, which is
// the normal, expected case under real concurrent load.
function rideColor(rideId) {
  let hash = 0;
  for (let i = 0; i < rideId.length; i++) {
    hash = rideId.charCodeAt(i) + ((hash << 5) - hash);
  }
  return `hsl(${Math.abs(hash) % 360}, 70%, 60%)`;
}

function logEvent(event) {
  const log = document.getElementById("event-log");
  const li = document.createElement("li");
  li.className = `event-${event.type}`;
  const time = new Date(event.timestamp).toLocaleTimeString();
  const rideId = event.data.ride_id;
  const detail = Object.entries(event.data)
    .filter(([key]) => key !== "ride_id")
    .map(([key, value]) => `${key}=${value}`)
    .join(" ");
  const rideTag = rideId
    ? `<span class="ride-tag" style="color:${rideColor(rideId)}" title="${rideId}">${rideId.slice(0, 8)}</span>`
    : `<span class="ride-tag">--------</span>`;
  li.innerHTML = `${rideTag} <span class="event-type">${event.type}</span> <span>${time}</span> <span>${detail}</span>`;
  log.prepend(li);
  while (log.children.length > MAX_LOG_ENTRIES) {
    log.removeChild(log.lastChild);
  }
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
