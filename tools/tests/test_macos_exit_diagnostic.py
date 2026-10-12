"""Direct shebang fixtures; no browser artifact, download, or native sampler required."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tools import macos_exit_diagnostic as diagnostic


@unittest.skipUnless(os.name == "posix", "requires POSIX sessions and resource limits")
class MacOSExitDiagnosticTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="exit diagnostic ' ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.browser = self.root / "Actual Browser"
        self.output = self.root / "evidence"
        self.profile = self.root / "fresh profile"
        self.processes = []
        self.streams = []
        self.kills = []
        original_popen = subprocess.Popen
        original_killpg = os.killpg

        def launch(*args, **kwargs):
            self.assertTrue(kwargs["start_new_session"])
            command = args[0]
            if command[0] == str(self.browser):
                self.assertNotIn("preexec_fn", kwargs)
                self.assertEqual(kwargs["stdout"], subprocess.PIPE)
                self.assertEqual(kwargs["stderr"], subprocess.PIPE)
            if "preexec_fn" in kwargs:
                self.assertIn("-file", command)
            process = original_popen(*args, **kwargs)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    self.streams.append(stream)
            self.processes.append(process)
            return process

        def killpg(group, sig):
            self.kills.append((group, sig))
            return original_killpg(group, sig)

        for patcher in (
                mock.patch.object(diagnostic, "BROWSER_TIMEOUT", 3.0),
                mock.patch.object(diagnostic, "SAMPLE_AFTER", 0.12),
                mock.patch.object(diagnostic, "SAMPLE_TIMEOUT", 1.0),
                mock.patch.object(diagnostic, "PS_TIMEOUT", 1.0),
                mock.patch.object(diagnostic.sys, "platform", "darwin"),
                mock.patch.object(diagnostic.subprocess, "Popen", side_effect=launch),
                mock.patch.object(diagnostic.os, "killpg", side_effect=killpg),
                mock.patch.object(diagnostic, "_ps_command", return_value=[sys.executable, "-c", "print('snapshot')"])):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.cleanup_processes)
        self.sample_source = "from pathlib import Path; import sys; Path(sys.argv[-1]).write_text('real sample evidence')"
        self.sample_targets = []

        def sample_command(pid, path):
            self.sample_targets.append((pid, path))
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent, self.output)
            return [sys.executable, "-c", self.sample_source, str(pid), "1", "10", "-file", str(path)]

        patcher = mock.patch.object(diagnostic, "_sample_command", side_effect=sample_command)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cleanup_processes(self):
        for process in self.processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)

    def fixture(self, source=None):
        if source is None:
            source = f"print({diagnostic.DOM_MARKER!r}, flush=True)"
        self.browser.write_text(
            f"#!{sys.executable}\nimport os, sys, time, signal, subprocess\n"
            "from pathlib import Path\n"
            "profile = Path(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--user-data-dir=')))\n"
            "assert list(profile.iterdir()) == []\n"
            "(profile / 'cache').write_text('owned')\n" + source + "\n")
        self.browser.chmod(0o755)

    def run_fixture(self):
        result = diagnostic.run_diagnostic(self.browser, self.output, self.profile)
        self.assertEqual(result, json.loads((self.output / "report.json").read_text()))
        self.assertTrue(result["profile_cleaned"])
        self.assertFalse(self.profile.exists())
        self.assertEqual(result["original_status"], "failed")
        self.assertFalse(result["release_allowed"])
        self.assertTrue(all(stream.closed for stream in self.streams))
        owned = {process.pid for process in self.processes}
        self.assertTrue(all(group in owned and sig == signal.SIGKILL for group, sig in self.kills))
        for process in self.processes:
            process.wait(timeout=1)
        captures = [path for path in self.output.iterdir() if path.name != "report.json"]
        self.assertTrue(all(path.stat().st_size <= diagnostic.MAX_FILE_BYTES for path in captures))
        self.assertLessEqual(sum(path.stat().st_size for path in captures), result["max_capture_bytes"])
        return result

    def test_normal_exit_exact_flags_and_direct_pid(self):
        self.fixture("import json\nprint(json.dumps({'argv':sys.argv[1:], 'pid':os.getpid()}))\n"
                     f"print({diagnostic.DOM_MARKER!r})\nprint('stderr evidence',file=sys.stderr)")
        with mock.patch.object(diagnostic, "SAMPLE_AFTER", 8):
            result = self.run_fixture()
        self.assertEqual(result["status"], "passed")
        headless = result["headless"]
        evidence = json.loads((self.output / "headless.stdout.log").read_text().splitlines()[0])
        self.assertEqual(evidence["pid"], headless["pid"])
        self.assertEqual(headless["command"][0], str(self.browser))
        self.assertEqual(evidence["argv"], [
            "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={self.profile}", "--dump-dom", diagnostic.SMOKE_URL])
        self.assertEqual(headless["natural_returncode"], 0)
        self.assertEqual(headless["returncode_after_cleanup"], 0)
        self.assertEqual((self.output / "headless.stderr.log").read_text(), "stderr evidence\n")
        self.assertEqual(result["diagnostics"]["sample"]["status"], "skipped")
        self.assertEqual(result["diagnostics"]["ps_after"]["status"], "passed")

    def test_marker_hang_is_failed_with_early_sample_and_two_snapshots(self):
        self.fixture(f"print({diagnostic.DOM_MARKER!r},flush=True)\ntime.sleep(30)")
        result = self.run_fixture()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["headless"]["status"], "timeout")
        self.assertTrue(result["headless"]["marker_present"])
        self.assertIsNone(result["headless"]["natural_returncode"])
        self.assertIsNone(result["headless"]["returncode"])
        self.assertIn(result["headless"]["returncode_after_cleanup"], (None, -signal.SIGKILL))
        self.assertEqual(self.sample_targets[0][0], result["headless"]["pid"])
        self.assertEqual(self.sample_targets[0][1].read_text(), "real sample evidence")
        self.assertEqual(result["diagnostics"]["sample"]["status"], "passed")
        for key in ("ps_early", "ps_after"):
            self.assertEqual(result["diagnostics"][key]["status"], "passed")
        self.assertEqual(result["diagnostics"]["ps_after"]["phase"], "after_sample")
        self.assertLess(result["elapsed_seconds"], 3.5)

    def test_nonzero_and_signal_are_not_hidden_by_marker(self):
        for index, (ending, code) in enumerate((("sys.exit(2)", 2),
                                                ("os.kill(os.getpid(),signal.SIGTERM)", -signal.SIGTERM))):
            with self.subTest(code=code):
                self.output = self.root / f"evidence-{index}"
                self.fixture(f"print({diagnostic.DOM_MARKER!r},flush=True)\n{ending}")
                result = self.run_fixture()
                self.assertFalse(result["passed"])
                self.assertEqual(result["headless"]["status"], "nonzero_exit")
                self.assertEqual(result["headless"]["natural_returncode"], code)

    def test_sample_does_not_delay_browser_exit_observation(self):
        self.sample_source = "import time; time.sleep(30)"
        self.fixture(f"print({diagnostic.DOM_MARKER!r},flush=True)\ntime.sleep(0.22)")
        with mock.patch.object(diagnostic, "SAMPLE_TIMEOUT", 2.0), \
                mock.patch.object(diagnostic, "BROWSER_TIMEOUT", 3.0):
            result = self.run_fixture()
        self.assertEqual(result["headless"]["status"], "passed")
        self.assertEqual(result["headless"]["natural_returncode"], 0)
        sample = result["diagnostics"]["sample"]
        self.assertEqual(sample["status"], "timeout")
        self.assertLess(result["headless"]["observed_monotonic"], sample["observed_monotonic"] - 0.2)
        self.assertLess(result["browser_observed_elapsed_seconds"], 1.8)
        self.assertFalse(result["passed"])

    def test_sample_budget_never_extends_original_deadline(self):
        self.sample_source = "import time; time.sleep(30)"
        self.fixture(f"print({diagnostic.DOM_MARKER!r},flush=True)\ntime.sleep(30)")
        with mock.patch.object(diagnostic, "BROWSER_TIMEOUT", 0.45), \
                mock.patch.object(diagnostic, "SAMPLE_TIMEOUT", 15):
            result = self.run_fixture()
        self.assertEqual(result["headless"]["status"], "timeout")
        self.assertEqual(result["diagnostics"]["sample"]["status"], "timeout")
        self.assertLess(result["elapsed_seconds"], 0.9)

    def test_inherited_file_descriptors_do_not_fake_browser_hang(self):
        child = "import time; time.sleep(30)"
        self.fixture(f"p=subprocess.Popen([sys.executable,'-c',{child!r}])\n"
                     f"print({diagnostic.DOM_MARKER!r},flush=True)\nprint(p.pid,flush=True)")
        result = self.run_fixture()
        self.assertTrue(result["passed"])
        self.assertEqual(result["headless"]["natural_returncode"], 0)
        self.assertEqual(result["headless"]["returncode_after_cleanup"], 0)
        self.assertEqual(result["headless"]["status"], "passed")
        self.assertLess(result["browser_observed_elapsed_seconds"], 0.9)
        self.assertIn((result["headless"]["pid"], signal.SIGKILL), self.kills)
        child_pid = int((self.output / "headless.stdout.log").read_text().splitlines()[1])
        with (self.root / "child-stat").open("w+b") as state:
            code = subprocess.call(["/bin/ps", "-p", str(child_pid), "-o", "stat="],
                                   stdout=state, stderr=state, start_new_session=True)
            state.seek(0)
            self.assertTrue(code != 0 or state.read().strip().startswith(b"Z"))

    def test_bounded_capture_limits_output_from_children_between_polls(self):
        source = ("import os, signal, time\n"
                  "signal.signal(signal.SIGXFSZ,signal.SIG_IGN)\n"
                  "try:\n    os.write(1,b'x'*10000000)\n    os.write(2,b'y'*10000000)\n"
                  "except OSError:\n    pass\n"
                  "time.sleep(30)")
        self.fixture(f"subprocess.Popen([sys.executable,'-c',{source!r}])\ntime.sleep(30)")
        with mock.patch.object(diagnostic, "MAX_FILE_BYTES", 4096), \
                mock.patch.object(diagnostic, "POLL_INTERVAL", 0.2):
            result = self.run_fixture()
            self.assertEqual(result["headless"]["status"], "output_limit")
            self.assertFalse(result["passed"])
            self.assertLessEqual(result["headless"]["captured_bytes"], 8192)

    def test_profile_two_mib_writes_in_browser_and_child_are_not_limited(self):
        child = "import sys; from pathlib import Path; Path(sys.argv[1]).write_bytes(b'c'*(2*1024*1024))"
        self.fixture(
            "import resource, json\n"
            "(profile / 'database').write_bytes(b'p' * (2*1024*1024))\n"
            f"subprocess.run([sys.executable,'-c',{child!r},str(profile / 'child-cache')],check=True)\n"
            "assert (profile / 'database').stat().st_size == 2*1024*1024\n"
            "assert (profile / 'child-cache').stat().st_size == 2*1024*1024\n"
            "print(json.dumps(resource.getrlimit(resource.RLIMIT_FSIZE)))\n"
            f"print({diagnostic.DOM_MARKER!r},flush=True)")
        with mock.patch.object(diagnostic, "SAMPLE_AFTER", 8):
            result = self.run_fixture()
        import resource
        limits = json.loads((self.output / "headless.stdout.log").read_text().splitlines()[0])
        self.assertEqual(limits, list(resource.getrlimit(resource.RLIMIT_FSIZE)))
        self.assertTrue(result["passed"])
        self.assertEqual(result["headless"]["natural_returncode"], 0)
        self.assertFalse(result["browser_resource_limits_modified"])

    def test_two_mib_stdout_or_stderr_fails_with_bounded_capture_file(self):
        for fd, stream in ((1, "stdout"), (2, "stderr")):
            with self.subTest(stream=stream):
                self.output = self.root / f"overflow-{stream}"
                self.fixture(f"os.write({fd},b'x'*(2*1024*1024))\ntime.sleep(30)")
                result = self.run_fixture()
                self.assertEqual(result["headless"]["status"], "output_limit")
                self.assertFalse(result["passed"])
                self.assertEqual((self.output / f"headless.{stream}.log").stat().st_size,
                                 diagnostic.MAX_FILE_BYTES)
                self.assertIn((result["headless"]["pid"], signal.SIGKILL), self.kills)

    def test_sample_real_output_also_has_hard_file_limit(self):
        self.sample_source = "import sys; open(sys.argv[-1],'wb').write(b'x'*10000000)"
        self.fixture(f"print({diagnostic.DOM_MARKER!r},flush=True)\ntime.sleep(0.4)")
        with mock.patch.object(diagnostic, "MAX_FILE_BYTES", 4096):
            result = self.run_fixture()
        self.assertEqual(result["diagnostics"]["sample"]["status"], "output_limit")
        self.assertFalse(result["passed"])

    def test_sample_nonzero_or_empty_file_cannot_report_passed(self):
        for index, source in enumerate(("import sys; sys.exit(2)", "pass")):
            with self.subTest(source=source):
                self.output = self.root / f"sample-failure-{index}"
                self.sample_source = source
                self.fixture(f"print({diagnostic.DOM_MARKER!r},flush=True)\ntime.sleep(0.4)")
                result = self.run_fixture()
                self.assertEqual(result["headless"]["natural_returncode"], 0)
                self.assertFalse(result["passed"])
                self.assertEqual(result["capture_status"], "failed")

    def test_missing_marker_and_launch_error_fail(self):
        self.fixture("print('wrong marker')")
        result = self.run_fixture()
        self.assertEqual(result["headless"]["status"], "marker_missing")
        self.output = self.root / "missing-executable"
        self.browser.unlink()
        result = self.run_fixture()
        self.assertEqual(result["headless"]["status"], "launch_error")
        self.assertFalse(result["passed"])

    def test_sampling_platform_guard_and_precise_native_arguments(self):
        self.fixture(f"print({diagnostic.DOM_MARKER!r},flush=True)\ntime.sleep(0.3)")
        with mock.patch.object(diagnostic.sys, "platform", "linux"):
            result = self.run_fixture()
        self.assertEqual(result["diagnostics"]["sample"]["status"], "not_supported")
        self.assertEqual(self.sample_targets, [])

    def test_existing_profile_and_evidence_are_not_touched(self):
        self.fixture()
        self.profile.mkdir()
        keep = self.profile / "keep"
        keep.write_text("untouched")
        with self.assertRaises(FileExistsError):
            diagnostic.run_diagnostic(self.browser, self.output, self.profile)
        self.assertEqual(keep.read_text(), "untouched")
        keep.unlink()
        self.profile.rmdir()
        self.output.mkdir()
        keep = self.output / "keep"
        keep.write_text("untouched")
        with self.assertRaises(FileExistsError):
            diagnostic.run_diagnostic(self.browser, self.output, self.profile)
        self.assertEqual(keep.read_text(), "untouched")

    def test_output_inside_profile_and_symlink_rejected(self):
        with self.assertRaises(ValueError):
            diagnostic.run_diagnostic(self.browser, self.profile / "evidence", self.profile)
        target = self.root / "target"
        target.mkdir()
        self.output.symlink_to(target, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            diagnostic.run_diagnostic(self.browser, self.output, self.profile)
        self.assertEqual(list(target.iterdir()), [])

    def test_diagnostic_launch_exception_cleans_browser(self):
        self.fixture("time.sleep(30)")
        with mock.patch.object(diagnostic, "_start_sample", side_effect=OSError("sample unavailable")):
            result = self.run_fixture()
        self.assertFalse(result["passed"])
        self.assertEqual(result["error"], "sample unavailable")
        self.assertEqual(result["headless"]["status"], "capture_error")
        self.assertIsNone(result["headless"]["natural_returncode"])

    def test_cleanup_error_cannot_pass(self):
        self.fixture()
        with mock.patch.object(diagnostic.shutil, "rmtree", side_effect=OSError("cleanup denied")):
            result = diagnostic.run_diagnostic(self.browser, self.output, self.profile)
        self.assertFalse(result["passed"])
        self.assertEqual(result["profile_cleanup_error"], "cleanup denied")

    def test_cli_exposes_no_gate_workaround_or_budget_override(self):
        from contextlib import redirect_stderr
        args = ["--browser", str(self.browser), "--output", str(self.output), "--profile", str(self.profile)]
        for option in ("--mock", "--no-sandbox", "--disable-features=Foo", "--timeout=120", "--launcher=x"):
            with self.subTest(option=option), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                diagnostic.main(args + [option])
            self.assertEqual(error.exception.code, 2)

    def test_cli_json_returns_one_for_browser_failure(self):
        self.fixture(f"print({diagnostic.DOM_MARKER!r}); sys.exit(2)")
        args = ["--browser", str(self.browser), "--output", str(self.output), "--profile", str(self.profile)]
        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(diagnostic.main(args), 1)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["headless"]["natural_returncode"], 2)
        self.assertEqual(result["original_status"], "failed")
        self.assertFalse(result["release_allowed"])
        self.output = self.root / "normal-evidence"
        self.fixture()
        args[3] = str(self.output)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(diagnostic.main(args), 0)


class FixedConfigurationTest(unittest.TestCase):
    def test_production_budgets_and_native_sample_command(self):
        self.assertEqual((diagnostic.BROWSER_TIMEOUT, diagnostic.SAMPLE_AFTER, diagnostic.SAMPLE_TIMEOUT), (60, 8, 15))
        self.assertEqual(diagnostic._sample_command(123, Path('/tmp/sample-real.txt')),
                         ["/usr/bin/sample", "123", "1", "10", "-file", "/tmp/sample-real.txt"])


if __name__ == "__main__":
    unittest.main()
