const statusEl = document.getElementById("status");
const isAdmin = document.body.classList.contains("admin-page");
const isStats = document.body.classList.contains("stats-page");
const isMain = document.body.classList.contains("main-page");

const state = {
  plates: [],
  plateStatus: {},
  platesSavedSnapshot: null,
  platesSaving: false,
  platesMessage: "",
  platesMessageType: "",
  events: [],
  eventsPage: 0,
  eventsCursors: [null],
  eventsNextCursor: null,
  eventsHasMore: false,
  eventsLoaded: false,
  eventsWindow: "30d",
  eventsStartId: null,
  eventsRequest: 0,
  loadingCandidateEvents: false,
  eventsKinds: new Set(["recognised", "unmatched"]),
  latestRecognised: null,
  streamLoaded: false,
  statsLoaded: false,
  timelineLoaded: false,
  tabletLoaded: false,
  logsLoaded: false,
  timelineEvents: [],
  timelinePage: 1,
  timelinePerPage: 25,
  timelineTotal: 0,
  timelineTotalPages: 1,
  timelineWindow: "30d",
  timelineRequest: 0,
  tabletEvents: [],
  homeStatus: null,
  homeStatusReceivedAt: null,
  homeStatusError: false,
  homeFrame: null,
  homeStreamError: "",
  homeSourceIsLocal: false,
  unfamiliarRenderedId: null,
  decisionRenderedKey: "",
  logsLastFetch: 0,
  logsLoading: false,
  logsLines: [],
  logsFilterText: "",
  logsWarnOnly: false,
  logsWrap: true,
  logsUpdatedAt: "",
  logsHideAccess: true,
  logsHideDeprecations: true,
  themeMode: "light",
  themeSun: null,
  themeLastFetch: 0,
  lastGateOpenTs: null,
  lastGateOpenMeta: null,
  loadingTimelineEvents: false,
  gateOpenInFlight: false,
};
const visibleTabPollers = [];
const HOME_STATUS_MAX_AGE_MS = 20000;
const DISPLAY_STALE_MS = 10000;
const DISPLAY_LOAD_WINDOW_MS = 5000;

const LEGACY_IOS =
  /iP(ad|hone|od)/.test(navigator.userAgent || "") &&
  /OS 12_/.test(navigator.userAgent || "");

let GROUP_WINDOW_SEC = 60;
let STREAM_REFRESH_MS = 200;
const OPEN_GATE_TIMEOUT_MS = 10000;
const apiSecretMeta = document.querySelector('meta[name="gate-api-secret"]');
const API_SHARED_SECRET = apiSecretMeta
  ? (apiSecretMeta.getAttribute("content") || "")
  : "";
const nativeFetch = window.fetch.bind(window);

function isApiRequest(resource) {
  try {
    if (typeof resource === "string" || resource instanceof URL) {
      const url = new URL(resource, window.location.href);
      return url.origin === window.location.origin && url.pathname.startsWith("/api/");
    }
    if (resource instanceof Request) {
      const url = new URL(resource.url, window.location.href);
      return url.origin === window.location.origin && url.pathname.startsWith("/api/");
    }
  } catch (err) {
    return false;
  }
  return false;
}

window.fetch = function fetchWithApiSecret(resource, init = {}) {
  if (!API_SHARED_SECRET || !isApiRequest(resource)) {
    return nativeFetch(resource, init);
  }

  if (resource instanceof Request) {
    const headers = new Headers(resource.headers);
    headers.set("X-Gate-Api-Secret", API_SHARED_SECRET);
    const nextRequest = new Request(resource, { headers });
    return nativeFetch(nextRequest, init);
  }

  const headers = new Headers(init.headers || {});
  headers.set("X-Gate-Api-Secret", API_SHARED_SECRET);
  return nativeFetch(resource, { ...init, headers });
};

const HTML_ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

// Escape server-derived values before interpolating them into innerHTML
// templates. Plates/owners/uuids/log lines/request IPs all originate outside
// the browser and must never be treated as markup.
function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);
}

function normalizeKind(kind) {
  return kind === "candidate" ? "unmatched" : kind;
}

function normalizeEventKind(event) {
  const base = normalizeKind(event.kind);
  if (base === "unmatched" && event.owner) {
    return "recognised";
  }
  return base;
}

function describeDecision(event) {
  const decision = event && event.detail && event.detail.decision;
  if (!decision || decision.version !== 1) return "";
  const reasons = {
    allowlist_match: "Allowlist match",
    not_allowlisted: "Plate not on allowlist",
    no_candidates: "No plate read",
    recent_allowlisted: "Allowlisted vehicle already handled",
    recent_vehicle: "Vehicle recently handled",
    recent_unmatched: "Repeated unmatched read",
    stale_capture: "Capture too old to act on",
    relay_error: "Relay command failed",
  };
  const matches = { exact: "Exact allowlist match", normalised: "Matched with OCR character correction", fuzzy: "Similar plate matched" };
  const commands = {
    pulse_sent: "Relay pulse sent",
    coalesced: "Used a recent relay pulse",
    not_requested: "No relay pulse requested",
    failed_before_activation: "No relay pulse sent",
    uncertain: "Relay outcome uncertain",
  };
  const reason = decision.reason === "allowlist_match" && matches[decision.match_type]
    ? matches[decision.match_type] : reasons[decision.reason];
  return [reason, commands[decision.relay_command]].filter(Boolean).join(" • ");
}

function setStatus(msg) {
  if (!statusEl) return;
  statusEl.textContent = msg;
}

function applyTheme(mode, sun) {
  const body = document.body;
  if (!body) return;
  let useDark = false;
  if (mode === "dark") {
    useDark = true;
  } else if (mode === "auto" && sun) {
    const now = Math.floor(Date.now() / 1000);
    const sunrise = sun.sunrise_ts;
    const sunset = sun.sunset_ts;
    const sunriseNext = sun.sunrise_next_ts;
    if (sunrise && now < sunrise) {
      useDark = true;
    } else if (sunset && now >= sunset) {
      useDark = true;
      if (sunriseNext && now >= sunriseNext) {
        useDark = false;
      }
    }
  }
  body.classList.toggle("theme-dark", useDark);
}

function updateThemeStatus(data) {
  const status = document.getElementById("theme-mode-status");
  if (!status) return;
  if (!data) {
    status.textContent = "Unable to load settings.";
    return;
  }
  if (data.theme_mode === "auto" && data.sunrise_ts && data.sunset_ts) {
    const sunrise = new Date(data.sunrise_ts * 1000).toLocaleTimeString();
    const sunset = new Date(data.sunset_ts * 1000).toLocaleTimeString();
    const now = Math.floor(Date.now() / 1000);
    const until = now < data.sunrise_ts
      ? data.sunrise_ts
      : now < data.sunset_ts
      ? data.sunset_ts
      : data.sunrise_next_ts || data.sunrise_ts;
    const untilLabel = until
      ? new Date(until * 1000).toLocaleTimeString()
      : "--";
    status.textContent = `Auto uses ${data.timezone} (sunrise ${sunrise}, sunset ${sunset}) • next switch ${untilLabel}.`;
  } else {
    status.textContent = `Theme set to ${data.theme_mode}.`;
  }
}

async function initTheme() {
  try {
    const resp = await fetch("/api/ui-settings");
    const data = await resp.json();
    state.themeMode = data.theme_mode || "light";
    state.themeSun = data;
    state.themeLastFetch = Date.now();
    applyTheme(state.themeMode, state.themeSun);
    updateThemeStatus(data);
    const selector = document.getElementById("theme-mode");
    if (selector) {
      selector.value = state.themeMode;
      selector.addEventListener("change", async () => {
        const next = selector.value;
        const saveResp = await fetch("/api/ui-settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ theme_mode: next }),
        });
        const saved = await saveResp.json();
        if (saved.error) {
          updateThemeStatus({ theme_mode: "light" });
          return;
        }
        state.themeMode = next;
        updateThemeStatus({ theme_mode: next, ...state.themeSun });
        applyTheme(state.themeMode, state.themeSun);
      });
    }
    setInterval(async () => {
      if (state.themeMode !== "auto") return;
      const now = Date.now();
      const sun = state.themeSun;
      applyTheme(state.themeMode, sun);
      if (!sun || now - state.themeLastFetch > 21600000) {
        try {
          const refresh = await fetch("/api/ui-settings");
          const refreshed = await refresh.json();
          state.themeSun = refreshed;
          state.themeLastFetch = Date.now();
          applyTheme(state.themeMode, state.themeSun);
          updateThemeStatus(refreshed);
        } catch (err) {
          return;
        }
      }
    }, 300000);
  } catch (err) {
    updateThemeStatus(null);
  }
}

function updateStatusTimestamp() {
  setStatus(new Date().toLocaleTimeString());
}

function coalesce(value, fallback) {
  return value === null || value === undefined ? fallback : value;
}

function isTabActive(name) {
  const panel = document.getElementById(`tab-${name}`);
  return Boolean(panel && panel.classList.contains("active"));
}

function setLowPriorityImage(img) {
  if (!img) return;
  img.loading = "lazy";
  img.decoding = "async";
  if ("fetchPriority" in img) {
    img.fetchPriority = "low";
  }
}

async function fetchOpenGate() {
  const requestedAtMs = Date.now();
  const init = {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-Gate-Requested-At": String(requestedAtMs),
    },
    body: JSON.stringify({ requested_at_ms: requestedAtMs }),
    priority: "high",
  };
  if (typeof AbortController === "undefined") {
    return fetch("/api/open-gate", init);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), OPEN_GATE_TIMEOUT_MS);
  try {
    return await fetch("/api/open-gate", { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function getGateEls({ buttonId, statusId, cooldownId }) {
  return {
    gateBtn: document.getElementById(buttonId),
    gateStatus: document.getElementById(statusId),
    cooldownEl: document.getElementById(cooldownId),
  };
}

function setGateStatus(gateStatus, state, label) {
  if (!gateStatus) return;
  gateStatus.classList.remove("gate-status--ready", "gate-status--opening", "gate-status--error");
  if (state) {
    gateStatus.classList.add(`gate-status--${state}`);
  }
  if (label) {
    gateStatus.textContent = label;
  }
}

function setGateButtonState(gateBtn, state) {
  if (!gateBtn) return;
  gateBtn.classList.remove("gate-button--opening");
  if (state === "opening") {
    gateBtn.classList.add("gate-button--opening");
  }
}

function getAgeMinutes(ts) {
  const date = parseLocalTimestamp(ts);
  if (!date || Number.isNaN(date.getTime())) return null;
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) return 0;
  return Math.floor(diffMs / 60000);
}

function parseLocalTimestamp(ts) {
  if (!ts) return null;
  if (ts instanceof Date) return ts;
  const match = String(ts).match(
    /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/
  );
  if (match) {
    return new Date(
      Number(match[1]),
      Number(match[2]) - 1,
      Number(match[3]),
      Number(match[4]),
      Number(match[5]),
      Number(match[6] || 0)
    );
  }
  const fallback = new Date(ts);
  if (Number.isNaN(fallback.getTime())) return null;
  return fallback;
}

function createLegacyStreamImage(url) {
  const img = new Image();
  img.alt = "Live stream";
  img.className = "stream-image-single";
  // Updated only by this displayed image's own load/error/watchdog events.
  img.gateFrameState = {
    loadedAt: null, lastResult: "loading", startedAt: Date.now(), loadTimes: [], width: 0, height: 0,
  };
  let timer = null;
  let requestTimeout = null;
  let requestStartedAt = 0;
  const schedule = (delay) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(loadNext, delay);
  };
  const finish = (delay) => {
    clearTimeout(requestTimeout);
    img.onload = null;
    img.onerror = null;
    schedule(delay);
  };
  const loadNext = () => {
    const panel = img.closest ? img.closest(".tab-panel") : null;
    if (document.hidden || state.gateOpenInFlight || (panel && !panel.classList.contains("active"))) {
      schedule(Math.max(250, STREAM_REFRESH_MS));
      return;
    }
    // Wait for this frame before requesting another. Replacing src on a
    // fixed timer can continually cancel slow loads on older iPads.
    requestStartedAt = Date.now();
    // The interval includes download/decode time; adding it after each load
    // unnecessarily reduced 10 fps streams to 3–4 fps on slower screens.
    img.onload = () => {
      const frame = img.gateFrameState;
      const loadedAt = Date.now();
      frame.loadedAt = loadedAt;
      frame.lastResult = "loaded";
      frame.width = img.naturalWidth || 0;
      frame.height = img.naturalHeight || 0;
      frame.loadTimes.push(loadedAt);
      // Count browser image-load completions, not distinct camera frames.
      // Only load events mutate this small buffer; no additional polling.
      while (frame.loadTimes.length > 128 || frame.loadTimes[0] <= loadedAt - DISPLAY_LOAD_WINDOW_MS) {
        frame.loadTimes.shift();
      }
      finish(Math.max(0, STREAM_REFRESH_MS - (Date.now() - requestStartedAt)));
    };
    img.onerror = () => {
      img.gateFrameState.lastResult = "error";
      finish(Math.max(1000, STREAM_REFRESH_MS));
    };
    // Recover even when WebKit never delivers a load/error event.
    requestTimeout = setTimeout(() => {
      img.onload = null;
      img.onerror = null;
      img.removeAttribute("src");
      img.gateFrameState.lastResult = "timeout";
      finish(Math.max(1000, STREAM_REFRESH_MS));
    }, 10000);
    const sep = url.includes("?") ? "&" : "?";
    img.src = `${url}${sep}ts=${Date.now()}&cb=${Math.random().toString(36).slice(2)}`;
  };
  loadNext();
  return img;
}

function createSmoothImageStream(url) {
  // Native image replacement retains the previous frame while loading. Using
  // the same bounded loader on every browser avoids extra fetch/blob/bitmap
  // conversions and gives desktop screens the same timeout recovery as iOS.
  return createLegacyStreamImage(url);
}

async function fetchJsonWithDeadline(url, init = {}) {
  const controller = typeof AbortController === "undefined" ? null : new AbortController();
  let timer = null;
  const request = async () => {
    const response = await fetch(url, controller ? { ...init, signal: controller.signal } : init);
    let data;
    try {
      data = await response.json();
    } catch (err) {
      if (response.ok) throw err;
      data = {};
    }
    return { ok: response.ok, data };
  };
  try {
    // The deadline covers headers AND the body. The losing request can never
    // apply data later, including on iOS without AbortController support.
    return await Promise.race([
      request(),
      new Promise((resolve, reject) => {
        timer = setTimeout(() => {
          if (controller) controller.abort();
          reject(new Error("Request timed out"));
        }, 10000);
      }),
    ]);
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

async function loadAllowlist() {
  const [platesRes, statusRes] = await Promise.all([
    fetchJsonWithDeadline("/api/plates"),
    fetchJsonWithDeadline("/api/allowlist-status"),
  ]);
  if (!platesRes.ok || !statusRes.ok) throw new Error("Could not load plates");
  const plates = platesRes.data;
  const status = statusRes.data;
  if (!Array.isArray(plates) || !Array.isArray(status)) throw new Error("Invalid plate response");
  state.plates = plates;
  state.plateStatus = {};
  status.forEach((entry) => {
    if (!entry.owner) return;
    state.plateStatus[entry.owner] = entry.plates || [];
  });
}

function updateAllowlistFeedback() {
  const dirty = state.platesSavedSnapshot !== JSON.stringify(state.plates);
  const feedback = document.getElementById("allowlist-status");
  if (feedback) {
    feedback.textContent = state.platesSaving ? "Saving changes…"
      : state.platesMessage || (dirty ? "Unsaved changes" : "All changes saved");
    feedback.classList.toggle("is-error", state.platesMessageType === "error");
  }
  document.querySelectorAll("#plates-list input, #plates-list button, #add-plate, #save-plates").forEach((input) => {
    input.disabled = state.platesSaving;
  });
}

function markAllowlistChanged() {
  state.platesMessage = "";
  state.platesMessageType = "";
  updateAllowlistFeedback();
}

function renderAllowlist() {
  const platesList = document.getElementById("plates-list");
  const plateTemplate = document.getElementById("plate-row");
  if (!platesList || !plateTemplate) return;
  platesList.innerHTML = "";
  state.plates.forEach((entry, idx) => {
    const row = plateTemplate.content.cloneNode(true);
    const ownerInput = row.querySelector(".owner-input");
    const platesInput = row.querySelector(".plates-input");
    const metaEl = row.querySelector(".plate-meta");
    const removeBtn = row.querySelector(".remove-plate");
    ownerInput.value = entry.owner || "";
    platesInput.value = (entry.plates || []).join(", ");
    if (metaEl) {
      const platesMeta = state.plateStatus[entry.owner] || [];
      if (!platesMeta.length) {
        metaEl.textContent = "Last seen: -- • Conf: --";
      } else {
        const parts = platesMeta.map((plateInfo) => {
          const seen = plateInfo.last_seen
            ? formatRelative(plateInfo.last_seen)
            : "--";
          const conf = Number.isFinite(plateInfo.confidence)
            ? plateInfo.confidence.toFixed(2)
            : "--";
          return `${plateInfo.plate}: ${seen} (${conf})`;
        });
        metaEl.textContent = parts.join(" | ");
      }
    }
    ownerInput.addEventListener("input", (e) => {
      state.plates[idx].owner = e.target.value;
      markAllowlistChanged();
    });
    platesInput.addEventListener("input", (e) => {
      const raw = e.target.value.split(",").map((p) => p.trim()).filter(Boolean);
      state.plates[idx].plates = raw.map((p) => p.toUpperCase());
      markAllowlistChanged();
    });
    removeBtn.addEventListener("click", () => {
      state.plates.splice(idx, 1);
      renderAllowlist();
      markAllowlistChanged();
    });
    platesList.appendChild(row);
  });
}

async function saveAllowlist() {
  if (state.platesSaving) return;
  state.platesSaving = true;
  state.platesMessage = "";
  state.platesMessageType = "";
  updateAllowlistFeedback();
  const cleaned = state.plates.map((entry) => ({
    owner: entry.owner,
    plates: (entry.plates || []).map((p) => p.toUpperCase()),
  }));
  let saved = false;
  try {
    const resp = await fetchJsonWithDeadline("/api/plates", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cleaned),
    });
    if (!resp.ok) {
      let message = "Could not save changes. Try again.";
      if (resp.data && typeof resp.data.error === "string") message = resp.data.error;
      state.platesMessage = message;
      state.platesMessageType = "error";
      return;
    }
    saved = true;
    state.platesSavedSnapshot = JSON.stringify(state.plates);
    await loadAllowlist();
    state.platesSavedSnapshot = JSON.stringify(state.plates);
    renderAllowlist();
    state.platesMessage = "All changes saved";
  } catch (err) {
    state.platesMessage = saved
      ? "Saved, but the updated list could not be loaded. Reload to check."
      : "Could not confirm the save. Your edits are still here; check the connection and try again.";
    state.platesMessageType = "error";
  } finally {
    state.platesSaving = false;
    updateAllowlistFeedback();
  }
}

async function fetchEvents({ reset = false, page = state.eventsPage, startId = null } = {}) {
  if (state.loadingCandidateEvents && !reset) return;
  if (reset) page = 0;
  if (page < 0) return;
  const requestedStartId = reset ? startId : state.eventsStartId;
  const cursor = page === state.eventsPage + 1 ? state.eventsNextCursor : state.eventsCursors[page];
  if (page > 0 && !cursor) return;
  const requestId = ++state.eventsRequest;
  state.loadingCandidateEvents = true;
  updateHistoryPagination();
  try {
    const params = new URLSearchParams({
      limit: "30",
      window: state.eventsWindow,
      kind: Array.from(state.eventsKinds).join(","),
    });
    if (page > 0) params.set("cursor", cursor);
    else if (requestedStartId) params.set("start_id", requestedStartId);
    const resp = await fetch(`/api/history?${params.toString()}`);
    if (!resp.ok) {
      throw new Error(`events ${resp.status}`);
    }
    const data = await resp.json();
    if (requestId !== state.eventsRequest) return;
    state.events = Array.isArray(data.items) ? data.items : [];
    state.eventsPage = page;
    if (reset) state.eventsCursors = [null];
    state.eventsStartId = requestedStartId;
    state.eventsCursors[page] = page > 0 ? cursor : null;
    state.eventsCursors.length = page + 1;
    state.eventsNextCursor = data.next_cursor || null;
    state.eventsHasMore = Boolean(data.has_more && state.eventsNextCursor);
    state.eventsLoaded = true;
  } catch (err) {
    if (requestId === state.eventsRequest) setStatus("Failed to load history. Try refreshing.");
  } finally {
    if (requestId === state.eventsRequest) {
      state.loadingCandidateEvents = false;
      renderEvents();
    }
  }
}

function updateHistoryPagination() {
  const previous = document.getElementById("history-prev");
  const next = document.getElementById("history-next");
  const label = document.getElementById("events-more");
  if (previous) previous.disabled = state.loadingCandidateEvents || state.eventsPage === 0;
  if (next) next.disabled = state.loadingCandidateEvents || !state.eventsHasMore;
  if (label) label.textContent = state.loadingCandidateEvents
    ? "Loading history…"
    : `Page ${state.eventsPage + 1}${state.eventsHasMore ? "" : " • End of history"}`;
}

function historyPreview(event, small = false) {
  if (small && event.thumbnail_url) return event.thumbnail_url;
  if (event.preview_url) return event.preview_url;
  // Also works during a deployment while a browser still has older event JSON.
  return event.image_url ? event.image_url.replace(/^\/images\//, "/previews/") + `?size=${small ? 160 : 640}` : "";
}

function deferHistoryImage(img, url) {
  setLowPriorityImage(img);
  img.dataset.previewSrc = url;
}

function loadVisibleHistoryImages() {
  if (!isTabActive("candidates")) return;
  // Bounding boxes work on iOS 12, where native image lazy loading is unavailable.
  document.querySelectorAll("#events-list img[data-preview-src]").forEach((img) => {
    const rect = img.getBoundingClientRect();
    if (rect.bottom >= -200 && rect.top <= window.innerHeight + 200) {
      img.src = img.dataset.previewSrc;
      delete img.dataset.previewSrc;
    }
  });
}

function parseCapturedEpoch(ts) {
  const date = parseLocalTimestamp(ts);
  if (!date || Number.isNaN(date.getTime())) return null;
  return date.getTime();
}

function groupEventsByWindow(events) {
  const groups = new Map();
  events.forEach((event) => {
    const epoch = parseCapturedEpoch(event.captured_at);
    const bucket =
      epoch !== null ? Math.floor(epoch / (GROUP_WINDOW_SEC * 1000)) : null;
    const key = bucket !== null ? `t:${bucket}` : `id:${event.id}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        events: [],
        captured_at: event.captured_at,
        epoch,
        images: [],
        eventIds: [],
      };
      groups.set(key, group);
    }
    group.events.push({ ...event, kind: normalizeEventKind(event) });
    if (event.id !== undefined && event.id !== null) {
      group.eventIds.push(String(event.id));
    }
    if (event.image_url) {
      const existing = group.images.find((img) => img.url === event.image_url);
      const conf = Number.isFinite(event.confidence) ? event.confidence : null;
      if (existing) {
        if (conf !== null && (existing.confidence === null || conf > existing.confidence)) {
          existing.confidence = conf;
        }
      } else {
        group.images.push({
          url: event.image_url,
          preview: historyPreview(event),
          thumbnail: historyPreview(event, true),
          name: event.image_name,
          confidence: conf,
        });
      }
    }
    if (epoch !== null && (!group.epoch || epoch > group.epoch)) {
      group.epoch = epoch;
      group.captured_at = event.captured_at;
    }
  });
  return Array.from(groups.values()).sort((a, b) => (b.epoch || 0) - (a.epoch || 0));
}

function pickBestEntry(entries) {
  if (!entries.length) return null;
  const order = { recognised: 0, unmatched: 1 };
  return [...entries].sort((a, b) => {
    const kindDiff = coalesce(order[a.kind], 9) - coalesce(order[b.kind], 9);
    if (kindDiff !== 0) return kindDiff;
    return coalesce(b.confidence, -1) - coalesce(a.confidence, -1);
  })[0];
}

function pickBestEvent(events, best) {
  if (!best) return null;
  let chosen = null;
  events.forEach((event) => {
    if (event.plate !== best.plate || event.kind !== best.kind) return;
    if (!chosen) {
      chosen = event;
      return;
    }
    const a = Number.isFinite(event.confidence) ? event.confidence : -1;
    const b = Number.isFinite(chosen.confidence) ? chosen.confidence : -1;
    if (a > b) {
      chosen = event;
    }
  });
  return chosen;
}

function pickBestImage(images) {
  if (!images.length) return null;
  return [...images].sort((a, b) => {
    const aConf = Number.isFinite(a.confidence) ? a.confidence : -1;
    const bConf = Number.isFinite(b.confidence) ? b.confidence : -1;
    return bConf - aConf;
  })[0];
}

function summarizeGroup(events) {
  const combined = new Map();
  events.forEach((event) => {
    const plate = event.plate || "UNKNOWN";
    const key = `${event.kind}|${plate}`;
    const existing = combined.get(key) || {
      plate,
      kind: event.kind,
      owner: event.owner || "",
      confidence: Number.isFinite(event.confidence) ? event.confidence : null,
      count: 0,
    };
    existing.count += 1;
    if (
      Number.isFinite(event.confidence) &&
      (existing.confidence === null || event.confidence > existing.confidence)
    ) {
      existing.confidence = event.confidence;
    }
    if (!existing.owner && event.owner) {
      existing.owner = event.owner;
    }
    combined.set(key, existing);
  });
  const order = { recognised: 0, unmatched: 1 };
  return Array.from(combined.values()).sort((a, b) => {
    const kindDiff = coalesce(order[a.kind], 9) - coalesce(order[b.kind], 9);
    if (kindDiff !== 0) return kindDiff;
    return coalesce(b.confidence, -1) - coalesce(a.confidence, -1);
  });
}

function renderFrameStrip(images, heroImg, confEl, originalLink, activeIndex = 0) {
  if (!images.length) return null;
  const strip = document.createElement("div");
  strip.className = "frame-strip";
  images.slice(0, 6).forEach((img, idx) => {
    const thumb = document.createElement("img");
    setLowPriorityImage(thumb);
    thumb.alt = "frame";
    thumb.className = idx === activeIndex ? "active" : "";
    deferHistoryImage(thumb, img.thumbnail);
    if (Number.isFinite(img.confidence)) {
      thumb.title = `Conf: ${img.confidence.toFixed(2)}`;
    }
    thumb.addEventListener("click", () => {
      delete heroImg.dataset.previewSrc;
      heroImg.src = img.preview;
      originalLink.href = img.url;
      if (confEl) {
        const nextConf = Number.isFinite(img.confidence) ? img.confidence.toFixed(2) : "--";
        confEl.textContent = `Conf: ${nextConf}`;
      }
      strip.querySelectorAll("img").forEach((node) => node.classList.remove("active"));
      thumb.classList.add("active");
    });
    strip.appendChild(thumb);
  });
  return strip;
}

function renderEvents() {
  const eventsList = document.getElementById("events-list");
  if (!eventsList) return;
  eventsList.innerHTML = "";
  const groups = groupEventsByWindow(state.events);
  const filtered = groups.filter((group) =>
    group.events.some((event) => state.eventsKinds.has(event.kind))
  );
  filtered.forEach((group, index) => {
    const card = document.createElement("div");
    const hasAllowed = group.events.some((event) => event.allowed);
    card.className = `event-card event-group ${hasAllowed ? "allowed" : ""}`;
    if (group.eventIds && group.eventIds.length) {
      card.dataset.eventIds = group.eventIds.join(",");
    }
    if (index === 0 && state.events.length) {
      card.id = "event-latest";
    }

    const counts = { recognised: 0, unmatched: 0 };
    group.events.forEach((event) => {
      if (counts[event.kind] !== undefined) counts[event.kind] += 1;
    });
    const processingTimes = group.events
      .map((event) => Number(event.processing_time_ms))
      .filter((value) => Number.isFinite(value));
    let processingLabel = "Proc: --";
    if (processingTimes.length === 1) {
      processingLabel = `Proc: ${formatProcessingTime(processingTimes[0])}`;
    } else if (processingTimes.length > 1) {
      const avg = processingTimes.reduce((sum, val) => sum + val, 0) / processingTimes.length;
      processingLabel = `Proc avg: ${formatProcessingTime(avg)}`;
    }

    const summary = document.createElement("div");
    summary.className = "event-summary";
    const when = group.captured_at ? formatRelative(group.captured_at) : "--";
    const whenAbs = group.captured_at ? formatDayTimeLabel(group.captured_at) : "--";
    const frames = group.images.length || 0;
    summary.innerHTML = `
      <div class="meta">${escapeHtml(whenAbs)} • ${escapeHtml(when)} • ${group.events.length} reads • ${frames} frame${
        frames === 1 ? "" : "s"
      } • ${escapeHtml(processingLabel)}</div>
      <div class="event-kinds">
        <span class="kind-chip recognised">Recognised ${counts.recognised}</span>
        <span class="kind-chip unmatched">Unmatched ${counts.unmatched}</span>
      </div>
    `;
    card.appendChild(summary);

    const body = document.createElement("div");
    body.className = "event-body";
    const left = document.createElement("div");
    left.className = "event-body-left";
    const right = document.createElement("div");
    right.className = "event-body-right";

    const entries = summarizeGroup(group.events);
    const best = pickBestEntry(entries);
    const header = document.createElement("div");
    header.className = "group-header";
    if (best) {
      const bestEvent = pickBestEvent(group.events, best);
      const bestMeta = Number.isFinite(best.confidence)
        ? `Conf: ${best.confidence.toFixed(2)}`
        : "Conf: --";
      let observedLine = "";
      if (
        bestEvent &&
        bestEvent.observed_plate &&
        bestEvent.observed_plate !== bestEvent.plate
      ) {
        const obsConf = Number.isFinite(bestEvent.observed_confidence)
          ? bestEvent.observed_confidence.toFixed(2)
          : "??";
        const fuzzy = bestEvent.fuzzy ? " • fuzzy" : "";
        const dist =
          Number.isFinite(bestEvent.fuzzy_distance) && bestEvent.fuzzy_distance > 0
            ? ` d=${bestEvent.fuzzy_distance}`
            : "";
        observedLine = `<div class="meta">Observed: ${escapeHtml(bestEvent.observed_plate)} • Conf: ${obsConf}${fuzzy}${dist}</div>`;
      }
      header.innerHTML = `
        <div class="plate">${escapeHtml(best.plate)}</div>
        <div class="meta">${escapeHtml(best.kind)} • ${bestMeta} ${best.owner ? `• ${escapeHtml(best.owner)}` : ""}</div>
        ${observedLine}
        ${describeDecision(bestEvent) ? `<div class="decision-note">${escapeHtml(describeDecision(bestEvent))}</div>` : ""}
      `;
    }
    left.appendChild(header);

    const altWrap = document.createElement("div");
    altWrap.className = "alt-plates open";
    const plateLines = document.createElement("div");
    plateLines.className = "plate-lines";
    entries.forEach((entry, entryIdx) => {
      if (best && entry.plate === best.plate && entry.kind === best.kind) return;
      const line = document.createElement("div");
      line.className = `plate-line ${entry.kind}`;
      const conf = Number.isFinite(entry.confidence)
        ? `Conf: ${entry.confidence.toFixed(2)}`
        : "Conf: --";
      const count = entry.count > 1 ? ` • x${entry.count}` : "";
      line.innerHTML = `
        <div class="plate">${escapeHtml(entry.plate)}</div>
        <div class="meta">${escapeHtml(entry.kind)}${count} • ${conf} ${entry.owner ? `• ${escapeHtml(entry.owner)}` : ""}</div>
      `;
      if (entryIdx >= 3) {
        line.classList.add("alt-extra");
      }
      plateLines.appendChild(line);
    });
    altWrap.appendChild(plateLines);
    const altCount = entries.length - (best ? 1 : 0);
    if (altCount > 0) {
      left.appendChild(altWrap);
      if (altCount > 3) {
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "alt-toggle";
        toggle.textContent = `Show more alternates (${altCount - 3})`;
        toggle.addEventListener("click", () => {
          const open = altWrap.classList.toggle("open");
          toggle.textContent = open
            ? "Hide alternates"
            : `Show more alternates (${altCount - 3})`;
        });
        left.appendChild(toggle);
      }
    }

    const bestImage = pickBestImage(group.images);
    const heroImage = bestImage
      ? bestImage.url
      : (group.images[0] ? group.images[0].url : null);
    if (heroImage) {
      const imageWrap = document.createElement("div");
      imageWrap.className = "event-image-wrap";
      const img = document.createElement("img");
      img.className = "event-image";
      setLowPriorityImage(img);
      img.alt = "capture";
      deferHistoryImage(img, bestImage ? bestImage.preview : group.images[0].preview);
      const originalLink = document.createElement("a");
      originalLink.href = heroImage;
      originalLink.target = "_blank";
      originalLink.rel = "noopener";
      originalLink.title = "Open full-size capture";
      originalLink.appendChild(img);
      imageWrap.appendChild(originalLink);
      const conf = document.createElement("div");
      conf.className = "event-image-conf";
      const startConf =
        bestImage && Number.isFinite(bestImage.confidence)
          ? bestImage.confidence.toFixed(2)
          : "--";
      conf.textContent = `Conf: ${startConf}`;
      imageWrap.appendChild(conf);
      right.appendChild(imageWrap);
      if (group.images.length > 1) {
        const actions = document.createElement("div");
        actions.className = "frame-actions";
        let idx = Math.max(
          0,
          group.images.findIndex((image) => image.url === heroImage)
        );
        const strip = renderFrameStrip(group.images, img, conf, originalLink, idx);
        if (strip) actions.appendChild(strip);
        right.appendChild(actions);
      }
    }
    body.appendChild(left);
    body.appendChild(right);
    card.appendChild(body);
    eventsList.appendChild(card);
  });

  updateHistoryPagination();
  loadVisibleHistoryImages();
  if (state.events.length === 0 || filtered.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const kinds = Array.from(state.eventsKinds).join(", ");
    empty.textContent = `No ${kinds} events in this date range.`;
    eventsList.appendChild(empty);
  }
  renderLatestEvent();
}

function renderLatestImage() {
  const latestImageEl = document.getElementById("latest-image");
  if (!latestImageEl) return;
  latestImageEl.innerHTML = "";
  const event = state.latestRecognised;
  if (!event || !event.image_url) {
    latestImageEl.textContent = "No captures yet.";
    return;
  }
  const image = document.createElement("img");
  setLowPriorityImage(image);
  image.alt = event.plate || "capture";
  image.classList.add("latest-thumb");
  image.src = historyPreview(event);
  image.addEventListener("click", () => {
    if (event && event.id) {
      jumpToEvent(String(event.id));
    }
  });
  latestImageEl.appendChild(image);
  const badge = document.createElement("div");
  badge.className = "badge";
  badge.textContent = event.kind || "capture";
  latestImageEl.appendChild(badge);
}

function formatDayTime(ts) {
  if (!ts) return "--";
  const date = parseLocalTimestamp(ts);
  if (!date || Number.isNaN(date.getTime())) return ts;
  const today = new Date();
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const startOfThatDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffDays = Math.round((startOfToday - startOfThatDay) / 86400000);
  let dayLabel = startOfThatDay.toLocaleDateString();
  if (diffDays === 0) dayLabel = "Today";
  if (diffDays === 1) dayLabel = "Yesterday";
  const timeLabel = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return `${dayLabel} • ${timeLabel}`;
}

function formatDayTimeLabel(ts) {
  if (!ts) return "--";
  const date = parseLocalTimestamp(ts);
  if (!date || Number.isNaN(date.getTime())) return ts;
  const today = new Date();
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const startOfThatDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffDays = Math.round((startOfToday - startOfThatDay) / 86400000);
  let dayLabel = startOfThatDay.toLocaleDateString();
  if (diffDays === 0) dayLabel = "Today";
  if (diffDays === 1) dayLabel = "Yesterday";
  const timeLabel = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return `${dayLabel} ${timeLabel}`;
}

function renderLatestEvent() {
  const latestEventEl = document.getElementById("latest-event");
  const cooldownEl = document.getElementById("cooldown-status");
  const tabletCooldown = document.getElementById("tablet-cooldown");
  if (!latestEventEl) return;
  const latest = state.latestRecognised;
  latestEventEl.innerHTML = "";
  if (!latest) {
    latestEventEl.textContent = "No recent plate events.";
    if (cooldownEl) cooldownEl.textContent = "Last opened: --";
    if (tabletCooldown) tabletCooldown.textContent = "Last opened: --";
    return;
  }
  latestEventEl.innerHTML = `
    <div class="plate">${escapeHtml(latest.plate || "UNKNOWN")}${latest.owner ? ` - ${escapeHtml(latest.owner)}` : ""}</div>
    <div class="meta">${escapeHtml(formatDayTimeLabel(latest.captured_at))} - ${escapeHtml(formatRelative(latest.captured_at))}</div>
  `;
  if (cooldownEl) {
    const lastOpen = formatRelativeEpoch(state.lastGateOpenTs);
    const source = state.lastGateOpenMeta && state.lastGateOpenMeta.kind === "manual_open"
      ? ` from ${state.lastGateOpenMeta.request_ip || "unknown"}`
      : "";
    cooldownEl.textContent = `Last opened: ${lastOpen}${source}`;
  }
  if (tabletCooldown) {
    const lastOpen = formatRelativeEpoch(state.lastGateOpenTs);
    tabletCooldown.textContent = `Last opened: ${lastOpen}`;
  }
}

async function refreshLatest() {
  try {
    const eventsRes = await fetch("/api/events?offset=0&limit=1&kind=recognised");
    const latestEvents = await eventsRes.json();
    if (latestEvents.length) {
      state.latestRecognised = latestEvents[0];
    }
    renderLatestEvent();
    if (isTabActive("candidates")) {
      renderLatestImage();
    }
  } catch (err) {
    setStatus("Refresh failed");
  }
}

async function refreshGateLastOpen() {
  try {
    const resp = await fetch("/api/gate-last-open");
    const data = await resp.json();
    state.lastGateOpenTs = Number.isFinite(data.last_open_ts) ? data.last_open_ts : null;
    state.lastGateOpenMeta = data || null;
  } catch (err) {
    state.lastGateOpenTs = null;
    state.lastGateOpenMeta = null;
  }
  renderLatestEvent();
}

function setActiveTab(target, { updateHash = true } = {}) {
  if (target === "plates") {
    target = "candidates";
  }
  document.body.classList.toggle("home-active", target === "home");
  const tabs = document.querySelectorAll(".tab[data-tab]");
  const panels = document.querySelectorAll(".tab-panel");
  tabs.forEach((btn) => {
    const isActive = btn.dataset.tab === target;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", String(isActive));
  });
  panels.forEach((panel) => {
    const isActive = panel.id === `tab-${target}`;
    panel.classList.toggle("active", isActive);
    panel.setAttribute("aria-hidden", String(!isActive));
  });
  if (target === "home") {
    renderHomeStatus();
    renderHomeArrivals();
    if (state.homeStatus) renderTabletTimeline();
  }
  visibleTabPollers.forEach((poll) => poll());
  if (target === "candidates" && !state.eventsLoaded && !state.loadingCandidateEvents) {
    fetchEvents({ reset: true });
  }
  if (target === "candidates") {
    renderLatestImage();
    loadVisibleHistoryImages();
  }
  if (target === "timeline" && !state.timelineLoaded) {
    initTimeline();
  }
  if (target === "home" && !state.tabletLoaded) {
    initTablet();
  }
  if (target === "stream" && !state.streamLoaded) {
    initStream();
  }
  if (target === "stats" && !state.statsLoaded) {
    initStats();
  }
  if (target === "logs" && !state.logsLoaded) {
    initLogs();
  }
  if (updateHash) {
    const path = target === "stats" ? "/stats" : "/";
    const hash = `#${target}`;
    if (history.replaceState) {
      history.replaceState(null, "", `${path}${hash}`);
    } else {
      window.location.hash = hash;
    }
  }
}

function initTabs() {
  const tabs = document.querySelectorAll(".tab[data-tab]");
  const panels = document.querySelectorAll(".tab-panel");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const target = tab.dataset.tab;
      setActiveTab(target);
    });
  });

  if (!tabs.length || !panels.length) return;
  const hash = window.location.hash.replace("#", "").trim();
  const known = new Set(["home", "candidates", "timeline", "stream", "stats", "logs"]);
  let initial = "home";
  if (hash === "tablet") {
    initial = "home";
  } else if (hash === "plates") {
    initial = "candidates";
  } else if (hash && known.has(hash)) {
    initial = hash;
  } else if (window.location.pathname === "/stats") {
    initial = "stats";
  }
  setActiveTab(initial, { updateHash: false });
}

function initLogs() {
  if (state.logsLoaded) return;
  state.logsLoaded = true;
  const serviceSelect = document.getElementById("logs-service");
  const rangeSelect = document.getElementById("logs-range");
  const refreshBtn = document.getElementById("logs-refresh");
  const filterInput = document.getElementById("logs-filter");
  const warnOnly = document.getElementById("logs-warn-only");
  const hideAccess = document.getElementById("logs-hide-access");
  const hideDeprecations = document.getElementById("logs-hide-deprecations");
  const wrapToggle = document.getElementById("logs-wrap");
  if (!serviceSelect || !rangeSelect) return;
  const trigger = () => fetchLogs({ resetStatus: true });
  serviceSelect.addEventListener("change", trigger);
  rangeSelect.addEventListener("change", trigger);
  if (filterInput) {
    filterInput.addEventListener("input", () => {
      state.logsFilterText = filterInput.value.trim().toLowerCase();
      renderLogs();
    });
  }
  if (warnOnly) {
    warnOnly.addEventListener("change", () => {
      state.logsWarnOnly = warnOnly.checked;
      renderLogs();
    });
  }
  if (hideAccess) {
    state.logsHideAccess = hideAccess.checked;
    hideAccess.addEventListener("change", () => {
      state.logsHideAccess = hideAccess.checked;
      renderLogs();
    });
  }
  if (hideDeprecations) {
    state.logsHideDeprecations = hideDeprecations.checked;
    hideDeprecations.addEventListener("change", () => {
      state.logsHideDeprecations = hideDeprecations.checked;
      renderLogs();
    });
  }
  if (wrapToggle) {
    wrapToggle.addEventListener("change", () => {
      state.logsWrap = wrapToggle.checked;
      renderLogs();
    });
  }
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => fetchLogs({ resetStatus: true }));
  }
  fetchLogs({ resetStatus: true });
}

function parseLogLine(line) {
  const isoMatch = line.match(
    /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:[+-]\d{2}:\d{2})?\s+\S+\s+[^:]+:\s+(.*)$/
  );
  if (isoMatch) {
    const msg = stripInnerTimestamp(isoMatch[3]);
    return { date: isoMatch[1], time: isoMatch[2], msg };
  }
  const match = line.match(
    /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:\s+[+-]\d{2}:\d{2})?\s+(.*)$/
  );
  if (match) {
    return { date: match[1], time: match[2], msg: stripInnerTimestamp(match[3]) };
  }
  return { date: "", time: "", msg: line };
}

function stripInnerTimestamp(message) {
  const inner = message.match(
    /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:,\d+)?\s+(INFO|WARN|WARNING|ERROR|DEBUG)\s+(.*)$/
  );
  if (!inner) return message;
  return inner[4];
}

function renderLogs() {
  const output = document.getElementById("logs-output");
  const status = document.getElementById("logs-status");
  if (!output || !status) return;
  output.classList.toggle("wrap", state.logsWrap);
  output.classList.toggle("no-wrap", !state.logsWrap);

  const filterText = state.logsFilterText;
  const warnOnly = state.logsWarnOnly;
  const hideAccess = state.logsHideAccess;
  const hideDeprecations = state.logsHideDeprecations;
  const warnRe = /(warn|error|failed|crit|fatal|panic)/i;
  const accessRe = /^\d+\.\d+\.\d+\.\d+\s+.*\"(GET|POST|PUT|DELETE|HEAD|OPTIONS)\s+/i;
  const deprecationRe = /DeprecationWarning/i;

  const filtered = state.logsLines.filter((line) => {
    if (hideAccess && accessRe.test(line.raw)) {
      return false;
    }
    if (hideDeprecations && deprecationRe.test(line.raw)) {
      return false;
    }
    if (warnOnly && !warnRe.test(line.raw)) {
      return false;
    }
    if (filterText && !line.raw.toLowerCase().includes(filterText)) {
      return false;
    }
    return true;
  });

  let lastDate = "";
  const html = [];
  filtered.forEach((line) => {
    if (line.date && line.date !== lastDate) {
      html.push(`<div class="log-date">${escapeHtml(line.date)}</div>`);
      lastDate = line.date;
    }
    if (!line.time) {
      const msg = line.msg.trim();
      if (msg) {
        html.push(
          `<div class="log-line log-line--continuation"><span class="log-msg">${escapeHtml(msg)}</span></div>`
        );
      }
      return;
    }
    html.push(
      `<div class="log-line"><span class="log-ts">${escapeHtml(line.time)}</span><span class="log-msg">${escapeHtml(line.msg)}</span></div>`
    );
  });
  output.innerHTML = html.join("");
  const updated = state.logsUpdatedAt ? `Updated ${state.logsUpdatedAt}` : "Updated";
  status.textContent = `${updated} • ${state.logsLines.length} lines • ${filtered.length} shown`;
}

async function fetchLogs({ resetStatus = false } = {}) {
  const serviceSelect = document.getElementById("logs-service");
  const rangeSelect = document.getElementById("logs-range");
  const output = document.getElementById("logs-output");
  const status = document.getElementById("logs-status");
  if (!serviceSelect || !rangeSelect || !output || !status) return;
  if (state.logsLoading) return;
  state.logsLoading = true;
  if (resetStatus) status.textContent = "Loading logs...";
  const service = serviceSelect.value;
  const range = rangeSelect.value;
  try {
    const resp = await fetch(
      `/api/logs?service=${encodeURIComponent(service)}&range=${encodeURIComponent(
        range
      )}&lines=400`
    );
    const data = await resp.json();
    if (data.error) {
      status.textContent = `Error: ${data.error}`;
      output.innerHTML = "";
    } else {
      const lines = Array.isArray(data.lines) ? data.lines : [];
      state.logsLines = lines
        .slice()
        .reverse()
        .map((entry) => {
          const parsed = parseLogLine(entry);
          return { raw: entry, date: parsed.date, time: parsed.time, msg: parsed.msg };
        });
      state.logsUpdatedAt = new Date().toLocaleTimeString();
      renderLogs();
    }
  } catch (err) {
    status.textContent = "Failed to load logs";
    output.innerHTML = "";
  } finally {
    state.logsLoading = false;
    state.logsLastFetch = Date.now();
  }
}

function setKindFilters(kinds) {
  const chips = document.querySelectorAll("#tab-candidates .chip");
  state.eventsKinds = new Set(kinds);
  chips.forEach((chip) => {
    chip.classList.toggle("active", state.eventsKinds.has(chip.dataset.kind));
  });
  renderEvents();
}

function showHistoryForKinds(kinds) {
  setKindFilters(kinds);
  const pending = fetchEvents({ reset: true });
  setActiveTab("candidates");
  return pending;
}

function initFilters() {
  const chips = document.querySelectorAll("#tab-candidates .chip");
  if (!chips.length) return;
  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      const kind = chip.dataset.kind;
      if (!kind) return;
      const isActive = chip.classList.contains("active");
      chip.classList.toggle("active", !isActive);
      if (isActive) {
        state.eventsKinds.delete(kind);
      } else {
        state.eventsKinds.add(kind);
      }
      if (state.eventsKinds.size === 0) {
        state.eventsKinds = new Set(["recognised", "unmatched"]);
        chips.forEach((btn) => btn.classList.add("active"));
      }
      fetchEvents({ reset: true });
    });
  });
}

function initGateButtonFor({ buttonId, statusId, cooldownId }) {
  const { gateBtn, gateStatus, cooldownEl } = getGateEls({
    buttonId,
    statusId,
    cooldownId,
  });
  if (!gateBtn) return;
  if (gateBtn.dataset.gateHandlerBound === "true") return;
  gateBtn.dataset.gateHandlerBound = "true";
  gateBtn.addEventListener("click", async () => {
    gateBtn.disabled = true;
    gateBtn.textContent = "Opening...";
    setGateStatus(gateStatus, "opening", "Gate opening");
    setGateButtonState(gateBtn, "opening");
    state.gateOpenInFlight = true;
    let failed = false;
    try {
      const resp = await fetchOpenGate();
      if (resp.status === 429) {
        const data = await resp.json();
        const retryIn = data.retry_in || 30;
        setStatus(`Opening the gate (${retryIn}s)`);
        startCooldownCountdown(retryIn, { gateBtn, gateStatus, cooldownEl });
      } else if (!resp.ok) {
        failed = true;
        let message = "Open failed";
        let retryIn = 0;
        try {
          const data = await resp.json();
          if (data && data.error === "stale_open_request") {
            message = "Open request expired";
          } else if (data && data.may_have_activated) {
            message = "Gate response uncertain — check gate";
            retryIn = Number(data.retry_in) || 30;
          }
        } catch (err) {
          // Keep the generic failure message if the response is not JSON.
        }
        setStatus(message);
        setGateStatus(gateStatus, "error", message);
        setGateButtonState(gateBtn, "error");
        if (retryIn > 0) {
          startCooldownCountdown(retryIn, { gateBtn, gateStatus, cooldownEl, errorLabel: message });
        }
      } else {
        setStatus("Gate opened");
        initCooldownStatus();
        refreshGateLastOpen();
      }
    } catch (err) {
      failed = true;
      const message = err && err.name === "AbortError" ? "Open timed out" : "Open failed";
      setStatus(message);
      setGateStatus(gateStatus, "error", message);
      setGateButtonState(gateBtn, "error");
    } finally {
      state.gateOpenInFlight = false;
      if (!gateBtn.dataset.cooldown) {
        gateBtn.disabled = false;
        gateBtn.textContent = "Open Sesame!";
        if (!failed) {
          setGateStatus(gateStatus, "ready", "Gate ready");
          setGateButtonState(gateBtn, "ready");
        }
        if (cooldownEl) cooldownEl.textContent = "Opening the gate: --";
      }
    }
  });
}

function startCooldownCountdown(seconds, { gateBtn, gateStatus, cooldownEl, errorLabel = "" }) {
  if (!gateBtn) return;
  gateBtn.dataset.cooldown = "true";
  let remaining = seconds;
  setGateStatus(gateStatus, errorLabel ? "error" : "opening", errorLabel || "Gate opening");
  setGateButtonState(gateBtn, errorLabel ? "error" : "opening");
  const tick = () => {
    const label = errorLabel ? `Retry available in ${remaining}s` : `Opening the gate: ${remaining}s`;
    if (cooldownEl) cooldownEl.textContent = label;
    gateBtn.textContent = label;
    gateBtn.disabled = true;
    remaining -= 1;
    if (remaining < 0) {
      gateBtn.dataset.cooldown = "";
      gateBtn.disabled = false;
      gateBtn.textContent = "Open Sesame!";
      setGateStatus(gateStatus, "ready", "Gate ready");
      setGateButtonState(gateBtn, "ready");
      if (cooldownEl) cooldownEl.textContent = "Opening the gate: --";
      return;
    }
    setTimeout(tick, 1000);
  };
  tick();
}

function initHistoryPagination() {
  const previous = document.getElementById("history-prev");
  const next = document.getElementById("history-next");
  const refresh = document.getElementById("history-refresh");
  const windowSelect = document.getElementById("history-window");
  const movePage = async (page) => {
    await fetchEvents({ page });
    const list = document.getElementById("history-controls");
    if (list) list.scrollIntoView({ block: "start" });
    loadVisibleHistoryImages();
  };
  if (previous) previous.addEventListener("click", () => movePage(state.eventsPage - 1));
  if (next) next.addEventListener("click", () => movePage(state.eventsPage + 1));
  if (refresh) refresh.addEventListener("click", () => fetchEvents({ reset: true }));
  if (windowSelect) windowSelect.addEventListener("change", () => {
    state.eventsWindow = windowSelect.value;
    fetchEvents({ reset: true });
  });
  let scheduled = false;
  const scheduleImages = () => {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => {
      scheduled = false;
      loadVisibleHistoryImages();
    }, 80);
  };
  window.addEventListener("scroll", scheduleImages, { passive: true });
  window.addEventListener("resize", scheduleImages);
}

function initLatestJump() {
  const latestJump = document.getElementById("latest-jump");
  if (!latestJump) return;
  latestJump.addEventListener("click", async () => {
    if (state.latestRecognised && state.latestRecognised.id) {
      jumpToEvent(String(state.latestRecognised.id));
      return;
    }
    setActiveTab("candidates");
    setKindFilters(["recognised"]);
    await fetchEvents({ reset: true });
  });
}

async function jumpToEvent(eventId) {
  setActiveTab("candidates");
  setKindFilters(["recognised", "unmatched"]);
  state.eventsWindow = "all";
  const windowSelect = document.getElementById("history-window");
  if (windowSelect) windowSelect.value = "all";
  await fetchEvents({ reset: true, startId: eventId });
  const cards = document.querySelectorAll(".event-card[data-event-ids]");
  for (const card of cards) {
    const ids = (card.dataset.eventIds || "").split(",");
    if (ids.includes(eventId)) {
      card.scrollIntoView({ block: "center" });
      loadVisibleHistoryImages();
      card.classList.add("highlight");
      setTimeout(() => card.classList.remove("highlight"), 1200);
      break;
    }
  }
}

async function initCooldownStatus() {
  try {
    const resp = await fetch("/api/gate-cooldown");
    const data = await resp.json();
    if (data.remaining > 0) {
      startCooldownCountdown(
        data.remaining,
        getGateEls({
          buttonId: "open-gate",
          statusId: "gate-status",
          cooldownId: "cooldown-status",
        })
      );
      startCooldownCountdown(
        data.remaining,
        getGateEls({
          buttonId: "open-gate-tablet",
          statusId: "tablet-gate-status",
          cooldownId: "tablet-cooldown",
        })
      );
    }
    refreshGateLastOpen();
  } catch (err) {
    setStatus("Gate open window check failed");
  }
}

function initHomeViewport() {
  // iOS 12's 100vh includes space behind Safari's address bar. Use the actual
  // visible height for the fixed wall layouts, leaving other tabs and phones
  // free to scroll. Modern browsers also report changing browser chrome here.
  const viewport = window.visualViewport;
  const update = () => {
    let height = window.innerHeight;
    if (viewport && viewport.scale === 1 && viewport.height > 0) {
      height = Math.min(height, viewport.height);
    }
    if (Number.isFinite(height) && height > 0) {
      document.documentElement.style.setProperty("--home-viewport-height", `${Math.floor(height)}px`);
    }
  };
  update();
  window.addEventListener("resize", update);
  window.addEventListener("orientationchange", update);
  window.addEventListener("pageshow", update);
  if (viewport) viewport.addEventListener("resize", update);
}

async function initMain() {
  initHomeViewport();
  try {
    const resp = await fetch("/api/config");
    const data = await resp.json();
    if (Number.isFinite(data.group_window_sec)) {
      GROUP_WINDOW_SEC = data.group_window_sec;
    }
    if (Number.isFinite(data.stream_fps) && data.stream_fps > 0) {
      STREAM_REFRESH_MS = Math.max(80, 1000 / data.stream_fps);
    }
  } catch (err) {
    // Use default window when config is unavailable.
  }
  initTabs();
  initGateButtonFor({ buttonId: "open-gate", statusId: "gate-status", cooldownId: "cooldown-status" });
  initHistoryPagination();
  initFilters();
  initLatestJump();
  updateStatusTimestamp();
  setKindFilters(["recognised", "unmatched"]);
  state.latestRecognised = null;
  if (isTabActive("candidates") && !state.eventsLoaded && !state.loadingCandidateEvents) {
    await fetchEvents({ reset: true });
  }
  initCooldownStatus();
  refreshGateLastOpen();
  updateStatusTimestamp();
  refreshLatest();
  setInterval(refreshLatest, 15000);
  setInterval(() => {
    renderLatestEvent();
  }, 30000);
  setInterval(refreshGateLastOpen, 30000);
  setInterval(() => {
    if (!document.hidden && isTabActive("home")) {
      renderHomeStatus();
      renderHomeArrivals();
      updateTabletTimelineRelativeTimes();
    }
    updateStatusTimestamp();
  }, 1000);
}

async function initTablet() {
  state.tabletLoaded = true;
  initGateButtonFor({
    buttonId: "open-gate-tablet",
    statusId: "tablet-gate-status",
    cooldownId: "tablet-cooldown",
  });
  initTabletStream();
  pollVisibleTab(fetchHomeStatus, 5000, "home");
  initCooldownStatus();
}

function homeStreamUrl(data) {
  let url = (data.url || "").trim();
  if (LEGACY_IOS) url = "/static/stream.jpg";
  if (url !== "/static/stream.jpg") return url;
  const profiles = data.profiles || {};
  const kiosk = document.body.classList.contains("fullscreen-page");
  const tablet = /iP(ad|hone|od)/.test(navigator.userAgent || "") || window.innerWidth <= 1100;
  const profile = kiosk ? "kiosk" : tablet ? "tablet" : null;
  // Only the known local JPEG endpoints can replace the configured feed.
  const candidate = profile && profiles[profile];
  return candidate === `/static/stream-${profile}.jpg` ? candidate : url;
}

async function initTabletStream() {
  const frame = document.getElementById("tablet-stream-frame");
  if (!frame) return;
  try {
    const resp = await fetch("/api/stream");
    if (!resp.ok) throw new Error("Stream configuration unavailable");
    const data = await resp.json();
    const url = homeStreamUrl(data);
    if (!url) {
      state.homeStreamError = "No stream configured";
      renderHomeStatus();
      return;
    }
    const lower = url.toLowerCase();
    if (lower.startsWith("rtsp://")) {
      state.homeStreamError = "Stream format unavailable";
      renderHomeStatus();
      return;
    }
    const parsed = new URL(url, window.location.href);
    state.homeSourceIsLocal = parsed.origin === window.location.origin &&
      /^\/static\/stream(?:-tablet|-kiosk)?\.jpg$/.test(parsed.pathname);
    const stack = LEGACY_IOS ? createLegacyStreamImage(url) : createSmoothImageStream(url);
    frame.innerHTML = "";
    frame.appendChild(stack);
    state.homeFrame = stack.gateFrameState;
    state.homeStreamError = "";
    renderHomeStatus();
  } catch (err) {
    state.homeStreamError = "Stream unavailable";
    renderHomeStatus();
  }
}

function homeServerNow() {
  if (!state.homeStatus || state.homeStatusReceivedAt === null) return null;
  return state.homeStatus.server_time + Math.max(0, Date.now() - state.homeStatusReceivedAt) / 1000;
}

function homeSeenAge(seenAt) {
  const now = homeServerNow();
  return now !== null && Number.isFinite(seenAt) ? Math.max(0, (now - seenAt) * 1000) : null;
}

function homeStatusIsCurrent() {
  return state.homeStatus !== null && !state.homeStatusError && state.homeStatusReceivedAt !== null &&
    Date.now() - state.homeStatusReceivedAt <= HOME_STATUS_MAX_AGE_MS;
}

function renderHomeStatus() {
  const badge = document.getElementById("tablet-stream-status");
  if (!badge) return;
  const frame = state.homeFrame;
  const measuredAt = Date.now();
  const frameAge = frame && frame.loadedAt !== null ? Math.max(0, measuredAt - frame.loadedAt) : null;
  const hasLoadSamples = frame && Array.isArray(frame.loadTimes);
  const duration = hasLoadSamples ? Math.min(DISPLAY_LOAD_WINDOW_MS, Math.max(0, measuredAt - frame.startedAt)) : 0;
  let displayRate = null;
  if (hasLoadSamples && duration >= 1000) {
    const recentLoads = frame.loadTimes.filter((time) => time > measuredAt - DISPLAY_LOAD_WINDOW_MS && time <= measuredAt).length;
    displayRate = (recentLoads * 1000 / duration).toFixed(1);
  }
  const snapshot = state.homeStatus;
  const current = homeStatusIsCurrent();
  const now = homeServerNow();
  const stream = snapshot && snapshot.stream || {};
  // This is the JPEG age observed by the last status poll, not the age of
  // the currently displayed image. The producer can replace it between polls.
  const sourceAge = Number.isFinite(stream.age_seconds) && stream.age_seconds >= 0
    ? stream.age_seconds : null;
  const threshold = Number.isFinite(stream.stale_after_seconds) ? stream.stale_after_seconds : 15;
  let label = displayRate === null ? "View updating" : `View ${displayRate} FPS`;
  let kind = "ok";
  if (state.homeStreamError) {
    label = state.homeStreamError;
    kind = "bad";
  } else if (frame && frame.lastResult === "error") {
    label = "View interrupted";
    kind = "bad";
  } else if (frame && (frame.lastResult === "timeout" || frameAge !== null && frameAge > DISPLAY_STALE_MS)) {
    label = "Frame stalled";
    kind = "bad";
  } else if (frameAge === null) {
    label = "Loading view…";
    kind = "unknown";
  } else if (!current) {
    label = "Status unavailable";
    kind = "unknown";
  } else if (state.homeSourceIsLocal && (!stream.fresh || sourceAge === null || sourceAge > threshold)) {
    label = "Source stale";
    kind = "bad";
  }
  if (badge.textContent !== label) badge.textContent = label;
  badge.className = `live-badge status-${kind}`;
  badge.title = "FPS counts completed JPEG loads over up to five seconds, including repeated camera frames. Tap for details.";
  const serviceLabels = { alprd: "Recognition service", gate_anpr: "Gate worker", stream_jpeg: "Frame service", beanstalkd: "Queue" };
  const servicesCurrent = current && Number.isFinite(snapshot.services_checked_at) && now - snapshot.services_checked_at <= 20;
  const services = snapshot && snapshot.services || {};
  const issues = servicesCurrent ? Object.keys(serviceLabels).filter((key) => services[key] !== "active") : [];
  const sourcePill = document.getElementById("tablet-source-status");
  if (sourcePill) {
    sourcePill.hidden = !state.homeSourceIsLocal;
    const sourceKnown = current && sourceAge !== null;
    const sourceFresh = sourceKnown && stream.fresh && sourceAge <= threshold;
    sourcePill.textContent = sourceKnown ? sourceFresh ? "Pi frame fresh" : "Pi frame stale" : "Source unknown";
    sourcePill.className = `live-badge status-${sourceKnown ? sourceFresh ? "neutral" : "bad" : "unknown"}`;
  }
  const servicePill = document.getElementById("tablet-service-status");
  if (servicePill) {
    servicePill.textContent = !servicesCurrent ? "Services unknown" : issues.length ? "Service issue" : "Services active";
    servicePill.className = `live-badge status-${!servicesCurrent ? "unknown" : issues.length ? "bad" : "neutral"}`;
  }
  const system = document.getElementById("tablet-system-status");
  if (system) {
    const parts = [frameAge === null ? "No image loaded" : `Image loaded ${Math.floor(frameAge / 1000)}s ago`];
    if (state.homeSourceIsLocal) {
      const checkedAgo = state.homeStatusReceivedAt === null ? null
        : Math.floor(Math.max(0, Date.now() - state.homeStatusReceivedAt) / 1000);
      parts.push(current && sourceAge !== null
        ? `Pi JPEG was ${sourceAge.toFixed(1)}s old when checked ${checkedAgo}s ago`
        : "Pi frame status unavailable");
    }
    if (!servicesCurrent) {
      parts.push("Service status unavailable");
    } else {
      parts.push(issues.length ? issues.map((key) => {
        return `${serviceLabels[key]} ${services[key] === "inactive" || services[key] === "failed" ? "inactive" : "unknown"}`;
      }).join("; ") : "Services active");
    }
    let viewportHeight = window.innerHeight;
    const viewport = window.visualViewport;
    if (viewport && viewport.scale === 1 && viewport.height > 0) {
      viewportHeight = Math.min(viewportHeight, viewport.height);
    }
    if (window.innerWidth > 0 && viewportHeight > 0) {
      parts.push(`Viewport ${Math.floor(window.innerWidth)}×${Math.floor(viewportHeight)} CSS px`);
    }
    if (frame && frame.width > 0 && frame.height > 0) {
      parts.push(`JPEG ${frame.width}×${frame.height}`);
    }
    if (hasLoadSamples) {
      if (displayRate !== null) {
        parts.push(`Image loads ${displayRate}/s (last ${Math.round(duration / 1000)}s; not distinct frames)`);
      } else {
        parts.push("Measuring image loads…");
      }
    }
    parts.push("Service activity does not confirm recognition is progressing.");
    const detail = parts.join(" • ");
    if (system.textContent !== detail) system.textContent = detail;
  }
}

async function fetchHomeStatus() {
  let timeout = null;
  const controller = typeof AbortController === "undefined" ? null : new AbortController();
  try {
    if (controller) timeout = setTimeout(() => controller.abort(), 10000);
    const resp = await fetch("/api/home-status", controller ? { signal: controller.signal } : {});
    if (!resp.ok) throw new Error("Home status unavailable");
    const data = await resp.json();
    if (!data || !Number.isFinite(data.server_time)) throw new Error("Invalid home status");
    const firstSnapshot = state.homeStatus === null;
    state.homeStatus = data;
    state.homeStatusReceivedAt = Date.now();
    state.homeStatusError = false;
    const recognised = Array.isArray(data.recognised) ? data.recognised.slice(0, 2) : [];
    if (firstSnapshot || JSON.stringify(state.tabletEvents) !== JSON.stringify(recognised)) {
      state.tabletEvents = recognised;
      if (!document.hidden && isTabActive("home")) renderTabletTimeline();
    }
    updateTabletTimelineRelativeTimes();
  } catch (err) {
    state.homeStatusError = true;
  } finally {
    if (timeout !== null) clearTimeout(timeout);
    renderHomeStatus();
    renderHomeArrivals();
  }
}

function renderHomeArrivals() {
  if (document.hidden || !isTabActive("home")) return;
  const snapshot = state.homeStatus;
  const now = homeServerNow();
  const unknown = document.getElementById("home-unfamiliar");
  if (unknown) {
    const entries = snapshot && Array.isArray(snapshot.unfamiliar) ? snapshot.unfamiliar : [];
    const event = entries.find((entry) => Number.isFinite(entry.seen_at) && Number.isFinite(entry.expires_at) &&
      now !== null && now < Math.min(entry.expires_at, entry.seen_at + 300));
    unknown.hidden = !event;
    if (!event) {
      unknown.innerHTML = "";
      state.unfamiliarRenderedId = null;
    } else {
      const key = JSON.stringify([event.id, event.seen_at, event.plate, event.thumbnail_url, event.preview_url]);
      if (state.unfamiliarRenderedId !== key) {
        const preview = historyPreview(event, true);
        const plate = event.plate && event.plate !== "UNKNOWN" ? event.plate : "Plate unreadable";
        unknown.innerHTML = `
          ${preview ? `<img src="${escapeHtml(preview)}" alt="Recent unfamiliar vehicle capture" />` : ""}
          <div><div class="unfamiliar-title">Unfamiliar vehicle seen</div>
            <div class="plate">${escapeHtml(plate)}</div><div id="home-unfamiliar-age" class="meta"></div>
          </div>`;
        state.unfamiliarRenderedId = key;
      }
      const age = document.getElementById("home-unfamiliar-age");
      if (age) age.textContent = `Seen ${formatRelativeDelta(homeSeenAge(event.seen_at))}`;
    }
  }
  const card = document.getElementById("home-decision");
  if (card) {
    const decisions = snapshot && Array.isArray(snapshot.recent_decisions) ? snapshot.recent_decisions : [];
    const event = decisions.find((item) => describeDecision(item));
    card.hidden = !event;
    if (snapshot && snapshot.decisions_available === false) {
      card.hidden = false;
      card.innerHTML = '<summary class="decision-unavailable">Decision history unavailable</summary>';
      state.decisionRenderedKey = "";
    } else if (!event) {
      card.innerHTML = "";
      state.decisionRenderedKey = "";
    } else {
      const key = JSON.stringify([event.id, event.plate, event.detail]);
      if (key !== state.decisionRenderedKey) {
        const command = event.detail.decision.relay_command;
        const outcomes = {
          pulse_sent: "Pulse sent", coalesced: "Already pulsed", not_requested: "No pulse requested",
          failed_before_activation: "Pulse failed", uncertain: "Pulse uncertain",
        };
        const outcome = outcomes[command] || "Recorded decision";
        const tone = command === "uncertain" || command === "failed_before_activation" ? "bad" : "neutral";
        card.innerHTML = `<summary><span class="decision-summary"><span class="decision-title">Latest check</span>
          <span class="plate">${escapeHtml(event.plate || event.observed_plate || "Plate unreadable")}</span>
          <span class="live-badge status-${tone}">${escapeHtml(outcome)}</span>
          <span id="home-decision-age" class="meta"></span></span></summary>
          <div class="decision-note">${escapeHtml(describeDecision(event))}</div>`;
        state.decisionRenderedKey = key;
      }
      const age = document.getElementById("home-decision-age");
      const elapsed = homeSeenAge(event.seen_at);
      if (age) age.textContent = elapsed === null ? "" : formatRelativeDelta(elapsed);
    }
  }
  const status = document.getElementById("home-arrival-status");
  if (status) {
    status.hidden = homeStatusIsCurrent();
    status.textContent = snapshot ? "Arrival updates unavailable; showing last received details." :
      state.homeStatusError ? "Arrival updates unavailable." : "Loading recent arrivals…";
  }
}

function renderTabletTimeline() {
  const list = document.getElementById("tablet-timeline-list");
  if (!list) return;
  list.innerHTML = "";
  state.tabletEvents.forEach((event) => {
    const row = document.createElement("div");
    row.className = "tablet-timeline-row";
    const thumb = event.image_url
      ? `<img src="${escapeHtml(historyPreview(event, true))}" alt="capture" loading="lazy" decoding="async" fetchpriority="low" />`
      : `<div class="tablet-thumb-placeholder"></div>`;
    const seenAge = homeSeenAge(event.seen_at);
    const ageMinutes = seenAge === null ? getAgeMinutes(event.captured_at) : Math.floor(seenAge / 60000);
    const dotClass = ageMinutes !== null && ageMinutes < 60 ? "dot-fresh" : "dot-stale";
    const rel = seenAge === null ? formatRelative(event.captured_at) : formatRelativeDelta(seenAge);
    const timestamp = escapeHtml(event.captured_at || "");
    const seenAttr = Number.isFinite(event.seen_at) ? `data-tablet-seen-at="${event.seen_at}"` : "";
    row.innerHTML = `
      <div class="tablet-thumb">${thumb}</div>
      <div class="tablet-info">
        <div class="plate">${escapeHtml(event.plate || "UNKNOWN")}${event.owner ? ` - ${escapeHtml(event.owner)}` : ""}</div>
        <div class="meta">${escapeHtml(formatDayTimeLabel(event.captured_at))}<span class="tablet-mobile-age"> · <span data-tablet-relative="${timestamp}" ${seenAttr}>${escapeHtml(rel)}</span></span></div>
      </div>
      <div class="tablet-time">
        <span class="dot ${dotClass}" data-tablet-relative-dot="${timestamp}" ${seenAttr}></span>
        <span data-tablet-relative="${timestamp}" ${seenAttr}>${escapeHtml(rel)}</span>
      </div>
    `;
    if (!document.body.classList.contains("fullscreen-page")) {
      if (event.id !== undefined && event.id !== null) {
        row.classList.add("clickable");
        row.addEventListener("click", () => {
          jumpToEvent(String(event.id));
        });
      }
    }
    list.appendChild(row);
  });
  if (!state.tabletEvents.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No recognised plates yet.";
    list.appendChild(empty);
  }
}

function updateTabletTimelineRelativeTimes() {
  const list = document.getElementById("tablet-timeline-list");
  if (!list) return;
  list.querySelectorAll("[data-tablet-relative]").forEach((node) => {
    const elapsed = homeSeenAge(Number(node.dataset.tabletSeenAt));
    node.textContent = elapsed === null ? formatRelative(node.dataset.tabletRelative) : formatRelativeDelta(elapsed);
  });
  list.querySelectorAll("[data-tablet-relative-dot]").forEach((dot) => {
    const elapsed = homeSeenAge(Number(dot.dataset.tabletSeenAt));
    const ageMinutes = elapsed === null ? getAgeMinutes(dot.dataset.tabletRelativeDot) : Math.floor(elapsed / 60000);
    const isFresh = ageMinutes !== null && ageMinutes < 60;
    dot.classList.toggle("dot-fresh", isFresh);
    dot.classList.toggle("dot-stale", !isFresh);
  });
}

async function initStream() {
  const frame = document.getElementById("stream-frame");
  const status = document.getElementById("stream-status");
  const fpsEl = document.getElementById("stream-fps");
  const lagEl = document.getElementById("stream-lag");
  if (!frame) return;
  state.streamLoaded = true;
  if (status) status.textContent = "Loading stream...";
  try {
    const resp = await fetch("/api/stream");
    const data = await resp.json();
    let url = (data.url || "").trim();
    if (!url) {
      if (status) status.textContent = "No stream configured.";
      return;
    }
    const lower = url.toLowerCase();
    let el = null;
    if (lower.startsWith("rtsp://")) {
      if (status) status.textContent = "RTSP is not supported in browsers.";
      return;
    }
    if (LEGACY_IOS) {
      url = "/static/stream.jpg";
      el = createLegacyStreamImage(url);
      frame.innerHTML = "";
      if (fpsEl) {
        frame.appendChild(fpsEl);
      }
      if (lagEl) frame.appendChild(lagEl);
      frame.appendChild(el);
      if (status) status.textContent = "Live";
      startStreamFps(el);
      initStreamLag();
      initStreamHealth();
      initSystemHealth();
      return;
    }
    if (lower.endsWith(".m3u8") || lower.endsWith(".mp4") || lower.endsWith(".webm")) {
      el = document.createElement("video");
      el.src = url;
      el.controls = true;
      el.autoplay = true;
      el.muted = true;
      el.playsInline = true;
    } else if (lower.endsWith(".mjpg") || lower.endsWith(".mjpeg")) {
      el = document.createElement("img");
      el.src = url;
      el.alt = "Live stream";
    } else if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) {
      el = createSmoothImageStream(url);
    } else {
      el = document.createElement("iframe");
      el.src = url;
      el.title = "Live stream";
      el.loading = "lazy";
      el.referrerPolicy = "no-referrer";
      el.allow =
        "autoplay; clipboard-read; clipboard-write; encrypted-media; fullscreen; picture-in-picture";
    }
    frame.innerHTML = "";
    if (fpsEl) {
      frame.appendChild(fpsEl);
    }
    if (lagEl) frame.appendChild(lagEl);
    frame.appendChild(el);
    if (status) status.textContent = "Live";
    setupStreamFullscreen(frame, status, url);
    startStreamFps(el);
    initStreamLag();
    initStreamHealth();
    initSystemHealth();
  } catch (err) {
    if (status) status.textContent = "Stream failed to load.";
  }
}

function setupStreamFullscreen(frame, status, url) {
  const fullscreenBtn = document.getElementById("stream-fullscreen");
  if (!fullscreenBtn || !frame) return;
  const isIos = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  const canFullscreen = Boolean(document.fullscreenEnabled && frame.requestFullscreen);
  if (!canFullscreen || isIos) {
    fullscreenBtn.textContent = "Open stream";
    fullscreenBtn.addEventListener("click", () => {
      if (!url) {
        if (status) status.textContent = "Stream URL missing.";
        return;
      }
      window.open(url, "_blank", "noopener");
    });
    return;
  }
  const updateLabel = () => {
    fullscreenBtn.textContent = document.fullscreenElement ? "Exit full screen" : "Full screen";
  };
  fullscreenBtn.addEventListener("click", async () => {
    try {
      if (!document.fullscreenElement) {
        if (frame.requestFullscreen) {
          await frame.requestFullscreen();
        } else {
          if (status) status.textContent = "Full screen not supported.";
        }
      } else if (document.exitFullscreen) {
        await document.exitFullscreen();
      }
    } catch (err) {
      if (status) status.textContent = "Full screen failed.";
    } finally {
      updateLabel();
    }
  });
  document.addEventListener("fullscreenchange", updateLabel);
  updateLabel();
}

function startStreamFps(el) {
  const fpsEl = document.getElementById("stream-fps");
  if (!fpsEl) return;
  let frames = 0;
  let last = performance.now();
  const timer = setInterval(() => {
    const now = performance.now();
    const seconds = Math.max(0.001, (now - last) / 1000);
    const fps = frames / seconds;
    fpsEl.textContent = `FPS: ${fps.toFixed(1)}`;
    frames = 0;
    last = now;
  }, 1000);

  if (el instanceof HTMLImageElement) {
    el.addEventListener("load", () => {
      frames += 1;
    });
    return;
  }

  if (el instanceof HTMLElement) {
    const imgs = el.querySelectorAll("img.stream-image, img.stream-image-single");
    if (imgs.length) {
      imgs.forEach((img) => {
        img.addEventListener("load", () => {
          frames += 1;
        });
      });
      return;
    }
  }

  if (el instanceof HTMLVideoElement) {
    if (typeof el.requestVideoFrameCallback === "function") {
      const onFrame = () => {
        frames += 1;
        el.requestVideoFrameCallback(onFrame);
      };
      el.requestVideoFrameCallback(onFrame);
    } else {
      el.addEventListener("timeupdate", () => {
        frames += 1;
      });
    }
    return;
  }

  clearInterval(timer);
  fpsEl.textContent = "FPS: --";
}

function pollVisibleTab(update, intervalMs, tab = "stream") {
  let pending = false;
  let timer = null;
  const visible = () => !document.hidden && isTabActive(tab);
  const poll = async () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (!visible() || pending) return;
    pending = true;
    try {
      await update();
    } finally {
      pending = false;
      if (visible()) timer = setTimeout(poll, intervalMs);
    }
  };
  visibleTabPollers.push(poll);
  document.addEventListener("visibilitychange", poll);
  poll();
}

async function initStreamLag() {
  const lagEl = document.getElementById("stream-lag");
  if (!lagEl) return;
  const update = async () => {
    try {
      const resp = await fetch("/api/stream-lag");
      const data = await resp.json();
      const lagMs = data.lag_ms;
      if (Number.isFinite(lagMs)) {
        lagEl.textContent = `Lag: ${(lagMs / 1000).toFixed(1)}s`;
      } else {
        lagEl.textContent = "Lag: --";
      }
    } catch (err) {
      lagEl.textContent = "Lag: --";
    }
  };
  pollVisibleTab(update, 3000);
}

async function initStreamHealth() {
  const healthEl = document.getElementById("stream-health");
  const bannerEl = document.getElementById("stream-banner");
  if (!healthEl) return;
  const setState = (label, state) => {
    healthEl.textContent = `Health: ${label}`;
    healthEl.classList.remove("ok", "warn", "bad");
    if (state) {
      healthEl.classList.add(state);
    }
  };
  const update = async () => {
    try {
      const resp = await fetch("/api/stream-health");
      const data = await resp.json();
      const issues = [];
      if (data.stream_stale) issues.push("stale");
      if (!issues.length) {
        setState("OK", "ok");
        if (bannerEl) {
          bannerEl.textContent = "";
          bannerEl.hidden = true;
        }
        return;
      }
      const label = issues.join(", ");
      const severity = data.stream_stale ? "bad" : "warn";
      setState(label, severity);
      if (bannerEl) {
        const age = Number.isFinite(data.stream_age_s)
          ? `${data.stream_age_s.toFixed(1)}s`
          : "--";
        bannerEl.textContent = `Stream stale: last frame ${age} ago`;
        bannerEl.hidden = false;
      }
    } catch (err) {
      setState("--", "");
      if (bannerEl) {
        bannerEl.textContent = "";
        bannerEl.hidden = true;
      }
    }
  };
  pollVisibleTab(update, 3000);
}

async function initSystemHealth() {
  const systemEl = document.getElementById("stream-system");
  if (!systemEl) return;
  const update = async () => {
    try {
      const resp = await fetch("/api/service-health");
      const data = await resp.json();
      const parts = [];
      const services = data.services || {};
      const svcLabel = (name, key) => `${name}:${services[key] || "?"}`;
      parts.push(svcLabel("alprd", "alprd"));
      parts.push(svcLabel("worker", "gate_anpr"));
      parts.push(svcLabel("stream", "stream_jpeg"));
      if (Number.isFinite(data.last_event_age_s)) {
        parts.push(`last plate ${data.last_event_age_s.toFixed(0)}s ago`);
      } else {
        parts.push("last plate --");
      }
      if (data.disk && Number.isFinite(data.disk.free_pct)) {
        parts.push(`disk ${data.disk.free_pct.toFixed(1)}% free`);
      }
      if (Number.isFinite(data.temperature_c)) {
        parts.push(`${data.temperature_c.toFixed(1)}C`);
      }
      const failures = Array.isArray(data.failed_units) ? data.failed_units.length : 0;
      if (failures) {
        parts.push(`${failures} failed units`);
      }
      systemEl.textContent = `System: ${parts.join(" • ")}`;
    } catch (err) {
      systemEl.textContent = "System: --";
    }
  };
  pollVisibleTab(update, 5000);
}

function describeTimelineEvent(event) {
  const kind = normalizeKind(event.kind);
  if (kind === "manual_open") {
    const detail = event.detail && typeof event.detail === "object" ? event.detail : {};
    const label = detail.label || "Manual open";
    const ip = event.request_ip ? ` • ${event.request_ip}` : "";
    return {
      title: label,
      meta: `${formatDayTimeLabel(event.captured_at)} • ${formatRelative(event.captured_at)}${ip}`,
      owner: event.source || "web_ui",
      confidence: "Gate opened",
    };
  }
  const observed =
    event.observed_plate && event.observed_plate !== event.plate
      ? `Observed ${event.observed_plate}`
      : null;
  const metaParts = [
    formatDayTimeLabel(event.captured_at),
    formatRelative(event.captured_at),
    kind,
  ];
  if (observed) metaParts.push(observed);
  return {
    title: event.plate || "UNKNOWN",
    meta: metaParts.join(" • "),
    owner: event.owner || "",
    confidence: Number.isFinite(event.confidence)
      ? `Conf: ${event.confidence.toFixed(2)}`
      : "Conf: --",
  };
}

function updateTimelinePagination(meta) {
  const info = document.getElementById("timeline-page-info");
  const prev = document.getElementById("timeline-prev");
  const next = document.getElementById("timeline-next");
  if (info) {
    const total = Number(meta.total || 0);
    info.textContent = total
      ? `Page ${meta.page} of ${meta.total_pages} • ${total} events`
      : "No events";
  }
  if (prev) prev.disabled = !meta.has_prev;
  if (next) next.disabled = !meta.has_next;
}

async function fetchTimeline({ page = 1 } = {}) {
  const list = document.getElementById("timeline-list");
  const more = document.getElementById("timeline-more");
  if (!list) return;
  const requestId = ++state.timelineRequest;
  state.loadingTimelineEvents = true;
  try {
    const resp = await fetch(
      `/api/timeline?page=${page}&per_page=${state.timelinePerPage}&window=${state.timelineWindow}`
    );
    if (!resp.ok) {
      throw new Error(`timeline ${resp.status}`);
    }
    const data = await resp.json();
    if (requestId !== state.timelineRequest) return;
    state.timelinePage = page;
    state.timelineEvents = Array.isArray(data.items) ? data.items : [];
    state.timelineTotal = Number(data.total || 0);
    state.timelineTotalPages = Number(data.total_pages || 1);
    renderTimeline();
    updateTimelinePagination(data);
    if (more) {
      more.textContent = state.timelineTotal
        ? "Recognised and manual opens."
        : "No timeline events in this window";
    }
  } catch (err) {
    if (requestId === state.timelineRequest && more) {
      more.textContent = "Failed to load timeline";
    }
  } finally {
    if (requestId === state.timelineRequest) state.loadingTimelineEvents = false;
  }
}

function renderTimeline() {
  const list = document.getElementById("timeline-list");
  if (!list) return;
  list.innerHTML = "";
  state.timelineEvents.forEach((event) => {
    const rowData = describeTimelineEvent(event);
    const row = document.createElement("div");
    row.className = `timeline-row timeline-row--${normalizeKind(event.kind)}`;
    row.innerHTML = `
      <div>
        <div class="plate">${escapeHtml(rowData.title)}</div>
        <div class="meta">${escapeHtml(rowData.meta)}</div>
      </div>
      <div class="owner">${escapeHtml(rowData.owner)}</div>
      <div class="confidence">${escapeHtml(rowData.confidence)}</div>
    `;
    list.appendChild(row);
  });
  if (!state.timelineEvents.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No timeline events yet.";
    list.appendChild(empty);
  }
}

function initTimeline() {
  state.timelineLoaded = true;
  const chips = document.querySelectorAll("#tab-timeline .chip");
  if (chips.length) {
    chips.forEach((chip) => {
      chip.addEventListener("click", () => {
        chips.forEach((btn) => btn.classList.remove("active"));
        chip.classList.add("active");
        state.timelineWindow = chip.dataset.window || "7d";
        fetchTimeline({ page: 1 });
      });
    });
  }
  const prev = document.getElementById("timeline-prev");
  const next = document.getElementById("timeline-next");
  if (prev) {
    prev.addEventListener("click", () => {
      if (state.timelinePage > 1) {
        fetchTimeline({ page: state.timelinePage - 1 });
      }
    });
  }
  if (next) {
    next.addEventListener("click", () => {
      if (state.timelinePage < state.timelineTotalPages) {
        fetchTimeline({ page: state.timelinePage + 1 });
      }
    });
  }
  fetchTimeline({ page: 1 });
}

async function initAdmin() {
  const addPlateBtn = document.getElementById("add-plate");
  const savePlatesBtn = document.getElementById("save-plates");
  const platesList = document.getElementById("plates-list");
  setStatus("Loading...");
  try {
    await loadAllowlist();
  } catch (err) {
    setStatus("Could not load plates");
    const feedback = document.getElementById("allowlist-status");
    if (feedback) feedback.textContent = "Could not load the allowlist. Reload this page to try again.";
    if (savePlatesBtn) savePlatesBtn.disabled = true;
    if (addPlateBtn) addPlateBtn.disabled = true;
    return;
  }
  if (Array.isArray(state.plates) && state.plates.length && Array.isArray(state.plates[0])) {
    const grouped = {};
    state.plates.forEach(([plate, owner]) => {
      grouped[owner] = grouped[owner] || { owner, plates: [] };
      grouped[owner].plates.push(plate);
    });
    state.plates = Object.values(grouped);
  }
  renderAllowlist();
  state.platesSavedSnapshot = JSON.stringify(state.plates);
  updateAllowlistFeedback();
  setStatus("Ready");
  addPlateBtn.addEventListener("click", () => {
    state.plates.push({ owner: "", plates: [] });
    renderAllowlist();
    markAllowlistChanged();
    if (!platesList) return;
    const rows = platesList.querySelectorAll(".plate-row");
    const last = rows[rows.length - 1];
    if (!last) return;
    const ownerInput = last.querySelector(".owner-input");
    if (ownerInput) ownerInput.focus();
    last.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  savePlatesBtn.addEventListener("click", saveAllowlist);
}

function formatRelative(ts) {
  if (!ts) return "--";
  const date = parseLocalTimestamp(ts);
  if (!date || Number.isNaN(date.getTime())) return ts;
  const delta = Math.max(0, Date.now() - date.getTime());
  return formatRelativeDelta(delta);
}

function formatRelativeEpoch(epochSeconds) {
  if (!Number.isFinite(epochSeconds)) return "--";
  const delta = Math.max(0, Date.now() - epochSeconds * 1000);
  return formatRelativeDelta(delta);
}

function formatRelativeDelta(deltaMs) {
  const minutes = Math.floor(deltaMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatProcessingTime(ms) {
  if (!Number.isFinite(ms)) return "--";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.round(ms)}ms`;
}

function statsCivilTime(value) {
  // Treat Pi-local labels as civil time, independent of the screen's timezone.
  const parts = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
  return parts ? Date.UTC(+parts[1], +parts[2] - 1, +parts[3], +(parts[4] || 0), +(parts[5] || 0)) : NaN;
}

function statsChartBuckets(timeseries) {
  const series = (timeseries && timeseries.series || []).filter((point) =>
    Number.isFinite(statsCivilTime(point.t)) && Number.isFinite(point.v) && point.v >= 0);
  const unit = timeseries && timeseries.bucket === "hour" ? 3600000 : 86400000;
  let start = statsCivilTime(timeseries && timeseries.start);
  let end = statsCivilTime(timeseries && timeseries.end);
  if (!Number.isFinite(start)) start = series.length ? Math.min(...series.map((p) => statsCivilTime(p.t))) : end;
  if (!Number.isFinite(end)) end = series.length ? Math.max(...series.map((p) => statsCivilTime(p.t))) : start;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return [];
  start = Math.floor(start / unit) * unit;
  end = Math.floor(end / unit) * unit;
  // Bound DOM work even for years of retained data. Longer spans aggregate
  // adjacent civil-time buckets; they never discard the quiet periods.
  const span = Math.floor((end - start) / unit) + 1;
  const group = Math.max(1, Math.ceil(span / 60));
  const width = group * unit;
  const points = [];
  for (let t = start; t <= end; t += width) points.push({ time: t, end: Math.min(t + width - unit, end), value: 0, unit });
  series.forEach((point) => {
    const index = Math.floor((statsCivilTime(point.t) - start) / width);
    if (index >= 0 && index < points.length) points[index].value += point.v;
  });
  return points;
}

function statsBucketLabel(point, compact = false) {
  const format = (value) => {
    const date = new Date(value);
    const day = `${date.getUTCDate()} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][date.getUTCMonth()]}`;
    return point.unit === 3600000 ? `${compact ? "" : day + ", "}${String(date.getUTCHours()).padStart(2, "0")}:00` : day;
  };
  return point.end === point.time ? format(point.time) : `${format(point.time)}–${format(point.end)}`;
}

async function initStats() {
  const chart = document.getElementById("stats-chart");
  if (!chart) return;
  state.statsLoaded = true;
  const chips = document.querySelectorAll("#tab-stats .chip");
  let requestId = 0;
  let chartPoints = [];
  let lastTimeseries = null;
  let loadedWindow = null;
  const text = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
  const periods = { "24h": "the last 24 hours", "7d": "the last 7 days", "30d": "the last 30 days", all: "retained history" };
  const error = (message) => {
    const el = document.getElementById("stats-error");
    if (el) { el.hidden = !message; el.textContent = message; }
  };
  const renderChart = (timeseries) => {
    lastTimeseries = timeseries;
    chartPoints = statsChartBuckets(timeseries);
    const max = Math.max(1, ...chartPoints.map((p) => p.value));
    const ceiling = Math.max(2, Math.ceil(max / 2) * 2);
    const width = Math.max(300, Math.min(900, chart.clientWidth || window.innerWidth || 800));
    chart.setAttribute("viewBox", `0 0 ${width} 240`);
    const left = 36, right = width - 18, top = 18, bottom = 198;
    const step = (right - left) / Math.max(chartPoints.length, 1);
    let markup = `<title>Recorded activity by time, including manual commands</title>`;
    [0, ceiling / 2, ceiling].forEach((value) => {
      const y = bottom - value / ceiling * (bottom - top);
      markup += `<line class="stats-gridline" x1="${left}" y1="${y}" x2="${right}" y2="${y}" />`;
      markup += `<text class="stats-axis" x="${left - 10}" y="${y + 4}" text-anchor="end">${value}</text>`;
    });
    chartPoints.forEach((point, index) => {
      const height = point.value ? point.value / ceiling * (bottom - top) : 2;
      const label = `${statsBucketLabel(point)}: ${point.value} recorded ${point.value === 1 ? "event" : "events"}`;
      markup += `<rect data-index="${index}" tabindex="0" role="button" aria-label="${escapeHtml(label)}" class="stats-bar${point.value ? "" : " stats-bar-empty"}" x="${left + index * step + step * .12}" y="${bottom - height}" width="${Math.max(1, step * .76)}" height="${height}" rx="2"><title>${escapeHtml(label)}</title></rect>`;
      const labelEvery = Math.max(1, Math.ceil(chartPoints.length / (width < 480 ? 3 : 5)));
      if (index % labelEvery === 0 || index === chartPoints.length - 1 && index % labelEvery > 1) {
        const anchor = index === 0 ? "start" : index === chartPoints.length - 1 ? "end" : "middle";
        markup += `<text class="stats-axis" x="${left + index * step + step / 2}" y="224" text-anchor="${anchor}">${escapeHtml(statsBucketLabel(point, true))}</text>`;
      }
    });
    chart.innerHTML = markup;
    const populated = chartPoints.some((point) => point.value > 0);
    const label = timeseries && timeseries.bucket === "hour" ? "Hourly" : "Daily";
    const grouped = chartPoints.length && chartPoints[0].end !== chartPoints[0].time;
    text("chart-label", `${grouped ? "Grouped daily" : label} records · Pi local time`);
    text("stats-chart-reading", populated ? "Select a bar for its count. Includes manual commands." : "No recorded activity in this period.");
  };
  const renderRanking = (id, items, empty) => {
    const el = document.getElementById(id);
    if (!el) return;
    const rows = (Array.isArray(items) ? items : []).slice(0, 8);
    const maximum = Math.max(1, ...rows.map((item) => Number(item.count) || 0));
    el.innerHTML = rows.length ? rows.map((item) => {
      const count = Math.max(0, Number(item.count) || 0);
      const plate = item.plate && item.plate !== "UNKNOWN" ? item.plate : "Unreadable";
      return `<div class="stats-ranking-row"><span class="stats-registration">${escapeHtml(plate)}</span><span class="stats-track" aria-hidden="true"><span style="width:${count / maximum * 100}%"></span></span><span class="stats-count">${count}</span></div>`;
    }).join("") : `<div class="stats-empty">${escapeHtml(empty)}</div>`;
  };
  const renderHealth = (data) => {
    if (!data) {
      text("stats-health-summary", "Status unavailable");
      ["health-temp", "health-disk-free", "health-maintenance", "health-failures"].forEach((id) => text(id, "—"));
      ["health-temp-meta", "health-disk-meta", "health-maintenance-meta", "health-failures-meta"].forEach((id) => text(id, "Status unavailable"));
      return;
    }
    const disk = data.disk || {}, maintenance = data.maintenance || {};
    const failed = Array.isArray(data.failed_units) ? data.failed_units : [];
    const temp = Number.isFinite(data.temperature_c) ? `${data.temperature_c.toFixed(1)}°C` : "Unavailable";
    text("health-temp", temp);
    const services = data.services || {};
    const active = ["gate_anpr_web", "gate_anpr", "alprd", "stream_jpeg"].every((name) => services[name] === "active");
    text("health-temp-meta", active ? "Services active; progress is not measured" : "One or more services inactive or unknown");
    text("health-disk-free", Number.isFinite(disk.free_pct) ? `${disk.free_pct.toFixed(0)}% free` : "Unavailable");
    text("health-disk-meta", Number.isFinite(disk.free_bytes) ? `${(disk.free_bytes / 1073741824).toFixed(1)} GB available` : "Disk status unavailable");
    text("health-maintenance", maintenance.last_success ? maintenance.stale ? "Overdue" : "Up to date" : "Unknown");
    text("health-maintenance-meta", maintenance.last_success ? `Last cleanup ${Number.isFinite(maintenance.age_hours) ? maintenance.age_hours + "h ago" : maintenance.last_success}` : "No successful cleanup recorded");
    text("health-failures", String(failed.length));
    text("health-failures-meta", failed.length ? failed.join(", ") : "No failed services reported");
    text("stats-health-summary", `${temp} · ${failed.length ? failed.length + " failed services" : active ? "Services active" : "Check services"}`);
  };
  const loadStats = async (windowKey) => {
    const current = ++requestId;
    error("");
    text("stats-summary", `Loading ${periods[windowKey] || "activity"}…`);
    // Remove prior-period values immediately: a failed new request must not
    // leave yesterday's totals looking like this month's data.
    ["stat-total", "stat-recognised", "stat-unmatched", "stat-manual-opens", "stat-processing", "insight-no-plate"].forEach((id) => text(id, "—"));
    text("insight-no-plate-meta", "");
    chartPoints = [];
    lastTimeseries = null;
    chart.innerHTML = "";
    text("chart-label", "Loading activity…");
    text("stats-chart-reading", "");
    renderRanking("stats-recognised-list", [], "Loading…");
    renderRanking("stats-unmatched-list", [], "Loading…");
    const health = fetchJsonWithDeadline("/api/service-health").then((response) => {
      if (current === requestId) renderHealth(response.ok ? response.data : null);
    }).catch(() => { if (current === requestId) renderHealth(null); });
    try {
      const responses = await Promise.all([
        fetchJsonWithDeadline(`/api/stats?window=${encodeURIComponent(windowKey)}`),
        fetchJsonWithDeadline(`/api/stats/insights?window=${encodeURIComponent(windowKey)}`),
      ]);
      if (current !== requestId) return;
      if (!responses.every((response) => response.ok)) throw new Error("Statistics unavailable");
      const data = responses[0].data, insightData = responses[1].data;
      if (!data || !data.counts || !data.timeseries || !insightData || !insightData.insights) throw new Error("Invalid statistics");
      const insights = insightData.insights;
      const rec = Number(data.counts.recognised || 0);
      const unmatched = Number(data.counts.unmatched || 0) + Number(data.counts.candidate || 0);
      const manual = Number(data.counts.manual_open || 0);
      text("stat-total", rec + unmatched);
      text("stat-recognised", rec);
      text("stat-unmatched", unmatched);
      text("stat-manual-opens", manual);
      text("stats-summary", rec + unmatched || manual
        ? `${rec} recognised detections, ${unmatched} unmatched captures and ${manual} manual commands in ${periods[windowKey]}.`
        : `No recorded activity in ${periods[windowKey]}. New captures will appear here.`);
      const processing = insights.avg_processing_ms && insights.avg_processing_ms.overall;
      text("stat-processing", Number.isFinite(processing) ? formatProcessingTime(processing) : "Unavailable");
      text("insight-no-plate", Number(insights.no_plate || 0));
      text("insight-no-plate-meta", "No registration returned; not an accuracy score");
      renderChart(data.timeseries);
      renderRanking("stats-recognised-list", insights.top_recognised_list, "No recognised detections in this period.");
      renderRanking("stats-unmatched-list", insights.top_unmatched_list, "No unmatched captures in this period.");
      loadedWindow = windowKey;
      updateStatusTimestamp();
    } catch (err) {
      if (current !== requestId) return;
      text("stats-summary", "Activity could not be loaded.");
      text("chart-label", "Activity unavailable");
      error("Could not load this period. Select it again to retry; no gate controls are affected.");
      renderRanking("stats-recognised-list", [], "Activity unavailable.");
      renderRanking("stats-unmatched-list", [], "Activity unavailable.");
    }
    // Health is independent; an unavailable maintenance check must not hide
    // already loaded activity, and its deadline cannot lock the whole page.
    await health;
  };
  chips.forEach((chip) => chip.addEventListener("click", () => {
    chips.forEach((button) => { button.classList.remove("active"); button.setAttribute("aria-pressed", "false"); });
    chip.classList.add("active");
    chip.setAttribute("aria-pressed", "true");
    loadStats(chip.dataset.window);
  }));
  [["insight-top-plate-card", "recognised"], ["insight-top-unmatched-card", "unmatched"]].forEach(([id, kind]) => {
    const card = document.getElementById(id);
    if (card) card.addEventListener("click", () => {
      if (loadedWindow) {
        state.eventsWindow = loadedWindow;
        const selector = document.getElementById("history-window");
        if (selector) selector.value = loadedWindow;
      }
      showHistoryForKinds([kind]);
    });
  });
  const readBar = (event) => {
    if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
    const target = event.target.closest ? event.target.closest("[data-index]") : null;
    if (!target) return;
    const point = chartPoints[Number(target.getAttribute("data-index"))];
    if (point) {
      if (event.type === "keydown") event.preventDefault();
      text("stats-chart-reading", `${statsBucketLabel(point)} · ${point.value} recorded ${point.value === 1 ? "event" : "events"}`);
    }
  };
  chart.addEventListener("click", readBar);
  chart.addEventListener("keydown", readBar);
  window.addEventListener("resize", () => {
    if (lastTimeseries && isTabActive("stats")) renderChart(lastTimeseries);
  });
  loadStats("24h");
}


initTheme();
if (isAdmin) {
  initAdmin();
} else if (isStats) {
  initStats();
} else if (isMain) {
  initMain();
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/sw.js").catch(() => {});
}
