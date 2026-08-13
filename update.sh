#!/usr/bin/env bash
# update.sh — idempotent update of the travian-discord-report-bot container.
#
# The image ghcr.io/peterpage2115/mufon:latest is PUBLIC (the GHCR package
# visibility is public), so no `docker login` is needed — the manifest check
# below and the pull both work anonymously. If the package is ever made
# private again, log in once per server first:
#
#   echo <PAT> | docker login ghcr.io -u PeterPage2115 --password-stdin
#
# (PAT = GitHub Personal Access Token with the read:packages scope).
#
# Idempotent: safe to run repeatedly. If the image check fails, the script
# exits BEFORE touching compose — nothing is pulled or restarted.
#
# IMAGE_TAG selects the image: default "latest", or a specific commit SHA
# for a pinned deploy / rollback (IMAGE_TAG=<full-sha> ./update.sh). After
# the update, confirm the running build via GET /api/meta — build_sha must
# equal the tag you deployed.
set -euo pipefail

export IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE="ghcr.io/peterpage2115/mufon:${IMAGE_TAG}"

# Step 1 — verify the image exists, BEFORE pulling. Fails with a readable
# message when the package is missing (CI hasn't pushed) or was made private
# without a login.
if ! manifest_err="$(docker manifest inspect "${IMAGE}" 2>&1)"; then
  grep -Eqi 'unauthorized|denied' <<< "${manifest_err}"; then
    echo "ERROR: cannot access ${IMAGE} — not logged in to GHCR." >&2
    echo "The package is private; either make it public (GitHub → package" >&2
    echo "settings → Change visibility) or log in once per server:" >&2
    echo "  echo <PAT> | docker login ghcr.io -u PeterPage2115 --password-stdin" >&2
    echo "  (PAT = GitHub Personal Access Token with the read:packages scope)" >&2
  else
    echo "ERROR: image ${IMAGE} not found or registry unreachable." >&2
    echo "The image is pushed by CI from main — check that a build ran:" >&2
    echo "  https://github.com/PeterPage2115/mufon/actions" >&2
    echo "To deploy a specific commit: IMAGE_TAG=<full-sha> ./update.sh" >&2
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
echo "==> Verify the running build: curl http://<host>:8099/api/meta (build_sha should equal ${IMAGE_TAG})"
