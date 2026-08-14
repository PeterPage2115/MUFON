/* Status module — /api/status rendering, the global connection state
   (online/degraded/offline), the persistent error banner and the
   last-good-load marker. Depends on ui, api and analysis (the alliance
   filter reacts to the status payload's tags). */

import { api } from "./api.js";
import { els, state, setText, formatClock, formatTime, formatTimestamp, showToast } from "./ui.js";
import { setAllianceTags } from "./analysis.js";

/* --- global connection state ----------------------------------------------- */
//
// One source of truth for online/degraded/offline: a successful /api/status
// marks the dashboard online and records last_good_load; a failure keeps
// the last good payload on screen, shows the persistent banner with the
// last-good time and a Retry path. The dashboard never claims "connected"
// without a successful /api/status.

export function renderConnectionState(connection) {
  if (!els.connectionState) return;
  els.connectionState.classList.remove(
    "connection-state--online",
    "connection-state--degraded",
    "connection-state--offline"
  );
  els.connectionState.classList.add("connection-state--" + connection);
  var text = "Local service";
  if (connection === "online") text = "Connected";
  else if (connection === "degraded") text = "Connection issue";
  else if (connection === "offline") text = "Connection issue";
  if (els.connectionText) setText(els.connectionText, text);
}

export function renderGlobalError(message) {
  if (!els.globalBanner) return;
  setText(els.globalBannerText, message);
  els.globalBanner.hidden = false;
  els.globalBanner.setAttribute("role", "alert");
  if (els.retryButton) els.retryButton.hidden = false;
  // The shared button becomes Retry: same action, honest label.
  if (els.refreshButton) {
    var label = els.refreshButton.querySelector(".button-label");
    if (label) setText(label, "Retry dashboard");
  }
}

export function clearGlobalError() {
  if (!els.globalBanner) return;
  els.globalBanner.hidden = true;
  els.globalBanner.setAttribute("role", "status");
  if (els.retryButton) els.retryButton.hidden = true;
  if (els.refreshButton) {
    var label = els.refreshButton.querySelector(".button-label");
    if (label) setText(label, "Refresh dashboard");
  }
}

export function markLastGoodLoad() {
  state.connectionState.lastGoodLoad = new Date();
  if (els.lastGoodLoad) setText(els.lastGoodLoad, formatTime(state.connectionState.lastGoodLoad.toISOString()));
  if (els.lastGoodLoadWrap) els.lastGoodLoadWrap.hidden = false;
}

export function setRefreshBusy(busy) {
  state.refreshInFlight = busy;
  if (!els.refreshButton) return;
  els.refreshButton.disabled = busy;
  els.refreshButton.classList.toggle("is-loading", busy);
  els.refreshButton.setAttribute("aria-busy", String(busy));
}

/* --- status card ------------------------------------------------------------ */

// Text-first freshness labels (never color alone): No data / Current /
// Stale · N d / Gap · N missing days.
function freshnessLabel(freshness) {
  if (!freshness) return "—";
  if (freshness.state === "no_data") return "No data";
  if (freshness.state === "current") return "Current";
  if (freshness.state === "stale") return "Stale \u00b7 " + freshness.age_days + " d";
  if (freshness.state === "gap") return "Gap \u00b7 " + freshness.gap_days + " missing days";
  return "—";
}

export function renderStatus(data) {
  state.lastStatus = data;  // consumed by Operations (db stats, report confirm)
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
      case "last_successful_fetch":
      case "last_successful_report":
        value = formatTimestamp(data[key]);
        break;
      case "freshness":
        value = freshnessLabel(data.freshness);
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
    if (key === "freshness" && data.freshness) {
      var note = document.querySelector('[data-status-note="freshness"]');
      if (note) {
        var noteText = "from snapshots";
        if (data.freshness.state === "no_data") noteText = "no snapshots stored";
        else if (data.freshness.snapshot_date) {
          // The comparison baseline is server-provided, never inferred in
          // JS (same no-false-day-over-day contract as the Changes tab).
          noteText = "as of " + data.freshness.snapshot_date;
          if (data.freshness.state === "gap" && data.freshness.previous_snapshot_date) {
            noteText = "prev " + data.freshness.previous_snapshot_date + " \u00b7 " + noteText;
          }
        }
        if (note.textContent !== noteText) setText(note, noteText);
      }
    }
  });

  var alertBox = els.statusAlert;
  var errors = data.errors || [];
  var freshness = data.freshness || null;

  // Job-log errors (visible to admins only) — the error badge must NOT
  // suppress the separate freshness warning: both may show at once.
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

  // Freshness warning: text-first states from the server payload (never
  // recomputed in JS). Visible to members even when errors are sanitized.
  var freshnessBox = els.statusFreshness;
  var freshnessText = "";
  if (freshness && freshness.state === "no_data") {
    freshnessText = "No snapshot has been stored yet. Fetch data to populate the dashboard.";
  } else if (freshness && freshness.state === "stale") {
    freshnessText =
      "Snapshot is " + freshness.age_days + " day" + (freshness.age_days === 1 ? "" : "s") +
      " old. Latest snapshot: " + freshness.snapshot_date + ".";
  } else if (freshness && freshness.state === "gap") {
    freshnessText =
      freshness.gap_days + " day" + (freshness.gap_days === 1 ? "" : "s") +
      " missing between " + freshness.previous_snapshot_date + " and " + freshness.snapshot_date + ".";
  }
  if (freshnessBox) {
    if (freshnessText) {
      setText(freshnessBox, freshnessText);
      freshnessBox.classList.remove("is-hidden");
    } else {
      freshnessBox.classList.add("is-hidden");
    }
  }

  // Card badge precedence: job-log errors → gap → stale → no_data → watching.
  var stateLabel = document.querySelector("[data-status-state-label]");
  var stateBadge = stateLabel ? stateLabel.closest(".card-state") : null;
  var badgeText = "Watching";
  var badgeClass = "card-state--healthy";
  if (errors.length) {
    badgeText = "Attention \u00b7 " + errors.length + " recent error" + (errors.length === 1 ? "" : "s");
    badgeClass = "card-state--attention";
  } else if (freshness && freshness.state === "gap") {
    badgeText = "Snapshot gap";
    badgeClass = "card-state--warning";
  } else if (freshness && freshness.state === "stale") {
    badgeText = "Stale data";
    badgeClass = "card-state--warning";
  } else if (freshness && freshness.state === "no_data") {
    badgeText = "No snapshot";
    badgeClass = "card-state--warning";
  }
  if (stateBadge) {
    stateBadge.classList.remove("card-state--healthy", "card-state--attention", "card-state--warning");
    stateBadge.classList.add(badgeClass);
    setText(stateLabel, badgeText);
  }

  setAllianceTags(data.alliance_tags || []);

  els.metricGrid.setAttribute("aria-busy", "false");
}

export function loadStatus() {
  return api.status().then(function (data) {
    state.connectionState.online = true;
    state.connectionState.error = null;
    renderConnectionState("online");
    clearGlobalError();
    markLastGoodLoad();
    renderStatus(data);
  }).catch(function (err) {
    els.metricGrid.setAttribute("aria-busy", "false");
    state.connectionState.online = false;
    state.connectionState.error = err;
    renderConnectionState("offline");
    var last = state.connectionState.lastGoodLoad
      ? " Last good load: " + formatTime(state.connectionState.lastGoodLoad.toISOString())
      : "";
    renderGlobalError("Connection issue — the last status read failed." + last);
    showToast("Status unavailable", err.message, "error");
  });
}

/* --- Overview command center (Faza 3) ---------------------------------------- */
//
// Role-aware landing content: freshness + last successful runs + tracked KPI
// with honest deltas + top regions + movement + quick links. The payload
// comes from /api/analysis/overview; last-success timestamps from the
// status payload (job_health/last_successful_* — safe for members).

function overviewEl(selector) {
  return document.querySelector(selector);
}

function setDeltaNote(el, delta, unit) {
  if (!el) return;
  if (delta === null || delta === undefined) {
    el.textContent = "";
    return;
  }
  var text = delta > 0 ? "+" : delta < 0 ? "\u2212" : "\u00b1";
  text += Number(Math.abs(delta)).toLocaleString("en-US") + (unit ? " " + unit : "");
  el.textContent = text;
  el.classList.remove("is-positive", "is-negative", "faint");
  if (delta > 0) el.classList.add("is-positive");
  else if (delta < 0) el.classList.add("is-negative");
  else el.classList.add("faint");
}

function renderOverview(payload, status) {
  var grid = overviewEl("[data-overview-grid]");
  var empty = overviewEl("[data-overview-empty]");
  if (!grid) return;

  var summary = payload.summary || {};
  var current = summary.current || null;
  var freshness = payload.freshness || {};
  var movement = payload.movement || {};

  setText(overviewEl("[data-overview-freshness]"), freshnessLabel(freshness));
  var note = overviewEl("[data-overview-freshness-note]");
  if (note) {
    var noteText = "from snapshots";
    if (freshness.state === "no_data") noteText = "no snapshots stored";
    else if (freshness.snapshot_date) noteText = "as of " + freshness.snapshot_date;
    setText(note, noteText);
  }

  setText(overviewEl("[data-overview-last-fetch]"), formatTimestamp(status.last_successful_fetch));
  setText(overviewEl("[data-overview-last-report]"), formatTimestamp(status.last_successful_report));

  var kpis = [
    ["villages", "villages"],
    ["population", "population"],
    ["players", "players"],
    ["vp", "vp"],
  ];
  kpis.forEach(function (pair) {
    var valueEl = overviewEl('[data-overview-' + pair[0] + ']');
    var deltaEl = overviewEl('[data-overview-' + pair[0] + '-delta]');
    if (current) {
      setText(valueEl, Number(current[pair[1]]).toLocaleString("en-US"));
      var delta = summary.delta ? summary.delta[pair[1]] : null;
      setDeltaNote(deltaEl, delta === null || delta === undefined ? null : delta, "");
    } else {
      setText(valueEl, "\u2014");
      setDeltaNote(deltaEl, null, "");
    }
  });

  setText(
    overviewEl("[data-overview-movement]"),
    movement.gained_total === null || movement.gained_total === undefined
      ? "\u2014"
      : String(movement.gained_total) + " gained \u00b7 " + String(movement.lost_total) + " lost"
  );
  setText(
    overviewEl("[data-overview-movement-note]"),
    movement.from && movement.to ? movement.from + " \u2192 " + movement.to : ""
  );

  var regionsList = overviewEl("[data-overview-regions]");
  regionsList.textContent = "";
  (payload.regions && payload.regions.top || []).forEach(function (r) {
    var li = document.createElement("li");
    var name = document.createElement("span");
    name.className = "region-top__tag";
    name.textContent = r.region;
    var share = document.createElement("span");
    share.className = "region-top__pop";
    share.textContent = (r.share * 100).toFixed(1) + "%";
    li.appendChild(name);
    li.appendChild(share);
    regionsList.appendChild(li);
  });
  if (!regionsList.children.length) {
    var emptyLi = document.createElement("li");
    emptyLi.className = "faint";
    emptyLi.textContent = "No regions yet.";
    regionsList.appendChild(emptyLi);
  }

  // Quick links ride the URL-state contract (plain links — no JS wiring).
  var links = overviewEl("[data-overview-links]");
  links.textContent = "";
  var quick = [
    { href: "?view=intelligence&tab=regions", label: "Regions" },
    { href: "?view=intelligence&tab=events", label: "Events" },
    { href: "?view=intelligence&tab=compare", label: "Compare periods" },
  ];
  if (state.dashboardState.canManage) {
    quick.push({ href: "?view=operations", label: "Operations" });
  }
  quick.forEach(function (q) {
    var a = document.createElement("a");
    a.className = "button button--outline button--small";
    a.href = q.href;
    a.textContent = q.label;
    links.appendChild(a);
  });

  setText(overviewEl("[data-overview-asof]"), payload.latest_date ? "as of " + payload.latest_date : "");
  if (empty) empty.hidden = true;
  grid.hidden = false;
}

export function loadOverview() {
  return Promise.all([api.status(), api.analysis("overview", { days: 30 })])
    .then(function (results) {
      renderOverview(results[1], results[0]);
    })
    .catch(function () {
      var grid = overviewEl("[data-overview-grid]");
      var empty = overviewEl("[data-overview-empty]");
      if (grid) grid.hidden = true;
      if (empty) {
        empty.hidden = false;
        setText(empty, "Couldn't load the intelligence overview.");
      }
    });
}
