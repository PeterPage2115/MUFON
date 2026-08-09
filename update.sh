#!/usr/bin/env bash
# update.sh — idempotent update of the travian-discord-report-bot container.
#
# The image ghcr.io/peterpage2115/mufon:latest is PRIVATE, so docker must be
# logged in to GHCR before the pull succeeds. Do this once per server:
#
#   echo <PAT> | docker login ghcr.io -u PeterPage2115 --password-stdin
#
# where <PAT> is a GitHub Personal Access Token with the read:packages scope.
# Without the login, both the manifest check below and the pull fail with
# "denied"/"unauthorized" — the check catches that first and exits with a
# readable message instead of a raw docker error.
#
# Idempotent: safe to run repeatedly. If the image check fails, the script
# exits BEFORE touching compose — nothing is pulled or restarted.
set -euo pipefail

IMAGE="ghcr.io/peterpage2115/mufon:latest"

# Step 1 — verify the image exists AND we are authenticated, BEFORE pulling.
if ! manifest_err="$(docker manifest inspect "${IMAGE}" 2>&1)"; then
  if grep -Eqi 'unauthorized|denied' <<< "${manifest_err}"; then
    echo "ERROR: cannot access ${IMAGE} — not logged in to GHCR." >&2
    echo "The image is private; log in once per server:" >&2
    echo "  echo <PAT> | docker login ghcr.io -u PeterPage2115 --password-stdin" >&2
    echo "  (PAT = GitHub Personal Access Token with the read:packages scope)" >&2
  else
    echo "ERROR: image ${IMAGE} not found or registry unreachable." >&2
    echo "The image is pushed by CI from main — check that a build ran:" >&2
    echo "  https://github.com/PeterPage2115/mufon/actions" >&2
  fi
  exit 1
fi

# Step 2 — pull the (now verified) image.
echo "==> Pulling ${IMAGE}"
docker compose pull

# Step 3 — recreate the container from the pulled image.
echo "==> Restarting container"
docker compose up -d

# Step 4 — completion + status hint.
echo "==> Update complete"
docker compose ps
