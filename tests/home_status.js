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
      id, dataset: {}, children: [], listeners: {}, hidden: false, text: "", writes: 0,
      set textContent(value) { this.text = value; this.children = []; },
      get textContent() { return this.text + this.children.map((child) => child.textContent).join(""); },
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
      querySelector(selector) {
        for (const child of this.children) {
          if (selector === "." + child.className) return child;
          const match = child.querySelector ? child.querySelector(selector) : null;
          if (match) return match;
        }
        return null;
      },
      addEventListener(name, callback) {
        if (!this.listeners[name]) this.listeners[name] = [];
        this.listeners[name].push(callback);
      },
      dispatch(name) { (this.listeners[name] || []).forEach((callback) => callback()); },
    };
  }
  const nodes = {};
  ["tab-home", "tab-candidates", "tablet-stream-status", "tablet-system-status", "tablet-source-status", "tablet-service-status", "tablet-timeline-list",
    "home-arrival-status", "home-cpu-temp", "home-processing", "home-confidence", "home-disk-free",
    "allowlist-status", "save-plates", "add-plate", "plates-list"].forEach((id) => { nodes[id] = node(id); });
  nodes["tab-home"].classList.add("active");
  const fields = [nodes["save-plates"], nodes["add-plate"], node("owner-input")];
  const document = {
    hidden: false, listeners: {},
    body: node("body"),
    documentElement: { style: { setProperty(name, value) { this[name] = value; } } },
    getElementById: (id) => nodes[id] || null,
    querySelector: () => null,
    querySelectorAll(selector) {
      if (selector === ".tab-panel") return [nodes["tab-home"], nodes["tab-candidates"]];
      if (selector.startsWith("#plates-list input")) return fields;
      return [];
    },
    createElement: node,
    createTextNode: (textContent) => ({ textContent }),
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
  const window = { fetch, innerWidth: 1024, innerHeight: 748, listeners: {}, addEventListener: node("").addEventListener,
    dispatch: node("").dispatch };
  const app = new Function("document", "window", "navigator", "fetch", "Date", "Image", "AbortController",
    "setTimeout", "clearTimeout", "history", source + `
      return { state, createLegacyStreamImage, renderHomeStatus, renderHomeArrivals, renderHomeMetrics, fetchHomeStatus,
        homeServerNow, describeDecision, pollVisibleTab, setActiveTab, saveAllowlist, markAllowlistChanged,
        renderTabletTimeline, updateTabletTimelineRelativeTimes, initHomeViewport };
    `)(document, window, { userAgent: "iPad; CPU OS 12_5_8 like Mac OS X" }, fetch, Clock, Image,
    abortAvailable ? AbortController : undefined, setTimeout, clearTimeout, { replaceState() {} });
  app.state.tabletLoaded = true;
  app.state.eventsLoaded = true;
  return { app, nodes, fields, document, window, requests, timers, advance, images, now: () => now };
}

function checkViewportSizing() {
  const legacy = setup();
  const height = () => legacy.document.documentElement.style["--home-viewport-height"];
  legacy.app.initHomeViewport();
  assert(height() === "748px", "iOS 12 must use innerHeight when visualViewport is absent");
  legacy.window.innerHeight = 704;
  legacy.window.dispatch("resize");
  assert(height() === "704px", "Safari's visible address bar must shrink the wall layout");
  legacy.window.innerHeight = 680;
  legacy.window.dispatch("orientationchange");
  assert(height() === "680px", "Orientation changes must measure the available height again");
  legacy.window.innerHeight = 748;
  legacy.window.dispatch("pageshow");
  assert(height() === "748px", "Returning from Safari's page cache must refresh the height");
  legacy.window.innerHeight = 0;
  legacy.window.dispatch("resize");
  assert(height() === "748px", "Transient invalid measurements must not collapse the gate control");
  const modern = setup();
  const visual = { height: 703.7, scale: 1, addEventListener(name, callback) { this[name] = callback; } };
  modern.window.visualViewport = visual;
  modern.app.initHomeViewport();
  const modernHeight = () => modern.document.documentElement.style["--home-viewport-height"];
  assert(modernHeight() === "703px", "Visible viewport height must account for modern browser chrome");
  visual.height = 900;
  visual.resize();
  assert(modernHeight() === "748px", "Visual viewport must not enlarge beyond innerHeight");
  visual.height = 350;
  visual.scale = 2;
  visual.resize();
  assert(modernHeight() === "748px", "Pinch zoom must not trigger a smaller wall layout");
}

async function settle() { for (let index = 0; index < 10; index++) await Promise.resolve(); }

function snapshot(overrides = {}) {
  return {
    server_time: 1900000000,
    services_checked_at: 1900000000,
    stream: { age_seconds: 1, fresh: true, stale_after_seconds: 15 },
    services: { alprd: "active", gate_anpr: "active", stream_jpeg: "active", beanstalkd: "active" },
    arrivals: [],
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
  assert(test.nodes["tablet-source-status"].textContent === "Pi frame fresh", "Source freshness belongs in its own pill");
  assert(test.nodes["tablet-service-status"].textContent === "Services active", "Service activity belongs in its own pill");
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
  assert(test.nodes["tablet-service-status"].textContent === "Service issue", "Service failures must be visible while details are collapsed");
  applySnapshot(test, snapshot({ services_checked_at: 1899999900 }));
  test.app.renderHomeStatus();
  assert(test.nodes["tablet-system-status"].textContent.includes("Service status unavailable"), "An old active-service snapshot cannot be reported as current");
}

function checkSampledSourceAge() {
  const test = setup();
  const image = test.app.createLegacyStreamImage("/static/stream.jpg");
  test.app.state.homeFrame = image.gateFrameState;
  applySnapshot(test, snapshot({ stream: { age_seconds: 0.08, fresh: true, stale_after_seconds: 15 } }));
  image.onload();
  for (let index = 0; index < 20; index++) {
    test.advance(200);
    image.onload();
  }
  test.app.renderHomeStatus();
  const pill = () => test.nodes["tablet-source-status"].textContent;
  const detail = () => test.nodes["tablet-system-status"].textContent;
  assert(pill() === "Pi frame fresh", "A recent source sample must not become an invented ticking frame age");
  assert(detail().includes("Pi JPEG was 0.1s old when checked 4s ago"), "Separate sampled JPEG age from check age, without comparing Pi and client clocks");

  applySnapshot(test, snapshot({ stream: { age_seconds: 14, fresh: true, stale_after_seconds: 15 } }));
  test.advance(4000);
  image.onload();
  test.app.renderHomeStatus();
  assert(pill() === "Pi frame fresh", "Elapsed polling time cannot prove that the latest producer frame is stale");

  applySnapshot(test, snapshot({ stream: { age_seconds: 16, fresh: false, stale_after_seconds: 15 } }));
  test.advance(200);
  image.onload();
  test.app.renderHomeStatus();
  assert(pill() === "Pi frame stale", "Repeated successful downloads cannot override a stale publisher sample");
  assert(test.nodes["tablet-stream-status"].textContent === "Source stale", "A genuine stale sample must still flag the main status");

  applySnapshot(test);
  for (let index = 0; index < 101; index++) {
    test.advance(200);
    image.onload();
  }
  test.app.renderHomeStatus();
  assert(pill() === "Source unknown", "An expired sample cannot continue claiming freshness");
  assert(detail().includes("Pi frame status unavailable"), "Expired source observations must not be presented as current");
  applySnapshot(test);
  test.app.state.homeStatusError = true;
  test.app.renderHomeStatus();
  assert(pill() === "Source unknown", "A failed check must turn source freshness unknown");
}

function checkDisplayDiagnostics() {
  const test = setup();
  const image = test.app.createLegacyStreamImage("/static/stream.jpg");
  image.naturalWidth = 800;
  image.naturalHeight = 450;
  test.app.state.homeFrame = image.gateFrameState;
  applySnapshot(test);
  const detail = () => test.nodes["tablet-system-status"].textContent;
  const pill = () => test.nodes["tablet-stream-status"].textContent;
  image.onload();
  test.app.renderHomeStatus();
  assert(detail().includes("Viewport 1024×748 CSS px"), "On-device details must report CSS viewport dimensions");
  assert(detail().includes("JPEG 800×450"), "Report the loaded image's real dimensions");
  assert(detail().includes("Measuring image loads"), "Do not invent a rate before the first sampling interval");
  assert(pill() === "View updating", "Wait for a sampling interval before showing FPS");
  for (let index = 0; index < 25; index++) {
    test.advance(200);
    image.onload();
  }
  test.app.renderHomeStatus();
  assert(detail().includes("Image loads 5.0/s (last 5s; not distinct frames)"), "Load count must be labelled separately from distinct camera frames");
  assert(pill() === "View 5.0 FPS", "Show the measured image load rate on the healthy view pill");
  assert(test.nodes["tablet-stream-status"].title.includes("repeated camera frames"), "Explain that FPS counts loads, not distinct frames");
  assert(image.gateFrameState.loadTimes.length === 25, "Only recent completed loads should remain in memory");
  test.app.state.homeStatus.stream.fresh = false;
  test.app.renderHomeStatus();
  assert(pill() === "Source stale", "Successful image loads must not conceal a stale producer");
  test.app.state.homeStatus.stream.fresh = true;
  test.window.innerHeight = 704;
  test.app.renderHomeStatus();
  assert(detail().includes("Viewport 1024×704 CSS px"), "Diagnostics must reflect Safari's reduced visible height");
  test.advance(200);
  image.onerror();
  const count = image.gateFrameState.loadTimes.length;
  test.advance(5200);
  test.app.renderHomeStatus();
  assert(detail().includes("Image loads 0.0/s"), "Recent load rate must fall to zero when no new image loads");
  assert(pill() === "View interrupted", "An image error must take priority over the FPS number");
  assert(image.gateFrameState.loadTimes.length === count, "Rendering diagnostics must not mutate the on-load sample buffer");
  image.naturalWidth = 640;
  image.naturalHeight = 360;
  image.onload();
  test.app.renderHomeStatus();
  assert(detail().includes("JPEG 640×360"), "A new image profile must update displayed JPEG dimensions");
  assert(image.gateFrameState.loadTimes.length === 1, "A resumed load must discard old samples");
  assert(pill() === "View 0.2 FPS", "After a pause, show the recent rate rather than the old healthy FPS");
}

async function checkLatestSeenArrivals() {
  const test = setup();
  const unfamiliar = {
    id: 1, kind: "unmatched", plate: '<img src=x onerror="fail()">',
    captured_at: "2030-03-17 11:58:20", seen_at: 1899999900,
    image_url: "/images/original.jpg", thumbnail_url: "/previews/thumb.jpg?size=160",
  };
  const recognised = {
    id: 2, kind: "recognised", plate: "OLD123", owner: "Someone & Co",
    seen_at: 1899992800, captured_at: "2030-03-17 10:00:00",
  };
  const refresh = test.app.fetchHomeStatus();
  test.requests[0].resolve(snapshot({ arrivals: [unfamiliar, recognised] }));
  await refresh;
  const list = test.nodes["tablet-timeline-list"];
  assert(list.children.length === 2, "Mixed arrivals must appear together as exactly two records");
  const unknownRow = list.children[0];
  const knownRow = list.children[1];
  assert(unknownRow.innerHTML.includes("/previews/thumb.jpg"), "Unfamiliar arrivals must use a preview");
  assert(!unknownRow.innerHTML.includes("/images/") && unknownRow.innerHTML.includes("&lt;img"), "Unfamiliar markup must escape plate text and avoid originals");
  assert(unknownRow.innerHTML.includes("Unfamiliar") && unknownRow.innerHTML.includes("1m ago"), "Unfamiliar records need a label and age from the Pi clock");
  assert(!knownRow.innerHTML.includes("Unfamiliar") && knownRow.innerHTML.includes("Someone &amp; Co"), "Matched records must retain escaped owner text without an unfamiliar label");
  assert(knownRow.innerHTML.includes("2h ago") && knownRow.innerHTML.includes("dot-stale"), "Matched arrival text and dot must agree with the server clock");
  [unknownRow, knownRow].forEach((row) => {
    assert((row.innerHTML.match(/data-tablet-relative=/g) || []).length === 1, "Every arrival must display its relative age once across phone and wall layouts");
    assert(!row.innerHTML.includes("Latest check") && !row.innerHTML.includes("No pulse"), "Arrival records must not repeat decision context");
  });
  assert(/title="[^"]*2030-03-17 10:00:00[^"]*"/.test(knownRow.innerHTML), "The exact capture timestamp must remain available without a second visible time");
  assert(test.nodes["home-arrival-status"].hidden, "A current arrival response must not display a stale warning");
  test.advance(1000);
  const unchangedRefresh = test.app.fetchHomeStatus();
  test.requests[1].resolve(snapshot({ arrivals: [unfamiliar, recognised] }));
  await unchangedRefresh;
  assert(list.children[0] === unknownRow && list.children[1] === knownRow,
    "Unchanged arrival polls must not recreate or redownload the preview images");

  const age = test.fields[2];
  age.dataset.tabletSeenAt = String(unfamiliar.seen_at);
  age.dataset.tabletRelative = unfamiliar.captured_at;
  const dot = test.fields[0];
  dot.dataset.tabletSeenAt = String(recognised.seen_at);
  dot.dataset.tabletRelativeDot = recognised.captured_at;
  dot.classList.add("dot-fresh");
  list.querySelectorAll = (selector) => selector === "[data-tablet-relative]" ? [age]
    : selector === "[data-tablet-relative-dot]" ? [dot] : [];
  test.advance(360000);
  const failedRefresh = test.app.fetchHomeStatus();
  test.requests[2].reject(new Error("offline"));
  await failedRefresh;
  test.app.updateTabletTimelineRelativeTimes();
  assert(test.app.state.tabletEvents.length === 2 && list.children[0] === unknownRow && list.children[1] === knownRow,
    "Offline polling must preserve the last two records, including older unfamiliar sightings");
  assert(unknownRow.innerHTML.includes("Unfamiliar") && age.textContent === "7m ago", "Retained unfamiliar records must keep their label and show their increasing age");
  assert(!test.nodes["home-arrival-status"].hidden && test.nodes["home-arrival-status"].textContent.includes("last received"),
    "Failed arrival updates must clearly label retained details");
  assert(dot.classList.contains("dot-stale") && !dot.classList.contains("dot-fresh"), "Periodic dot updates must also ignore a wrong client clock");
}

function checkRecordedDecisions() {
  const test = setup();
  const decision = { id: 2, plate: "TEST123", seen_at: 1899999999,
    detail: { decision: { version: 1, reason: "allowlist_match", match_type: "exact", relay_command: "pulse_sent" } } };
  assert(test.app.describeDecision(decision).includes("Relay pulse sent"), "Decision history must retain the recorded relay outcome");
  assert(!test.app.describeDecision(decision).includes("Gate opened"), "A relay acknowledgement is not physical gate position");
  assert(test.app.describeDecision({ kind: "recognised", allowed: true }) === "", "Historical captures must not acquire invented decisions");
  const expected = { coalesced: "recent relay pulse", uncertain: "outcome uncertain", failed_before_activation: "No relay pulse sent", not_requested: "No relay pulse requested" };
  Object.keys(expected).forEach((command) => {
    decision.detail.decision.relay_command = command;
    assert(test.app.describeDecision(decision).includes(expected[command]), "Each relay outcome must remain distinct in decision history");
  });
}

function checkHomeMetrics() {
  const test = setup();
  const latest = { id: 1, kind: "unmatched", processing_time_ms: 184, confidence: 92.4 };
  const earlier = { id: 2, kind: "recognised", processing_time_ms: 999, confidence: 12.3 };
  const metrics = { temperature_c: 58.2, disk_free_pct: 73 };
  const ids = ["home-cpu-temp", "home-processing", "home-confidence", "home-disk-free"];
  const values = () => ids.map((id) => test.nodes[id].textContent);
  function prepare(measurements, arrivals) {
    applySnapshot(test, snapshot({ metrics: measurements, arrivals }));
    test.app.state.tabletEvents = arrivals;
  }
  function assertValues(expected, message) {
    assert(JSON.stringify(values()) === JSON.stringify(expected), message);
  }
  prepare(metrics, [latest, earlier]);
  test.app.renderHomeStatus();
  test.app.renderHomeArrivals();
  assertValues(["58.2°C", "184 ms", "92.4%", "73%"],
    "Home refresh must show current system measurements and the latest arrival's processing time and OCR confidence");

  prepare(metrics, [{ id: 3 }, earlier]);
  test.app.renderHomeMetrics();
  assertValues(["58.2°C", "—", "—", "73%"],
    "Missing latest-arrival measurements must not be replaced with the older matched record's measurements");
  prepare(undefined, []);
  test.app.renderHomeMetrics();
  assertValues(["—", "—", "—", "—"], "Missing measurements must display as unavailable instead of zero");
  [null, undefined, "0", "58.2", "", NaN, Infinity, -Infinity].forEach((invalid) => {
    prepare({ temperature_c: invalid, disk_free_pct: invalid },
      [{ ...latest, processing_time_ms: invalid, confidence: invalid }]);
    test.app.renderHomeMetrics();
    assertValues(["—", "—", "—", "—"], "Only finite numeric measurements may appear in Home metrics");
  });
  [-1, 101].forEach((invalidPercent) => {
    prepare({ temperature_c: -3.5, disk_free_pct: invalidPercent },
      [{ ...latest, processing_time_ms: -1, confidence: invalidPercent }]);
    test.app.renderHomeMetrics();
    assertValues(["-3.5°C", "—", "—", "—"],
      "Finite temperatures are allowed while negative processing times and percentages outside 0–100 are unavailable");
  });
  prepare({ temperature_c: 0, disk_free_pct: 0 }, [{ ...latest, processing_time_ms: 0, confidence: 0 }]);
  test.app.renderHomeMetrics();
  assert(test.nodes["home-processing"].textContent === "0 ms", "A recorded zero processing time must remain valid");
  ["home-cpu-temp", "home-confidence", "home-disk-free"].forEach((id) => {
    assert(parseFloat(test.nodes[id].textContent) === 0, "Valid zero measurements must remain visible");
  });
  prepare({ ...metrics, disk_free_pct: 100 }, [{ ...latest, confidence: 100 }]);
  test.app.renderHomeMetrics();
  assert(parseFloat(test.nodes["home-confidence"].textContent) === 100 &&
    parseFloat(test.nodes["home-disk-free"].textContent) === 100, "Percentage measurements must accept their upper boundary");

  prepare(metrics, [latest, earlier]);
  test.app.state.homeStatusError = true;
  test.app.renderHomeMetrics();
  assertValues(["—", "184 ms", "92.4%", "—"],
    "Offline updates must stop claiming current system measurements while preserving historical capture measurements");
  prepare(metrics, [latest, earlier]);
  test.advance(21000);
  test.app.renderHomeMetrics();
  assertValues(["—", "184 ms", "92.4%", "—"],
    "An expired status sample must hide current system measurements even without a network error");
}

async function checkHomePolling() {
  const test = setup();
  test.app.pollVisibleTab(test.app.fetchHomeStatus, 5000, "home");
  test.document.dispatch("visibilitychange");
  assert(test.requests.length === 1, "Home refresh must be single-flight");
  test.requests[0].resolve(snapshot({ arrivals: [{ id: 1, kind: "unmatched" }, { id: 2, kind: "recognised" }, { id: 3, kind: "recognised" }] }));
  await settle();
  assert(test.app.state.tabletEvents.length === 2, "Home must retain at most two arrivals, including mixed kinds");
  assert(test.app.state.tabletEvents[0].id === 1 && test.app.state.tabletEvents[1].id === 2,
    "The home UI must retain the backend's latest-seen order without filtering unfamiliar records");
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
  checkViewportSizing();
  checkFrameTruth();
  checkSampledSourceAge();
  checkDisplayDiagnostics();
  await checkLatestSeenArrivals();
  checkRecordedDecisions();
  checkHomeMetrics();
  await checkHomePolling();
  await checkPlateFeedback();
  await checkPlateDeadlines();
  report("Home frame/source truth, service freshness, unified arrivals, decision history, metrics, polling and plate feedback checks passed");
}
run().catch((error) => {
  report(String(error));
  if (typeof quit === "function") quit(1);
  else process.exitCode = 1;
});
