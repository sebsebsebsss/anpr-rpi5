// Run with node tests/web_diagnostics.js or the macOS JavaScriptCore jsc binary.
// Requests, DOM events and timers are fake; this never talks to a gate.
const source = typeof require === "function"
  ? require("fs").readFileSync("files/web/static/app.js", "utf8")
  : readFile("files/web/static/app.js");
const report = typeof print === "function" ? print : console.log;
function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function setup({ legacyIos = true } = {}) {
  const requests = [];
  const timers = new Map();
  let timerId = 0;
  function node(id) {
    const classes = new Set();
    return {
      id, dataset: {}, children: [], listeners: {}, textContent: "", attached: true,
      classList: {
        contains: (name) => classes.has(name),
        add: (name) => classes.add(name),
        remove: (name) => classes.delete(name),
        toggle: (name, on) => on ? classes.add(name) : classes.delete(name),
      },
      setAttribute() {},
      appendChild(child) { child.attached = true; this.children.push(child); },
      set innerHTML(value) {
        this.children.forEach((child) => { child.attached = false; });
        this.children = [];
      },
      addEventListener(name, callback) {
        if (!this.listeners[name]) this.listeners[name] = [];
        this.listeners[name].push(callback);
      },
      dispatch(name) { (this.listeners[name] || []).forEach((callback) => callback()); },
      getBoundingClientRect() { return { top: 0, bottom: 100 }; },
    };
  }
  const ids = [
    "tab-home", "tab-candidates", "tab-timeline", "tab-stream", "tab-stats",
    "events-list", "events-more", "history-prev", "history-next",
    "timeline-list", "timeline-more", "timeline-page-info", "timeline-prev", "timeline-next",
    "stream-frame", "stream-status", "stream-fps", "stream-lag", "stream-health", "stream-banner", "stream-system",
    "stats-chart", "insight-top-plate-card", "insight-top-unmatched-card",
  ];
  const nodes = {};
  ids.forEach((id) => { nodes[id] = node(id); });
  nodes["stream-frame"].appendChild(nodes["stream-fps"]);
  nodes["stream-frame"].appendChild(nodes["stream-lag"]);
  nodes["stats-chart"].getContext = () => ({});
  const timelineChips = ["30d", "7d"].map((value) => {
    const chip = node(value);
    chip.dataset.window = value;
    return chip;
  });
  const panels = ids.filter((id) => id.startsWith("tab-")).map((id) => nodes[id]);
  const document = {
    hidden: false,
    listeners: {},
    body: { classList: { contains: () => false } },
    getElementById: (id) => nodes[id] && nodes[id].attached ? nodes[id] : null,
    querySelector: () => null,
    querySelectorAll(selector) {
      if (selector === ".tab-panel") return panels;
      if (selector === "#tab-timeline .chip") return timelineChips;
      return [];
    },
    createElement: node,
    addEventListener: node("").addEventListener,
    dispatch: node("").dispatch,
  };
  const fetch = (url) => {
    if (url.startsWith("/api/ui-settings")) return new Promise(() => {});
    return new Promise((resolve, reject) => requests.push({
      url, reject,
      resolve: (data) => resolve({ ok: true, json: async () => data }),
    }));
  };
  const setTimeout = (callback, delay) => {
    const id = ++timerId;
    timers.set(id, { callback, delay });
    return id;
  };
  class SearchParams {
    constructor(data) { this.data = data; }
    set(key, value) { this.data[key] = value; }
    toString() { return Object.keys(this.data).map((key) => `${key}=${encodeURIComponent(this.data[key])}`).join("&"); }
  }
  const app = new Function(
    "document", "window", "navigator", "fetch", "URLSearchParams", "history", "setTimeout", "clearTimeout",
    source + `
      // Isolate image loading and fullscreen APIs while exercising the real
      // initStream DOM replacement and diagnostic initialization below.
      createLegacyStreamImage = () => document.createElement("legacy-image");
      createSmoothImageStream = () => document.createElement("smooth-image");
      startStreamFps = () => {};
      setupStreamFullscreen = () => {};
      return { state, initTimeline, fetchTimeline, initStats, initStreamLag, initStreamHealth, initSystemHealth, setActiveTab };
    `
  )(document, { fetch, innerHeight: 800, addEventListener() {} },
    { userAgent: legacyIos ? "iPad; CPU OS 12_5_8 like Mac OS X" : "Chrome" }, fetch, SearchParams,
    { replaceState() {} }, setTimeout, (id) => timers.delete(id));
  app.state.streamLoaded = true;
  app.state.tabletLoaded = true;
  function fireTimers() {
    const due = Array.from(timers.entries());
    due.forEach(([id, timer]) => {
      timers.delete(id);
      timer.callback();
    });
  }
  return { app, requests, nodes, document, timelineChips, timers, fireTimers };
}

async function settle() {
  // Flush the nested fetch/json/poller promise continuations.
  for (let index = 0; index < 8; index++) await Promise.resolve();
}

function timelinePage(plate) {
  return { items: [{ kind: "recognised", plate }], page: 1, total: 1, total_pages: 1 };
}

async function checkTimeline() {
  const test = setup();
  test.app.initTimeline();
  assert(test.requests[0].url.includes("window=30d"), "Initial timeline must request 30 days");
  test.timelineChips[1].dispatch("click");
  assert(test.requests.length === 2, "Changing the filter during a request must fetch the new period");
  assert(test.requests[1].url.includes("window=7d"), "Selected seven-day period must reach the server");
  test.requests[0].resolve(timelinePage("OUTDATED"));
  await settle();
  assert(test.app.state.loadingTimelineEvents, "An old response cannot clear the new request's loading state");
  assert(test.app.state.timelineEvents.length === 0, "An old response cannot render under the new filter");
  test.requests[1].resolve(timelinePage("CURRENT"));
  await settle();
  assert(test.app.state.timelineEvents[0].plate === "CURRENT", "The selected period must render");
  assert(!test.app.state.loadingTimelineEvents, "Latest response must clear the loading state");

  const older = test.app.fetchTimeline();
  test.app.state.timelineWindow = "30d";
  const newer = test.app.fetchTimeline();
  test.requests[3].resolve(timelinePage("LATEST"));
  await newer;
  test.requests[2].reject(new Error("old request failed"));
  await older;
  assert(test.app.state.timelineEvents[0].plate === "LATEST", "A late response cannot replace newer results");
  assert(test.nodes["timeline-more"].textContent !== "Failed to load timeline", "A stale failure cannot replace newer success");
}

async function checkStatsShortcuts() {
  const test = setup();
  test.app.state.eventsLoaded = true;
  test.app.state.eventsKinds = new Set(["recognised"]);
  test.app.state.events = [{ id: 1, kind: "recognised", plate: "OLD" }];
  test.app.state.eventsWindow = "7d";
  test.app.state.eventsPage = 4;
  test.app.initStats();
  for (const [card, kind] of [["insight-top-unmatched-card", "unmatched"], ["insight-top-plate-card", "recognised"]]) {
    const before = test.requests.filter((item) => item.url.startsWith("/api/history?")).length;
    test.nodes[card].dispatch("click");
    const historyRequests = test.requests.filter((item) => item.url.startsWith("/api/history?"));
    assert(historyRequests.length === before + 1, "Each shortcut must fetch one new server-filtered page");
    const request = historyRequests[historyRequests.length - 1];
    assert(request.url.includes(`kind=${kind}`), "Shortcut category must reach the server");
    assert(request.url.includes("window=7d") && !request.url.includes("cursor="), "Shortcut must keep dates and reset pagination");
    request.resolve({ items: [{ id: 2, kind, plate: "CURRENT" }], has_more: false, next_cursor: null });
    await settle();
    assert(test.app.state.eventsPage === 0 && test.app.state.events[0].kind === kind, "Shortcut must replace the previous page");
  }
}

async function checkDiagnosticPolling() {
  const test = setup();
  test.app.setActiveTab("stream");
  test.app.initStreamLag();
  test.app.initStreamHealth();
  test.app.initSystemHealth();
  assert(test.requests.length === 3, "Live diagnostics must load immediately");
  test.app.setActiveTab("stream");
  test.document.dispatch("visibilitychange");
  assert(test.requests.length === 3, "Repeated resume events must not overlap pending requests");
  test.requests[0].resolve({ lag_ms: null });
  test.requests[1].resolve({ stream_stale: false });
  test.requests[2].resolve({});
  await settle();
  assert(test.nodes["stream-lag"].textContent === "Lag: --", "A missing frame must not claim zero lag");
  assert(test.timers.size === 3, "Visible diagnostics must schedule their next update");
  test.app.setActiveTab("home");
  assert(test.timers.size === 0, "Leaving Live must cancel all diagnostic timers");
  test.fireTimers();
  assert(test.requests.length === 3, "Home must not poll Live diagnostics");
  test.app.setActiveTab("stream");
  assert(test.requests.length === 6, "Returning to Live must refresh immediately");
  test.document.hidden = true;
  test.document.dispatch("visibilitychange");
  test.requests[3].resolve({ lag_ms: 1200 });
  test.requests[4].resolve({ stream_stale: false });
  test.requests[5].resolve({});
  await settle();
  assert(test.nodes["stream-lag"].textContent === "Lag: 1.2s", "Real lag must remain numeric");
  assert(test.timers.size === 0, "Requests finishing while hidden must not restart polling");
  test.document.hidden = false;
  test.document.dispatch("visibilitychange");
  assert(test.requests.length === 9, "Unhiding Live must refresh immediately");
  test.requests.slice(6).forEach((request) => request.reject(new Error("offline")));
  await settle();
  assert(test.timers.size === 3, "A transient failure must still schedule a later retry");
  test.fireTimers();
  assert(test.requests.length === 12, "The next timer must retry each failed diagnostic");
}

async function checkStreamInitialization() {
  for (const legacyIos of [true, false]) {
    const test = setup({ legacyIos });
    test.app.state.streamLoaded = false;
    test.app.setActiveTab("stream");
    assert(test.requests.length === 1 && test.requests[0].url === "/api/stream", "Live tab must initialize its source first");
    test.requests[0].resolve({ url: "/static/stream.jpg" });
    await settle();
    const frame = test.nodes["stream-frame"];
    assert(frame.children.some((child) => child.id === (legacyIos ? "legacy-image" : "smooth-image")), "Test must exercise both stream branches");
    assert(test.document.getElementById("stream-lag") === test.nodes["stream-lag"], "Stream initialization must preserve the lag label in the DOM");
    assert(frame.children.includes(test.nodes["stream-fps"]), "Stream initialization must preserve FPS alongside lag");
    const lag = test.requests.find((request) => request.url === "/api/stream-lag");
    assert(lag, "Actual Live initialization must start the lag request");
    lag.resolve({ lag_ms: 400 });
    await settle();
    assert(test.nodes["stream-lag"].textContent === "Lag: 0.4s", "The preserved lag label must receive the API result");
  }
}

async function run() {
  await checkTimeline();
  await checkStatsShortcuts();
  await checkDiagnosticPolling();
  await checkStreamInitialization();
  report("Timeline races, stats shortcuts, Live initialization, stream lag and visible-only diagnostic polling checks passed");
}
run().catch((error) => {
  report(String(error));
  if (typeof quit === "function") quit(1);
  else process.exitCode = 1;
});
