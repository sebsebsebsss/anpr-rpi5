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
  timelineEvents: [],
  timelineOffset: 0,
  timelineWindow: "7d",
};

let GROUP_WINDOW_SEC = 60;

function normalizeKind(kind) {
  return kind === "candidate" ? "unmatched" : kind;
}

function setStatus(msg) {
  if (!statusEl) return;
  statusEl.textContent = msg;
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
      };
      groups.set(key, group);
    }
    group.events.push({ ...event, kind: normalizeKind(event.kind) });
    if (event.image_url) {
      const exists = group.images.some((img) => img.url === event.image_url);
      if (!exists) {
        group.images.push({ url: event.image_url, name: event.image_name });
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
    const kindDiff = (order[a.kind] ?? 9) - (order[b.kind] ?? 9);
    if (kindDiff !== 0) return kindDiff;
    return (b.confidence ?? -1) - (a.confidence ?? -1);
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
    const kindDiff = (order[a.kind] ?? 9) - (order[b.kind] ?? 9);
    if (kindDiff !== 0) return kindDiff;
    return (b.confidence ?? -1) - (a.confidence ?? -1);
  });
}

function renderFrameStrip(images, heroImg) {
  if (!images.length) return null;
  const strip = document.createElement("div");
  strip.className = "frame-strip";
  images.slice(0, 6).forEach((img, idx) => {
    const thumb = document.createElement("img");
    thumb.src = img.url;
    thumb.alt = "frame";
    thumb.className = idx === 0 ? "active" : "";
    thumb.addEventListener("click", () => {
      heroImg.src = img.url;
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
    const frames = group.images.length || 0;
    summary.innerHTML = `
      <div class="meta">${when} • ${group.events.length} reads • ${frames} frame${
        frames === 1 ? "" : "s"
      } • ${processingLabel}</div>
      <div class="event-kinds">
        <span class="kind-chip recognised">Recognised ${counts.recognised}</span>
        <span class="kind-chip unmatched">Unmatched ${counts.unmatched}</span>
      </div>
    `;
    card.appendChild(summary);

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
    card.appendChild(header);

    const altWrap = document.createElement("div");
    altWrap.className = "alt-plates";
    const plateLines = document.createElement("div");
    plateLines.className = "plate-lines";
    entries.forEach((entry) => {
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
      plateLines.appendChild(line);
    });
    altWrap.appendChild(plateLines);
    const altCount = entries.length - (best ? 1 : 0);
    if (altCount > 0) {
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "alt-toggle";
      toggle.textContent = `Show alternates (${altCount})`;
      toggle.addEventListener("click", () => {
        const open = altWrap.classList.toggle("open");
        toggle.textContent = open ? "Hide alternates" : `Show alternates (${altCount})`;
      });
      card.appendChild(toggle);
      card.appendChild(altWrap);
    }

    const heroImage = group.images[0]?.url;
    if (heroImage) {
      const img = document.createElement("img");
      img.src = heroImage;
      img.alt = "capture";
      card.appendChild(img);
      if (group.images.length > 1) {
        const actions = document.createElement("div");
        actions.className = "frame-actions";
        const nextBtn = document.createElement("button");
        nextBtn.type = "button";
        nextBtn.className = "frame-next";
        nextBtn.textContent = "Next frame";
        let idx = 0;
        nextBtn.addEventListener("click", () => {
          idx = (idx + 1) % group.images.length;
          img.src = group.images[idx].url;
          const strip = actions.querySelector(".frame-strip");
          if (strip) {
            strip.querySelectorAll("img").forEach((node, i) => {
              node.classList.toggle("active", i === idx);
            });
          }
        });
        const strip = renderFrameStrip(group.images, img);
        if (strip) actions.appendChild(strip);
        actions.appendChild(nextBtn);
        card.appendChild(actions);
      }
    }
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
  latestImageEl.appendChild(image);
  const badge = document.createElement("div");
  badge.className = "badge";
  badge.textContent = event.kind || "capture";
  latestImageEl.appendChild(badge);
}

function renderLatestEvent() {
  const latestEventEl = document.getElementById("latest-event");
  const cooldownEl = document.getElementById("cooldown-status");
  if (!latestEventEl) return;
  const latest = state.latestRecognised;
  latestEventEl.innerHTML = "";
  if (!latest) {
    latestEventEl.textContent = "No recent plate events.";
    if (cooldownEl) cooldownEl.textContent = "Cooldown: --";
    return;
  }
  latestEventEl.innerHTML = `
    <div class="plate">${latest.plate || "UNKNOWN"}</div>
    <div class="owner">${latest.owner || ""}</div>
    <div class="meta">${latest.kind} • ${formatRelative(latest.captured_at)}</div>
  `;
  if (cooldownEl && latest.kind === "recognised") {
    cooldownEl.textContent = `Last fire: ${formatRelative(latest.captured_at)}`;
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

function setActiveTab(target, { updateHash = true } = {}) {
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
  if (target === "plates" && state.events.length === 0) {
    state.eventsKinds = new Set(["recognised", "unmatched"]);
    fetchEvents({ reset: true });
  }
  if (target === "timeline" && !state.timelineLoaded) {
    initTimeline();
  }
  if (target === "stream" && !state.streamLoaded) {
    initStream();
  }
  if (target === "stats" && !state.statsLoaded) {
    initStats();
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
  const known = new Set(["gate", "plates", "timeline", "stream", "stats"]);
  let initial = "gate";
  if (hash && known.has(hash)) {
    initial = hash;
  } else if (window.location.pathname === "/stats") {
    initial = "stats";
  }
  setActiveTab(initial, { updateHash: false });
}

function setKindFilters(kinds) {
  const chips = document.querySelectorAll("#tab-plates .chip");
  state.eventsKinds = new Set(kinds);
  chips.forEach((chip) => {
    chip.classList.toggle("active", state.eventsKinds.has(chip.dataset.kind));
  });
  renderEvents();
}

function initFilters() {
  const chips = document.querySelectorAll("#tab-plates .chip");
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

function initGateButton() {
  const gateBtn = document.getElementById("open-gate");
  const gateStatus = document.getElementById("gate-status");
  const cooldownEl = document.getElementById("cooldown-status");
  if (!gateBtn) return;
  gateBtn.addEventListener("click", async () => {
    gateBtn.disabled = true;
    gateBtn.textContent = "Opening...";
    if (gateStatus) gateStatus.textContent = "Triggering GPIO";
    try {
      const resp = await fetch("/api/open-gate", { method: "POST" });
      if (resp.status === 429) {
        const data = await resp.json();
        const retryIn = data.retry_in || 30;
        setStatus(`Cooldown ${retryIn}s`);
        startCooldownCountdown(retryIn);
      } else if (!resp.ok) {
        setStatus("Open failed");
      } else {
        setStatus("Gate opened");
        initCooldownStatus();
      }
    } catch (err) {
      setStatus("Open failed");
    } finally {
      if (!gateBtn.dataset.cooldown) {
        gateBtn.disabled = false;
        gateBtn.textContent = "Open the gate";
        if (gateStatus) gateStatus.textContent = "Ready";
        if (cooldownEl) cooldownEl.textContent = "Cooldown: --";
      }
    }
  });
}

function startCooldownCountdown(seconds) {
  const gateBtn = document.getElementById("open-gate");
  const gateStatus = document.getElementById("gate-status");
  const cooldownEl = document.getElementById("cooldown-status");
  if (!gateBtn) return;
  gateBtn.dataset.cooldown = "true";
  let remaining = seconds;
  if (gateStatus) gateStatus.textContent = "Cooldown active";
  const tick = () => {
    if (cooldownEl) cooldownEl.textContent = `Cooldown: ${remaining}s`;
    gateBtn.textContent = `Wait ${remaining}s`;
    gateBtn.disabled = true;
    remaining -= 1;
    if (remaining < 0) {
      gateBtn.dataset.cooldown = "";
      gateBtn.disabled = false;
      gateBtn.textContent = "Open the gate";
      if (gateStatus) gateStatus.textContent = "Ready";
      if (cooldownEl) cooldownEl.textContent = "Cooldown: --";
      return;
    }
    setTimeout(tick, 1000);
  };
  tick();
}

function initInfiniteScroll() {
  window.addEventListener("scroll", () => {
    const platesPanel = document.getElementById("tab-plates");
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
    const historyTab = document.querySelector('.tab[data-tab="plates"]');
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

async function initCooldownStatus() {
  try {
    const resp = await fetch("/api/gate-cooldown");
    const data = await resp.json();
    if (data.remaining > 0) {
      startCooldownCountdown(data.remaining);
    }
  } catch (err) {
    setStatus("Cooldown check failed");
  }
}

async function initMain() {
  try {
    const resp = await fetch("/api/config");
    const data = await resp.json();
    if (Number.isFinite(data.group_window_sec)) {
      GROUP_WINDOW_SEC = data.group_window_sec;
    }
  } catch (err) {
    // Use default window when config is unavailable.
  }
  initTabs();
  initGateButton();
  initInfiniteScroll();
  initFilters();
  initLatestJump();
  setStatus("Loading...");
  setKindFilters(["recognised", "unmatched"]);
  state.latestRecognised = null;
  await fetchEvents({ reset: true });
  initCooldownStatus();
  setStatus(`Updated ${new Date().toLocaleTimeString()}`);
  refreshLatest();
  setInterval(refreshLatest, 15000);
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
      el = document.createElement("img");
      el.alt = "Live stream";
      let refreshTimer = null;
      const refresh = () => {
        const sep = url.includes("?") ? "&" : "?";
        const next = `${url}${sep}ts=${Date.now()}`;
        const probe = new Image();
        probe.onload = () => {
          el.src = next;
        };
        probe.onerror = () => {
          if (refreshTimer) clearTimeout(refreshTimer);
          refreshTimer = setTimeout(refresh, 300);
        };
        probe.src = next;
      };
      refresh();
      setInterval(refresh, 200);
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
      if (!data.video10?.ok) issues.push("v10");
      if (!data.video11?.ok) issues.push("v11");
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
      parts.push(svcLabel("rtsp", "rtsp_v4l2"));
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
  });
  savePlatesBtn.addEventListener("click", saveAllowlist);
}

function formatRelative(ts) {
  if (!ts) return "--";
  const date = new Date(ts.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return ts;
  const delta = Math.max(0, Date.now() - date.getTime());
  const minutes = Math.floor(delta / 60000);
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
    const barWidth = (chart.width - padding * 2) / series.length;
    series.forEach((point, idx) => {
      const height = (point.v / max) * (chart.height - padding * 2);
      const x = padding + idx * barWidth;
      const y = chart.height - padding - height;
      ctx.fillStyle = "#15a05f";
      ctx.fillRect(x, y, Math.max(2, barWidth - 4), height);
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
    totalEl.textContent = data.total ?? "--";
    const recognisedCount = Number(data.counts.recognised || 0);
    const unmatchedCount = Number(data.counts.unmatched || 0) + Number(data.counts.candidate || 0);
    recEl.textContent = recognisedCount;
    unmatchEl.textContent = unmatchedCount;
    const total = Number(data.total || 0);
    const recognised = Number(data.counts.recognised || 0);
    if (hitRateEl) {
      hitRateEl.textContent = total ? `${((recognised / total) * 100).toFixed(1)}%` : "--";
    }
    const avgProcessing = insightsData?.insights?.avg_processing_ms?.overall;
    if (processingEl) {
      processingEl.textContent = Number.isFinite(avgProcessing)
        ? formatProcessingTime(avgProcessing)
        : "--";
    }
    const label = data.timeseries.bucket === "hour" ? "Hourly activity" : "Daily activity";
    renderChart(data.timeseries.series, label);
    const insights = insightsData?.insights || {};
    if (insightLabel) {
      insightLabel.textContent = `Window: ${windowKey}`;
    }
    if (topPlateEl && topPlateMetaEl) {
      const plate = insights.top_recognised?.plate;
      const count = insights.top_recognised?.count || 0;
      topPlateEl.textContent = plate && plate !== "UNKNOWN" ? plate : "--";
      topPlateMetaEl.textContent = count ? `${count} hits` : "No recognised plates";
    }
    if (topUnmatchedEl && topUnmatchedMetaEl) {
      const plate = insights.top_unmatched?.plate;
      const count = insights.top_unmatched?.count || 0;
      topUnmatchedEl.textContent = plate && plate !== "UNKNOWN" ? plate : "--";
      topUnmatchedMetaEl.textContent = count ? `${count} unmatched reads` : "No unmatched reads";
    }
    if (busiestEl && busiestMetaEl) {
      const bucket = insights.busiest_bucket?.bucket;
      const count = insights.busiest_bucket?.count || 0;
      const bucketType = insights.busiest_bucket?.bucket_type === "hour" ? "Busiest hour" : "Busiest day";
      busiestEl.textContent = bucket || "--";
      busiestMetaEl.textContent = count ? `${bucketType}: ${count} events` : "No activity";
    }
    if (noPlateEl && noPlateMetaEl) {
      const noPlate = Number(insights.no_plate || 0);
      noPlateEl.textContent = Number.isFinite(noPlate) ? String(noPlate) : "--";
      noPlateMetaEl.textContent = total ? `${((noPlate / total) * 100).toFixed(1)}% of events` : "--";
    }
    setStatus(`Updated ${new Date().toLocaleTimeString()}`);
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
      setActiveTab("plates");
      setKindFilters(["recognised"]);
    });
  }
  const topUnmatchedCard = document.getElementById("insight-top-unmatched-card");
  if (topUnmatchedCard) {
    topUnmatchedCard.addEventListener("click", () => {
      setActiveTab("plates");
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
    const containerWidth = chart.parentElement?.clientWidth || 600;
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

if (isAdmin) {
  initAdmin();
} else if (isStats) {
  initStats();
} else if (isMain) {
  initMain();
}
