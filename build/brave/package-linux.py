#!/usr/bin/env python3
"""Package only Brave's generated runtime dependencies, not its object tree."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

def fail(message: str) -> None:
    raise SystemExit(f"Brave packaging error: {message}")

if len(sys.argv) != 4:
    fail("usage: package-linux.py CHROMIUM_SRC DIST_DIR BRAVE_CORE_TAG")

src = Path(sys.argv[1]).resolve(strict=True)
dist = Path(sys.argv[2]).resolve()
tag = sys.argv[3]
out = src / "out" / "Release"
brave = src / "brave"
runtime_deps = out / "brave.runtime_deps"
binary = out / "brave"

if not binary.is_file() or not os.access(binary, os.X_OK):
    fail(f"missing Brave executable: {binary}")
if not runtime_deps.is_file():
    fail(f"missing Ninja runtime dependency manifest: {runtime_deps}")

stage = dist / "brave-linux-x64"
if dist.exists():
    shutil.rmtree(dist)
stage.mkdir(parents=True)

def resolve_dep(raw: str) -> Path:
    dep = Path(raw)
    candidates = [dep] if dep.is_absolute() else [out / dep, src / dep]
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(src)
        except (OSError, ValueError):
            continue
        if candidate.is_file() or candidate.is_symlink():
            return Path(os.path.abspath(candidate))
    fail(f"runtime dependency is missing or outside Chromium source tree: {raw}")

seen: set[str] = set()
count = 0
for raw in runtime_deps.read_text(encoding="utf-8").splitlines():
    raw = raw.strip()
    if not raw or raw in seen:
        continue
    seen.add(raw)
    source = resolve_dep(raw)
    resolved = source.resolve(strict=True)
    resolved.relative_to(src)
    relative = source.relative_to(src) if source.is_symlink() else resolved.relative_to(src)
    destination = stage / "src" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        if destination.exists() or destination.is_symlink():
            continue
        shutil.copy2(source, destination, follow_symlinks=False)
    elif source.is_file():
        shutil.copy2(source, destination)
    else:
        fail(f"unsupported runtime dependency type: {raw}")
    count += 1

# Keep the manifest and the exact GN args alongside the runtime files.
for source in (runtime_deps, out / "args.gn", src / "LICENSE", brave / "LICENSE"):
    if source.is_file():
        relative = source.resolve().relative_to(src)
        destination = stage / "src" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(source, destination)

launcher = stage / "run-brave"
launcher.write_text(
    '#!/usr/bin/env bash\n'
    'set -euo pipefail\n'
    'HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    'cd "$HERE/src/out/Release"\n'
    'exec ./brave "$@"\n',
    encoding="utf-8",
)
launcher.chmod(0o755)

version = subprocess.run([str(binary), "--version"], check=True, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.strip()
(stamp := stage / "BUILD-INFO.txt").write_text(
    f"Brave core tag: {tag}\n"
    f"Chromium version: {(src / 'chrome' / 'VERSION').read_text(encoding='ascii').strip().replace(chr(10), ', ')}\n"
    f"Build configuration: Release\n"
    f"Target: Linux x86_64 / Intel Core 2\n"
    f"Compiler flags: -march=core2 -mtune=core2\n"
    f"CFI: disabled (required with ThinLTO disabled)\n"
    f"ThinLTO: disabled\n"
    f"Brave version output: {version}\n"
    f"Runtime dependencies packaged: {count}\n",
    encoding="utf-8",
)

# Verify the packaged tree rather than only the original build directory.
subprocess.run(
    ["timeout", "90s", str(launcher), "--headless", "--no-sandbox",
     "--disable-gpu", "--disable-dev-shm-usage", "--dump-dom", "about:blank"],
    cwd=stage, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
dist.mkdir(parents=True, exist_ok=True)
archive = dist / "brave-linux-x64.tar.zst"
subprocess.run(
    ["tar", "--use-compress-program=zstd -T0 -3", "-cpf", str(archive),
     "-C", str(dist), stage.name],
    check=True,
)
hasher = hashlib.sha256()
with archive.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        hasher.update(chunk)
digest = hasher.hexdigest()
(dist / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
print(f"Packaged {count} runtime dependencies")
print(f"Brave: {version}")
print(f"Artifact: {archive} ({archive.stat().st_size} bytes)")
print(f"SHA256: {digest}")
