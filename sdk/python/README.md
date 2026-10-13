# chromix (Python)

Drive the Chromix Chromium engine with familiar Playwright `Browser` and
`BrowserContext` objects. The SDK provides synchronous and asynchronous launch
helpers, persistent profiles, proxy/GeoIP configuration, optional humanized
interactions, and encrypted cookie migration.

Chromix follows CloakBrowser's common API names. Start a migration by changing
`from cloakbrowser import launch` to `from chromix import launch`, then review
[compatibility differences](#compatibility-with-cloakbrowser). Native fingerprint
features require a matching Chromix executable; the SDK alone does not add them
to stock Chromium or guarantee a site's detection outcome.

**On this page:** [Install](#install) · [Quick start](#quick-start) ·
[Async](#async) · [Profiles and seeds](#persistent-profiles-and-seeds) ·
[Proxy and GeoIP](#proxy-and-geoip) · [Local executable](#local-executable) ·
[Cookies](#cookies-and-session-state) · [API](#api) ·
[Advanced options](#advanced-options) · [Configuration](#configuration) ·
[CLI](#cli) · [Compatibility](#compatibility-with-cloakbrowser)

## Install

```bash
python -m pip install chromix playwright
```

The distribution and import package are both named `chromix`. From the repository
root, install the checkout instead with:

```bash
python -m pip install './sdk/python[playwright]'
```

Package metadata declares Python >=3.8; use a Python version supported by the
installed Playwright and any optional dependencies. GeoIP needs no dedicated
extra, but timezone validation needs IANA timezone data. Python >=3.9 uses
`zoneinfo` (install `tzdata` if your system lacks the database); older Python
falls back to system zoneinfo files. Cookie migration additionally needs
`chromix[cookies]`.

The first launch downloads the selected Chromix release into `~/.cache/chromix`.
You do not need `playwright install chromium` for this browser. SHA256 is checked
when a matching entry in the release's `SHA256SUMS` is available; otherwise the
SDK warns and continues without verification. This is not signed-release
verification. Use a trusted release host or a [local build](#local-executable).

### Binary platforms

The SDK resolves Linux x64/ARM64, Windows x64/ARM64, and macOS x64/ARM64.
These are download mappings, not confirmation that every platform has a published
or runtime-validated browser in the selected release.
On Windows, `platform.machine()` values `ARM64`/`aarch64` (case-insensitive)
select `win-arm64` and `chromix-win-arm64.zip`; `AMD64`/`x86_64` keep selecting
`win-x64` and `chromix-win-x64.zip`. Use native ARM64 Python on Windows ARM64.
The SDK follows the reported architecture and does not fall back to an x64
bundle when the ARM64 asset is missing.

Both Windows ZIPs contain `chromix/chromix.cmd` and `chromix/chrome.exe`.
`ensure_binary()` returns `chrome.exe` for Playwright; the cache is isolated
by release tag and platform (`~/.cache/chromix/<tag>/win-arm64/`). Downloads
require the matching asset in the selected release or `CHROMIX_DOWNLOAD_HOST`;
SDK support alone does not publish an ARM64 browser. Windows ARM64 Widevine
CDM discovery is not supported; an x64 CDM is not reused for ARM64.

## Quick start

Save as `example.py` and run `python example.py`:

```python
from chromix import launch

browser = launch()  # Headless by default; use headless=False for a window.
try:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
finally:
    browser.close()
```

Use `launch()` when you want to manage multiple contexts, or `launch_context()`
for one context whose `close()` also closes its owned browser. Headed launches
need a graphical session. Pages use the standard Playwright API.

## Async

```python
import asyncio
from chromix import launch_async

async def main():
    browser = await launch_async()
    try:
        page = await browser.new_page()
        await page.goto("https://example.com")
        print(await page.title())
    finally:
        await browser.close()

asyncio.run(main())
```

`launch_context_async()` returns an async context. For persistent storage, use
`await launch_persistent_context_async(user_data_dir="./profiles/account-1")`:
**the async directory argument is keyword-only**, unlike the synchronous entry
point. Close either returned context with `await context.close()`. In a notebook
or another running event loop, await `main()` rather than calling `asyncio.run()`.

## Persistent profiles and seeds

Reuse a dedicated user-data directory to retain cookies, localStorage and other
browser-managed profile data across runs:

```python
from chromix import launch_persistent_context

context = launch_persistent_context("./profiles/account-1", headless=False)
try:
    page = context.new_page()
    page.goto("https://example.com")
finally:
    context.close()
```

Run the same script again to reopen the profile. Do not run two browser processes
against the same directory at once; atomic seed creation does not remove
Chromium's profile lock. Use a separate directory per independent session.

| Launch configuration | Seed behavior |
|---|---|
| `launch()` / `launch_context()` with defaults | New nonzero random 32-bit seed per launch |
| Persistent context with defaults | Creates `.chromix-fingerprint-seed` in the profile once, then reuses it |
| `args=["--fingerprint=42"]` | Explicit seed wins; does not create or rewrite the seed file |
| `stealth_args=False` | No default seed injection or profile seed I/O |

The seed file is one decimal 32-bit value plus a newline, atomically created and
shared with the Node SDK. If you choose an explicit seed, supply it on every
subsequent run; it is not saved into that file. A seed controls fingerprint
inputs, not cookies or login state, and does not guarantee identical rendering
across different browser builds, hardware or fonts. Profile persistence is not a
promise that copying the directory to another machine preserves encrypted logins.

## Proxy and GeoIP

Set `CHROMIX_PROXY` to your own proxy URL, for example
`http://user:password@proxy.example:8080`, before running this example:

```python
import os
from chromix import launch

browser = launch(
    proxy=os.environ["CHROMIX_PROXY"],
    geoip=True,
)
try:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
finally:
    browser.close()
```

`CHROMIX_PROXY` is only an example application variable; the SDK does not read it
automatically. `proxy` also accepts a Playwright-shaped dict with `server`,
`username`, `password`, and optional `bypass`. URL-encode credentials in URLs,
or use the separate fields. Do not commit real proxy credentials to scripts.

`geoip=True` queries **ip-api.com over HTTP** through the effective proxy to fill
in timezone, country-derived locale and WebRTC presentation IP. With no proxy it
uses a direct connection. Explicit `timezone="Europe/London"` / `locale="en-GB"`
(and explicit regional flags) win over detected values. Omit `geoip` and specify
those values yourself to avoid that lookup; `--fingerprint-webrtc-ip=auto` also
requests an IP lookup independently.

### Routing and limitations

GeoIP is metadata, not a routing mechanism. The lookup uses the effective
HTTP/HTTPS/SOCKS proxy and does not inherit environment proxies or `NO_PROXY`
bypasses. Failed lookups do not fall back to the host connection. Metadata
transport supports SOCKS4/4a/5/5h, including SOCKS5 credentials; that transport
alone does not extend Chromium's proxy backend. A browser rebuilt with patches
`0154`–`0157` also supports native SOCKS5 TCP authentication through the SDK's
high-level `proxy` option. Endpoint-bound credentials travel in its launch
environment, not argv or origin HTTP auth. Unrelated launches scrub inherited
auth, including Windows case aliases; font environment merging cannot restore
it. UDP ASSOCIATE is not implemented; end-to-end validation against a matching
native build remains pending.
SOCKS5/4a metadata lookups resolve destination names at the proxy; SOCKS4 uses local
IPv4 DNS. Single raw `--proxy-server` routes are supported for lookup;
PAC/auto-detect, route lists, empty raw proxies, raw proxy credentials and
conflicting `--no-proxy-server` are rejected. If raw `--proxy-server` and a
high-level proxy are both supplied, their endpoints must match (default ports
and equivalent IPv6 spellings are normalized); use the high-level option for credentials.

With a proxy, the SDK defaults to the native
`--force-webrtc-ip-handling-policy=disable_non_proxied_udp` unless an explicit
native policy was supplied. This does not guarantee the routing of all DNS,
HTTP, QUIC or operating-system traffic.

`--fingerprint-webrtc-ip=<IPv4|IPv6|auto>` is supported. Auto resolves before
launch through the same effective proxy; `geoip=True` reuses its one lookup to
append the exit IP unless an explicit IP wins. Off mode skips IP injection.
The browser changes local candidate/SDP/stats presentation, not sockets or
STUN success; remote addresses, zero placeholders and relay allocations remain
native. `webrtc-fake-srflx` and `webrtc-fake-srflx-allow-udp` (including `uxr`
equivalents) remain rejected. The SDK's HTTP metadata service is unauthenticated
and is not proof of an exit route. Bare-browser auto uses its own bounded HTTPS
startup resolver; see the [full resolution contract](../../docs/fingerprint-flags.md#webrtc-ip-and-proxy-resolution).

GeoIP lookup failures raise `ValueError`. The timeout defaults to 10
seconds and accepts values greater than zero and at most 60. Python's
synchronous DNS/connection setup cannot always be interrupted at that
deadline; a late connection is rejected before sending the GeoIP request.
IANA timezone data must be installed for timezone validation. Creating a
later context with another proxy does not recompute browser-level locale
or timezone/IP.

## Local executable

Set `CLOAKBROWSER_BINARY_PATH` before launch to bypass the downloader. For example,
on Linux or macOS (replace the path with your actual native executable):

```bash
export CLOAKBROWSER_BINARY_PATH="/absolute/path/to/chromix/chrome"
python example.py
```

On macOS, a bundle's executable is typically
`Chromium.app/Contents/MacOS/Chromium`. On Windows PowerShell:

```powershell
$env:CLOAKBROWSER_BINARY_PATH = "C:\Chromix\chrome.exe"
python example.py
```

Use `chrome.exe`, not `chromix.cmd`, and keep the rest of the browser bundle
beside it. Python's wrapper sets `executable_path` internally: use this environment
variable, **not** `launch(executable_path=...)`. A local path is not downloaded or
checksum-verified by the SDK; it must match your OS/architecture and include the
native patches needed by the options you use.

## Cookies and session state

Choose the mechanism that matches your task:

- **Same profile across runs:** use [persistent contexts](#persistent-profiles-and-seeds).
- **Playwright session snapshot:** use `storage_state` for cookies/localStorage.
  This is a plaintext credential-bearing file, not a full profile or a seed backup.
- **Encrypted cookies between live contexts/SDKs:** use the migration helpers below.
  These migrate cookies only, not localStorage, IndexedDB or the profile seed.

### Playwright storage state

This example creates `state.json` on the first run and loads it when present:

```python
from pathlib import Path
from chromix import launch_context

state = Path("state.json")
context = launch_context(**({"storage_state": str(state)} if state.is_file() else {}))
try:
    page = context.new_page()
    page.goto("https://example.com")
    context.storage_state(path=str(state))
finally:
    context.close()
```

Protect this file and exclude it from version control. Saving state does not log
you in; complete your application's normal login before saving if needed.

### Encrypted cookie migration

Install the optional dependency and set `COOKIE_PASSPHRASE` in your environment:

```bash
python -m pip install 'chromix[cookies]'
```

This self-contained example migrates a demo cookie into a fresh context:

```python
import os
from chromix import launch_context, export_cookies, import_cookies

passphrase = os.environ["COOKIE_PASSPHRASE"]
source = launch_context()
try:
    source.add_cookies([{
        "name": "demo", "value": "1", "url": "https://example.com",
    }])
    print(export_cookies(source, "cookies.enc", passphrase=passphrase))
finally:
    source.close()

destination = launch_context()
try:
    print(import_cookies(destination, "cookies.enc", passphrase=passphrase))
finally:
    destination.close()
```

Use a new output filename if `cookies.enc` already exists: exports never replace
an existing file. In real workflows pass your authenticated source context and
a fresh destination **before navigating it**. Async contexts require
`await export_cookies_async(...)` / `await import_cookies_async(...)`.

The AES-GCM/scrypt format interoperates with Node. Passphrases must contain
12–1024 UTF-8 bytes; keep the passphrase separate from the encrypted file. Imports
require a cookie-empty context, skip expired entries, preserve host-only/domain,
CHIPS and security attributes, and compare browser readback. Existing cookies are
never cleared. CDP writes are not transactional: on failure, discard the destination
context because it may contain a partial import. This is live-context migration,
not OSCrypt or portable profile-database encryption. See the
[format and limitations](../../docs/functionality-followup.md).

## API

| Function | Description |
|---|---|
| `launch(**opts)` | Returns a Playwright `Browser` |
| `launch_async(**opts)` | Async variant |
| `launch_context(**opts)` | Returns a `BrowserContext` (native viewport by default) |
| `launch_context_async(**opts)` | Async variant |
| `launch_persistent_context(user_data_dir, **opts)` | Persistent profile |
| `launch_persistent_context_async(user_data_dir=..., **opts)` | Async variant |
| `build_args` / `get_default_stealth_args` | Arg assembly (32-bit random seed + native platform claim) |
| `maybe_resolve_geoip(geoip, proxy, timezone, locale, args=None)` | Egress IP → (timezone, locale, exit_ip) |
| `ensure_binary` / `clear_cache` / `binary_info` / `check_for_update` | Binary management |
| `HumanConfig` / `resolve_human_config` | Behavioral-layer config (`default` / `careful` presets) |
| `ProxySettings` | Playwright-shaped proxy TypedDict |
| `export_cookies` / `import_cookies` | Explicit encrypted migration between live Chromium contexts |
| `export_cookies_async` / `import_cookies_async` | Async cookie migration variants |
| `encrypt_cookies` / `decrypt_cookies` | Node-compatible authenticated cookie envelope |

### Option routing

Common launch options include `headless`, `proxy`, `args`, `stealth_args`,
`timezone`, `locale`, `geoip`, `humanize`, `human_preset`, `human_config`,
`extension_paths`, `browser_version`, and `release_channel`.

- `launch()` / `launch_async()` forward extra keywords to Playwright's Chromium
  launch call. Context settings belong on `browser.new_context()` / `new_page()`.
- Context helpers additionally accept `user_agent`, `viewport`, and `color_scheme`.
  For `launch_context()` / `launch_context_async()`, extra context keywords such
  as `storage_state` and `permissions` go to `browser.new_context()`; `env` is
  handled as a launch setting.
- Persistent helpers forward extra options to `chromium.launch_persistent_context()`.
- `humanize=True` enables the wrapper's mouse/typing/scroll behavior;
  `human_preset="careful"` selects slower defaults. Coverage depends on the helper
  and page creation path; it is not a guarantee that every Playwright operation
  is humanized or that a site will accept the session.
- `user_agent` emulation can disagree with UA Client Hints. Prefer coherent native
  fingerprint settings rather than an arbitrary UA string.

### Paced input: supported subset

`humanize=True` installs instance-level wrappers, with matching sync/async
behavior. Each wrapped call accepts `human_config={...}`; it merges into a fresh
copy of the page's launch preset and does not modify later calls or other pages.
For example, `page.type("#name", "Ada", human_config={"typing_delay": 100})`
uses a slower delay for that call only (use `await` on async pages).

| Wrapped API | Behavior |
|---|---|
| `page.mouse.move/click/dblclick` | Paced cursor movement; native click count/button events; explicit `steps` and click `delay` are honored |
| `page.mouse.wheel` | Paced wheel deltas, including fractional horizontal/vertical totals |
| `page.keyboard.type/press` | Character pacing or a short pre-key pause; native press options are forwarded |
| `page.click/hover` | Waits for visible, enabled, stable targets; native trial checks, optional cursor approach, then the original native action |
| `page.type` | Waits for visible, enabled, stable targets; original native typing with one sampled inter-key delay per call |
| `page.fill` | Waits for visible, enabled, stable, editable targets; original native fill, **not** character-by-character typing |

Selector calls share one timeout budget across waits, pacing and the final native
action. Omitted timeouts inherit Playwright's page/context default; `timeout=0`
disables the deadline. Explicit `delay` wins over configured timing. `force=True`
and `trial=True` delegate directly to the original native method without extra
pacing or waits, preserving Playwright's semantics (trial can still scroll or
apply modifiers). Custom click/hover `position` is passed through without an
extra center-target approach. Other native options are forwarded, and unsupported
keywords raise rather than disappearing. Detached targets during the additional
preparation may fail; the wrapper does not promise Locator-style retries.

The supported config fields are `typing_delay`, `typing_delay_spread`,
`typing_pause_chance`, `mistype_chance`, `mouse_wobble_max`,
`mouse_overshoot_chance`, `mouse_min_steps`, `mouse_steps_divisor`,
`click_aim_delay`, `click_hold`, `scroll_pause`, and `seed`.
Only relevant fields affect each operation: typo correction and random typing
pauses apply to `keyboard.type`, not selector `page.type/fill`.
Unknown fields (including upstream idle/advanced-behavior settings) now raise
`ValueError`; this is not the full CloakBrowser configuration schema.

**Not wrapped:** `Locator`, `Frame`, `FrameLocator`, `ElementHandle`, other page
methods (including `page.dblclick/press/check`), and other keyboard/mouse methods.
Patching `page.mouse` alone does not affect `locator.click()`; those operations
remain native and do not accept this extra `human_config` argument. There is no
`page._original` compatibility alias. Coordinate input has no element
actionability checks or timeout option. Serialize input calls on a page; concurrent
input sequences can interleave even though their configurations are isolated.

Launch helpers cover explicitly created `browser.new_page()` and
`browser.new_context().new_page()` pages, context-helper `new_page()` calls, and
persistent initial/new pages. Popups and pages created by the browser itself are
not automatically patched; `chromix.humanize.patch_page(page, cfg)` can patch
such a page explicitly and detects sync versus async input methods. Repatching
an already patched page is a no-op. These are SDK input conveniences, not a
claim about detection outcomes or advanced behavioral simulation.

## Advanced options

### Fingerprint and viewport defaults

Defaults claim the native OS persona: `linux`, `windows`, or `macos`, with native
context viewport geometry. The browser's public fingerprint mode supplies
CPU/RAM 8/8, platform-specific screen/taskbar defaults and a 102400 MiB quota. The older seeded synthetic
viewport/hardware pools require `args=["--uxr-synthetic-device-tests=true"]`;
that separate test mode retains deterministic cross-SDK templates. Explicit
viewport options still win outside measured mode.

Explicit synthetic seeds accept nonzero decimal uint64 values, including values
above `2**32`; Python and Node derive identical geometry. Malformed seeds, conflicting
screen/taskbar aliases, invalid work areas and incomplete viewport pairs fail instead
of silently choosing another template. `--uxr-viewport-width`/`--uxr-viewport-height`
override the UI-strip template. Screen dimensions and DPR (including 1) are sent
together with a configured viewport; `viewport=None` keeps native context geometry.
The [launch display backend](../../docs/persona-cross-process-design.md) requires
a browser rebuilt from the current patch stack.

### Public fingerprint flags

All listed public flags are passed through `args`: GPU vendor/renderer,
hardware concurrency, device memory, screen/taskbar, brand/version/platform
version, timezone/locale, storage quota, Windows font metrics, WebRTC IP/auto,
noise/off, third-party cookies and `FakeShadowRoot`. Backend policy flags add
GPU mode, restricted fonts, graph audio isolation, clock resolution, codec
restrictions and effective CSS/input preferences. Voice tables are synthetic fixtures.
See the [complete flag contract](../../docs/fingerprint-flags.md) for defaults
and native-versus-SDK boundaries. These source changes require a rebuilt browser;
updating this Python package alone does not upgrade an older executable.

```python
from chromix import launch

browser = launch(args=[
    "--fingerprint=42",
    "--fingerprint-brand=Edge",
    "--fingerprint-brand-version=152.0.0.0",
    "--fingerprint-noise=false",
    "--fingerprint-allow-3p-cookies",
    "--enable-blink-features=FakeShadowRoot",
])
try:
    page = browser.new_page()
    page.goto("https://example.com")
finally:
    browser.close()
```

`--fingerprint=off` also accepts `false/0/disable/disabled` and strips the
injected platform. Explicit timezone/locale and `geoip=True` still apply their
regional settings; omit them for a native-persona comparison. `noise=false`
keeps identity seeds and disables existing perturbation paths, not four new
Canvas/WebGL/audio/client-rect noise implementations.

The SDK accepts and validates new noise parameters such as
`--fingerprint-pixel-noise=seeded`, but SDK parameter support does not mean the
released browser binary implements them. The Windows ARM64 `v154.0.8037.97`
bundle reused for this release is the existing 216-patch build; it does not
include the new pixel-noise implementation. Upgrading the SDK or selecting this
channel does not enable it; a browser rebuilt with the matching new patches is required.

Ordinary launches now default to `--fingerprint-gpu-backend=native`; explicit
WebGL name hints require `compatibility`. The SDK no longer injects
`--ignore-gpu-blocklist`. Optional `--fingerprint-audio-render=isolated` uses
the fingerprint seed (or an explicit audio seed), and
`--fingerprint-timer-resolution=7` means **7 milliseconds**. See
[backend policy](../../docs/backend-policy.md) for limits and native acceptance status.

### Measured device launch

Install `chromix[measured]` for Playwright and the image-validation dependency.

`launch_context(device_pool={"host": "record.json", "records": ["record.json"],
"seed": "42"})` validates whole device records and native host capabilities,
then checks five live contexts before returning. Async and persistent context
variants support the same option; the async persistent directory is keyword-only.
Point `CLOAKBROWSER_BINARY_PATH` at the collected executable. Records default to
a 24-hour maximum age, and extra launch/context overrides are rejected. Persistent
profiles bind record and seed rather than rotating identities. Browser-returning
`launch` does not support this option. See [device pool documentation](../../docs/device-pool.md)
for collection, configuration, native fallback and remaining limitations.

### Custom font directory

`fonts_dir="path/to/fonts"` parses `.ttf` / `.otf` / `.ttc` family names and,
on Linux, configures the actual Fontconfig directory. It does not install fonts
into the Windows/macOS font backend or prove the file used for each glyph.
Normal launches keep native font selection. Add
`--fingerprint-font-policy=restricted` to enforce the parsed family pool on
resolved native fonts and fallback; an explicit whitelist overrides generated names.
Legacy substitutions and persona fallback still require synthetic-test opt-in.
Measured device mode rejects `fonts_dir` and other per-field overrides.

Pass the directory with `fonts_dir="/path/to/fonts"` and enable the policy with
`args=["--fingerprint-font-policy=restricted"]`. Use fonts you are licensed to use.

### High-risk engine options

`--fingerprint-devtools-runtime-suppression` and
`--fingerprint-canvas-bridge=127.0.0.1:9228` (with
`--fingerprint-canvas-bridge-unsafe`) require explicit `args`.

Runtime suppression can break console/binding-based automation. Canvas Bridge
removes the sandbox from bridge renderer processes and forwards canvas/WebGL
operations to the configured endpoint. Do not enable these for ordinary launches.

## Configuration

Set environment variables before importing the SDK, especially cache settings.
Only the listed compatibility variables are handled; not all CloakBrowser
environment variables have a Chromix equivalent.

| Variable | Purpose |
|---|---|
| `CLOAKBROWSER_BINARY_PATH` | Local executable; bypasses download |
| `CLOAKBROWSER_RELEASE_CHANNEL` | Selects a built-in channel: `stable` (default) or `latest` |
| `CLOAKBROWSER_VERSION` | Known Chromium major or exact four-part version (optional `v` prefix) |
| `CLOAKBROWSER_GEOIP_TIMEOUT_SECONDS` | GeoIP timeout, default `10`; must be >0 and <=60 |
| `CLOAKBROWSER_WIDEVINE_CDM` | Explicit Widevine CDM directory |
| `CLOAKBROWSER_WIDEVINE=0` | Disables automatic CDM setup |
| `CHROMIX_CACHE_DIR` | Cache root; default `~/.cache/chromix` |
| `CHROMIX_DOWNLOAD_HOST` | Release asset directory URL, including its `SHA256SUMS` |

`browser_version` / `release_channel` are the per-call equivalents of the version
and channel variables. Each per-call value overrides its corresponding environment
variable. A selected channel takes precedence over a version, including a channel
set in the environment; unset `CLOAKBROWSER_RELEASE_CHANNEL` to select by version.
Unknown channels, unsupported majors, and malformed versions raise an error
instead of silently selecting a different browser.

Channel tags are built into the installed SDK and resolved independently by platform:

| Platform | `stable` (default) | `latest` |
|---|---|---|
| Linux x64 | `v155.0.8059.39` | `v155.0.8059.39` |
| Linux arm64 | `v155.0.8059.39` | `v155.0.8059.39` |
| Windows x64 | `v154.0.8037.97` | `v154.0.8037.97` |
| Windows arm64 | `v154.0.8037.97` | `v154.0.8037.97` |
| macOS x64 / arm64 | `v151.0.7922.173` | `v152.0.7977.75` |

Linux x64/arm64 select Chromium 155 assets in this rollout. Windows x64/arm64
remain on Chromium 154.
macOS retains its prior channel mappings; `latest` does not mean GitHub's
globally newest release. Major `151` still selects `v151.0.7922.173`, and `152`
selects `v152.0.7977.75`, including on Linux x64/arm64 and Windows x64/arm64. Major `154` is
available only on Linux x64/arm64 and Windows x64/arm64, retaining
`v154.0.8037.97` even after Linux channel promotion. Major `155` (or `v155`)
selects `v155.0.8059.39` only on Linux x64/arm64; it is rejected on Windows
and macOS. A full version such as `155.0.8059.39` or `v155.0.8059.39`
pins that exact release tag on any platform: it never substitutes the channel's
patch version or establishes that the platform has an asset. Explicit pins
require a published asset for that platform (or a complete cached bundle);
missing assets fail without falling back to another version.
An exact 155 pin does not declare Windows or macOS 155 availability.

The SDK package version is not the browser version; the compatibility matrix
above determines the default browser. Linux 155 runtime acceptance remains
`full=false`: dedicated noise validation was not covered. Neither the release
nor passing SDK tests constitute an all-features-passed runtime claim.

`binary_info()` and CLI `info` report the same selected release/cache metadata used
by installation, not the version of `CLOAKBROWSER_BINARY_PATH`. The `channel`
field is null for exact pins or historical majors no longer represented by a
platform's channels. A local executable override still bypasses downloading and
version/channel validation for installation and launch, even on unsupported hosts.

`check_for_update()` accepts the same version/channel selectors and compares the
selected tag, not a probed executable, with GitHub's latest release. It reports
an update only when that release has a newer four-part version and the current
platform's asset. Missing assets, older releases, and offline checks leave the
reported latest version at the selected version. It does not install an update
or change the built-in channel mapping.

### Upgrade from this checkout

This checkout uses SDK package version `152.0.7977.82.post5` with the mappings above.
An already installed registry package does not acquire these mappings automatically.
From the repository root,
install the updated Python SDK with `python -m pip install --upgrade ./sdk/python`,
then run `python -m chromix info` and `python -m chromix install`.
Changing browser-channel mappings does not itself publish a PyPI or npm package.
SDK package versioning and registry publication are separate release decisions.

## CLI

```bash
python -m chromix install      # pre-download the binary
python -m chromix info         # binary / cache info
python -m chromix widevine     # fetch the Widevine CDM (Linux x64)
python -m chromix clear-cache
```

`install` prints the resolved executable path (including a local override).
`info` does not launch a browser. `clear-cache` deletes the entire configured
binary cache; it does not manage your separately located profile directories.
The `widevine` downloader is Linux x64-only. CDM discovery alone does not
guarantee playback for a particular DRM service.

## Compatibility with CloakBrowser

1. Common launch names and Playwright return objects are retained; review option
   routing and limits rather than assuming complete upstream feature parity.
2. `license_key` is accepted and ignored (one open tier).
3. GeoIP uses an HTTP metadata service, not a local GeoLite2 database or a
   `chromix[geoip]` extra. Explicit timezone/locale win; failed lookups raise.
4. Python uses Playwright; the [Node SDK](../node/README.md#puppeteer) also provides
   a native Puppeteer adapter.
5. Widevine setup is automatic when a supported CDM is found (installed Chrome,
   `CLOAKBROWSER_WIDEVINE_CDM`, or the Python `widevine` command), subject to the
   platform and service limitations above.

## License

The Python SDK is available under the BSD 3-Clause License. See
[`LICENSE`](LICENSE).
