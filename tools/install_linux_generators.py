#!/usr/bin/env python3
"""Provision pinned Linux esbuild without executing archive code or npm scripts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request

try:
    from . import linux_restored_generators as generators
    from . import prepare_restored_build as prepare
    from . import restore_upstream_cache as restore
except ImportError:
    import linux_restored_generators as generators
    import prepare_restored_build as prepare
    import restore_upstream_cache as restore

MAX_ARCHIVE = 32 * 1024**2
DOWNLOAD_TIMEOUT = 120


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("esbuild archive redirects are forbidden")


def archive_identity(path: Path, digest: str) -> bool:
    restore.local_path(path.parent)
    if path.is_symlink():
        raise ValueError("linked esbuild archive")
    if not path.exists():
        return False
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or not 0 < info.st_size <= MAX_ARCHIVE):
        raise ValueError("invalid esbuild archive size/type/link")
    with path.open("rb") as stream:
        data = stream.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("esbuild archive SHA256 mismatch")
    return True


def fetch_archive(url: str, digest: str) -> bytes:
    # Ignore proxy credentials and reject even same-origin redirects.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"}, method="GET")
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT
    with opener.open(request, timeout=DOWNLOAD_TIMEOUT) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("unexpected esbuild archive response")
        if response.headers.get("Content-Encoding", "identity") != "identity":
            raise ValueError("encoded esbuild archive response")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdecimal() or not 0 < int(length) <= MAX_ARCHIVE):
            raise ValueError("invalid esbuild archive response size")
        data = bytearray()
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("esbuild archive download timed out")
            chunk = response.read1(min(64 * 1024, MAX_ARCHIVE + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_ARCHIVE:
                raise ValueError("oversized esbuild archive response")
        if length is not None and len(data) != int(length):
            raise ValueError("incomplete esbuild archive response")
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("esbuild download SHA256 mismatch")
    return bytes(data)


def ensure_archive(downloads: Path, key: str, *, download: bool,
                   pins: tuple = generators.PINS) -> Path:
    profile = generators.generator_profile(pins)
    if key not in ("module", "x64", "arm64"):
        raise ValueError("unsupported Linux esbuild archive")
    url, digest = profile["archives"][key]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("missing or invalid esbuild archive SHA256")
    package = "esbuild" if key == "module" else f"@esbuild/linux-{key}"
    filename = f"{package.rsplit('/', 1)[-1]}-{profile['version']}.tgz"
    if url != f"https://registry.npmjs.org/{package}/-/{filename}":
        raise ValueError("untrusted esbuild archive endpoint")
    downloads = restore.local_path(downloads)
    path = downloads / filename
    if archive_identity(path, digest):
        return path
    if not download or os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("missing exact esbuild archive; populate offline cache or use --download in GitHub Actions")
    data = fetch_archive(url, digest)
    restore.local_path(downloads).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".linux-generator-download-", dir=downloads) as temporary:
        staged = Path(temporary) / filename
        staged.write_bytes(data)
        restore.local_path(downloads)
        # Publish without replacing any file or link that appeared during the GET.
        os.link(staged, path)
        staged.unlink()
    archive_identity(path, digest)
    return path


def install(workdir: Path, arch: str, downloads: Path, *, repo: Path = prepare.ROOT,
            download: bool = False) -> dict:
    workdir, repo = restore.local_path(workdir), restore.local_path(repo)
    receipt = restore.verify_restored(workdir, "linux", arch, repo=repo)
    receipt_marker = workdir / "src" / restore.MARKER
    receipt_bytes = receipt_marker.read_bytes()
    pins = prepare.load_pins(repo, "linux")
    identity = tuple(pins[key] for key in ("ChromiumVersion", "UngoogledCommit", "UngoogledLinuxCommit"))
    generators.generator_profile(identity)
    platform, host = prepare.host_identity()
    if platform != "linux" or (host, arch) not in (("x64", "x64"), ("arm64", "arm64"), ("x64", "arm64")):
        raise ValueError("unsupported Linux native host/target")
    src = workdir / "src"
    state = prepare.prepare_linux_typescript(src, host_arch=host, repo=repo)["esbuild"]
    if not state["install_needed"]:
        return {"status": "already_installed", "source_identity": receipt["identity"], "esbuild": state}
    archives = {key: ensure_archive(downloads, key, download=download, pins=identity)
                for key in ("module", host)}
    if (receipt_marker.read_bytes() != receipt_bytes
            or restore.verify_restored(workdir, "linux", arch, repo=repo) != receipt):
        raise ValueError("restored source receipt changed during esbuild provisioning")
    prepare.prepare_linux_typescript(src, host_arch=host, repo=repo)
    result = generators.install_esbuild(src, host_arch=host, module_archive=archives["module"],
                                        native_archive=archives[host], pins=identity)
    return {"status": "installed", "source_identity": receipt["identity"], "esbuild": result}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=prepare.ROOT)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--arch", choices=("x64", "arm64"), required=True)
    parser.add_argument("--downloads", type=Path, required=True)
    parser.add_argument("--download", action="store_true", help="allow missing exact archives only in GitHub Actions")
    args = parser.parse_args(argv)
    try:
        result = install(args.workdir, args.arch, args.downloads, repo=args.repo, download=args.download)
    except (OSError, ValueError, restore.Miss, restore.LocalError, urllib.error.URLError) as error:
        print(f"Linux generator installation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
