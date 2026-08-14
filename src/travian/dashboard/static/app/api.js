/* API client module — the single fetch surface for the dashboard modules.
   No framework, no build step. The token and the 401 handler are injected
   by the bootstrap (auth.js owns storage; showTokenDialog lives there), so
   this module never depends on auth or analysis. */

import { state } from "./ui.js";

var LOG_LIMIT = 50;

//: Bearer token getter — wired by the bootstrap to auth.tokenStore.get.
var tokenGetter = function () {
  return null;
};
//: 401 handler — wired by the bootstrap to auth.showTokenDialog.
var unauthorizedHandler = function () {};

export function setTokenGetter(fn) {
  tokenGetter = fn;
}

export function setUnauthorizedHandler(fn) {
  unauthorizedHandler = fn;
}

/* --- JSON safety ------------------------------------------------------------ */

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

export function request(method, url, payload) {
  var opts = { method: method, headers: { Accept: "application/json" } };
  var token = tokenGetter();
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
          if (res.status === 401) unauthorizedHandler();
          var err = new Error(extractError(body, res.status));
          err.status = res.status;
          err.body = body;
          throw err;
        }
        return body;
      });
  });
}

/* --- endpoint surface ------------------------------------------------------- */

export var api = {
  status: function () {
    return request("GET", "/api/status");
  },
  meta: function () {
    return request("GET", "/api/meta");
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
  if (ALLIANCE_FILTERED_KINDS.indexOf(kind) !== -1 && state.analysisState.alliance !== "combined") {
    parts.push(["alliance", state.analysisState.alliance]);
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

api.standings = function (tags, days) {
  var parts = [["days", days]];
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
