/* Auth module — token storage (sessionStorage), the unlock dialog, OAuth
   login flow and the user chip. Depends on ui (DOM helpers) and api (the
   logout request); the api module's 401 handler is wired by the bootstrap. */

import { setText, els } from "./ui.js";
import { request } from "./api.js";

var TOKEN_KEY = "dashboard_token";
var tokenDialogShown = false;

// The token lives in sessionStorage (same key as before): it survives a
// reload of the same tab but never persists after the browser session
// ends. OAuth sessions never touch storage (HttpOnly cookie). Unavailable
// or throwing storage degrades to in-memory — a fresh login dialog on the
// next load, never a crash.
export var tokenStore = (function () {
  var memory = null;
  var supported = null;
  function storage() {
    if (supported === null) {
      try {
        var probe = window.sessionStorage;
        probe.setItem("__travian_probe", "1");
        probe.removeItem("__travian_probe");
        supported = probe;
      } catch (_e) {
        supported = false;
      }
    }
    return supported;
  }
  return {
    get: function () {
      var s = storage();
      if (s) {
        try {
          var stored = s.getItem(TOKEN_KEY);
          if (stored) return stored;
        } catch (_e) {
          /* fall through to memory */
        }
      }
      return memory;
    },
    set: function (value) {
      memory = value;
      var s = storage();
      if (s) {
        try {
          s.setItem(TOKEN_KEY, value);
        } catch (_e) {
          /* memory keeps it for this session */
        }
      }
    },
    remove: function () {
      memory = null;
      var s = storage();
      if (s) {
        try {
          s.removeItem(TOKEN_KEY);
        } catch (_e) {
          /* nothing to clean */
        }
      }
    },
  };
})();

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
        tokenStore.remove();
        window.location.reload();
      });
  });

  chip.appendChild(name);
  chip.appendChild(role);
  chip.appendChild(logout);
}

// Resolves to the decoded auth status (or null when the request fails);
// the bootstrap waits for it before starting any protected request.
export function loadAuthStatus() {
  // OAuth mode: the HttpOnly session cookie rides along on this
  // same-origin request automatically. Token mode: the stored bearer is
  // sent explicitly (the server resolves oauth sessions from the cookie).
  var headers = { Accept: "application/json" };
  var token = tokenStore.get();
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
    tokenStore.set(value);
    window.location.reload(); // static UI is public — the reload then authenticates every API call
  });
  // The token is required to use the dashboard; Esc must not dismiss the dialog.
  dialog.addEventListener("cancel", function (event) {
    event.preventDefault();
  });
  return dialog;
}

export function showTokenDialog() {
  if (tokenDialogShown) return;
  tokenDialogShown = true;
  var dialog = ensureTokenDialog();
  // Auth-aware content: oauth mode offers the Discord login (the token
  // field is meaningless there); token mode keeps the classic form.
  // Same bearer handling as loadAuthStatus — the header only ever
  // refines the response, it never downgrades the dialog choice.
  var headers = { Accept: "application/json" };
  var token = tokenStore.get();
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

// Token / no-auth mode — or an unresolved status — is manageable; only a
// confirmed OAuth admin gains the operational controls.
export function canManageFromAuth(status) {
  return status ? status.method !== "oauth" || Boolean(status.user && status.user.admin) : true;
}
