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
    "ALLIANCE_TAGS", "TRACKED_ALLIANCES", "CHANNEL_ID", "ADMIN_ROLE_ID",
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
    var token = localStorage.getItem(TOKEN_KEY);
    if (token) opts.headers["Authorization"] = "Bearer " + token;
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
            if (res.status === 401) showTokenDialog();
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

  /* --- dashboard access token (LAN mode) ----------------------------------- */

  var TOKEN_KEY = "dashboard_token";
  var tokenDialogShown = false;

  // OAuth callback lands on /?session=<token> — adopt it into localStorage
  // and drop the parameter (the UI then authenticates like any token).
  var urlParams = new URLSearchParams(window.location.search);
  var urlSession = urlParams.get("session");
  if (urlSession) {
    localStorage.setItem(TOKEN_KEY, urlSession);
  }
  if (urlSession || urlParams.get("auth_error")) {
    history.replaceState({}, "", window.location.pathname);
  }

  var authState = { method: "token", user: null };

  function renderUserChip() {
    var chip = document.getElementById("user-chip");
    if (!chip) return;
    chip.textContent = "";
    if (authState.method !== "oauth" || !authState.user) {
      chip.hidden = true;
      return;
    }
    chip.hidden = false;

    var name = document.createElement("span");
    name.className = "user-chip__name";
    name.textContent = authState.user.name;

    var role = document.createElement("span");
    role.className = "user-chip__role";
    role.textContent = authState.user.admin ? "admin" : "member";

    var logout = document.createElement("button");
    logout.type = "button";
    logout.className = "user-chip__logout";
    logout.textContent = "Log out";
    logout.addEventListener("click", function () {
      request("POST", "/api/auth/logout")
        .catch(function () {}) // 401 on an already-dead session is fine
        .then(function () {
          localStorage.removeItem(TOKEN_KEY);
          window.location.reload();
        });
    });

    chip.appendChild(name);
    chip.appendChild(role);
    chip.appendChild(logout);
  }

  function loadAuthStatus() {
    return fetch("/api/auth/status", { headers: { Accept: "application/json" } })
      .then(function (res) {
        return res.status === 200 ? res.json() : null;
      })
      .catch(function () {
        return null;
      })
      .then(function (data) {
        if (!data) return;
        authState.method = data.method;
        authState.user = data.user || null;
        renderUserChip();
        if (authState.user && !authState.user.admin) {
          document.body.classList.add("is-member");
        }
      });
  }

  function ensureTokenDialog() {
    var existing = document.getElementById("token-dialog");
    if (existing) return existing;
    var dialog = document.createElement("dialog");
    dialog.className = "token-dialog";
    dialog.id = "token-dialog";
    dialog.setAttribute("aria-labelledby", "token-dialog-title");
    dialog.setAttribute("aria-describedby", "token-dialog-copy");
    dialog.innerHTML =
      '<form class="token-dialog__form" id="token-form" novalidate>' +
      '<p class="overline" id="token-dialog-title">Access token required</p>' +
      '<p class="token-dialog__copy" id="token-dialog-copy">This dashboard is protected. Enter the access token (DASHBOARD_TOKEN on the server).</p>' +
      '<input type="password" id="token-input" autocomplete="off" spellcheck="false" placeholder="Dashboard access token" aria-describedby="token-error">' +
      '<p class="token-dialog__error" id="token-error" role="alert"></p>' +
      '<button class="button button--primary" type="submit">Unlock</button>' +
      "</form>";
    document.body.appendChild(dialog);
    dialog.querySelector("#token-form").addEventListener("submit", function (event) {
      event.preventDefault();
      var value = document.getElementById("token-input").value.trim();
      var error = document.getElementById("token-error");
      if (!value) {
        error.textContent = "Token is required.";
        return;
      }
      error.textContent = "";
      localStorage.setItem(TOKEN_KEY, value);
      window.location.reload(); // static UI is public — the reload then authenticates every API call
    });
    // The token is required to use the dashboard; Esc must not dismiss the dialog.
    dialog.addEventListener("cancel", function (event) {
      event.preventDefault();
    });
    return dialog;
  }

  function showTokenDialog() {
    if (tokenDialogShown) return;
    tokenDialogShown = true;
    var dialog = ensureTokenDialog();
    // Auth-aware content: oauth mode offers the Discord login (the token
    // field is meaningless there); token mode keeps the classic form.
    fetch("/api/auth/status", { headers: { Accept: "application/json" } })
      .then(function (res) {
        return res.status === 200 ? res.json() : null;
      })
      .catch(function () {
        return null;
      })
      .then(function (data) {
        var form = dialog.querySelector("#token-form");
        if (!form) return;
        if (data && data.method === "oauth") {
          form.innerHTML =
            '<p class="overline" id="token-dialog-title">Sign in required</p>' +
            '<p class="token-dialog__copy" id="token-dialog-copy">This dashboard is protected. Sign in with your Discord account.</p>' +
            '<a class="button button--primary" href="/api/auth/login">Sign in with Discord</a>';
          return;
        }
        var input = document.getElementById("token-input");
        if (input) input.focus();
      });
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      // Fallback for browsers without <dialog> support: inline overlay.
      dialog.setAttribute("open", "");
      dialog.classList.add("token-dialog--inline");
    }
  }

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

    allianceTags = data.alliance_tags || [];
    renderAllianceFilter();

    els.metricGrid.setAttribute("aria-busy", "false");
  }

  /* --- alliance filter (analysis) ---------------------------------------------- */

  function renderAllianceFilter() {
    var filter = analysisElements().allianceFilter;
    if (!filter || filter.dataset.rendered) return; // options are stable per process
    filter.dataset.rendered = "1";
    if (allianceTags.length < 2) {
      filter.hidden = true;
      return;
    }
    filter.hidden = false;
    ["combined"].concat(allianceTags).forEach(function (tag) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "segmented__btn";
      btn.setAttribute("data-alliance", tag);
      btn.setAttribute("aria-pressed", String(tag === analysisState.alliance));
      btn.textContent = tag === "combined" ? "Combined" : tag;
      filter.appendChild(btn);
    });
  }

  function wireAllianceSwitch() {
    var filter = analysisElements().allianceFilter;
    if (!filter) return;
    filter.addEventListener("click", function (event) {
      var btn = event.target && event.target.closest ? event.target.closest(".segmented__btn") : null;
      if (!btn || !filter.contains(btn)) return;
      var tag = btn.getAttribute("data-alliance");
      if (tag === analysisState.alliance) return;
      analysisState.alliance = tag;
      Array.prototype.forEach.call(filter.querySelectorAll(".segmented__btn"), function (b) {
        b.setAttribute("aria-pressed", String(b === btn));
      });
      // Active tabs refetch with the new filter; inactive ones reset so the
      // next activation loads with it (their loaders preserve the selection
      // across refetches — regions keeps the region, events keeps from/to).
      ["regions", "events", "changes"].forEach(function (name) {
        if (!activatedTabs[name]) return;
        var panel = document.getElementById("panel-" + name);
        if (panel && panel.getAttribute("aria-busy") === "true") return;
        tabLoaders[name]();
      });
    });
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

    var payload = payloadFromValues(values, checked.tags, checked.trackedTags);
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

  /* --- analysis (report trim) ---------------------------------------------- */

  var ANALYSIS_DAYS = 30;
  var SERIES_COLORS = ["#1abc9c", "#e67e22", "#3498db", "#f1c40f"];
  var analysisState = {
    charts: {},
    metric: "population",
    alliance: "combined",
    region: null,
    from: null,
    to: null,
    regionsDates: [],
    regionsSeries: {},
    standingsPayload: null,
  };
  var allianceTags = [];
  var activatedTabs = {};
  var activeTabName = "regions";
  var analysisEls = null;

  function cssVar(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function fmtInt(n) {
    return Number(n).toLocaleString("en-US");
  }

  function analysisElements() {
    if (!analysisEls) {
      analysisEls = {
        range: document.querySelector("[data-analysis-range]"),
        allianceFilter: document.getElementById("analysis-alliance-filter"),
        tabs: Array.prototype.slice.call(document.querySelectorAll(".tab-bar__tab")),
        panels: Array.prototype.slice.call(document.querySelectorAll(".analysis-panel")),
        regionsBody: document.querySelector("[data-regions-body]"),
        regionSelect: document.getElementById("analysis-region-select"),
        regionCanvas: document.getElementById("analysis-chart-regions"),
        regionTop: document.getElementById("region-top"),
        regionTopTitle: document.getElementById("region-top-title"),
        regionTopList: document.getElementById("region-top-list"),
        standingsCanvas: document.getElementById("analysis-chart-standings"),
        metricButtons: Array.prototype.slice.call(document.querySelectorAll(".segmented__btn")),
        playersPopulation: document.querySelector("[data-players-population]"),
        playersGrowth: document.querySelector("[data-players-growth]"),
        playersNew: document.querySelector("[data-players-new]"),
        eventsFrom: document.getElementById("analysis-events-from"),
        eventsTo: document.getElementById("analysis-events-to"),
        eventsError: document.querySelector(".analysis-controls__error"),
        eventsGrid: document.querySelector(".events-grid"),
        eventsEmpty: document.querySelector("[data-events-empty]"),
        gainedList: document.querySelector("[data-events-gained]"),
        lostList: document.querySelector("[data-events-lost]"),
        gainedCount: document.querySelector("[data-events-gained-count]"),
        lostCount: document.querySelector("[data-events-lost-count]"),
        changesBody: document.querySelector("[data-changes-body]"),
      };
    }
    return analysisEls;
  }

  function setPanelBusy(name, busy) {
    var panel = document.getElementById("panel-" + name);
    if (panel) panel.setAttribute("aria-busy", String(busy));
  }

  function showPanelEmpty(panel, message, alert) {
    var state = panel.querySelector(".empty-state");
    if (!state) {
      state = document.createElement("p");
      state.className = "empty-state";
      panel.appendChild(state);
    }
    state.textContent = message;
    state.hidden = false; // the events panel's dedicated node starts hidden
    if (alert) state.setAttribute("role", "alert");
    panel.classList.add("is-empty");
  }

  function hidePanelEmpty(panel) {
    panel.classList.remove("is-empty");
    var state = panel.querySelector(".empty-state");
    // The events panel's dedicated node ([data-events-empty]) is owned by
    // renderEvents; generated states are removed so recovery never leaves a
    // stale message behind.
    if (state && !state.hasAttribute("data-events-empty")) {
      if (state.parentNode) state.parentNode.removeChild(state);
    }
  }

  function showChartUnavailable(card) {
    var state = card.querySelector(".empty-state");
    if (!state) {
      state = document.createElement("p");
      state.className = "empty-state";
      card.appendChild(state);
    }
    state.textContent = "Chart library unavailable.";
    card.classList.add("is-empty");
  }

  function showChartLoading(card) {
    var state = card.querySelector(".empty-state");
    if (!state) {
      state = document.createElement("p");
      state.className = "empty-state";
      card.appendChild(state);
    }
    state.textContent = "Loading…";
    card.classList.add("is-empty");
  }

  function applyChartDefaults() {
    if (!window.Chart) return;
    var bodyFont = getComputedStyle(document.body).fontFamily || "system-ui";
    Chart.defaults.font.family = bodyFont;
    Chart.defaults.color = "#96988c"; // --text-muted
  }

  var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function baseChartOpts() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      animation: reducedMotion ? false : { duration: 300 },
      scales: {
        x: {
          ticks: { color: "#96988c", maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
          grid: { display: false },
          border: { display: false },
        },
        y: {
          beginAtZero: true,
          grid: { color: "rgba(42,51,40,0.55)" },
          border: { display: false },
          ticks: { color: "#96988c", maxTicksLimit: 6 },
        },
      },
      plugins: {
        legend: {
          labels: {
            color: "#c5c2b5",
            boxWidth: 10,
            boxHeight: 10,
            usePointStyle: true,
            pointStyle: "line",
            padding: 16,
            font: { size: 11, weight: "600" },
          },
        },
        tooltip: {
          backgroundColor: "#151b15",
          borderColor: "#2a3328",
          borderWidth: 1,
          titleColor: "#f1eee1",
          bodyColor: "#c5c2b5",
          displayColors: false,
          padding: 10,
        },
      },
    };
  }

  // Kinds that honor the alliance filter (standings is a cross-alliance
  // comparison and never filters).
  var ALLIANCE_FILTERED_KINDS = ["regions", "events", "deltas", "players"];

  api.analysis = function (kind, params) {
    var parts = [];
    if (params) {
      parts = Object.keys(params).map(function (key) {
        return [key, params[key]];
      });
    }
    if (ALLIANCE_FILTERED_KINDS.indexOf(kind) !== -1 && analysisState.alliance !== "combined") {
      parts.push(["alliance", analysisState.alliance]);
    }
    var qs = parts.length
      ? "?" +
        parts
          .map(function (pair) {
            return encodeURIComponent(pair[0]) + "=" + encodeURIComponent(pair[1]);
          })
          .join("&")
      : "";
    return request("GET", "/api/analysis/" + kind + qs);
  };

  function tableLoading(tbody, colspan) {
    tbody.textContent = "";
    var tr = document.createElement("tr");
    var td = document.createElement("td");
    td.colSpan = colspan;
    td.className = "table-loading";
    td.textContent = "Loading…";
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  /* Regions tab */

  function loadRegions() {
    var panel = document.getElementById("panel-regions");
    var els = analysisElements();
    setPanelBusy("regions", true);
    tableLoading(els.regionsBody, 6);
    return api
      .analysis("regions", { days: ANALYSIS_DAYS })
      .then(function (payload) {
        renderRegions(payload);
        setPanelBusy("regions", false);
      })
      .catch(function (err) {
        setPanelBusy("regions", false);
        showPanelEmpty(panel, "Couldn't load analysis data.", true);
        activatedTabs.regions = false; // next activation retries
      });
  }

  function renderRegions(payload) {
    var panel = document.getElementById("panel-regions");
    var els = analysisElements();
    var current = payload.current || [];
    var dates = payload.dates || [];
    var series = payload.series || {};
    analysisState.regionsPayload = payload;
    setExportEnabled("regions", current.length > 0);
    if (els.regionTop) els.regionTop.hidden = true;

    if (!current.length || !dates.length) {
      showPanelEmpty(panel, "No data yet.");
      els.range.hidden = true;
      return;
    }
    hidePanelEmpty(panel);
    els.range.hidden = false;

    els.regionsBody.textContent = "";
    current.forEach(function (row) {
      els.regionsBody.appendChild(regionRow(row));
    });

    // Chartable regions = series keys; default = highest current share.
    var shareOf = {};
    current.forEach(function (row) {
      shareOf[row.region] = row.share;
    });
    var chartable = Object.keys(series);
    var ordered = chartable.slice().sort(function (a, b) {
      return (shareOf[b] || 0) - (shareOf[a] || 0) || (a < b ? -1 : a > b ? 1 : 0);
    });
    var region = analysisState.region && ordered.indexOf(analysisState.region) !== -1 ? analysisState.region : ordered[0];
    analysisState.region = region;
    analysisState.regionsDates = dates;
    analysisState.regionsSeries = series;

    els.regionSelect.textContent = "";
    ordered.forEach(function (name) {
      var opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      els.regionSelect.appendChild(opt);
    });
    if (region) els.regionSelect.value = region;

    if (!ordered.length) {
      showChartUnavailable(panel.querySelector(".chart-card"));
      return;
    }
    renderRegionChart(region);
  }

  function regionRow(row) {
    var tr = document.createElement("tr");
    if (!row.active) tr.classList.add("is-inactive");

    var tdRegion = document.createElement("td");
    tdRegion.className = "region-name";
    tdRegion.textContent = row.region;

    var tdControl = document.createElement("td");
    var fills = Math.min(6, Math.round(row.share * 6));
    var bar = document.createElement("span");
    bar.className = "control-bar";
    bar.textContent = new Array(fills + 1).join("\u2593") + new Array(6 - fills + 1).join("\u2591");
    tdControl.appendChild(bar);

    var tdShare = document.createElement("td");
    tdShare.className = "num";
    tdShare.textContent = (row.share * 100).toFixed(1) + "%";

    var tdPop = document.createElement("td");
    tdPop.className = "num";
    tdPop.textContent = fmtInt(row.our_pop);

    var tdDelta = document.createElement("td");
    tdDelta.className = "num";
    var d = row.share_delta;
    if (d === null || d === undefined) {
      tdDelta.textContent = "\u2014";
      tdDelta.classList.add("faint");
    } else if (Math.abs(d) < 0.0005) {
      tdDelta.textContent = "\u00b10.0%";
      tdDelta.classList.add("faint");
    } else if (d > 0) {
      tdDelta.textContent = "+" + (d * 100).toFixed(1) + "%";
      tdDelta.classList.add("is-positive");
    } else {
      tdDelta.textContent = "\u2212" + Math.abs(d * 100).toFixed(1) + "%";
      tdDelta.classList.add("is-negative");
    }

    var tdTo50 = document.createElement("td");
    tdTo50.className = "num";
    if (row.controlled) {
      tdTo50.textContent = "\u2713";
      tdTo50.classList.add("is-positive");
    } else if (!row.active) {
      tdTo50.textContent = "\u2014";
      tdTo50.classList.add("faint");
    } else {
      tdTo50.textContent = "+" + fmtInt(row.to50_needed);
    }

    tr.appendChild(tdRegion);
    tr.appendChild(tdControl);
    tr.appendChild(tdShare);
    tr.appendChild(tdPop);
    tr.appendChild(tdDelta);
    tr.appendChild(tdTo50);
    return tr;
  }

  function renderRegionTop(region) {
    var els = analysisElements();
    var byRegion = (analysisState.regionsPayload && analysisState.regionsPayload.top_alliances) || {};
    var entries = byRegion[region] || [];
    if (!entries.length) {
      els.regionTop.hidden = true;
      return;
    }
    els.regionTop.hidden = false;
    setText(els.regionTopTitle, "Top alliances in " + region);
    els.regionTopList.textContent = "";
    entries.forEach(function (entry) {
      var li = document.createElement("li");
      var tag = document.createElement("span");
      tag.className = "region-top__tag";
      tag.textContent = entry.tag || "(none)";
      var pop = document.createElement("span");
      pop.className = "region-top__pop";
      pop.textContent = fmtInt(entry.population);
      li.appendChild(tag);
      li.appendChild(pop);
      els.regionTopList.appendChild(li);
    });
  }

  function renderRegionChart(region) {
    var panel = document.getElementById("panel-regions");
    var els = analysisElements();
    if (!window.Chart) {
      showChartUnavailable(panel.querySelector(".chart-card"));
      return;
    }
    var card = panel.querySelector(".chart-card");
    card.classList.remove("is-empty");
    var stale = card.querySelector(".empty-state");
    if (stale) stale.remove();
    var dates = analysisState.regionsDates;
    var points = analysisState.regionsSeries[region] || [];
    var byDate = {};
    points.forEach(function (p) {
      byDate[p.date] = p;
    });
    var data = [];
    var rows = [];
    dates.forEach(function (d) {
      var p = byDate[d];
      data.push(p ? p.share : null);
      rows.push(p ? { our_pop: p.our_pop, total_pop: p.total_pop } : null);
    });

    els.regionCanvas.setAttribute("aria-label", "Share of population over time for " + region);
    renderRegionTop(region);

    var chart = analysisState.charts.regions;
    if (chart) {
      chart.data.labels = dates;
      chart.data.datasets[0].label = region;
      chart.data.datasets[0].data = data;
      chart.data.datasets[0]._rows = rows;
      chart.update();
      return;
    }

    chart = new Chart(els.regionCanvas.getContext("2d"), {
      type: "line",
      data: {
        labels: dates,
        datasets: [
          {
            label: region,
            data: data,
            borderColor: "#1abc9c",
            backgroundColor: "rgba(26,188,156,0.12)",
            fill: true,
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.3,
            spanGaps: false,
            _rows: rows,
          },
        ],
      },
      options: baseChartOpts(),
    });
    chart.options.plugins.legend.display = false;
    chart.options.scales.y.ticks.callback = function (value) {
      return Math.round(value * 100) + "%";
    };
    chart.options.plugins.tooltip.callbacks = {
      title: function (ctx) {
        return ctx[0].label;
      },
      label: function (ctx) {
        return ctx.dataset.label + ": " + (ctx.parsed.y * 100).toFixed(1) + "%";
      },
      afterBody: function (ctx) {
        var point = ctx[0].dataset._rows[ctx[0].dataIndex];
        return point ? ["our " + fmtInt(point.our_pop) + " \u00b7 total " + fmtInt(point.total_pop)] : [""];
      },
    };
    analysisState.charts.regions = chart;
  }

  /* Alliances tab */

  function loadStandings() {
    var panel = document.getElementById("panel-alliances");
    setPanelBusy("alliances", true);
    if (!analysisState.charts.alliances) {
      showChartLoading(panel.querySelector(".chart-card"));
    }
    return api
      .analysis("standings", { days: ANALYSIS_DAYS })
      .then(function (payload) {
        analysisState.standingsPayload = payload;
        var series = payload.series || [];
        var dates = payload.dates || [];
        if (!series.length || !dates.length) {
          showPanelEmpty(panel, "No data yet.");
        } else {
          hidePanelEmpty(panel);
          renderStandingsChart(payload);
        }
        setPanelBusy("alliances", false);
      })
      .catch(function (err) {
        setPanelBusy("alliances", false);
        showPanelEmpty(panel, "Couldn't load analysis data.", true);
        activatedTabs.alliances = false;
      });
  }

  function alignedValues(row, dates, metric) {
    var key = metric === "vp" ? "vp_points" : "points";
    var byDate = {};
    (row[key] || []).forEach(function (pair) {
      byDate[pair[0]] = pair[1];
    });
    return dates.map(function (d) {
      return byDate[d] !== undefined ? byDate[d] : null;
    });
  }

  function applyStandingsTicks(chart, metric) {
    if (metric === "vp") {
      chart.options.scales.y.ticks.callback = function (value) {
        return Number(value).toLocaleString("en-US");
      };
    } else {
      chart.options.scales.y.ticks.callback = function (value) {
        return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
      };
    }
  }

  function renderStandingsChart(payload) {
    var panel = document.getElementById("panel-alliances");
    var els = analysisElements();
    var card = panel.querySelector(".chart-card");
    card.classList.remove("is-empty");
    var stale = card.querySelector(".empty-state");
    if (stale) stale.remove();
    if (!window.Chart) {
      showPanelEmpty(panel, "Chart library unavailable.");
      return;
    }
    var dates = payload.dates || [];
    var series = payload.series || [];
    var metric = analysisState.metric;
    els.standingsCanvas.setAttribute(
      "aria-label",
      (metric === "vp" ? "Victory points" : "Population") + " over time for tracked alliances"
    );

    var chart = analysisState.charts.alliances;
    if (chart) {
      chart.data.labels = dates;
      chart.data.datasets.forEach(function (ds, i) {
        ds.data = alignedValues(series[i], dates, metric);
      });
      applyStandingsTicks(chart, metric);
      chart.update();
      return;
    }

    chart = new Chart(els.standingsCanvas.getContext("2d"), {
      type: "line",
      data: {
        labels: dates,
        datasets: series.map(function (row, i) {
          return {
            label: row.tag,
            data: alignedValues(row, dates, metric),
            borderColor: row.ours ? cssVar("--accent-gold", "#d1a84a") : SERIES_COLORS[i % SERIES_COLORS.length],
            borderWidth: row.ours ? 2.5 : 1.75,
            fill: false,
            pointRadius: 0,
            pointHoverRadius: 3,
            tension: 0.3,
            spanGaps: false,
          };
        }),
      },
      options: baseChartOpts(),
    });
    chart.options.plugins.legend.position = "bottom";
    applyStandingsTicks(chart, metric);
    analysisState.charts.alliances = chart;
  }

  /* Players tab */

  function loadPlayers() {
    var panel = document.getElementById("panel-players");
    setPanelBusy("players", true);
    return api
      .analysis("players", {})
      .then(function (payload) {
        renderPlayers(payload);
        setPanelBusy("players", false);
      })
      .catch(function (err) {
        setPanelBusy("players", false);
        showPanelEmpty(panel, "Couldn't load analysis data.", true);
        activatedTabs.players = false; // next activation retries
      });
  }

  function playersSection(tbody, rows, valueCell) {
    tbody.textContent = "";
    if (!rows.length) {
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 3;
      td.className = "faint";
      td.textContent = "No data yet.";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    rows.forEach(function (stat, index) {
      var tr = document.createElement("tr");
      var tdRank = document.createElement("td");
      tdRank.className = "num faint";
      tdRank.textContent = String(index + 1);
      var tdName = document.createElement("td");
      tdName.className = "player-name";
      tdName.textContent = stat.player_name || "unknown";
      tr.appendChild(tdRank);
      tr.appendChild(tdName);
      tr.appendChild(valueCell(stat));
      tbody.appendChild(tr);
    });
  }

  function renderPlayers(payload) {
    var els = analysisElements();
    playersSection(els.playersPopulation, payload.population || [], function (s) {
      return numCell(fmtInt(s.population));
    });
    playersSection(els.playersGrowth, payload.growth || [], function (s) {
      return deltaCell(s.growth);
    });
    playersSection(els.playersNew, payload.new_villages || [], function (s) {
      return numCell(fmtInt(s.gains));
    });
  }

  /* Events tab */

  function fillDateSelect(select, dates) {
    select.textContent = "";
    dates.forEach(function (d) {
      var opt = document.createElement("option");
      opt.value = d;
      opt.textContent = d;
      select.appendChild(opt);
    });
  }

  function setEventsBusy(busy) {
    var els = analysisElements();
    els.eventsFrom.disabled = busy;
    els.eventsTo.disabled = busy;
  }

  function loadEvents() {
    var panel = document.getElementById("panel-events");
    var els = analysisElements();
    setPanelBusy("events", true);
    setEventsBusy(true);
    return api
      .analysis("dates")
      .then(function (payload) {
        var dates = payload.dates || [];
        if (dates.length < 2) {
          showPanelEmpty(panel, "No data yet.");
          setPanelBusy("events", false);
          setEventsBusy(false);
          return;
        }
        hidePanelEmpty(panel);
        fillDateSelect(els.eventsFrom, dates);
        fillDateSelect(els.eventsTo, dates);
        if (analysisState.from && dates.indexOf(analysisState.from) !== -1) {
          els.eventsFrom.value = analysisState.from;
        } else {
          els.eventsFrom.value = dates[dates.length - 2];
        }
        if (analysisState.to && dates.indexOf(analysisState.to) !== -1) {
          els.eventsTo.value = analysisState.to;
        } else {
          els.eventsTo.value = dates[dates.length - 1];
        }
        analysisState.from = els.eventsFrom.value;
        analysisState.to = els.eventsTo.value;
        return fetchEvents(analysisState.from, analysisState.to);
      })
      .then(function () {
        setEventsBusy(false);
      })
      .catch(function (err) {
        setPanelBusy("events", false);
        setEventsBusy(false);
        showPanelEmpty(panel, "Couldn't load analysis data.", true);
        activatedTabs.events = false;
      });
  }

  function fetchEvents(from, to) {
    return api.analysis("events", { from: from, to: to }).then(function (payload) {
      renderEvents(payload, from, to);
    });
  }

  function renderEvents(payload, from, to) {
    var els = analysisElements();
    var gained = payload.gained || [];
    var lost = payload.lost || [];
    analysisState.eventsPayload = payload;
    setExportEnabled("events", gained.length + lost.length > 0);
    els.gainedList.textContent = "";
    els.lostList.textContent = "";
    setText(els.gainedCount, String(gained.length));
    setText(els.lostCount, String(lost.length));

    if (!gained.length && !lost.length) {
      els.eventsGrid.hidden = true;
      els.eventsEmpty.hidden = false;
      setText(els.eventsEmpty, "No villages changed between " + from + " and " + to + ".");
      return;
    }
    els.eventsEmpty.hidden = true;
    els.eventsGrid.hidden = false;
    gained.forEach(function (ev) {
      els.gainedList.appendChild(eventLine(ev, true));
    });
    lost.forEach(function (ev) {
      els.lostList.appendChild(eventLine(ev, false));
    });
  }

  function eventLine(ev, gained) {
    var li = document.createElement("li");
    li.className = "event-line " + (gained ? "event-line--gained" : "event-line--lost");

    var name = document.createElement("span");
    name.className = "event-line__name";
    name.textContent = ev.village_name;
    li.appendChild(name);

    var coords = document.createElement("span");
    coords.className = "event-line__coords";
    coords.textContent = "(" + ev.x + "|" + ev.y + ")";
    li.appendChild(coords);

    if (ev.region) {
      var region = document.createElement("span");
      region.className = "event-line__region";
      region.textContent = "\u2014 " + ev.region;
      li.appendChild(region);
    }

    var meta = document.createElement("span");
    meta.className = "event-line__meta";
    if (gained) {
      meta.textContent = "by " + (ev.owner_player || "unknown");
    } else if (ev.event === "lost_deleted") {
      meta.textContent = "deleted";
      meta.classList.add("is-muted");
    } else {
      meta.textContent = "conquered by " + (ev.owner_tag || ev.owner_player || "unknown");
    }
    li.appendChild(meta);
    return li;
  }

  /* Changes tab */

  function loadChanges() {
    var panel = document.getElementById("panel-changes");
    var els = analysisElements();
    setPanelBusy("changes", true);
    tableLoading(els.changesBody, 9);
    return api
      .analysis("deltas", { days: ANALYSIS_DAYS })
      .then(function (payload) {
        var rows = payload.rows || [];
        analysisState.changesPayload = payload;
        setExportEnabled("changes", rows.length > 0);
        if (!rows.length) {
          showPanelEmpty(panel, "No data yet.");
          setPanelBusy("changes", false);
          return;
        }
        hidePanelEmpty(panel);
        els.changesBody.textContent = "";
        rows.slice().reverse().forEach(function (row) {
          els.changesBody.appendChild(changeRow(row));
        });
        setPanelBusy("changes", false);
      })
      .catch(function (err) {
        setPanelBusy("changes", false);
        showPanelEmpty(panel, "Couldn't load analysis data.", true);
        activatedTabs.changes = false;
      });
  }

  function changeRow(row) {
    var tr = document.createElement("tr");
    var tdDate = document.createElement("td");
    tdDate.className = "date-cell";
    tdDate.textContent = row.date;
    tr.appendChild(tdDate);
    tr.appendChild(numCell(fmtInt(row.villages)));
    tr.appendChild(deltaCell(row.villages_delta));
    tr.appendChild(numCell(fmtInt(row.population)));
    tr.appendChild(deltaCell(row.population_delta));
    tr.appendChild(numCell(fmtInt(row.players)));
    tr.appendChild(deltaCell(row.players_delta));
    tr.appendChild(numCell(fmtInt(row.vp)));
    tr.appendChild(deltaCell(row.vp_delta));
    return tr;
  }

  function numCell(text) {
    var td = document.createElement("td");
    td.className = "num";
    td.textContent = text;
    return td;
  }

  function deltaCell(d) {
    var td = document.createElement("td");
    td.className = "num";
    if (d === null || d === undefined) {
      td.textContent = "\u2014";
      td.classList.add("faint");
    } else if (d > 0) {
      td.textContent = "+" + fmtInt(d);
      td.classList.add("is-positive");
    } else if (d < 0) {
      td.textContent = "\u2212" + fmtInt(Math.abs(d));
      td.classList.add("is-negative");
    } else {
      td.textContent = "\u00b10";
      td.classList.add("faint");
    }
    return td;
  }

  /* CSV export (client-side) */

  function setExportEnabled(kind, enabled) {
    var btn = document.querySelector('[data-export="' + kind + '"]');
    if (btn) btn.disabled = !enabled;
  }

  function exportCsv(filename, headers, rows) {
    var lines = [headers].concat(rows);
    var csv = lines
      .map(function (cells) {
        return cells
          .map(function (value) {
            var text = value === null || value === undefined ? "" : String(value);
            return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
          })
          .join(",");
      })
      .join("\r\n");
    var blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function wireExportButtons() {
    var exporters = {
      regions: function () {
        var payload = analysisState.regionsPayload;
        if (!payload || !payload.current.length) return;
        var snapshot = payload.dates && payload.dates.length ? payload.dates[payload.dates.length - 1] : "latest";
        var headers = ["Region", "Share", "Pop", "Delta %", "To 50%", "Active", "Controlled"];
        var rows = payload.current.map(function (r) {
          return [
            r.region,
            (r.share * 100).toFixed(1) + "%",
            r.our_pop,
            r.share_delta === null || r.share_delta === undefined ? "" : (r.share_delta * 100).toFixed(1) + "%",
            r.controlled ? "yes" : r.to50_needed === null || r.to50_needed === undefined ? "" : r.to50_needed,
            r.active ? "yes" : "no",
            r.controlled ? "yes" : "no",
          ];
        });
        exportCsv("regions-" + snapshot + ".csv", headers, rows);
      },
      changes: function () {
        var payload = analysisState.changesPayload;
        var rows = payload && payload.rows ? payload.rows : [];
        if (!rows.length) return;
        var snapshot = rows[rows.length - 1].date;
        var headers = ["Date", "Villages", "Villages Δ", "Population", "Population Δ", "Players", "Players Δ", "VP", "VP Δ"];
        exportCsv(
          "changes-" + snapshot + ".csv",
          headers,
          rows.map(function (r) {
            return [r.date, r.villages, r.villages_delta, r.population, r.population_delta, r.players, r.players_delta, r.vp, r.vp_delta];
          })
        );
      },
      events: function () {
        var payload = analysisState.eventsPayload;
        if (!payload || (!payload.gained.length && !payload.lost.length)) return;
        var headers = ["Event", "Village", "X", "Y", "Region", "Player"];
        var rows = [];
        (payload.gained || []).forEach(function (e) {
          rows.push(["gained", e.village_name, e.x, e.y, e.region || "", e.owner_player || ""]);
        });
        (payload.lost || []).forEach(function (e) {
          rows.push(["lost", e.village_name, e.x, e.y, e.region || "", e.owner_tag || e.owner_player || ""]);
        });
        exportCsv("events-" + analysisState.from + "-" + analysisState.to + ".csv", headers, rows);
      },
    };
    document.querySelectorAll("[data-export]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var exporter = exporters[btn.getAttribute("data-export")];
        if (exporter) exporter();
      });
    });
  }

  /* Tab bar + wiring */

  var tabLoaders = {
    regions: loadRegions,
    alliances: loadStandings,
    players: loadPlayers,
    events: loadEvents,
    changes: loadChanges,
  };

  function activateTab(tab) {
    var els = analysisElements();
    els.tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
    });
    els.panels.forEach(function (panel) {
      panel.hidden = panel.id !== tab.getAttribute("aria-controls");
    });
    var name = tab.id.replace("tab-", "");
    activeTabName = name;
    if (!activatedTabs[name]) {
      activatedTabs[name] = true;
      tabLoaders[name]();
    } else {
      var chart = analysisState.charts[name];
      if (chart) chart.resize();
    }
  }

  function wireTabs() {
    var els = analysisElements();
    els.tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () {
        activateTab(tab);
      });
      tab.addEventListener("keydown", function (event) {
        var target = null;
        if (event.key === "ArrowRight") {
          target = els.tabs[(index + 1) % els.tabs.length];
        } else if (event.key === "ArrowLeft") {
          target = els.tabs[(index - 1 + els.tabs.length) % els.tabs.length];
        } else if (event.key === "Home") {
          target = els.tabs[0];
        } else if (event.key === "End") {
          target = els.tabs[els.tabs.length - 1];
        }
        if (target) {
          event.preventDefault();
          target.focus();
          activateTab(target);
        }
      });
    });
  }

  function wireRegionSelect() {
    var els = analysisElements();
    els.regionSelect.addEventListener("change", function () {
      analysisState.region = els.regionSelect.value;
      renderRegionChart(analysisState.region);
    });
  }

  function wireMetricToggle() {
    var els = analysisElements();
    els.metricButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var metric = btn.getAttribute("data-metric");
        if (metric === analysisState.metric) return;
        analysisState.metric = metric;
        els.metricButtons.forEach(function (b) {
          b.setAttribute("aria-pressed", String(b === btn));
        });
        if (analysisState.standingsPayload) {
          renderStandingsChart(analysisState.standingsPayload);
        } else {
          loadStandings();
        }
      });
    });
  }

  function wireEventsControls() {
    var els = analysisElements();
    function onChange() {
      var from = els.eventsFrom.value;
      var to = els.eventsTo.value;
      if (from >= to) {
        setText(els.eventsError, "From must be earlier than To.");
        els.eventsError.hidden = false;
        return; // keep the previous lists
      }
      els.eventsError.hidden = true;
      analysisState.from = from;
      analysisState.to = to;
      setPanelBusy("events", true);
      setEventsBusy(true);
      fetchEvents(from, to)
        .catch(function (err) {
          showToast("Events refresh failed", err.message, "error");
        })
        .then(function () {
          setPanelBusy("events", false);
          setEventsBusy(false);
        });
    }
    els.eventsFrom.addEventListener("change", onChange);
    els.eventsTo.addEventListener("change", onChange);
  }

  function wireAnalysis() {
    applyChartDefaults();
    wireTabs();
    wireRegionSelect();
    wireMetricToggle();
    wireEventsControls();
    wireAllianceSwitch();
    wireExportButtons();
    // Regions is the default tab — its payload is fetched at init.
    activateTab(document.getElementById("tab-regions"));
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
    loadAuthStatus();
    window.setInterval(loadLogs, LOG_REFRESH_MS);
    // Live dashboard: status and the active analysis tab refresh every
    // minute; a busy panel (in-flight load) is skipped, never double-fired.
    window.setInterval(loadStatus, 60000);
    window.setInterval(function () {
      var panel = document.getElementById("panel-" + activeTabName);
      if (!panel || panel.getAttribute("aria-busy") === "true") return;
      var loader = tabLoaders[activeTabName];
      if (loader) loader();
    }, 60000);

    els.fetchButton.addEventListener("click", function () {
      runAction("fetch");
    });
    els.reportButton.addEventListener("click", function () {
      runAction("report");
    });

    wireAnalysis();
  });
})();
