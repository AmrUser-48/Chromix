"""The fixed failed x64 checkpoint resumes once, without a cold-build fallback."""
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/build-win155-x64-stage8-api-recovery.yml"
STAGE = ROOT / "build/windows/ci-stage.ps1"


def flow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_fixed_dispatch_and_isolated_checkouts():
    data = flow()
    assert data["on"] == {"workflow_dispatch": {}}
    assert data["permissions"] == {"contents": "read", "actions": "read"}
    assert set(data["jobs"]) == {"build-8", "complete"}
    assert data["env"]["CHROMIX_TARGET_ARCH"] == "x64"
    assert data["env"]["CHROMIX_BUILD_PROFILE"] == "native"
    assert data["env"]["CHROMIX_WINDOWS_CACHE_REQUIRED"] == "1"
    assert data["env"]["CHROMIX_USE_UPSTREAM_CACHE"] == "1"
    assert data["env"]["CHROMIX_PREFER_UPSTREAM_CACHE"] == "0"
    job = data["jobs"]["build-8"]
    assert job["runs-on"] == "windows-2022"
    assert job["timeout-minutes"] == 355
    assert job["defaults"]["run"]["working-directory"] == "target"
    target, donor = [step["with"] for step in job["steps"] if step.get("uses") == "actions/checkout@v4"]
    assert target == {"path": "target", "fetch-depth": 0, "persist-credentials": False}
    assert donor == {"path": "donor", "fetch-depth": 0, "persist-credentials": False,
                     "submodules": False, "repository": "xiaozhou26/Chromix",
                     "ref": "c05c458c021f0f0964ec031c23395e1b86deb46f"}


def test_strict_order_budget_actual_parameters_and_no_source_download():
    steps = flow()["jobs"]["build-8"]["steps"]
    named = {step.get("name"): step for step in steps}
    order = ["Validate exact stage8 metadata", "Download exact stage8 snapshot volumes",
             "Validate x64 snapshot markers and receipts", "Restore verified checkpoint without cold fallback",
             "Migrate exact donor checkpoint to checked-out target source", "Run stage 8",
             "Upload fingerprint diagnostics", "Ensure build tree snapshot", "Upload tree part 1"]
    positions = [steps.index(named[name]) for name in order]
    assert positions == sorted(positions)
    scripts = "\n".join(step.get("run", "") for step in steps)
    assert scripts.count("tools/migrate_windows155_stage8_api_x64_snapshot.py") == 1
    assert "--work-dir 'C:\\c\\chromix'" in scripts
    assert '--donor-repo "$env:GITHUB_WORKSPACE\\donor"' in scripts
    assert '--target-repo "$env:GITHUB_WORKSPACE\\target"' in scripts
    for token in ("python -c", "fetch_upstream_cache.py", "-StageIndex 1 ", "-FromArtifact",
                  "continue-on-error", "windows-11-arm"):
        assert token not in WORKFLOW.read_text()
    stage = named["Run stage 8"]["run"]
    assert "-StageIndex 8 -MaxStages 8 -FromSynced -UseUpstreamCache -Arch x64" in stage
    assert "325 - $elapsed" in stage
    assert "-StageBudgetMinutes $budget" in stage
    assert "if ($budget -lt 60) { throw" in stage
    assert "src\\out\\Default\\build.ninja' -PathType Leaf" in stage
    for argument in ("MigrationReceipt", "MigrationDonorRepo", "FromSynced", "StageBudgetMinutes"):
        assert "-" + argument in stage
        assert re.search(r"\$" + argument + r"\b", STAGE.read_text().split('$ErrorActionPreference')[0])
    restore = named["Restore verified checkpoint without cold fallback"]["run"]
    assert "& $sevenZip t " in restore and "& $sevenZip x " in restore
    assert "if (Test-Path -LiteralPath 'C:\\c') { throw" in restore
    for step in steps:
        assert "continue-on-error" not in step
        if "python " in step.get("run", ""):
            assert "if ($LASTEXITCODE -ne 0) { throw" in step["run"]


def test_diagnostics_precede_checkpoint_and_only_verified_final_is_published():
    steps = flow()["jobs"]["build-8"]["steps"]
    named = {step.get("name"): step for step in steps}
    for name in ("Upload recovery proof diagnostics", "Upload fingerprint diagnostics", "Upload restored reuse evidence"):
        assert named[name]["if"] == "${{ always() }}"
        assert steps.index(named[name]) < steps.index(named["Upload tree part 1"])
    for step in steps:
        if step.get("uses") == "actions/upload-artifact@v4":
            assert step["with"]["retention-days"] == 7
    for index in range(1, 5):
        step = named[f"Upload tree part {index}"]
        assert step["with"]["name"] == f"win-x64-tree-s8-attempt-${{{{ github.run_attempt }}}}-part{index}"
        assert "always()" in step["if"] and "snapshot_safe == 'true'" in step["if"]
        assert "finished != 'true'" in step["if"]
        assert step["with"]["compression-level"] == 0
    for name in ("Upload final bundle", "Upload final source receipt"):
        assert named[name]["if"] == "${{ success() && steps.stage.outputs.finished == 'true' }}"
        assert named[name]["with"]["if-no-files-found"] == "error"
    assert named["Upload final source receipt"]["with"]["path"] == "${{ steps.stage.outputs.recovery_report_dir }}/source-verification.json"
    complete = flow()["jobs"]["complete"]
    assert complete["needs"] == "build-8"
    assert complete["if"] == "${{ !cancelled() }}"
    assert '"$RESULT" != "success"' in complete["steps"][0]["run"]
    assert '"$FINISHED" != "true"' in complete["steps"][0]["run"]
    assert "exit 1" in complete["steps"][0]["run"]


def test_stage_guard_keeps_receipt_verification_and_native_acceptance_mandatory():
    text = STAGE.read_text()
    start = text.index("if ($MigrationReceipt -or $MigrationDonorRepo)")
    guard = text[start:text.index("} elseif ($FromSynced)", start)]
    for token in ("-not $FromSynced", "$FromArtifact", "$StageIndex -ne 8", "$Arch -cne 'x64'",
                  "$BuildProfile -cne 'native'", "-not $RequireUpstreamCache", "$ValidateOnly",
                  "$env:GITHUB_JOB -cne 'build-8'", "CHROMIX_WINDOWS_CACHE_REQUIRED",
                  "--prepare-report", "--target-sha $env:GITHUB_SHA", "--migration-receipt $MigrationReceipt"):
        assert token in guard
    assert "snapshot_safe true" not in guard
    assert text.index("--prepare-report") < text.index('tools\\restore_upstream_cache.py") --phase verify')
    assert text.index('--prepare-report') < text.index('& "$PSScriptRoot\\prepare-ungoogled.ps1"')
    assert text.index('& "$PSScriptRoot\\prepare-ungoogled.ps1"') < text.index('if ($FromSynced) { Write-OutVar snapshot_safe true }')
    final = text[text.index('if ($rc -eq 0) {'):]
    assert final.index("Verify-FinalBundle") < final.index("Write-OutVar finished true")
    native = text[text.index("function Verify-FinalBundle"):text.index('Write-Host "==> Chromix CI stage')]
    assert "Invoke-FingerprintAcceptance -Browser $chrome" in native
    assert "--source-report" in text and "--expected-version" in text
    assert "$metagenBindings --configure" in text and "$metagenBindings --src" in text
    arm_guard = text[text.index('} elseif ($FromSynced)'):text.index('if (-not $FromArtifact -and -not $FromSynced')]
    assert '$arm64MigrationReceipt = Get-Content' in arm_guard
    assert '$migrationreceipt' not in arm_guard.lower()


def test_new_checkpoint_selector_is_explicit_and_legacy_default_remains():
    text = STAGE.read_text()
    assert '[ValidateSet("stage7", "stage8", "stage8-api")] [string]$MigrationCheckpoint = "stage7"' in text
    assert "tools\\validate_windows155_stage8_checkpoint.py" in text
    assert "tools\\validate_windows155_stage7_checkpoint.py" in text
    assert "-MigrationCheckpoint stage8-api" in WORKFLOW.read_text()


def test_stage8_api_route_and_unchanged_native_snapshot_steps():
    text = STAGE.read_text()
    assert "if ($MigrationCheckpoint -ceq 'stage8-api')" in text
    assert "'38387174079') { throw 'stage8-api donor run cannot be its own recovery'" in text
    assert "tools\\validate_windows155_stage8_api_checkpoint.py" in text
    legacy = yaml.safe_load((ROOT / ".github/workflows/build-win155-x64-stage8-recovery.yml").read_text())
    old_steps = {step.get("name"): step for step in legacy["jobs"]["build-8"]["steps"]}
    new_steps = {step.get("name"): step for step in flow()["jobs"]["build-8"]["steps"]}
    for name in ("Record final loaded-module payload identities", "Upload restored reuse evidence",
                 "Upload fingerprint diagnostics", "Ensure build tree snapshot", "Upload final bundle",
                 "Upload final source receipt", *(f"Upload tree part {n}" for n in range(1, 5))):
        assert new_steps[name] == old_steps[name]
    assert flow()["jobs"]["complete"] == legacy["jobs"]["complete"]
