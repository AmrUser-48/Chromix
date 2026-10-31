#!/usr/bin/env bash
# Staged Brave Linux x64 build. The parent CI job snapshots this entire WORK
# directory after timeout, then restores it and re-enters this script.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${1:?usage: build.sh WORKDIR x64}"
ARCH="${2:-x64}"

BRAVE_TAG="v1.98.64"
BRAVE_COMMIT="84c4b9af378e546d7f8ed4b52e0f7a6d0f0e45c7"
BRAVE_VERSION="1.98.64"
CHROMIUM_VERSION="156.0.8078.80"
PNPM_VERSION="11.11.0"

[ "$(uname -s)" = "Linux" ] || { echo "Brave CI build requires Linux" >&2; exit 2; }
[ "$ARCH" = "x64" ] || { echo "Brave Core 2 profile requires Linux x64" >&2; exit 2; }

WORK="$(cd "$WORK" && pwd)"
SRC="$WORK/src"
BRAVE_CORE="$SRC/brave"
OUT="$SRC/out/Release"

mkdir -p "$SRC"

# Checkout only the pinned Brave Core release. Chromium is fetched by Brave's
# own gclient sync configuration, not by the ungoogled-Chromium pipeline.
if [ ! -d "$BRAVE_CORE/.git" ]; then
  rm -rf "$BRAVE_CORE"
  git clone --depth 1 --branch "$BRAVE_TAG" \
    https://github.com/brave/brave-core.git "$BRAVE_CORE"
fi
git -C "$BRAVE_CORE" remote set-url origin https://github.com/brave/brave-core.git
if [ "$(git -C "$BRAVE_CORE" rev-parse HEAD 2>/dev/null || true)" != "$BRAVE_COMMIT" ]; then
  git -C "$BRAVE_CORE" fetch --depth 1 origin "refs/tags/$BRAVE_TAG:refs/tags/$BRAVE_TAG"
  git -C "$BRAVE_CORE" checkout --detach --force "$BRAVE_COMMIT"
fi
test "$(git -C "$BRAVE_CORE" rev-parse HEAD)" = "$BRAVE_COMMIT"

python3 - "$BRAVE_CORE/package.json" "$BRAVE_VERSION" "$CHROMIUM_VERSION" <<'PY'
import json
import sys
from pathlib import Path

package = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if package.get("version") != sys.argv[2]:
    raise SystemExit(f"unexpected Brave version: {package.get('version')}")
chrome_tag = package.get("config", {}).get("projects", {}).get("chrome", {}).get("tag")
if chrome_tag != sys.argv[3]:
    raise SystemExit(f"unexpected Chromium pin: {chrome_tag}")
print(f"Brave {sys.argv[2]} / Chromium {chrome_tag}")
PY

# The Brave release requires Node >=24.16,<25 and pnpm >=11.11.
if ! command -v pnpm >/dev/null 2>&1 || [ "$(pnpm --version)" != "$PNPM_VERSION" ]; then
  npm install --global "pnpm@$PNPM_VERSION"
fi
test "$(pnpm --version)" = "$PNPM_VERSION"

if [ ! -f "$WORK/.brave-pnpm-ready" ]; then
  cd "$BRAVE_CORE"
  pnpm install --frozen-lockfile --yes
  touch "$WORK/.brave-pnpm-ready"
fi

# First invocation creates the root .gclient file. If sync is interrupted,
# later stages reuse that config and continue the same shallow gclient sync.
if [ ! -f "$WORK/.brave-sync-ready" ]; then
  cd "$BRAVE_CORE"
  if [ -f "$WORK/.gclient" ]; then
    node ./build/commands/scripts/sync.ts --no-history
  else
    node ./build/commands/scripts/sync.ts --init --no-history
  fi
  test -f "$WORK/.gclient"
  test -f "$SRC/chrome/VERSION"
  python3 - "$SRC/chrome/VERSION" "$CHROMIUM_VERSION" <<'PY'
import sys
from pathlib import Path

fields = {}
for line in Path(sys.argv[1]).read_text(encoding="ascii").splitlines():
    key, value = line.split("=", 1)
    fields[key] = value
version = ".".join(fields[key] for key in ("MAJOR", "MINOR", "BUILD", "PATCH"))
if version != sys.argv[2]:
    raise SystemExit(f"Chromium source mismatch: expected {sys.argv[2]}, got {version}")
print(f"Synced Chromium {version}")
PY
  touch "$WORK/.brave-sync-ready"
fi

# Hosted runners are fresh for every stage, so ensure Chromium's Linux build
# dependencies are installed before resuming Ninja.
if [ "${CHROMIX_SKIP_DEPS:-0}" != "1" ]; then
  sudo "$SRC/build/install-build-deps.sh" --no-prompt
fi

python3 "$REPO/build/brave/core2_flags.py" apply --src "$SRC"

JOBS="${CHROMIX_JOBS:-$(getconf _NPROCESSORS_ONLN)}"
case "$JOBS" in
  auto|"") JOBS="$(getconf _NPROCESSORS_ONLN)" ;;
esac
case "$JOBS" in
  *[!0-9]*|"0") echo "invalid CHROMIX_JOBS: $JOBS" >&2; exit 2 ;;
esac

cd "$BRAVE_CORE"
BUILD_ARGS=(
  --target=create_dist_zips
  --gn is_cfi:false
  --gn use_thin_lto:false
  --gn thin_lto_enable_optimizations:false
  --ninja "j:$JOBS"
  --offline
)

# Prepare GN once and verify the actual generated compile command before the
# expensive build. Re-running this is safe: GN/Ninja state remains incremental.
node ./build/commands/scripts/build.ts Release --prepare_only "${BUILD_ARGS[@]}"
python3 "$REPO/build/brave/core2_flags.py" verify \
  --out "$OUT" --report "$WORK/core2-compiler-flags.txt"

# create_dist_zips depends on Brave's executable, locale packs, ICU data,
# resource packs and Linux distribution resources.
node ./build/commands/scripts/build.ts Release "${BUILD_ARGS[@]}"

test -x "$OUT/brave" || { echo "Brave executable missing: $OUT/brave" >&2; exit 1; }
DIST_ZIP="$(find "$OUT" -type f -name "*v${BRAVE_VERSION}*linux*x64.zip" -print -quit)"
[ -n "$DIST_ZIP" ] && [ -s "$DIST_ZIP" ] || {
  echo "Brave Linux x64 distribution ZIP was not generated under $OUT" >&2
  find "$OUT" -maxdepth 3 -type f -name '*.zip' -print >&2 || true
  exit 1
}
"$OUT/brave" --version
echo "Brave build output: $DIST_ZIP"
