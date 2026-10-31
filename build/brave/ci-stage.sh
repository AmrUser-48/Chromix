#!/usr/bin/env bash
# Brave Linux x64 staged source/build pipeline. The full workspace is checkpointed
# between GitHub-hosted runners; timestamps and Ninja/Siso state are preserved.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${1:?usage: ci-stage.sh WORKDIR STAGE}"
STAGE="${2:?usage: ci-stage.sh WORKDIR STAGE}"
case "$STAGE" in 1|2|3|4|5|6|7|8) ;; *) echo "stage must be 1..8" >&2; exit 2 ;; esac

WORK="$(mkdir -p "$WORK" && cd "$WORK" && pwd)"
SRC="$WORK/src"
BRAVE="$SRC/brave"
OUT="$SRC/out/Release"
TAG="${BRAVE_CORE_TAG:-v1.98.64}"
JOBS="${CHROMIX_BRAVE_JOBS:-4}"
SNAPSHOT="$WORK/.snapshot-stage-$STAGE"
PATCH="$REPO/build/brave/core2-compiler-flags.patch"

emit() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
  fi
}
log() { printf '==> %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

JOB_START="${CHROMIX_JOB_START_EPOCH:-$(date +%s)}"
DEADLINE=$((JOB_START + 300 * 60))
remaining_minutes() {
  local now
  now="$(date +%s)"
  echo $(( (DEADLINE - now) / 60 ))
}
is_timeout() { [ "$1" -eq 124 ] || [ "$1" -eq 137 ] || [ "$1" -eq 143 ]; }

snapshot_and_handoff() {
  local reason="$1"
  log "$reason; saving complete Brave workspace for the next runner"
  rm -rf "$SNAPSHOT"
  CHROMIX_SNAPSHOT_MAX_SLOTS=8 CHROMIX_SNAPSHOT_MAX_VOLUMES=8 \
    CHROMIX_SNAPSHOT_VOLUME_BYTES=8G \
    bash "$REPO/build/posix/ci-parts.sh" "$WORK" "$SNAPSHOT"
  emit finished false
  emit upload_snapshot true
  emit stage "$STAGE"
  exit 0
}

emit finished false
emit upload_snapshot false
emit stage "$STAGE"

# The Brave build root is <workspace>/src/brave; Chromium is synced into
# <workspace>/src by Brave's own pnpm/depot_tools build system.
if [ ! -d "$BRAVE/.git" ]; then
  mkdir -p "$SRC"
  git clone --depth 1 --branch "$TAG" https://github.com/brave/brave-core.git "$BRAVE"
fi
test -d "$BRAVE/.git" || die "brave-core checkout is missing"
ACTUAL_TAG="$(git -C "$BRAVE" describe --exact-match --tags HEAD 2>/dev/null || true)"
[ "$ACTUAL_TAG" = "$TAG" ] || die "brave-core checkout is not pinned to $TAG (found: ${ACTUAL_TAG:-untagged})"

# A timed-out init is safe to repeat: pnpm install and gclient sync are
# incremental, so an interrupted checkout continues instead of restarting.
if [ ! -f "$SRC/.brave-init-complete" ]; then
  BUDGET="$(remaining_minutes)"
  [ "$BUDGET" -gt 2 ] || snapshot_and_handoff "stage deadline reached before Brave initialization"
  log "initializing Brave $TAG and its pinned Chromium source (no Git history)"
  set +e
  timeout -k 2m -s TERM "${BUDGET}m" bash -lc 'cd "$1" && pnpm run init -- --no-history' _ "$BRAVE"
  RC=$?
  set -e
  if is_timeout "$RC"; then snapshot_and_handoff "Brave source initialization reached the stage deadline"; fi
  [ "$RC" -eq 0 ] || die "pnpm run init failed with exit $RC"
  printf '%s\n' "$TAG" > "$SRC/.brave-init-complete"
fi

test -x "$SRC/build/install-build-deps.sh" || die "Chromium build dependency installer is missing after sync"
log "installing Brave/Chromium Linux build dependencies"
sudo "$SRC/build/install-build-deps.sh" --no-prompt

# Apply the target-only Core 2 flags after Brave has applied its Chromium patch
# stack. This patch does not affect host-toolchain compilations.
if [ ! -f "$SRC/.brave-core2-patch-applied" ]; then
  (cd "$SRC" && git apply --check "$PATCH")
  (cd "$SRC" && git apply "$PATCH")
  printf '%s\n' "$(sha256sum "$PATCH" | awk '{print $1}')" > "$SRC/.brave-core2-patch-applied"
else
  grep -Fq -- '-march=core2' "$SRC/build/config/compiler/BUILD.gn" ||
    die "Core 2 patch marker exists but compiler configuration is missing -march=core2"
  grep -Fq -- '-mtune=core2' "$SRC/build/config/compiler/BUILD.gn" ||
    die "Core 2 patch marker exists but compiler configuration is missing -mtune=core2"
fi

BUDGET="$(remaining_minutes)"
[ "$BUDGET" -gt 2 ] || snapshot_and_handoff "stage deadline reached before compilation"
log "building Brave Release for Linux x64 with Core 2 flags; jobs=$JOBS"
set +e
(
  cd "$BRAVE"
  pnpm run build -- Release \
    --target_os linux --target_arch x64 \
    --gn is_cfi:false \
    --gn use_thin_lto:false \
    --gn thin_lto_enable_optimizations:false \
    --ninja "j:$JOBS"
)
RC=$?
set -e
if [ "$RC" -ne 0 ]; then
  if is_timeout "$RC"; then snapshot_and_handoff "Brave Release compilation reached the stage deadline"; fi
  die "Brave Release build failed with exit $RC"
fi

[ -x "$OUT/brave" ] || die "build exited successfully but $OUT/brave is missing"
python3 - "$OUT" <<'PY'
from pathlib import Path
import re
import subprocess
import sys

out = Path(sys.argv[1])
args = (out / "args.gn").read_text(encoding="utf-8")
for key in ("is_cfi", "use_thin_lto", "thin_lto_enable_optimizations"):
    if not re.search(rf"(?m)^{key}\s*=\s*false\s*$", args):
        raise SystemExit(f"Core 2 profile validation failed: {key}=false missing from args.gn")
result = subprocess.run(
    ["ninja", "-C", str(out), "-t", "commands", "brave"],
    check=True, text=True, stdout=subprocess.PIPE,
)
commands = result.stdout.splitlines()
if not any("-march=core2" in line and "-mtune=core2" in line and " -c " in line
           for line in commands):
    raise SystemExit("Core 2 profile validation failed: no Brave compile command contains both Core 2 flags")
print("Verified Release GN settings: CFI=false, ThinLTO=false")
print("Verified Brave target compile commands: -march=core2 -mtune=core2")
PY

log "Brave version: $("$OUT/brave" --version)"
rm -rf "$WORK/dist"
python3 "$REPO/build/brave/package-linux.py" "$SRC" "$WORK/dist" "$TAG"
emit finished true
emit upload_snapshot false
log "Brave Linux x64 build and runtime bundle completed"
