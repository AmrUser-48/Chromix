"""Linux generator installation is receipt-bound, offline by default, and non-executing."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.request

from tools import install_linux_generators as installer
from tools import linux_restored_generators as generators
from tools import platform_pins
from tools import prepare_restored_build as prepare
from tools import restore_upstream_cache as restore
from tools.tests import test_linux154_generators as generator_tests
from tools.tests import test_prepare_restored_build as prepare_tests
from tools.tests.test_linux_typescript import LINUX153_PINS, PORTABLE, install_linux_repo_fixture


class InstallLinuxGeneratorsTest(unittest.TestCase):
    def setUp(self):
        urls = {key: entry[0] for key, entry in generators.ARCHIVES.items()}
        self.data = generator_tests.Linux154GeneratorsTest()
        self.data.setUp()
        self.addCleanup(self.data.doCleanups)
        self.fixture = prepare_tests.PrepareRestoredBuildTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.fixture("linux", "arm64", host_arch="x64", linux_pins=generators.PINS)
        self.repo = self.fixture.fixture_repo
        self.work, self.src = self.fixture.work, self.fixture.src
        shutil.rmtree(self.src / prepare.TYPESCRIPT_PACKAGE)
        shutil.copytree(self.data.src / generators.DEVTOOLS, self.src / generators.DEVTOOLS, dirs_exist_ok=True)
        self.downloads = self.work / "offline downloads"
        self.downloads.mkdir()
        archives = {key: (urls[key], generators.sha256(path.read_bytes()))
                    for key, path in self.data.archives.items()}
        self.patch(generators, "ARCHIVES", archives)
        self.patch(prepare, "load_pins", platform_pins.load_pins)
        self.patch(prepare, "host_identity", return_value=("linux", "x64"))
        for key, source in self.data.archives.items():
            shutil.copyfile(source, self.archive(key))
        self.network = self.patch(installer, "fetch_archive", side_effect=AssertionError("unexpected download"))

    def patch(self, target, name, *args, **kwargs):
        patcher = mock.patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def archive(self, key):
        return self.downloads / generators.ARCHIVES[key][0].rsplit("/", 1)[-1]

    def install(self, **kwargs):
        return installer.install(self.work, "arm64", self.downloads, repo=self.repo, **kwargs)

    def test_offline_install_selects_host_package_without_execution_and_resumes_without_cache(self):
        self.archive("arm64").unlink()
        protected = {path: path.read_bytes() for path in (
            self.src / restore.MARKER, self.src / prepare.TYPESCRIPT_WRAPPER,
            *(self.src / generators.DEVTOOLS / name for name in generators.SOURCE_REPAIRS))}
        with mock.patch.object(prepare.subprocess, "run") as run:
            result = self.install()
            self.assertEqual(result["status"], "installed")
            self.assertEqual(result["esbuild"]["binary"]["host_arch"], "x64")
            shutil.rmtree(self.downloads)
            resumed = self.install(download=True)
        self.assertEqual(resumed["status"], "already_installed")
        self.assertFalse(self.downloads.exists())
        self.network.assert_not_called()
        run.assert_not_called()
        for path, data in protected.items():
            self.assertEqual(path.read_bytes(), data)

    def test_native_arm64_uses_only_arm64_archive(self):
        self.archive("x64").unlink()
        with mock.patch.object(prepare, "host_identity", return_value=("linux", "arm64")), \
                mock.patch.object(prepare.subprocess, "run") as run:
            result = self.install()
        self.assertEqual(result["esbuild"]["binary"]["host_arch"], "arm64")
        run.assert_not_called()

    def test_missing_offline_cache_and_nonexact_ci_flags_never_download(self):
        self.archive("module").unlink()
        for flag, download in ((None, False), (None, True), ("false", True), ("True", True), ("true", False)):
            with self.subTest(flag=flag, download=download), \
                    mock.patch.dict(os.environ, {} if flag is None else {"GITHUB_ACTIONS": flag}, clear=True), \
                    self.assertRaisesRegex(ValueError, "offline cache"):
                self.install(download=download)
        self.network.assert_not_called()
        self.assertFalse((self.src / generators.ESBUILD_MODULE).exists())

    def test_ci_downloads_only_missing_archives_and_publishes_verified_single_link_files(self):
        self.archive("x64").unlink()
        data = self.data.archives["x64"].read_bytes()
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True), \
                mock.patch.object(installer, "fetch_archive", return_value=data) as download, \
                mock.patch.object(prepare.subprocess, "run") as run:
            result = self.install(download=True)
        download.assert_called_once_with(*generators.ARCHIVES["x64"])
        self.assertEqual(result["status"], "installed")
        self.assertEqual(self.archive("x64").stat().st_nlink, 1)
        self.assertEqual(self.archive("x64").read_bytes(), data)
        run.assert_not_called()

    def test_invalid_receipt_source_and_pins_fail_before_any_archive_access(self):
        receipt = self.src / restore.MARKER
        original = receipt.read_bytes()
        value = json.loads(original)
        for key, changed in (("arch", "x64"), ("owner", "unknown"), ("manifest", {})):
            receipt.write_text(json.dumps(dict(value, **{key: changed})))
            with self.subTest(key=key), mock.patch.object(installer, "ensure_archive") as archive, \
                    self.assertRaises(restore.Miss):
                self.install(download=True)
            archive.assert_not_called()
        receipt.write_bytes(original)
        wrapper = self.src / prepare.TYPESCRIPT_WRAPPER
        for payload in (PORTABLE, wrapper.read_bytes() + b"unknown"):
            wrapper.write_bytes(payload)
            with mock.patch.object(installer, "ensure_archive") as archive, \
                    self.assertRaisesRegex(ValueError, "unknown restored"):
                self.install(download=True)
            archive.assert_not_called()
        install_linux_repo_fixture(self.repo, LINUX153_PINS)
        identity, _, manifest = restore.identities(self.repo, "linux", "arm64")
        value.update(identity=identity, manifest=manifest)
        receipt.write_text(json.dumps(value))
        self.fixture.write(self.src / "chrome/VERSION", "\n".join(
            f"{key}={value}" for key, value in zip(("MAJOR", "MINOR", "BUILD", "PATCH"), LINUX153_PINS[0].split("."))))
        with mock.patch.object(installer, "ensure_archive") as archive, \
                self.assertRaisesRegex(ValueError, "unverified Linux generator pins"):
            self.install(download=True)
        archive.assert_not_called()

    def test_unrecognized_hosts_and_reverse_cross_fail_closed(self):
        for host in (("macos", "arm64"), ("linux", "unknown")):
            with mock.patch.object(prepare, "host_identity", return_value=host), \
                    self.assertRaisesRegex(ValueError, "host/target"):
                self.install()
        self.fixture.fixture("linux", "x64", linux_pins=generators.PINS)
        with mock.patch.object(prepare, "host_identity", return_value=("linux", "arm64")), \
                self.assertRaisesRegex(ValueError, "host/target"):
            installer.install(self.work, "x64", self.downloads, repo=self.fixture.fixture_repo)
        self.network.assert_not_called()

    def test_hash_size_type_and_cache_links_are_rejected_without_replacement(self):
        path = self.archive("module")
        original = path.read_bytes()
        target = self.work / "outside archive"
        target.write_bytes(original)
        for kind in ("corrupt", "empty", "symlink", "hardlink", "directory", "fifo", "oversize"):
            path.unlink()
            if kind == "symlink":
                path.symlink_to(target)
            elif kind == "hardlink":
                os.link(target, path)
            elif kind == "directory":
                path.mkdir()
            elif kind == "fifo":
                os.mkfifo(path)
            elif kind == "oversize":
                with path.open("wb") as stream:
                    stream.truncate(installer.MAX_ARCHIVE + 1)
            else:
                path.write_bytes(b"" if kind == "empty" else original + b"unknown")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.install(download=True)
            self.assertEqual(target.read_bytes(), original)
            path.rmdir() if kind == "directory" else path.unlink()
            path.write_bytes(original)
        self.downloads.rename(self.work / "outside-cache")
        self.downloads.symlink_to(self.work / "outside-cache", target_is_directory=True)
        with self.assertRaises(restore.LocalError):
            self.install()
        self.network.assert_not_called()
        self.assertFalse((self.src / generators.ESBUILD_MODULE).exists())

    def test_linked_work_and_source_are_rejected_before_archive_access(self):
        for relative in ("src", "src/third_party/devtools-frontend/src/node_modules"):
            path = self.work / relative
            external = self.work / "outside"
            path.rename(external)
            path.symlink_to(external, target_is_directory=True)
            try:
                with mock.patch.object(installer, "ensure_archive") as archive, \
                        self.assertRaises((ValueError, restore.Miss)):
                    self.install()
                archive.assert_not_called()
            finally:
                path.unlink()
                external.rename(path)

    def test_receipt_change_during_download_prevents_installation(self):
        self.archive("x64").unlink()
        def fetch(*args):
            receipt = self.src / restore.MARKER
            data = json.loads(receipt.read_text())
            data["counts"] = {"changed": 1}
            receipt.write_text(json.dumps(data))
            return self.data.archives["x64"].read_bytes()
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True), \
                mock.patch.object(installer, "fetch_archive", side_effect=fetch), \
                self.assertRaisesRegex(ValueError, "receipt changed"):
            self.install(download=True)
        self.assertFalse((self.src / generators.ESBUILD_MODULE).exists())

    def test_archive_appearing_during_download_is_not_overwritten(self):
        path = self.archive("x64")
        path.unlink()
        def fetch(*args):
            path.write_bytes(b"must survive")
            return self.data.archives["x64"].read_bytes()
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True), \
                mock.patch.object(installer, "fetch_archive", side_effect=fetch), self.assertRaises(FileExistsError):
            self.install(download=True)
        self.assertEqual(path.read_bytes(), b"must survive")
        self.assertFalse((self.src / generators.ESBUILD_MODULE).exists())

    def test_inspect_install_finish_resume_preserves_strict_esbuild_probes(self):
        with self.fixture.native_context("linux", "x64"):
            native = prepare.subprocess.run.side_effect
            def probe(command, **kwargs):
                if command[0] == str(self.src / generators.ESBUILD_BINARY) or command[1] == "-e":
                    return self.data.probe(command, **kwargs)
                return native(command, **kwargs)
            with mock.patch.object(prepare.subprocess, "run", side_effect=probe) as run:
                inspected = self.fixture.prepare(self.work, "linux", "arm64", phase="inspect")
                self.assertTrue(inspected["generator_fingerprint"]["typescript"]["esbuild"]["install_needed"])
                self.install()
                first = self.fixture.prepare(self.work, "linux", "arm64")
                self.assertTrue(first["ready_for_gn"])
                shutil.rmtree(self.downloads)
                self.assertEqual(self.install()["status"], "already_installed")
                resumed = self.fixture.prepare(self.work, "linux", "arm64")
                self.assertEqual(resumed["counters"]["generator_rechecks"], 0)
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual(sum(command == [str(self.src / generators.ESBUILD_BINARY), "--version"]
                                     for command in commands), 2)
                self.assertEqual(sum("transformSync" in str(command) for command in commands), 2)
                marker = (self.src / prepare.MARKER).read_bytes()
                for failure in ("version", "module"):
                    def fail(command, **kwargs):
                        if command[0] == str(self.src / generators.ESBUILD_BINARY) and failure == "version":
                            return subprocess.CompletedProcess(command, 0, "0.25.2\n")
                        if command[1] == "-e" and failure == "module":
                            return subprocess.CompletedProcess(command, 1, "module mismatch")
                        return probe(command, **kwargs)
                    with mock.patch.object(prepare.subprocess, "run", side_effect=fail), \
                            self.assertRaisesRegex(ValueError, "esbuild .*probe failed"):
                        self.fixture.prepare(self.work, "linux", "arm64")
                    self.assertEqual((self.src / prepare.MARKER).read_bytes(), marker)


class LinuxGeneratorDownloadTest(unittest.TestCase):
    def response(self, data=b"archive", **kwargs):
        response = mock.MagicMock(status=200)
        response.headers = {"Content-Length": str(len(data))}
        response.geturl.return_value = generators.ARCHIVES["module"][0]
        response.read1.side_effect = [data, b""]
        response.__enter__.return_value = response
        for key, value in kwargs.items():
            setattr(response, key, value)
        return response

    def fetch(self, response, digest=None):
        with mock.patch.object(installer.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            data = installer.fetch_archive(generators.ARCHIVES["module"][0], digest or generators.sha256(b"archive"))
        return data, build

    def test_get_has_no_auth_proxy_or_redirect_and_has_timeout_and_size_bounds(self):
        data, build = self.fetch(self.response())
        self.assertEqual(data, b"archive")
        proxy, redirects = build.call_args.args
        self.assertIsInstance(proxy, urllib.request.ProxyHandler)
        self.assertEqual(proxy.proxies, {})
        self.assertIsInstance(redirects, installer.NoRedirect)
        call = build.return_value.open.call_args
        self.assertEqual(call.kwargs, {"timeout": 120})
        request = call.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.header_items(), [("Accept-encoding", "identity")])
        self.assertIsNone(request.data)
        opener = urllib.request.build_opener(proxy, redirects)
        self.assertFalse(any(isinstance(handler, (urllib.request.HTTPBasicAuthHandler,
                                                   urllib.request.HTTPDigestAuthHandler,
                                                   urllib.request.ProxyBasicAuthHandler))
                             for handler in opener.handlers))

    def test_redirects_including_same_origin_and_credentials_are_rejected(self):
        url = generators.ARCHIVES["module"][0]
        for target in (url, "https://example.invalid/file", "http://registry.npmjs.org/file",
                       "https://name:password@registry.npmjs.org/file", "file:///etc/passwd"):
            for code in (301, 302, 303, 307, 308):
                with self.subTest(target=target, code=code), self.assertRaisesRegex(ValueError, "redirect"):
                    installer.NoRedirect().redirect_request(urllib.request.Request(url), None, code, "", {}, target)

    def test_error_auth_status_url_encoding_size_and_hash_fail_closed(self):
        responses = [self.response(status=401), self.response(status=407), self.response(status=302),
                     self.response(status=500), self.response(headers={"Content-Encoding": "gzip"})]
        changed = self.response()
        changed.geturl.return_value = "https://example.invalid/file"
        responses.append(changed)
        for length in ("-1", "no", "0", str(installer.MAX_ARCHIVE + 1), "8"):
            responses.append(self.response(headers={"Content-Length": length}))
        for response in responses:
            with self.assertRaises(ValueError):
                self.fetch(response)
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.fetch(self.response(), "0" * 64)
        with mock.patch.object(installer, "MAX_ARCHIVE", 6), self.assertRaisesRegex(ValueError, "oversized"):
            self.fetch(self.response(headers={}))
        with mock.patch.object(installer.time, "monotonic", side_effect=[0, 121]), self.assertRaises(TimeoutError):
            self.fetch(self.response())
        response = self.response()
        response.read1.side_effect = TimeoutError("socket timeout")
        with self.assertRaises(TimeoutError):
            self.fetch(response)

    def test_untrusted_endpoints_never_open_or_create_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            downloads = Path(temporary) / "missing"
            official, digest = generators.ARCHIVES["module"]
            for url in (official.replace("https:", "http:"), official + "?token=bad",
                        official.replace("registry.npmjs.org", "registry.npmjs.org.evil.invalid"),
                        official.replace("registry.npmjs.org", "user:password@registry.npmjs.org")):
                with mock.patch.dict(generators.ARCHIVES, module=(url, digest)), \
                        mock.patch.object(installer, "fetch_archive") as fetch, \
                        self.assertRaisesRegex(ValueError, "endpoint"):
                    installer.ensure_archive(downloads, "module", download=True)
                fetch.assert_not_called()
                self.assertFalse(downloads.exists())


class LinuxGeneratorShellTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="linux generator shell ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo, self.work = self.root / "repo", self.root / "work"
        self.log = self.root / "calls.jsonl"
        (self.repo / "build/posix").mkdir(parents=True)
        (self.work / "src").mkdir(parents=True)
        shutil.copyfile(prepare.ROOT / "build/posix/prepare-restored-tools.sh",
                        self.repo / "build/posix/prepare-restored-tools.sh")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        shim = self.bin / "python3"
        shim.write_text(f'''#!{sys.executable}
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
if args[0] == '-c':
    raise SystemExit(subprocess.call([sys.executable, '-B', *args]))
with open(os.environ['CALL_LOG'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[0].endswith('/prepare_restored_build.py'):
    if args[args.index('--phase') + 1] == 'inspect':
        if os.environ.get('FAIL_INSPECT') == '1': raise SystemExit(17)
        print(json.dumps({{'compilers_native': True, 'tools': {{'bindgen': {{'native': True}},
            'node': {{'native': True}}}}, 'generator_fingerprint': {{'typescript': {{'esbuild':
            {{'install_needed': os.environ['NEEDED'] == '1'}}}}}}}}))
    elif os.environ.get('FAIL_FINISH') == '1': raise SystemExit(19)
elif args[0].endswith('/install_linux_generators.py'):
    if os.environ.get('FAIL_INSTALL') == '1': raise SystemExit(18)
elif args[0].endswith('install-sysroot.py'):
    pass
elif args[0] == '-':
    sys.stdin.read()
else:
    raise SystemExit(97)
''')
        shim.chmod(0o755)
        uname = self.bin / "uname"
        uname.write_text(f'#!{sys.executable}\nimport sys\nprint("Linux" if sys.argv[1] == "-s" else "x86_64")\n')
        uname.chmod(0o755)

    def run_shell(self, needed, ci=False, **extra):
        self.log.unlink(missing_ok=True)
        env = {"PATH": str(self.bin) + os.pathsep + os.defpath, "CALL_LOG": str(self.log),
               "NEEDED": str(int(needed)), "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(self.root),
               "CHROMIX_LINUX_GENERATOR_DOWNLOADS": str(self.root / "offline cache"), **extra}
        if ci:
            env["GITHUB_ACTIONS"] = "true"
        result = subprocess.run(["bash", str(self.repo / "build/posix/prepare-restored-tools.sh"),
                                 str(self.work), "linux", "x64"], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return result, calls

    def test_shell_inspects_then_installs_only_when_needed_before_finish(self):
        for needed in (True, False):
            for ci in (True, False):
                with self.subTest(needed=needed, ci=ci):
                    result, calls = self.run_shell(needed, ci)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(calls[0][1:3], ["--phase", "inspect"])
                    self.assertEqual(calls[-1][1:3], ["--phase", "finish"])
                    installs = [call for call in calls if call[0].endswith("/install_linux_generators.py")]
                    self.assertEqual(len(installs), int(needed))
                    if needed:
                        self.assertEqual("--download" in installs[0], ci)
                        self.assertEqual(installs[0][installs[0].index("--downloads") + 1], str(self.root / "offline cache"))
                        self.assertLess(calls.index(installs[0]), len(calls) - 1)
                    self.assertTrue((self.work / "src/.chromix-toolchain-ready").exists())

    def test_failed_inspect_install_or_finish_never_marks_tools_ready(self):
        for failure, code in (("FAIL_INSPECT", 17), ("FAIL_INSTALL", 18), ("FAIL_FINISH", 19)):
            with self.subTest(failure=failure):
                result, calls = self.run_shell(True, **{failure: "1"})
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertFalse((self.work / "src/.chromix-toolchain-ready").exists())
                if failure != "FAIL_FINISH":
                    self.assertFalse(any("finish" in call for call in calls))
                if failure == "FAIL_INSPECT":
                    self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
