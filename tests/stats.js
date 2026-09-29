// Run with Node22+. Synthetic DOM, requests and timers; never a real server.
const source = require("fs").readFileSync("files/web/static/app.js", "utf8");
const assert = require("assert").strict;

function setup() {
  const nodes = {}, requests = [], timers = new Map(), listeners = {};
  let timerId = 0;
  const makeNode = (id) => ({
    id, textContent: "", innerHTML: "", hidden: false, dataset: {}, attrs: {}, listeners: {},
    clientWidth: 700,
    classList: { add() {}, remove() {}, contains: () => true },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(name, value) { this.attrs[name] = value; },
  });
  ["stats-chart", "stats-summary", "stats-error", "stat-total", "stat-recognised", "stat-unmatched", "stat-manual-opens",
    "stat-processing", "insight-no-plate", "insight-no-plate-meta", "chart-label", "stats-chart-reading", "stats-recognised-list",
    "stats-unmatched-list", "stats-health-summary", "health-temp", "health-temp-meta", "health-disk-free", "health-disk-meta",
    "health-maintenance", "health-maintenance-meta", "health-failures", "health-failures-meta", "tab-stats"].forEach((id) => { nodes[id] = makeNode(id); });
  const chips = ["24h", "7d", "30d", "all"].map((period) => { const el = makeNode(period); el.dataset.window = period; return el; });
  const fetch = (url) => {
    if (url === "/api/ui-settings") return new Promise(() => {});
    return new Promise((resolve, reject) => requests.push({ url, reject,
      resolve: (data, ok = true) => resolve({ ok, json: async () => data }) }));
  };
  const document = {
    body: { classList: { contains: () => false } }, hidden: false,
    getElementById: (id) => nodes[id] || null,
    querySelector: () => null,
    querySelectorAll: (selector) => selector === "#tab-stats .chip" ? chips : [],
    addEventListener() {},
  };
  const window = { fetch, innerWidth: 800, addEventListener: (name, fn) => { listeners[name] = fn; } };
  const app = new Function("document", "window", "navigator", "fetch", "setTimeout", "clearTimeout", "AbortController",
    source + "\nreturn {initStats, statsChartBuckets, statsBucketLabel};")(
    document, window, { userAgent: "iPad; CPU OS12_5 like Mac OS X" }, fetch,
    (fn) => { timers.set(++timerId, fn); return timerId; }, (id) => timers.delete(id), undefined);
  return { app, nodes, chips, requests, timers, listeners };
}

async function settle() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
function data(count, marker = "TEST123") {
  return [{ counts: { recognised: count, unmatched: 2, manual_open: 1 },
    timeseries: { bucket: "hour", start: "2026-09-29 09:30:00", end: "2026-09-29 12:30:00",
      series: [{ t: "2026-09-29 10:00", v: count + 3 }] } },
  { insights: { top_recognised_list: [{ plate: marker, count }], top_unmatched_list: [],
    avg_processing_ms: { overall: 250 }, no_plate: 1 } }];
}
function resolveActivity(test, start, count, marker) {
  const payload = data(count, marker);
  test.requests[start + 1].resolve(payload[0]);
  test.requests[start + 2].resolve(payload[1]);
}

async function run() {
  const pure = setup().app;
  const points = pure.statsChartBuckets(data(4)[0].timeseries);
  assert.equal(points.length, 4, "Keep the entire Pi-clock window including quiet hours");
  assert.deepEqual(points.map((p) => p.value), [0, 7, 0, 0]);
  assert.equal(pure.statsBucketLabel(points[1]), "29 Sep, 10:00");
  const long = pure.statsChartBuckets({ bucket: "day", start: "1900-01-01", end: "2100-01-01",
    series: [{ t: "1900-01-01", v: 7 }, { t: "2099-12-31", v: 9 }] });
  assert(long.length <= 60, "Long history must not create unbounded chart elements");
  assert.equal(long.reduce((sum, p) => sum + p.value, 0), 16, "Aggregation must retain every count");

  const test = setup();
  test.app.initStats();
  test.chips[1].listeners.click();
  resolveActivity(test, 3, 12, "<img onerror=alert(1)>");
  test.requests[3].reject(new Error("health unavailable"));
  await settle();
  assert.equal(test.nodes["stat-recognised"].textContent, 12);
  assert.equal(test.nodes["stat-total"].textContent, 14, "Manual commands are not ANPR detections");
  assert(test.nodes["stats-summary"].textContent.includes("last 7 days"));
  assert(test.nodes["stats-recognised-list"].innerHTML.includes("&lt;img"), "Escape registrations");
  assert.equal(test.nodes["stats-health-summary"].textContent, "Status unavailable");
  resolveActivity(test, 0, 99);
  test.requests[0].resolve({ temperature_c: 40 });
  await settle();
  assert.equal(test.nodes["stat-recognised"].textContent, 12, "Late previous-period responses cannot overwrite new results");
  assert.equal(test.nodes["stats-health-summary"].textContent, "Status unavailable", "Stale health cannot overwrite a later result");
  const beforeResize = test.requests.length;
  test.nodes["stats-chart"].clientWidth = 330;
  test.listeners.resize();
  assert.equal(test.requests.length, beforeResize, "Resizing must redraw locally without new API calls");
  assert.equal(test.nodes["stats-chart"].attrs.viewBox, "0 0 330 240", "Phone axes must remain legible");
  test.nodes["stats-chart"].listeners.click({ type: "click", target: { closest: () => ({ getAttribute: () => "1" }) } });
  assert(test.nodes["stats-chart-reading"].textContent.includes("15 recorded events"));
  assert.equal(test.requests.length, beforeResize, "Reading a bar must not fetch or navigate unexpectedly");

  test.chips[2].listeners.click();
  assert.equal(test.nodes["stat-recognised"].textContent, "—", "Clear old-period values during a new load");
  test.requests[7].resolve({}, false);
  test.requests[8].resolve({});
  test.requests[6].resolve({ services: {}, failed_units: [] });
  await settle();
  assert.equal(test.nodes["stats-error"].hidden, false);
  assert.equal(test.nodes["stats-chart"].innerHTML, "", "A failed period must not retain a previous chart");

  const timeout = setup();
  timeout.app.initStats();
  Array.from(timeout.timers.values()).forEach((fn) => fn());
  await settle();
  assert.equal(timeout.nodes["stats-error"].hidden, false, "Hung fetch/body must leave a retryable error");
  resolveActivity(timeout, 0, 100);
  await settle();
  assert.equal(timeout.nodes["stat-recognised"].textContent, "—", "Late timed-out responses cannot apply data");

  const health = setup();
  health.app.initStats();
  resolveActivity(health, 0, 1);
  health.requests[0].resolve({ temperature_c: 55, failed_units: [],
    services: { gate_anpr_web: "active", gate_anpr: "active", alprd: "active", stream_jpeg: "inactive" } });
  await settle();
  assert(health.nodes["stats-health-summary"].textContent.includes("Check services"), "Stopped preview must not be labelled Services active");
  assert(health.nodes["health-temp-meta"].textContent.includes("inactive or unknown"));
  health.chips[1].listeners.click();
  health.requests[3].resolve({ temperature_c: 55, failed_units: [],
    services: { gate_anpr_web: "active", gate_anpr: "active", alprd: "active", stream_jpeg: "active" } });
  resolveActivity(health, 3, 1);
  await settle();
  assert(health.nodes["stats-health-summary"].textContent.includes("Services active"), "Healthy preview can recover the summary");
  console.log("Stats quiet intervals, bounded charts, Pi clock, races, partial failures, deadlines and local redraw checks passed");
}
run().catch((err) => { console.error(err); process.exitCode = 1; });
