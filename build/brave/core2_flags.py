#!/usr/bin/env python3
"""Apply and verify the Linux x86-64 Core 2 compiler profile for Brave."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

MARKER = "# CHROMIX_CORE2_FLAGS"
NEEDLE = 'config("compiler") {\n  asmflags = []\n  cflags = []\n'
INJECTION = (
    f"  {MARKER}: Intel Core 2 x86-64 target tuning.\n"
    '  if (is_linux && current_cpu == "x64") {\n'
    '    cflags += [ "-march=core2", "-mtune=core2" ]\n'
    "  }\n"
)


def apply(src: Path) -> None:
    path = src / "build/config/compiler/BUILD.gn"
    if not path.is_file():
        raise SystemExit(f"Chromium compiler configuration is missing: {path}")

    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        if text.count(MARKER) != 1 or INJECTION not in text:
            raise SystemExit("Core 2 compiler patch marker exists with unexpected contents")
        print("Core 2 compiler flags already applied")
        return

    if text.count(NEEDLE) != 1:
        raise SystemExit(
            "Unexpected Chromium compiler.gn context; refusing to apply an unreviewed patch"
        )

    path.write_text(text.replace(NEEDLE, NEEDLE + INJECTION, 1), encoding="utf-8")
    print(f"Applied -march=core2 -mtune=core2 to {path}")


def verify(out: Path, report: Path) -> None:
    args_path = out / "args.gn"
    if not args_path.is_file():
        raise SystemExit(f"Generated GN arguments are missing: {args_path}")
    args = args_path.read_text(encoding="utf-8")

    required_args = {
        "is_cfi": "false",
        "use_thin_lto": "false",
        "thin_lto_enable_optimizations": "false",
    }
    for key, value in required_args.items():
        if not re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*{value}\s*$", args):
            raise SystemExit(f"Generated args.gn does not contain {key} = {value}")

    ninja = shutil.which("ninja")
    if not ninja:
        raise SystemExit("ninja is not available on PATH; cannot verify compiler commands")

    proc = subprocess.Popen(
        [ninja, "-C", str(out), "-t", "commands", "create_dist_zips"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    command = None
    assert proc.stdout is not None
    for line in proc.stdout:
        if "clang" in line and re.search(r"\s-c\s", line):
            command = line.strip()
            proc.terminate()
            break

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    if not command:
        raise SystemExit("No Clang compile command found for create_dist_zips")

    for flag in ("-march=core2", "-mtune=core2", "-O2"):
        if not re.search(rf"(?<!\S){re.escape(flag)}(?!\S)", command):
            raise SystemExit(f"Core 2 compiler validation failed: missing {flag}")
    if re.search(r"(?<!\S)-flto(?:=\S*)?(?!\S)", command):
        raise SystemExit("Core 2 compiler validation failed: compile command enables LTO")

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "profile=core2-o2\n"
        "target_cpu=x64\n"
        "march=core2\n"
        "mtune=core2\n"
        "optimization=O2\n"
        "is_cfi=false\n"
        "use_thin_lto=false\n"
        "thin_lto_enable_optimizations=false\n"
        + command
        + "\n",
        encoding="utf-8",
    )
    print("Verified Core 2 O2 compile flags and disabled CFI/ThinLTO")
    print(f"Compiler command report: {report}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--src", type=Path, required=True)

    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("--out", type=Path, required=True)
    verify_cmd.add_argument("--report", type=Path, required=True)

    args = parser.parse_args()
    if args.action == "apply":
        apply(args.src)
    else:
        verify(args.out, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
