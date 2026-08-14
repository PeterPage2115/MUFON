/* Travian Report Bot dashboard — bootstrap module.
   Wires the ES modules (ui/api/auth/status/analysis/operations) together:
   auth gating, the three-view shell, the manual refresh cycle and the
   role-aware landing. Vanilla JS, no build step, no dependencies. */

import { state, setText, formatTime } from "./app/ui.js";
import { api, setTokenGetter, setUnauthorizedHandler } from "./app/api.js";
import { tokenStore, loadAuthStatus, showTokenDialog, canManageFromAuth } from "./app/auth.js";
import { loadStatus, loadOverview, setRefreshBusy } from "./app/status.js";
import { wireAnalysis, activateTab, tabLoaders, refreshActiveAnalysis, applyInitialContext } from "./app/analysis.js";
import { wireActionButtons, loadSettings, loadLogs, loadRuns, renderDbStats, wireForm } from "./app/operations.js";

var els = state.els;
var dashboardState = state.dashboardState;
var analysisState = state.analysisState;

// OAuth login lands on /?auth=success with the session in an HttpOnly
// cookie (never in the URL or storage) — just drop the marker. A ?session=
// query string is never adopted.
var urlParams = new URLSearchParams(window.location.search);
if (urlParams.get("auth") || urlParams.get("auth_error")) {
  history.replaceState({}, "", window.location.pathname);
}

/* --- dashboard views ------------------------------------------------------------ */

function dashboardElements() {
  return {
    tablist: document.getElementById("dashboard-view-tabs"),
    tabs: Array.prototype.slice.call(document.querySelectorAll("#dashboard-view-tabs .tab-bar__tab")),
    panels: Array.prototype.slice.call(document.querySelectorAll(".dashboard-panel")),
    operationsTab: document.getElementById("dashboard-tab-operations"),
    operationsPanel: document.getElementById("dashboard-panel-operations"),
  };
}

function setDashboardAccess(canManage) {
  dashboardState.canManage = canManage;
  var elsViews = dashboardElements();
  // A member never sees the Operations tab; the panel stays hidden and no
  // settings/action request is ever issued. The panel opens only through
  // activateDashboardView, which re-checks the same gate.
  if (elsViews.operationsTab) elsViews.operationsTab.hidden = !canManage;
  if (!canManage && elsViews.operationsPanel) elsViews.operationsPanel.hidden = true;
  // Admin-only cards (the raw Job log) follow the same boundary; a role
  // switch never re-opens the Operations panel — only its tab does.
  document.querySelectorAll(".card[data-admin-only]").forEach(function (card) {
    card.hidden = !canManage;
  });
}

function activateDashboardView(name) {
  if (name === "operations" && !dashboardState.canManage) return;
  var elsViews = dashboardElements();
  var tab = document.getElementById("dashboard-tab-" + name);
  var panel = document.getElementById("dashboard-panel-" + name);
  if (!tab || !panel) return;
  elsViews.tabs.forEach(function (t) {
    var on = t === tab;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
  });
  elsViews.panels.forEach(function (p) {
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
  if (name === "overview") {
    // Refresh on view activation: the command center + status + the admin
    // job log (on demand, never a background poller).
    loadOverview();
    loadStatus();
    if (dashboardState.canManage) loadLogs();
    return;
  }
  if (name === "operations" && dashboardState.canManage) {
    // First selection only: manual actions are wired and Settings loaded
    // for a manageable user; both stay untouched for members. The job log
    // is loaded on demand so the run history is current.
    if (!dashboardState.operationsInitialized) {
      dashboardState.operationsInitialized = true;
      wireActionButtons();
      loadSettings();
    }
    loadLogs();
    loadRuns();
    renderDbStats();
  }
}

function wireDashboardViews() {
  var elsViews = dashboardElements();
  elsViews.tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      activateDashboardView(tab.id.replace("dashboard-tab-", ""));
    });
    tab.addEventListener("keydown", function (event) {
      // Arrow/Home/End navigation walks only the visible top-level tabs,
      // so a hidden Operations tab is never keyboard-activatable.
      var visible = elsViews.tabs.filter(function (t) {
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

// Public build provenance: best-effort, never blocks the dashboard. The
// header shows the installed VERSION (v<version>); /api/meta still carries
// build_sha for deployment verification and the live smoke.
function loadMeta() {
  return api
    .meta()
    .then(function (meta) {
      setText(els.headerBuild, meta.version ? "v" + meta.version : "—");
    })
    .catch(function () {
      setText(els.headerBuild, "—");
    });
}

/* --- manual refresh cycle ------------------------------------------------------- */

// The one manual refresh path: status + the admin job log + the active
// analysis panel. Never overwrites a dirty settings form (settings reload
// happens after Save or on Operations entry).
function refreshDashboard() {
  if (state.refreshInFlight) return Promise.resolve();
  setRefreshBusy(true);
  var jobs = [loadStatus()];
  if (dashboardState.canManage) jobs.push(loadLogs());
  if (dashboardState.activeView === "intelligence") {
    jobs.push(refreshActiveAnalysis());
  } else if (dashboardState.activeView === "overview") {
    jobs.push(loadOverview());
  }
  return Promise.all(jobs)
    .catch(function () {}) // failures are rendered by the individual loaders
    .then(function () {
      setRefreshBusy(false);
    });
}

function retryDashboard() {
  return refreshDashboard();
}

/* --- role-aware landing --------------------------------------------------------- */

function resolveInitialView(canManage) {
  var urlView = new URLSearchParams(window.location.search).get("view");
  if (urlView === "intelligence" || urlView === "overview" || urlView === "operations") {
    if (urlView === "operations" && !canManage) return "overview";
    return urlView;
  }
  return canManage ? "overview" : "intelligence";
}

function resolveInitialTab() {
  var tabName = new URLSearchParams(window.location.search).get("tab");
  if (!tabName || !tabLoaders[tabName]) return null;
  var tab = document.getElementById("tab-" + tabName);
  return tab && !tab.hidden ? tab : null;
}

// Auth-gated bootstrap: protected requests start only after
// /api/auth/status settles, so a member's denied settings call can never
// emit its misleading admin-required toast. `canManage` is true for
// admins, token mode and no-auth mode; a confirmed OAuth member gets the
// read-only flows (status, analysis) only — logs stay admin-only and are
// never fetched for members.
//
// Data lifecycle: initial load → refresh on view activation → refresh
// after action → manual Refresh. There is no background polling.
function startDashboardData(canManage) {
  setDashboardAccess(canManage);
  applyInitialContext(); // URL + stored preference for days/alliance
  loadStatus();
  // Role-aware landing: an OAuth member starts in Intelligence; admins /
  // token operators start in Overview. An explicit, valid ?view= URL wins
  // over the role default (invalid values fall back safely).
  var view = resolveInitialView(canManage);
  // Resolve the initial analysis tab BEFORE any URL rewrite (the first
  // syncAnalysisUrl would otherwise drop the ?tab= parameter).
  var initialTab = view === "intelligence" ? resolveInitialTab() : null;
  if (view === "intelligence") {
    activateDashboardView("intelligence");
    if (initialTab) activateTab(initialTab);
  } else {
    activateDashboardView(view);
  }
  if (els.refreshButton) {
    els.refreshButton.addEventListener("click", refreshDashboard);
  }
  if (els.retryButton) {
    els.retryButton.addEventListener("click", retryDashboard);
  }
}

/* --- init --------------------------------------------------------------------- */

document.addEventListener("DOMContentLoaded", function () {
  // Module wiring: the api client learns where the token lives and who
  // handles 401s (both live in auth; api never imports auth).
  setTokenGetter(function () {
    return tokenStore.get();
  });
  setUnauthorizedHandler(showTokenDialog);

  wireForm();
  wireDashboardViews();
  loadMeta();
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
    if (status && status.method === "token" && !tokenStore.get()) {
      setDashboardAccess(false);
      showTokenDialog();
      return;
    }
    startDashboardData(canManage);
  });
});
