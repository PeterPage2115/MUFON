/* Operations module — the admin surface: settings form (validation, save,
   schedule sync feedback), manual actions (fetch/report with the run
   feedback) and the job log. Depends on ui, api, status (renderStatus) and
   analysis (alliance-dirty marking + active-panel refresh after actions). */

import { api } from "./api.js";
import {
  els,
  state,
  $,
  setText,
  formatTime,
  showToast,
  setBusy,
} from "./ui.js";
import { renderStatus } from "./status.js";
import { markAllianceDirty, refreshActiveAnalysis } from "./analysis.js";

// Aliases into the shared state object (ui owns the single source of truth).
// NOTE: primitives are snapshots — only the job-log/action state below is
// touched by this module, so value copies are safe.
var currentSettings = state.currentSettings;
var knownLogKeys = state.knownLogKeys;
var logEls = state.logEls;
var actionInFlight = state.actionInFlight;

var SETTINGS_KEYS = [
  "ALLIANCE_TAGS", "TRACKED_ALLIANCES", "CHANNEL_ID", "ADMIN_ROLE_ID",
  "FETCH_HOUR", "FETCH_MINUTE", "FETCH_TZ",
  "REPORT_HOUR", "REPORT_MINUTE", "REPORT_TZ",
  "REPORT_EMBED_COLOR",
];

/* --- settings form ------------------------------------------------------------ */

function hexToInt(hex) {
  return parseInt(hex.replace(/^#/, ""), 16);
}

function intToHex(value) {
  return "#" + (value >>> 0).toString(16).toUpperCase().padStart(6, "0");
}

function settingsFromForm() {
  var out = {};
  els.settingsForm.querySelectorAll("[data-setting-key]").forEach(function (el) {
    var key = el.getAttribute("data-setting-key");
    if (key === "REPORT_EMBED_COLOR") return; // canonical input: the text field below
    out[key] = el.value;
  });
  // The text input is the canonical color source: a type=color picker only
  // ever yields a valid #rrggbb, so an invalid typed hex would be silently
  // "accepted" (and ignored). validateSettings then blocks the PUT.
  var colorText = document.getElementById("REPORT_EMBED_COLOR_TEXT");
  out.REPORT_EMBED_COLOR = colorText ? colorText.value : "";
  return out;
}

function fieldFor(key) {
  return els.settingsForm.querySelector('[data-field="' + key + '"]');
}

function setFieldError(key, message) {
  var field = fieldFor(key);
  if (!field) return;
  field.classList.toggle("field--invalid", !!message);
  var errorEl = field.querySelector(".field-error");
  if (errorEl) setText(errorEl, message || "");
}

function isValidTimezone(name) {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: name });
    return true;
  } catch (_e) {
    return false;
  }
}

function validateSettings(values) {
  var errors = {};

  var tags = (values.ALLIANCE_TAGS || "")
    .split(/[\n,]+/)
    .map(function (t) {
      return t.trim();
    })
    .filter(Boolean);
  var unique = [];
  tags.forEach(function (t) {
    if (unique.indexOf(t) === -1) unique.push(t);
  });
  if (!unique.length) {
    errors.ALLIANCE_TAGS = "At least one alliance tag is required.";
  }

  var tracked = (values.TRACKED_ALLIANCES || "")
    .split(/[\n,]+/)
    .map(function (t) {
      return t.trim();
    })
    .filter(Boolean);
  var trackedUnique = [];
  tracked.forEach(function (t) {
    if (trackedUnique.indexOf(t) === -1) trackedUnique.push(t);
  });
  // Empty is allowed: it just hides the Standings field.

  ["FETCH_HOUR", "REPORT_HOUR"].forEach(function (key) {
    var v = values[key] === "" ? NaN : Number(values[key]);
    if (isNaN(v) || v < 0 || v > 23) errors[key] = "Hour must be between 0 and 23.";
  });

  ["FETCH_MINUTE", "REPORT_MINUTE"].forEach(function (key) {
    var v = values[key] === "" ? NaN : Number(values[key]);
    if (isNaN(v) || v < 0 || v > 59) errors[key] = "Minute must be between 0 and 59.";
  });

  ["FETCH_TZ", "REPORT_TZ"].forEach(function (key) {
    var tz = String(values[key] || "").trim();
    if (!tz) {
      errors[key] = "Timezone is required (e.g. Europe/Warsaw).";
    } else if (!isValidTimezone(tz)) {
      errors[key] = "Unknown timezone \"" + tz + "\". Use IANA names like Europe/Warsaw.";
    }
  });

  var channel = values.CHANNEL_ID;
  if (channel === "" || channel === null) {
    errors.CHANNEL_ID = "Channel ID is required (the daily report target).";
  } else if (!/^\d+$/.test(String(channel))) {
    errors.CHANNEL_ID = "Channel ID must be an integer.";
  }

  var admin = values.ADMIN_ROLE_ID;
  if (admin !== "" && admin !== null && !/^\d+$/.test(String(admin))) {
    errors.ADMIN_ROLE_ID = "Admin role ID must be an integer (or empty).";
  }

  var colorHex = String(values.REPORT_EMBED_COLOR || "").trim();
  if (!/^#?[0-9a-fA-F]{6}$/.test(colorHex)) {
    errors.REPORT_EMBED_COLOR = "Color must be six hex digits, e.g. #D1A84A.";
  }

  return { errors: errors, tags: unique, trackedTags: trackedUnique };
}

// The API accepts only JSON ints (dashboard/app.py `_int_setting`). Send a
// real Number when the digits fit JS's safe-integer range; Discord snowflakes
// can exceed Number.MAX_SAFE_INTEGER (2^53), where Number() silently rounds
// (111111111111111111 -> 111111111111111100), so those keep their exact digit
// string — stringifyPayload emits strings as unquoted digit literals, so the
// API still receives the exact int.
function intSetting(value) {
  if (value === "" || value === null || value === undefined) return null;
  var digits = String(value).trim();
  var num = Number(digits);
  return Number.isSafeInteger(num) ? num : digits;
}

function payloadFromValues(values, tags, trackedTags) {
  return {
    ALLIANCE_TAGS: tags,
    TRACKED_ALLIANCES: trackedTags,
    CHANNEL_ID: intSetting(values.CHANNEL_ID),
    FETCH_HOUR: Number(values.FETCH_HOUR),
    FETCH_MINUTE: Number(values.FETCH_MINUTE),
    FETCH_TZ: String(values.FETCH_TZ).trim(),
    REPORT_HOUR: Number(values.REPORT_HOUR),
    REPORT_MINUTE: Number(values.REPORT_MINUTE),
    REPORT_TZ: String(values.REPORT_TZ).trim(),
    ADMIN_ROLE_ID: intSetting(values.ADMIN_ROLE_ID),
    REPORT_EMBED_COLOR: hexToInt(values.REPORT_EMBED_COLOR),
  };
}

function renderSettingsForm(settings) {
  currentSettings = settings;

  var tagsEl = els.settingsForm.querySelector('[data-setting-key="ALLIANCE_TAGS"]');
  if (tagsEl && !tagsEl.dataset.userEdited) {
    tagsEl.value = (settings.ALLIANCE_TAGS || []).join("\n");
  }

  var trackedEl = els.settingsForm.querySelector('[data-setting-key="TRACKED_ALLIANCES"]');
  if (trackedEl && !trackedEl.dataset.userEdited) {
    trackedEl.value = (settings.TRACKED_ALLIANCES || []).join("\n");
  }

  var channelEl = els.settingsForm.querySelector('[data-setting-key="CHANNEL_ID"]');
  if (channelEl) channelEl.value = settings.CHANNEL_ID === null || settings.CHANNEL_ID === undefined ? "" : String(settings.CHANNEL_ID);

  var adminEl = els.settingsForm.querySelector('[data-setting-key="ADMIN_ROLE_ID"]');
  if (adminEl) adminEl.value = settings.ADMIN_ROLE_ID === null || settings.ADMIN_ROLE_ID === undefined ? "" : String(settings.ADMIN_ROLE_ID);

  [
    ["FETCH_HOUR", "FETCH_MINUTE", "FETCH_TZ"],
    ["REPORT_HOUR", "REPORT_MINUTE", "REPORT_TZ"],
  ].forEach(function (group) {
    group.forEach(function (key) {
      var el = els.settingsForm.querySelector('[data-setting-key="' + key + '"]');
      if (el && settings[key] !== undefined && settings[key] !== null) el.value = String(settings[key]);
    });
  });

  var colorInput = els.settingsForm.querySelector('[data-setting-key="REPORT_EMBED_COLOR"]');
  var colorText = document.getElementById("REPORT_EMBED_COLOR_TEXT");
  var colorInt = Number(settings.REPORT_EMBED_COLOR);
  if (!isNaN(colorInt)) {
    var hex = intToHex(colorInt);
    if (colorInput && colorInput.value !== hex) colorInput.value = hex;
    if (colorText && colorText.value !== hex) colorText.value = hex;
  }

  // Clear stale validation state after a successful reload. The feedback
  // line is NOT cleared here: submitSettings' refresh follows a save whose
  // schedule_sync message must survive (loadSettings clears it on load).
  SETTINGS_KEYS.forEach(function (key) {
    setFieldError(key, "");
  });
}

function setFeedback(text, kind) {
  var el = els.settingsFeedback;
  el.textContent = text;
  el.classList.toggle("is-success", kind === "success");
  el.classList.toggle("is-error", kind === "error");
}

function submitSettings(event) {
  event.preventDefault();

  // Clear stale validation state first: a field corrected since the last
  // submit must not keep its old error message when another field blocks.
  SETTINGS_KEYS.forEach(function (key) {
    setFieldError(key, "");
  });

  var values = settingsFromForm();
  var checked = validateSettings(values);
  Object.keys(checked.errors).forEach(function (key) {
    setFieldError(key, checked.errors[key]);
  });
  if (Object.keys(checked.errors).length) {
    setFeedback("Fix the highlighted fields before saving.", "error");
    return;
  }

  var payload = payloadFromValues(values, checked.tags, checked.trackedTags);
  var saved = false;
  setBusy(els.saveButton, true, "Saving…");

  api
    .saveSettings(payload)
    .then(function (body) {
      saved = true;
      // The response's schedule_sync reports what actually happened to the
      // running bot's scheduler — the feedback must match reality.
      var sync = body && body.schedule_sync;
      var feedback;
      if (sync === "applied") {
        feedback = "Settings saved. Schedule updated.";
      } else if (sync === "pending") {
        feedback = "Settings saved. Schedule will apply when the bot starts.";
      } else if (sync === "failed") {
        feedback = "Settings saved, but scheduler refresh failed. Restart the bot.";
      } else {
        feedback = "Settings saved.";
      }
      if (sync === "failed") {
        setFeedback(feedback, "error");
        showToast("Settings saved", "Scheduler refresh failed. Restart the bot.", "error");
      } else {
        setFeedback(feedback, "success");
        showToast(
          "Settings saved",
          sync === "applied" ? "The scheduler picked up the new schedule." : "Changes are stored.",
          "success"
        );
      }
      return Promise.all([api.settings(), api.status()]);
    })
    .then(function (results) {
      renderSettingsForm(results[0]);
      renderStatus(results[1]);
      // Settings may have changed ALLIANCE_TAGS/TRACKED_ALLIANCES — every
      // filtered payload is stale until re-entered or manually refreshed.
      markAllianceDirty();
      refreshActiveAnalysis();
    })
    .catch(function (err) {
      if (saved) {
        setFeedback("Settings saved, but the refresh failed.", "error");
        showToast("Refresh failed", err.message, "error");
      } else {
        setFeedback(err.message, "error");
        showToast("Settings not saved", err.message, "error");
      }
    })
    .then(function () {
      setBusy(els.saveButton, false);
    });
}

/* --- actions ---------------------------------------------------------------- */

//: Run polling is allowed ONLY while an explicitly launched action is
//: settling: it stops at the first terminal status (ROADMAP.md §6) and never
//: refreshes daily KPIs in the background.
var RUN_POLL_MS = 1500;
var RUN_POLL_MAX = 80; // ~2 minutes of settling time
var TERMINAL_RUN_STATUSES = ["succeeded", "skipped", "failed", "timed_out"];

function runStatusWord(status) {
  return status === "timed_out" ? "timed out" : status;
}

function pollRun(runId, onUpdate) {
  var attempts = 0;
  var timer = window.setInterval(function () {
    attempts += 1;
    api
      .run(runId)
      .then(function (run) {
        if (!run) return;
        if (onUpdate) onUpdate(run);
        if (TERMINAL_RUN_STATUSES.indexOf(run.status) !== -1 || attempts >= RUN_POLL_MAX) {
          window.clearInterval(timer);
          loadRuns();
        }
      })
      .catch(function () {
        if (attempts >= RUN_POLL_MAX) window.clearInterval(timer);
      });
  }, RUN_POLL_MS);
}

function hideActionConfirm() {
  var box = document.getElementById("action-confirm");
  if (box) box.hidden = true;
}

function requestReportConfirmation() {
  var snapshot = state.lastStatus ? state.lastStatus.snapshot_date : null;
  var channel = currentSettings ? currentSettings.CHANNEL_ID : null;
  var copy = document.querySelector("[data-action-confirm-copy]");
  if (copy) {
    if (snapshot) {
      setText(
        copy,
        "Send the daily report to channel " + (channel === null || channel === undefined ? "?" : channel) +
          " for snapshot " + snapshot + "?"
      );
    } else {
      setText(copy, "No snapshot stored yet — the report will send the no-data placeholder. Send anyway?");
    }
  }
  var box = document.getElementById("action-confirm");
  if (box) box.hidden = false;
  var yes = document.getElementById("action-confirm-yes");
  if (yes) yes.focus();
}

function runAction(kind) {
  if (actionInFlight) return;
  hideActionConfirm();
  actionInFlight = true;

  var button = kind === "fetch" ? els.fetchButton : els.reportButton;
  var label = kind === "fetch" ? "Fetching…" : "Sending…";
  setBusy(button, true, label);
  els.actionFeedback.classList.remove("is-success", "is-error");
  setText($("#action-feedback span:last-child"), "Running…");

  api
    .action(kind)
    .then(function (body) {
      var message = body && body.message ? body.message : "Done";
      var title = kind === "fetch" ? "Fetch completed" : "Report action completed";
      showToast(title, message, "success");
      var runTag = body && body.run_id ? " · run " + body.run_id.slice(0, 8) : "";
      setText($("#action-feedback span:last-child"), "Result: " + message + runTag);
      els.actionFeedback.classList.add("is-success");
      if (body && body.run_id) {
        pollRun(body.run_id, function (run) {
          setText(
            $("#action-feedback span:last-child"),
            "Run " + run.run_id.slice(0, 8) + ": " + runStatusWord(run.status) +
              (run.result ? " · " + run.result : "")
          );
        });
      }
    })
    .catch(function (err) {
      var message = err.status === 409 || err.status === 504 ? err.message : "Action failed: " + err.message;
      var title = err.status === 409 ? "Action skipped" : err.status === 504 ? "Action timed out" : "Action failed";
      showToast(title, message, "error");
      setText($("#action-feedback span:last-child"), message);
      els.actionFeedback.classList.add("is-error");
      // A timed-out action still settles later (asyncio.shield): follow its
      // run row to the terminal state.
      if (err.status === 504 && err.body && err.body.run_id) {
        pollRun(err.body.run_id, function (run) {
          setText(
            $("#action-feedback span:last-child"),
            "Run " + run.run_id.slice(0, 8) + ": " + runStatusWord(run.status) +
              (run.result ? " · " + run.result : "")
          );
        });
      }
    })
    .then(function () {
      // A fetch may create a snapshot; a report may fail on missing data —
      // refresh status, logs and the active analysis panel so the console
      // reflects reality (on demand, never a background poller).
      var jobs = [api.status(), api.logs(), refreshActiveAnalysis(), loadRuns()];
      return Promise.all(jobs).catch(function () {
        return [null, null, null, null];
      });
    })
    .then(function (results) {
      if (results[0]) renderStatus(results[0]);
      if (results[1]) renderLogs(results[1]);
    })
    .then(function () {
      actionInFlight = false;
      setBusy(button, false);
    });
}


/* --- job log ---------------------------------------------------------------- */

function logKey(entry) {
  return entry.ts + "|" + entry.job + "|" + entry.level + "|" + entry.message;
}

function levelClass(level) {
  if (level === "error") return "log-entry--error";
  if (level === "warning") return "log-entry--warning";
  return "log-entry--info";
}

function levelWord(level) {
  if (level === "error") return "ERR";
  if (level === "warning") return "WRN";
  return "INF";
}

function renderLogs(entries) {
  var list = els.jobLog;
  if (!list) return;

  var emptyEl = list.querySelector(".log-empty");
  if (entries.length === 0) {
    if (!emptyEl) {
      var emptyLi = document.createElement("li");
      emptyLi.className = "log-empty";
      emptyLi.textContent = "No activity yet.";
      list.appendChild(emptyLi);
    } else {
      setText(emptyEl, "No activity yet.");
    }
    knownLogKeys.clear();
    logEls = {};
    setText(els.logCount, "No activity yet");
    setText(els.logUpdated, "updated " + formatTime(new Date().toISOString()));
    list.setAttribute("aria-busy", "false");
    return;
  }

  if (emptyEl && emptyEl.parentNode) emptyEl.parentNode.removeChild(emptyEl);

  var fresh = new Set(entries.map(logKey));
  var staleKeys = [];
  knownLogKeys.forEach(function (key) {
    if (!fresh.has(key)) staleKeys.push(key);
  });
  staleKeys.forEach(function (key) {
    knownLogKeys.delete(key);
    var item = logEls[key];
    delete logEls[key];
    if (item && item.parentNode) item.parentNode.removeChild(item);
  });

  entries.forEach(function (entry) {
    var key = logKey(entry);
    if (knownLogKeys.has(key)) return;
    knownLogKeys.add(key);

    var li = document.createElement("li");
    li.className = "log-entry " + levelClass(entry.level || "info");
    li.setAttribute("data-log-key", key);
    logEls[key] = li;

    var ts = document.createElement("span");
    ts.className = "log-entry__ts";
    ts.textContent = formatTime(entry.ts);

    var job = document.createElement("span");
    job.className = "log-entry__job";
    job.textContent = entry.job || "?";

    var level = document.createElement("span");
    level.className = "log-entry__level";
    level.textContent = levelWord(entry.level || "info");

    var msg = document.createElement("span");
    msg.className = "log-entry__msg";
    msg.textContent = entry.message || "";

    li.appendChild(ts);
    li.appendChild(job);
    li.appendChild(level);
    li.appendChild(msg);

    list.insertBefore(li, list.firstChild);
  });

  while (list.children.length > MAX_LOGS) {
    var last = list.lastChild;
    var lastKey = last.getAttribute("data-log-key");
    if (lastKey) {
      knownLogKeys.delete(lastKey);
      delete logEls[lastKey];
    }
    list.removeChild(last);
  }

  setText(els.logCount, entries.length + (entries.length === 1 ? " entry" : " entries"));
  setText(els.logUpdated, "updated " + formatTime(new Date().toISOString()));
  els.logFooter.classList.add("is-live");
  list.setAttribute("aria-busy", "false");
}

function loadLogs() {
  return api.logs().then(renderLogs).catch(function (err) {
    els.jobLog.setAttribute("aria-busy", "false");
    showToast("Job log refresh failed", err.message, "error");
  });
}

/* --- exports ---------------------------------------------------------------- */

export function wireForm() {
  // Keep the hex text and the color picker in sync, both directions.
  var colorInput = els.settingsForm.querySelector('[data-setting-key="REPORT_EMBED_COLOR"]');
  var colorText = document.getElementById("REPORT_EMBED_COLOR_TEXT");
  if (colorInput && colorText) {
    colorInput.addEventListener("input", function () {
      colorText.value = colorInput.value.toUpperCase();
    });
    colorText.addEventListener("input", function () {
      var value = colorText.value.trim();
      if (/^#?[0-9a-fA-F]{6}$/.test(value)) {
        colorInput.value = value.startsWith("#") ? value : "#" + value;
      }
    });
  }

  var tagsEl = els.settingsForm.querySelector('[data-setting-key="ALLIANCE_TAGS"]');
  if (tagsEl) {
    tagsEl.addEventListener("input", function () {
      tagsEl.dataset.userEdited = "1";
    });
  }

  els.settingsForm.addEventListener("submit", submitSettings);
}

export function loadSettings() {
  return api
    .settings()
    .then(function (settings) {
      setFeedback("", ""); // fresh load clears stale save feedback
      renderSettingsForm(settings);
    })
    .catch(function (err) {
      showToast("Settings unavailable", err.message, "error");
    });
}

function cell(text) {
  var td = document.createElement("td");
  td.className = "num";
  td.textContent = text === null || text === undefined ? "\u2014" : String(text);
  return td;
}

function runsFilters() {
  return {
    job: document.getElementById("runs-job") ? document.getElementById("runs-job").value : "",
    status: document.getElementById("runs-status") ? document.getElementById("runs-status").value : "",
  };
}

export function loadRuns() {
  var filters = runsFilters();
  return api
    .runs({ job: filters.job, status: filters.status, limit: 50 })
    .then(renderRuns)
    .catch(function () {});
}

function renderRuns(runs) {
  var tbody = document.querySelector("[data-runs-body]");
  if (!tbody) return;
  tbody.textContent = "";
  if (!runs || !runs.length) {
    var tr = document.createElement("tr");
    var td = document.createElement("td");
    td.colSpan = 6;
    td.className = "empty-cell";
    td.textContent = "No runs yet.";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  runs.forEach(function (run) {
    var tr = document.createElement("tr");
    var tdStarted = document.createElement("td");
    tdStarted.className = "date-cell";
    tdStarted.textContent = formatTime(run.started_at);
    tr.appendChild(tdStarted);
    tr.appendChild(cell(run.job));
    tr.appendChild(cell(run.source));
    var tdStatus = document.createElement("td");
    tdStatus.className = "run-status run-status--" + run.status;
    tdStatus.textContent = runStatusWord(run.status);
    tr.appendChild(tdStatus);
    tr.appendChild(cell(run.result || "—"));
    tr.appendChild(cell(run.snapshot_date || "—"));
    tbody.appendChild(tr);
  });
}

export function renderDbStats() {
  var caption = document.querySelector("[data-db-stats]");
  var stats = state.lastStatus ? state.lastStatus.database_stats : null;
  if (!caption) return;
  if (!stats || !stats.snapshot_count) {
    setText(caption, "");
    return;
  }
  var size = stats.db_size_bytes >= 1048576
    ? (stats.db_size_bytes / 1048576).toFixed(1) + " MB"
    : Math.max(1, Math.round(stats.db_size_bytes / 1024)) + " KB";
  setText(
    caption,
    stats.snapshot_count + " snapshot" + (stats.snapshot_count === 1 ? "" : "s") +
      " · " + size + " · " + (stats.oldest_snapshot_date || "—") + " → " + (stats.latest_snapshot_date || "—")
  );
}

function wireRunsFilters() {
  var job = document.getElementById("runs-job");
  var status = document.getElementById("runs-status");
  if (job) job.addEventListener("change", loadRuns);
  if (status) status.addEventListener("change", loadRuns);
}

export function wireActionButtons() {
  els.fetchButton.addEventListener("click", function () {
    runAction("fetch");
  });
  els.reportButton.addEventListener("click", function () {
    // Conscious confirmation: channel + snapshot date before any Discord
    // message leaves the process (ROADMAP.md §6).
    requestReportConfirmation();
  });
  var yes = document.getElementById("action-confirm-yes");
  var no = document.getElementById("action-confirm-no");
  if (yes) yes.addEventListener("click", function () {
    runAction("report");
  });
  if (no) no.addEventListener("click", hideActionConfirm);
  wireRunsFilters();
}

// loadLogs is defined in the job-log section above; exported for the
// bootstrap's manual refresh + view-activation cycle.
export { loadLogs };
