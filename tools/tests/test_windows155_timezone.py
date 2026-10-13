"""Exact-155 replay and source-derived native ICU tests; scheduler/V8 are shims."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_fingerprint_features import block

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
PATCH = "patches/chromium155-windows/0228-synchronous-launch-timezone.patch"
CONTROLLER = "third_party/blink/renderer/core/timezone/timezone_controller"
RENDERER = "content/renderer/render_thread_impl.cc"
BRIDGE = "third_party/blink/renderer/controller/blink_initializer.cc"
PUBLIC = "third_party/blink/public/web/blink.h"
EVIDENCE = json.loads(gzip.decompress(
    (FIXTURES / "windows155_timezone_sources.json.gz").read_bytes()))


def run_patch(directory, path, reverse=False):
    program = shutil.which("gpatch") or shutil.which("patch")
    assert program, "GNU patch is required for exact-source replay"
    result = subprocess.run(
        [program, "--batch", "--fuzz=0", "-p1", *(["-R"] if reverse else []),
         "-i", str(path)], cwd=directory, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fuzz" not in result.stdout and "FAILED" not in result.stdout


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    directory = tmp_path_factory.mktemp("windows155-timezone")
    assert EVIDENCE["version"] == (ROOT / "CHROMIUM_WINDOWS_VERSION").read_text().strip()
    for name, entry in EVIDENCE["sources"].items():
        raw = entry["text"].encode()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    for number in (5, 7, 19):
        run_patch(directory, next((ROOT / "patches").glob(f"{number:04d}-*.patch")))
    before = {name: (directory / name).read_bytes() for name in EVIDENCE["sources"]}
    run_patch(directory, ROOT / PATCH)
    result = {name: (directory / name).read_text() for name in before}
    run_patch(directory, ROOT / PATCH, reverse=True)
    assert {name: (directory / name).read_bytes() for name in before} == before
    return result


def test_exact_source_install_boundary_and_layering(sources):
    renderer = sources[RENDERER]
    setter = block(renderer, "void RenderThreadImpl::SetUxrConfig(")
    assert setter.index("CHECK(snapshot.SetAll(") < setter.index("snapshot.Get(")
    assert setter.index("snapshot.Get(") < setter.index("CHECK(blink::SetRendererDefaultTimeZone(")
    assert "PostTask" not in setter and "ForTesting" not in setter
    assert 'third_party/blink/renderer/core/' not in renderer
    assert "BLINK_EXPORT bool SetRendererDefaultTimeZone(const WebString& timezone_id);" in sources[PUBLIC]
    assert "TimeZoneController::SetDefaultTimeZone(timezone_id)" in block(
        sources[BRIDGE], "bool SetRendererDefaultTimeZone(")
    assert '"+third_party/blink/renderer/core"' in sources[
        "third_party/blink/renderer/controller/DEPS"]
    assert '"//third_party/blink/renderer/core"' in sources[
        "third_party/blink/renderer/controller/BUILD.gn"]
    assert '"blink_initializer.cc"' in sources[
        "third_party/blink/renderer/controller/BUILD.gn"]
    assert '"+third_party/blink/public"' in sources["content/DEPS"]
    assert renderer.index("InitializeWebKit(&binders)") < renderer.index(
        "AddInterface<mojom::Renderer>")
    host = sources["content/browser/renderer_host/render_process_host_impl.cc"]
    assert host.index("GetRendererInterface()->SetUxrConfig(") < host.index(
        "GetRendererInterface()->InitializeRenderer(")


def test_native_notification_and_devtools_paths_are_preserved(sources):
    original = EVIDENCE["sources"][CONTROLLER + ".cc"]["text"]
    patched = sources[CONTROLLER + ".cc"]
    for signature in (
        "bool TimeZoneController::SetIcuTimeZoneAndNotifyV8(",
        "void NotifyTimezoneChangeToV8(",
        "void NotifyTimezoneChangeOnWorkerThread(",
        "void NotifyTimezoneChangeOnMainThread(",
        "void DispatchTimeZoneChangeEventToFrames(",
        "TimeZoneController::SetTimeZoneOverride(",
        "void TimeZoneController::ChangeTimeZoneOverride(",
        "void TimeZoneController::ClearTimeZoneOverride(",
    ):
        assert block(patched, signature) == block(original, signature)
    apply = block(patched, "bool TimeZoneController::SetDefaultTimeZone(")
    assert "HasTimeZoneOverride()" in apply
    assert apply.index("SetIcuTimeZoneAndNotifyV8(") < apply.index("host_timezone_id_ =")
    monitor = block(patched, "void TimeZoneController::OnTimeZoneChange(")
    assert 'Get("uxr-timezone")' in monitor
    assert "SetDefaultTimeZone(effective_id);" in monitor


def test_manifest_appends_226_without_changing_png_addition():
    data = json.loads((ROOT / "build/patch-additions-windows155.json").read_text())
    series = [line for line in (ROOT / "patches/series").read_text().splitlines()
              if line.strip() and not line.startswith("#")]
    assert len(series) == 224
    assert len(series) + len(data["patches"]) == 226
    assert data["patches"][0] == {
        "path": "patches/chromium155-windows/0227-opaque-png-export-consistency.patch",
        "sha256": "4c048a50930c3541a3bb9e4442ad7f18e94c04ebccf86c0cfbfc286b15a70751",
    }
    assert data["patches"][1] == {
        "path": PATCH, "sha256": hashlib.sha256((ROOT / PATCH).read_bytes()).hexdigest(),
    }


def icu_flags():
    prefix = os.environ.get("CHROMIX_TIMEZONE_ICU_ROOT")
    if not prefix and Path("/opt/homebrew/opt/icu4c/include/unicode/timezone.h").exists():
        prefix = "/opt/homebrew/opt/icu4c"
    if prefix:
        return ["-I" + prefix + "/include", "-L" + prefix + "/lib",
                "-Wl,-rpath," + prefix + "/lib", "-licui18n", "-licuuc", "-licudata"]
    program = shutil.which("pkg-config")
    if program:
        result = subprocess.run([program, "--cflags", "--libs", "icu-i18n"],
                                capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            import shlex
            return shlex.split(result.stdout)
    pytest.skip("ICU development libraries required; set CHROMIX_TIMEZONE_ICU_ROOT")


@pytest.fixture(scope="module")
def native_binary(tmp_path_factory, sources):
    compiler = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("c++")
    if not compiler:
        pytest.skip("C++20 compiler required")
    controller = sources[CONTROLLER + ".cc"]
    header = "\n".join(line for line in sources[CONTROLLER + ".h"].splitlines()
                       if not line.startswith("#include"))
    definitions = "\n".join(block(controller, signature) for signature in (
        "TimeZoneController::TimeZoneController()",
        "TimeZoneController& TimeZoneController::instance()",
        "bool CanonicalEquals(",
        "TimeZoneController::TimeZoneOverrideResult\nTimeZoneController::SetTimeZoneOverride(",
        "bool TimeZoneController::HasTimeZoneOverride()",
        "const String& TimeZoneController::TimeZoneIdOverride()",
        "void TimeZoneController::ClearTimeZoneOverride()",
        "void TimeZoneController::ChangeTimeZoneOverride(",
        "bool TimeZoneController::SetIcuTimeZoneAndNotifyV8(",
        "bool TimeZoneController::SetDefaultTimeZone(",
        "void TimeZoneController::OnTimeZoneChange(",
        "const String& TimeZoneController::GetHostTimezoneId()",
        "void TimeZoneController::ChangeTimeZoneForTesting(",
    ))
    unit = (FIXTURES / "windows155_timezone_shim.h").read_text() + header
    unit += "\nnamespace blink {\n" + definitions
    unit += "\nTimeZoneController::~TimeZoneController() = default;\n"
    unit += block(sources[BRIDGE], "bool SetRendererDefaultTimeZone(") + "\n}\n"
    unit += block(sources[RENDERER], "void RenderThreadImpl::SetUxrConfig(")
    unit += (FIXTURES / "windows155_timezone_cases.cc").read_text()
    directory = tmp_path_factory.mktemp("windows155-timezone-native")
    cpp, binary = directory / "timezone.cc", directory / "timezone"
    cpp.write_text(unit)
    result = subprocess.run([compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror",
                             str(cpp), *icu_flags(), "-o", str(binary)],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return binary


@pytest.mark.parametrize("case", [
    "monitor-early", "monitor-late", "no-timezone", "empty-timezone",
    "override-before", "override-after", "override-same", "invalid-default",
    "invalid-under-override", "invalid-launch", "immutable-snapshot", "replay",
])
@pytest.mark.parametrize("lazy", [False, True])
def test_native_icu_install_order_and_override(native_binary, case, lazy):
    result = subprocess.run([str(native_binary), case, str(int(lazy))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mutation,case", [
    ("missing-install", "monitor-early"),
    ("missing-install", "monitor-late"),
    ("missing-main-notification", "monitor-late"),
    ("missing-worker-notification", "monitor-late"),
])
def test_harness_detects_install_and_notification_regressions(native_binary, tmp_path,
                                                             mutation, case):
    source = (native_binary.parent / "timezone.cc").read_text()
    before, after = {
        "missing-install": (
            "return TimeZoneController::SetDefaultTimeZone(timezone_id);",
            "(void)timezone_id; return true;"),
        "missing-main-notification": (
            "    NotifyTimezoneChangeOnMainThread();", "    // Mutation: no main-isolate notification."),
        "missing-worker-notification": (
            "  WorkerThread::CallOnAllWorkerThreads(&NotifyTimezoneChangeOnWorkerThread,\n"
            "                                       TaskType::kInternalDefault);",
            "  // Mutation: no worker notification."),
    }[mutation]
    assert source.count(before) == 1
    cpp, binary = tmp_path / "mutated.cc", tmp_path / "mutated"
    cpp.write_text(source.replace(before, after))
    compiler = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("c++")
    compiled = subprocess.run([compiler, "-std=c++20", str(cpp), *icu_flags(), "-o", str(binary)],
                              capture_output=True, text=True, timeout=90)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([str(binary), case, "0"], capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "assert" in result.stderr.lower()


def test_optional_independent_exact_sources():
    directory = os.environ.get("CHROMIX_WINDOWS155_TIMEZONE_SOURCES")
    if not directory:
        pytest.skip("set CHROMIX_WINDOWS155_TIMEZONE_SOURCES to independently fetched exact tag")
    for name, entry in EVIDENCE["sources"].items():
        assert hashlib.sha256((Path(directory) / name).read_bytes()).hexdigest() == entry["sha256"]
