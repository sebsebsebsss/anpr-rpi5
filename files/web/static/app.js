const statusEl = document.getElementById("status");
const isAdmin = document.body.classList.contains("admin-page");
const isStats = document.body.classList.contains("stats-page");
const isMain = document.body.classList.contains("main-page");

const state = {
  plates: [],
  plateStatus: {},
  events: [],
  eventsOffset: 0,
  loadingEvents: false,
  eventsKinds: new Set(["recognised", "unmatched"]),
  latestRecognised: null,
  streamLoaded: false,
  statsLoaded: false,
  timelineLoaded: false,
  tabletLoaded: false,
  logsLoaded: false,
  timelineEvents: [],
  timelineOffset: 0,
  timelineWindow: "30d",
  tabletEvents: [],
  tabletLastFetch: 0,
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
};

const LEGACY_IOS =
  /iP(ad|hone|od)/.test(navigator.userAgent || "") &&
  /OS 12_/.test(navigator.userAgent || "");

let GROUP_WINDOW_SEC = 60;
let STREAM_REFRESH_MS = 200;

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
  if (!ts) return null;
  const date = new Date(ts.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return null;
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) return 0;
  return Math.floor(diffMs / 60000);
}

function createLegacyStreamImage(url) {
  const img = new Image();
  img.alt = "Live stream";
  img.className = "stream-image-single";
  let timer = null;
  const schedule = (delay) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(loadNext, delay);
  };
  const loadNext = () => {
    const sep = url.includes("?") ? "&" : "?";
    img.src = `${url}${sep}ts=${Date.now()}&cb=${Math.random().toString(36).slice(2)}`;
    schedule(Math.max(100, STREAM_REFRESH_MS));
  };
  loadNext();
  return img;
}

function createSmoothImageStream(url) {
  const stack = document.createElement("div");
  stack.className = "stream-image-stack";
  const display = new Image();
  display.className = "stream-image-single";
  display.alt = "Live stream";
  stack.appendChild(display);

  const buffer = new Image();
  buffer.alt = "Live stream";
  let timer = null;
  let delayMs = STREAM_REFRESH_MS;
  let inFlight = false;
  let activeUrl = "";
  let bufferUrl = "";
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const canFetchBlob =
    typeof fetch === "function" &&
    typeof URL !== "undefined" &&
    typeof URL.createObjectURL === "function";
  let useDirect = !canFetchBlob;

  const schedule = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(loadNext, delayMs);
  };

  const isMostlyBlack = (image) => {
    if (!ctx) return false;
    const sampleSize = 16;
    canvas.width = sampleSize;
    canvas.height = sampleSize;
    try {
      ctx.drawImage(image, 0, 0, sampleSize, sampleSize);
    } catch (err) {
      return false;
    }
    const data = ctx.getImageData(0, 0, sampleSize, sampleSize).data;
    let sum = 0;
    let sumSq = 0;
    const count = data.length / 4;
    for (let i = 0; i < data.length; i += 4) {
      const r = data[i];
      const g = data[i + 1];
      const b = data[i + 2];
      const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b);
      sum += lum;
      sumSq += lum * lum;
    }
    const mean = sum / count;
    const variance = sumSq / count - mean * mean;
    return mean < 6 && variance < 6;
  };

  const loadNext = async () => {
    if (inFlight) {
      schedule();
      return;
    }
    inFlight = true;
    const sep = url.includes("?") ? "&" : "?";
    const next = `${url}${sep}ts=${Date.now()}&cb=${Math.random().toString(36).slice(2)}`;
    try {
      if (!useDirect) {
        const resp = await fetch(next, { cache: "no-store" });
        if (!resp.ok) {
          throw new Error("fetch failed");
        }
        const blob = await resp.blob();
        if (ctx && typeof createImageBitmap === "function") {
          const bmp = await createImageBitmap(blob);
          const isBlack = isMostlyBlack(bmp);
          bmp.close();
          if (isBlack) {
            delayMs = Math.min(Math.round(delayMs * 1.2), 1000);
            schedule();
            return;
          }
        }
        if (bufferUrl) URL.revokeObjectURL(bufferUrl);
        bufferUrl = URL.createObjectURL(blob);
        buffer.src = bufferUrl;
      } else {
        buffer.src = next;
      }
      buffer.onload = () => {
        const nextUrl = bufferUrl || buffer.src;
        display.src = nextUrl;
        if (activeUrl) URL.revokeObjectURL(activeUrl);
        activeUrl = bufferUrl || "";
        bufferUrl = "";
        buffer.onload = null;
        buffer.onerror = null;
        delayMs = STREAM_REFRESH_MS;
        schedule();
      };
      buffer.onerror = () => {
        buffer.onload = null;
        buffer.onerror = null;
        delayMs = Math.min(Math.round(delayMs * 1.5), 1000);
        schedule();
      };
    } catch (err) {
      useDirect = true;
      delayMs = Math.min(Math.round(delayMs * 1.5), 1000);
      schedule();
    } finally {
      inFlight = false;
    }
  };

  loadNext();
  return stack;
}

async function loadAllowlist() {
  const [platesRes, statusRes] = await Promise.all([
    fetch("/api/plates"),
    fetch("/api/allowlist-status"),
  ]);
  state.plates = await platesRes.json();
  const status = await statusRes.json();
  state.plateStatus = {};
  status.forEach((entry) => {
    if (!entry.owner) return;
    state.plateStatus[entry.owner] = entry.plates || [];
  });
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
    });
    platesInput.addEventListener("input", (e) => {
      const raw = e.target.value.split(",").map((p) => p.trim()).filter(Boolean);
      state.plates[idx].plates = raw.map((p) => p.toUpperCase());
    });
    removeBtn.addEventListener("click", () => {
      state.plates.splice(idx, 1);
      renderAllowlist();
    });
    platesList.appendChild(row);
  });
}

async function saveAllowlist() {
  setStatus("Saving...");
  const cleaned = state.plates.map((entry) => ({
    owner: entry.owner,
    plates: (entry.plates || []).map((p) => p.toUpperCase()),
  }));
  const resp = await fetch("/api/plates", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cleaned),
  });
  if (!resp.ok) {
    const body = await resp.json();
    setStatus(`Save failed: ${body.error || resp.status}`);
    return;
  }
  await loadAllowlist();
  renderAllowlist();
  setStatus("Saved");
}

async function fetchEvents({ reset = false, limit = 30, kind = null } = {}) {
  if (state.loadingEvents) return;
  state.loadingEvents = true;
  if (reset) {
    state.events = [];
    state.eventsOffset = 0;
  }
  const resp = await fetch(`/api/events?offset=${state.eventsOffset}&limit=${limit}`);
  const data = await resp.json();
  state.eventsOffset += data.length;
  state.events = state.events.concat(data);
  state.loadingEvents = false;
  renderEvents();
}

function parseCapturedEpoch(ts) {
  if (!ts) return null;
  const date = new Date(ts.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return null;
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

function renderFrameStrip(images, heroImg, confEl, activeIndex = 0) {
  if (!images.length) return null;
  const strip = document.createElement("div");
  strip.className = "frame-strip";
  images.slice(0, 6).forEach((img, idx) => {
    const thumb = document.createElement("img");
    thumb.src = img.url;
    thumb.alt = "frame";
    thumb.className = idx === activeIndex ? "active" : "";
    if (Number.isFinite(img.confidence)) {
      thumb.title = `Conf: ${img.confidence.toFixed(2)}`;
    }
    thumb.addEventListener("click", () => {
      heroImg.src = img.url;
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
  const eventsMore = document.getElementById("events-more");
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
      <div class="meta">${whenAbs} • ${when} • ${group.events.length} reads • ${frames} frame${
        frames === 1 ? "" : "s"
      } • ${processingLabel}</div>
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
        observedLine = `<div class="meta">Observed: ${bestEvent.observed_plate} • Conf: ${obsConf}${fuzzy}${dist}</div>`;
      }
      header.innerHTML = `
        <div class="plate">${best.plate}</div>
        <div class="meta">${best.kind} • ${bestMeta} ${best.owner ? `• ${best.owner}` : ""}</div>
        ${observedLine}
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
        <div class="plate">${entry.plate}</div>
        <div class="meta">${entry.kind}${count} • ${conf} ${entry.owner ? `• ${entry.owner}` : ""}</div>
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
      img.src = heroImage;
      img.alt = "capture";
      imageWrap.appendChild(img);
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
        const strip = renderFrameStrip(group.images, img, conf, idx);
        if (strip) actions.appendChild(strip);
        right.appendChild(actions);
      }
    }
    body.appendChild(left);
    body.appendChild(right);
    card.appendChild(body);
    eventsList.appendChild(card);
  });

  if (eventsMore) {
    eventsMore.textContent = state.loadingEvents ? "Loading..." : "Scroll for more";
  }
  if (state.events.length === 0 || filtered.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const kinds = Array.from(state.eventsKinds).join(", ");
    empty.textContent = `No ${kinds} events yet.`;
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
  image.src = event.image_url;
  image.alt = event.plate || "capture";
  image.classList.add("latest-thumb");
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
  const date = new Date(ts.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return ts;
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
  const date = new Date(ts.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return ts;
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
    <div class="plate">${latest.plate || "UNKNOWN"}${latest.owner ? ` - ${latest.owner}` : ""}</div>
    <div class="meta">${formatDayTimeLabel(latest.captured_at)} - ${formatRelative(latest.captured_at)}</div>
  `;
  if (cooldownEl) {
    const lastOpen = formatRelativeEpoch(state.lastGateOpenTs);
    cooldownEl.textContent = `Last opened: ${lastOpen}`;
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
    renderLatestImage();
  } catch (err) {
    setStatus("Refresh failed");
  }
}

async function refreshGateLastOpen() {
  try {
    const resp = await fetch("/api/gate-last-open");
    const data = await resp.json();
    state.lastGateOpenTs = Number.isFinite(data.last_open_ts) ? data.last_open_ts : null;
  } catch (err) {
    state.lastGateOpenTs = null;
  }
  renderLatestEvent();
}

function setActiveTab(target, { updateHash = true } = {}) {
  if (target === "plates") {
    target = "candidates";
  }
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
  if (target === "candidates" && state.events.length === 0) {
    state.eventsKinds = new Set(["recognised", "unmatched"]);
    fetchEvents({ reset: true });
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
      html.push(`<div class="log-date">${line.date}</div>`);
      lastDate = line.date;
    }
    if (!line.time) {
      const msg = line.msg.trim();
      if (msg) {
        html.push(
          `<div class="log-line log-line--continuation"><span class="log-msg">${msg}</span></div>`
        );
      }
      return;
    }
    html.push(
      `<div class="log-line"><span class="log-ts">${line.time}</span><span class="log-msg">${line.msg}</span></div>`
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
      if (state.events.length === 0) {
        fetchEvents({ reset: true });
      } else {
        renderEvents();
      }
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
  gateBtn.addEventListener("click", async () => {
    gateBtn.disabled = true;
    gateBtn.textContent = "Opening...";
    setGateStatus(gateStatus, "opening", "Gate opening");
    setGateButtonState(gateBtn, "opening");
    try {
      const resp = await fetch("/api/open-gate", { method: "POST" });
      if (resp.status === 429) {
        const data = await resp.json();
        const retryIn = data.retry_in || 30;
        setStatus(`Opening the gate (${retryIn}s)`);
        startCooldownCountdown(retryIn, { gateBtn, gateStatus, cooldownEl });
      } else if (!resp.ok) {
        setStatus("Open failed");
        setGateStatus(gateStatus, "error", "Open failed");
        setGateButtonState(gateBtn, "error");
      } else {
        setStatus("Gate opened");
        initCooldownStatus();
        refreshGateLastOpen();
      }
    } catch (err) {
      setStatus("Open failed");
      setGateStatus(gateStatus, "error", "Open failed");
      setGateButtonState(gateBtn, "error");
    } finally {
      if (!gateBtn.dataset.cooldown) {
        gateBtn.disabled = false;
        gateBtn.textContent = "Open the gate";
        setGateStatus(gateStatus, "ready", "Gate ready");
        setGateButtonState(gateBtn, "ready");
        if (cooldownEl) cooldownEl.textContent = "Opening the gate: --";
      }
    }
  });
}

function startCooldownCountdown(seconds, { gateBtn, gateStatus, cooldownEl }) {
  if (!gateBtn) return;
  gateBtn.dataset.cooldown = "true";
  let remaining = seconds;
  setGateStatus(gateStatus, "opening", "Gate opening");
  setGateButtonState(gateBtn, "opening");
  const tick = () => {
    if (cooldownEl) cooldownEl.textContent = `Opening the gate: ${remaining}s`;
    gateBtn.textContent = `Opening the gate: ${remaining}s`;
    gateBtn.disabled = true;
    remaining -= 1;
    if (remaining < 0) {
      gateBtn.dataset.cooldown = "";
      gateBtn.disabled = false;
      gateBtn.textContent = "Open the gate";
      setGateStatus(gateStatus, "ready", "Gate ready");
      setGateButtonState(gateBtn, "ready");
      if (cooldownEl) cooldownEl.textContent = "Opening the gate: --";
      return;
    }
    setTimeout(tick, 1000);
  };
  tick();
}

function initInfiniteScroll() {
  window.addEventListener("scroll", () => {
    const platesPanel = document.getElementById("tab-candidates");
    if (!platesPanel || !platesPanel.classList.contains("active")) return;
    const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 200;
    if (!nearBottom) return;
    fetchEvents();
  });
}

function initLatestJump() {
  const latestJump = document.getElementById("latest-jump");
  if (!latestJump) return;
  latestJump.addEventListener("click", () => {
    const historyTab = document.querySelector('.tab[data-tab="candidates"]');
    if (historyTab) historyTab.click();
    setKindFilters(["recognised"]);
    fetchEvents({ reset: true });
    setTimeout(() => {
      const latest = document.getElementById("event-latest");
      if (latest) {
        latest.scrollIntoView({ behavior: "smooth", block: "center" });
        latest.classList.add("highlight");
        setTimeout(() => latest.classList.remove("highlight"), 1200);
      }
    }, 200);
  });
}

function jumpToEvent(eventId) {
  const historyTab = document.querySelector('.tab[data-tab="candidates"]');
  if (historyTab) historyTab.click();
  setKindFilters(["recognised", "unmatched"]);
  fetchEvents({ reset: true });
  const attempt = (tries = 0) => {
    const cards = document.querySelectorAll(".event-card[data-event-ids]");
    for (const card of cards) {
      const ids = (card.dataset.eventIds || "").split(",").map((id) => id.trim());
      if (ids.includes(eventId)) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        card.classList.add("highlight");
        setTimeout(() => card.classList.remove("highlight"), 1200);
        return;
      }
    }
    if (tries < 6) {
      if (!state.loadingEvents) {
        fetchEvents();
      }
      setTimeout(() => attempt(tries + 1), 500);
    }
  };
  setTimeout(() => attempt(0), 300);
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

async function initMain() {
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
  initInfiniteScroll();
  initFilters();
  initLatestJump();
  updateStatusTimestamp();
  setKindFilters(["recognised", "unmatched"]);
  state.latestRecognised = null;
  await fetchEvents({ reset: true });
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
    if (isTabActive("home")) {
      const now = Date.now();
      if (now - state.tabletLastFetch > 5000) {
        state.tabletLastFetch = now;
        fetchTabletTimeline();
      }
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
  fetchTabletTimeline({ reset: true });
  initCooldownStatus();
}

async function initTabletStream() {
  const frame = document.getElementById("tablet-stream-frame");
  const status = document.getElementById("tablet-stream-status");
  if (!frame) return;
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
    if (lower.startsWith("rtsp://")) {
      if (status) status.textContent = "RTSP not supported in browsers.";
      return;
    }
    if (LEGACY_IOS) {
      url = "/static/stream.jpg";
      frame.innerHTML = "";
      frame.appendChild(createLegacyStreamImage(url));
      if (status) status.textContent = "Live";
      return;
    }
    const stack = createSmoothImageStream(url);
    frame.innerHTML = "";
    frame.appendChild(stack);
    if (status) status.textContent = "Live";
  } catch (err) {
    if (status) status.textContent = "Stream failed.";
  }
}

async function fetchTabletTimeline({ reset = false } = {}) {
  const list = document.getElementById("tablet-timeline-list");
  if (!list) return;
  if (reset) state.tabletEvents = [];
  try {
    const resp = await fetch("/api/events?offset=0&limit=2&kind=recognised&window=30d");
    const data = await resp.json();
    const same =
      Array.isArray(state.tabletEvents) &&
      state.tabletEvents.length === data.length &&
      state.tabletEvents.every((event, idx) => {
        const next = data[idx] || {};
        return (
          event.id === next.id &&
          event.captured_at === next.captured_at &&
          event.image_url === next.image_url &&
          event.plate === next.plate &&
          event.owner === next.owner
        );
      });
    if (!same) {
      state.tabletEvents = data;
      renderTabletTimeline();
    }
  } catch (err) {
    // silent
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
      ? `<img src="${event.image_url}" alt="capture" loading="lazy" />`
      : `<div class="tablet-thumb-placeholder"></div>`;
    const ageMinutes = getAgeMinutes(event.captured_at);
    const dotClass = ageMinutes !== null && ageMinutes < 60 ? "dot-fresh" : "dot-stale";
    const rel = formatRelative(event.captured_at);
    row.innerHTML = `
      <div class="tablet-thumb">${thumb}</div>
      <div class="tablet-info">
        <div class="plate">${event.plate || "UNKNOWN"}${event.owner ? ` - ${event.owner}` : ""}</div>
        <div class="meta">${formatDayTimeLabel(event.captured_at)} - ${formatRelative(event.captured_at)}</div>
      </div>
      <div class="tablet-time">
        <span class="dot ${dotClass}"></span>
        <span>${rel}</span>
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

async function initStream() {
  const frame = document.getElementById("stream-frame");
  const status = document.getElementById("stream-status");
  const fpsEl = document.getElementById("stream-fps");
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

async function initStreamLag() {
  const lagEl = document.getElementById("stream-lag");
  if (!lagEl) return;
  const update = async () => {
    try {
      const resp = await fetch("/api/stream-lag");
      const data = await resp.json();
      const lagMs = Number(data.lag_ms);
      if (Number.isFinite(lagMs)) {
        lagEl.textContent = `Lag: ${(lagMs / 1000).toFixed(1)}s`;
      } else {
        lagEl.textContent = "Lag: --";
      }
    } catch (err) {
      lagEl.textContent = "Lag: --";
    }
  };
  update();
  setInterval(update, 3000);
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
  update();
  setInterval(update, 3000);
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
      systemEl.textContent = `System: ${parts.join(" • ")}`;
    } catch (err) {
      systemEl.textContent = "System: --";
    }
  };
  update();
  setInterval(update, 5000);
}

async function fetchTimeline({ reset = false, limit = 60 } = {}) {
  const list = document.getElementById("timeline-list");
  const more = document.getElementById("timeline-more");
  if (!list || state.loadingEvents) return;
  state.loadingEvents = true;
  if (reset) {
    list.innerHTML = "";
    state.timelineOffset = 0;
    state.timelineEvents = [];
  }
  const resp = await fetch(
    `/api/events?offset=${state.timelineOffset}&limit=${limit}&kind=recognised&window=${state.timelineWindow}`
  );
  const data = await resp.json();
  state.timelineOffset += data.length;
  state.timelineEvents = state.timelineEvents.concat(data);
  renderTimeline();
  if (more) {
    more.textContent = data.length ? "Scroll for more" : "No more results";
  }
  state.loadingEvents = false;
}

function renderTimeline() {
  const list = document.getElementById("timeline-list");
  if (!list) return;
  list.innerHTML = "";
  const groups = groupEventsByWindow(state.timelineEvents);
  groups.forEach((group) => {
    const entries = summarizeGroup(group.events);
    const best = pickBestEntry(entries);
    if (!best) return;
    const bestEvent = pickBestEvent(group.events, best) || group.events[0];
    const conf = Number.isFinite(best.confidence) ? `${best.confidence.toFixed(2)}` : "--";
    const plate = best.plate || bestEvent.observed_plate || "UNKNOWN";
    const observed =
      bestEvent.observed_plate &&
      bestEvent.observed_plate !== plate
        ? `Observed: ${bestEvent.observed_plate}`
        : "";
    const meta = `${group.captured_at} • ${group.events.length} reads`;
    const row = document.createElement("div");
    row.className = "timeline-row";
    row.innerHTML = `
      <div>
        <div class="plate">${plate}</div>
        <div class="meta">${meta}${observed ? ` • ${observed}` : ""}</div>
      </div>
      <div class="owner">${best.owner || bestEvent.owner || ""}</div>
      <div class="confidence">Conf: ${conf}</div>
    `;
    list.appendChild(row);
  });
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
        fetchTimeline({ reset: true });
      });
    });
  }
  fetchTimeline({ reset: true });
  window.addEventListener("scroll", () => {
    const timelinePanel = document.getElementById("tab-timeline");
    if (!timelinePanel || !timelinePanel.classList.contains("active")) return;
    const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 200;
    if (!nearBottom) return;
    fetchTimeline();
  });
}

async function initAdmin() {
  const addPlateBtn = document.getElementById("add-plate");
  const savePlatesBtn = document.getElementById("save-plates");
  const platesList = document.getElementById("plates-list");
  setStatus("Loading...");
  await loadAllowlist();
  if (Array.isArray(state.plates) && state.plates.length && Array.isArray(state.plates[0])) {
    const grouped = {};
    state.plates.forEach(([plate, owner]) => {
      grouped[owner] = grouped[owner] || { owner, plates: [] };
      grouped[owner].plates.push(plate);
    });
    state.plates = Object.values(grouped);
  }
  renderAllowlist();
  setStatus("Ready");
  addPlateBtn.addEventListener("click", () => {
    state.plates.push({ owner: "", plates: [] });
    renderAllowlist();
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
  const date = new Date(ts.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return ts;
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

async function initStats() {
  const chips = document.querySelectorAll("#tab-stats .chip");
  const totalEl = document.getElementById("stat-total");
  const recEl = document.getElementById("stat-recognised");
  const unmatchEl = document.getElementById("stat-unmatched");
  const hitRateEl = document.getElementById("stat-hit-rate");
  const processingEl = document.getElementById("stat-processing");
  const insightLabel = document.getElementById("insight-label");
  const topPlateEl = document.getElementById("insight-top-plate");
  const topPlateMetaEl = document.getElementById("insight-top-plate-meta");
  const topUnmatchedEl = document.getElementById("insight-top-unmatched");
  const topUnmatchedMetaEl = document.getElementById("insight-top-unmatched-meta");
  const busiestEl = document.getElementById("insight-busiest");
  const busiestMetaEl = document.getElementById("insight-busiest-meta");
  const noPlateEl = document.getElementById("insight-no-plate");
  const noPlateMetaEl = document.getElementById("insight-no-plate-meta");
  const chart = document.getElementById("stats-chart");
  const chartLabel = document.getElementById("chart-label");
  const donutEl = document.getElementById("stats-donut");
  const donutLegend = document.getElementById("stats-donut-legend");
  const cloudRecognised = document.getElementById("cloud-recognised");
  const cloudUnmatched = document.getElementById("cloud-unmatched");
  if (!chart) return;
  const ctx = chart.getContext("2d");
  state.statsLoaded = true;

  const renderChart = (series, label) => {
    ctx.clearRect(0, 0, chart.width, chart.height);
    if (!series.length) {
      chartLabel.textContent = "No data yet.";
      return;
    }
    chartLabel.textContent = label;
    const values = series.map((p) => p.v);
    const max = Math.max(...values, 1);
    const padding = 24;
    const width = chart.width - padding * 2;
    const height = chart.height - padding * 2;
    const step = series.length > 1 ? width / (series.length - 1) : width;
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#15a05f";
    ctx.beginPath();
    series.forEach((point, idx) => {
      const x = padding + idx * step;
      const y = chart.height - padding - (point.v / max) * height;
      if (idx === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
    ctx.lineTo(padding + (series.length - 1) * step, chart.height - padding);
    ctx.lineTo(padding, chart.height - padding);
    ctx.closePath();
    const gradient = ctx.createLinearGradient(0, padding, 0, chart.height - padding);
    gradient.addColorStop(0, "rgba(21, 160, 95, 0.35)");
    gradient.addColorStop(1, "rgba(21, 160, 95, 0.02)");
    ctx.fillStyle = gradient;
    ctx.fill();
    ctx.fillStyle = "#0b6a3b";
    series.forEach((point, idx) => {
      const x = padding + idx * step;
      const y = chart.height - padding - (point.v / max) * height;
      ctx.beginPath();
      ctx.arc(x, y, 2.5, 0, Math.PI * 2);
      ctx.fill();
    });
  };

  const renderDonut = (recognised, unmatched) => {
    if (!donutEl || !donutLegend) return;
    const total = recognised + unmatched;
    const ratio = total ? Math.round((recognised / total) * 100) : 0;
    const deg = (ratio / 100) * 360;
    donutEl.style.background = `conic-gradient(var(--accent) 0deg ${deg}deg, rgba(31, 27, 22, 0.15) ${deg}deg 360deg)`;
    donutLegend.textContent = total
      ? `${ratio}% recognised • ${100 - ratio}% unmatched`
      : "--";
  };

  const renderCloud = (el, items) => {
    if (!el) return;
    el.innerHTML = "";
    if (!items || !items.length) {
      el.textContent = "No data yet.";
      return;
    }
    const max = Math.max(...items.map((i) => i.count || 0), 1);
    items.forEach((item) => {
      const word = document.createElement("span");
      word.className = "word";
      const size = 12 + Math.round((item.count / max) * 16);
      word.style.fontSize = `${size}px`;
      word.textContent = item.plate || "UNKNOWN";
      el.appendChild(word);
    });
  };

  const loadStats = async (windowKey) => {
    setStatus("Loading stats...");
    const [statsRes, insightsRes] = await Promise.all([
      fetch(`/api/stats?window=${windowKey}`),
      fetch(`/api/stats/insights?window=${windowKey}`),
    ]);
    const data = await statsRes.json();
    const insightsData = await insightsRes.json();
    totalEl.textContent = coalesce(data.total, "--");
    const recognisedCount = Number(data.counts.recognised || 0);
    const unmatchedCount = Number(data.counts.unmatched || 0) + Number(data.counts.candidate || 0);
    recEl.textContent = recognisedCount;
    unmatchEl.textContent = unmatchedCount;
    const total = Number(data.total || 0);
    const recognised = Number(data.counts.recognised || 0);
    if (hitRateEl) {
      hitRateEl.textContent = total ? `${((recognised / total) * 100).toFixed(1)}%` : "--";
    }
    const insights = insightsData && insightsData.insights ? insightsData.insights : {};
    const avgProcessing = insights.avg_processing_ms
      ? insights.avg_processing_ms.overall
      : undefined;
    if (processingEl) {
      processingEl.textContent = Number.isFinite(avgProcessing)
        ? formatProcessingTime(avgProcessing)
        : "--";
    }
    const label = data.timeseries.bucket === "hour" ? "Hourly activity" : "Daily activity";
    renderChart(data.timeseries.series, label);
    if (insightLabel) {
      insightLabel.textContent = `Window: ${windowKey}`;
    }
    if (topPlateEl && topPlateMetaEl) {
      const topRecognised = insights.top_recognised || {};
      const plate = topRecognised.plate;
      const count = topRecognised.count || 0;
      topPlateEl.textContent = plate && plate !== "UNKNOWN" ? plate : "--";
      topPlateMetaEl.textContent = count ? `${count} hits` : "No recognised plates";
    }
    if (topUnmatchedEl && topUnmatchedMetaEl) {
      const topUnmatched = insights.top_unmatched || {};
      const plate = topUnmatched.plate;
      const count = topUnmatched.count || 0;
      topUnmatchedEl.textContent = plate && plate !== "UNKNOWN" ? plate : "--";
      topUnmatchedMetaEl.textContent = count ? `${count} unmatched reads` : "No unmatched reads";
    }
    if (busiestEl && busiestMetaEl) {
      const busiest = insights.busiest_bucket || {};
      const bucket = busiest.bucket;
      const count = busiest.count || 0;
      const bucketType = busiest.bucket_type === "hour" ? "Busiest hour" : "Busiest day";
      busiestEl.textContent = bucket || "--";
      busiestMetaEl.textContent = count ? `${bucketType}: ${count} events` : "No activity";
    }
    if (noPlateEl && noPlateMetaEl) {
      const noPlate = Number(insights.no_plate || 0);
      noPlateEl.textContent = Number.isFinite(noPlate) ? String(noPlate) : "--";
      noPlateMetaEl.textContent = total ? `${((noPlate / total) * 100).toFixed(1)}% of events` : "--";
    }
    renderDonut(recognisedCount, unmatchedCount);
    renderCloud(cloudRecognised, insights.top_recognised_list || []);
    renderCloud(cloudUnmatched, insights.top_unmatched_list || []);
    updateStatusTimestamp();
    state.timelineWindow = windowKey === "24h" ? "7d" : windowKey;
  };

  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      chips.forEach((btn) => btn.classList.remove("active"));
      chip.classList.add("active");
      loadStats(chip.dataset.window);
    });
  });

  const topPlateCard = document.getElementById("insight-top-plate-card");
  if (topPlateCard) {
    topPlateCard.addEventListener("click", () => {
      setActiveTab("candidates");
      setKindFilters(["recognised"]);
    });
  }
  const topUnmatchedCard = document.getElementById("insight-top-unmatched-card");
  if (topUnmatchedCard) {
    topUnmatchedCard.addEventListener("click", () => {
      setActiveTab("candidates");
      setKindFilters(["unmatched"]);
    });
  }
  const busiestCard = document.getElementById("insight-busiest-card");
  if (busiestCard) {
    busiestCard.addEventListener("click", () => {
      setActiveTab("timeline");
      fetchTimeline({ reset: true });
    });
  }

  const resizeCanvas = () => {
    const parent = chart.parentElement;
    const containerWidth = parent && parent.clientWidth ? parent.clientWidth : 600;
    chart.width = Math.max(240, containerWidth - 16);
    chart.height = 220;
  };
  resizeCanvas();
  window.addEventListener("resize", () => {
    resizeCanvas();
    const active = document.querySelector(".chip.active");
    if (active) {
      loadStats(active.dataset.window);
    }
  });

  loadStats("24h");

  chart.addEventListener("click", (event) => {
    const rect = chart.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const containerWidth = chart.width;
    const padding = 24;
    const active = document.querySelector(".chip.active");
    if (!active) return;
    fetch(`/api/stats?window=${active.dataset.window}`)
      .then((resp) => resp.json())
      .then((data) => {
        const series = data.timeseries.series || [];
        if (!series.length) return;
        const barWidth = (containerWidth - padding * 2) / series.length;
        const index = Math.floor((x - padding) / barWidth);
        if (index < 0 || index >= series.length) return;
        setActiveTab("timeline");
      })
      .catch(() => {});
  });
}

initTheme();
if (isAdmin) {
  initAdmin();
} else if (isStats) {
  initStats();
} else if (isMain) {
  initMain();
}
