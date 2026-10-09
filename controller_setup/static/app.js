/**
 * 🎮 3DR Solo Controller Setup GUI - Interactive Client
 */

let definitions = null;
let currentConfig = {
  mappings: {},
  timeOffsetHrs: "None",
  raw_text: ""
};
let originalRaw = "";
let selectedButtonId = 4; // Default to Button A

// Predefined Profiles
const PRESET_PROFILES = {
  default: {
    name: "Default OpenSolo",
    mappings: {
      "4,7": { shot: -1, mode: 0, func: -1 },   // A Short: Stabilize
      "4,5": { shot: -1, mode: 3, func: -1 },   // A Long: Auto (Autonomous)
      "5,7": { shot: -1, mode: 11, func: -1 },  // B Short: Drift
      "5,5": { shot: -1, mode: 13, func: -1 },  // B Long: Sport
      "6,5": { shot: -1, mode: -1, func: -1 },  // Preset 1 Long: None
      "7,5": { shot: -1, mode: -1, func: -1 },  // Preset 2 Long: None
      "8,7": { shot: -1, mode: -1, func: -1 },  // Camera Short: None
      "8,5": { shot: -1, mode: -1, func: 3 }    // Camera Long: Gimbal Lock Toggle
    },
    timeOffsetHrs: "None"
  },
  cinematography: {
    name: "Cinematography & Shots",
    mappings: {
      "4,7": { shot: 2, mode: -1, func: -1 },   // A Short: Cablecam
      "4,5": { shot: 1, mode: -1, func: -1 },   // A Long: Orbit
      "5,7": { shot: 0, mode: -1, func: -1 },   // B Short: Selfie
      "5,5": { shot: 7, mode: -1, func: -1 },   // B Long: Panorama
      "6,5": { shot: 3, mode: -1, func: -1 },   // Preset 1 Long: Zipline
      "7,5": { shot: 5, mode: -1, func: -1 },   // Preset 2 Long: Follow
      "8,7": { shot: -1, mode: -1, func: -1 },  // Camera Short: None (takes picture)
      "8,5": { shot: -1, mode: -1, func: 3 }    // Camera Long: Gimbal Lock Toggle
    },
    timeOffsetHrs: "None"
  },
  acro_sport: {
    name: "Acro & Sport Pilot",
    mappings: {
      "4,7": { shot: -1, mode: 1, func: -1 },   // A Short: Acro
      "4,5": { shot: -1, mode: 14, func: -1 },  // A Long: Flip
      "5,7": { shot: -1, mode: 13, func: -1 },  // B Short: Sport
      "5,5": { shot: -1, mode: 15, func: -1 },  // B Long: Auto-tune
      "6,5": { shot: -1, mode: 0, func: -1 },   // Preset 1 Long: Stabilize
      "7,5": { shot: -1, mode: 17, func: -1 },  // Preset 2 Long: Brake
      "8,7": { shot: -1, mode: -1, func: -1 },
      "8,5": { shot: -1, mode: -1, func: 3 }
    },
    timeOffsetHrs: "None"
  },
  payload_gripper: {
    name: "Payload / Gripper / Servo",
    mappings: {
      "4,7": { shot: -1, mode: -1, func: 4 },   // A Short: Servo 6 LOW
      "4,5": { shot: -1, mode: -1, func: 6 },   // A Long: Servo 6 HIGH
      "5,7": { shot: -1, mode: -1, func: 13 },  // B Short: Gripper Close
      "5,5": { shot: -1, mode: -1, func: 14 },  // B Long: Gripper Open
      "6,5": { shot: -1, mode: -1, func: 7 },   // Preset 1 Long: Servo 7 Low
      "7,5": { shot: -1, mode: -1, func: 9 },   // Preset 2 Long: Servo 7 High
      "8,7": { shot: -1, mode: -1, func: -1 },
      "8,5": { shot: -1, mode: -1, func: 3 }    // Camera Long: Gimbal Lock
    },
    timeOffsetHrs: "None"
  },
  survey_smartrtl: {
    name: "Survey & SmartRTL",
    mappings: {
      "4,7": { shot: -1, mode: 3, func: -1 },   // A Short: Autonomous (Auto)
      "4,5": { shot: -1, mode: 17, func: -1 },  // A Long: Brake
      "5,7": { shot: -1, mode: 21, func: -1 },  // B Short: SmartRTL
      "5,5": { shot: -1, mode: 9, func: -1 },   // B Long: Land
      "6,5": { shot: 9, mode: -1, func: -1 },   // Preset 1 Long: Transect
      "7,5": { shot: 7, mode: -1, func: -1 },   // Preset 2 Long: Panorama
      "8,7": { shot: -1, mode: -1, func: -1 },
      "8,5": { shot: -1, mode: -1, func: 3 }
    },
    timeOffsetHrs: "None"
  }
};

document.addEventListener("DOMContentLoaded", () => {
  initApp();
});

async function initApp() {
  bindEvents();
  await checkStatus();
  await loadDefinitions();
  await loadConfig();
  setInterval(checkStatus, 5000);
}

function bindEvents() {
  // Navigation actions
  document.getElementById("fetchBtn").addEventListener("click", () => loadConfig(true));
  document.getElementById("presetsBtn").addEventListener("click", openPresetsModal);
  document.getElementById("rawBtn").addEventListener("click", openRawModal);
  document.getElementById("saveBtn").addEventListener("click", openConfirmModal);

  // Time offset controls
  const timeInput = document.getElementById("timeOffsetInput");
  timeInput.addEventListener("input", (e) => {
    const val = e.target.value.trim();
    currentConfig.timeOffsetHrs = val ? val : "None";
    document.getElementById("timeOffsetDisplay").textContent = currentConfig.timeOffsetHrs;
  });

  document.getElementById("timeNoneBtn").addEventListener("click", () => {
    currentConfig.timeOffsetHrs = "None";
    timeInput.value = "";
    document.getElementById("timeOffsetDisplay").textContent = "None";
  });

  document.getElementById("timeLocalBtn").addEventListener("click", () => {
    const offsetHours = -new Date().getTimezoneOffset() / 60;
    const offsetStr = offsetHours >= 0 ? `+${offsetHours}` : `${offsetHours}`;
    currentConfig.timeOffsetHrs = offsetStr;
    timeInput.value = offsetHours;
    document.getElementById("timeOffsetDisplay").textContent = offsetStr;
  });

  // Modal close buttons
  document.querySelectorAll("[data-close-modal]").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".modal-backdrop").forEach(m => m.classList.remove("open"));
    });
  });

  // Presets modal selection
  document.querySelectorAll(".preset-card").forEach(card => {
    card.addEventListener("click", () => {
      const presetKey = card.dataset.preset;
      applyPreset(presetKey);
    });
  });

  // Raw editor actions
  document.getElementById("rawRevertBtn").addEventListener("click", () => {
    document.getElementById("rawConfigTextarea").value = buildRawFromCurrent();
  });

  document.getElementById("rawApplyToGuiBtn").addEventListener("click", () => {
    parseRawIntoCurrent(document.getElementById("rawConfigTextarea").value);
    document.getElementById("rawModal").classList.remove("open");
    renderButtonCards();
    showToast("Parsed raw text and updated button configurations!");
  });

  // Confirm Upload
  document.getElementById("confirmUploadBtn").addEventListener("click", performUpload);

  // SVG interactive buttons
  document.querySelectorAll(".interactive-btn").forEach(el => {
    el.addEventListener("click", () => {
      const btnId = parseInt(el.dataset.btnId, 10);
      selectButton(btnId);
    });
  });
}

async function checkStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();

    const soloVal = document.getElementById("soloStatusVal");
    const soloChip = document.getElementById("soloStatusChip").querySelector(".dot");
    if (data.solo_connected) {
      soloChip.className = "dot connected";
      soloVal.textContent = "Online (Connected)";
    } else {
      soloChip.className = "dot disconnected";
      soloVal.textContent = "Offline (Local mode)";
    }

    const ctrlVal = document.getElementById("ctrlStatusVal");
    const ctrlChip = document.getElementById("ctrlStatusChip").querySelector(".dot");
    if (data.controller_connected) {
      ctrlChip.className = "dot connected";
      ctrlVal.textContent = "Online";
    } else {
      ctrlChip.className = "dot disconnected";
      ctrlVal.textContent = "Standby";
    }
  } catch (e) {
    // Server offline or connection lost
  }
}

async function loadDefinitions() {
  try {
    const res = await fetch("/api/definitions");
    definitions = await res.json();
  } catch (e) {
    console.error("Failed to load definitions", e);
  }
}

async function loadConfig(isManualFetch = false) {
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    currentConfig = data;
    originalRaw = data.raw_text || "";

    const liveBadge = document.getElementById("liveBadge");
    if (data.is_live) {
      liveBadge.textContent = "🟢 Live on Solo 10.1.1.10";
      liveBadge.style.color = "var(--accent-green)";
    } else {
      liveBadge.textContent = "🟡 Using Local Template";
      liveBadge.style.color = "var(--accent-amber)";
    }

    // Time offset display
    const timeDisplay = document.getElementById("timeOffsetDisplay");
    const timeInput = document.getElementById("timeOffsetInput");
    timeDisplay.textContent = currentConfig.timeOffsetHrs || "None";
    if (currentConfig.timeOffsetHrs && currentConfig.timeOffsetHrs !== "None") {
      timeInput.value = parseFloat(currentConfig.timeOffsetHrs);
    } else {
      timeInput.value = "";
    }

    renderButtonCards();
    updateControllerLegend();

    if (isManualFetch) {
      showToast("Settings successfully loaded from Solo!");
    }
  } catch (e) {
    showToast("Error loading config from server", true);
  }
}

function renderButtonCards() {
  if (!definitions) return;
  const container = document.getElementById("buttonCardsContainer");
  container.innerHTML = "";

  definitions.buttons.forEach(btn => {
    const card = document.createElement("div");
    card.className = `btn-config-card ${btn.id === selectedButtonId ? "selected" : ""}`;
    card.id = `card-btn-${btn.id}`;

    // Header
    const header = document.createElement("div");
    header.className = "btn-card-header";
    header.innerHTML = `
      <div class="btn-title-wrap">
        <div class="btn-ident-bubble ${btn.id >= 6 ? 'sub' : ''}">${btn.id === 8 ? '📷' : (btn.id === 4 ? 'A' : (btn.id === 5 ? 'B' : (btn.id === 6 ? '1' : '2')))}</div>
        <div>
          <div class="btn-ident-text">${btn.name}</div>
          <div class="btn-ident-desc">${btn.description}</div>
        </div>
      </div>
      <span class="badge badge-pulse">ID: ${btn.id}</span>
    `;
    card.appendChild(header);

    // Body with Events
    const body = document.createElement("div");
    body.className = "btn-card-body";

    btn.supported_events.forEach(eventId => {
      const eventDef = definitions.events.find(e => e.id === eventId);
      const lookupKey = `${btn.id},${eventId}`;
      const mapping = currentConfig.mappings[lookupKey] || { shot: -1, mode: -1, func: -1 };

      const eventRow = document.createElement("div");
      eventRow.className = "event-row";
      eventRow.id = `row-${btn.id}-${eventId}`;

      // Event Title
      const eventHeader = document.createElement("div");
      eventHeader.className = "event-header";
      eventHeader.innerHTML = `
        <div class="event-label-wrap">
          <span class="event-pill ${eventId === 7 ? 'short' : 'long'}">${eventDef ? eventDef.short_label : 'Hold'}</span>
          <span class="event-name">${eventDef ? eventDef.name : `Event ${eventId}`}</span>
        </div>
        <span class="event-key-badge">Key: [${lookupKey}]</span>
      `;
      eventRow.appendChild(eventHeader);

      // Unified Action Dropdown pulldown list
      const selectWrap = document.createElement("div");
      selectWrap.className = "unified-select-wrap";

      const actionSelect = document.createElement("select");
      actionSelect.className = "custom-select action-dropdown";

      // 1. None Option
      const optNone = document.createElement("option");
      optNone.value = "none";
      optNone.textContent = "⛔ None / Unassigned";
      actionSelect.appendChild(optNone);

      // 2. Flight Modes Group
      const grpModes = document.createElement("optgroup");
      grpModes.label = "🚀 Flight Modes";
      definitions.modes.forEach(m => {
        if (m.id === -1) return;
        const opt = document.createElement("option");
        opt.value = `mode:${m.id}`;
        opt.textContent = m.name;
        if (m.disabled) opt.disabled = true;
        grpModes.appendChild(opt);
      });
      actionSelect.appendChild(grpModes);

      // 3. Smart Shots Group
      const grpShots = document.createElement("optgroup");
      grpShots.label = "🎬 Smart Shots";
      definitions.shots.forEach(s => {
        if (s.id === -1) return;
        const opt = document.createElement("option");
        opt.value = `shot:${s.id}`;
        opt.textContent = s.name;
        grpShots.appendChild(opt);
      });
      actionSelect.appendChild(grpShots);

      // 4. Auxiliary / Servos Group
      const grpFuncs = document.createElement("optgroup");
      grpFuncs.label = "⚙️ Auxiliary / Gimbal / Servos";
      definitions.functions.forEach(f => {
        if (f.id === -1) return;
        const opt = document.createElement("option");
        opt.value = `func:${f.id}`;
        opt.textContent = f.name;
        if (f.disabled) opt.disabled = true;
        grpFuncs.appendChild(opt);
      });
      actionSelect.appendChild(grpFuncs);

      // Set current selected value
      if (mapping.shot >= 0) {
        actionSelect.value = `shot:${mapping.shot}`;
      } else if (mapping.mode >= 0) {
        actionSelect.value = `mode:${mapping.mode}`;
      } else if (mapping.func >= 0) {
        actionSelect.value = `func:${mapping.func}`;
      } else {
        actionSelect.value = "none";
      }

      // Summary Tag
      const summaryTag = document.createElement("div");
      summaryTag.className = "action-summary-badge";
      summaryTag.textContent = formatSummary(mapping);

      selectWrap.appendChild(actionSelect);
      selectWrap.appendChild(summaryTag);
      eventRow.appendChild(selectWrap);

      // Bind change event
      actionSelect.addEventListener("change", () => {
        const val = actionSelect.value;
        if (val === "none") {
          mapping.shot = -1;
          mapping.mode = -1;
          mapping.func = -1;
        } else if (val.startsWith("mode:")) {
          mapping.mode = parseInt(val.split(":")[1], 10);
          mapping.shot = -1;
          mapping.func = -1;
        } else if (val.startsWith("shot:")) {
          mapping.shot = parseInt(val.split(":")[1], 10);
          mapping.mode = -1;
          mapping.func = -1;
        } else if (val.startsWith("func:")) {
          mapping.func = parseInt(val.split(":")[1], 10);
          mapping.shot = -1;
          mapping.mode = -1;
        }

        currentConfig.mappings[lookupKey] = mapping;
        summaryTag.textContent = formatSummary(mapping);
        updateControllerLegend();
      });

      body.appendChild(eventRow);
    });

    card.appendChild(body);
    container.appendChild(card);
  });
}

function formatSummary(mapping) {
  const parts = [];
  if (mapping.shot >= 0 && definitions) {
    const s = definitions.shots.find(x => x.id === mapping.shot);
    parts.push(`Shot: ${s ? s.name : mapping.shot}`);
  }
  if (mapping.mode >= 0 && definitions) {
    const m = definitions.modes.find(x => x.id === mapping.mode);
    const shortMode = m ? m.name.split("(")[0].trim() : mapping.mode;
    parts.push(`Mode: ${shortMode}`);
  }
  if (mapping.func >= 0 && definitions) {
    const f = definitions.functions.find(x => x.id === mapping.func);
    const shortFunc = f ? f.name.split("(")[0].trim() : mapping.func;
    parts.push(`Aux: ${shortFunc}`);
  }
  return parts.length > 0 ? parts.join(" | ") : "Disabled (None)";
}

function updateControllerLegend() {
  if (!definitions) return;

  const btnIds = [4, 5, 6, 7, 8];
  btnIds.forEach(id => {
    const legendElem = document.getElementById(`legendSummary${id}`);
    const svgElem = document.getElementById(`svgBtn${id}`);
    if (!legendElem) return;

    const shortKey = `${id},7`;
    const longKey = `${id},5`;
    const shortMap = currentConfig.mappings[shortKey];
    const longMap = currentConfig.mappings[longKey];

    const summaries = [];
    if (shortMap && (shortMap.shot >= 0 || shortMap.mode >= 0 || shortMap.func >= 0)) {
      summaries.push(`Short: ${formatSummary(shortMap)}`);
    }
    if (longMap && (longMap.shot >= 0 || longMap.mode >= 0 || longMap.func >= 0)) {
      summaries.push(`Long: ${formatSummary(longMap)}`);
    }

    if (summaries.length === 0) {
      legendElem.textContent = "Unassigned / Stock";
    } else {
      legendElem.textContent = summaries.join(" • ");
    }
  });
}

function selectButton(btnId) {
  selectedButtonId = btnId;

  // Highlight card
  document.querySelectorAll(".btn-config-card").forEach(c => c.classList.remove("selected"));
  const targetCard = document.getElementById(`card-btn-${btnId}`);
  if (targetCard) {
    targetCard.classList.add("selected");
    targetCard.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // Highlight SVG
  document.querySelectorAll(".interactive-btn").forEach(el => el.classList.remove("active"));
  const targetSvg = document.getElementById(`svgBtn${btnId}`);
  if (targetSvg) {
    targetSvg.classList.add("active");
  }

  // Highlight Legend
  document.querySelectorAll(".legend-item").forEach(l => l.classList.remove("active"));
  const targetLegend = document.getElementById(`legendBtn${btnId}`);
  if (targetLegend) {
    targetLegend.classList.add("active");
  }
}

function openPresetsModal() {
  document.getElementById("presetsModal").classList.add("open");
}

function applyPreset(presetKey) {
  const profile = PRESET_PROFILES[presetKey];
  if (!profile) return;

  // Deep clone mappings
  currentConfig.mappings = JSON.parse(JSON.stringify(profile.mappings));
  currentConfig.timeOffsetHrs = profile.timeOffsetHrs || "None";

  document.getElementById("timeOffsetDisplay").textContent = currentConfig.timeOffsetHrs;
  document.getElementById("timeOffsetInput").value = currentConfig.timeOffsetHrs === "None" ? "" : currentConfig.timeOffsetHrs;

  renderButtonCards();
  updateControllerLegend();

  document.getElementById("presetsModal").classList.remove("open");
  showToast(`Applied preset profile: "${profile.name}"!`);
}

function buildRawFromCurrent() {
  const lines = ["[extFunctions]"];
  const keys = Object.keys(currentConfig.mappings).sort();

  keys.forEach(k => {
    const item = currentConfig.mappings[k];
    const s = item.shot !== undefined ? item.shot : -1;
    const m = item.mode !== undefined ? item.mode : -1;
    const f = item.func !== undefined ? item.func : -1;
    lines.push(`${k}=${s}, ${m}, ${f}`);
  });

  lines.push(`timeOffsetHrs=${currentConfig.timeOffsetHrs || "None"}`);
  lines.push("");
  lines.push("");

  // Preserve or append standard documentation
  if (originalRaw && originalRaw.indexOf("[USAGE]") !== -1) {
    lines.push(originalRaw.substring(originalRaw.indexOf("[USAGE]")).trim());
  } else {
    lines.push("[USAGE]\nUSAGE: {button},{event} = {shot}, {mode}, {func}");
  }

  return lines.join("\n") + "\n";
}

function parseRawIntoCurrent(raw) {
  const lines = raw.split("\n");
  let inExt = false;
  const newMappings = {};
  let timeOffset = "None";

  lines.forEach(line => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith(";")) return;
    if (trimmed.startsWith("[")) {
      inExt = (trimmed.replace(/[\[\]]/g, "").trim().toLowerCase() === "extfunctions");
      return;
    }

    if (inExt && trimmed.includes("=")) {
      const [k, v] = trimmed.split("=", 2).map(s => s.trim());
      if (k.toLowerCase() === "timeoffsethrs") {
        timeOffset = v;
      } else {
        const parts = v.split(",").map(s => parseInt(s.trim(), 10) || -1);
        newMappings[k] = {
          shot: parts[0] !== undefined ? parts[0] : -1,
          mode: parts[1] !== undefined ? parts[1] : -1,
          func: parts[2] !== undefined ? parts[2] : -1
        };
      }
    }
  });

  currentConfig.mappings = newMappings;
  currentConfig.timeOffsetHrs = timeOffset;
  currentConfig.raw_text = raw;

  document.getElementById("timeOffsetDisplay").textContent = timeOffset;
  document.getElementById("timeOffsetInput").value = timeOffset === "None" ? "" : timeOffset;
}

function openRawModal() {
  const rawText = buildRawFromCurrent();
  document.getElementById("rawConfigTextarea").value = rawText;
  document.getElementById("rawModal").classList.add("open");
}

function openConfirmModal() {
  const newRaw = buildRawFromCurrent();
  const diffBox = document.getElementById("diffViewContainer");
  diffBox.innerHTML = "";

  const origLines = originalRaw.split("\n");
  const newLines = newRaw.split("\n");

  // Show top differences
  let diffCount = 0;
  const maxLines = Math.max(origLines.length, newLines.length);

  for (let i = 0; i < maxLines; i++) {
    const o = origLines[i] !== undefined ? origLines[i] : null;
    const n = newLines[i] !== undefined ? newLines[i] : null;

    if (o !== n) {
      diffCount++;
      if (o !== null) {
        const spanRem = document.createElement("span");
        spanRem.className = "diff-line diff-rem";
        spanRem.textContent = `- ${o}`;
        diffBox.appendChild(spanRem);
      }
      if (n !== null) {
        const spanAdd = document.createElement("span");
        spanAdd.className = "diff-line diff-add";
        spanAdd.textContent = `+ ${n}`;
        diffBox.appendChild(spanAdd);
      }
    } else if (i < 12 && n) {
      const spanSame = document.createElement("span");
      spanSame.className = "diff-line diff-same";
      spanSame.textContent = `  ${n}`;
      diffBox.appendChild(spanSame);
    }
  }

  if (diffCount === 0) {
    const spanNone = document.createElement("span");
    spanNone.className = "diff-line diff-same";
    spanNone.textContent = "  (No differences from current configuration)";
    diffBox.appendChild(spanNone);
  }

  document.getElementById("confirmModal").classList.add("open");
}

async function performUpload() {
  const confirmBtn = document.getElementById("confirmUploadBtn");
  confirmBtn.disabled = true;
  confirmBtn.textContent = "Writing to Solo...";

  const newRaw = buildRawFromCurrent();

  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        raw_text: newRaw,
        mappings: currentConfig.mappings,
        timeOffsetHrs: currentConfig.timeOffsetHrs
      })
    });

    const data = await res.json();
    confirmBtn.disabled = false;
    confirmBtn.textContent = "✅ Confirm & Write to Solo";
    document.getElementById("confirmModal").classList.remove("open");

    if (data.remote_saved) {
      originalRaw = newRaw;
      showToast("🎉 Successfully updated /usr/bin/extSettings.conf on Solo! Changes are active immediately.");
    } else if (data.local_saved || data.success) {
      originalRaw = newRaw;
      showToast("💾 Saved locally (Solo is offline or not reachable). Backup created: " + data.local_backup);
    } else {
      showToast("Error writing configuration: " + (data.error || "Unknown"), true);
    }
  } catch (e) {
    confirmBtn.disabled = false;
    confirmBtn.textContent = "✅ Confirm & Write to Solo";
    showToast("Network error uploading to server: " + e.message, true);
  }
}

function showToast(msg, isError = false) {
  const toast = document.getElementById("toast");
  toast.textContent = msg;
  toast.className = `toast show ${isError ? 'error' : ''}`;
  setTimeout(() => {
    toast.className = "toast";
  }, 4000);
}
