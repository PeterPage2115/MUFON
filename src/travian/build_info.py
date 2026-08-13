"""Build provenance for the dashboard.

Exposes the installed package version and the injected build SHA
(``TRAVIAN_BUILD_SHA``, set by the Docker build via ``--build-arg
BUILD_SHA``). Kept free of Discord/dashboard imports so any module can
safely call it.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version

_FALLBACK_VERSION = "0.1.0"


def get_build_info(env: Mapping[str, str]) -> dict[str, str]:
    """Return ``{"version": ..., "build_sha": ...}`` for the public meta endpoint.

    - ``version`` — installed distribution version
      (``travian-discord-report-bot``), falling back to the pyproject value.
    - ``build_sha`` — ``TRAVIAN_BUILD_SHA`` when set and non-empty,
      otherwise ``"dev"`` (local runs / images built without the arg).
    """
    try:
        pkg_version = version("travian-discord-report-bot")
    except PackageNotFoundError:
        pkg_version = _FALLBACK_VERSION
    build_sha = (env.get("TRAVIAN_BUILD_SHA") or "").strip() or "dev"
    return {"version": pkg_version, "build_sha": build_sha}
