// Run with node tests/history.js, or the macOS JavaScriptCore jsc binary.
const source = typeof require === "function"
  ? require("fs").readFileSync("files/web/static/app.js", "utf8")
  : readFile("files/web/static/app.js");
const report = typeof print === "function" ? print : console.log;
function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function setup() {
  const requests = [];
  const images = [];
  function node(tag = "div") {
    return {
      tag, dataset: {}, children: [], top: 0,
      classList: { contains: () => true, add: () => {}, remove: () => {}, toggle: () => {} },
      appendChild(child) { this.children.push(child); },
      addEventListener() {},
      getBoundingClientRect() { return { top: this.top, bottom: this.top + 100 }; },
      set innerHTML(value) { this.children = []; },
      querySelectorAll() { return []; },
    };
  }
  const list = node();
  const panel = node();
  const previous = node();
  const next = node();
  const more = node();
  const document = {
    body: { classList: { contains: () => false } },
    getElementById(id) {
      return { "events-list": list, "tab-candidates": panel, "history-prev": previous,
        "history-next": next, "events-more": more }[id] || null;
    },
    querySelector: () => null,
    querySelectorAll(selector) {
      const found = [];
      function visit(item) {
        if (item.tag === "img" && item.dataset.previewSrc) found.push(item);
        item.children.forEach(visit);
      }
      if (selector === "#events-list img[data-preview-src]") visit(list);
      return found;
    },
    createElement(tag) {
      const element = node(tag);
      if (tag === "img") images.push(element);
      return element;
    },
  };
  const fetch = (url) => {
    if (!url.startsWith("/api/history?")) return new Promise(() => {});
    return new Promise((resolve) => requests.push({ url, resolve: (data) => resolve({ ok: true, json: async () => data }) }));
  };
  class SearchParams {
    constructor(data) { this.data = data; }
    set(key, value) { this.data[key] = value; }
    toString() { return Object.keys(this.data).map((key) => `${key}=${encodeURIComponent(this.data[key])}`).join("&"); }
  }
  const app = new Function("document", "window", "navigator", "fetch", "URLSearchParams",
    source + "\nreturn { state, fetchEvents, deferHistoryImage, loadVisibleHistoryImages };"
  )(document, { fetch, innerHeight: 800 }, { userAgent: "iPad; CPU OS 12_5_8 like Mac OS X" }, fetch, SearchParams);
  return { app, requests, list, previous, next, images, node };
}

function page(offset, hasMore) {
  return {
    items: Array.from({ length: 30 }, (_, index) => ({
      id: offset + index + 1,
      captured_at: new Date(Date.UTC(2026, 8, 29, 12) - (offset + index) * 120000).toISOString().slice(0, 19).replace("T", " "),
      kind: "recognised", plate: "SYNTHETIC", confidence: 90,
      image_url: `/images/${offset + index}.jpg`,
      preview_url: `/previews/${offset + index}.jpg?size=640`,
      thumbnail_url: `/previews/${offset + index}.jpg?size=160`,
    })),
    has_more: hasMore,
    next_cursor: hasMore ? `cursor-${offset + 30}` : null,
  };
}

async function run() {
  const test = setup();
  for (let index = 0; index < 40; index++) {
    const pending = test.app.fetchEvents({ page: index });
    test.requests[index].resolve(page(index * 30, index < 39));
    await pending;
    assert(test.app.state.events.length === 30, "History must retain only one page of events");
    assert(test.list.children.length === 30, "History DOM must remain bounded across forty pages");
  }
  assert(test.images.every((image) => !image.src || image.src.startsWith("/previews/")), "Cards must never eagerly request originals");
  assert(test.next.disabled, "Older button must stop at the end of history");
  await test.app.fetchEvents({ page: 40 });
  assert(test.requests.length === 40, "Exhausted history must not send another request");
  const previous = test.app.fetchEvents({ page: 38 });
  assert(test.requests[40].url.includes("cursor=cursor-1140"), "Newer navigation must reuse its saved cursor");
  test.requests[40].resolve(page(1140, true));
  await previous;
  assert(test.app.state.eventsPage === 38, "Previous page must replace current page");

  // An older response arriving after a filter change must not overwrite it.
  const oldRequest = test.app.fetchEvents({ reset: true });
  test.app.state.eventsKinds = new Set(["unmatched"]);
  const newRequest = test.app.fetchEvents({ reset: true });
  assert(test.requests[42].url.includes("kind=unmatched"), "Filters must be sent to the server");
  test.requests[42].resolve({ items: [], has_more: false, next_cursor: null });
  await newRequest;
  test.requests[41].resolve(page(0, true));
  await oldRequest;
  assert(test.app.state.events.length === 0, "Stale requests must not restore outdated filter results");

  // No native loading or IntersectionObserver exists in this test browser.
  const offscreen = test.node("img");
  offscreen.top = 2000;
  test.list.appendChild(offscreen);
  test.app.deferHistoryImage(offscreen, "/previews/deferred.jpg?size=640");
  test.app.loadVisibleHistoryImages();
  assert(!offscreen.src, "iOS12 must defer offscreen preview requests");
  offscreen.top = 100;
  test.app.loadVisibleHistoryImages();
  assert(offscreen.src === "/previews/deferred.jpg?size=640", "Visible previews must load without native lazy loading");
  report("History pagination, race, preview and iOS12 lazy-loading checks passed");
}

run().catch((error) => {
  report(String(error));
  if (typeof quit === "function") quit(1);
  else process.exitCode = 1;
});
