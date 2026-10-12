#!/usr/bin/env python3
"""Diagnose an existing Intel macOS executable; never replace the original smoke gate."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import datetime as dt
import json
import os
from pathlib import Path
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

BROWSER_TIMEOUT = 60
SAMPLE_AFTER = 8
SAMPLE_TIMEOUT = 15
PS_TIMEOUT = 2
POLL_INTERVAL = 0.02
MAX_FILE_BYTES = 1024 * 1024
DOM_MARKER = "<p>chromix-smoke-ok</p>"
SMOKE_URL = "data:text/html," + DOM_MARKER


def _file_limit(limit):
    # Only the standalone sampler receives a regular-file size limit.
    _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    ceiling = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
    resource.setrlimit(resource.RLIMIT_FSIZE, (ceiling, ceiling))


def _sample_command(pid, path):
    return ["/usr/bin/sample", str(pid), "1", "10", "-file", str(path)]


def _ps_command():
    return ["/bin/ps", "-axo", "pid=,ppid=,pgid=,stat=,etime=,comm="]


class _Job:
    def __init__(self, command, output, name, stack, deadline, *, extra_paths=(),
                 limit_sample_file=False):
        self.process = None
        self.deadline = deadline
        self.selector = stack.enter_context(selectors.DefaultSelector())
        self.written = [0, 0]
        self.overflow = False
        self.paths = [output / f"{name}.{stream}.log" for stream in ("stdout", "stderr")]
        self.paths.extend(extra_paths)
        self.result = {
            "command": command, "status": "running", "returncode": None,
            "natural_returncode": None, "stdout_path": str(self.paths[0]),
            "stderr_path": str(self.paths[1]), "file_limit_bytes": MAX_FILE_BYTES,
            "started_monotonic": time.monotonic(), "deadline_monotonic": deadline,
        }
        streams = [stack.enter_context(path.open("xb", buffering=0)) for path in self.paths[:2]]
        self.streams = streams
        options = {"preexec_fn": lambda: _file_limit(MAX_FILE_BYTES)} if limit_sample_file else {}
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, start_new_session=True, **options)
            self.result.update(pid=self.process.pid, process_group=self.process.pid)
            for index, pipe in enumerate((self.process.stdout, self.process.stderr)):
                stack.enter_context(pipe)
                os.set_blocking(pipe.fileno(), False)
                self.selector.register(pipe, selectors.EVENT_READ, index)
        except (OSError, subprocess.SubprocessError) as error:
            self.result.update(status="launch_error", error=str(error))

    def drain(self):
        # Bound each turn so a noisy descendant cannot starve leader polling.
        budget = 256 * 1024
        while budget > 0 and self.selector.get_map():
            ready = self.selector.select(0)
            if not ready:
                break
            for key, _ in ready:
                try:
                    chunk = os.read(key.fd, min(65536, budget))
                except BlockingIOError:
                    continue
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    continue
                budget -= len(chunk)
                index = key.data
                available = MAX_FILE_BYTES - self.written[index]
                captured = chunk[:available]
                self.streams[index].write(captured)
                self.written[index] += len(captured)
                if len(chunk) > available:
                    self.overflow = True
                    self.result["status"] = "output_limit"
                    return
                if budget <= 0:
                    break

    def poll(self, now):
        if self.result["status"] != "running":
            return
        # Pipe EOF is independent of the lifetime of the directly executed leader.
        code = self.process.poll()
        if code is not None:
            self.result.update(returncode=code, natural_returncode=code)
        self.drain()
        if self.overflow or any(path.stat().st_size >= MAX_FILE_BYTES for path in self.paths[2:]):
            status = "output_limit"
        elif now >= self.deadline:
            status = "timeout"
        elif code is not None:
            status = "passed" if code == 0 else "nonzero_exit"
        else:
            return
        self.result.update(status=status, observed_monotonic=now)

    def cleanup(self, deadline):
        if self.result.get("cleanup_attempted") or self.process is None:
            return
        self.result["cleanup_attempted"] = True
        # A natural leader exit does not imply that its descendants have exited.
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            self.result.setdefault("cleanup_errors", []).append(str(error))
        try:
            self.process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            self.result["reap_pending"] = True
        except OSError as error:
            self.result.setdefault("cleanup_errors", []).append(str(error))
        self.result["returncode_after_cleanup"] = self.process.poll()

    def cancel(self, reason, deadline):
        if self.result["status"] == "running":
            self.result.update(status=reason, observed_monotonic=time.monotonic())
        self.cleanup(deadline)

    def evidence(self):
        # Cleanup has stopped owned writers; never wait for inherited pipe EOF.
        for _ in range(2 * MAX_FILE_BYTES // (256 * 1024) + 2):
            before = sum(self.written)
            self.drain()
            if self.overflow or sum(self.written) == before:
                break
        sizes = {str(path): path.stat().st_size for path in self.paths}
        self.result["file_bytes"] = sizes
        self.result["captured_bytes"] = sum(sizes.values())
        if self.overflow or any(sizes[str(path)] >= MAX_FILE_BYTES for path in self.paths[2:]):
            self.result["status"] = "output_limit"
        if self.result["status"] == "passed" and len(self.paths) > 2 and not sizes[str(self.paths[2])]:
            self.result.update(status="capture_error", error="sample returned no file evidence")
        if self.process is not None:
            self.result["returncode_after_cleanup"] = self.process.poll()
            if self.process.returncode is not None:
                self.result.pop("reap_pending", None)
        return self.result


def _start_sample(pid, output, stack, deadline):
    # sample requires a real pathname, not a pipe or /dev/stdout.
    fd, name = tempfile.mkstemp(prefix="sample-", suffix=".txt", dir=output)
    os.close(fd)
    path = Path(name)
    job = _Job(_sample_command(pid, path), output, "sample", stack,
               min(deadline, time.monotonic() + SAMPLE_TIMEOUT), extra_paths=(path,),
               limit_sample_file=True)
    job.result.update(target_pid=pid, sample_path=str(path), timeout_seconds=SAMPLE_TIMEOUT)
    return job


def run_diagnostic(browser, output, profile):
    """Run the supplied executable directly, with a fresh owned profile and fixed budgets."""
    browser = os.path.abspath(browser)
    profile_argument = os.fspath(profile)
    profile_path, output = Path(profile).absolute(), Path(output).absolute()
    if profile_path.exists() or profile_path.is_symlink():
        raise FileExistsError("profile must be a new directory; existing data is never removed")
    if output.resolve().is_relative_to(profile_path.resolve()):
        raise ValueError("output must not be inside the disposable profile")
    if output.is_symlink() or output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError("output must be a new or empty directory; evidence is never overwritten")
    if Path(browser).resolve().is_relative_to(profile_path.resolve()):
        raise ValueError("browser must not be inside the disposable profile")
    output.mkdir(parents=True, exist_ok=True)
    command = [browser, "--headless", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", f"--user-data-dir={profile_argument}",
               "--dump-dom", SMOKE_URL]
    report = {
        "status": "failed", "passed": False, "diagnostic_only": True,
        "original_status": "failed", "release_allowed": False,
        "original_conclusion": "Original smoke remains failed; diagnostic evidence does not authorize release.",
        "browser": browser, "cwd": str(Path.cwd()), "profile": profile_argument,
        "profile_cleaned": False, "timeout_seconds": BROWSER_TIMEOUT,
        "sample_after_seconds": SAMPLE_AFTER, "sample_timeout_seconds": SAMPLE_TIMEOUT,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "diagnostics": {}, "headless": None,
        "output_limit_policy": "Nonblocking pipes drained to bounded files; overflow fails and kills the owned group. Only sample uses RLIMIT_FSIZE.",
        "max_capture_bytes": 9 * MAX_FILE_BYTES,
        "capture_limit_scope": "stdout/stderr/sample files; not browser-created profile files",
        "browser_resource_limits_modified": False,
    }
    started = time.monotonic()
    deadline = started + BROWSER_TIMEOUT
    jobs = []
    browser_job = None
    owned_profile = False
    with (output / "report.json").open("x", encoding="utf-8") as report_file, ExitStack() as stack:
        try:
            profile_path.mkdir(mode=0o700, parents=True, exist_ok=False)
            owned_profile = True
            browser_job = _Job(command, output, "headless", stack, deadline)
            jobs.append(browser_job)
            report["headless"] = browser_job.result
            early_started = False
            after_started = False
            browser_observed = False
            while True:
                now = time.monotonic()
                # Observe the browser before any diagnostic completion or cleanup wait.
                browser_job.poll(now)
                if browser_job.result["status"] != "running" and not browser_observed:
                    report["browser_observed_elapsed_seconds"] = now - started
                    browser_observed = True
                    browser_job.cleanup(now)
                sample_result = report["diagnostics"].get("sample", {})
                after_reason = (
                    "browser_exit_observed" if browser_observed else
                    "after_sample" if sample_result.get("status") not in (None, "running") else
                    "before_original_deadline" if now >= deadline - PS_TIMEOUT else None)
                if not after_started and after_reason is not None:
                    after_started = True
                    if now < deadline:
                        after = _Job(_ps_command(), output, "ps-after", stack,
                                     min(deadline, now + PS_TIMEOUT))
                        after.result["phase"] = after_reason
                        jobs.append(after)
                        report["diagnostics"]["ps_after"] = after.result
                    else:
                        report["diagnostics"]["ps_after"] = {
                            "status": "skipped", "reason": "original_deadline_exhausted"}
                if not early_started and now - started >= SAMPLE_AFTER and browser_job.result["status"] == "running":
                    early_started = True
                    early = _Job(_ps_command(), output, "ps-early", stack,
                                 min(deadline, now + PS_TIMEOUT))
                    jobs.append(early)
                    report["diagnostics"]["ps_early"] = early.result
                    if sys.platform == "darwin":
                        sample = _start_sample(browser_job.process.pid, output, stack, deadline)
                        jobs.append(sample)
                        report["diagnostics"]["sample"] = sample.result
                    else:
                        report["diagnostics"]["sample"] = {"status": "not_supported", "platform": sys.platform}
                for job in jobs[1:]:
                    job.poll(time.monotonic())
                    if job.result["status"] != "running":
                        job.cleanup(time.monotonic())
                if time.monotonic() >= deadline:
                    browser_job.poll(time.monotonic())
                    report.setdefault("browser_observed_elapsed_seconds", time.monotonic() - started)
                    break
                if all(job.result["status"] != "running" for job in jobs):
                    break
                pause = min(POLL_INTERVAL, max(0, deadline - time.monotonic()))
                if browser_job.result["status"] == "running" and browser_job.selector.get_map():
                    browser_job.selector.select(pause)
                else:
                    time.sleep(pause)
            report["diagnostics"].setdefault("sample", {"status": "skipped", "reason": "browser_exited_before_sample"})
            report["diagnostics"].setdefault("ps_early", {"status": "skipped", "reason": "browser_exited_before_sample"})
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            report["error"] = str(error)
        finally:
            # No subprocess gets a fresh timeout after the original deadline.
            for job in jobs:
                job.cancel("capture_error" if "error" in report else "timeout", time.monotonic())
            for job in jobs:
                if job.process is not None:
                    try:
                        job.process.wait(timeout=max(0, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        job.result["reap_pending_at_deadline"] = True
                job.evidence()
            if browser_job is not None:
                with browser_job.paths[0].open("rb") as stream:
                    stdout = stream.read(MAX_FILE_BYTES)
                browser_job.result["marker_present"] = DOM_MARKER.encode() in stdout
                if browser_job.result["status"] == "passed" and not browser_job.result["marker_present"]:
                    browser_job.result["status"] = "marker_missing"
            if owned_profile:
                try:
                    shutil.rmtree(profile_path)
                    report["profile_cleaned"] = True
                except OSError as error:
                    report["profile_cleanup_error"] = str(error)
            capture_errors = any(job.result["status"] != "passed"
                                 or job.result.get("cleanup_errors") for job in jobs)
            report["capture_status"] = "failed" if "error" in report or capture_errors else "completed"
            report["passed"] = bool(
                browser_job is not None and browser_job.result["status"] == "passed"
                and browser_job.result["natural_returncode"] == 0
                and report["profile_cleaned"] and report["capture_status"] == "completed")
            report["status"] = "passed" if report["passed"] else "failed"
            report["elapsed_seconds"] = time.monotonic() - started
            json.dump(report, report_file, indent=2)
            report_file.write("\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", required=True, help="Actual browser executable, not an app directory or launcher")
    parser.add_argument("--output", required=True, help="New or empty evidence directory")
    parser.add_argument("--profile", required=True, help="Fresh disposable user-data directory (must not exist)")
    args = parser.parse_args(argv)
    try:
        report = run_diagnostic(args.browser, args.output, args.profile)
    except (OSError, ValueError) as error:
        report = {"status": "failed", "passed": False, "error": str(error),
                  "original_status": "failed", "release_allowed": False}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
