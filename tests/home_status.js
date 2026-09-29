// Run with node tests/home_status.js (also supports macOS JavaScriptCore jsc).
// All data is synthetic; this test does not contact or actuate a gate.
const source = typeof require === "function"
  ? require("fs").readFileSync("files/web/static/app.js", "utf8")
  : readFile("files/web/static/app.js");
const report = typeof print === "function" ? print : console.log;
function assert(condition, message) { if (!condition) throw new Error(message); }

function setup({ abortAvailable = true } = {}) {
  let now = 60000; // Intentionally unrelated to the Pi clock.
  let nextTimer = 0;
  const timers = new Map();
  const requests = [];
  const images = [];
  function node(id) {
    const classes = new Set();
    return {
      id, dataset: {}, children: [], listeners: {}, hidden: false, textContent: "", writes: 0,
      classList: {
        contains: (name) => classes.has(name),
        add: (name) => classes.add(name),
        remove: (name) => classes.delete(name),
        toggle: (name, on) => on ? classes.add(name) : classes.delete(name),
      },
      set innerHTML(value) { this.html = value; this.children = []; this.writes++; },
      get innerHTML() { return this.html || ""; },
      appendChild(child) { this.children.push(child); },
      setAttribute() {},
      querySelectorAll: () => [],
      addEventListener(name, callback) {
        if (!this.listeners[name]) this.listeners[name] = [];
        this.listeners[name].push(callback);
      },
      dispatch(name) { (this.listeners[name] || []).forEach((callback) => callback()); },
    };
  }
  const nodes = {};
  ["tab-home", "tab-candidates", "tablet-stream-status", "tablet-system-status", "tablet-timeline-list",
    "home-unfamiliar", "home-unfamiliar-age", "home-decision", "home-decision-age", "home-arrival-status",
    "allowlist-status", "save-plates", "add-plate", "plates-list"].forEach((id) => { nodes[id] = node(id); });
  nodes["tab-home"].classList.add("active");
  const fields = [nodes["save-plates"], nodes["add-plate"], node("owner-input")];
  const document = {
    hidden: false, listeners: {},
    body: { classList: { contains: () => false } },
    getElementById: (id) => nodes[id] || null,
    querySelector: () => null,
    querySelectorAll(selector) {
      if (selector === ".tab-panel") return [nodes["tab-home"], nodes["tab-candidates"]];
      if (selector.startsWith("#plates-list input")) return fields;
      return [];
    },
    createElement: node,
    addEventListener: node("").addEventListener,
    dispatch: node("").dispatch,
  };
  const setTimeout = (callback, delay) => {
    const id = ++nextTimer;
    timers.set(id, { callback, at: now + delay });
    return id;
  };
  const clearTimeout = (id) => timers.delete(id);
  function advance(ms) {
    const target = now + ms;
    while (true) {
      const due = Array.from(timers.entries()).filter((entry) => entry[1].at <= target)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      now = due[1].at;
      timers.delete(due[0]);
      due[1].callback();
    }
    now = target;
  }
  class Clock extends Date { static now() { return now; } }
  class AbortController {
    constructor() {
      this.callbacks = [];
      this.signal = { addEventListener: (name, callback) => this.callbacks.push(callback) };
    }
    abort() { this.callbacks.forEach((callback) => callback()); }
  }
  class Image {
    constructor() { images.push(this); }
    set src(value) { this.url = value; }
    closest() { return nodes["tab-home"]; }
    removeAttribute() { this.url = ""; }
  }
  const fetch = (url, init = {}) => {
    if (url === "/api/ui-settings") return new Promise(() => {});
    return new Promise((resolve, reject) => {
      const request = { url, init, reject, resolve: (data, status = 200) => resolve({
        ok: status >= 200 && status < 300, status,
        json: async () => { if (data instanceof Error) throw data; return data; },
      }) };
      if (init.signal) init.signal.addEventListener("abort", () => reject(new Error("timeout")));
      requests.push(request);
    });
  };
  const app = new Function("document", "window", "navigator", "fetch", "Date", "Image", "AbortController",
    "setTimeout", "clearTimeout", "history", source + `
      return { state, createLegacyStreamImage, renderHomeStatus, renderHomeArrivals, fetchHomeStatus,
        homeServerNow, describeDecision, pollVisibleTab, setActiveTab, saveAllowlist, markAllowlistChanged,
        renderTabletTimeline, updateTabletTimelineRelativeTimes };
    `)(document, { fetch }, { userAgent: "iPad; CPU OS 12_5_8 like Mac OS X" }, fetch, Clock, Image,
    abortAvailable ? AbortController : undefined, setTimeout, clearTimeout, { replaceState() {} });
  app.state.tabletLoaded = true;
  app.state.eventsLoaded = true;
  return { app, nodes, fields, document, requests, timers, advance, images, now: () => now };
}

async function settle() { for (let index = 0; index < 10; index++) await Promise.resolve(); }

function snapshot(overrides = {}) {
  return {
    server_time: 1900000000,
    services_checked_at: 1900000000,
    stream: { age_seconds: 1, fresh: true, stale_after_seconds: 15 },
    services: { alprd: "active", gate_anpr: "active", stream_jpeg: "active", beanstalkd: "active" },
    recognised: [], unfamiliar: [], recent_decisions: [], decisions_available: true,
    ...overrides,
  };
}

function applySnapshot(test, data = snapshot()) {
  test.app.state.homeStatus = data;
  test.app.state.homeStatusReceivedAt = test.now();
  test.app.state.homeStatusError = false;
  test.app.state.homeSourceIsLocal = true;
}

function checkFrameTruth() {
  const test = setup();
  const image = test.app.createLegacyStreamImage("/static/stream.jpg");
  test.app.state.homeFrame = image.gateFrameState;
  applySnapshot(test);
  const label = () => test.nodes["tablet-stream-status"].textContent;
  test.app.renderHomeStatus();
  assert(label() === "Loading view…", "Healthy services must not claim a frame has displayed before load");
  image.onload();
  test.app.renderHomeStatus();
  assert(label() === "View updating", "A loaded image and fresh source should show an updating view");
  test.advance(200);
  image.onerror();
  test.app.renderHomeStatus();
  assert(label() === "View interrupted", "A real image error must override a healthy source API");
  test.advance(1000);
  image.onload();
  test.app.state.homeStatus.stream = { age_seconds: 60, fresh: false, stale_after_seconds: 15 };
  test.app.renderHomeStatus();
  assert(label() === "Source stale", "Repeated successful downloads of a stale source must not look live");
  applySnapshot(test);
  test.advance(10200);
  test.app.renderHomeStatus();
  assert(label() === "Frame stalled", "A watchdog timeout must override source health");
  test.advance(1000);
  image.onload();
  test.app.state.homeStatusError = true;
  test.app.renderHomeStatus();
  assert(label() === "Status unavailable", "Recent successful image loads cannot prove an unavailable backend healthy");
  applySnapshot(test, snapshot({ services: { alprd: "inactive", gate_anpr: "active", stream_jpeg: "active", beanstalkd: "active" } }));
  test.app.renderHomeStatus();
  assert(test.nodes["tablet-system-status"].textContent.includes("Recognition service inactive"), "Service failures must be visible separately from image freshness");
  applySnapshot(test, snapshot({ services_checked_at: 1899999900 }));
  test.app.renderHomeStatus();
  assert(test.nodes["tablet-system-status"].textContent.includes("Service status unavailable"), "An old active-service snapshot cannot be reported as current");
}

function checkArrivalExpiryAndDecisions() {
  const test = setup();
  const event = { id: 1, plate: '<img src=x onerror="fail()">', seen_at: 1899999900, expires_at: 1900000200,
    image_url: "/images/original.jpg", thumbnail_url: "/previews/thumb.jpg?size=160" };
  const decision = { id: 2, plate: "TEST123", seen_at: 1899999999,
    detail: { decision: { version: 1, reason: "allowlist_match", match_type: "exact", relay_command: "pulse_sent" } } };
  applySnapshot(test, snapshot({ unfamiliar: [event], recent_decisions: [decision] }));
  test.app.renderHomeArrivals();
  const card = test.nodes["home-unfamiliar"];
  assert(!card.hidden && card.innerHTML.includes("/previews/thumb.jpg"), "Recent unfamiliar arrival must use a preview");
  assert(!card.innerHTML.includes("/images/") && card.innerHTML.includes("&lt;img"), "Unfamiliar markup must escape plate text and avoid originals");
  assert(test.nodes["home-unfamiliar-age"].textContent === "Seen 1m ago", "Arrival age must use Pi time despite a wrong browser clock");
  const writes = card.writes;
  test.advance(1000);
  test.app.renderHomeArrivals();
  assert(card.writes === writes, "Age updates must not rebuild or redownload the arrival preview");
  assert(test.nodes["home-decision"].innerHTML.includes("Relay pulse sent"), "Decision card must use the recorded relay outcome");
  assert(test.nodes["home-decision-age"].textContent.startsWith("Decision "), "Decision creation time must not be labelled capture time");
  assert(!test.nodes["home-decision"].innerHTML.includes("Gate opened"), "A relay acknowledgement is not physical gate position");
  test.app.state.homeStatusError = true;
  test.advance(199000);
  test.app.renderHomeArrivals();
  assert(card.hidden && card.innerHTML === "", "Unfamiliar arrival must expire locally even while polling is offline");
  assert(!test.nodes["home-arrival-status"].hidden, "Old arrival snapshots must be labelled when updates stop");
  test.app.state.homeStatus.decisions_available = false;
  test.app.renderHomeArrivals();
  assert(test.nodes["home-decision"].textContent === "Decision history unavailable", "Unavailable decisions must not be presented as an empty history");
  assert(test.app.describeDecision({ kind: "recognised", allowed: true }) === "", "Historical captures must not acquire invented decisions");
  const expected = { coalesced: "recent relay pulse", uncertain: "outcome uncertain", failed_before_activation: "No relay pulse sent", not_requested: "No relay pulse requested" };
  Object.keys(expected).forEach((command) => {
    decision.detail.decision.relay_command = command;
    assert(test.app.describeDecision(decision).includes(expected[command]), "Each relay outcome must remain distinct");
  });
  test.app.state.tabletEvents = [{ id: 5, plate: "OLD123", seen_at: 1899992800, captured_at: "2030-03-17 10:00:00" }];
  test.app.renderTabletTimeline();
  const row = test.nodes["tablet-timeline-list"].children[0];
  assert(row.innerHTML.includes("2h ago") && row.innerHTML.includes("dot-stale"), "Known arrival text and dot must agree with the server clock");
  const dot = test.fields[2];
  dot.dataset.tabletSeenAt = "1899992800";
  dot.dataset.tabletRelativeDot = "2030-03-17 10:00:00";
  dot.classList.add("dot-fresh");
  test.nodes["tablet-timeline-list"].querySelectorAll = (selector) => selector === "[data-tablet-relative-dot]" ? [dot] : [];
  test.app.updateTabletTimelineRelativeTimes();
  assert(dot.classList.contains("dot-stale") && !dot.classList.contains("dot-fresh"), "Periodic dot updates must also ignore a wrong client clock");
}

async function checkHomePolling() {
  const test = setup();
  test.app.pollVisibleTab(test.app.fetchHomeStatus, 5000, "home");
  test.document.dispatch("visibilitychange");
  assert(test.requests.length === 1, "Home refresh must be single-flight");
  test.requests[0].resolve(snapshot({ recognised: [{ id: 1 }, { id: 2 }, { id: 3 }] }));
  await settle();
  assert(test.app.state.tabletEvents.length === 2, "Home must retain at most two recognised arrivals");
  test.app.setActiveTab("candidates");
  test.advance(6000);
  assert(test.requests.length === 1, "Hidden Home must not poll");
  test.app.setActiveTab("home");
  assert(test.requests.length === 2, "Returning Home must refresh immediately");
  test.document.hidden = true;
  test.document.dispatch("visibilitychange");
  test.requests[1].resolve(snapshot());
  await settle();
  assert(test.timers.size === 0, "A response finishing in the background must not restart polling");
  test.document.hidden = false;
  test.document.dispatch("visibilitychange");
  assert(test.requests.length === 3, "Unhiding Home must refresh immediately");
  test.advance(10000);
  await settle();
  assert(test.app.state.homeStatusError, "A hung status request must time out and be marked unavailable");
  test.advance(5000);
  assert(test.requests.length === 4, "Home polling must recover after a request timeout");
}

async function checkPlateFeedback() {
  const test = setup();
  test.app.state.plates = [{ owner: "Before", plates: ["AB12 CDE"] }];
  test.app.state.platesSavedSnapshot = JSON.stringify(test.app.state.plates);
  test.app.state.plates[0].owner = "Edited";
  test.app.markAllowlistChanged();
  assert(test.nodes["allowlist-status"].textContent === "Unsaved changes", "Editing must be visibly unsaved");
  let saving = test.app.saveAllowlist();
  test.app.saveAllowlist();
  assert(test.requests.length === 1 && test.fields.every((field) => field.disabled), "Save must prevent duplicate requests and edits during submission");
  test.requests[0].resolve({ error: "Row 1: duplicate plate" }, 400);
  await saving;
  assert(test.app.state.plates[0].owner === "Edited", "Validation failure must retain the edited allowlist");
  assert(test.nodes["allowlist-status"].textContent === "Row 1: duplicate plate", "Validation errors must identify the problem");
  assert(test.fields.every((field) => !field.disabled), "Failed saves must restore editing controls");
  saving = test.app.saveAllowlist();
  test.requests[1].reject(new Error("offline"));
  await saving;
  assert(test.app.state.plates[0].owner === "Edited" && test.nodes["allowlist-status"].textContent.includes("edits are still here"), "Network errors must retain unsaved edits");
  saving = test.app.saveAllowlist();
  test.requests[2].resolve(new Error("HTML response"), 502);
  await saving;
  assert(test.app.state.plates[0].owner === "Edited", "Non-JSON proxy errors must not lose edits");
  saving = test.app.saveAllowlist();
  test.requests[3].resolve({ ok: true });
  await settle();
  test.requests[4].resolve([{ owner: "Edited", plates: ["AB12 CDE"] }]);
  test.requests[5].resolve([]);
  await saving;
  assert(test.nodes["allowlist-status"].textContent === "All changes saved", "Only a successful save and reload should report completion");
}

async function checkPlateDeadlines() {
  for (const abortAvailable of [true, false]) {
    const test = setup({ abortAvailable });
    test.app.state.plates = [{ owner: "Unsaved", plates: ["TEST123"] }];
    const saving = test.app.saveAllowlist();
    test.advance(10000);
    await settle();
    await saving;
    assert(!test.app.state.platesSaving && test.fields.every((field) => !field.disabled), "Hung PUT must release editing controls with or without AbortController");
    assert(test.nodes["allowlist-status"].textContent.includes("Could not confirm"), "An unconfirmed PUT must not claim it definitely failed");
    test.app.state.plates[0].owner = "Newer edit";
    test.requests[0].resolve({ ok: true });
    await settle();
    assert(test.requests.length === 1 && test.app.state.plates[0].owner === "Newer edit", "A late PUT response must not trigger a reload or replace newer edits");

    const reload = test.app.saveAllowlist();
    test.requests[1].resolve({ ok: true });
    await settle();
    let resolveBody;
    const delayedBody = new Promise((resolve) => { resolveBody = resolve; });
    test.requests[2].resolve(delayedBody);
    test.requests[3].resolve([]);
    test.advance(10000);
    await settle();
    await reload;
    assert(!test.app.state.platesSaving && test.fields.every((field) => !field.disabled), "Hung reload response bodies must also have a deadline");
    assert(test.nodes["allowlist-status"].textContent.startsWith("Saved, but"), "A confirmed PUT and failed reload must be distinguished");
    test.app.state.plates[0].owner = "Latest edit";
    resolveBody([{ owner: "Outdated server response", plates: ["TEST123"] }]);
    await settle();
    assert(test.app.state.plates[0].owner === "Latest edit", "Late reload body must never overwrite newer edits");

    const bodyTimeout = test.app.saveAllowlist();
    test.requests[4].resolve(new Promise(() => {}));
    test.advance(10000);
    await settle();
    await bodyTimeout;
    assert(!test.app.state.platesSaving && test.app.state.plates[0].owner === "Latest edit", "PUT body parsing must be inside the deadline too");
  }
}

async function run() {
  checkFrameTruth();
  checkArrivalExpiryAndDecisions();
  await checkHomePolling();
  await checkPlateFeedback();
  await checkPlateDeadlines();
  report("Home frame/source truth, service freshness, arrival expiry, decisions, polling and plate feedback checks passed");
}
run().catch((error) => {
  report(String(error));
  if (typeof quit === "function") quit(1);
  else process.exitCode = 1;
});
