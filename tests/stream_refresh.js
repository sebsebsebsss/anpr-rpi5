// Run from the repository root with node tests/stream_refresh.js (or jsc).
const source = typeof require === "function"
  ? require("fs").readFileSync("files/web/static/app.js", "utf8")
  : readFile("files/web/static/app.js");
const report = typeof print === "function" ? print : console.log;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function setup(options = {}) {
  let now = 0;
  let nextId = 0;
  const timers = new Map();
  const requests = [];
  let loaded = 0;
  const document = {
    hidden: false,
    body: { classList: { contains: (name) => name === "fullscreen-page" && !!options.kiosk } },
    getElementById: () => null,
    querySelector: () => null,
  };
  const setTimeout = (callback, delay) => {
    const id = ++nextId;
    timers.set(id, { callback, at: now + delay });
    return id;
  };
  const clearTimeout = (id) => timers.delete(id);
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
  class Image {
    set src(url) {
      // A new URL cancels an unfinished frame, as in the browser.
      clearTimeout(this.pending);
      requests.push(url);
      if (options.stalled) return;
      this.pending = setTimeout(() => {
        if (options.error) {
          if (this.onerror) this.onerror();
        } else {
          loaded += 1;
          if (this.onload) this.onload();
        }
      }, options.delay === undefined ? 800 : options.delay);
    }
    closest() {
      return { classList: { contains: () => !options.inactive } };
    }
    removeAttribute(name) {
      assert(name === "src", "Only the pending image should be cancelled");
      clearTimeout(this.pending);
    }
  }
  // Load the actual application, with unrelated startup fetches left pending.
  const fetch = () => new Promise(() => {});
  const app = new Function(
    "document", "window", "navigator", "Image", "setTimeout", "clearTimeout", "fetch", "Date",
    source + "\nreturn { createLegacyStreamImage, homeStreamUrl, state, LEGACY_IOS };"
  )(document, { fetch, innerWidth: options.width || 1280 }, { userAgent: options.userAgent || "iPad; CPU OS 12_5_8 like Mac OS X" },
    Image, setTimeout, clearTimeout, fetch, { now: () => now });
  if (!options.userAgent) assert(app.LEGACY_IOS, "iPad mini 3 must use the legacy stream");
  app.createLegacyStreamImage("/static/stream.jpg?camera=gate");
  return { app, advance, requests, options, document, state: app.state, loaded: () => loaded };
}

const slow = setup();
slow.advance(799);
assert(slow.requests.length === 1, "Do not replace a frame still loading after 200 ms");
slow.advance(4201);
assert(slow.loaded() === 6, "Slow frames must keep displaying instead of being cancelled");
assert(new Set(slow.requests).size === slow.requests.length, "Each request must bypass cache");
assert(slow.requests.every((url) => url.includes("?camera=gate&ts=")), "Preserve existing query parameters");

const fast = setup({ delay: 0 });
fast.advance(1000);
assert(fast.loaded() === 6, "Fast loads must still respect the refresh interval");
fast.advance(9500);
assert(fast.loaded() === 53, "Completed requests must clear their watchdogs");

const failed = setup({ error: true, delay: 100 });
failed.advance(1099);
assert(failed.requests.length === 1, "Errors must back off before retrying");
failed.options.error = false;
failed.advance(101);
assert(failed.loaded() === 1, "A failed request must not stop subsequent frames");

const stalled = setup({ stalled: true });
stalled.advance(10999);
assert(stalled.requests.length === 1, "Allow a stalled request its timeout and retry delay");
stalled.options.stalled = false;
stalled.advance(801);
assert(stalled.loaded() === 1, "Recover when a request never fires load or error");

const paused = setup();
paused.state.gateOpenInFlight = true;
paused.advance(3000);
assert(paused.requests.length === 1, "Pause new image requests during a gate open");
paused.state.gateOpenInFlight = false;
paused.document.hidden = true;
paused.advance(3000);
assert(paused.requests.length === 1, "Pause new image requests while the page is hidden");
paused.document.hidden = false;
paused.advance(1100);
assert(paused.loaded() === 2, "Resume frames when the page is visible again");

const paced = setup({ delay: 150 });
paced.advance(1000);
assert(paced.loaded() === 5, "Download time must count toward the frame interval, not add to it");
const inactive = setup({ inactive: true });
inactive.advance(2000);
assert(inactive.requests.length === 0, "A hidden app tab must not compete for image bandwidth");
inactive.options.inactive = false;
inactive.advance(1100);
assert(inactive.loaded() >= 1, "Switching back to the stream must resume frames");

const profiles = { url: "/static/stream.jpg", profiles: {
  tablet: "/static/stream-tablet.jpg", kiosk: "/static/stream-kiosk.jpg",
} };
assert(setup().app.homeStreamUrl(profiles) === profiles.profiles.tablet, "Old iPad Home uses its smaller JPEG");
assert(setup({kiosk: true, userAgent: "Chrome"}).app.homeStreamUrl(profiles) === profiles.profiles.kiosk, "Pi one-pager uses the smallest JPEG");
assert(setup({userAgent: "Chrome", width: 1440}).app.homeStreamUrl(profiles) === profiles.url, "Large desktop Home retains full-size JPEG");
assert(setup({userAgent: "Chrome", width: 390}).app.homeStreamUrl(profiles) === profiles.profiles.tablet, "Phone Home can use the compact JPEG");
assert(setup().app.homeStreamUrl({url: profiles.url}) === profiles.url, "Older servers retain the original feed");
assert(setup().app.homeStreamUrl({url: profiles.url, profiles: {tablet: "https://untrusted.example/frame"}}) === profiles.url, "Profile selection accepts only known local JPEG paths");
report("Stream refresh and device profile regression checks passed");
