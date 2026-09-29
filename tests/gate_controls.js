// Run with node tests/gate_controls.js, or the macOS JavaScriptCore jsc binary.
// All DOM, timers, abort signals and HTTP responses below are local fakes.
const source = typeof require === "function"
  ? require("fs").readFileSync("files/web/static/app.js", "utf8")
  : readFile("files/web/static/app.js");
const report = typeof print === "function" ? print : console.log;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function setup(options = {}) {
  let now = 1700000000000;
  let nextTimer = 0;
  const timers = new Map();
  const requests = [];
  function element() {
    const classes = new Set();
    return {
      dataset: {}, disabled: false, textContent: "", listeners: {},
      classList: {
        contains: (name) => classes.has(name),
        add: (...names) => names.forEach((name) => classes.add(name)),
        remove: (...names) => names.forEach((name) => classes.delete(name)),
      },
      addEventListener(event, listener) {
        if (!this.listeners[event]) this.listeners[event] = [];
        this.listeners[event].push(listener);
      },
      click() {
        if (this.disabled) return Promise.resolve();
        return Promise.all((this.listeners.click || []).map((listener) => listener()));
      },
    };
  }
  const button = element();
  const status = element();
  const cooldown = element();
  const globalStatus = element();
  const nodes = { button, cooldown, status: globalStatus, "gate-status": status };
  const document = {
    body: { classList: { contains: () => false } },
    getElementById: (id) => nodes[id] || null,
    querySelector: () => null,
  };
  const setTimeout = (callback, delay) => {
    const id = ++nextTimer;
    timers.set(id, { callback, at: now + delay });
    return id;
  };
  function advance(ms) {
    const target = now + ms;
    while (true) {
      const next = Array.from(timers.entries())
        .filter((entry) => entry[1].at <= target)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      now = next[1].at;
      timers.delete(next[0]);
      next[1].callback();
    }
    now = target;
  }
  class AbortController {
    constructor() {
      this.listeners = [];
      this.signal = {
        aborted: false,
        addEventListener: (event, callback) => this.listeners.push(callback),
      };
    }
    abort() {
      this.signal.aborted = true;
      this.listeners.forEach((callback) => callback());
    }
  }
  const fetch = (url, init) => {
    // Theme initialization stays pending; no unrelated timer or network runs.
    if (url !== "/api/open-gate") return new Promise(() => {});
    requests.push({ url, init });
    if (options.pending) {
      return new Promise((resolve, reject) => {
        init.signal.addEventListener("abort", () => {
          const error = new Error("request deadline reached");
          error.name = "AbortError";
          reject(error);
        });
      });
    }
    if (options.networkError) return Promise.reject(new Error("connection lost"));
    const httpStatus = options.httpStatus || 503;
    return Promise.resolve({
      status: httpStatus,
      ok: httpStatus >= 200 && httpStatus < 300,
      json: async () => {
        if (options.invalidJson) throw new Error("non-JSON error response");
        return options.data || {};
      },
    });
  };
  class Clock extends Date {
    static now() { return now; }
  }
  const app = new Function(
    "document", "window", "navigator", "fetch", "Date", "AbortController", "setTimeout", "clearTimeout",
    source + "\nreturn { state, initGateButtonFor };"
  )(document, { fetch }, { userAgent: "iPad; CPU OS 12_5_8 like Mac OS X" },
    fetch, Clock, AbortController, setTimeout, (id) => timers.delete(id));
  const config = { buttonId: "button", statusId: "gate-status", cooldownId: "cooldown" };
  app.initGateButtonFor(config);
  app.initGateButtonFor(config);
  assert(button.listeners.click.length === 1, "Repeated initialization must not send duplicate gate requests");
  return { app, button, status, cooldown, globalStatus, requests, timers, advance, now: () => now };
}

function assertFailure(test, message) {
  assert(test.status.textContent === message, "Failure text must survive the request finally block");
  assert(test.globalStatus.textContent === message, "Global status must explain the failed request");
  assert(test.status.classList.contains("gate-status--error"), "Failure must retain its error style");
  assert(!test.status.classList.contains("gate-status--ready"), "A failed request must not appear successful/ready");
  assert(!test.app.state.gateOpenInFlight, "Streaming must resume after a failed request completes");
}

async function run() {
  const uncertain = setup({ data: { error: "gate_open_failed", may_have_activated: true, retry_in: 30 } });
  await uncertain.button.click();
  const uncertainty = "Gate response uncertain — check gate";
  assertFailure(uncertain, uncertainty);
  assert(uncertain.button.disabled && uncertain.button.dataset.cooldown === "true", "An uncertain pulse must prevent another click during cooldown");
  assert(uncertain.cooldown.textContent === "Retry available in 30s", "Uncertain response must show retry timing, not claim the gate is opening");
  await uncertain.button.click();
  assert(uncertain.requests.length === 1, "A disabled gate button must not retry an uncertain actuation");
  uncertain.advance(29000);
  assertFailure(uncertain, uncertainty);
  assert(uncertain.button.disabled, "Do not release the button before the full cooldown expires");
  uncertain.advance(1000);
  assert(!uncertain.button.disabled && !uncertain.button.dataset.cooldown, "Cooldown must eventually allow a deliberate retry");
  assert(!uncertain.requests[0].init.signal.aborted, "A completed request must clear its abort deadline");
  assert(uncertain.timers.size === 0, "Completed cooldown must not leave timers running");

  const preactivation = setup({ data: { error: "gate_open_failed", may_have_activated: false, retry_in: 0 } });
  const pending = preactivation.button.click();
  assert(preactivation.button.disabled && preactivation.app.state.gateOpenInFlight, "Disable the button and pause video while the request is in flight");
  await pending;
  assertFailure(preactivation, "Open failed");
  assert(!preactivation.button.disabled && !preactivation.button.dataset.cooldown, "Known pre-activation failure must permit a deliberate retry");
  assert(preactivation.timers.size === 0, "Pre-activation failure must clear its request deadline");
  const request = preactivation.requests[0].init;
  assert(request.method === "POST" && request.cache === "no-store", "Gate requests must use an uncached POST");
  assert(Number(request.headers["X-Gate-Requested-At"]) === preactivation.now(), "Freshness header must use the current request time");
  assert(JSON.parse(request.body).requested_at_ms === preactivation.now(), "Freshness body must match the header");

  const expired = setup({ httpStatus: 409, data: { error: "stale_open_request" } });
  await expired.button.click();
  assertFailure(expired, "Open request expired");
  assert(!expired.button.disabled, "An expired request must allow a new, deliberate request");
  assert(expired.requests.length === 1, "Expired gate requests must not automatically retry");

  const deadline = setup({ pending: true });
  const waiting = deadline.button.click();
  deadline.advance(9999);
  assert(deadline.button.disabled && deadline.app.state.gateOpenInFlight, "Wait until the deadline before aborting a pending request");
  assert(!deadline.requests[0].init.signal.aborted, "Do not abort early");
  deadline.advance(1);
  await waiting;
  assertFailure(deadline, "Open timed out");
  assert(deadline.requests[0].init.signal.aborted, "The request deadline must abort the fetch");
  assert(!deadline.button.disabled && deadline.requests.length === 1, "Timeout must restore deliberate control without automatic retry");

  for (const options of [{ networkError: true }, { invalidJson: true }]) {
    const failure = setup(options);
    await failure.button.click();
    assertFailure(failure, "Open failed");
    assert(!failure.button.disabled, "Unexpected failures must not leave the button disabled indefinitely");
  }

  const coalesced = setup({ httpStatus: 429, data: { retry_in: 3 } });
  await coalesced.button.click();
  assert(coalesced.button.disabled && coalesced.status.textContent === "Gate opening", "Ordinary cooldown must retain its existing opening display");
  coalesced.advance(3000);
  assert(!coalesced.button.disabled && coalesced.status.textContent === "Gate ready", "Ordinary cooldown must restore the ready state");
  report("Gate control error, uncertainty, cooldown and deadline checks passed");
}

run().catch((error) => {
  report(String(error));
  if (typeof quit === "function") quit(1);
  else process.exitCode = 1;
});
