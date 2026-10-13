# @xiaoxiaofeihh/chromix

Drive the Chromix Chromium engine with familiar Playwright or Puppeteer objects.
The SDK provides launch helpers, persistent profiles, proxy/GeoIP configuration,
optional humanized interactions, and encrypted cookie migration.

Chromix follows CloakBrowser's common camelCase API names. Start a migration by
changing the import to `@xiaoxiaofeihh/chromix`, then review
[compatibility differences](#compatibility-with-cloakbrowser). Native fingerprint
features require a matching Chromix executable; the SDK alone does not add them
to stock Chromium or guarantee a site's detection outcome.

**On this page:** [Install](#install) · [Quick start](#quick-start) ·
[Profiles and seeds](#persistent-profiles-and-seeds) ·
[Proxy and GeoIP](#proxy-and-geoip) · [Local executable](#local-executable) ·
[Puppeteer](#puppeteer) · [Cookies](#cookies-and-session-state) ·
[API](#api) · [Advanced options](#advanced-options) ·
[Configuration](#configuration) · [CLI](#cli) ·
[Compatibility](#compatibility-with-cloakbrowser)

## Install

Choose one automation driver:

```bash
# Playwright (default entry point)
npm install @xiaoxiaofeihh/chromix playwright-core

# Or Puppeteer (separate entry point)
npm install @xiaoxiaofeihh/chromix puppeteer-core
```

The SDK declares Node.js >=18; your selected driver's release may require a newer
Node.js version. It loads an installed `playwright-core` / `playwright` or
`puppeteer-core` / `puppeteer` at launch time. You only need the driver you use.
Examples below use ES modules and top-level `await`: save them as `.mjs`, or set
`"type": "module"` in your application's `package.json`.

The unscoped npm name `chromix` belongs to an unrelated project. Always use the
full `@xiaoxiaofeihh/chromix` package name when installing or importing. From the
repository root, install the checkout with `npm install ./sdk/node playwright-core`.

The first launch downloads the selected Chromix release into `~/.cache/chromix`.
No separate Playwright/Puppeteer Chromium download is needed. SHA256 is checked
when a matching entry in the release's `SHA256SUMS` is available; otherwise the
SDK warns and continues without verification. This is not signed-release
verification. Use a trusted release host or a [local build](#local-executable).

### Binary platforms

The SDK resolves Linux x64/ARM64, Windows x64/ARM64, and macOS x64/ARM64.
These are download mappings, not confirmation that every platform has a published
or runtime-validated browser in the selected release.
On Windows, `process.platform === "win32"` with `process.arch === "arm64"`
selects `win-arm64` and `chromix-win-arm64.zip`; `process.arch === "x64"`
keeps selecting `win-x64` and `chromix-win-x64.zip`. Use native ARM64 Node.js
on Windows ARM64: an x64 Node.js process running under emulation still reports
`x64`. A missing ARM64 asset does not trigger an x64 fallback.

Both Windows ZIPs contain `chromix/chromix.cmd` and `chromix/chrome.exe`.
`ensureBinary()` returns `chrome.exe` for Playwright; the cache is isolated
by release tag and platform (`~/.cache/chromix/<tag>/win-arm64/`). Downloads
require the matching asset in the selected release or `CHROMIX_DOWNLOAD_HOST`;
SDK support alone does not publish an ARM64 browser. Windows ARM64 Widevine
CDM discovery is not supported; an x64 CDM is not reused for ARM64.

## Quick start

Save as `example.mjs` and run `node example.mjs`:

```javascript
import { launch } from '@xiaoxiaofeihh/chromix';

const browser = await launch(); // Headless by default; use { headless: false } for a window.
try {
  const page = await browser.newPage();
  await page.goto('https://example.com');
  console.log(await page.title());
} finally {
  await browser.close();
}
```

Use `launch()` to manage multiple contexts, or `launchContext()` for one context
whose `close()` also closes its owned browser. Headed launches need a graphical
session. Pages use the standard Playwright API; all launch helpers are async.

## Persistent profiles and seeds

Reuse a dedicated user-data directory to retain cookies, localStorage and other
browser-managed profile data across runs:

```javascript
import { launchPersistentContext } from '@xiaoxiaofeihh/chromix';

const context = await launchPersistentContext({
  userDataDir: './profiles/account-1',
  headless: false,
});
try {
  const page = await context.newPage();
  await page.goto('https://example.com');
} finally {
  await context.close();
}
```

Run the same script again to reopen the profile. Do not run two browser processes
against the same directory at once; atomic seed creation does not remove
Chromium's profile lock. Use a separate directory per independent session.

| Launch configuration | Seed behavior |
|---|---|
| `launch()` / `launchContext()` with defaults | New nonzero random 32-bit seed per launch |
| Persistent context with defaults | Creates `.chromix-fingerprint-seed` in the profile once, then reuses it |
| `args: ['--fingerprint=42']` | Explicit seed wins; does not create or rewrite the seed file |
| `stealthArgs: false` | No default seed injection or profile seed I/O |

The seed file is one decimal 32-bit value plus a newline, atomically created and
shared with Python and the Puppeteer adapter. For Playwright persistent contexts,
the effective argument list is `contextOptions.args`, then `launchOptions.args`,
then top-level `args`; an explicit seed in that list wins. Prefer top-level `args`
unless you need a lower-level override. If you choose an explicit seed, supply it
on every run; it is not saved into the seed file.

A seed controls fingerprint inputs, not cookies or login state, and does not
guarantee identical rendering across different browser builds, hardware or fonts.
Profile persistence is not a promise that copying the directory to another
machine preserves encrypted logins.

## Proxy and GeoIP

Set `CHROMIX_PROXY` to your own proxy URL, for example
`http://user:password@proxy.example:8080`, before running this Playwright example:

```javascript
import { launch } from '@xiaoxiaofeihh/chromix';

const proxy = process.env.CHROMIX_PROXY;
if (!proxy) throw new Error('Set CHROMIX_PROXY to your proxy URL');
const browser = await launch({ proxy, geoip: true });
try {
  const page = await browser.newPage();
  await page.goto('https://example.com');
  console.log(await page.title());
} finally {
  await browser.close();
}
```

`CHROMIX_PROXY` is only an example application variable; the SDK does not read it
automatically. `proxy` also accepts a Playwright-shaped object with `server`,
`username`, `password`, and optional `bypass`. URL-encode credentials in URLs,
or use the separate fields. Do not commit real proxy credentials to scripts.
Puppeteer has [additional proxy-auth restrictions](#puppeteer).

`geoip: true` queries **ip-api.com over HTTP** through the effective proxy to fill
in timezone, country-derived locale and WebRTC presentation IP. With no proxy it
uses a direct connection. Explicit `timezone: 'Europe/London'` / `locale: 'en-GB'`
(and explicit regional flags) win over detected values. Omit `geoip` and specify
those values yourself to avoid that lookup; `--fingerprint-webrtc-ip=auto` also
requests an IP lookup independently.

### Routing and limitations

GeoIP is metadata, not a routing mechanism. The lookup uses the effective
HTTP/HTTPS/SOCKS proxy, including `launchOptions.proxy` overrides, and does not
inherit environment proxies or `NO_PROXY` bypasses. Failed lookups do not
fall back to the host connection. Metadata transport supports SOCKS4/4a/5/5h
and SOCKS5 credentials; that transport alone does not extend Chromium's proxy
backend. With a browser built from patches `0154`–`0157`, the launch SDK also
supports native SOCKS5 TCP username/password authentication via the high-level
`proxy` option. Credentials are endpoint-bound in the launch environment, not
argv or `page.authenticate`; context-specific SOCKS credentials are rejected.
Unrelated launches scrub inherited auth, including Windows case aliases. UDP ASSOCIATE is not implemented;
end-to-end validation against a matching native build remains pending.
SOCKS5/4a metadata lookups use remote
destination DNS; SOCKS4 uses local IPv4 DNS. Lookup accepts one raw
`--proxy-server` route, not PAC/auto-detect, route lists, empty raw proxies,
raw proxy credentials or a proxy conflicting with `--no-proxy-server`.
Simultaneous raw/Playwright proxy endpoints must match; omitted default ports
and equivalent IPv6 spellings are normalized. Supply credentials in the high-level option.

With a proxy, the SDK defaults to the native
`--force-webrtc-ip-handling-policy=disable_non_proxied_udp` unless an explicit
native policy was supplied. This is a WebRTC policy, not a guarantee about
all DNS, HTTP, QUIC or operating-system traffic.

`--fingerprint-webrtc-ip=<IPv4|IPv6|auto>` is supported. Auto resolves before
launch through the effective proxy. `geoip: true` reuses its one lookup to inject
the exit IP unless an explicit IP wins; off mode skips IP injection. The browser
rewrites local candidate/SDP/stats presentation, not sockets or STUN success.
Remote addresses, zero placeholders and relay allocations remain native.
`webrtc-fake-srflx` and `webrtc-fake-srflx-allow-udp` (including `uxr` equivalents)
remain rejected. The HTTP metadata service is not independent proof of an exit
route. Bare-browser auto has a separate bounded HTTPS startup resolver; see
the [full resolution contract](../../docs/fingerprint-flags.md#webrtc-ip-and-proxy-resolution).

GeoIP lookup failures reject with `Error`. The timeout defaults to 10
seconds and accepts values greater than zero and at most 60. Creating a
later context with another proxy does not recompute browser-level locale
or timezone/IP.

## Local executable

Set `CLOAKBROWSER_BINARY_PATH` before launch to bypass the downloader. For example,
on Linux or macOS (replace the path with your actual native executable):

```bash
export CLOAKBROWSER_BINARY_PATH="/absolute/path/to/chromix/chrome"
node example.mjs
```

On macOS, a bundle's executable is typically
`Chromium.app/Contents/MacOS/Chromium`. On Windows PowerShell:

```powershell
$env:CLOAKBROWSER_BINARY_PATH = "C:\Chromix\chrome.exe"
node example.mjs
```

Use `chrome.exe`, not `chromix.cmd`, and keep the rest of the browser bundle
beside it. A local path is not downloaded or checksum-verified by the SDK; it
must match your OS/architecture and include the native patches you need.

For a **per-call Playwright override**, put the path in `launchOptions`:

```javascript
import { launch } from '@xiaoxiaofeihh/chromix';

const browser = await launch({
  launchOptions: { executablePath: '/absolute/path/to/chromix/chrome' },
});
try {
  const page = await browser.newPage();
  await page.goto('https://example.com');
} finally {
  await browser.close();
}
```

This override takes precedence over `CLOAKBROWSER_BINARY_PATH`. The root entry
point does **not** use a top-level `executablePath`; the Puppeteer adapter does.

## Puppeteer

Use the separate native adapter; no Playwright driver is required:

```javascript
import { launchContext } from '@xiaoxiaofeihh/chromix/puppeteer';

const context = await launchContext();
try {
  const page = await context.newPage();
  await page.goto('https://example.com');
  console.log(await page.title());
} finally {
  await context.close(); // Also closes this helper's owned browser.
}
```

| Export | Returns / purpose |
|---|---|
| `launch(options)` | Native Puppeteer `Browser` |
| `launchContext(options)` | New isolated `BrowserContext`; close also closes its browser |
| `launchPersistentContext({ userDataDir, ...options })` | Default persistent context; close closes its browser |
| `connect(options)` | Connect to a caller-owned browser; use `browser.disconnect()` to detach |
| `buildLaunchOptions(options)` | Assemble Puppeteer launch settings without launching |

Persistent profiles use the [same seed rules](#persistent-profiles-and-seeds).
For a local build, supply top-level `executablePath` **or**
`launchOptions.executablePath`, not both, or use `CLOAKBROWSER_BINARY_PATH`.

Important differences from the Playwright entry point:

- `defaultViewport` defaults to `null`. Use `viewport` or `defaultViewport`, not
  both. `launchOptions` must contain Puppeteer options, not Playwright options.
- Nonempty `contextOptions`, launch-level `userAgent` / `colorScheme`, and
  `devicePool` are rejected. Configure returned contexts/pages using native
  Puppeteer APIs where appropriate.
- Browser-wide HTTP/HTTPS proxy authentication is not implemented: credentialed
  HTTP proxies are rejected, not silently mapped to `page.authenticate()`.
  Native SOCKS5 TCP authentication requires the matching browser patches
  described in [proxy limitations](#routing-and-limitations).
- `connect()` cannot change launch identity, proxy, profile or regional settings.
  Failed connection preparation disconnects without closing the caller's browser.
- Humanized wheel calls retain Puppeteer's `{ deltaX, deltaY }` API. Humanization
  does not imply coverage of every Puppeteer operation or a detection guarantee.

## Humanized interactions (limited subset)

This optional timing layer is for ordinary UI automation. It does not claim full
CloakBrowser behavior coverage or detection avoidance. Configuration keys remain
camelCase in this Node SDK; the upstream snake_case config examples are not aliases.

```javascript
import { launch } from '@xiaoxiaofeihh/chromix';

const browser = await launch({
  humanize: true,
  humanPreset: 'careful',
  humanConfig: { mistype: 0, typingDelay: 60 },
});
try {
  const page = await browser.newPage();
  await page.setContent('<button id="save">Save</button>');
  await page.click('#save', { timeout: 3000, humanConfig: { aimDelay: 0, hold: 0 } });
} finally {
  await browser.close();
}
```

Each supported call merges `humanConfig` into a snapshot of the page's launch
configuration. It never changes a preset, caller object, sibling page or subsequent
call's configuration. Calls do not share temporary override state; input actions
on the same page should still be awaited sequentially. Unknown keys, invalid
values and unknown presets throw instead of being silently ignored.

| Supported configuration | Meaning |
|---|---|
| `typingDelay`, `typingSpread`, `aimDelay`, `hold`, `scrollPause` | Nonnegative timing values in milliseconds |
| `pauseChance`, `overshoot`, `mistype` | Probabilities from 0 to 1 |
| `wobble` | Nonnegative pointer variation in pixels |
| `minSteps`, `stepsDivisor` | Positive integer minimum steps; positive distance divisor |
| `seed` | Optional uint32 timing random seed; a per-call seed resets only that call's generator |

### Supported calls and option semantics

- **Playwright `page.click` / `page.dblclick`:** with only `timeout`, `strict`
  and/or `humanConfig`, first perform a native trial (visible, enabled, stable,
  in-viewport and receiving events), move toward the target, then dispatch a
  native action that repeats its checks and retries if the DOM changed. Temporary
  element handles are disposed. This is a selector-page subset, not a Locator patch.
- An explicit positive `timeout` is a shared budget across checks, pointer pacing,
  aim delay and final native dispatch. `timeout: 0` disables that budget. Without
  an explicit timeout, each native phase uses the driver's configured default;
  extra movement/aim time is not covered by that default's single-call deadline.
- **`force`, `trial`, `position`, `button`, `modifiers`, `delay`, `clickCount`,
  `noWaitAfter` and other extra page-click options:** skip additional pacing and
  pass the original options to the native method, removing only `humanConfig`.
  In particular, `trial` does not click, and `force` does not acquire extra waits.
- **Both drivers:** `mouse.move`, `mouse.click`, `mouse.dblclick`, `keyboard.type`
  and `keyboard.press` accept `humanConfig` in the options object. Explicit native
  options such as `steps`, `delay` or click count bypass pacing and pass through;
  mouse-click `button` alone can retain pacing. Native click implementations
  preserve actual multi-click event counts. Typing without native options uses
  the configured per-character timing and optional correction behavior.
- **Wheel:** Playwright uses `mouse.wheel(dx, dy, { humanConfig })`; Puppeteer uses
  `mouse.wheel({ deltaX, deltaY, humanConfig })`. Totals are preserved. Unsupported
  extra wheel keys throw.

`humanizePage(page, config)` and `humanizeBrowser(browser, config)` accept the
resolved configuration directly (for example `resolveHumanConfig('careful', {...})`)
and are idempotent: a second application does not stack wrappers or reconfigure
the object. The browser helper recognizes Playwright `newContext` versus Puppeteer
`createBrowserContext`, and wraps existing pages and pages returned by its browser
and context factories. Playwright context page events also cover popups and
persistent initial pages. Puppeteer popups/externally-created targets are not
covered automatically; apply `humanizePage` explicitly when needed.

**Not implemented:** per-call `humanConfig` for `page.fill`, `page.type`, `page.hover`,
Locator, Frame or ElementHandle methods; Puppeteer selector-level actionability;
upstream idle options, snake_case aliases or `page._original`. Those higher-level
APIs remain driver-owned; do not pass SDK-only options to them. Puppeteer methods
may use the low-level wrappers internally, but this is not complete high-level
coverage. Low-level coordinate actions have no element actionability checks.

### Tests

From `sdk/node`, run `npm test` for unit tests. The real-browser test is opt-in and
uses an isolated SDK copy, a preinstalled driver, and local `setContent` fixtures;
it neither downloads a binary nor contacts a detection service:

```bash
CHROMIX_TEST_CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
CHROMIX_TEST_PLAYWRIGHT=/tmp/chromix-node-local/node_modules/playwright-core \
CLOAKBROWSER_WIDEVINE=0 node --test test/humanize-local.test.mjs
```

`CHROMIX_TEST_PLAYWRIGHT` is an absolute path to a `playwright-core` package
folder. Replace both paths as appropriate. Real Puppeteer browser execution is
not part of this fixture; its adapter and wheel/options compatibility have unit
coverage.

## Cookies and session state

Choose the mechanism that matches your task:

- **Same profile across runs:** use [persistent contexts](#persistent-profiles-and-seeds).
- **Playwright session snapshot:** use `storageState` for cookies/localStorage.
  This is a plaintext credential-bearing file, not a full profile or a seed backup.
- **Encrypted cookies between live contexts/SDKs:** use the migration helpers below.
  These migrate cookies only, not localStorage, IndexedDB or the profile seed.

### Playwright storage state

This example creates `state.json` on the first run and loads it when present:

```javascript
import { existsSync } from 'node:fs';
import { launchContext } from '@xiaoxiaofeihh/chromix';

const context = await launchContext({
  contextOptions: existsSync('state.json') ? { storageState: 'state.json' } : {},
});
try {
  const page = await context.newPage();
  await page.goto('https://example.com');
  await context.storageState({ path: 'state.json' });
} finally {
  await context.close();
}
```

Protect this file and exclude it from version control. Saving state does not log
you in; complete your application's normal login before saving if needed.
`storageState` is a Playwright API, not a Puppeteer adapter option.

### Encrypted cookie migration

Set `COOKIE_PASSPHRASE` in your environment. No additional Node cryptography
package is needed. This example migrates a demo cookie into a fresh context:

```javascript
import { launchContext } from '@xiaoxiaofeihh/chromix';
import { exportCookies, importCookies } from '@xiaoxiaofeihh/chromix/cookies';

const passphrase = process.env.COOKIE_PASSPHRASE;
if (!passphrase) throw new Error('Set COOKIE_PASSPHRASE');
const options = { passphrase };
const source = await launchContext();
try {
  await source.addCookies([{
    name: 'demo', value: '1', url: 'https://example.com',
  }]);
  console.log(await exportCookies(source, 'cookies.enc', options));
} finally {
  await source.close();
}

const destination = await launchContext();
try {
  console.log(await importCookies(destination, 'cookies.enc', options));
} finally {
  await destination.close();
}
```

Use a new output filename if `cookies.enc` already exists: exports never replace
an existing file. In real workflows pass your authenticated source context and
a fresh destination **before navigating it**. The migration helpers accept live
Chromium Playwright or Puppeteer contexts. The same helpers, plus
`encryptCookies` / `decryptCookies`, are exported from the root and `/puppeteer`.

The authenticated AES-GCM/scrypt file format interoperates with Python;
passphrases must contain 12–1024 UTF-8 bytes. Keep the passphrase separate from
the encrypted file. Imports require a cookie-empty context, preserve CHIPS,
host-only/domain and security attributes, skip expired entries and verify
readback. Existing cookies are never cleared. On failure, discard the destination
context because it may contain a partial import; there is no rollback. This is
not an OSCrypt/profile-database portability switch. See the
[format and limitations](../../docs/functionality-followup.md).

## API

The root entry point uses Playwright:

| Export | Returns / purpose |
|---|---|
| `launch(options)` | Playwright `Browser` |
| `launchContext(options)` | `BrowserContext`; close also closes its owned browser |
| `launchPersistentContext({ userDataDir, ...options })` | Persistent `BrowserContext` |
| `buildLaunchOptions` / `buildContextOptions` | Assemble driver options |
| `buildArgs` / `getDefaultStealthArgs` | Fingerprint argument assembly |
| `maybeResolveGeoip` | Resolve timezone, locale and exit-IP metadata |
| `ensureBinary` / `binaryInfo` / `clearCache` / `checkForUpdate` | Binary management |
| `humanizeBrowser` / `humanizePage` / `resolveHumanConfig` | Behavioral-layer helpers |
| `exportCookies` / `importCookies` / `encryptCookies` / `decryptCookies` | Encrypted cookie migration |

### Option routing

Common options include `headless`, `proxy`, `args`, `stealthArgs`, `timezone`,
`locale`, `geoip`, `humanize`, `humanPreset`, `humanConfig`, `extensionPaths`,
`browserVersion`, `releaseChannel`, and `startMaximized`.

- Put Playwright launch settings (for example `executablePath`, `env`, `timeout`)
  in `launchOptions`. Its `args` replaces, rather than concatenates with, top-level
  `args`; the SDK then assembles and validates the final flags.
- Put Playwright context settings (for example `storageState`, `permissions`,
  `extraHTTPHeaders`) in `contextOptions`. `userAgent`, `viewport`, and
  `colorScheme` also have top-level conveniences.
- Use top-level `timezone` / `locale`: `contextOptions.timezoneId` / `locale`
  are ignored in favor of browser flags. Creating another context later does
  not change the browser's regional identity.
- `humanize: true` enables the wrapper's mouse/typing/scroll behavior;
  `humanPreset: 'careful'` selects slower defaults. This is not a guarantee of
  full driver-operation coverage or site acceptance.
- An arbitrary `userAgent` override may disagree with UA Client Hints. Prefer
  coherent native fingerprint settings.

## Advanced options

### Fingerprint and viewport defaults

Defaults claim the native OS persona: `linux`, `windows`, or `macos`.
Default page viewport geometry is native. Public fingerprint mode supplies
CPU/RAM 8/8, platform-specific screen/taskbar defaults and a 102400 MiB quota.
The older seeded synthetic viewport/hardware pools require explicit
`args: ['--uxr-synthetic-device-tests=true']` and remain separate test templates.

Explicit synthetic seeds accept nonzero decimal uint64 strings without rounding
through JavaScript `Number`; Python and Node derive identical geometry. Malformed
seeds, conflicting screen/taskbar aliases, invalid work areas and incomplete
viewport pairs fail. `--uxr-viewport-width`/`--uxr-viewport-height` override the
UI-strip template. A configured viewport sends screen and DPR together, including
DPR 1; `viewport: null` removes inherited screen/DPR defaults. The
[launch display backend](../../docs/persona-cross-process-design.md) needs a
browser rebuilt from the current patch stack.

### Public fingerprint flags

GPU vendor/renderer, CPU/RAM, screen/taskbar, brand/version/platform version,
timezone/locale, quota, Windows font metrics, WebRTC IP/auto, noise/off,
third-party cookies and `FakeShadowRoot` are available through `args`, along
with GPU mode, restricted fonts, graph audio isolation, millisecond clock
resolution, codec restrictions and effective CSS/input preferences. Voice tables
are synthetic fixtures. See the [complete flag contract](../../docs/fingerprint-flags.md)
for defaults and limitations. Updating this SDK does not add native features
to an old executable; use a browser rebuilt from the matching patch stack.

```javascript
import { launch } from '@xiaoxiaofeihh/chromix';

const browser = await launch({ args: [
  '--fingerprint=42',
  '--fingerprint-brand=Edge',
  '--fingerprint-brand-version=152.0.0.0',
  '--fingerprint-noise=false',
  '--fingerprint-allow-3p-cookies',
  '--enable-blink-features=FakeShadowRoot',
] });
try {
  const page = await browser.newPage();
  await page.goto('https://example.com');
} finally {
  await browser.close();
}
```

`--fingerprint=off` accepts `false/0/disable/disabled` and strips the injected
platform. Explicit timezone/locale and `geoip: true` still apply regional
settings; omit them for a native-persona comparison. `noise=false` keeps seeds
while disabling existing perturbations; it does not install four independent
Canvas/WebGL/audio/client-rect noise implementations.

The SDK accepts and validates new noise parameters such as
`--fingerprint-pixel-noise=seeded`, but SDK parameter support does not mean the
released browser binary implements them. The Windows ARM64 `v154.0.8037.97`
bundle reused for this release is the existing 216-patch build; it does not
include the new pixel-noise implementation. Upgrading the SDK or selecting this
channel does not enable it; a browser rebuilt with the matching new patches is required.

Ordinary launches default to `--fingerprint-gpu-backend=native`; explicit WebGL
name hints require `compatibility`. The SDK no longer adds `--ignore-gpu-blocklist`.
Use `--fingerprint-audio-render=isolated` with the fingerprint seed or an explicit
audio seed, and `--fingerprint-timer-resolution=7` for **7 milliseconds**.
See [backend policy](../../docs/backend-policy.md) for implementation limits.

### Custom font directory

`fontsDir: '/path/to/fonts'` parses `.ttf` / `.otf` / `.ttc` family names and,
on Linux, configures the actual Fontconfig directory. It does not install fonts
into Windows/macOS font backends or prove which file rendered each glyph.
Normal launches keep native font selection. Add
`args: ['--fingerprint-font-policy=restricted']` to enforce the parsed family
pool on resolved native fonts and fallback; an explicit whitelist wins.
Legacy substitutions and persona fallback still require synthetic-test opt-in.
Use fonts you are licensed to use. Measured mode rejects `fontsDir` overrides.

### Measured device launch

`launchContext({devicePool: {python: 'python', host: 'record.json',
records: ['record.json'], seed: '42'}})` validates complete device records and
the native host, then verifies five live contexts before returning. The persistent
variant uses `launchPersistentContext` with `userDataDir` and binds record/seed.
Install the matching Python SDK into the selected interpreter first:
`python -m pip install './sdk/python[measured]'` from this checkout. Set
`CLOAKBROWSER_BINARY_PATH` to the exact collected executable. Records default to
a 24-hour age limit. Other field/launch/context overrides are rejected;
browser-returning `launch` does not support measured mode. See
[device pool documentation](../../docs/device-pool.md) for the full contract.

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

`browserVersion` / `releaseChannel` are the per-call equivalents of the version
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

`binaryInfo()` and CLI `info` report the same selected release/cache metadata used
by installation, not the version of `CLOAKBROWSER_BINARY_PATH`. The `channel`
field is null for exact pins or historical majors no longer represented by a
platform's channels. A local executable override still bypasses downloading and
version/channel validation for installation and launch, even on unsupported hosts.

`checkForUpdate()` accepts the same version/channel selectors and compares the
selected tag, not a probed executable, with GitHub's latest release. It reports
an update only when that release has a newer four-part version and the current
platform's asset. Missing assets, older releases, and offline checks leave the
reported latest version at the selected version. It does not install an update
or change the built-in channel mapping.

### Upgrade from this checkout

This checkout uses SDK package version `0.1.5` with the mappings above.
An already installed registry package does not acquire these mappings automatically.
From the repository root,
install the updated Node SDK with `npm install ./sdk/node`, then run
`./node_modules/.bin/chromix info` and `./node_modules/.bin/chromix install`.
Changing browser-channel mappings does not itself publish a PyPI or npm package.
SDK package versioning and registry publication are separate release decisions.

## CLI

After installation, the package provides a local `chromix` executable. Explicitly
select the scoped package to avoid accidentally resolving the unrelated package:

```bash
npm exec --package=@xiaoxiaofeihh/chromix -- chromix --version
npm exec --package=@xiaoxiaofeihh/chromix -- chromix install
npm exec --package=@xiaoxiaofeihh/chromix -- chromix info
npm exec --package=@xiaoxiaofeihh/chromix -- chromix clear-cache
```

`install` pre-downloads the browser and prints its path (or the environment's
local override). `info` reports channel/cache metadata without launching a
browser. `clear-cache` deletes the entire configured binary cache, not separately
located profile directories. The Node CLI has no `widevine` command; the optional
Python SDK provides `python -m chromix widevine` for Linux x64.

## Versioning

The npm package follows SemVer independently of Chromium's four-part version.
Package versions and source build targets may move ahead of the binary channels.
Use `chromix info` for the installed SDK's selected release/cache metadata and
`chromix install` to resolve its executable path; neither verifies that every
platform asset is published or that a local override matches the channel version.

## Compatibility with CloakBrowser

1. Common launch names and Playwright return objects are retained; review option
   routing and limits rather than assuming complete upstream feature parity.
2. `licenseKey` is accepted and ignored (one open tier).
3. GeoIP uses an HTTP metadata service, not a local GeoLite2 database. Explicit
   timezone/locale win; failed lookups reject.
4. Puppeteer uses `@xiaoxiaofeihh/chromix/puppeteer` with the restrictions above.
5. Widevine setup is automatic when a supported CDM is found (installed Chrome or
   `CLOAKBROWSER_WIDEVINE_CDM`); discovery alone does not guarantee playback for
   a particular DRM service. Linux x64 can fetch a CDM with the Python CLI.

## License

The Node SDK is available under the BSD 3-Clause License. See [`LICENSE`](LICENSE).
