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
    try {
      // Unambiguous wall-clock: log entries carry UTC ISO timestamps, so the
      // timezone must be pinned — local formatting would silently shift them.
      return (
        new Intl.DateTimeFormat("en-GB", {
          timeZone: "UTC",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        }).format(d) + " UTC"
      );
    } catch (_e) {
      return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds()) + " UTC";
    }
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
    villageHistory: function (villageId, days) {
      return request(
        "GET",
        "/api/analysis/villages/" + encodeURIComponent(villageId) + "/history?days=" + encodeURIComponent(days)
      );
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

  // Resolves to the decoded auth status (or null when the request fails);
  // the bootstrap waits for it before starting any protected request.
  function loadAuthStatus() {
    // Send the stored bearer too: in oauth mode the server resolves the
    // session user only from the Authorization header. Without it the
    // callback's freshly adopted ?session=<token> would read back as
    // user:null and the login dialog would reopen despite HTTP 200.
    var headers = { Accept: "application/json" };
    var token = localStorage.getItem(TOKEN_KEY);
    if (token) headers.Authorization = "Bearer " + token;
    return fetch("/api/auth/status", { headers: headers })
      .then(function (res) {
        return res.status === 200 ? res.json() : null;
      })
      .catch(function () {
        return null;
      })
      .then(function (data) {
        if (data) {
          authState.method = data.method;
          authState.user = data.user || null;
          renderUserChip();
          document.body.classList.toggle("is-member", !!(authState.user && !authState.user.admin));
        }
        // Auth resolution is complete — reveal whatever the session allows.
        document.body.classList.remove("auth-pending");
        return data;
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
    // Same bearer handling as loadAuthStatus — the header only ever
    // refines the response, it never downgrades the dialog choice.
    var headers = { Accept: "application/json" };
    var token = localStorage.getItem(TOKEN_KEY);
    if (token) headers.Authorization = "Bearer " + token;
    fetch("/api/auth/status", { headers: headers })
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
    // The card badge describes the ``_recent_errors`` data (recent job-log
    // errors), NOT the /healthz probe — "Watching" stays honest while the
    // alert box carries the details.
    var stateLabel = document.querySelector("[data-status-state-label]");
    var stateBadge = stateLabel ? stateLabel.closest(".card-state") : null;
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
      if (stateBadge) {
        stateBadge.classList.remove("card-state--healthy");
        stateBadge.classList.add("card-state--attention");
        setText(stateLabel, "Attention · " + errors.length + " recent error" + (errors.length === 1 ? "" : "s"));
      }
    } else {
      alertBox.classList.add("is-hidden");
      if (stateBadge) {
        stateBadge.classList.remove("card-state--attention");
        stateBadge.classList.add("card-state--healthy");
        setText(stateLabel, "Watching");
      }
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
      ["regions", "events", "changes", "players"].forEach(function (name) {
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
    eventsLimit: 200,
    regionsDates: [],
    regionsSeries: {},
    standingsPayload: null,
    standingsSelection: null, // null = first load (derive from defaults); [] = explicit empty
    standingsDefaults: [],
    villageQuerySeq: 0, // monotonically rising: stale search responses are ignored
    villageDebounce: null,
    villageHistorySeq: 0, // same guard for history responses
    villageDays: 30,
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
        // Only the analysis sub-tabs — the top-level dashboard view tabs live
        // in #dashboard-view-tabs and are wired separately.
        tabs: Array.prototype.slice.call(document.querySelectorAll("#analysis-tabs .tab-bar__tab")),
        panels: Array.prototype.slice.call(document.querySelectorAll(".analysis-panel")),
        regionsBody: document.querySelector("[data-regions-body]"),
        regionSelect: document.getElementById("analysis-region-select"),
        regionCanvas: document.getElementById("analysis-chart-regions"),
        regionTop: document.getElementById("region-top"),
        regionTopTitle: document.getElementById("region-top-title"),
        regionTopList: document.getElementById("region-top-list"),
        standingsCanvas: document.getElementById("analysis-chart-standings"),
        standingsSearch: document.getElementById("analysis-standings-search"),
        standingsOptions: document.getElementById("analysis-standings-options"),
        standingsApply: document.getElementById("analysis-standings-apply"),
        standingsReset: document.getElementById("analysis-standings-reset"),
        eventsLimit: document.getElementById("analysis-events-limit"),
        eventsExportNote: document.getElementById("analysis-events-export-note"),
        standingsFeedback: document.getElementById("analysis-standings-feedback"),
        metricButtons: Array.prototype.slice.call(document.querySelectorAll(".segmented__btn")),
        playersPopulation: document.querySelector("[data-players-population]"),
        playersVp: document.querySelector("[data-players-vp]"),
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
        villagesInput: document.getElementById("village-search-input"),
        villagesTable: document.querySelector("[data-villages-table]"),
        villagesBody: document.querySelector("[data-villages-body]"),
        villagesEmpty: document.querySelector("[data-villages-empty]"),
        villageDetail: document.getElementById("village-detail"),
        villageDetailName: document.getElementById("village-detail-name"),
        villageAbsent: document.querySelector("[data-village-absent]"),
        villageMeta: document.querySelector("[data-village-meta]"),
        villageCoords: document.querySelector("[data-village-coords]"),
        villagePlayer: document.querySelector("[data-village-player]"),
        villageAlliance: document.querySelector("[data-village-alliance]"),
        villageChartCard: document.querySelector("[data-village-chart]"),
        villageCanvas: document.getElementById("analysis-chart-village"),
        villageHistoryTable: document.querySelector("[data-village-history-table]"),
        villageHistoryBody: document.querySelector("[data-village-history-body]"),
        villageDetailNote: document.querySelector("[data-village-detail-note]"),
        villageDetailError: document.querySelector("[data-village-detail-error]"),
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

  api.standings = function (tags) {
    var parts = [["days", ANALYSIS_DAYS]];
    (tags || []).forEach(function (tag) {
      parts.push(["tag", tag]);
    });
    var qs =
      "?" +
      parts
        .map(function (pair) {
          return encodeURIComponent(pair[0]) + "=" + encodeURIComponent(pair[1]);
        })
        .join("&");
    return request("GET", "/api/analysis/standings" + qs);
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
    var selection = analysisState.standingsSelection;
    if (selection !== null && !selection.length) {
      // Explicit empty selection (or no resolvable defaults with a live
      // snapshot): nothing to chart — the picker shows the validation hint.
      setPanelBusy("alliances", false);
      showPanelEmpty(panel, "Select at least one alliance.");
      return Promise.resolve();
    }
    setPanelBusy("alliances", true);
    if (!analysisState.charts.alliances) {
      showChartLoading(panel.querySelector(".chart-card"));
    }
    return api
      .standings(selection)
      .then(function (payload) {
        var dates = payload.dates || [];
        var series = payload.series || [];
        analysisState.standingsTags = payload.available_tags || [];
        if (selection === null) {
          // First load: the request carried no tag params — adopt the first
          // eight resolved defaults and slice the fetched series client-side
          // (no extra request).
          analysisState.standingsDefaults = (payload.default_tags || []).slice(0, 8);
          analysisState.standingsSelection = analysisState.standingsDefaults.slice();
          var chosen = analysisState.standingsSelection;
          series = series.filter(function (row) {
            return chosen.indexOf(row.tag) !== -1;
          });
        }
        analysisState.standingsPayload = { dates: dates, series: series };
        renderStandingsPicker();
        if (!dates.length) {
          showPanelEmpty(panel, "No data yet.");
        } else if (!series.length) {
          showPanelEmpty(panel, "Select at least one alliance.");
        } else {
          hidePanelEmpty(panel);
          renderStandingsChart();
        }
        setPanelBusy("alliances", false);
      })
      .catch(function (err) {
        setPanelBusy("alliances", false);
        showPanelEmpty(panel, "Couldn't load analysis data.", true);
        activatedTabs.alliances = false;
      });
  }

  function renderStandingsPicker() {
    var els = analysisElements();
    if (!els.standingsOptions) return;
    var selected = analysisState.standingsSelection || [];
    var needle = els.standingsSearch.value.trim().toLowerCase();
    els.standingsOptions.textContent = "";
    (analysisState.standingsTags || []).forEach(function (tag) {
      var label = document.createElement("label");
      label.className = "standings-option";
      var box = document.createElement("input");
      box.type = "checkbox";
      box.value = tag;
      box.checked = selected.indexOf(tag) !== -1;
      label.appendChild(box);
      var name = document.createElement("span");
      name.textContent = tag;
      label.appendChild(name);
      if (needle !== "" && tag.toLowerCase().indexOf(needle) === -1) {
        label.hidden = true;
      }
      els.standingsOptions.appendChild(label);
    });
    updateStandingsFeedback();
  }

  function updateStandingsFeedback() {
    var els = analysisElements();
    var checked = els.standingsOptions.querySelectorAll('input[type="checkbox"]:checked').length;
    var message = "";
    if (checked === 0) {
      message = "Select at least one alliance.";
    } else if (checked > 8) {
      message = "Select up to 8 alliances.";
    }
    setText(els.standingsFeedback, message);
    els.standingsFeedback.classList.toggle("is-error", message !== "");
    els.standingsApply.disabled = checked === 0 || checked > 8;
  }

  function wireStandingsPicker() {
    var els = analysisElements();
    if (!els.standingsApply) return;
    els.standingsSearch.addEventListener("input", function () {
      // Local visibility filter only: selected tags stay selected and
      // nothing is written back to TRACKED_ALLIANCES.
      var needle = els.standingsSearch.value.trim().toLowerCase();
      Array.prototype.forEach.call(els.standingsOptions.children, function (label) {
        var tag = label.textContent.toLowerCase();
        label.hidden = needle !== "" && tag.indexOf(needle) === -1;
      });
    });
    els.standingsOptions.addEventListener("change", function (event) {
      if (event.target && event.target.type === "checkbox") updateStandingsFeedback();
    });
    els.standingsApply.addEventListener("click", function () {
      analysisState.standingsSelection = Array.prototype.slice
        .call(els.standingsOptions.querySelectorAll('input[type="checkbox"]:checked'))
        .map(function (box) {
          return box.value;
        });
      loadStandings();
    });
    els.standingsReset.addEventListener("click", function () {
      analysisState.standingsSelection = analysisState.standingsDefaults.slice();
      loadStandings();
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

  function standingsDatasets(series, dates, metric) {
    // Single builder for both creation and updates: selection changes the
    // number of series, labels and colors — datasets are replaced whole.
    return series.map(function (row, i) {
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
    });
  }

  function renderStandingsChart() {
    var payload = analysisState.standingsPayload;
    if (!payload) return;
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
      (metric === "vp" ? "Victory points" : "Population") + " over time for selected alliances"
    );

    var chart = analysisState.charts.alliances;
    if (chart) {
      chart.data.labels = dates;
      chart.data.datasets = standingsDatasets(series, dates, metric);
      applyStandingsTicks(chart, metric);
      chart.update();
      return;
    }

    chart = new Chart(els.standingsCanvas.getContext("2d"), {
      type: "line",
      data: {
        labels: dates,
        datasets: standingsDatasets(series, dates, metric),
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
    playersSection(els.playersVp, payload.vp || [], function (s) {
      return numCell(fmtInt(s.vp));
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
        setPanelBusy("events", false);
      })
      .catch(function (err) {
        setPanelBusy("events", false);
        setEventsBusy(false);
        showPanelEmpty(panel, "Couldn't load analysis data.", true);
        activatedTabs.events = false;
      });
  }

  function fetchEvents(from, to) {
    return api
      .analysis("events", { from: from, to: to, limit: analysisState.eventsLimit })
      .then(function (payload) {
        renderEvents(payload, from, to);
      });
  }

  function renderEvents(payload, from, to) {
    var els = analysisElements();
    var gained = payload.gained || [];
    var lost = payload.lost || [];
    var gainedTotal = payload.gained_total !== undefined ? payload.gained_total : gained.length;
    var lostTotal = payload.lost_total !== undefined ? payload.lost_total : lost.length;
    var truncated = gained.length < gainedTotal || lost.length < lostTotal;
    analysisState.eventsPayload = payload;
    setExportEnabled("events", gained.length + lost.length > 0);
    els.gainedList.textContent = "";
    els.lostList.textContent = "";
    setText(els.gainedCount, truncated && gained.length < gainedTotal ? gained.length + " / " + gainedTotal : String(gained.length));
    setText(els.lostCount, truncated && lost.length < lostTotal ? lost.length + " / " + lostTotal : String(lost.length));
    if (els.eventsExportNote) els.eventsExportNote.hidden = !truncated;

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

    // The name is a semantic button: clicking opens the village's history in
    // the Villages tab (no fetch/report trigger, no alliance-filter change).
    var name = document.createElement("button");
    name.type = "button";
    name.className = "event-line__name";
    name.textContent = ev.village_name;
    name.addEventListener("click", function () {
      openVillageFromEvent(ev);
    });
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

  /* Villages tab (explorer + owner history) */

  function setVillagesPrompt(message) {
    var els = analysisElements();
    els.villagesTable.hidden = true;
    els.villageDetail.hidden = true;
    els.villagesEmpty.hidden = false;
    setText(els.villagesEmpty, message);
  }

  // The initial tab activation renders only the prompt — a request is issued
  // solely for actual query text (no full-map scan on tab open).
  function loadVillages() {
    var els = analysisElements();
    var query = (els.villagesInput.value || "").trim();
    if (!query) {
      setPanelBusy("villages", true);
      setVillagesPrompt("Search by village, player or coordinates.");
      setPanelBusy("villages", false);
      return;
    }
    return requestVillages(query);
  }

  // One search request per query; the rising sequence makes late responses
  // (and superseded inputs) silently ignored.
  function requestVillages(query) {
    var seq = ++analysisState.villageQuerySeq;
    setPanelBusy("villages", true);
    return api
      .analysis("villages", { q: query, limit: 50 })
      .then(function (payload) {
        if (seq !== analysisState.villageQuerySeq) return;
        renderVillages(payload);
        setPanelBusy("villages", false);
      })
      .catch(function (err) {
        if (seq !== analysisState.villageQuerySeq) return;
        setPanelBusy("villages", false);
        showToast("Village search failed", err.message, "error");
      });
  }

  function wireVillagesSearch() {
    var els = analysisElements();
    els.villagesInput.addEventListener("input", function () {
      window.clearTimeout(analysisState.villageDebounce);
      analysisState.villageDebounce = window.setTimeout(function () {
        var query = (els.villagesInput.value || "").trim();
        if (!query) {
          analysisState.villageQuerySeq += 1; // invalidate any in-flight search
          setVillagesPrompt("Search by village, player or coordinates.");
          setPanelBusy("villages", false);
          return;
        }
        requestVillages(query);
      }, 250);
    });
  }

  function renderVillages(payload) {
    var els = analysisElements();
    var results = payload.results || [];
    if (!results.length) {
      setVillagesPrompt("No villages found.");
      return;
    }
    els.villagesEmpty.hidden = true;
    els.villageDetail.hidden = true;
    els.villagesTable.hidden = false;
    els.villagesBody.textContent = "";
    results.forEach(function (row) {
      els.villagesBody.appendChild(villageResultRow(row));
    });
  }

  function villageResultRow(row) {
    var tr = document.createElement("tr");

    var tdName = document.createElement("td");
    tdName.className = "village-name";
    var open = document.createElement("button");
    open.type = "button";
    open.className = "village-open";
    open.textContent = row.name;
    open.setAttribute("aria-label", "Open history for " + row.name);
    open.addEventListener("click", function () {
      openVillageHistory(row.village_id, row.name);
    });
    tdName.appendChild(open);

    var tdCoords = document.createElement("td");
    tdCoords.className = "num";
    tdCoords.textContent = row.x + "|" + row.y;

    var tdPop = document.createElement("td");
    tdPop.className = "num";
    tdPop.textContent = fmtInt(row.population);

    var tdPlayer = document.createElement("td");
    tdPlayer.className = "player-name";
    tdPlayer.textContent = row.player_name || "—";

    var tdAlliance = document.createElement("td");
    tdAlliance.textContent = row.alliance_tag || "—";

    var tdFlags = document.createElement("td");
    tdFlags.className = "village-flags";
    [["Capital", row.is_capital], ["City", row.is_city], ["Harbor", row.is_harbor]].forEach(function (pair) {
      if (!pair[1]) return;
      var chip = document.createElement("span");
      chip.className = "village-flag";
      chip.textContent = pair[0];
      tdFlags.appendChild(chip);
    });
    if (!tdFlags.children.length) {
      tdFlags.textContent = "—";
      tdFlags.classList.add("faint");
    }

    tr.appendChild(tdName);
    tr.appendChild(tdCoords);
    tr.appendChild(tdPop);
    tr.appendChild(tdPlayer);
    tr.appendChild(tdAlliance);
    tr.appendChild(tdFlags);
    return tr;
  }

  function openVillageFromEvent(ev) {
    var tab = document.getElementById("tab-villages");
    if (tab) {
      activateTab(tab);
    }
    openVillageHistory(ev.village_id, ev.village_name);
  }

  function openVillageHistory(villageId, villageName) {
    var els = analysisElements();
    var seq = ++analysisState.villageHistorySeq;
    els.villageDetail.hidden = false;
    els.villageDetailError.hidden = true;
    els.villageAbsent.hidden = true;
    els.villageMeta.hidden = true;
    els.villageChartCard.hidden = true;
    els.villageHistoryTable.hidden = true;
    els.villageDetailNote.hidden = false;
    setText(els.villageDetailNote, "Loading…");
    setText(els.villageDetailName, villageName || "Village " + villageId);
    api
      .villageHistory(villageId, analysisState.villageDays)
      .then(function (payload) {
        if (seq !== analysisState.villageHistorySeq) return;
        renderVillageHistory(payload);
      })
      .catch(function (err) {
        if (seq !== analysisState.villageHistorySeq) return;
        els.villageDetailNote.hidden = true;
        els.villageDetailError.hidden = false;
        setText(
          els.villageDetailError,
          err.status === 404 ? "No stored history for this village." : err.message
        );
        showToast("Village history failed", err.message, "error");
      });
  }

  function renderVillageHistory(payload) {
    var els = analysisElements();
    var history = payload.history || [];
    els.villageDetailError.hidden = true;
    els.villageDetailNote.hidden = true;
    if (!history.length) {
      els.villageDetailError.hidden = false;
      setText(els.villageDetailError, "No stored history for this village.");
      return;
    }
    var latest = history[history.length - 1];
    setText(els.villageCoords, latest.x + "|" + latest.y);
    setText(els.villagePlayer, latest.player_name || "—");
    setText(els.villageAlliance, latest.alliance_tag || "—");
    els.villageMeta.hidden = false;
    els.villageAbsent.hidden = payload.present_in_latest !== false;

    els.villageHistoryTable.hidden = false;
    els.villageHistoryBody.textContent = "";
    history.forEach(function (point) {
      els.villageHistoryBody.appendChild(villageHistoryRow(point));
    });

    if (history.length === 1) {
      els.villageChartCard.hidden = true;
      els.villageDetailNote.hidden = false;
      setText(els.villageDetailNote, "Only one stored observation; no trend chart.");
      return;
    }
    renderVillageChart(history, latest.name);
  }

  function villageHistoryRow(point) {
    var tr = document.createElement("tr");
    var tdDate = document.createElement("td");
    tdDate.className = "date-cell";
    tdDate.textContent = point.snapshot_date;
    var tdName = document.createElement("td");
    tdName.textContent = point.name;
    var tdPop = document.createElement("td");
    tdPop.className = "num";
    tdPop.textContent = fmtInt(point.population);
    var tdPlayer = document.createElement("td");
    tdPlayer.className = "player-name";
    tdPlayer.textContent = point.player_name || "—";
    var tdAlliance = document.createElement("td");
    tdAlliance.textContent = point.alliance_tag || "—";
    tr.appendChild(tdDate);
    tr.appendChild(tdName);
    tr.appendChild(tdPop);
    tr.appendChild(tdPlayer);
    tr.appendChild(tdAlliance);
    return tr;
  }

  // One gold population line chart per village; updated in place on reopen.
  function renderVillageChart(history, name) {
    var els = analysisElements();
    if (!window.Chart) {
      els.villageChartCard.hidden = true;
      els.villageDetailNote.hidden = false;
      setText(els.villageDetailNote, "Chart library unavailable.");
      return;
    }
    els.villageChartCard.hidden = false;
    var labels = history.map(function (p) {
      return p.snapshot_date;
    });
    var data = history.map(function (p) {
      return p.population;
    });
    els.villageCanvas.setAttribute("aria-label", "Population history for " + name);
    var chart = analysisState.charts.village;
    if (chart) {
      chart.data.labels = labels;
      chart.data.datasets[0].label = name;
      chart.data.datasets[0].data = data;
      chart.update();
      chart.resize();
      return;
    }
    chart = new Chart(els.villageCanvas.getContext("2d"), {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: name,
            data: data,
            borderColor: cssVar("--accent-gold", "#d1a84a"),
            backgroundColor: "rgba(209,168,74,0.12)",
            fill: true,
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.3,
            spanGaps: false,
          },
        ],
      },
      options: baseChartOpts(),
    });
    chart.options.plugins.legend.display = false;
    analysisState.charts.village = chart;
  }


  function changeRow(row) {
    var tr = document.createElement("tr");
    var tdDate = document.createElement("td");
    tdDate.className = "date-cell";
    tdDate.textContent = row.date;
    tr.appendChild(tdDate);
    tr.appendChild(numCell(fmtInt(row.villages)));
    tr.appendChild(deltaCell(row.villages_delta, row.previous_date, row.elapsed_days));
    tr.appendChild(numCell(fmtInt(row.population)));
    tr.appendChild(deltaCell(row.population_delta, row.previous_date, row.elapsed_days));
    tr.appendChild(numCell(fmtInt(row.players)));
    tr.appendChild(deltaCell(row.players_delta, row.previous_date, row.elapsed_days));
    tr.appendChild(numCell(fmtInt(row.vp)));
    tr.appendChild(deltaCell(row.vp_delta, row.previous_date, row.elapsed_days));
    return tr;
  }

  function numCell(text) {
    var td = document.createElement("td");
    td.className = "num";
    td.textContent = text;
    return td;
  }

  function deltaCell(d, previousDate, elapsedDays) {
    var td = document.createElement("td");
    td.className = "num";
    if (d === null || d === undefined) {
      td.textContent = "\u2014";
      td.classList.add("faint");
    } else {
      if (d > 0) {
        td.textContent = "+" + fmtInt(d);
        td.classList.add("is-positive");
      } else if (d < 0) {
        td.textContent = "\u2212" + fmtInt(Math.abs(d));
        td.classList.add("is-negative");
      } else {
        td.textContent = "\u00b10";
        td.classList.add("faint");
      }
      // Honest horizon: a delta computed across a multi-day gap is marked,
      // with the actual comparison date in the tooltip. First/adjacent
      // snapshots carry no marker.
      if (elapsedDays > 1) {
        var gap = document.createElement("span");
        gap.className = "delta-gap";
        gap.textContent = " (" + elapsedDays + " d)";
        td.appendChild(gap);
        td.title = "Compared with " + previousDate;
      }
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
        var headers = ["Date", "Previous snapshot", "Days elapsed", "Villages", "Villages Δ", "Population", "Population Δ", "Players", "Players Δ", "VP", "VP Δ"];
        exportCsv(
          "changes-" + snapshot + ".csv",
          headers,
          rows.map(function (r) {
            return [r.date, r.previous_date, r.elapsed_days, r.villages, r.villages_delta, r.population, r.population_delta, r.players, r.players_delta, r.vp, r.vp_delta];
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
        exportCsv(
          "events-" + analysisState.from + "-" + analysisState.to + "-limit-" + analysisState.eventsLimit + ".csv",
          headers,
          rows
        );
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
    villages: loadVillages,
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
    // Six tabs can overflow on narrow screens — keep the chosen one visible.
    tab.scrollIntoView({ block: "nearest", inline: "nearest" });
    // The global alliance filter scopes regions/events/changes/players; the
    // Alliances tab is a cross-alliance chart with its own local picker.
    if (els.allianceFilter) {
      els.allianceFilter.hidden = name === "alliances" || allianceTags.length < 2;
    }
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
          renderStandingsChart();
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
    els.eventsLimit.addEventListener("change", function () {
      // Keeps the selected range and alliance filter; only the row limit
      // changes (the loader also preserves it across refetches).
      analysisState.eventsLimit = Number(els.eventsLimit.value);
      onChange();
    });
  }

  function wireAnalysis() {
    applyChartDefaults();
    wireTabs();
    wireRegionSelect();
    wireMetricToggle();
    wireEventsControls();
    wireStandingsPicker();
    wireAllianceSwitch();
    wireVillagesSearch();
    wireExportButtons();
    // Regions is the default tab — its payload is fetched at init.
    activateTab(document.getElementById("tab-regions"));
  }

  /* --- dashboard views ------------------------------------------------------------ */

  var dashboardState = {
    activeView: "intelligence",
    canManage: true,
    analysisInitialized: false,
    operationsInitialized: false,
  };

  function dashboardElements() {
    return {
      tablist: document.getElementById("dashboard-view-tabs"),
      tabs: Array.prototype.slice.call(document.querySelectorAll("#dashboard-view-tabs .tab-bar__tab")),
      panels: Array.prototype.slice.call(document.querySelectorAll(".dashboard-panel")),
      operationsTab: document.getElementById("dashboard-tab-operations"),
      operationsPanel: document.getElementById("dashboard-panel-operations"),
    };
  }

  function canManageFromAuth(status) {
    // Token / no-auth mode — or an unresolved status — is manageable; only a
    // confirmed OAuth admin gains the operational controls.
    return status ? status.method !== "oauth" || Boolean(status.user && status.user.admin) : true;
  }

  function setDashboardAccess(canManage) {
    dashboardState.canManage = canManage;
    var els = dashboardElements();
    // A member never sees the Operations tab; the panel stays hidden and no
    // settings/action request is ever issued. The panel opens only through
    // activateDashboardView, which re-checks the same gate.
    if (els.operationsTab) els.operationsTab.hidden = !canManage;
    if (!canManage && els.operationsPanel) els.operationsPanel.hidden = true;
    // Admin-only cards (the raw Job log) follow the same boundary; a role
    // switch never re-opens the Operations panel — only its tab does.
    document.querySelectorAll(".card[data-admin-only]").forEach(function (card) {
      card.hidden = !canManage;
    });
  }

  function activateDashboardView(name) {
    if (name === "operations" && !dashboardState.canManage) return;
    var els = dashboardElements();
    var tab = document.getElementById("dashboard-tab-" + name);
    var panel = document.getElementById("dashboard-panel-" + name);
    if (!tab || !panel) return;
    els.tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
    });
    els.panels.forEach(function (p) {
      p.hidden = p !== panel;
    });
    dashboardState.activeView = name;
    onDashboardViewActivated(name);
  }

  function onDashboardViewActivated(name) {
    if (name === "intelligence") {
      // Lazy analysis: wire it (and its default Regions request) exactly
      // once, then re-measure charts — one created or updated while the
      // panel was hidden keeps a zero-size canvas.
      if (!dashboardState.analysisInitialized) {
        dashboardState.analysisInitialized = true;
        wireAnalysis();
      }
      Object.keys(analysisState.charts).forEach(function (chartName) {
        var chart = analysisState.charts[chartName];
        if (chart && chart.resize) chart.resize();
      });
      return;
    }
    if (name === "operations" && dashboardState.canManage) {
      // First selection only: manual actions are wired and Settings loaded
      // for a manageable user; both stay untouched for members.
      if (!dashboardState.operationsInitialized) {
        dashboardState.operationsInitialized = true;
        wireActionButtons();
        loadSettings();
      }
    }
  }

  function wireDashboardViews() {
    var els = dashboardElements();
    els.tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        activateDashboardView(tab.id.replace("dashboard-tab-", ""));
      });
      tab.addEventListener("keydown", function (event) {
        // Arrow/Home/End navigation walks only the visible top-level tabs,
        // so a hidden Operations tab is never keyboard-activatable.
        var visible = els.tabs.filter(function (t) {
          return !t.hidden;
        });
        var index = visible.indexOf(tab);
        if (index === -1) return;
        var target = null;
        if (event.key === "ArrowRight") {
          target = visible[(index + 1) % visible.length];
        } else if (event.key === "ArrowLeft") {
          target = visible[(index - 1 + visible.length) % visible.length];
        } else if (event.key === "Home") {
          target = visible[0];
        } else if (event.key === "End") {
          target = visible[visible.length - 1];
        }
        if (target) {
          event.preventDefault();
          target.focus();
          activateDashboardView(target.id.replace("dashboard-tab-", ""));
        }
      });
    });
  }

  function wireActionButtons() {
    els.fetchButton.addEventListener("click", function () {
      runAction("fetch");
    });
    els.reportButton.addEventListener("click", function () {
      runAction("report");
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

  // Auth-gated bootstrap: protected requests start only after
  // /api/auth/status settles, so a member's denied settings call can never
  // emit its misleading admin-required toast. `canManage` is true for
  // admins, token mode and no-auth mode; a confirmed OAuth member gets the
  // read-only flows (status, analysis) only — logs stay admin-only and are
  // neither fetched nor polled for members.
  function startDashboardData(canManage) {
    setDashboardAccess(canManage);
    // Status powers the top bar and Overview; the raw Job log is admin-only
    // (a member polling it would emit a 403 toast every 15 s).
    loadStatus();
    if (canManage) {
      loadLogs();
      window.setInterval(loadLogs, LOG_REFRESH_MS);
    }
    // Live status refreshes every minute regardless of the active view.
    window.setInterval(loadStatus, 60000);
    // The active-analysis refresh runs only while Intelligence is active:
    // hidden panels have nothing to keep warm, and a hidden-panel update
    // would size the chart at zero width.
    window.setInterval(function () {
      if (dashboardState.activeView !== "intelligence") return;
      var panel = document.getElementById("panel-" + activeTabName);
      if (!panel || panel.getAttribute("aria-busy") === "true") return;
      var loader = tabLoaders[activeTabName];
      if (loader) loader();
    }, 60000);
    // Intelligence is the initial view — analysis wires itself (and issues
    // its default Regions request) on this first activation, exactly once.
    activateDashboardView("intelligence");
  }

  document.addEventListener("DOMContentLoaded", function () {
    wireForm();
    wireDashboardViews();
    loadAuthStatus().then(function (status) {
      var canManage = canManageFromAuth(status);
      if (status && status.method === "oauth") {
        // Resolve the Operations gate before branching: a pending login
        // (oauth without a session) must not expose the admin-only view.
        setDashboardAccess(canManage);
        if (!status.user) {
          // Confirmed OAuth session missing — ask for login before any
          // protected request; the callback reload re-enters this bootstrap
          // with the adopted session token.
          showTokenDialog();
          return;
        }
        // OAuth member: read-only (admin users pass canManage=true).
        startDashboardData(canManage);
        return;
      }
      // Token / no-auth mode (or an unknown status). In token mode without a
      // stored token the admin-only surface stays hidden and no protected
      // request is issued before the user unlocks (the dialog's reload
      // re-enters this bootstrap with the token stored).
      if (status && status.method === "token" && !localStorage.getItem(TOKEN_KEY)) {
        setDashboardAccess(false);
        showTokenDialog();
        return;
      }
      startDashboardData(canManage);
    });
  });
})();
