#!/usr/bin/env bash
# Copy Brave's upstream create_dist_zips output into the staged CI artifact.
set -euo pipefail

OUT="${1:?usage: package-linux.sh OUT_DIR DEST_DIR x64 [artifact-name]}"
DEST="${2:?destination directory is required}"
ARCH="${3:-x64}"
ARTIFACT="${4:-${CHROMIX_ARTIFACT_NAME:-brave-linux-x64}}"
BRAVE_VERSION="1.98.64"

[ "$ARCH" = "x64" ] || { echo "Brave package currently supports Linux x64 only" >&2; exit 2; }

mapfile -t CANDIDATES < <(
  find "$OUT" -type f -name "*v${BRAVE_VERSION}*linux*x64.zip" -print | sort
)
[ "${#CANDIDATES[@]}" -eq 1 ] || {
  echo "Expected exactly one Brave Linux x64 distribution ZIP; found ${#CANDIDATES[@]}" >&2
  printf '%s\n' "${CANDIDATES[@]}" >&2
  exit 1
}
SOURCE_ZIP="${CANDIDATES[0]}"

unzip -Z1 "$SOURCE_ZIP" | grep -Eq '(^|/)brave$' || {
  echo "Brave distribution ZIP does not contain the brave executable: $SOURCE_ZIP" >&2
  exit 1
}
unzip -Z1 "$SOURCE_ZIP" | grep -Eq '(^|/)icudtl\.dat$' || {
  echo "Brave distribution ZIP does not contain icudtl.dat: $SOURCE_ZIP" >&2
  exit 1
}

mkdir -p "$DEST"
ASSET="$DEST/$ARTIFACT.zip"
cp -- "$SOURCE_ZIP" "$ASSET"
(
  cd "$DEST"
  sha256sum "$ARTIFACT.zip" > SHA256SUMS
  sha256sum --check --strict SHA256SUMS
)
printf 'Brave artifact: %s\n' "$ASSET"
