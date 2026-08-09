/* Travian Report Bot dashboard — vanilla JS, no build step, no dependencies.
   Talks to the T12 API: /api/status, /api/settings (GET/PUT),
   /api/actions/{fetch,report}, /api/logs?n=50. Server is the source of
   truth; client-side validation only mirrors the API rules for fast feedback. */

(function () {
  "use strict";

  var LOG_REFRESH_MS = 15000; // 15s: live job console without noisy traffic (DESIGN.md §6)
  var LOG_LIMIT = 50;
  var TOAST_DISMISS_MS = 4000; // DESIGN.md §5 Toast: auto-dismiss after 4s
  var TOAST_OUT_MS = 220; // matches --motion-standard
  var MAX_LOGS = 50;

  var els = {
    headerSnapshot: document.querySelector("[data-header-snapshot]"),
    headerSource: document.querySelector("[data-header-source]"),
    statusAlert: document.querySelector("[data-status-errors]"),
    statusValues: document.querySelectorAll("[data-status-value]"),
    metricGrid: document.querySelector(".metric-grid"),
    settingsForm: document.getElementById("settings-form"),
    settingsFeedback: document.getElementById("settings-feedback"),
    saveButton: document.getElementById("save-settings"),
    fetchButton: document.getElementById("fetch-action"),
    reportButton: document.getElementById("report-action"),
    actionFeedback: document.getElementById("action-feedback"),
    jobLog: document.getElementById("job-log"),
    logCount: document.getElementById("log-count"),
    logUpdated: document.getElementById("log-updated"),
    logFooter: document.querySelector(".log-footer"),
    toastContainer: document.getElementById("toast-container"),
  };

  var currentSettings = null;
  var knownLogKeys = new Set();
  var logEls = {}; // logKey -> <li> element (avoids attribute-selector escaping issues)
  var actionInFlight = false;
  var SETTINGS_KEYS = [
    "ALLIANCE_TAGS", "CHANNEL_ID", "ADMIN_ROLE_ID",
    "FETCH_HOUR", "FETCH_MINUTE", "FETCH_TZ",
    "REPORT_HOUR", "REPORT_MINUTE", "REPORT_TZ",
    "REPORT_EMBED_COLOR",
  ];

  /* --- tiny helpers ------------------------------------------------------- */

  function $(selector) {
    return document.querySelector(selector);
  }

  function setText(el, value) {
    if (el && el.textContent !== value) el.textContent = value;
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function formatClock(iso, tz) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    try {
      return new Intl.DateTimeFormat("en-GB", {
        timeZone: tz || undefined,
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(d);
    } catch (_e) {
      return d.toLocaleString("en-GB", { hour12: false });
    }
  }

  function formatTime(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  /* --- API client ---------------------------------------------------------- */

  // Discord snowflake IDs (17-19 digits) exceed Number.MAX_SAFE_INTEGER, so
  // JSON.parse silently corrupts them. Keep their exact digits as strings on
  // read, and emit raw digit literals on write (server parses exact ints).
  var BIG_INT_KEYS = ["CHANNEL_ID", "ADMIN_ROLE_ID"];

  function protectBigInts(text) {
    return BIG_INT_KEYS.reduce(function (acc, key) {
      return acc.replace(new RegExp('("' + key + '"\\s*:\\s*)(-?\\d+)', "g"), '$1"$2"');
    }, text);
  }

  function parseJson(text) {
    return JSON.parse(protectBigInts(text));
  }

  function stringifyPayload(payload) {
    return (
      "{" +
      Object.keys(payload)
        .map(function (key) {
          var value = payload[key];
          if (typeof value === "string" && BIG_INT_KEYS.indexOf(key) !== -1) {
            return '"' + key + '": ' + value; // exact digits, not a float
          }
          return '"' + key + '": ' + JSON.stringify(value);
        })
        .join(", ") +
      "}"
    );
  }

  function extractError(body, status) {
    // FastAPI 422: {"detail": "..."} (HTTPException) or a validation-errors array.
    if (body && typeof body === "object") {
      if (typeof body.detail === "string") return body.detail;
      if (body.detail && Array.isArray(body.detail) && body.detail.length) {
        var first = body.detail[0];
        if (first && typeof first.msg === "string") return first.msg;
      }
      if (typeof body.error === "string") return body.error;
      if (typeof body.message === "string") return body.message;
    }
    return "Request failed (HTTP " + status + ")";
  }

  function request(method, url, payload) {
    var opts = { method: method, headers: { Accept: "application/json" } };
    if (payload !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = stringifyPayload(payload);
    }
    return fetch(url, opts).then(function (res) {
      return res
        .text()
        .then(function (text) {
          return text ? parseJson(text) : null;
        })
        .catch(function () {
          return null;
        })
        .then(function (body) {
          if (!res.ok) {
            var err = new Error(extractError(body, res.status));
            err.status = res.status;
            err.body = body;
            throw err;
          }
          return body;
        });
    });
  }

  var api = {
    status: function () {
      return request("GET", "/api/status");
    },
    settings: function () {
      return request("GET", "/api/settings");
    },
    saveSettings: function (payload) {
      return request("PUT", "/api/settings", payload);
    },
    action: function (kind) {
      return request("POST", "/api/actions/" + kind);
    },
    logs: function () {
      return request("GET", "/api/logs?n=" + LOG_LIMIT);
    },
  };

  /* --- toasts ---------------------------------------------------------------- */

  function showToast(title, message, variant) {
    var toast = document.createElement("div");
    toast.className = "toast toast--" + variant;

    var marker = document.createElement("span");
    marker.className = "toast__marker";
    marker.setAttribute("aria-hidden", "true");

    var body = document.createElement("div");
    body.className = "toast__body";
    var titleEl = document.createElement("p");
    titleEl.className = "toast__title";
    titleEl.textContent = title;
    var msgEl = document.createElement("p");
    msgEl.className = "toast__msg";
    msgEl.textContent = message || "";
    body.appendChild(titleEl);
    body.appendChild(msgEl);

    toast.appendChild(marker);
    toast.appendChild(body);

    if (variant === "error") {
      // Errors get announced assertively, not via the container's polite region.
      els.toastContainer.setAttribute("aria-live", "assertive");
    }
    // Newest on top per DESIGN.md §5 Toast (container is bottom-anchored).
    els.toastContainer.insertBefore(toast, els.toastContainer.firstChild);
    els.toastContainer.setAttribute("aria-live", "polite");

    window.setTimeout(function () {
      toast.classList.add("is-dismissing");
      window.setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, TOAST_OUT_MS);
    }, TOAST_DISMISS_MS);
  }

  /* --- status card ------------------------------------------------------------ */

  function renderStatus(data) {
    setText(els.headerSnapshot, data.snapshot_date || "No snapshot yet");
    setText(els.headerSource, data.snapshot_source || "—");

    els.statusValues.forEach(function (el) {
      var key = el.getAttribute("data-status-value");
      var value = "—";
      switch (key) {
        case "villages":
        case "players":
        case "alliances":
        case "total_population":
          value = data[key] == null ? "—" : Number(data[key]).toLocaleString("en-US");
          break;
        case "next_fetch":
          value = formatClock(data.next_fetch, data.fetch_tz);
          break;
        case "next_report":
          value = formatClock(data.next_report, data.report_tz);
          break;
        case "fetch_tz":
          value = data.fetch_tz || "—";
          break;
        case "report_tz":
          value = data.report_tz || "—";
          break;
        case "snapshot_date":
          value = data.snapshot_date || "—";
          break;
        case "snapshot_source":
          value = data.snapshot_source || "—";
          break;
        default:
          value = data[key] !== undefined && data[key] !== null ? String(data[key]) : "—";
      }
      if (el.textContent !== value) {
        setText(el, value);
        el.classList.remove("refreshing");
        void el.offsetWidth; // restart the flash animation
        el.classList.add("refreshing");
      }
    });

    var alertBox = els.statusAlert;
    var errors = data.errors || [];
    if (errors.length) {
      alertBox.textContent = "";
      var head = document.createElement("span");
      head.className = "status-alert__head";
      head.textContent = errors.length + " error" + (errors.length === 1 ? "" : "s") + " in job log";
      alertBox.appendChild(head);
      var lines = document.createElement("span");
      lines.className = "mono";
      lines.textContent = errors
        .map(function (e) {
          return formatTime(e.ts) + " \u00b7 " + (e.message || "");
        })
        .join("\n");
      alertBox.appendChild(lines);
      alertBox.classList.remove("is-hidden");
    } else {
      alertBox.classList.add("is-hidden");
    }

    els.metricGrid.setAttribute("aria-busy", "false");
  }

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
      out[key] = el.value;
    });
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

    return { errors: errors, tags: unique };
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

  function payloadFromValues(values, tags) {
    return {
      ALLIANCE_TAGS: tags,
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

    // Clear stale validation state after a successful reload.
    SETTINGS_KEYS.forEach(function (key) {
      setFieldError(key, "");
    });
    setFeedback("", "");
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

    var payload = payloadFromValues(values, checked.tags);
    var saved = false;
    setBusy(els.saveButton, true, "Saving…");

    api
      .saveSettings(payload)
      .then(function () {
        saved = true;
        setFeedback("Settings saved.", "success");
        showToast("Settings saved", "The bot will pick up the new schedule on the next run.", "success");
        return Promise.all([api.settings(), api.status()]);
      })
      .then(function (results) {
        renderSettingsForm(results[0]);
        renderStatus(results[1]);
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

  function setBusy(button, busy, label) {
    button.disabled = busy;
    button.classList.toggle("is-loading", busy);
    button.setAttribute("aria-busy", String(busy));
    var labelEl = button.querySelector(".button-label");
    if (labelEl && label) setText(labelEl, label);
    if (!busy && labelEl) {
      var original = button.getAttribute("data-label");
      if (original) setText(labelEl, original);
    }
  }

  /* --- actions ---------------------------------------------------------------- */

  function runAction(kind) {
    if (actionInFlight) return;
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
        setText($("#action-feedback span:last-child"), "Result: " + message);
        els.actionFeedback.classList.add("is-success");
      })
      .catch(function (err) {
        var message = err.status === 409 || err.status === 504 ? err.message : "Action failed: " + err.message;
        var title = err.status === 409 ? "Action skipped" : err.status === 504 ? "Action timed out" : "Action failed";
        showToast(title, message, "error");
        setText($("#action-feedback span:last-child"), message);
        els.actionFeedback.classList.add("is-error");
      })
      .then(function () {
        // A fetch may create a snapshot; a report may fail on missing data —
        // refresh status and logs either way so the console reflects reality.
        return Promise.all([api.status(), api.logs()]).catch(function () {
          return [null, null];
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
    var now = new Date();
    setText(els.logUpdated, "updated " + pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds()));
    els.logFooter.classList.add("is-live");
    list.setAttribute("aria-busy", "false");
  }

  function loadLogs() {
    return api.logs().then(renderLogs).catch(function (err) {
      els.jobLog.setAttribute("aria-busy", "false");
      showToast("Job log refresh failed", err.message, "error");
    });
  }

  /* --- init --------------------------------------------------------------------- */

  function loadStatus() {
    return api.status().then(renderStatus).catch(function (err) {
      els.metricGrid.setAttribute("aria-busy", "false");
      showToast("Status unavailable", err.message, "error");
    });
  }

  function loadSettings() {
    return api.settings().then(renderSettingsForm).catch(function (err) {
      showToast("Settings unavailable", err.message, "error");
    });
  }

  function wireForm() {
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

  document.addEventListener("DOMContentLoaded", function () {
    wireForm();
    loadStatus();
    loadSettings();
    loadLogs();
    window.setInterval(loadLogs, LOG_REFRESH_MS);

    els.fetchButton.addEventListener("click", function () {
      runAction("fetch");
    });
    els.reportButton.addEventListener("click", function () {
      runAction("report");
    });
  });
})();
