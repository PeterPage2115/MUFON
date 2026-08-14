/* Shared UI state and DOM helpers — the dependency root of the dashboard
   modules (api/auth/status/analysis/operations import from here; ui imports
   nothing). Vanilla JS, no build step. */

/* Shared mutable state — one object so modules never duplicate it. */

//: Toast timing (DESIGN.md §5 Toast: auto-dismiss after 4s, --motion-standard).
var TOAST_DISMISS_MS = 4000;
var TOAST_OUT_MS = 220;
export const state = {
  els: {
    headerSnapshot: document.querySelector("[data-header-snapshot]"),
    headerSource: document.querySelector("[data-header-source]"),
    headerBuild: document.querySelector("[data-header-build]"),
    statusAlert: document.querySelector("[data-status-errors]"),
    statusFreshness: document.querySelector("[data-status-freshness]"),
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
    connectionState: document.querySelector("[data-connection-state]"),
    connectionText: document.querySelector("[data-connection-text]"),
    globalBanner: document.getElementById("global-status-banner"),
    globalBannerText: document.querySelector("[data-global-banner-text]"),
    retryButton: document.querySelector("[data-retry-dashboard]"),
    refreshButton: document.getElementById("refresh-dashboard"),
    lastGoodLoad: document.querySelector("[data-last-good-load]"),
    lastGoodLoadWrap: document.getElementById("last-good-load"),
  },
  currentSettings: null,
  knownLogKeys: new Set(),
  logEls: {},
  actionInFlight: false,
  refreshInFlight: false,
  connectionState: { online: false, lastGoodLoad: null, error: null },
  analysisState: {
    charts: {},
    metric: "population",
    alliance: "combined",
    region: null,
    from: null,
    to: null,
    eventsLimit: 200,
    warsPayload: null,
    warsFrom: null,
    warsTo: null,
    regionsDates: [],
    regionsSeries: {},
    standingsPayload: null,
    standingsSelection: null, // null = first load (derive from defaults); [] = explicit empty
    standingsDefaults: [],
    villageQuerySeq: 0, // monotonically rising: stale search responses are ignored
    villageDebounce: null,
    villageHistorySeq: 0, // same guard for history responses
    villageDays: 30,
    dirtyTabs: {}, // alliance-filtered tabs pending reload on next activation
  },
  allianceTags: [],
  activatedTabs: {},
  activeTabName: "regions",
  analysisEls: null,
  dashboardState: {
    activeView: "intelligence",
    canManage: true,
    analysisInitialized: false,
    operationsInitialized: false,
  },
};

export const els = state.els;

/* --- tiny helpers ------------------------------------------------------- */

export function $(selector) {
  return document.querySelector(selector);
}

export function setText(el, value) {
  if (el && el.textContent !== value) el.textContent = value;
}

function pad(n) {
  return String(n).padStart(2, "0");
}

export function formatClock(iso, tz) {
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

export function formatTime(iso) {
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

// Calendar date + time in UTC for the last-success rows (server persists
// job_log timestamps in UTC — never the browser timezone). Missing or
// invalid input renders "Never": a job that never succeeded is a fact, not
// a dash.
export function formatTimestamp(iso) {
  if (!iso) return "Never";
  var d = new Date(iso);
  if (isNaN(d.getTime())) return "Never";
  return d.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "") + " UTC";
}

export function showToast(title, message, variant) {
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

export function setBusy(button, busy, label) {
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

export function cssVar(name, fallback) {
  var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export function fmtInt(n) {
  return Number(n).toLocaleString("en-US");
}

/* --- analysis panel states -------------------------------------------------- */

export function setPanelBusy(name, busy) {
  var panel = document.getElementById("panel-" + name);
  if (panel) panel.setAttribute("aria-busy", String(busy));
}

export function showPanelEmpty(panel, message, alert) {
  hidePanelError(panel);
  var stateEl = panel.querySelector(".empty-state");
  if (!stateEl) {
    stateEl = document.createElement("p");
    stateEl.className = "empty-state";
    panel.appendChild(stateEl);
  }
  stateEl.textContent = message;
  stateEl.hidden = false; // the events panel's dedicated node starts hidden
  if (alert) stateEl.setAttribute("role", "alert");
  panel.classList.add("is-empty");
}

export function hidePanelEmpty(panel) {
  panel.classList.remove("is-empty");
  var stateEl = panel.querySelector(".empty-state");
  // The events/wars panels' dedicated nodes ([data-events-empty] /
  // [data-wars-empty]) are owned by their renderers; generated states are
  // removed so recovery never leaves a stale message behind.
  if (stateEl && !stateEl.hasAttribute("data-events-empty") && !stateEl.hasAttribute("data-wars-empty")) {
    if (stateEl.parentNode) stateEl.parentNode.removeChild(stateEl);
  }
}

// Persistent per-panel error state with an explicit Retry action — tab
// switching must never be the only way to recover (ROADMAP.md §4).
export function showPanelError(panel, message, retry) {
  hidePanelEmpty(panel);
  var stateEl = panel.querySelector(".panel-error");
  if (!stateEl) {
    stateEl = document.createElement("div");
    stateEl.className = "panel-error";
    panel.appendChild(stateEl);
  }
  stateEl.textContent = "";
  var text = document.createElement("span");
  text.className = "panel-error__text";
  text.textContent = message;
  stateEl.appendChild(text);
  if (retry) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "button button--outline button--small";
    btn.textContent = "Retry";
    btn.addEventListener("click", retry);
    stateEl.appendChild(btn);
  }
  panel.classList.add("is-error");
}

export function hidePanelError(panel) {
  panel.classList.remove("is-error");
  var stateEl = panel.querySelector(".panel-error");
  if (stateEl && stateEl.parentNode) stateEl.parentNode.removeChild(stateEl);
}

export function showChartUnavailable(card) {
  var stateEl = card.querySelector(".empty-state");
  if (!stateEl) {
    stateEl = document.createElement("p");
    stateEl.className = "empty-state";
    card.appendChild(stateEl);
  }
  stateEl.textContent = "Chart library unavailable.";
  card.classList.add("is-empty");
}

export function showChartLoading(card) {
  var stateEl = card.querySelector(".empty-state");
  if (!stateEl) {
    stateEl = document.createElement("p");
    stateEl.className = "empty-state";
    card.appendChild(stateEl);
  }
  stateEl.textContent = "Loading…";
  card.classList.add("is-empty");
}

export function tableLoading(tbody, colspan) {
  tbody.textContent = "";
  var tr = document.createElement("tr");
  var td = document.createElement("td");
  td.colSpan = colspan;
  td.className = "table-loading";
  td.textContent = "Loading…";
  tr.appendChild(td);
  tbody.appendChild(tr);
}

/* Chart data tables — the textual fallback for every Chart.js canvas
   (ROADMAP.md §4): a <details> holding a semantic table built from the
   exact chart payload. The canvas describes it via aria-describedby; the
   table may be visually collapsed but is never replaced by tooltips. */

export function fillChartDataTable(card, id, headers, rows) {
  var details = card.querySelector(".chart-data-table");
  if (!details) {
    details = document.createElement("details");
    details.className = "chart-data-table";
    details.id = id;
    var summary = document.createElement("summary");
    summary.textContent = "Show data table";
    details.appendChild(summary);
    var wrap = document.createElement("div");
    wrap.className = "data-table__wrap";
    var table = document.createElement("table");
    table.className = "data-table";
    table.appendChild(document.createElement("thead"));
    table.appendChild(document.createElement("tbody"));
    wrap.appendChild(table);
    details.appendChild(wrap);
    card.appendChild(details);
  }
  var head = details.querySelector("thead");
  head.textContent = "";
  var headRow = document.createElement("tr");
  headers.forEach(function (h) {
    var th = document.createElement("th");
    th.scope = "col";
    th.textContent = h;
    headRow.appendChild(th);
  });
  head.appendChild(headRow);
  var body = details.querySelector("tbody");
  body.textContent = "";
  rows.forEach(function (cells) {
    var tr = document.createElement("tr");
    cells.forEach(function (value) {
      var td = document.createElement("td");
      td.textContent = value === null || value === undefined ? "\u2014" : String(value);
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
  return details;
}
