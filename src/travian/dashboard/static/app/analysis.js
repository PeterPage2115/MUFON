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
  formatTimestamp,
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

// Kinds that honor the alliance filter (standings is a cross-alliance
// comparison and never filters).
var ALLIANCE_FILTERED_KINDS = ["regions", "events", "deltas", "players", "watch"];


function loadRegions() {
  var panel = document.getElementById("panel-regions");
  var els = analysisElements();
  hidePanelError(panel);
  setPanelBusy("regions", true);
  tableLoading(els.regionsBody, 6);
  return api
    .analysis("regions", { days: analysisState.days })
    .then(function (payload) {
      renderRegions(payload);
      setPanelBusy("regions", false);
    })
    .catch(function (err) {
      setPanelBusy("regions", false);
      showPanelError(panel, "Couldn't load analysis data.", loadRegions);
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
  // describes it, tooltips are never the only way to read the chart.
  var tableHeaders = ["Date", "Share", "Our pop", "Total pop"];
  var tableRows = dates.map(function (d, i) {
    var p = byDate[d];
    return [
      d,
      p ? (p.share * 100).toFixed(1) + "%" : "—",
      p ? fmtInt(p.our_pop) : "—",
      p ? fmtInt(p.total_pop) : "—",
    ];
  });
  var tableDetails = fillChartDataTable(card, "chart-data-regions", tableHeaders, tableRows);
  var asOf = dates.length ? dates[dates.length - 1] : null;
  var asOfEl = document.querySelector('[data-as-of="regions"]');
  if (asOfEl) {
    asOfEl.hidden = !asOf;
    if (asOf) setText(asOfEl, "as of " + asOf);
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
  return api
    .standings(selection, analysisState.days)
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
      setExportEnabled("standings", series.length > 0);
      renderStandingsPicker();
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
  var dates = payload.dates || [];
  var series = payload.series || [];
  var metric = analysisState.metric;

  // Textual fallback with the exact payload: one column per series.
  var tableHeaders = ["Date"].concat(series.map(function (row) {
    return row.tag;
  }));
  var tableRows = dates.map(function (d) {
    var cells = [d];
    series.forEach(function (row) {
      var byDate = {};
      (row[metric === "vp" ? "vp_points" : "points"] || []).forEach(function (pair) {
        byDate[pair[0]] = pair[1];
      });
      cells.push(byDate[d] !== undefined ? fmtInt(byDate[d]) : "—");
    });
    return cells;
  });
  var tableDetails = fillChartDataTable(card, "chart-data-standings", tableHeaders, tableRows);
  var asOf = dates.length ? dates[dates.length - 1] : null;
  var asOfEl = document.querySelector('[data-as-of="standings"]');
  if (asOfEl) {
    asOfEl.hidden = !asOf;
    if (asOf) setText(asOfEl, "as of " + asOf);
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
  hidePanelError(panel);
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
      if (dates.length < 2) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("wars", false);
        setWarsBusy(false);
        return;
      }
      hidePanelEmpty(panel);
      fillDateSelect(els.warsFrom, dates);
      fillDateSelect(els.warsTo, dates);
      if (analysisState.warsFrom && dates.indexOf(analysisState.warsFrom) !== -1) {
        els.warsFrom.value = analysisState.warsFrom;
      } else {
        els.warsFrom.value = dates[dates.length - 2];
      }
      if (analysisState.warsTo && dates.indexOf(analysisState.warsTo) !== -1) {
        els.warsTo.value = analysisState.warsTo;
      } else {
        els.warsTo.value = dates[dates.length - 1];
      }
      analysisState.warsFrom = els.warsFrom.value;
      analysisState.warsTo = els.warsTo.value;
      return fetchWars(analysisState.warsFrom, analysisState.warsTo);
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
  if (ev.from_tag !== undefined) {
    meta.textContent =
      (ev.to_tag !== undefined ? ev.from_tag + " \u2192 " + ev.to_tag : ev.from_tag) +
      " \u00b7 " +
      (ev.to_player || ev.from_player || "unknown");
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
  return api
    .analysis("deltas", { days: analysisState.days })
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
      showPanelError(panel, "Couldn't load analysis data.", loadChanges);
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
      exportCsv("wars-" + analysisState.warsFrom + "-" + analysisState.warsTo + ".csv", headers, rows);
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
      var rows = (payload.dates || []).map(function (d) {
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
      var snapshot = rows[rows.length - 1].date;
      var headers = ["Date", "Previous snapshot", "Days elapsed", "Villages", "Villages Δ", "Population", "Population Δ", "Players", "Players Δ", "VP", "VP Δ"];
      exportCsv(
        "deltas-" + snapshot + ".csv",
        headers,
        rows.map(function (r) {
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
      if (dates.length < 2) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("watch", false);
        return;
      }
      hidePanelEmpty(panel);
      fillDateSelect(els.watchFrom, dates);
      fillDateSelect(els.watchTo, dates);
      els.watchFrom.value = dates[dates.length - 2];
      els.watchTo.value = dates[dates.length - 1];
      return fetchWatch(els.watchFrom.value, els.watchTo.value);
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
    setPanelBusy("watch", true);
    fetchWatch(from, to)
      .catch(function (err) {
        showToast("Watch refresh failed", err.message, "error");
      })
      .then(function () {
        setPanelBusy("watch", false);
      });
  }
  els.watchFrom.addEventListener("change", onChange);
  els.watchTo.addEventListener("change", onChange);
  els.watchSeverity.addEventListener("change", onChange);
  els.watchKind.addEventListener("change", onChange);
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
      if (!dates.length) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("roster", false);
        return;
      }
      hidePanelEmpty(panel);
      fillDateSelect(els.rosterDate, dates);
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
// The Intelligence context: range (7|30|60 days), alliance, and the current
// from/to pair live in the URL query (?view=&tab=&alliance=&days=&from=&to=)
// and the local preference key — never near the token. Invalid values are
// rejected to safe defaults and never cause a request loop.

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
  if (analysisState.days !== 30) {
    params.set("days", String(analysisState.days));
  } else {
    params.delete("days");
  }
  if (analysisState.from && analysisState.to) {
    params.set("from", analysisState.from);
    params.set("to", analysisState.to);
  } else {
    params.delete("from");
    params.delete("to");
  }
  var qs = params.toString();
  var target = window.location.pathname + (qs ? "?" + qs : "");
  history.replaceState(null, "", target);
  try {
    window.localStorage.setItem(
      "mufon.dashboard.view.v1",
      JSON.stringify({ days: analysisState.days, alliance: analysisState.alliance })
    );
  } catch (_e) {
    /* preference is best-effort; the URL is the source of truth */
  }
}

export function setDays(days) {
  var valid = days === 7 || days === 30 || days === 60;
  analysisState.days = valid ? days : 30;
  var select = document.getElementById("analysis-days");
  if (select && select.value !== String(analysisState.days)) select.value = String(analysisState.days);
  var range = analysisElements().range;
  if (range) {
    range.hidden = false;
    setText(range, "Last " + analysisState.days + " days");
  }
  // The range shapes every filtered tab: reload the active one now, mark
  // the rest dirty for their next activation.
  markAllianceDirty();
  if (activatedTabs[activeTabName] && tabLoaders[activeTabName]) {
    analysisState.dirtyTabs[activeTabName] = false;
    tabLoaders[activeTabName]();
  }
  syncAnalysisUrl();
}

function wireDaysSelect() {
  var select = document.getElementById("analysis-days");
  if (!select) return;
  select.value = String(analysisState.days);
  var range = analysisElements().range;
  if (range) {
    range.hidden = false;
    setText(range, "Last " + analysisState.days + " days");
  }
  select.addEventListener("change", function () {
    setDays(Number(select.value));
  });
}

//: Bootstrap entry: apply URL + stored preference context before the first
//: analysis load. Invalid days fall back to the stored/30 default; an
//: invalid alliance is reset by renderAllianceFilter once tags resolve.
export function applyInitialContext() {
  var params = new URLSearchParams(window.location.search);
  var daysRaw = params.get("days");
  var allianceRaw = params.get("alliance");
  var stored = null;
  try {
    stored = JSON.parse(window.localStorage.getItem("mufon.dashboard.view.v1") || "null");
  } catch (_e) {
    stored = null;
  }
  var days = daysRaw !== null ? Number(daysRaw) : stored && stored.days ? stored.days : 30;
  analysisState.days = days === 7 || days === 30 || days === 60 ? days : 30;
  if (allianceRaw !== null) {
    analysisState.alliance = allianceRaw === "combined" ? "combined" : allianceRaw;
  } else if (stored && stored.alliance) {
    analysisState.alliance = stored.alliance;
  }
  var fromRaw = params.get("from");
  var toRaw = params.get("to");
  if (fromRaw && toRaw) {
    analysisState.from = fromRaw;
    analysisState.to = toRaw;
  }
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
      if (dates.length < 2) {
        showPanelEmpty(panel, "No data yet.");
        setPanelBusy("compare", false);
        return;
      }
      hidePanelEmpty(panel);
      fillDateSelect(els.compareFrom, dates);
      fillDateSelect(els.compareTo, dates);
      if (analysisState.from && dates.indexOf(analysisState.from) !== -1) {
        els.compareFrom.value = analysisState.from;
      } else {
        els.compareFrom.value = dates[0];
      }
      if (analysisState.to && dates.indexOf(analysisState.to) !== -1) {
        els.compareTo.value = analysisState.to;
      } else {
        els.compareTo.value = dates[dates.length - 1];
      }
      analysisState.from = els.compareFrom.value;
      analysisState.to = els.compareTo.value;
      syncAnalysisUrl();
      return fetchCompare(analysisState.from, analysisState.to);
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
    analysisState.from = from;
    analysisState.to = to;
    syncAnalysisUrl();
    setPanelBusy("compare", true);
    fetchCompare(from, to)
      .catch(function (err) {
        showToast("Compare refresh failed", err.message, "error");
      })
      .then(function () {
        setPanelBusy("compare", false);
      });
  }
  els.compareFrom.addEventListener("change", onChange);
  els.compareTo.addEventListener("change", onChange);
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
  history.forEach(function (point) {
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

// Regions: every row opens the region's villages for the latest pair date.
function openRegionVillages(region) {
  var els = analysisElements();
  var seq = ++analysisState.regionVillagesSeq;
  var detail = document.getElementById("region-detail");
  detail.hidden = false;
  els.regionDetailError.hidden = true;
  els.regionDetailNote.hidden = true;
  setText(els.regionDetailTitle, region);
  setPanelBusy("regions", true);
  return api
    .analysis("regions/" + encodeURIComponent(region) + "/villages", {})
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
  // Six tabs can overflow on narrow screens — keep the chosen one visible.
  tab.scrollIntoView({ block: "nearest", inline: "nearest" });
  // The global alliance filter scopes regions/events/changes/players; the
  // Alliances tab is a cross-alliance chart with its own local picker and
  // the Wars tab always uses the tracked universe (never the filter).
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
    analysisState.from = from;
    analysisState.to = to;
    syncAnalysisUrl();
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
    analysisState.warsFrom = from;
    analysisState.warsTo = to;
    syncAnalysisUrl();
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
  els.warsFrom.addEventListener("change", onChange);
  els.warsTo.addEventListener("change", onChange);
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
  wireDaysSelect();
  wireCompareControls();
  wireWatchControls();
  wireRosterControls();
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
  ["regions", "events", "changes", "players"].forEach(function (name) {
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
  console.log("RAA-DEBUG", state.dashboardState.activeView, activeTabName, Boolean(tabLoaders[activeTabName]), Boolean(activatedTabs[activeTabName]));
  if (state.dashboardState.activeView !== "intelligence") {
    return Promise.resolve();
  }
  var loader = tabLoaders[activeTabName];
  if (!loader || !activatedTabs[activeTabName]) return Promise.resolve();
  return loader();
}

export { activateTab, wireAnalysis, markAllianceDirty, tabLoaders };
