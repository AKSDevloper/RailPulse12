/* ==========================================================================
   SIH26028 dashboard frontend. Plain JS, no framework, no build step.
   Talks to the FastAPI backend at the same origin it's served from.
   ========================================================================== */

const COLORS = {
  accent: "#3FD6C0", accent2: "#7C9CFF", amber: "#F2B84B",
  red: "#FF6B6B", green: "#3ED598", faint: "#4E5A70", muted: "#8592A8", text: "#E8ECF4",
};

const state = {
  route: null,          // { stations, blocks, route_km }
  timetable: [],
  faultTypes: {},
  modelInfo: null,
  latest: null,          // most recent WS payload
  page: "overview",
  ctlSelectedId: null,
  paxSelectedId: null,
  feedSelectedId: null,
};

/* ------------------------------------------------------------------ utils */

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function fmtMinShort(min) {
  if (min == null || Number.isNaN(min)) return "--";
  const sign = min < 0 ? "-" : "";
  const v = Math.round(Math.abs(min));
  const h = Math.floor(v / 60), m = v % 60;
  return h > 0 ? `${sign}${h}h ${m}m` : `${sign}${m}m`;
}

function delayBadge(delay) {
  let c = COLORS.green, l = "On time";
  if (delay > 15) { c = COLORS.red; l = `Delayed ${Math.round(delay)}m`; }
  else if (delay > 4) { c = COLORS.amber; l = `Delayed ${Math.round(delay)}m`; }
  else if (delay < -1) { l = `Early ${Math.round(-delay)}m`; }
  return `<span class="badge" style="color:${c};background:${c}1A;border-color:${c}88">${l}</span>`;
}

function statusPill(status) {
  const map = { clear: [COLORS.green, "Clear"], restricted: [COLORS.amber, "Restricted"], blocked: [COLORS.red, "Blocked"] };
  const [c, l] = map[status] || map.clear;
  return `<span class="pill" style="color:${c};border-color:${c}"><span class="dot" style="background:${c}"></span>${l}</span>`;
}

/* ------------------------------------------------------------- SVG charts */

function trackSchematicSVG(stations, blocks, trains, selectedId, height = 230, onlySelected = false) {
  const W = 1000, padX = 48, centerY = height / 2 + 18;
  const routeKm = stations[stations.length - 1].km;
  const kmToX = (km) => padX + (km / routeKm) * (W - padX * 2);
  const visibleTrains = trains.filter((t) => t.status !== "scheduled" && (!onlySelected || t.id === selectedId));
  let svg = `<svg viewBox="0 0 ${W} ${height}" width="100%" height="${height}" class="rail-schematic" style="overflow:visible">`;
  svg += `<rect x="0" y="0" width="${W}" height="${height}" rx="12" fill="rgba(5,15,28,.32)"/>`;

  blocks.forEach((b) => {
    const x1 = kmToX(b.from_km), x2 = kmToX(b.to_km);
    const color = b.status === "clear" ? "#31506A" : b.status === "restricted" ? COLORS.amber : COLORS.red;
    const dash = b.status === "restricted" ? `stroke-dasharray="9 7"` : "";
    svg += `<line x1="${x1}" y1="${centerY}" x2="${x2}" y2="${centerY}" stroke="${color}" stroke-width="${b.status === "clear" ? 5 : 8}" ${dash} stroke-linecap="round"/>`;
    svg += `<text x="${(x1+x2)/2}" y="${centerY-13}" text-anchor="middle" font-size="10" fill="#718aa0" font-family="monospace">${b.mps_kmh} km/h</text>`;
    svg += `<text x="${(x1+x2)/2}" y="${centerY+58}" text-anchor="middle" font-size="8" fill="#415A70">${b.id}</text>`;
  });

  stations.forEach((stn) => {
    const x = kmToX(stn.km);
    svg += `<circle cx="${x}" cy="${centerY}" r="7" fill="#08111E" stroke="#DCEAF5" stroke-width="2"/>`;
    svg += `<circle cx="${x}" cy="${centerY}" r="2.5" fill="#3FD6C0"/>`;
    svg += `<text x="${x}" y="${centerY+24}" text-anchor="middle" font-size="11" fill="#E8ECF4" font-weight="700">${stn.id}</text>`;
    svg += `<text x="${x}" y="${centerY+39}" text-anchor="middle" font-size="9" fill="#8592A8">${stn.name}</text>`;
  });

  // Put every live train directly ON the same rail line. Each train gets a
  // short vertical leader line and its number above the line, like a railway
  // control schematic. Nearby labels are lifted to separate levels so the
  // numbers never visually merge.
  const markers = visibleTrains.map(t => ({t, x: kmToX(t.position_km), level: 0})).sort((a,b)=>a.x-b.x);
  // Labels must never visually stick together even when trains are close.
  // Keep the dot on the rail, but lift each nearby number/leader to a
  // separate fixed level. Four levels gives enough room on a dense section.
  markers.forEach((m, i) => {
    const recent = markers.slice(Math.max(0, i - 3), i);
    const used = recent.filter(p => Math.abs(m.x - p.x) < 72).map(p => p.level);
    let level = 0;
    while (used.includes(level) && level < 3) level += 1;
    m.level = level;
  });
  markers.forEach(({t, x, level}) => {
    const delay = Number(t.delay_min || 0);
    const color = delay > 15 ? COLORS.red : delay > 4 ? COLORS.amber : COLORS.accent;
    const isSel = t.id === selectedId;
    const radius = isSel ? 8 : 6;
    const lineTop = centerY - 28 - level * 21;
    const labelY = lineTop - 5;
    svg += `<g data-train-id="${t.id}" class="train-marker" style="cursor:pointer" aria-label="Train ${t.number}">`;
    svg += `<line x1="${x}" y1="${centerY-3}" x2="${x}" y2="${lineTop}" stroke="${color}" stroke-width="2" opacity=".9"/>`;
    svg += `<circle cx="${x}" cy="${centerY}" r="${radius}" fill="${color}" stroke="#07111E" stroke-width="2.5"/>`;
    svg += `<text x="${x}" y="${labelY}" text-anchor="middle" font-size="${isSel ? 10 : 9}" fill="${color}" font-family="monospace" font-weight="800">${t.number}</text>`;
    if (isSel) svg += `<circle cx="${x}" cy="${centerY}" r="12" fill="none" stroke="${color}" stroke-width="1" opacity=".5"/>`;
    svg += `</g>`;
  });
  svg += `</svg>`;
  return svg;
}

function lineChartSVG(data, key, { color = COLORS.accent2, unit = "", height = 170, fill = true } = {}) {
  const W = 560;
  const points = data.filter((d) => d[key] != null);
  if (points.length < 2) return `<div class="empty">Collecting data&hellip;</div>`;
  const xs = points.map((d) => d.t);
  const ys = points.map((d) => d[key]);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  // Delay charts use a zero baseline; delay is never shown as negative.
  const minY = 0, maxY = Math.max(...ys, 0) * 1.15 || 1;
  const px = (x) => 6 + ((x - minX) / Math.max(1, maxX - minX)) * (W - 12);
  const py = (y) => height - 20 - ((y - minY) / Math.max(0.001, maxY - minY)) * (height - 34);

  const path = points.map((d, i) => `${i === 0 ? "M" : "L"} ${px(d.t).toFixed(1)} ${py(d[key]).toFixed(1)}`).join(" ");
  const areaPath = `${path} L ${px(points[points.length - 1].t).toFixed(1)} ${height - 20} L ${px(points[0].t).toFixed(1)} ${height - 20} Z`;

  let svg = `<svg viewBox="0 0 ${W} ${height}" width="100%" height="${height}">`;
  svg += `<line x1="0" y1="${height - 20}" x2="${W}" y2="${height - 20}" stroke="${COLORS.faint}" stroke-opacity="0.3"/>`;
  if (fill) svg += `<path d="${areaPath}" fill="${color}" fill-opacity="0.12" stroke="none"/>`;
  svg += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2"/>`;
  const last = points[points.length - 1];
  svg += `<circle cx="${px(last.t)}" cy="${py(last[key])}" r="3.5" fill="${color}"/>`;
  svg += `<text x="${px(last.t) - 6}" y="${py(last[key]) - 8}" text-anchor="end" font-size="11" fill="${color}" font-family="monospace">${last[key]}${unit}</text>`;
  svg += `</svg>`;
  return svg;
}

function barChartSVG(items, { valueKey = "value", labelKey = "name", height = 180, colorFn } = {}) {
  const W = 560;
  const max = Math.max(...items.map((d) => d[valueKey]), 1);
  const barW = (W - 20) / items.length - 8;
  let svg = `<svg viewBox="0 0 ${W} ${height}" width="100%" height="${height}">`;
  items.forEach((d, i) => {
    const x = 10 + i * ((W - 20) / items.length);
    const h = (d[valueKey] / max) * (height - 40);
    const y = height - 24 - h;
    const color = colorFn ? colorFn(d) : COLORS.accent;
    svg += `<rect x="${x}" y="${y}" width="${barW}" height="${h}" rx="4" fill="${color}"/>`;
    svg += `<text x="${x + barW / 2}" y="${height - 24 - h - 6}" text-anchor="middle" font-size="11" fill="${COLORS.text}" font-family="monospace">${d[valueKey]}</text>`;
    svg += `<text x="${x + barW / 2}" y="${height - 8}" text-anchor="middle" font-size="9.5" fill="${COLORS.muted}">${d[labelKey]}</text>`;
  });
  svg += `</svg>`;
  return svg;
}

function hBarList(items) {
  // Feature importances are already percentages of total model importance.
  // Do not normalize each bar against the largest feature: that made every
  // feature look nearly 100% in the ML screen even when its real share was small.
  return `<div style="display:flex;flex-direction:column;gap:10px;">` + items.map((d) => `
    <div>
      <div style="display:flex;justify-content:space-between;font-size:11.5px;margin-bottom:4px;">
        <span style="color:${COLORS.text}">${d.name}</span>
        <span class="mono" style="color:${COLORS.muted}">${Number(d.value).toFixed(1)}%</span>
      </div>
      <div style="height:7px;background:rgba(255,255,255,0.06);border-radius:4px;">
        <div style="height:100%;width:${Math.max(0, Math.min(100, Number(d.value)))}%;background:${COLORS.accent2};border-radius:4px;"></div>
      </div>
    </div>`).join("") + `</div>`;
}

/* --------------------------------------------------------------- fetches */

async function getJson(url, fallback) {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} returned ${response.status}`);
    return await response.json();
  } catch (error) {
    console.warn(`RailPulse: ${url} unavailable`, error);
    return fallback;
  }
}

async function loadStaticData() {
  const [route, timetable, faultTypes, modelInfo] = await Promise.all([
    getJson("/api/route", {stations:[],blocks:[],route_km:440}),
    getJson("/api/timetable", []),
    getJson("/api/fault-types", {}),
    getJson("/api/model-info", {model_name:"ML model unavailable",training_samples:0,metrics:{rmse:0,mae:0,r2:0},feature_importances:[]}),
  ]);
  state.route = route;
  state.timetable = timetable;
  state.faultTypes = faultTypes;
  state.modelInfo = modelInfo;

  const blockOptions = (route.blocks || []).map((b) => `<option value="${b.id}">${b.from_name} &rarr; ${b.to_name}</option>`).join("");
  const typeOptions = Object.entries(faultTypes || {}).map(([k, v]) => `<option value="${k}">${v.label}</option>`).join("");
  document.getElementById("fault-block").innerHTML = blockOptions;
  document.getElementById("fault-type").innerHTML = typeOptions;
  document.getElementById("ov-fault-block").innerHTML = blockOptions;
  document.getElementById("ov-fault-type").innerHTML = typeOptions;
  renderMLStatic();
}

/* ------------------------------------------------------------- Live updates
   Vercel serverless functions do not keep a WebSocket connection alive.
   Use the same /api/state endpoint with short polling instead. This also
   works when the dashboard is opened locally with uvicorn. */

let pollBusy = false;
async function pollState() {
  if (pollBusy) return;
  pollBusy = true;
  const dot = document.getElementById("conn-dot");
  const label = document.getElementById("conn-label");
  try {
    const response = await fetch(`/api/state?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`state ${response.status}`);
    const payload = await response.json();
    state.latest = payload;
    dot.className = "conn-dot live";
    label.textContent = "live";
    renderAll();
  } catch (error) {
    console.warn("RailPulse state unavailable", error);
    dot.className = "conn-dot down";
    label.textContent = "reconnecting...";
  } finally {
    pollBusy = false;
  }
}

function connectWS() {
  pollState();
  setInterval(pollState, 1000);
}

/* ------------------------------------------------------------- rendering */

function renderAll() {
  if (!state.latest || !state.route) return;
  document.getElementById("sim-clock").textContent = state.latest.clock;
  document.getElementById("sim-cycle").textContent = `cycle ${state.latest.cycle}`;

  renderOverview();
  renderController();
  renderPassenger();
  renderMLLive();
  renderFaults();
  renderTimetable();
}

function renderOverview() {
  const L = state.latest;
  const total = L.trains.length;
  const active = L.kpis.active_trains;
  const atRisk = L.blocks.filter((b) => b.status !== "clear").length;
  const confidence = L.ml_monitor.rolling_accuracy ?? 0;
  const running = L.trains.filter((t) => t.status === "running");

  document.getElementById("ov-total-trains").textContent = total;
  document.getElementById("ov-trains-sub").textContent = `${active} active · ${total - active} scheduled/completed`;
  document.getElementById("ov-ontime").innerHTML = `${L.kpis.on_time_pct}<span>%</span>`;
  document.getElementById("ov-ontime-sub").textContent = `${L.kpis.on_time_pct >= 80 ? "+" : ""}${L.kpis.on_time_pct}% of dispatched services on time`;
  document.getElementById("ov-avg-delay").textContent = `${L.kpis.avg_delay_min.toFixed(2)} min`;
  document.getElementById("ov-block-risk").textContent = atRisk;
  document.getElementById("ov-risk-sub").textContent = `${L.blocks.filter((b) => b.status === "restricted").length} restricted · ${L.blocks.filter((b) => b.status === "blocked").length} blocked`;
  document.getElementById("ov-model-confidence").textContent = `${confidence}%`;
  document.getElementById("ov-model-sub").textContent = `MAE ${L.ml_monitor.rolling_mae ?? "--"} min`;
  document.getElementById("ov-route-distance").textContent = `Route distance ${state.route.route_km} km`;
  document.getElementById("ov-live-clock").textContent = `Simulation ${L.clock}`;
  document.getElementById("topbar-updated").textContent = `Last updated ${L.clock}`;

  const ovMap = document.getElementById("ov-map");
  ovMap.innerHTML = trackSchematicSVG(state.route.stations, L.blocks, L.trains, null, 240);
  ovMap.querySelectorAll("[data-train-id]").forEach((g) => g.addEventListener("click", () => {
    state.ctlSelectedId = g.getAttribute("data-train-id");
    document.querySelector('[data-page="controller"]').click();
  }));
  document.getElementById("ov-delay-chart").innerHTML = lineChartSVG(L.history, "avg_delay", { color: COLORS.accent, unit: " min", height: 150 });

  const congestion = L.blocks.map((b) => ({ name: `${b.from_id}→${b.to_id}`, value: b.occupancy }));
  document.getElementById("ov-congestion-chart").innerHTML = barChartSVG(congestion, {
    height: 150,
    colorFn: (d) => d.value > 1 ? COLORS.amber : COLORS.accent,
  });
  document.getElementById("ov-congestion-summary").innerHTML = `
    <div><span class="summary-dot green"></span><b>${L.blocks.filter((b) => b.status === "clear").length}</b> Clear</div>
    <div><span class="summary-dot amber"></span><b>${L.blocks.filter((b) => b.status === "restricted").length}</b> Restricted</div>
    <div><span class="summary-dot red"></span><b>${L.blocks.filter((b) => b.status === "blocked").length}</b> Blocked</div>`;

  renderLiveEvents(L);
  renderKeyInsights(L, running);
  renderQuickStats(L);
  renderFaultList("ov-fault-list", L.faults);
  renderStationLog("ov-station-log", L.station_log);
}

function renderLiveEvents(L) {
  const events = [];
  L.faults.forEach(f => {
    const b = state.route.blocks.find(x => x.id === f.block_id);
    events.push({id:null,time:f.start_min,tone:f.effect === "blocked" ? "red":"amber",title:f.label,sub:`${b ? b.from_name+" → "+b.to_name : f.block_id} · clears in ${Math.round(f.clears_in_min)} min`});
  });
  L.trains.filter(t => t.status !== "scheduled" && (Number(t.delay_min)>4 || t.conflict_status === "caution")).forEach(t => events.push({id:t.id,time:L.sim_min,tone:Number(t.delay_min)>15?"red":"amber",title:`${t.number} · ${t.name}`,sub:`${t.delay_reason || "Traffic adjustment"} · gap ${t.safety_gap_km>=999?'clear':t.safety_gap_km.toFixed(1)+' km'}`}));
  events.sort((a,b)=>b.time-a.time); const shown=events.slice(0,8);
  document.getElementById("ov-event-count").textContent=shown.length;
  document.getElementById("ov-live-events").innerHTML=shown.length?shown.map(e=>`<button class="event-row clickable-event event-tone-${e.tone}" data-event-train="${e.id||''}"><span class="event-icon ${e.tone}">${e.tone==='red'?'△':'●'}</span><span class="event-time">${L.clock}</span><span class="event-copy"><b>${e.title}</b><span>${e.sub}</span></span><span class="event-action">${e.id?'Details':'Active'}</span></button>`).join(""):`<div class="empty">No active events. Network operating normally.</div>`;
  document.querySelectorAll("#ov-live-events [data-event-train]").forEach(btn=>btn.addEventListener("click",()=>{if(btn.dataset.eventTrain){state.ctlSelectedId=btn.dataset.eventTrain;document.querySelector('[data-page="controller"]').click();}}));
}

function renderKeyInsights(L, running) {
  const confidence = L.ml_monitor.rolling_accuracy ?? 0;
  document.getElementById("ov-ring-value").textContent = `${confidence}%`;
  const predictions = running.map((t) => t.block_prediction?.predicted_min).filter((v) => typeof v === "number");
  const predicted = predictions.length ? predictions.reduce((a, b) => a + b, 0) / predictions.length : 0;
  document.getElementById("ov-predicted-delay").textContent = `+${predicted.toFixed(1)} min`;

  const risks = [...L.blocks].map((b) => ({ ...b, score: (b.status === "blocked" ? 100 : b.status === "restricted" ? 60 : 0) + b.occupancy * 20 })).sort((a, b) => b.score - a.score).slice(0, 3);
  document.getElementById("ov-risk-list").innerHTML = risks.map((b, i) => `<div class="risk-row"><span>${i + 1}. ${b.from_name} → ${b.to_name}</span><b class="risk-value ${b.status === "blocked" ? "red" : "amber"}">${b.status === "clear" ? `${b.occupancy} train` : b.status}</b></div>`).join("");
}

function renderQuickStats(L) {
  const totalBlocks = L.blocks.length;
  const occupied = L.blocks.filter((b) => b.occupancy > 0).length;
  document.getElementById("ov-quickstats").innerHTML = `
    <div class="stat-item"><span>⎋</span><label>Total Stations</label><b>${state.route.stations.length}</b></div>
    <div class="stat-item"><span>□</span><label>Total Blocks</label><b>${totalBlocks}</b></div>
    <div class="stat-item"><span>◈</span><label>Occupied Blocks</label><b>${occupied}</b></div>
    <div class="stat-item"><span>↗</span><label>Max Speed</label><b>${Math.max(...L.blocks.map((b) => b.mps_kmh))} km/h</b></div>`;
}

function renderFaultList(containerId, faults) {
  const el = document.getElementById(containerId);
  if (!faults.length) { el.innerHTML = `<div class="empty">No active faults. Track is clear across all sections.</div>`; return; }
  el.innerHTML = `<table class="tbl"><tbody>` + faults.map((f) => {
    const b = state.route.blocks.find((x) => x.id === f.block_id);
    return `<tr>
      <td>${b ? `${b.from_name} \u2192 ${b.to_name}` : f.block_id}</td>
      <td>${statusPill(f.effect)} ${f.label}</td>
      <td class="mono">${Math.round(f.clears_in_min)} min</td>
      <td><button class="btn-ghost" data-fault-id="${f.id}" style="font-size:12px;">Clear now</button></td>
    </tr>`;
  }).join("") + `</tbody></table>`;
  el.querySelectorAll("[data-fault-id]").forEach((btn) => {
    btn.addEventListener("click", () => fetch(`/api/faults/${btn.getAttribute("data-fault-id")}`, { method: "DELETE" }));
  });
}

function renderStationLog(tbodyId, log) {
  const tbody = document.getElementById(tbodyId);
  if (!log || !log.length) { tbody.innerHTML = `<tr><td class="empty" colspan="5">No arrivals recorded yet this cycle.</td></tr>`; return; }
  tbody.innerHTML = log.map((r) => `
    <tr>
      <td class="mono">${r.train_number} <span class="muted">${r.train_name}</span></td>
      <td>${r.station_name}</td>
      <td class="mono">${r.scheduled}</td>
      <td class="mono">${r.actual}</td>
      <td>${r.delay_min == null ? '<span class="muted">\u2014</span>' : delayBadge(r.delay_min)}</td>
    </tr>`).join("");
}

function renderTrainPopup(sel, mapEl) {
  const existing = mapEl.querySelector('.train-map-popup');
  if (existing) existing.remove();
  if (!sel) return;
  const delayText = Number(sel.delay_min || 0) > 0 ? `${Number(sel.delay_min).toFixed(1)} min` : 'On time';
  const next = sel.next_station_name || 'Kanpur Central';
  const platform = sel.platform ? `Platform ${sel.platform}` : 'Platform —';
  const control = sel.current_block_status === 'blocked' ? 'HOLD · protect route' : sel.current_block_status === 'restricted' ? 'REDUCE SPEED' : (sel.conflict_status === 'caution' ? 'HEADWAY CONTROL' : 'NORMAL');
  const popup = el(`<div class="train-map-popup">
    <div class="train-map-popup-head"><div><b>${sel.number}</b><span>${sel.name}</span></div><button class="btn-ghost" aria-label="Close">×</button></div>
    <div class="train-map-popup-grid">
      <div><small>Status</small><strong>${sel.status}</strong></div>
      <div><small>Current time</small><strong>${sel.live_clock || '—'}</strong></div>
      <div><small>Delay</small><strong>${delayText}</strong></div>
      <div><small>Speed</small><strong>${sel.speed_kmh ?? 0} km/h</strong></div>
      <div><small>Next station</small><strong>${next}</strong></div>
      <div><small>Platform</small><strong>${platform}</strong></div>
      <div><small>ETA</small><strong>${sel.eta_clock || '—'}</strong></div>
      <div><small>Safety gap</small><strong>${sel.safety_gap_km >= 999 ? 'Clear' : `${sel.safety_gap_km.toFixed(1)} km`}</strong></div>
      <div><small>Current block</small><strong>${sel.current_block_id || '—'}</strong></div>
      <div><small>Control</small><strong>${control}</strong></div>
      <div class="wide"><small>Delay reason</small><strong>${sel.delay_reason || 'On time'}</strong></div>
      <div class="wide"><small>Prediction window</small><strong>${sel.p10_clock || '—'} – ${sel.p90_clock || '—'}</strong></div>
    </div>
  </div>`);
  popup.querySelector('button').onclick = (e) => { e.stopPropagation(); state.ctlSelectedId = null; renderController(); };
  mapEl.appendChild(popup);
}

function renderController() {
  const L = state.latest;
  if (!L || !state.route) return;
  const mapEl = document.getElementById("ctl-map");
  mapEl.innerHTML = trackSchematicSVG(state.route.stations, L.blocks, L.trains, state.ctlSelectedId, 285);
  mapEl.querySelectorAll("[data-train-id]").forEach((g) => g.addEventListener("click", () => {
    state.ctlSelectedId = g.getAttribute("data-train-id");
    renderController();
  }));

  const running = L.trains.filter(t => t.status === "running");
  const delayed = running.filter(t => Number(t.delay_min) > 4).length;
  const blocked = L.blocks.filter(b => b.status === "blocked").length;
  const restricted = L.blocks.filter(b => b.status === "restricted").length;
  document.getElementById("sm-kpis").innerHTML = [
    ["Active trains", running.length, "live movement", "accent"],
    ["Delayed services", delayed, "only services currently affected", delayed ? "amber" : "green"],
    ["Restricted blocks", restricted, "temporary speed restriction", restricted ? "amber" : "green"],
    ["Blocked blocks", blocked, "signal / route hold", blocked ? "red" : "green"],
    ["Safe headway", "5.0 km", "hard minimum", "accent"]
  ].map(x => `<div class="sm-kpi"><span>${x[0]}</span><b class="${x[3]}">${x[1]}</b><small>${x[2]}</small></div>`).join("");

  const card = document.getElementById("ctl-detail-card");
  const sel = L.trains.find((t) => t.id === state.ctlSelectedId);
  if (sel) {
    card.classList.remove("hidden");
    document.getElementById("ctl-detail-title").textContent = `${sel.number} · ${sel.name}`;
    document.getElementById("ctl-detail-sub").textContent = `${sel.type} · ${sel.current_block_id || "—"} · ${sel.delay_reason || "On time"}`;
    document.getElementById("ctl-detail-grid").innerHTML = `
      <div><div class="detail-label">Status</div><div class="detail-value">${sel.status}</div></div>
      <div><div class="detail-label">Position</div><div class="detail-value mono">${sel.position_km.toFixed(1)} km</div></div>
      <div><div class="detail-label">Speed</div><div class="detail-value mono">${sel.speed_kmh} km/h</div></div>
      <div><div class="detail-label">Delay</div>${delayBadge(sel.delay_min)}</div>
      <div><div class="detail-label">Reason</div><div class="detail-value">${sel.delay_reason || "On time"}</div></div>
      <div><div class="detail-label">Safety gap</div><div class="detail-value mono">${sel.safety_gap_km >= 999 ? "Clear" : sel.safety_gap_km.toFixed(1)+" km"}</div></div>
      <div><div class="detail-label">Next station</div><div class="detail-value">${sel.next_station_name || "Kanpur Central"}</div></div>
      <div><div class="detail-label">Platform</div><div class="detail-value">Platform ${sel.platform || "—"}</div></div>
      <div><div class="detail-label">ETA</div><div class="detail-value mono">${sel.eta_clock || "—"}</div></div>
      <div><div class="detail-label">Prediction window</div><div class="detail-value mono">${sel.p10_clock || "—"} – ${sel.p90_clock || "—"}</div></div>`;
    renderTrainPopup(sel, mapEl);
  } else {
    card.classList.add("hidden");
  }

  document.getElementById("ctl-block-table").innerHTML = L.blocks.map(b => {
    const trains = running.filter(t => t.current_block_id === b.id).map(t => t.number).join(", ") || "—";
    const action = b.status === "blocked" ? "HOLD · protect route" : b.status === "restricted" ? "Reduce speed" : b.occupancy > 1 ? "Monitor headway" : "Normal movement";
    return `<tr><td><b>${b.from_name} → ${b.to_name}</b><div class="muted mono">${b.id}</div></td><td class="mono">${b.length_km} km</td><td class="mono">${b.mps_kmh} km/h</td><td>${statusPill(b.status)}</td><td class="mono">${b.occupancy} <span class="muted">${trains}</span></td><td>${action}</td></tr>`;
  }).join("");

  const eventRows = [];
  L.faults.forEach(f => eventRows.push({id:null,tone:f.effect === "blocked" ? "red":"amber",title:f.label,sub:`${f.block_id} · clears in ${Math.round(f.clears_in_min)} min`}));
  running.filter(t => Number(t.delay_min)>4 || t.conflict_status === "caution").forEach(t => eventRows.push({id:t.id,tone:Number(t.delay_min)>15?"red":"amber",title:`${t.number} · ${t.name}`,sub:`${t.delay_reason || "Traffic adjustment"} · gap ${t.safety_gap_km >= 999 ? 'clear' : t.safety_gap_km.toFixed(1)+' km'}`}));
  document.getElementById("sm-event-count").textContent = eventRows.length;
  document.getElementById("sm-live-events").innerHTML = eventRows.slice(0,8).map(e => `<button class="sm-event event-tone-${e.tone}" data-train-id="${e.id||""}"><span class="event-icon ${e.tone}">●</span><span><b>${e.title}</b><small>${e.sub}</small></span></button>`).join("") || `<div class="empty">No active conflicts. Route is operating normally.</div>`;
  document.querySelectorAll("#sm-live-events [data-train-id]").forEach(btn => btn.addEventListener("click",()=>{ if(btn.dataset.trainId){state.ctlSelectedId=btn.dataset.trainId;renderController();}}));

  // Platform occupancy is shown for EVERY station, not as one generic six-row list.
  // A train is shown against its next station/platform when it is approaching,
  // and as occupied when it is within the station approach window.
  const stationPlatforms = state.route.stations.map(st => {
    const approaching = running.filter(t => t.next_station_id === st.id);
    const rows = Array.from({length:4},(_,i)=>{
      const pf=i+1;
      const train=approaching.find(t=>Number(t.platform)===pf);
      const occupied = !!train && Math.abs(st.km-train.position_km) <= 8;
      return {pf, train, occupied};
    });
    return `<div class="station-platform-group"><div class="station-platform-head"><b>${st.id}</b><span>${st.name}</span><small>${approaching.length ? `${approaching.length} approaching` : 'No approaching trains'}</small></div>${rows.map(r=>`<div class="platform-row ${r.occupied?'occupied':''}"><span class="platform-no">PF ${r.pf}</span><span>${r.train ? `<b>${r.train.number}</b> · ${r.train.next_station_name || st.name}` : 'Available'}</span><em>${r.train ? (r.occupied?'OCCUPIED':'RESERVED') : 'FREE'}</em></div>`).join('')}</div>`;
  }).join('');
  document.getElementById("sm-platform-board").innerHTML = stationPlatforms;

  const queue = [...running].sort((a,b)=>a.position_km-b.position_km).map((t,i,arr)=>{
    const ahead=arr.find(x=>x.position_km>t.position_km);
    const gap=ahead?ahead.position_km-t.position_km:999;
    return `<div class="headway-row"><span class="queue-num">${i+1}</span><b>${t.number}</b><span>${ahead?`behind ${ahead.number}`:'clear ahead'}</span><strong class="${gap<5.1?'danger':gap<9?'warn':'good'}">${gap===999?'Clear':gap.toFixed(1)+' km'}</strong></div>`;
  }).join("");
  document.getElementById("sm-headway-board").innerHTML = queue || `<div class="empty">No running trains.</div>`;
}

function paxClockAdd(clock, minutes) {
  if (!clock || minutes == null || Number.isNaN(Number(minutes))) return "--:--";
  const [hh, mm] = clock.split(":").map(Number);
  const total = (hh * 60 + mm + Math.round(Number(minutes))) % 1440;
  return `${String(Math.floor(total / 60)).padStart(2,"0")}:${String(total % 60).padStart(2,"0")}`;
}

function paxNextStation(sel) {
  const stations = state.route?.stations || [];
  return stations.find((st) => st.km > sel.position_km + 0.5) || stations[stations.length - 1];
}

function paxPriorityLabel(weight) {
  if (weight >= 0.995) return "Premium · Rajdhani priority";
  if (weight >= 0.95) return "Premium · Superfast priority";
  if (weight >= 0.87) return "Standard · Express service";
  return "Standard · Passenger service";
}

function paxDelayExplanation(sel) {
  const reason = sel.delay_reason || "On time";
  if (reason === "On time") return {title:"Running normally", text:"No operational delay is currently reported for this service."};
  const map = {
    signal_failure: ["Signal restriction", "The train is being held or slowed because a signalling block is restricted."],
    speed_restriction: ["Speed restriction", "A temporary speed restriction is affecting this section."],
    safe_headway: ["Safety spacing", "Speed is being reduced to maintain a safe distance from the train ahead."],
    close_following: ["Traffic spacing", "The train is being moderated because another service is close ahead."],
    fog: ["Reduced visibility", "Lower visibility is causing a temporary reduction in operating speed."],
    technical_halt: ["Technical inspection", "A short technical check is affecting the service."],
    platform_wait: ["Platform availability", "The train is waiting for platform capacity at the destination."],
    yellow_signal: ["Caution signal", "The train is approaching a caution signal and is operating at a reduced speed."]
  };
  const hit = map[reason] || ["Operational adjustment", reason];
  return {title:hit[0], text:hit[1]};
}

function renderPassenger() {
  const L = state.latest;
  if (!L || !state.route || !L.trains.length) return;
  const running = L.trains.filter(t => t.status !== "completed");
  if (!state.paxSelectedId || !L.trains.some(t => t.id === state.paxSelectedId)) state.paxSelectedId = running[0]?.id || L.trains[0].id;
  const select = document.getElementById("pax-train-select");
  select.innerHTML = L.trains.map(t => `<option value="${t.id}" ${t.id===state.paxSelectedId?'selected':''}>${t.number} · ${t.name}</option>`).join("");
  select.onchange = () => { state.paxSelectedId = select.value; renderPassenger(); };

  const sel = L.trains.find(t => t.id === state.paxSelectedId) || L.trains[0];
  const delay = Number(sel.delay_min || 0);
  const next = paxNextStation(sel);
  const explanation = paxDelayExplanation(sel);
  const status = sel.status === "completed" ? "Arrived" : delay > 15 ? "Major delay" : delay > 4 ? "Minor delay" : "On time";
  const statusClass = delay > 15 ? "danger" : delay > 4 ? "warn" : "good";
  document.getElementById("pax-top-status").textContent = status;
  document.getElementById("pax-last-update").textContent = `${L.clock} · cycle ${L.cycle}`;

  const map = document.getElementById("pax-map");
  map.innerHTML = trackSchematicSVG(state.route.stations, L.blocks, L.trains, sel.id, 315, true);
  map.querySelectorAll("[data-train-id]").forEach(g => g.addEventListener("click",()=>{state.paxSelectedId=g.dataset.trainId;renderPassenger();}));

  const progress = Math.max(0, Math.min(100, sel.position_km/state.route.route_km*100));
  document.getElementById("pax-selected-details").innerHTML = `<div class="pax-detail-head"><div><span class="pax-kicker">${sel.type} · ${sel.number}</span><h2>${sel.name}</h2><p>New Delhi (NDLS) → Kanpur Central (CNB)</p></div><span class="pax-status ${statusClass}">${status}</span></div>
    <div class="pax-big-eta"><small>Next station ETA</small><b>${sel.eta_clock || "—"}</b><span>${next?.name || "Kanpur Central"} · ${sel.status==='running'?fmtMinShort(Math.max(0,sel.eta_min-L.sim_min)):"Arrived"}</span></div>
    <div class="pax-detail-list"><div><span>Current speed</span><b>${sel.speed_kmh} km/h</b></div><div><span>Next station</span><b>${sel.next_station_name || next?.name || "Kanpur Central"}</b></div><div><span>Platform</span><b>Platform ${sel.platform || "—"}</b></div><div><span>Current block</span><b>${sel.current_block_id || "—"}</b></div><div><span>Safety gap</span><b>${sel.safety_gap_km>=999?'Clear':sel.safety_gap_km.toFixed(1)+' km'}</b></div><div><span>Final arrival</span><b>${sel.eta_clock || "—"}</b></div></div>
    <div class="pax-progress-head"><span>Journey progress</span><b>${progress.toFixed(0)}%</b></div><div class="progress-track pax-progress"><div class="progress-fill" style="width:${progress}%"></div></div>
    <div class="pax-reason ${delay>0?'has-delay':'normal'}"><div class="pax-reason-icon">${delay>0?'!':'✓'}</div><div><b>${explanation.title}</b><span>${explanation.text}</span></div><strong>${delay>0?'+'+delay.toFixed(1)+' min':'On time'}</strong></div>
    <div class="pax-ml-mini"><span>ML prediction</span><b>${sel.block_prediction?.predicted_min ?? '—'} min</b><small>80% window ${sel.block_prediction?.p10_min ?? '—'}–${sel.block_prediction?.p90_min ?? '—'} min</small></div>`;

  renderPassengerStationBoard(sel,L); renderPassengerAlerts(sel,L); renderPassengerUseful(sel,L);
}

function renderPassengerStationBoard(sel, L) {
  const el = document.getElementById("pax-station-board");
  if (!el) return;
  const board = Array.isArray(sel.station_board) ? sel.station_board : [];
  if (!board.length) {
    el.innerHTML = `<div class="pax-board-empty"><span>○</span><div><b>Loading live station board</b><small>Station timings will update from the live train simulation.</small></div></div>`;
    return;
  }

  el.innerHTML = board.map((r) => {
    const delay = Number(r.delay_min || 0);
    const status = r.status || "Upcoming";
    const isNext = status === "Next station";
    const isReached = status === "Reached" || status === "Departed";
    const statusClass = isNext ? "next" : isReached ? "past" : "upcoming";
    const timeChanged = r.live && r.scheduled && r.live !== r.scheduled;
    const liveTime = r.live || r.scheduled || "—";
    const delayText = delay > 0.1 ? `+${delay.toFixed(1)} min delay` : "On time";
    return `<div class="pax-station-row ${statusClass}">
      <div class="pax-station-dot ${isNext ? "next-dot" : isReached ? "arrived" : ""}"></div>
      <div class="pax-station-name"><b>${r.station_name}</b><span>${status} · Platform ${r.platform || "—"}</span></div>
      <div class="pax-station-time">
        <span class="scheduled-time ${timeChanged ? "struck" : ""}">${r.scheduled || "—"}</span>
        <strong class="live-time">${liveTime}</strong>
        <small class="delay-line ${delay > 0.1 ? "has-delay" : "on-time"}">${delayText}</small>
      </div>
    </div>`;
  }).join("");
}

function renderPassengerAlerts(sel, L) {
  const el = document.getElementById("pax-alerts");
  if (!el) return;
  const alerts = [];
  if (Number(sel.delay_min || 0) > 0) {
    const ex = paxDelayExplanation(sel);
    alerts.push({kind:Number(sel.delay_min)>15?"danger":"warn", title:ex.title, text:`${ex.text} Current estimated delay: ${Number(sel.delay_min).toFixed(1)} min.`});
  } else alerts.push({kind:"good", title:"Service running normally", text:"No passenger-impacting delay is currently reported for this train."});
  if (sel.conflict_status === "caution") alerts.push({kind:"warn", title:"Traffic spacing", text:`The control system is maintaining safe spacing from train ${sel.conflict_train_number || "ahead"}.`});
  const activeFaults = (L.faults || []).filter(f => f.block_id === sel.current_block_id);
  activeFaults.forEach(f => alerts.push({kind:"danger", title:f.label, text:`Current section ${f.block_id} is affected. Estimated clearance: ${f.clears_in_min} min.`}));
  el.innerHTML = alerts.slice(0,4).map(a => `<div class="pax-alert ${a.kind}"><span class="pax-alert-icon">${a.kind === "good" ? "✓" : "!"}</span><div><b>${a.title}</b><span>${a.text}</span></div></div>`).join("");
}

function renderPassengerUseful(sel, L) {
  const el = document.getElementById("pax-useful-grid");
  if (!el) return;
  const delay = Number(sel.delay_min || 0);
  const next = paxNextStation(sel);
  const platform = sel.platform ? `Platform ${sel.platform}` : "Platform will be updated";
  const block = L.blocks.find(b => b.id === sel.current_block_id);
  const condition = block?.status === "blocked" ? "Route hold" : block?.status === "restricted" ? "Speed restriction" : "Clear route";
  el.innerHTML = [
    [`Next station`, next?.name || "Kanpur Central", sel.eta_clock ? `Expected ${sel.eta_clock}` : "ETA updating live"],
    [`Arrival status`, delay > 0 ? `Delayed by ${delay.toFixed(1)} min` : "On schedule", delay > 0 ? (paxDelayExplanation(sel).title) : "No passenger-impacting delay"],
    [`Platform`, platform, "Check this panel before arrival"],
    [`Route condition`, condition, block?.status === "clear" ? "No active block restriction" : "Operations team is managing the section"],
    [`Live safety`, sel.safety_gap_km >= 999 ? "Clear ahead" : `${sel.safety_gap_km.toFixed(1)} km ahead`, "Automatic safe-headway control is active"],
    [`Prediction`, sel.block_prediction?.predicted_min != null ? `${sel.block_prediction.predicted_min} min section ETA` : "Updating", sel.block_prediction ? `Range ${sel.block_prediction.p10_min}–${sel.block_prediction.p90_min} min` : "ML estimate pending"]
  ].map(x => `<div class="pax-useful-item"><span>${x[0]}</span><b>${x[1]}</b><small>${x[2]}</small></div>`).join("");
}

function renderPassengerServices(L) {
  const el = document.getElementById("pax-services");
  if (!el) return;
  el.innerHTML = L.trains.map(t => `<button class="pax-service-row ${t.id === state.paxSelectedId ? "selected" : ""}" data-train-id="${t.id}">
    <span><b>${t.number}</b><small>${t.name}</small></span><span class="pax-service-eta">${t.status === "running" ? t.eta_clock : t.status === "completed" ? "Arrived" : "Scheduled"}</span><span>${delayBadge(t.delay_min)}</span>
  </button>`).join("");
  el.querySelectorAll("[data-train-id]").forEach(btn => btn.addEventListener("click", () => { state.paxSelectedId = btn.dataset.trainId; renderPassenger(); }));
}

function renderMLStatic() {
  const M = state.modelInfo;
  document.getElementById("ml-model-name").textContent =
    `${M.model_name} \u00B7 trained on ${M.training_samples?.toLocaleString()} simulated block-records`;
  document.getElementById("ml-offline-metrics").innerHTML = `
    <div class="metric"><div class="metric-value">${M.training_samples?.toLocaleString()}</div><div class="metric-label">Training samples</div></div>
    <div class="metric"><div class="metric-value">${M.metrics.rmse.toFixed(2)} min</div><div class="metric-label">RMSE (held-out)</div></div>
    <div class="metric"><div class="metric-value">${M.metrics.mae.toFixed(2)} min</div><div class="metric-label">MAE (held-out)</div></div>
    <div class="metric"><div class="metric-value">${M.metrics.r2.toFixed(3)}</div><div class="metric-label">R\u00B2</div></div>
  `;
  document.getElementById("ml-feature-chart").innerHTML = M.feature_importances
    ? hBarList(M.feature_importances)
    : `<div class="empty">Feature importances not available for this model type.</div>`;
}

function renderMLLive() {
  const L = state.latest;
  if (!L) return;
  const M = L.ml_monitor || {};
  document.getElementById("ml-live-metrics").innerHTML = `<div class="metric"><div class="metric-value" style="color:${COLORS.green}">${M.rolling_accuracy ?? "--"}%</div><div class="metric-label">Rolling accuracy</div></div><div class="metric"><div class="metric-value">${M.rolling_mae ?? "--"} min</div><div class="metric-label">Rolling MAE</div></div><div class="metric"><div class="metric-value">${M.samples ?? 0}</div><div class="metric-label">Blocks scored</div></div><div class="metric"><div class="metric-value">${M.trained_mae ?? "--"} min</div><div class="metric-label">Offline MAE</div></div>`;
  document.getElementById("ml-accuracy-chart").innerHTML = lineChartSVG(L.history || [], "rolling_accuracy", { color: COLORS.green, unit: "%" });
  document.getElementById("ml-mae-chart").innerHTML = lineChartSVG(L.history || [], "rolling_mae", { color: COLORS.amber, unit: " min" });

  const trains = L.trains.filter(t => t.status === "running");
  if (!state.feedSelectedId && trains.length) state.feedSelectedId = trains[0].id;
  document.getElementById("ml-train-predictions").innerHTML = trains.map(t => {
    const p=t.block_prediction || {}; const d=Number(t.delay_min||0); const c=d>15?COLORS.red:d>4?COLORS.amber:COLORS.green;
    return `<button class="ml-train-card ${t.id===state.feedSelectedId?'selected':''}" data-ml-train="${t.id}"><div><b>${t.number}</b><span>${t.name}</span></div><strong style="color:${c}">${p.predicted_min??'—'} min</strong><small>Block ${t.current_block_id||'—'} · P10 ${p.p10_min??'—'} · P90 ${p.p90_min??'—'}</small><em>${t.delay_reason||'On time'}</em></button>`;
  }).join("") || `<div class="empty">No running trains are available for live prediction.</div>`;
  document.querySelectorAll("[data-ml-train]").forEach(btn=>btn.addEventListener("click",()=>{state.feedSelectedId=btn.dataset.mlTrain;renderMLLive();renderMLDetail();}));

  const feedSel=document.getElementById("ml-feed-select");
  feedSel.innerHTML=trains.map(t=>`<option value="${t.id}" ${t.id===state.feedSelectedId?'selected':''}>${t.number} · ${t.name}</option>`).join("") || `<option value="">No running trains</option>`;
  feedSel.onchange=()=>{state.feedSelectedId=feedSel.value;renderMLLive();renderMLDetail();};
  const feed=trains.find(t=>t.id===state.feedSelectedId); const tbody=document.querySelector("#ml-feed-table tbody");
  if(feed?.live_features){const rows=Object.entries(feed.live_features).map(([k,v])=>`<tr><td class="muted">${k}</td><td class="mono">${v}</td></tr>`).join("");tbody.innerHTML=rows+`<tr><td colspan="2"><b>Predicted section time</b><span class="mono" style="float:right">${feed.block_prediction.predicted_min} min · ${feed.block_prediction.p10_min}–${feed.block_prediction.p90_min} min</span></td></tr>`;} else tbody.innerHTML=`<tr><td class="empty">No live feature data yet.</td></tr>`;
  renderMLDetail();
}

function renderMLDetail(){
  const L=state.latest, card=document.getElementById("ml-detail-card"), t=L?.trains.find(x=>x.id===state.feedSelectedId);
  if(!t){card.classList.add("hidden");return;} card.classList.remove("hidden");
  document.getElementById("ml-detail-title").textContent=`${t.number} · ${t.name}`;
  document.getElementById("ml-detail-sub").textContent=`${t.current_block_id||'—'} · ${t.delay_reason||'On time'}`;
  const p=t.block_prediction||{};
  document.getElementById("ml-detail-body").innerHTML=`<div class="ml-detail-grid"><div><span>Predicted section time</span><b>${p.predicted_min??'—'} min</b></div><div><span>P10</span><b>${p.p10_min??'—'} min</b></div><div><span>P90</span><b>${p.p90_min??'—'} min</b></div><div><span>Current delay</span><b>${Number(t.delay_min||0).toFixed(1)} min</b></div><div><span>Speed</span><b>${t.speed_kmh} km/h</b></div><div><span>Headway</span><b>${t.safety_gap_km>=999?'Clear':t.safety_gap_km.toFixed(1)+' km'}</b></div></div><div class="ml-detail-reason"><b>Why this prediction changed</b><span>${t.delay_reason||'No active operational restriction.'}</span></div>`;
}

function renderFaults() {
  const L = state.latest;
  document.getElementById("fault-count-sub").textContent = `${L.faults.length} currently in effect`;
  document.getElementById("fault-empty").classList.add("hidden");
  const tbody = document.querySelector("#fault-table tbody");
  if (!L.faults.length) {
    tbody.innerHTML = "";
    document.getElementById("fault-empty").classList.remove("hidden");
    return;
  }
  tbody.innerHTML = L.faults.map((f) => {
    const b = state.route.blocks.find((x) => x.id === f.block_id);
    return `<tr>
      <td>${b ? `${b.from_name} \u2192 ${b.to_name}` : f.block_id}</td>
      <td>${statusPill(f.effect)} ${f.label}</td>
      <td class="mono">${Math.round(f.clears_in_min)} min</td>
      <td><button class="btn-ghost" data-fault-id="${f.id}" style="font-size:12px;">Clear now</button></td>
    </tr>`;
  }).join("");
  tbody.querySelectorAll("[data-fault-id]").forEach((btn) => {
    btn.addEventListener("click", () => fetch(`/api/faults/${btn.getAttribute("data-fault-id")}`, { method: "DELETE" }));
  });
}

function renderTimetable() {
  const L = state.latest;
  const tbody = document.getElementById("tt-table");
  tbody.innerHTML = L.trains.map((t) => `
    <tr>
      <td class="mono">${t.number} <span class="muted">${t.name}</span></td>
      <td>${t.type}</td>
      <td class="mono">${(state.timetable.find((r) => r.train_id === t.id && r.sequence === "0") || {}).scheduled_departure || "--"}</td>
      <td style="text-transform:capitalize">${t.status}</td>
      <td class="mono">${t.eta_clock || (t.status === "completed" ? "arrived" : "\u2014")}</td>
      <td>${t.status === "scheduled" ? '<span class="muted">\u2014</span>' : (`<span title="${t.delay_reason || "On time"} · Speed ${t.speed_kmh ?? "--"} km/h · Gap ${t.safety_gap_km ?? "--"} km">${delayBadge(t.delay_min)}</span>`)}</td>
      <td class="mono">PF ${t.platform}</td>
    </tr>`).join("");
}

/* ------------------------------------------------------------- nav + init */

function setupNav() {
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const page = btn.getAttribute("data-page");
      state.page = page;
      document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
      document.getElementById(`page-${page}`).classList.remove("hidden");
      document.getElementById("page-title").textContent = btn.textContent.trim();
      if (page !== "ml") document.getElementById("ml-detail-card")?.classList.add("hidden");
    });
  });
}

async function injectFault(blockSelectId, typeSelectId) {
  const block_id = document.getElementById(blockSelectId).value;
  const fault_type = document.getElementById(typeSelectId).value;
  await fetch("/api/faults", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ block_id, fault_type }),
  });
}

function setupFaultForm() {
  document.getElementById("fault-inject-btn").addEventListener("click", () => injectFault("fault-block", "fault-type"));
  document.getElementById("ov-fault-inject-btn").addEventListener("click", () => injectFault("ov-fault-block", "ov-fault-type"));
}

function setupCycleControl() {
  document.getElementById("ov-next-cycle-btn").addEventListener("click", async () => {
    await fetch("/api/next-cycle", { method: "POST" });
  });
}

function setupDetailClose() {
  document.getElementById("ctl-detail-close").addEventListener("click", () => { state.ctlSelectedId = null; renderController(); });
  document.getElementById("ml-detail-close").addEventListener("click", () => { document.getElementById("ml-detail-card").classList.add("hidden"); });
}

async function main() {
  setupNav();
  setupFaultForm();
  setupCycleControl();
  setupDetailClose();
  await loadStaticData();
  connectWS();
}

main();
