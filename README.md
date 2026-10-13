# Chromix

English | [简体中文](readme_cn.md)

[![PyPI](https://img.shields.io/pypi/v/chromix?logo=pypi&label=PyPI)](https://pypi.org/project/chromix/)
[![npm](https://img.shields.io/npm/v/%40xiaoxiaofeihh%2Fchromix?logo=npm&label=npm)](https://www.npmjs.com/package/@xiaoxiaofeihh/chromix)
[![License](https://img.shields.io/github/license/xiaozhou26/Chromix)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-GHCR-2496ED?logo=docker&logoColor=white)](docs/docker.md)
[![Release](https://img.shields.io/github/v/release/xiaozhou26/Chromix?display_name=tag)](https://github.com/xiaozhou26/Chromix/releases)

**A configurable Chromium browser for automation, compatibility testing, and reproducible browser-identity experiments.**

Chromix combines source-level Chromium patches with Python and Node.js SDKs. Use familiar Playwright objects, keep a stable profile across sessions, and configure language, timezone, display, and other browser-visible properties from one launch configuration. A separate Node entry point supports Puppeteer.

[Project website](https://xiaozhou26.github.io/Chromix/) · [Download browser](https://github.com/xiaozhou26/Chromix/releases) · [Feature guide (中文)](docs/features.md) · [Flag reference](docs/fingerprint-flags.md) · [Build guide](BUILDING.md) · [Report an issue](https://github.com/xiaozhou26/Chromix/issues)

## See Chromix

[![Chromix project website preview](site/assets/homepage-desktop.png)](https://xiaozhou26.github.io/Chromix/)

**Project website walkthrough:** language switching, Python/Node/Docker examples and the feature FAQ. These are recordings of the Chromix website, separate from browser-engine or detection-test results.

[![Chromix website interaction demo](site/assets/homepage-demo.gif)](https://xiaozhou26.github.io/Chromix/assets/homepage-demo.webm)

[Watch the WebM video](https://xiaozhou26.github.io/Chromix/assets/homepage-demo.webm) · [Mobile screenshot](site/assets/homepage-mobile.png) · [Open the website](https://xiaozhou26.github.io/Chromix/)

## At a glance

| Capability | What you can do | Details |
|---|---|---|
| Python + Node automation | Launch Chromix through Playwright; use Puppeteer from Node | [Python](sdk/python/README.md), [Node](sdk/node/README.md) |
| Persistent profiles | Retain cookies/localStorage and reuse a fingerprint seed | [Profile example](#persistent-profiles) |
| Browser identity | Configure UA, platform, locale, timezone, hardware and display properties | [Public flags](docs/fingerprint-flags.md) |
| Proxy-aware configuration | Use HTTP/HTTPS/SOCKS proxies and optionally derive locale/timezone through GeoIP | [Proxy example](#proxy-and-geoip) |
| Rendering and media policies | Configure native GPU policy, fonts, audio and codec restrictions | [Backend policies](docs/backend-policy.md) |
| Input helpers | Add mouse, typing and scrolling timing with `humanize` | [SDK feature details](docs/functionality-followup.md) |
| Encrypted cookie transfer | Export/import cookies between explicitly selected active contexts | [Cookie migration](docs/functionality-followup.md#加密-cookie-迁移) |
| Portable distribution | Use a release ZIP or a Linux amd64/arm64 container | [Downloads](#downloads-and-platforms), [Docker](docs/docker.md) |

A **persona** is the browser identity configuration used for a launch. Its reported values, actual browser behavior, and underlying device capabilities have separate verification requirements. Chromix tracks those boundaries in [FINGERPRINT_STATUS.md](FINGERPRINT_STATUS.md); website-specific detection outcomes depend on more than browser configuration.

## Quick start

Choose a language below. The SDK resolves a locally configured executable first; otherwise it downloads and caches the browser selected by its **configured release channel**.

> The SDK's `latest` channel is a pinned mapping, separate from GitHub's Latest release. To use Linux `v155.0.8059.39` before the new SDK is published, download its package and set `CLOAKBROWSER_BINARY_PATH`. The [Docker image](docs/docker.md) remains at `154.0.8037.97` for both architectures. Updating an SDK does not replace an explicitly configured browser.

### Python

Requires Python 3.8+ according to the package metadata; your Playwright version must also support your Python version.

```bash
python -m pip install chromix playwright
```

On Linux, install Playwright's system dependencies if needed:

```bash
python -m playwright install-deps chromium
```

```python
from chromix import launch

browser = launch(headless=True)
try:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
finally:
    browser.close()
```

Use `launch_async`, `launch_context` or `launch_persistent_context` for other lifecycles. See the [Python API guide](sdk/python/README.md). To use this checkout's SDK, install `./sdk/python` in place of `chromix`.

### Node.js / Playwright

Requires Node.js 18+ according to the package metadata; choose a compatible automation-driver version. The npm package is **`@xiaoxiaofeihh/chromix`**. The unscoped `chromix` name belongs to an unrelated project.

```bash
npm install @xiaoxiaofeihh/chromix playwright-core
```

Save as `example.mjs`, then run `node example.mjs`:

```javascript
import { launch } from "@xiaoxiaofeihh/chromix";

const browser = await launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto("https://example.com");
  console.log(await page.title());
} finally {
  await browser.close();
}
```

### Node.js / Puppeteer

```bash
npm install @xiaoxiaofeihh/chromix puppeteer-core
```

Use the separate driver entry point:

```javascript
import { launch } from "@xiaoxiaofeihh/chromix/puppeteer";

const browser = await launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto("https://example.com");
  console.log(await page.title());
} finally {
  await browser.close();
}
```

Driver-specific options and context ownership are explained in the [Node API guide](sdk/node/README.md). Python provides Playwright integration.

### Docker / Linux

The published Docker release provides **`154.0.8037.97` for both amd64 and arm64** under `latest` and `154.0.8037.97`. Docker's `amd64` platform selects the release's `linux-x64` archive; `arm64` selects `linux-arm64`.

```bash
docker pull ghcr.io/xiaozhou26/chromix:latest
docker run --rm ghcr.io/xiaozhou26/chromix:latest --version
```

`latest` selects the matching architecture at `154.0.8037.97`; the existing `154.0.8037.57` tag remains unchanged for both architectures. The image is a non-root browser CLI; SDKs and a remote browser service are separate. See [Docker setup and commands](docs/docker.md) for checksum verification, sandbox requirements and headless use. The image build reuses release binaries rather than compiling Chromium again.

## Common configurations

### Persistent profiles

Use one user-data directory per identity. The SDK stores a seed with the profile so subsequent launches reuse it.

```python
from chromix import launch_persistent_context

context = launch_persistent_context(
    "./profiles/demo",
    headless=False,
    locale="en-US",
    timezone="America/New_York",
)
try:
    page = context.new_page()
    page.goto("https://example.com")
finally:
    context.close()
```

Node uses `launchPersistentContext({ userDataDir: "./profiles/demo", ... })`. Keep concurrent browser processes on separate profile directories. For reproducible nonpersistent tests, pass an explicit `args=["--fingerprint=42"]` (Python) or `args: ["--fingerprint=42"]` (Node).

### Proxy and GeoIP

```python
import os
from chromix import launch

browser = launch(
    proxy=os.environ["CHROMIX_PROXY"],
    geoip=True,
    locale="en-US",
    headless=True,
)
try:
    page = browser.new_page()
    page.goto("https://example.com")
finally:
    browser.close()
```

Set `CHROMIX_PROXY` to your proxy URL, for example `http://user:pass@proxy.example:8080`. Explicit locale/timezone settings take precedence over GeoIP results. GeoIP makes a metadata request through the effective proxy.

Authenticated SOCKS5 TCP requires a matching patched Chromix binary. WebRTC address presentation and actual traffic routing are separate: changing a candidate IP does not create a UDP tunnel. Read the [WebRTC/proxy contract](docs/fingerprint-flags.md#webrtc-ip-and-proxy-resolution) before relying on a particular network route.

### Input timing and extensions

Add `humanize=True` in Python or `humanize: true` in Node to enable the SDK's input helpers. Python accepts `extension_paths`; Node uses `extensionPaths`. Consult the SDK guides for the supported operations, presets and driver-specific restrictions. Input helpers do not establish website-specific detection guarantees.

## Downloads and platforms

Download ZIPs and their checksum files from the **same release**. **Linux x64 / ARM64 downloads use [v155.0.8059.39](https://github.com/xiaozhou26/Chromix/releases/tag/v155.0.8059.39)**. Windows x64 / ARM64 remain at [v154.0.8037.97](https://github.com/xiaozhou26/Chromix/releases/tag/v154.0.8037.97), and macOS x64 / ARM64 remain at [v154.0.8037.57](https://github.com/xiaozhou26/Chromix/releases/tag/v154.0.8037.57). Docker is unchanged at `154.0.8037.97` for both architectures.

| Target | Archive | Manual launcher inside the extracted directory |
|---|---|---|
| Windows x64 | [`chromix-win-x64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/chromix-win-x64.zip) | `chromix/chromix.cmd` |
| Windows ARM64 | [`chromix-win-arm64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/chromix-win-arm64.zip) | `chromix/chromix.cmd` |
| Linux x64 | [`chromix-linux-x64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v155.0.8059.39/chromix-linux-x64.zip) | `chromix/chromix` |
| Linux ARM64 | [`chromix-linux-arm64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v155.0.8059.39/chromix-linux-arm64.zip) | `chromix/chromix` |
| macOS Intel | [`chromix-mac-x64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.57/chromix-mac-x64.zip) | `chromix/chromix` |
| macOS Apple Silicon | [`chromix-mac-arm64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.57/chromix-mac-arm64.zip) | `chromix/chromix` |

Platforms build and publish independently. Windows ARM64 cross-compiles on `windows-2022` and uses `windows-11-arm` for native verification. macOS bundles have no Developer ID distribution signature or notarization. Linux needs compatible system libraries and a working Chromium sandbox.

**Checksum files may be platform-specific.** For `v155.0.8059.39`, Linux x64 uses [`SHA256SUMS-linux-x64`](https://github.com/xiaozhou26/Chromix/releases/download/v155.0.8059.39/SHA256SUMS-linux-x64), and Linux ARM64 uses [`SHA256SUMS-linux-arm64`](https://github.com/xiaozhou26/Chromix/releases/download/v155.0.8059.39/SHA256SUMS-linux-arm64); the release also provides a main [`SHA256SUMS`](https://github.com/xiaozhou26/Chromix/releases/download/v155.0.8059.39/SHA256SUMS). Windows x64 uses [`SHA256SUMS-win-x64`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/SHA256SUMS-win-x64); its ZIP is also listed in the same release's main [`SHA256SUMS`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/SHA256SUMS). Windows ARM64 uses [`SHA256SUMS-win-arm64`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/SHA256SUMS-win-arm64). Select the file containing your exact ZIP name; do not assume the generic manifest lists every platform.

Linux x64 example, after downloading both files:

```bash
set -eu
archive=chromix-linux-x64.zip
awk -v name="$archive" '$2 == name || $2 == "*" name { print; count++ } END { if (count != 1) exit 1 }' SHA256SUMS-linux-x64 > SHA256SUMS.selected
sha256sum -c SHA256SUMS.selected
unzip "$archive" -d chromix-linux-x64
./chromix-linux-x64/chromix/chromix --version
```

For Linux ARM64, change the archive/directory to `chromix-linux-arm64` and the manifest to `SHA256SUMS-linux-arm64`. On macOS use `shasum -a 256 -c` and preserve framework symlinks and executable permissions. On Windows use PowerShell `Get-FileHash -Algorithm SHA256` to compare against the matching manifest before `Expand-Archive`.

For Actions artifacts, first extract GitHub's outer ZIP, then verify the inner browser archive. Detailed candidate verification is in [BUILDING.md](BUILDING.md#verify-and-run-a-posix-candidate).

## Use a local browser binary

Set **`CLOAKBROWSER_BINARY_PATH`** in the same terminal as your SDK script. Keep the full extracted bundle next to the executable.

```powershell
# Windows: actual executable, rather than the .cmd launcher
$env:CLOAKBROWSER_BINARY_PATH = "D:\chromix-win-x64\chromix\chrome.exe"
```

```bash
# Linux
export CLOAKBROWSER_BINARY_PATH="/absolute/path/chromix/chrome"

# macOS (use this assignment instead on a Mac)
export CLOAKBROWSER_BINARY_PATH="/absolute/path/chromix/Chromium.app/Contents/MacOS/Chromium"
```

Then use the usual SDK launch functions. This environment variable works in both SDKs. Python `launch(executable_path=...)` conflicts with the wrapper's own argument. Node's ordinary Playwright launches also accept `launchOptions: { executablePath: "/absolute/path/to/chrome" }` to bypass download; the Puppeteer entry point additionally accepts top-level `executablePath`. Measured-device launches have separate restrictions; see the Node guide.

### Versions and download channels

**Python SDK `152.0.7977.82.post5` and Node SDK `0.1.5` are published.** Their `stable` / `latest` mappings are Linux x64 / ARM64 → `v155.0.8059.39` and Windows x64 / ARM64 → `v154.0.8037.97`. macOS keeps stable `v151.0.7922.173` and latest `v152.0.7977.75`. Earlier post4 / 0.1.4 packages retain Linux `v154.0.8037.97`; upgrade your SDK or select a verified local executable with `CLOAKBROWSER_BINARY_PATH`. Upgrade with `python -m pip install --upgrade chromix` or `npm install @xiaoxiaofeihh/chromix@latest`. SDK package versions remain separate from the browser version.

These are separate version sources:

| Version source | Version / target | Meaning |
|---|---|---|
| `main` Linux / Windows source pins | Chromium `154.0.8037.97` | Main source configuration remains unchanged |
| Linux 155 build branch | Chromium `155.0.8059.39` | Independent release build; does not upgrade the source pins on `main` |
| `main` macOS source pin | Chromium `154.0.8037.97` | Build cache from upstream run `37654894671`; existing published downloads remain unchanged until the new build is verified |
| Container `latest` / `154.0.8037.97` | amd64 / arm64 `154.0.8037.97` | Both architectures use the same release; the old `.57` tag is retained |
| SDK Linux x64 / ARM64 `stable` / `latest` | [`v155.0.8059.39`](https://github.com/xiaozhou26/Chromix/releases/tag/v155.0.8059.39) | Published in Python `152.0.7977.82.post5` / Node `0.1.5` |
| SDK Windows x64 `stable` / `latest` | [`v154.0.8037.97`](https://github.com/xiaozhou26/Chromix/releases/tag/v154.0.8037.97) | Published in Python `152.0.7977.82.post3` / Node `0.1.3` |
| SDK Windows ARM64 `stable` / `latest` | [`v154.0.8037.97`](https://github.com/xiaozhou26/Chromix/releases/tag/v154.0.8037.97) | Published in Python `152.0.7977.82.post4` / Node `0.1.4` |
| SDK macOS `stable` | [`v151.0.7922.173`](https://github.com/xiaozhou26/Chromix/releases/tag/v151.0.7922.173) | Existing mapping retained; check asset availability |
| SDK macOS `latest` | [`v152.0.7977.75`](https://github.com/xiaozhou26/Chromix/releases/tag/v152.0.7977.75) | Existing mapping retained; check asset availability |

Source pins are authoritative in [build/ungoogled-revisions.psd1](build/ungoogled-revisions.psd1); channel mappings are in the [Python](sdk/python/chromix/_binary.py) and [Node](sdk/node/_binary.js) downloaders. `CLOAKBROWSER_VERSION` accepts a configured major or an exact four-part release version; an explicit channel takes precedence. Exact versions fail if the requested platform asset is absent. A source feature requires a browser built with the corresponding patches; an SDK update alone cannot add it to an older executable. The Windows ARM64 `.97` package reuses the natively reverified 216-patch build; it does not include the new noise changes in the current source.

The Linux `v155.0.8059.39` release reuses the original 226-patch packages for both architectures. Native CI gates passed, but `fullAcceptance=false` and 164 acceptance gaps remain. There was no dedicated new pixel-noise validation; prior-patch noise coverage remains unknown. These results do not establish full acceptance or pixel-noise effectiveness.

## Feature scope and verification

- **Identity settings:** supported flags and defaults are listed in the [flag reference](docs/fingerprint-flags.md). Screen size and page viewport are separate settings.
- **GPU behavior:** the default uses the shared native policy. Identity templates and measured device records have different admission requirements; see [GPU backend](docs/gpu-backend.md) and [device pool](docs/device-pool.md).
- **Experimental controls:** runtime suppression and the remote Canvas bridge are opt-in. The bridge's unsafe mode changes renderer sandboxing; read the [patch documentation](patches/README.md) before enabling it.
- **Evidence:** [fingerprint acceptance](docs/fingerprint-acceptance.md) describes checks against the exact built executable. Unit tests, source patches, browser startup and full runtime acceptance establish different facts.

For a verified local executable, run a focused smoke check:

```bash
python3 tools/fingerprint_smoke.py \
  --browser /absolute/path/chromix/chrome \
  --platform linux --locale en-US \
  --output /tmp/chromix-fingerprint-smoke.json
```

This requires Python Playwright and uses local test pages. It does not download a browser.

## Troubleshooting

| Symptom | First checks |
|---|---|
| SDK downloads an older browser or returns 404 | Check configured channels and release Assets; use `CLOAKBROWSER_BINARY_PATH` for the exact build you downloaded |
| A flag has no visible effect | Confirm the executable version and relevant source feature; distinguish profile settings from native device capabilities |
| Linux fails to start | Check system libraries, sandbox support, user permissions and shared memory in containers |
| Headed mode fails on a server | Use headless mode or configure a display server |
| Proxy/GeoIP fails | Check credentials, DNS, protocol and metadata reachability through that proxy; explicit locale/timezone can avoid a GeoIP dependency |
| Windows crashes on reload | Follow [Windows crash diagnostics](docs/windows-crash-diagnostics.md) and record the exact executable/build |
| A GitHub build fails | Reuse the latest verified uploaded file-tree checkpoint; see [BUILDING.md](BUILDING.md) for exact run/stage/attempt/asset inputs |

When reporting an issue, include OS/architecture, browser version, SDK version, launch options with credentials removed, build/run link, and a minimal reproduction.

## Documentation map

| Goal | Read |
|---|---|
| Choose a feature and configure it | [Feature guide (中文)](docs/features.md) |
| Use Python / async contexts | [Python SDK](sdk/python/README.md) |
| Use Node / Playwright / Puppeteer | [Node SDK](sdk/node/README.md) |
| Run a Linux container | [Docker](docs/docker.md) |
| Look up native browser switches | [Fingerprint flags](docs/fingerprint-flags.md) |
| Understand rendering and media behavior | [Backend policy](docs/backend-policy.md), [Canvas](docs/canvas-chain.md), [GPU](docs/gpu-backend.md) |
| Evaluate device templates and measured records | [Device pool](docs/device-pool.md) |
| Inspect verification status | [Status](FINGERPRINT_STATUS.md), [coverage](docs/fingerprint-coverage-matrix.md), [acceptance](docs/fingerprint-acceptance.md) |
| Compare CloakBrowser-compatible APIs | [Functionality comparison](docs/cloakbrowser-functionality-comparison.md), [implementation history](docs/functionality-followup.md) |
| Build, resume, package or publish | [BUILDING.md](BUILDING.md) |

## Build and contribute

The repository pins Chromium, ungoogled core and platform overlays, then applies the Chromix stack from [patches/series](patches/series). See [BUILDING.md](BUILDING.md) for per-platform dependencies and staged GitHub Actions builds.

Windows needs Visual Studio C++ tools and Windows SDK **10.0.28000.0**, including Debugging Tools, plus Python, Git, PowerShell 7 and 7-Zip. Linux and macOS require their respective Chromium toolchains. Build caches restore source and intermediate outputs; Ninja recompiles outputs affected by changed inputs.

```powershell
pwsh build/windows/build.ps1 -WorkDir D:\chromix-build -Jobs 8
# Continue with the same work directory:
pwsh build/windows/build.ps1 -WorkDir D:\chromix-build -Resume -Jobs 8
```

Useful development checks, with the appropriate development dependencies installed:

```bash
python3 tools/check_patches.py
python3 -m unittest discover -s tools/tests -v
npm --prefix sdk/node test
git diff --check
```

```text
patches/       Chromium source patches and version-specific overrides
build/         Platform preparation, staged compilation and packaging
docker/        Release-based Linux container
site/          GitHub Pages project website
sdk/           Python and Node automation wrappers
tools/         Build validation, diagnostics and regression tests
docs/          Feature guides, design notes and acceptance boundaries
assets/fonts/  Font assets and provenance
```

## License

Chromix's original code, patch integration and SDKs use the [BSD 3-Clause License](LICENSE). Chromium, third-party components and fonts retain their own terms; see [font provenance](assets/fonts/SOURCE.md). Referenced projects retain their own branding and licensing.

[Issues](https://github.com/xiaozhou26/Chromix/issues) · [Releases](https://github.com/xiaozhou26/Chromix/releases) · [LINUX DO](https://linux.do)
