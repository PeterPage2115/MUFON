/* Analysis module — every Intelligence tab: regions, alliances (standings),
   players, events, wars, changes, villages, their charts (vendored Chart.js),
   the alliance filter and the tab bar. Depends on ui (shared state + DOM
   helpers) and api; exports the wiring + reload surfaces the bootstrap and
   status/operations modules need. */

import { api } from "./api.js";
import {
  $,
  setText,
  formatTime,
  showToast,
  cssVar,
  fmtInt,
  setPanelBusy,
  showPanelEmpty,
  hidePanelEmpty,
  showPanelError,
  hidePanelError,
  showChartUnavailable,
  showChartLoading,
  tableLoading,
  fillChartDataTable,
  state,
} from "./ui.js";

// Aliases into the shared state object (ui owns the single source of truth).
var els = state.els;
var analysisState = state.analysisState;
var allianceTags = state.allianceTags;
var activatedTabs = state.activatedTabs;
var activeTabName = state.activeTabName;
var analysisEls = state.analysisEls;

var SERIES_COLORS = ["#1abc9c", "#e67e22", "#3498db", "#f1c40f"];
function analysisElements() {
  if (!analysisEls) {
    analysisEls = {
      allianceFilter: document.getElementById("analysis-alliance-filter"),
      // Only the analysis sub-tabs — the top-level dashboard view tabs live
      // in #dashboard-view-tabs and are wired separately.
      tabs: Array.prototype.slice.call(document.querySelectorAll("#analysis-tabs .tab-bar__tab")),
      panels: Array.prototype.slice.call(document.querySelectorAll(".analysis-panel")),
      regionsBody: document.querySelector("[data-regions-body]"),
      regionsToolbar: document.querySelector("[data-regions-toolbar]"),
      playersToolbar: document.querySelector("[data-players-toolbar]"),
      changesToolbar: document.querySelector("[data-changes-toolbar]"),
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
      eventsError: document.getElementById("analysis-events-error"),
      eventsGrid: document.querySelector(".events-grid"),
      eventsEmpty: document.querySelector("[data-events-empty]"),
      gainedList: document.querySelector("[data-events-gained]"),
      lostList: document.querySelector("[data-events-lost]"),
      gainedCount: document.querySelector("[data-events-gained-count]"),
      lostCount: document.querySelector("[data-events-lost-count]"),
      warsFrom: document.getElementById("analysis-wars-from"),
      warsTo: document.getElementById("analysis-wars-to"),
      warsError: document.getElementById("analysis-wars-error"),
      warsMatrix: document.querySelector("[data-wars-matrix]"),
      warsMatrixHead: document.querySelector("[data-wars-matrix-head]"),
      warsMatrixBody: document.querySelector("[data-wars-matrix-body]"),
      warsDetail: document.querySelector("[data-wars-detail]"),
      warsDetailTitle: document.querySelector("[data-wars-detail-title]"),
      warsDetailCount: document.querySelector("[data-wars-detail-count]"),
      warsEntries: document.querySelector("[data-wars-entries]"),
      warsDeleted: document.querySelector("[data-wars-deleted]"),
      warsDeletedCount: document.querySelector("[data-wars-deleted-count]"),
      warsEmpty: document.querySelector("[data-wars-empty]"),
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
      compareFrom: document.getElementById("analysis-compare-from"),
      compareTo: document.getElementById("analysis-compare-to"),
      compareError: document.getElementById("analysis-compare-error"),
      compareSummary: document.querySelector("[data-compare-summary]"),
      compareVillages: document.querySelector("[data-compare-villages]"),
      compareVillagesDelta: document.querySelector("[data-compare-villages-delta]"),
      comparePopulation: document.querySelector("[data-compare-population]"),
      comparePopulationDelta: document.querySelector("[data-compare-population-delta]"),
      comparePlayers: document.querySelector("[data-compare-players]"),
      comparePlayersDelta: document.querySelector("[data-compare-players-delta]"),
      compareVp: document.querySelector("[data-compare-vp]"),
      compareVpDelta: document.querySelector("[data-compare-vp-delta]"),
      compareTable: document.querySelector("[data-compare-table]"),
      compareBody: document.querySelector("[data-compare-body]"),
      compareRangeNote: document.querySelector("[data-compare-range-note]"),
      compareMovement: document.querySelector("[data-compare-movement]"),
      compareEmpty: document.querySelector("[data-compare-empty]"),
      playerDetail: document.getElementById("player-detail"),
      playerDetailName: document.getElementById("player-detail-name"),
      playerAbsent: document.querySelector("[data-player-absent]"),
      playerDetailNote: document.querySelector("[data-player-detail-note]"),
      playerDetailError: document.querySelector("[data-player-detail-error]"),
      playerHistoryTable: document.querySelector("[data-player-history-table]"),
      playerHistoryBody: document.querySelector("[data-player-history-body]"),
      playerChartCard: document.querySelector("[data-player-chart]"),
      playerCanvas: document.getElementById("analysis-chart-player"),
      regionDetailTitle: document.getElementById("region-detail-title"),
      regionDetailDate: document.querySelector("[data-region-detail-date]"),
      regionDetailNote: document.querySelector("[data-region-detail-note]"),
      regionDetailError: document.querySelector("[data-region-detail-error]"),
      regionVillagesBody: document.querySelector("[data-region-villages-body]"),
      watchFrom: document.getElementById("analysis-watch-from"),
      watchTo: document.getElementById("analysis-watch-to"),
      watchSeverity: document.getElementById("analysis-watch-severity"),
      watchKind: document.getElementById("analysis-watch-kind"),
      watchError: document.getElementById("analysis-watch-error"),
      watchRange: document.querySelector("[data-watch-range]"),
      watchList: document.querySelector("[data-watch-list]"),
      watchEmpty: document.querySelector("[data-watch-empty]"),
      watchCounts: document.querySelector("[data-watch-counts]"),
      rosterDate: document.getElementById("analysis-roster-date"),
      rosterAlliance: document.getElementById("analysis-roster-alliance"),
      rosterError: document.getElementById("analysis-roster-error"),
      rosterNote: document.querySelector("[data-roster-note]"),
      rosterBody: document.querySelector("[data-roster-body]"),
      rosterEmpty: document.querySelector("[data-roster-empty]"),
    };
  }
  return analysisEls;
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

// The alliance-filter surface lives in api.js (ALLIANCE_FILTERED_KINDS);
// this module only decides the filter's VISIBILITY per tab (activateTab).


//: Range helpers shared by every historical tab. Each tab owns exactly one
//: range (ranges[tab]); pair tabs (events/wars/compare/watch) always send
//: explicit from/to computed from their mode, series tabs (regions/
//: alliances/changes) send days or the explicit pair.
function rangeParams(tab) {
  var r = analysisState.ranges[tab];
  if (r.mode === "custom") return { from: r.from, to: r.to };
  return { days: r.days };
}

//: The (from, to) pair for ``tab`` over the available dates — the stored
//: custom pair when valid, else the last ``days`` snapshots (all when
//: fewer exist). Null when fewer than two dates exist or the stored custom
//: pair is invalid.
function tabPair(tab, dates) {
  if (!dates || dates.length < 2) return null;
  var r = analysisState.ranges[tab];
  if (r.mode === "custom") {
    if (dates.indexOf(r.from) === -1 || dates.indexOf(r.to) === -1 || r.from >= r.to) return null;
    return [r.from, r.to];
  }
  return [dates[Math.max(0, dates.length - r.days)], dates[dates.length - 1]];
}

function setRangeSelectValue(tab) {
  var select = document.querySelector('[data-range-select="' + tab + '"]');
  if (!select) return;
  var r = analysisState.ranges[tab];
  select.value = r.mode === "custom" ? "custom" : String(r.days);
}

function setPairSelectValues(tab, from, to) {
  var fromEl = document.querySelector('[data-range-from="' + tab + '"]');
  var toEl = document.querySelector('[data-range-to="' + tab + '"]');
  if (fromEl && from) fromEl.value = from;
  if (toEl && to) toEl.value = to;
}

//: Revert a stored custom pair that no longer exists among the snapshot
//: dates to the 7-day default — never a request loop, never a broken pair.
function revertStaleCustom(tab) {
  analysisState.ranges[tab] = { mode: "days", days: 7, from: null, to: null };
  syncAnalysisUrl();
  setRangeSelectValue(tab);
}

//: Ensure the tab's custom pair is populated and valid against the
//: snapshot dates. Resolves with true when a request may proceed; false
//: when the pair is invalid (from >= to) — the caller shows the error
//: instead of requesting.
function prepareCustomPair(tab) {
  var r = analysisState.ranges[tab];
  return api.analysis("dates").then(function (payload) {
    var dates = payload.dates || [];
    fillPairSelects(tab, dates, "Need at least two snapshots");
    if (dates.indexOf(r.from) === -1 || dates.indexOf(r.to) === -1) {
      revertStaleCustom(tab);
      return true; // the fallback (days 7) is valid — reload normally
    }
    return r.from < r.to;
  });
}

function fillPairSelects(tab, dates, emptyText) {
  var fromEl = document.querySelector('[data-range-from="' + tab + '"]');
  var toEl = document.querySelector('[data-range-to="' + tab + '"]');
  if (!fromEl || !toEl) return;
  fillDateSelect(fromEl, dates, emptyText);
  fillDateSelect(toEl, dates, emptyText);
}

function reloadTab(tab) {
  if (activatedTabs[tab] && tabLoaders[tab]) {
    analysisState.dirtyTabs[tab] = false;
    tabLoaders[tab]();
  }
}

function loadRegions() {
  var panel = document.getElementById("panel-regions");
  var els = analysisElements();
  hidePanelError(panel);
  setPanelBusy("regions", true);
  tableLoading(els.regionsBody, 5);
  var request = function () {
    return api
      .analysis("regions", rangeParams("regions"))
      .then(function (payload) {
        renderRegions(payload);
        setPanelBusy("regions", false);
      })
      .catch(function (err) {
        setPanelBusy("regions", false);
        showPanelError(panel, "Couldn't load analysis data.", loadRegions);
        activatedTabs.regions = false; // next activation retries
      });
  };
  if (analysisState.ranges.regions.mode !== "custom") return request();
  return prepareCustomPair("regions").then(function (valid) {
    if (!valid) {
      setPanelBusy("regions", false);
      var errEl = document.getElementById("range-regions-error");
      if (errEl) {
        errEl.hidden = false;
        setText(errEl, "From must be earlier than To.");
      }
      els.regionsBody.textContent = "";
      setExportEnabled("regions", false);
      return;
    }
    var errEl = document.getElementById("range-regions-error");
    if (errEl) errEl.hidden = true;
    return request();
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
    return;
  }
  hidePanelEmpty(panel);

  // Honest labels: the table is a single pair (latest vs previous in the
  // selected range), the chart spans the whole range.
  var latestLabel = payload.current_date
    ? "Latest snapshot: " + payload.current_date + (payload.previous_date ? " vs previous snapshot" : "")
    : "Latest snapshot vs previous";
  setText(els.regionsToolbar, latestLabel);
  setRangeSelectValue("regions");
  var pairControls = document.querySelectorAll('[data-range-pair="regions"]');
  Array.prototype.forEach.call(pairControls, function (el) {
    el.hidden = analysisState.ranges.regions.mode !== "custom";
  });
  if (analysisState.ranges.regions.mode === "custom") {
    setPairSelectValues("regions", analysisState.ranges.regions.from, analysisState.ranges.regions.to);
  }

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
  fillSelectOptions(
    els.regionSelect,
    ordered.map(function (name) {
      return { value: name, label: name };
    }),
    "No region history available"
  );
  if (ordered.length && region) els.regionSelect.value = region;

  if (!ordered.length) {
    showChartUnavailable(panel.querySelector(".chart-card"), "No region history available for this range.");
    return;
  }
  renderRegionChart(region);
}

function regionRow(row) {
  var tr = document.createElement("tr");
  if (!row.active) tr.classList.add("is-inactive");

  var tdRegion = document.createElement("td");
  tdRegion.setAttribute("data-label", "Region");
  tdRegion.className = "region-name";
  var regionButton = document.createElement("button");
  regionButton.type = "button";
  regionButton.className = "event-line__name";
  regionButton.textContent = row.region;
  regionButton.setAttribute("aria-label", "Open villages of " + row.region);
  regionButton.addEventListener("click", function () {
    openRegionVillages(row.region);
  });
  tdRegion.appendChild(regionButton);

  var tdControl = document.createElement("td");
  tdControl.setAttribute("data-label", "Control");
  // Semantic meter: real progressbar semantics with a visible percentage —
  // never a color-only or glyph-only signal (ROADMAP.md §4 / DESIGN §8).
  var bar = document.createElement("span");
  bar.className = "control-bar";
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", "100");
  bar.setAttribute("aria-valuenow", (row.share * 100).toFixed(1));
  bar.setAttribute("aria-label", "Control share for " + row.region);
  bar.style.setProperty("--fill", (row.share * 100).toFixed(1) + "%");
  bar.textContent = (row.share * 100).toFixed(1) + "%";
  tdControl.appendChild(bar);

  var tdPop = document.createElement("td");
  tdPop.className = "num";
  tdPop.setAttribute("data-label", "Pop");
  tdPop.textContent = fmtInt(row.our_pop);

  var tdDelta = document.createElement("td");
  tdDelta.className = "num";
  tdDelta.setAttribute("data-label", "\u0394");
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
  tdTo50.setAttribute("data-label", "To 50%");
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
  var card = panel.querySelector(".chart-card");
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

  // Textual fallback: the exact payload as a semantic table — the canvas
  // describes it, tooltips are never the only way to read the chart. The
  // table is NEWEST-FIRST (matches the other history tables); the chart
  // itself stays ASC.
  var tableHeaders = ["Date", "Share", "Our pop", "Total pop"];
  var tableRows = dates
    .map(function (d, i) {
      var p = byDate[d];
      return [
        d,
        p ? (p.share * 100).toFixed(1) + "%" : "—",
        p ? fmtInt(p.our_pop) : "—",
        p ? fmtInt(p.total_pop) : "—",
      ];
    })
    .reverse();
  var tableDetails = fillChartDataTable(card, "chart-data-regions", tableHeaders, tableRows);
  var payload = analysisState.regionsPayload || {};
  var rangeCaption = "Selected range: " + (payload.range_from || "—") + " → " + (payload.range_to || "—");
  var asOfEl = document.querySelector('[data-as-of="regions"]');
  if (asOfEl) {
    asOfEl.hidden = false;
    if (asOfEl.textContent !== rangeCaption) setText(asOfEl, rangeCaption);
  }

  if (!window.Chart) {
    showChartUnavailable(card);
    tableDetails.open = true;
    return;
  }
  card.classList.remove("is-empty");
  var stale = card.querySelector(".empty-state");
  if (stale) stale.remove();

  els.regionCanvas.setAttribute("aria-label", "Share of population over time for " + region);
  els.regionCanvas.setAttribute("aria-describedby", tableDetails.id);
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
  hidePanelError(panel);
  var selection = analysisState.standingsSelection;
  if (selection !== null && !selection.length) {
    // Explicit empty selection: nothing to chart — keep the picker
    // visible with its validation hint (an .is-empty state would hide it,
    // making recovery impossible).
    setPanelBusy("alliances", false);
    hidePanelEmpty(panel);
    renderStandingsPicker();
    updateStandingsFeedback();
    return Promise.resolve();
  }
  setPanelBusy("alliances", true);
  if (!analysisState.charts.alliances) {
    showChartLoading(panel.querySelector(".chart-card"));
  }
  var request = function () {
    return api
      .standings(selection, rangeParams("alliances"))
      .then(function (payload) {
        var dates = payload.dates || [];
        var series = payload.series || [];
        analysisState.standingsTags = payload.available_tags || [];
        if (selection === null) {
          // First load: the request carried no tag params — adopt the
          // resolved top-10 defaults and slice the fetched series
          // client-side (no extra request).
          analysisState.standingsDefaults = (payload.default_tags || []).slice(0, 10);
          analysisState.standingsSelection = analysisState.standingsDefaults.slice();
          var chosen = analysisState.standingsSelection;
          series = series.filter(function (row) {
            return chosen.indexOf(row.tag) !== -1;
          });
        }
        analysisState.standingsPayload = {
          dates: dates,
          series: series,
          range_from: payload.range_from,
          range_to: payload.range_to,
        };
        setExportEnabled("standings", series.length > 0);
        renderStandingsPicker();
        setRangeSelectValue("alliances");
        var pairControls = document.querySelectorAll('[data-range-pair="alliances"]');
        Array.prototype.forEach.call(pairControls, function (el) {
          el.hidden = analysisState.ranges.alliances.mode !== "custom";
        });
        if (analysisState.ranges.alliances.mode === "custom") {
          setPairSelectValues("alliances", analysisState.ranges.alliances.from, analysisState.ranges.alliances.to);
        }
        if (!dates.length) {
          showPanelEmpty(panel, "No data yet.");
        } else if (!series.length) {
          if ((analysisState.standingsTags || []).length) {
            // Tags exist but none are selected/defaulted: keep the picker
            // visible with the hint — the user must be able to choose. The
            // chart card's loading/empty state must go, or it hides the
            // picker (ROADMAP.md §4 picker fix).
            hidePanelEmpty(panel);
            var chartCard = panel.querySelector(".chart-card");
            if (chartCard) {
              chartCard.classList.remove("is-empty");
              var staleState = chartCard.querySelector(".empty-state");
              if (staleState && staleState.parentNode) staleState.parentNode.removeChild(staleState);
            }
            renderStandingsPicker();
            updateStandingsFeedback();
          } else {
            showPanelEmpty(panel, "No data yet.");
          }
        } else {
          hidePanelEmpty(panel);
          renderStandingsChart();
        }
        setPanelBusy("alliances", false);
      })
      .catch(function (err) {
        setPanelBusy("alliances", false);
        showPanelError(panel, "Couldn't load analysis data.", loadStandings);
        activatedTabs.alliances = false;
      });
  };
  if (analysisState.ranges.alliances.mode !== "custom") return request();
  return prepareCustomPair("alliances").then(function (valid) {
    if (!valid) {
      setPanelBusy("alliances", false);
      var errEl = document.getElementById("range-alliances-error");
      if (errEl) {
        errEl.hidden = false;
        setText(errEl, "From must be earlier than To.");
      }
      var card = panel.querySelector(".chart-card");
      if (card) card.hidden = true;
      setExportEnabled("standings", false);
      return;
    }
    var errEl = document.getElementById("range-alliances-error");
    if (errEl) errEl.hidden = true;
    var card = panel.querySelector(".chart-card");
    if (card) card.hidden = false;
    return request();
  });
}

function renderStandingsPicker() {
  var els = analysisElements();
  if (!els.standingsOptions) return;
  var selected = analysisState.standingsSelection || [];
  var needle = els.standingsSearch.value.trim().toLowerCase();
  var payload = analysisState.standingsPayload;
  var truncated = payload && payload.available_truncated;
  var note = document.getElementById("analysis-standings-catalog-note");
  if (note) {
    var noteText = "Top 10 by latest population";
    if (truncated > 0) noteText += " \u00b7 " + truncated + " more on the map";
    setText(note, noteText);
  }
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
  } else if (checked > 10) {
    message = "Select up to 10 alliances.";
  }
  setText(els.standingsFeedback, message);
  els.standingsFeedback.classList.toggle("is-error", message !== "");
  els.standingsApply.disabled = checked === 0 || checked > 10;
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
  var dates = payload.dates || [];
  var series = payload.series || [];
  var metric = analysisState.metric;

  // Textual fallback with the exact payload: one column per series,
  // NEWEST-FIRST (the chart itself stays ASC).
  var tableHeaders = ["Date"].concat(series.map(function (row) {
    return row.tag;
  }));
  var tableRows = dates
    .map(function (d) {
      var cells = [d];
      series.forEach(function (row) {
        var byDate = {};
        (row[metric === "vp" ? "vp_points" : "points"] || []).forEach(function (pair) {
          byDate[pair[0]] = pair[1];
        });
        cells.push(byDate[d] !== undefined ? fmtInt(byDate[d]) : "—");
      });
      return cells;
    })
    .reverse();
  var tableDetails = fillChartDataTable(card, "chart-data-standings", tableHeaders, tableRows);
  var rangeCaption = "Selected range: " + (payload.range_from || "—") + " → " + (payload.range_to || "—");
  var asOfEl = document.querySelector('[data-as-of="standings"]');
  if (asOfEl) {
    asOfEl.hidden = false;
    if (asOfEl.textContent !== rangeCaption) setText(asOfEl, rangeCaption);
  }

  if (!window.Chart) {
    showPanelEmpty(panel, "Chart library unavailable.");
    tableDetails.open = true;
    return;
  }
  card.classList.remove("is-empty");
  var stale = card.querySelector(".empty-state");
  if (stale) stale.remove();
  els.standingsCanvas.setAttribute(
    "aria-label",
    (metric === "vp" ? "Victory points" : "Population") + " over time for selected alliances"
  );
  els.standingsCanvas.setAttribute("aria-describedby", tableDetails.id);

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
  hidePanelError(panel);
  setPanelBusy("players", true);
  return api
    .analysis("players", {})
    .then(function (payload) {
      renderPlayers(payload);
      setPanelBusy("players", false);
    })
    .catch(function (err) {
      setPanelBusy("players", false);
      showPanelError(panel, "Couldn't load analysis data.", loadPlayers);
      activatedTabs.players = false; // next activation retries
    });
}

function renderPlayers(payload) {
  var els = analysisElements();
  analysisState.playersPayload = payload;
  analysisState.playersSnapshot = payload.snapshot_date || "latest";
  setExportEnabled("players", (payload.population || []).length > 0);
  // The players rankings are a LATEST-PAIR view — the toolbar names the
  // pair explicitly (never a fake "range").
  if (els.playersToolbar) {
    setText(
      els.playersToolbar,
      payload.snapshot_date
        ? "Latest snapshot: " + payload.snapshot_date +
          (payload.previous_date ? " vs " + payload.previous_date : "")
        : "Latest snapshot vs previous"
    );
  }
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

//: ONE disabled placeholder option — a dynamic select is never blank.
function setSelectPlaceholder(select, text) {
  select.textContent = "";
  var opt = document.createElement("option");
  opt.disabled = true;
  opt.value = "";
  opt.textContent = text;
  select.appendChild(opt);
}

//: The single DOM path for dynamic <select> contents: ``options`` are
//: (value, label) pairs; an empty list leaves ONE disabled placeholder —
//: never a blank control with a misleading empty selection.
function fillSelectOptions(select, options, emptyText) {
  if (!options || !options.length) {
    setSelectPlaceholder(select, emptyText);
    return;
  }
  select.textContent = "";
  options.forEach(function (opt) {
    var el = document.createElement("option");
    el.value = opt.value;
    el.textContent = opt.label;
    select.appendChild(el);
  });
}

//: Every dynamic select starts with a visible disabled ``Loading…`` option
//: so no control is ever blank during its first request.
function primeDynamicSelects() {
  var selects = [];
  var regionSelect = document.getElementById("analysis-region-select");
  if (regionSelect) selects.push(regionSelect);
  Array.prototype.push.apply(
    selects,
    Array.prototype.slice.call(document.querySelectorAll("[data-range-from], [data-range-to]"))
  );
  var rosterDate = document.getElementById("analysis-roster-date");
  var rosterAlliance = document.getElementById("analysis-roster-alliance");
  if (rosterDate) selects.push(rosterDate);
  if (rosterAlliance) selects.push(rosterAlliance);
  selects.forEach(function (select) {
    setSelectPlaceholder(select, "Loading\u2026");
  });
}

function fillDateSelect(select, dates, emptyText) {
  fillSelectOptions(
    select,
    (dates || []).map(function (d) {
      return { value: d, label: d };
    }),
    emptyText || "No snapshots available"
  );
}

function setEventsBusy(busy) {
  var els = analysisElements();
  els.eventsFrom.disabled = busy;
  els.eventsTo.disabled = busy;
}

function loadEvents() {
  var panel = document.getElementById("panel-events");
  var els = analysisElements();
  hidePanelError(panel);
  setPanelBusy("events", true);
  setEventsBusy(true);
  return api
    .analysis("dates")
    .then(function (payload) {
      var dates = payload.dates || [];
      fillDateSelect(els.eventsFrom, dates, "Need at least two snapshots");
      fillDateSelect(els.eventsTo, dates, "Need at least two snapshots");
      if (dates.length < 2) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("events", false);
        setEventsBusy(false);
        return;
      }
      hidePanelEmpty(panel);
      setRangeSelectValue("events");
      var r = analysisState.ranges.events;
      if (r.mode === "custom" && (dates.indexOf(r.from) === -1 || dates.indexOf(r.to) === -1)) {
        revertStaleCustom("events");
        setRangeSelectValue("events");
      }
      var pair = tabPair("events", dates);
      if (!pair) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("events", false);
        setEventsBusy(false);
        return;
      }
      els.eventsFrom.value = pair[0];
      els.eventsTo.value = pair[1];
      analysisState.ranges.events.from = pair[0];
      analysisState.ranges.events.to = pair[1];
      return fetchEvents(pair[0], pair[1]);
    })
    .then(function () {
      setEventsBusy(false);
      setPanelBusy("events", false);
    })
    .catch(function (err) {
      setPanelBusy("events", false);
      setEventsBusy(false);
      showPanelError(panel, "Couldn't load analysis data.", loadEvents);
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
  } else if (ev.same_player) {
    // The owner switched alliances (stable player_id) — the village was
    // NOT conquered, so the line never says "conquered".
    meta.textContent = "Alliance changed to " + (ev.owner_tag || ev.owner_player || "unknown");
  } else {
    meta.textContent = "conquered by " + (ev.owner_tag || ev.owner_player || "unknown");
  }
  li.appendChild(meta);
  return li;
}

/* Wars tab */

function loadWars() {
  var panel = document.getElementById("panel-wars");
  var els = analysisElements();
  hidePanelError(panel);
  setPanelBusy("wars", true);
  setWarsBusy(true);
  return api
    .analysis("dates")
    .then(function (payload) {
      var dates = payload.dates || [];
      fillDateSelect(els.warsFrom, dates, "Need at least two snapshots");
      fillDateSelect(els.warsTo, dates, "Need at least two snapshots");
      if (dates.length < 2) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("wars", false);
        setWarsBusy(false);
        return;
      }
      hidePanelEmpty(panel);
      setRangeSelectValue("wars");
      var r = analysisState.ranges.wars;
      if (r.mode === "custom" && (dates.indexOf(r.from) === -1 || dates.indexOf(r.to) === -1)) {
        revertStaleCustom("wars");
        setRangeSelectValue("wars");
      }
      var pair = tabPair("wars", dates);
      if (!pair) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("wars", false);
        setWarsBusy(false);
        return;
      }
      els.warsFrom.value = pair[0];
      els.warsTo.value = pair[1];
      analysisState.ranges.wars.from = pair[0];
      analysisState.ranges.wars.to = pair[1];
      return fetchWars(pair[0], pair[1]);
    })
    .then(function () {
      setWarsBusy(false);
      setPanelBusy("wars", false);
    })
    .catch(function (err) {
      setPanelBusy("wars", false);
      setWarsBusy(false);
      showPanelError(panel, "Couldn't load analysis data.", loadWars);
      activatedTabs.wars = false;
    });
}

function setWarsBusy(busy) {
  var els = analysisElements();
  els.warsFrom.disabled = busy;
  els.warsTo.disabled = busy;
}

function fetchWars(from, to) {
  return api
    .analysis("wars", { from: from, to: to })
    .then(function (payload) {
      renderWars(payload, from, to);
    });
}

function renderWars(payload, from, to) {
  var els = analysisElements();
  var pairs = payload.pairs || [];
  var deleted = payload.deleted || [];
  analysisState.warsPayload = payload;
  setExportEnabled("wars", pairs.length + deleted.length > 0);
  els.warsMatrixHead.textContent = "";
  els.warsMatrixBody.textContent = "";
  els.warsEntries.textContent = "";
  els.warsDeleted.textContent = "";
  setText(els.warsDeletedCount, String(deleted.length));

  if (!pairs.length && !deleted.length) {
    els.warsMatrix.hidden = true;
    els.warsDetail.hidden = true;
    els.warsEmpty.hidden = false;
    setText(els.warsEmpty, "No conquests or deleted villages between " + from + " and " + to + ".");
    return;
  }
  els.warsEmpty.hidden = true;
  els.warsMatrix.hidden = pairs.length === 0;
  els.warsDetail.hidden = false;

  var tags = payload.tracked_tags || [];
  var byPair = {};
  pairs.forEach(function (pair) {
    byPair[pair.from_tag + "\u0000" + pair.to_tag] = pair;
  });

  // Matrix head: empty corner + one column per tracked tag.
  var headRow = document.createElement("tr");
  var corner = document.createElement("th");
  corner.scope = "col";
  corner.textContent = "From \\ To";
  headRow.appendChild(corner);
  tags.forEach(function (tag) {
    var th = document.createElement("th");
    th.scope = "col";
    th.textContent = tag;
    headRow.appendChild(th);
  });
  els.warsMatrixHead.appendChild(headRow);

  // Body: one row per tracked tag; cells are buttons when a pair has events.
  tags.forEach(function (fromTag) {
    var tr = document.createElement("tr");
    var label = document.createElement("th");
    label.scope = "row";
    label.textContent = fromTag;
    tr.appendChild(label);
    tags.forEach(function (toTag) {
      var td = document.createElement("td");
      var pair = byPair[fromTag + "\u0000" + toTag];
      if (pair && pair.villages > 0) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "wars-cell";
        btn.textContent = String(pair.villages);
        btn.title = pair.population + " population";
        btn.addEventListener("click", function () {
          showWarsPair(pair);
        });
        td.appendChild(btn);
      } else {
        td.textContent = "\u2013";
      }
      tr.appendChild(td);
    });
    els.warsMatrixBody.appendChild(tr);
  });

  // Detail defaults to the first pair (deterministic order).
  showWarsPair(pairs[0]);

  deleted.forEach(function (ev) {
    els.warsDeleted.appendChild(warsLine(ev));
  });
}

function showWarsPair(pair) {
  var els = analysisElements();
  setText(
    els.warsDetailTitle,
    pair.from_tag + " \u2192 " + pair.to_tag + " \u2014 " + pair.villages + " villages, " + pair.population + " pop"
  );
  setText(els.warsDetailCount, String(pair.villages));
  els.warsEntries.textContent = "";
  pair.entries.forEach(function (ev) {
    els.warsEntries.appendChild(warsLine(ev));
  });
}

function warsLine(ev) {
  var li = document.createElement("li");
  li.className = "event-line event-line--lost";

  // Same semantic button as the Events tab: opens the village's history.
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
  if (ev.from_tag !== undefined && ev.to_tag !== undefined) {
    // The aggregate title above already states FROM → TO — the row itself
    // shows the village, coordinates and the player only (the direction
    // never repeats per row).
    meta.textContent = ev.to_player || ev.from_player || "unknown";
  } else {
    meta.textContent = "deleted \u00b7 " + (ev.from_player || "unknown");
  }
  li.appendChild(meta);
  return li;
}

/* Changes tab */

function loadChanges() {
  var panel = document.getElementById("panel-changes");
  var els = analysisElements();
  hidePanelError(panel);
  setPanelBusy("changes", true);
  tableLoading(els.changesBody, 9);
  var request = function () {
    return api
      .analysis("deltas", rangeParams("changes"))
      .then(function (payload) {
        var rows = payload.rows || [];
        analysisState.changesPayload = payload;
        setExportEnabled("changes", rows.length > 0);
        setRangeSelectValue("changes");
        var pairControls = document.querySelectorAll('[data-range-pair="changes"]');
        Array.prototype.forEach.call(pairControls, function (el) {
          el.hidden = analysisState.ranges.changes.mode !== "custom";
        });
        if (analysisState.ranges.changes.mode === "custom") {
          setPairSelectValues("changes", analysisState.ranges.changes.from, analysisState.ranges.changes.to);
        }
        if (els.changesToolbar) {
          setText(
            els.changesToolbar,
            payload.range_from && payload.range_to
              ? "Selected range: " + payload.range_from + " \u2192 " + payload.range_to
              : "Daily headline history"
          );
        }
        if (!rows.length) {
          showPanelEmpty(panel, "No data yet.");
          setPanelBusy("changes", false);
          return;
        }
        hidePanelEmpty(panel);
        els.changesBody.textContent = "";
        // Newest observation first — the chart payload stays ASC, the
        // table is a DESC presentation of the same data.
        rows.slice().reverse().forEach(function (row) {
          els.changesBody.appendChild(changeRow(row));
        });
        setPanelBusy("changes", false);
      })
      .catch(function (err) {
        setPanelBusy("changes", false);
        showPanelError(panel, "Couldn't load analysis data.", loadChanges);
        activatedTabs.changes = false;
      });
  };
  if (analysisState.ranges.changes.mode !== "custom") return request();
  return prepareCustomPair("changes").then(function (valid) {
    if (!valid) {
      setPanelBusy("changes", false);
      var errEl = document.getElementById("range-changes-error");
      if (errEl) {
        errEl.hidden = false;
        setText(errEl, "From must be earlier than To.");
      }
      els.changesBody.textContent = "";
      setExportEnabled("changes", false);
      return;
    }
    var errEl = document.getElementById("range-changes-error");
    if (errEl) errEl.hidden = true;
    return request();
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
  hidePanelError(document.getElementById("panel-villages"));
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
      var panel = document.getElementById("panel-villages");
      if (panel) showPanelError(panel, "Village search failed.", function () {
        return requestVillages((analysisElements().villagesInput.value || "").trim());
      });
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
  // Newest observation first (the chart stays ASC — table vs trend axis).
  history.slice().reverse().forEach(function (point) {
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
  var labels = history.map(function (p) {
    return p.snapshot_date;
  });
  var data = history.map(function (p) {
    return p.population;
  });
  // Textual fallback from the exact history payload.
  var tableDetails = fillChartDataTable(
    els.villageChartCard,
    "chart-data-village",
    ["Snapshot", "Population"],
    history.map(function (p) {
      return [p.snapshot_date, fmtInt(p.population)];
    })
  );
  var asOf = labels.length ? labels[labels.length - 1] : null;
  var asOfEl = document.querySelector('[data-as-of="village"]');
  if (asOfEl) {
    asOfEl.hidden = !asOf;
    if (asOf) setText(asOfEl, "as of " + asOf);
  }
  if (!window.Chart) {
    els.villageChartCard.hidden = false;
    els.villageDetailNote.hidden = false;
    setText(els.villageDetailNote, "Chart library unavailable.");
    tableDetails.open = true;
    return;
  }
  els.villageChartCard.hidden = false;
  els.villageCanvas.setAttribute("aria-label", "Population history for " + name);
  els.villageCanvas.setAttribute("aria-describedby", tableDetails.id);
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
      // CSV mirrors the on-screen table: newest observation first.
      var newest = rows[rows.length - 1];
      var headers = ["Date", "Previous snapshot", "Days elapsed", "Villages", "Villages Δ", "Population", "Population Δ", "Players", "Players Δ", "VP", "VP Δ"];
      exportCsv(
        "changes-" + newest.date + ".csv",
        headers,
        rows.slice().reverse().map(function (r) {
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
        "events-" + analysisState.ranges.events.from + "-" + analysisState.ranges.events.to + "-limit-" + analysisState.eventsLimit + ".csv",
        headers,
        rows
      );
    },
    wars: function () {
      var payload = analysisState.warsPayload;
      if (!payload || (!payload.pairs.length && !payload.deleted.length)) return;
      var headers = ["Event", "From", "To", "Village", "Coordinates", "Region", "From player", "To player", "Population"];
      var rows = [];
      (payload.pairs || []).forEach(function (pair) {
        pair.entries.forEach(function (e) {
          rows.push(["conquest", e.from_tag, e.to_tag, e.village_name, e.x + "|" + e.y, e.region || "", e.from_player, e.to_player, e.population]);
        });
      });
      (payload.deleted || []).forEach(function (e) {
        rows.push(["deleted", e.from_tag, "", e.village_name, e.x + "|" + e.y, e.region || "", e.from_player, "", e.population]);
      });
      exportCsv(
        "wars-" + analysisState.ranges.wars.from + "-" + analysisState.ranges.wars.to + ".csv",
        headers,
        rows
      );
    },
    players: function () {
      var payload = analysisState.playersPayload;
      if (!payload || !payload.population.length) return;
      var snapshot = analysisState.playersSnapshot || "latest";
      var headers = ["Rank", "Player", "Villages", "Population", "Growth", "VP", "New villages"];
      var byName = {};
      (payload.population || []).forEach(function (s, i) {
        byName[s.player_id] = { rank: i + 1, player_id: s.player_id, player_name: s.player_name, villages: s.villages, population: s.population };
      });
      (payload.growth || []).forEach(function (s) {
        if (byName[s.player_id]) byName[s.player_id].growth = s.growth;
      });
      (payload.new_villages || []).forEach(function (s) {
        if (byName[s.player_id]) byName[s.player_id].gains = s.gains;
      });
      (payload.vp || []).forEach(function (s) {
        if (byName[s.player_id]) byName[s.player_id].vp = s.vp;
      });
      var rows = Object.keys(byName).map(function (pid) {
        var s = byName[pid];
        return [s.rank, s.player_name, s.villages, s.population, s.growth === null || s.growth === undefined ? "" : s.growth, s.vp === undefined ? "" : s.vp, s.gains === undefined ? "" : s.gains];
      });
      exportCsv("players-" + snapshot + ".csv", headers, rows);
    },
    standings: function () {
      var payload = analysisState.standingsPayload;
      if (!payload || !payload.series.length) return;
      var snapshot = (payload.dates && payload.dates.length) ? payload.dates[payload.dates.length - 1] : "latest";
      var headers = ["Date"].concat(payload.series.map(function (s) { return s.tag; }));
      var byTag = {};
      payload.series.forEach(function (s) {
        byTag[s.tag] = s;
      });
      var rows = (payload.dates || []).slice().reverse().map(function (d) {
        var cells = [d];
        payload.series.forEach(function (s) {
          var byDate = {};
          (s.points || []).forEach(function (pair) { byDate[pair[0]] = pair[1]; });
          cells.push(byDate[d] !== undefined ? byDate[d] : "");
        });
        return cells;
      });
      exportCsv("standings-" + snapshot + ".csv", headers, rows);
    },
    deltas: function () {
      var payload = analysisState.changesPayload;
      var rows = payload && payload.rows ? payload.rows : [];
      if (!rows.length) return;
      // Same DESC order as the screen (newest observation first).
      var newest = rows[rows.length - 1];
      var headers = ["Date", "Previous snapshot", "Days elapsed", "Villages", "Villages Δ", "Population", "Population Δ", "Players", "Players Δ", "VP", "VP Δ"];
      exportCsv(
        "deltas-" + newest.date + ".csv",
        headers,
        rows.slice().reverse().map(function (r) {
          return [r.date, r.previous_date, r.elapsed_days, r.villages, r.villages_delta, r.population, r.population_delta, r.players, r.players_delta, r.vp, r.vp_delta];
        })
      );
    },
    roster: function () {
      var payload = analysisState.rosterPayload;
      if (!payload || !payload.players.length) return;
      var headers = ["Player", "Alliance", "Villages", "Population", "Growth", "VP"];
      var rows = payload.players.map(function (p) {
        return [
          p.player_name,
          p.alliance_tag || "",
          p.villages,
          p.population,
          p.growth === null || p.growth === undefined ? "" : p.growth,
          p.vp,
        ];
      });
      exportCsv("roster-" + payload.snapshot_date + "-" + payload.alliance + ".csv", headers, rows);
    },
    compare: function () {
      var payload = analysisState.comparePayload;
      if (!payload || !payload.regions.length) return;
      var headers = ["Region", "Share from", "Share to", "Δ share", "Pop from", "Pop to", "Δ pop"];
      var rows = payload.regions.map(function (r) {
        return [
          r.region,
          r.from_share === null || r.from_share === undefined ? "" : (r.from_share * 100).toFixed(1) + "%",
          (r.to_share * 100).toFixed(1) + "%",
          r.share_delta === null || r.share_delta === undefined ? "" : (r.share_delta * 100).toFixed(1) + "%",
          r.from_pop === null || r.from_pop === undefined ? "" : r.from_pop,
          r.to_pop,
          r.pop_delta === null || r.pop_delta === undefined ? "" : r.pop_delta,
        ];
      });
      exportCsv("deltas-" + payload.from + "-" + payload.to + ".csv", headers, rows);
    },
  };
  document.querySelectorAll("[data-export]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var exporter = exporters[btn.getAttribute("data-export")];
      if (exporter) exporter();
    });
  });
}


/* --- Watch tab ---------------------------------------------------------------- */

function loadWatch() {
  var panel = document.getElementById("panel-watch");
  var els = analysisElements();
  hidePanelError(panel);
  setPanelBusy("watch", true);
  return api
    .analysis("dates")
    .then(function (payload) {
      var dates = payload.dates || [];
      fillDateSelect(els.watchFrom, dates, "Need at least two snapshots");
      fillDateSelect(els.watchTo, dates, "Need at least two snapshots");
      if (dates.length < 2) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("watch", false);
        return;
      }
      hidePanelEmpty(panel);
      setRangeSelectValue("watch");
      var r = analysisState.ranges.watch;
      if (r.mode === "custom" && (dates.indexOf(r.from) === -1 || dates.indexOf(r.to) === -1)) {
        revertStaleCustom("watch");
        setRangeSelectValue("watch");
      }
      var pair = tabPair("watch", dates);
      if (!pair) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("watch", false);
        return;
      }
      els.watchFrom.value = pair[0];
      els.watchTo.value = pair[1];
      analysisState.ranges.watch.from = pair[0];
      analysisState.ranges.watch.to = pair[1];
      return fetchWatch(pair[0], pair[1]);
    })
    .then(function () {
      setPanelBusy("watch", false);
    })
    .catch(function () {
      setPanelBusy("watch", false);
      showPanelError(panel, "Couldn't load analysis data.", loadWatch);
      activatedTabs.watch = false;
    });
}

function fetchWatch(from, to) {
  var els = analysisElements();
  return api.analysis("watch", { from: from, to: to, limit: 500 }).then(function (payload) {
    // Severity/kind are client-side filters over the full payload: the
    // counters stay honest (X of Y items).
    var severity = els.watchSeverity.value;
    var kind = els.watchKind.value;
    var items = (payload.items || []).filter(function (item) {
      if (severity && item.severity !== severity) return false;
      if (kind && item.kind !== kind) return false;
      return true;
    });
    renderWatch({ from: payload.from, to: payload.to, total: payload.total, items: items });
  });
}

function watchCountLabel(payload) {
  var counts = { info: 0, warning: 0 };
  (payload.items || []).forEach(function (item) {
    counts[item.severity] = (counts[item.severity] || 0) + 1;
  });
  var label = payload.total + " item" + (payload.total === 1 ? "" : "s");
  if (payload.items.length !== payload.total) {
    label = payload.items.length + " of " + payload.total + " item" + (payload.total === 1 ? "" : "s");
  }
  return label + " · " + counts.warning + " warning" + (counts.warning === 1 ? "" : "s") +
    " · " + counts.info + " info";
}

function renderWatch(payload) {
  var els = analysisElements();
  var items = payload.items || [];
  setText(els.watchCounts, watchCountLabel(payload));
  setText(els.watchRange, "Comparing " + payload.from + " → " + payload.to + ".");
  els.watchRange.hidden = false;
  els.watchEmpty.hidden = items.length > 0;
  setText(els.watchEmpty, "No village movement between " + payload.from + " and " + payload.to + ".");
  els.watchList.textContent = "";
  items.forEach(function (item) {
    var li = document.createElement("li");
    li.className = "watch-item watch-item--" + item.severity;

    var severity = document.createElement("span");
    severity.className = "watch-item__severity";
    severity.textContent = item.severity === "warning" ? "WRN" : "INF";
    li.appendChild(severity);

    var kind = document.createElement("span");
    kind.className = "watch-item__kind";
    kind.textContent = item.kind;
    li.appendChild(kind);

    var name = document.createElement("button");
    name.type = "button";
    name.className = "event-line__name";
    name.textContent = item.village_name;
    name.setAttribute("aria-label", "Open history for " + item.village_name);
    name.addEventListener("click", function () {
      openVillageHistory(item.village_id, item.village_name);
    });
    li.appendChild(name);

    if (item.region) {
      var region = document.createElement("button");
      region.type = "button";
      region.className = "watch-item__region";
      region.textContent = "— " + item.region;
      region.setAttribute("aria-label", "Open villages of " + item.region);
      region.addEventListener("click", function () {
        openRegionVillages(item.region);
      });
      li.appendChild(region);
    }

    var tags = document.createElement("span");
    tags.className = "watch-item__tags mono";
    if (item.from_tag && item.to_tag) {
      tags.textContent = item.from_tag + " → " + item.to_tag;
    } else if (item.to_tag) {
      tags.textContent = "→ " + item.to_tag;
    } else if (item.from_tag) {
      tags.textContent = item.from_tag + " → —";
    }
    li.appendChild(tags);

    var message = document.createElement("span");
    message.className = "watch-item__message";
    message.textContent = item.message || "";
    li.appendChild(message);

    els.watchList.appendChild(li);
  });
}

function wireWatchControls() {
  var els = analysisElements();
  function onChange() {
    var from = els.watchFrom.value;
    var to = els.watchTo.value;
    if (!from || !to) return;
    if (from >= to) {
      setText(els.watchError, "From must be earlier than To.");
      els.watchError.hidden = false;
      els.watchList.textContent = "";
      els.watchRange.hidden = true;
      return;
    }
    els.watchError.hidden = true;
    // A touched pair is a custom range for this tab.
    analysisState.ranges.watch = { mode: "custom", days: 7, from: from, to: to };
    syncAnalysisUrl();
    setRangeSelectValue("watch");
    setPanelBusy("watch", true);
    fetchWatch(from, to)
      .catch(function (err) {
        showToast("Watch refresh failed", err.message, "error");
      })
      .then(function () {
        setPanelBusy("watch", false);
      });
  }
  function onRangeChange() {
    var value = document.querySelector('[data-range-select="watch"]').value;
    if (value === "custom") {
      // Enter custom with the last valid pair as the starting point.
      var r = analysisState.ranges.watch;
      r.mode = "custom";
      syncAnalysisUrl();
      setRangeSelectValue("watch");
      return;
    }
    var days = Number(value);
    analysisState.ranges.watch = { mode: "days", days: days === 30 || days === 60 ? days : 7, from: null, to: null };
    syncAnalysisUrl();
    loadWatch();
  }
  els.watchFrom.addEventListener("change", onChange);
  els.watchTo.addEventListener("change", onChange);
  els.watchSeverity.addEventListener("change", onChange);
  els.watchKind.addEventListener("change", onChange);
  var rangeSelect = document.querySelector('[data-range-select="watch"]');
  if (rangeSelect) rangeSelect.addEventListener("change", onRangeChange);
}

/* --- Roster tab --------------------------------------------------------------- */

function loadRoster() {
  var panel = document.getElementById("panel-roster");
  var els = analysisElements();
  hidePanelError(panel);
  setPanelBusy("roster", true);
  return api
    .analysis("dates")
    .then(function (payload) {
      var dates = payload.dates || [];
      fillDateSelect(els.rosterDate, dates);
      if (!dates.length) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("roster", false);
        return;
      }
      hidePanelEmpty(panel);
      els.rosterDate.value = dates[dates.length - 1];
      fillAllianceSelect(els.rosterAlliance, allianceTags);
      return fetchRoster();
    })
    .then(function () {
      setPanelBusy("roster", false);
    })
    .catch(function () {
      setPanelBusy("roster", false);
      showPanelError(panel, "Couldn't load analysis data.", loadRoster);
      activatedTabs.roster = false;
    });
}

function fillAllianceSelect(select, tags) {
  select.textContent = "";
  var combined = document.createElement("option");
  combined.value = "combined";
  combined.textContent = "Combined";
  select.appendChild(combined);
  tags.forEach(function (tag) {
    var opt = document.createElement("option");
    opt.value = tag;
    opt.textContent = tag;
    select.appendChild(opt);
  });
}

function fetchRoster() {
  var els = analysisElements();
  return api
    .analysis("roster", {
      date: els.rosterDate.value,
      alliance: els.rosterAlliance.value,
      limit: 200,
    })
    .then(function (payload) {
      renderRoster(payload);
    });
}

function renderRoster(payload) {
  var els = analysisElements();
  var players = payload.players || [];
  analysisState.rosterPayload = payload;
  setExportEnabled("roster", players.length > 0);
  els.rosterNote.hidden = false;
  setText(
    els.rosterNote,
    payload.total + " player" + (payload.total === 1 ? "" : "s") +
      " on " + payload.snapshot_date + " · showing " + players.length
  );
  els.rosterEmpty.hidden = players.length > 0;
  setText(els.rosterEmpty, "No players for this alliance on " + payload.snapshot_date + ".");
  els.rosterBody.textContent = "";
  players.forEach(function (player) {
    var tr = document.createElement("tr");
    var tdName = document.createElement("td");
    var name = document.createElement("button");
    name.type = "button";
    name.className = "event-line__name";
    name.textContent = player.player_name;
    name.setAttribute("aria-label", "Open history for " + player.player_name);
    name.addEventListener("click", function () {
      openPlayerHistory(player.player_id, player.player_name);
    });
    tdName.appendChild(name);
    tr.appendChild(tdName);
    tr.appendChild(numCell(player.alliance_tag || "—"));
    tr.appendChild(numCell(fmtInt(player.villages)));
    tr.appendChild(numCell(fmtInt(player.population)));
    tr.appendChild(deltaCell(player.growth, null, null));
    tr.appendChild(numCell(fmtInt(player.vp)));
    els.rosterBody.appendChild(tr);
  });
}

function wireRosterControls() {
  var els = analysisElements();
  function onChange() {
    els.rosterError.hidden = true;
    setPanelBusy("roster", true);
    fetchRoster()
      .catch(function (err) {
        els.rosterError.hidden = false;
        setText(els.rosterError, err.message || "Couldn't load the roster.");
        els.rosterBody.textContent = "";
      })
      .then(function () {
        setPanelBusy("roster", false);
      });
  }
  els.rosterDate.addEventListener("change", onChange);
  els.rosterAlliance.addEventListener("change", onChange);
}

/* Tab bar + wiring */

/* --- context bar + URL state (Faza 3) -------------------------------------- */
//
// The Intelligence context: alliance, the active tab, and ONE range per
// historical tab live in the URL query (?view=&tab=&alliance=&range_<tab>=
// &from_<tab>=&to_<tab>=) and the local preference key (v2, per-tab) —
// never near the token. Invalid values are rejected to safe defaults (7
// days) and never cause a request loop.

var RANGE_TABS = ["regions", "alliances", "changes", "events", "wars", "compare", "watch"];

export function syncAnalysisUrl() {
  var params = new URLSearchParams(window.location.search);
  var view = state.dashboardState.activeView;
  if (view !== "intelligence") {
    params.set("view", view);
  } else {
    params.delete("view");
  }
  if (activeTabName && activeTabName !== "regions") {
    params.set("tab", activeTabName);
  } else {
    params.delete("tab");
  }
  if (analysisState.alliance !== "combined") {
    params.set("alliance", analysisState.alliance);
  } else {
    params.delete("alliance");
  }
  RANGE_TABS.forEach(function (tab) {
    var r = analysisState.ranges[tab];
    if (r.mode === "custom") {
      params.set("range_" + tab, "custom");
      params.set("from_" + tab, r.from || "");
      params.set("to_" + tab, r.to || "");
    } else if (r.days !== 7) {
      params.set("range_" + tab, String(r.days));
      params.delete("from_" + tab);
      params.delete("to_" + tab);
    } else {
      params.delete("range_" + tab);
      params.delete("from_" + tab);
      params.delete("to_" + tab);
    }
  });
  var qs = params.toString();
  var target = window.location.pathname + (qs ? "?" + qs : "");
  history.replaceState(null, "", target);
  try {
    window.localStorage.setItem(
      "mufon.dashboard.view.v2",
      JSON.stringify({ alliance: analysisState.alliance, ranges: analysisState.ranges })
    );
  } catch (_e) {
    /* preference is best-effort; the URL is the source of truth */
  }
}

function setTabRange(tab, mode, days) {
  analysisState.ranges[tab] = { mode: mode, days: days, from: null, to: null };
  syncAnalysisUrl();
  // The range shapes only THIS tab: reload it now, leave every other tab
  // untouched.
  if (activatedTabs[tab] && tabLoaders[tab]) {
    analysisState.dirtyTabs[tab] = false;
    tabLoaders[tab]();
  }
}

//: One shared range controller per historical tab (replaces the old global
//: days select): 7|30|60|Custom presets, plus the tab's custom pair.
function wireRangeControls(tab) {
  var select = document.querySelector('[data-range-select="' + tab + '"]');
  if (!select) return;
  select.addEventListener("change", function () {
    var value = select.value;
    if (value === "custom") {
      var r = analysisState.ranges[tab];
      r.mode = "custom";
      syncAnalysisUrl();
      // Reveal the pair controls DISABLED, then fill them from the real
      // date catalog: the pair selects are re-enabled only after their
      // options exist (a blank/enabled pair is never shown).
      var fromEl = document.querySelector('[data-range-from="' + tab + '"]');
      var toEl = document.querySelector('[data-range-to="' + tab + '"]');
      var pairControls = document.querySelectorAll('[data-range-pair="' + tab + '"]');
      Array.prototype.forEach.call(pairControls, function (el) {
        el.hidden = false;
      });
      if (fromEl) fromEl.disabled = true;
      if (toEl) toEl.disabled = true;
      api.analysis("dates").then(function (payload) {
        var dates = payload.dates || [];
        fillPairSelects(tab, dates, "Need at least two snapshots");
        if (fromEl) fromEl.disabled = false;
        if (toEl) toEl.disabled = false;
        // Fewer than two snapshots: the disabled placeholder stays and the
        // existing no-data state is kept — never a blank selection.
        if (dates.length < 2) return;
        if (!r.from || dates.indexOf(r.from) === -1 || !r.to || dates.indexOf(r.to) === -1) {
          r.from = dates[Math.max(0, dates.length - 7)];
          r.to = dates[dates.length - 1];
        }
        setPairSelectValues(tab, r.from, r.to);
        reloadTab(tab);
      });
      return;
    }
    var days = Number(value);
    setTabRange(tab, "days", days === 30 || days === 60 ? days : 7);
  });
}

//: Custom pair selectors for the series tabs (regions/alliances/changes) —
//: events/wars/compare/watch wire their existing From/To in their own
//: control functions.
function wirePairSelects(tab) {
  var fromEl = document.querySelector('[data-range-from="' + tab + '"]');
  var toEl = document.querySelector('[data-range-to="' + tab + '"]');
  if (!fromEl || !toEl) return;
  function onChange() {
    var from = fromEl.value;
    var to = toEl.value;
    if (!from || !to) return;
    analysisState.ranges[tab].mode = "custom";
    analysisState.ranges[tab].from = from;
    analysisState.ranges[tab].to = to;
    syncAnalysisUrl();
    var errEl = document.getElementById("range-" + tab + "-error");
    if (from >= to) {
      if (errEl) {
        errEl.hidden = false;
        setText(errEl, "From must be earlier than To.");
      }
      // The stale table must not contradict the message: clear it.
      var panel = document.getElementById("panel-" + tab);
      var body = panel && panel.querySelector("tbody");
      if (body) body.textContent = "";
      var card = panel && panel.querySelector(".chart-card");
      if (card) card.hidden = true;
      setExportEnabled(tab === "alliances" ? "standings" : tab, false);
      return;
    }
    if (errEl) errEl.hidden = true;
    reloadTab(tab);
  }
  fromEl.addEventListener("change", onChange);
  toEl.addEventListener("change", onChange);
}

//: Bootstrap entry: apply URL + stored preference context before the first
//: analysis load. Invalid range values fall back to the 7-day default; an
//: invalid alliance is reset by renderAllianceFilter once tags resolve.
//: Custom pairs are validated against the dates list at load time (a stale
//: pair reverts to 7 — never a request loop).
export function applyInitialContext() {
  var params = new URLSearchParams(window.location.search);
  var allianceRaw = params.get("alliance");
  var stored = null;
  try {
    stored = JSON.parse(window.localStorage.getItem("mufon.dashboard.view.v2") || "null");
  } catch (_e) {
    stored = null;
  }
  if (allianceRaw !== null) {
    analysisState.alliance = allianceRaw === "combined" ? "combined" : allianceRaw;
  } else if (stored && stored.alliance) {
    analysisState.alliance = stored.alliance;
  }
  RANGE_TABS.forEach(function (tab) {
    var r = analysisState.ranges[tab];
    var modeRaw = params.get("range_" + tab);
    var fromRaw = params.get("from_" + tab);
    var toRaw = params.get("to_" + tab);
    var storedRange = stored && stored.ranges && stored.ranges[tab];
    if (modeRaw === "custom") {
      if (fromRaw && toRaw) {
        r.mode = "custom";
        r.from = fromRaw;
        r.to = toRaw;
      }
      // Incomplete custom values stay on the 7-day default.
    } else if (modeRaw !== null) {
      var days = Number(modeRaw);
      r.mode = "days";
      r.days = days === 30 || days === 60 ? days : 7;
    } else if (storedRange) {
      if (storedRange.mode === "custom" && storedRange.from && storedRange.to) {
        r.mode = "custom";
        r.from = storedRange.from;
        r.to = storedRange.to;
      } else {
        r.mode = "days";
        r.days = storedRange.days === 30 || storedRange.days === 60 ? storedRange.days : 7;
      }
    }
  });
}

/* --- Compare tab ------------------------------------------------------------- */

function loadCompare() {
  var panel = document.getElementById("panel-compare");
  var els = analysisElements();
  hidePanelError(panel);
  setPanelBusy("compare", true);
  return api
    .analysis("dates")
    .then(function (payload) {
      var dates = payload.dates || [];
      fillDateSelect(els.compareFrom, dates, "Need at least two snapshots");
      fillDateSelect(els.compareTo, dates, "Need at least two snapshots");
      if (dates.length < 2) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("compare", false);
        return;
      }
      hidePanelEmpty(panel);
      setRangeSelectValue("compare");
      var r = analysisState.ranges.compare;
      if (r.mode === "custom" && (dates.indexOf(r.from) === -1 || dates.indexOf(r.to) === -1)) {
        revertStaleCustom("compare");
        setRangeSelectValue("compare");
      }
      var pair = tabPair("compare", dates);
      if (!pair) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("compare", false);
        return;
      }
      els.compareFrom.value = pair[0];
      els.compareTo.value = pair[1];
      analysisState.ranges.compare.from = pair[0];
      analysisState.ranges.compare.to = pair[1];
      syncAnalysisUrl();
      return fetchCompare(pair[0], pair[1]);
    })
    .then(function () {
      setPanelBusy("compare", false);
    })
    .catch(function () {
      setPanelBusy("compare", false);
      showPanelError(panel, "Couldn't load analysis data.", loadCompare);
      activatedTabs.compare = false;
    });
}

function fetchCompare(from, to) {
  return api
    .analysis("compare", { from: from, to: to })
    .then(function (payload) {
      renderCompare(payload);
    });
}

function deltaCellText(d) {
  if (d === null || d === undefined) return "\u2014";
  if (d > 0) return "+" + fmtInt(d);
  if (d < 0) return "\u2212" + fmtInt(Math.abs(d));
  return "\u00b10";
}

function renderCompare(payload) {
  var els = analysisElements();
  analysisState.comparePayload = payload;
  var regions = payload.regions || [];
  setExportEnabled("compare", regions.length > 0);
  var summary = payload.summary || {};
  var to = summary.to || {};
  var delta = summary.delta || {};
  var from = summary.from || {};

  if (!regions.length) {
    els.compareSummary.hidden = true;
    els.compareTable.hidden = true;
    els.compareRangeNote.hidden = true;
    els.compareMovement.hidden = true;
    els.compareEmpty.hidden = false;
    setText(els.compareEmpty, "No regions to compare between " + payload.from + " and " + payload.to + ".");
    return;
  }
  els.compareEmpty.hidden = true;
  els.compareSummary.hidden = false;
  els.compareTable.hidden = false;
  els.compareRangeNote.hidden = false;
  setText(
    els.compareRangeNote,
    "Comparing " + payload.from + " \u2192 " + payload.to + " (" + payload.elapsed_days + " day" + (payload.elapsed_days === 1 ? "" : "s") + ")."
  );

  setText(els.compareVillages, fmtInt(to.villages));
  setText(els.compareVillagesDelta, deltaCellText(delta.villages));
  setText(els.comparePopulation, fmtInt(to.population));
  setText(els.comparePopulationDelta, deltaCellText(delta.population));
  setText(els.comparePlayers, fmtInt(to.players));
  setText(els.comparePlayersDelta, deltaCellText(delta.players));
  setText(els.compareVp, fmtInt(to.vp));
  setText(els.compareVpDelta, deltaCellText(delta.vp));

  els.compareBody.textContent = "";
  regions.forEach(function (r) {
    var tr = document.createElement("tr");
    var tdRegion = document.createElement("td");
    tdRegion.className = "region-name";
    tdRegion.textContent = r.region;
    tr.appendChild(tdRegion);
    tr.appendChild(numCell(r.from_share === null || r.from_share === undefined ? "\u2014" : (r.from_share * 100).toFixed(1) + "%"));
    tr.appendChild(numCell((r.to_share * 100).toFixed(1) + "%"));
    var tdShare = numCell("");
    var sd = r.share_delta;
    if (sd !== null && sd !== undefined) {
      if (Math.abs(sd) < 0.0005) {
        tdShare.textContent = "\u00b10.0%";
        tdShare.classList.add("faint");
      } else if (sd > 0) {
        tdShare.textContent = "+" + (sd * 100).toFixed(1) + "%";
        tdShare.classList.add("is-positive");
      } else {
        tdShare.textContent = "\u2212" + Math.abs(sd * 100).toFixed(1) + "%";
        tdShare.classList.add("is-negative");
      }
    }
    tr.appendChild(tdShare);
    tr.appendChild(numCell(r.from_pop === null || r.from_pop === undefined ? "\u2014" : fmtInt(r.from_pop)));
    tr.appendChild(numCell(fmtInt(r.to_pop)));
    tr.appendChild(deltaCell(r.pop_delta, null, null));
    els.compareBody.appendChild(tr);
  });

  els.compareMovement.hidden = false;
  setText(
    els.compareMovement,
    "Movement between the pair: " + (payload.movement.gained_total || 0) + " gained \u00b7 " + (payload.movement.lost_total || 0) + " lost."
  );
}

function wireCompareControls() {
  var els = analysisElements();
  function onChange() {
    var from = els.compareFrom.value;
    var to = els.compareTo.value;
    if (!from || !to) return;
    if (from >= to) {
      setText(els.compareError, "From must be earlier than To.");
      els.compareError.hidden = false;
      els.compareSummary.hidden = true;
      els.compareTable.hidden = true;
      setExportEnabled("compare", false);
      return;
    }
    els.compareError.hidden = true;
    analysisState.ranges.compare = { mode: "custom", days: 7, from: from, to: to };
    syncAnalysisUrl();
    setRangeSelectValue("compare");
    setPanelBusy("compare", true);
    fetchCompare(from, to)
      .catch(function (err) {
        showToast("Compare refresh failed", err.message, "error");
      })
      .then(function () {
        setPanelBusy("compare", false);
      });
  }
  function onRangeChange() {
    var value = document.querySelector('[data-range-select="compare"]').value;
    if (value === "custom") {
      var r = analysisState.ranges.compare;
      r.mode = "custom";
      syncAnalysisUrl();
      setRangeSelectValue("compare");
      return;
    }
    var days = Number(value);
    analysisState.ranges.compare = { mode: "days", days: days === 30 || days === 60 ? days : 7, from: null, to: null };
    syncAnalysisUrl();
    loadCompare();
  }
  els.compareFrom.addEventListener("change", onChange);
  els.compareTo.addEventListener("change", onChange);
  var rangeSelect = document.querySelector('[data-range-select="compare"]');
  if (rangeSelect) rangeSelect.addEventListener("change", onRangeChange);
}

/* --- drill-downs: player history + region villages --------------------------- */

// Players rankings: every name opens the player's per-snapshot history.
function playersSection(tbody, rows, valueCell) {
  tbody.textContent = "";
  if (!rows.length) {
    var tr = document.createElement("tr");
    var td = document.createElement("td");
    td.colSpan = 3;
    td.className = "empty-cell";
    td.textContent = "No players.";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  rows.forEach(function (stat, index) {
    var tr = document.createElement("tr");
    var tdRank = document.createElement("td");
    tdRank.className = "num";
    tdRank.textContent = String(index + 1);
    tr.appendChild(tdRank);
    var tdName = document.createElement("td");
    var name = document.createElement("button");
    name.type = "button";
    name.className = "event-line__name";
    name.textContent = stat.player_name;
    name.setAttribute("aria-label", "Open history for " + stat.player_name);
    name.addEventListener("click", function () {
      openPlayerHistory(stat.player_id, stat.player_name);
    });
    tdName.appendChild(name);
    tr.appendChild(tdName);
    tr.appendChild(valueCell(stat));
    tbody.appendChild(tr);
  });
}

function openPlayerHistory(playerId, playerName) {
  var els = analysisElements();
  var seq = ++analysisState.playerHistorySeq;
  els.playerDetail.hidden = false;
  els.playerDetailError.hidden = true;
  els.playerDetailNote.hidden = true;
  els.playerAbsent.hidden = true;
  els.playerHistoryTable.hidden = true;
  els.playerChartCard.hidden = true;
  setText(els.playerDetailName, playerName + " \u00b7 #" + playerId);
  setPanelBusy("players", true);
  return api
    .playerHistory(playerId, analysisState.playerHistoryDays)
    .then(function (payload) {
      if (seq !== analysisState.playerHistorySeq) return;
      renderPlayerHistory(payload);
      setPanelBusy("players", false);
    })
    .catch(function (err) {
      if (seq !== analysisState.playerHistorySeq) return;
      setPanelBusy("players", false);
      els.playerDetailError.hidden = false;
      setText(els.playerDetailError, err.message || "Couldn't load player history.");
    });
}

function renderPlayerHistory(payload) {
  var els = analysisElements();
  var history = payload.history || [];
  els.playerAbsent.hidden = payload.present_in_latest;
  setText(els.playerDetailName, (history.length ? history[history.length - 1].player_name : "Player") + " \u00b7 #" + payload.player_id);
  els.playerHistoryTable.hidden = false;
  els.playerHistoryBody.textContent = "";
  // Newest observation first (the chart stays ASC — table vs trend axis).
  history.slice().reverse().forEach(function (point) {
    var tr = document.createElement("tr");
    var tdDate = document.createElement("td");
    tdDate.className = "date-cell";
    tdDate.textContent = point.snapshot_date;
    tr.appendChild(tdDate);
    tr.appendChild(numCell(point.player_name));
    tr.appendChild(numCell(point.alliance_tag || "\u2014"));
    tr.appendChild(numCell(fmtInt(point.villages)));
    tr.appendChild(numCell(fmtInt(point.population)));
    tr.appendChild(numCell(fmtInt(point.vp)));
    els.playerHistoryBody.appendChild(tr);
  });
  if (history.length < 2) {
    els.playerDetailNote.hidden = false;
    setText(els.playerDetailNote, "Only one stored observation — no trend chart.");
    return;
  }
  renderPlayerChart(history);
}

function renderPlayerChart(history) {
  var els = analysisElements();
  var labels = history.map(function (p) { return p.snapshot_date; });
  var data = history.map(function (p) { return p.population; });
  var tableDetails = fillChartDataTable(
    els.playerChartCard,
    "chart-data-player",
    ["Snapshot", "Villages", "Population", "VP"],
    history.map(function (p) { return [p.snapshot_date, fmtInt(p.villages), fmtInt(p.population), fmtInt(p.vp)]; })
  );
  var asOfEl = document.querySelector('[data-as-of="player"]');
  if (asOfEl) {
    asOfEl.hidden = false;
    setText(asOfEl, "as of " + labels[labels.length - 1]);
  }
  if (!window.Chart) {
    els.playerChartCard.hidden = false;
    els.playerDetailNote.hidden = false;
    setText(els.playerDetailNote, "Chart library unavailable.");
    tableDetails.open = true;
    return;
  }
  els.playerChartCard.hidden = false;
  els.playerCanvas.setAttribute("aria-label", "Population history for " + els.playerDetailName.textContent);
  els.playerCanvas.setAttribute("aria-describedby", tableDetails.id);
  var chart = analysisState.charts.player;
  if (chart) {
    chart.data.labels = labels;
    chart.data.datasets[0].data = data;
    chart.update();
    chart.resize();
    return;
  }
  chart = new Chart(els.playerCanvas.getContext("2d"), {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Population",
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
  analysisState.charts.player = chart;
}

// Regions: every row opens the region's villages for the range-end date
// (the table's "current" snapshot — matches the selected range, not a
// hard-coded latest).
function openRegionVillages(region) {
  var els = analysisElements();
  var seq = ++analysisState.regionVillagesSeq;
  var detail = document.getElementById("region-detail");
  detail.hidden = false;
  els.regionDetailError.hidden = true;
  els.regionDetailNote.hidden = true;
  setText(els.regionDetailTitle, region);
  setPanelBusy("regions", true);
  var params = {};
  var regionsPayload = analysisState.regionsPayload;
  if (regionsPayload && regionsPayload.current_date) params.date = regionsPayload.current_date;
  return api
    .analysis("regions/" + encodeURIComponent(region) + "/villages", params)
    .then(function (payload) {
      if (seq !== analysisState.regionVillagesSeq) return;
      renderRegionVillages(payload);
      setPanelBusy("regions", false);
    })
    .catch(function (err) {
      if (seq !== analysisState.regionVillagesSeq) return;
      setPanelBusy("regions", false);
      els.regionDetailError.hidden = false;
      setText(els.regionDetailError, err.message || "Couldn't load region villages.");
    });
}

function renderRegionVillages(payload) {
  var els = analysisElements();
  var results = payload.results || [];
  setText(els.regionDetailDate, payload.snapshot_date || "—");
  setText(els.regionDetailTitle, payload.region + " \u00b7 " + results.length + " villages");
  els.regionVillagesBody.textContent = "";
  if (!results.length) {
    els.regionDetailNote.hidden = false;
    setText(els.regionDetailNote, "No villages in this region" + (payload.snapshot_date ? " on " + payload.snapshot_date : "") + ".");
    return;
  }
  els.regionDetailNote.hidden = true;
  results.forEach(function (v) {
    var tr = document.createElement("tr");
    var tdName = document.createElement("td");
    var open = document.createElement("button");
    open.type = "button";
    open.className = "event-line__name";
    open.textContent = v.name;
    open.setAttribute("aria-label", "Open history for " + v.name);
    open.addEventListener("click", function () {
      openVillageHistory(v.village_id, v.name);
    });
    tdName.appendChild(open);
    tr.appendChild(tdName);
    var tdCoords = document.createElement("td");
    tdCoords.className = "num";
    tdCoords.textContent = "(" + v.x + "|" + v.y + ")";
    tr.appendChild(tdCoords);
    tr.appendChild(numCell(fmtInt(v.population)));
    tr.appendChild(numCell(v.player_name));
    tr.appendChild(numCell(v.alliance_tag || "\u2014"));
    var tdSide = document.createElement("td");
    tdSide.textContent = v.side === "tracked" ? "tracked" : "other";
    tdSide.classList.add(v.side === "tracked" ? "is-positive" : "faint");
    tr.appendChild(tdSide);
    els.regionVillagesBody.appendChild(tr);
  });
}

var tabLoaders = {
  regions: loadRegions,
  alliances: loadStandings,
  players: loadPlayers,
  events: loadEvents,
  wars: loadWars,
  changes: loadChanges,
  compare: loadCompare,
  watch: loadWatch,
  roster: loadRoster,
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
  syncAnalysisUrl();
  // The header Scope filter scopes regions/players/events/changes/compare/
  // watch; Alliances has its own local Series picker, Wars is always the
  // tracked universe and Roster its own local Alliance select (never the
  // header filter).
  if (els.allianceFilter) {
    els.allianceFilter.hidden =
      name === "alliances" || name === "wars" || name === "roster" || allianceTags.length < 2;
  }
  if (!activatedTabs[name]) {
    activatedTabs[name] = true;
    tabLoaders[name]();
  } else if (analysisState.dirtyTabs[name]) {
    analysisState.dirtyTabs[name] = false;
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
      // The stale list must not contradict the message: hide it.
      els.eventsGrid.hidden = true;
      els.eventsEmpty.hidden = true;
      setExportEnabled("events", false);
      return;
    }
    els.eventsError.hidden = true;
    // A touched pair is a custom range for this tab.
    analysisState.ranges.events = { mode: "custom", days: 7, from: from, to: to };
    syncAnalysisUrl();
    setRangeSelectValue("events");
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
  function onRangeChange() {
    var value = document.querySelector('[data-range-select="events"]').value;
    if (value === "custom") {
      var r = analysisState.ranges.events;
      r.mode = "custom";
      syncAnalysisUrl();
      setRangeSelectValue("events");
      return;
    }
    var days = Number(value);
    analysisState.ranges.events = { mode: "days", days: days === 30 || days === 60 ? days : 7, from: null, to: null };
    syncAnalysisUrl();
    loadEvents();
  }
  els.eventsFrom.addEventListener("change", onChange);
  els.eventsTo.addEventListener("change", onChange);
  els.eventsLimit.addEventListener("change", function () {
    // Keeps the selected range and alliance filter; only the row limit
    // changes (the loader also preserves it across refetches).
    analysisState.eventsLimit = Number(els.eventsLimit.value);
    onChange();
  });
  var rangeSelect = document.querySelector('[data-range-select="events"]');
  if (rangeSelect) rangeSelect.addEventListener("change", onRangeChange);
}

function wireWarsControls() {
  var els = analysisElements();
  function onChange() {
    var from = els.warsFrom.value;
    var to = els.warsTo.value;
    if (!from || !to) return;
    if (from >= to) {
      setText(els.warsError, "From must be earlier than To.");
      els.warsError.hidden = false;
      els.warsMatrix.hidden = true;
      els.warsDetail.hidden = true;
      els.warsEmpty.hidden = true;
      setExportEnabled("wars", false);
      return; // keep the previous lists
    }
    els.warsError.hidden = true;
    analysisState.ranges.wars = { mode: "custom", days: 7, from: from, to: to };
    syncAnalysisUrl();
    setRangeSelectValue("wars");
    setPanelBusy("wars", true);
    setWarsBusy(true);
    fetchWars(from, to)
      .catch(function (err) {
        showToast("Wars refresh failed", err.message, "error");
      })
      .then(function () {
        setPanelBusy("wars", false);
        setWarsBusy(false);
      });
  }
  function onRangeChange() {
    var value = document.querySelector('[data-range-select="wars"]').value;
    if (value === "custom") {
      var r = analysisState.ranges.wars;
      r.mode = "custom";
      syncAnalysisUrl();
      setRangeSelectValue("wars");
      return;
    }
    var days = Number(value);
    analysisState.ranges.wars = { mode: "days", days: days === 30 || days === 60 ? days : 7, from: null, to: null };
    syncAnalysisUrl();
    loadWars();
  }
  els.warsFrom.addEventListener("change", onChange);
  els.warsTo.addEventListener("change", onChange);
  var rangeSelect = document.querySelector('[data-range-select="wars"]');
  if (rangeSelect) rangeSelect.addEventListener("change", onRangeChange);
}

function wireAnalysis() {
  applyChartDefaults();
  wireTabs();
  wireRegionSelect();
  wireMetricToggle();
  wireEventsControls();
  wireWarsControls();
  wireStandingsPicker();
  wireAllianceSwitch();
  wireVillagesSearch();
  wireExportButtons();
  wireCompareControls();
  wireWatchControls();
  wireRosterControls();
  // Every dynamic select starts with a visible disabled Loading… option —
  // no blank control during the first request.
  primeDynamicSelects();
  // One range controller per historical tab — never a shared global window.
  RANGE_TABS.forEach(function (tab) {
    wireRangeControls(tab);
    wirePairSelects(tab);
  });
  // Regions is the default tab — its payload is fetched at init.
  activateTab(document.getElementById("tab-regions"));
}

/* --- alliance filter (analysis) ---------------------------------------------- */

function renderAllianceFilter() {
  var filter = analysisElements().allianceFilter;
  if (!filter) return;
  // Rebuild on EVERY change of alliance_tags: the options are not stable
  // per process (settings can change them). A vanished selected tag
  // resets to "combined" and the filtered payloads are marked for reload.
  filter.textContent = "";
  var tags = (allianceTags || []).slice();
  if (tags.length < 2) {
    filter.hidden = true;
    return;
  }
  if (analysisState.alliance !== "combined" && tags.indexOf(analysisState.alliance) === -1) {
    analysisState.alliance = "combined";
    markAllianceDirty();
  }
  filter.hidden = false;
  ["combined"].concat(tags).forEach(function (tag) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "segmented__btn";
    btn.setAttribute("data-alliance", tag);
    btn.setAttribute("aria-pressed", String(tag === analysisState.alliance));
    btn.textContent = tag === "combined" ? "Combined" : tag;
    filter.appendChild(btn);
  });
}

// The filtered tabs load with the new filter on their next activation.
function markAllianceDirty() {
  ["regions", "events", "changes", "players", "compare", "watch"].forEach(function (name) {
    analysisState.dirtyTabs[name] = true;
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
    syncAnalysisUrl();
    // ONE refetch: only the active tab reloads right now; the other
    // filtered tabs are marked dirty and load on their next activation.
    markAllianceDirty();
    if (activatedTabs[activeTabName] && tabLoaders[activeTabName]) {
      var panel = document.getElementById("panel-" + activeTabName);
      if (!panel || panel.getAttribute("aria-busy") !== "true") {
        analysisState.dirtyTabs[activeTabName] = false;
        tabLoaders[activeTabName]();
      }
    }
  });
}

/* --- exports ---------------------------------------------------------------- */

//: Status payload integration: the filter rebuilds whenever the server tags
//: change (settings saves included), resetting a vanished selection.
export function setAllianceTags(tags) {
  allianceTags = (tags || []).slice();
  renderAllianceFilter();
}

//: Reload the currently active analysis tab (refresh-after-action / manual
//: refresh); resolves immediately when Intelligence is not active or the tab
//: was never activated.
export function refreshActiveAnalysis() {
  if (state.dashboardState.activeView !== "intelligence") {
    return Promise.resolve();
  }
  var loader = tabLoaders[activeTabName];
  if (!loader || !activatedTabs[activeTabName]) return Promise.resolve();
  return loader();
}

export { activateTab, wireAnalysis, markAllianceDirty, tabLoaders };
