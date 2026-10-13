# Chromix

[English](README.md) | 简体中文

[![PyPI](https://img.shields.io/pypi/v/chromix?logo=pypi&label=PyPI)](https://pypi.org/project/chromix/)
[![npm](https://img.shields.io/npm/v/%40xiaoxiaofeihh%2Fchromix?logo=npm&label=npm)](https://www.npmjs.com/package/@xiaoxiaofeihh/chromix)
[![License](https://img.shields.io/github/license/xiaozhou26/Chromix)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-GHCR-2496ED?logo=docker&logoColor=white)](docs/docker.md)
[![Release](https://img.shields.io/github/v/release/xiaozhou26/Chromix?display_name=tag)](https://github.com/xiaozhou26/Chromix/releases)

**可配置的 Chromium 浏览器，面向自动化、兼容性测试和可复现的浏览器身份实验。**

Chromix 在固定版本的 Chromium、ungoogled 核心及平台层之上维护源码补丁，并提供 Python 和 Node.js SDK。你可以继续使用熟悉的 Playwright 页面操作，通过启动配置控制语言、时区、显示等浏览器属性，使用持久化目录保留会话。Node.js 另有独立的 Puppeteer 入口。

[项目主页](https://xiaozhou26.github.io/Chromix/) · [下载浏览器](https://github.com/xiaozhou26/Chromix/releases) · [功能指南](docs/features.md) · [参数表](docs/fingerprint-flags.md) · [构建文档](BUILDING.md) · [问题反馈](https://github.com/xiaozhou26/Chromix/issues)

## 项目预览

[![Chromix 项目主页截图](site/assets/homepage-desktop.png)](https://xiaozhou26.github.io/Chromix/)

**主页操作演示：**切换语言、查看 Python/Node/Docker 示例和展开功能问答。以下内容为 Chromix 主页实录，展示网站使用方式；浏览器引擎的运行结果以对应验收记录为准。

[![Chromix 主页交互演示](site/assets/homepage-demo.gif)](https://xiaozhou26.github.io/Chromix/assets/homepage-demo.webm)

[观看 WebM 视频](https://xiaozhou26.github.io/Chromix/assets/homepage-demo.webm) · [移动端截图](site/assets/homepage-mobile.png) · [打开主页](https://xiaozhou26.github.io/Chromix/)

## 能做什么

| 功能 | 使用方式与用途 | 详细说明 |
|---|---|---|
| Python / Node 自动化 | 用 Playwright 启动和操作 Chromix；Node 也支持 Puppeteer | [Python SDK](sdk/python/README.md)、[Node SDK](sdk/node/README.md) |
| 持久化身份 | 保留 Cookie、localStorage，同一用户目录复用指纹种子 | [持久化目录示例](#持久化目录) |
| 浏览器身份配置 | 配置 UA、平台、语言、时区、硬件与屏幕属性 | [原生参数表](docs/fingerprint-flags.md) |
| 代理感知启动 | 设置 HTTP/HTTPS/SOCKS 代理，可选 GeoIP 推导语言和时区 | [代理示例](#代理与-geoip) |
| 图形与媒体策略 | 配置原生 GPU 策略、字体、音频及编解码限制 | [后端策略](docs/backend-policy.md) |
| 输入动作辅助 | `humanize` 为部分鼠标、输入和滚动操作加入轨迹与时序 | [功能指南](docs/features.md) |
| 加密 Cookie 迁移 | 在明确选定的活动 context 之间导出、导入 Cookie | [迁移说明](docs/functionality-followup.md#加密-cookie-迁移) |
| 跨平台分发 | 使用 ZIP 发布包，或 Linux amd64/arm64 Docker 镜像 | [下载](#下载与平台)、[Docker](docs/docker.md) |

**persona（浏览器身份配置）** 指一次启动使用的身份参数。接口显示值、实际浏览器行为、底层设备能力分别需要验证；设置一个平台名称不会把主机变成另一种物理设备。项目将各项实现与验证边界记录在 [FINGERPRINT_STATUS.md](FINGERPRINT_STATUS.md)，网站判断还会受网络、行为和其他因素影响。

## 快速开始

选择下面任一种 SDK。SDK 优先使用显式配置的本地可执行文件，否则按**代码内配置的发布通道**下载浏览器并缓存。

> SDK 的 `latest` 是固定版本映射，与 GitHub 的 Latest Release 分开维护。新 SDK 发布前要使用 Linux `v155.0.8059.39`，请下载对应发布包并设置 `CLOAKBROWSER_BINARY_PATH`。[Docker 镜像](docs/docker.md) 双架构仍为 `154.0.8037.97`。更新 SDK 不会替换显式指定的浏览器。

### Python

包元数据要求 Python 3.8+；所安装的 Playwright 版本也需要支持你的 Python 版本。

```bash
python -m pip install chromix playwright
```

Linux 如缺少运行依赖，可以安装 Playwright 的系统依赖：

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

其他入口包括 `launch_async`、`launch_context` 和 `launch_persistent_context`。参见 [Python API 指南](sdk/python/README.md)。需要使用当前仓库 SDK 时，将安装命令里的 `chromix` 替换为 `./sdk/python`。

### Node.js / Playwright

包元数据要求 Node.js 18+，还需选择兼容的自动化驱动版本。npm 包名为 **`@xiaoxiaofeihh/chromix`**；不带 scope 的 `chromix` 属于其他项目。

```bash
npm install @xiaoxiaofeihh/chromix playwright-core
```

保存为 `example.mjs`，执行 `node example.mjs`：

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

使用独立的驱动入口：

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

Puppeteer 的启动参数和 context 生命周期与 Playwright 有区别，详见 [Node API 指南](sdk/node/README.md)。Python SDK 提供 Playwright 集成。

### Docker / Linux

已发布的 Docker 镜像中，`latest` 与 `154.0.8037.97` 标签为 **amd64 和 arm64 均提供 `154.0.8037.97`**。Docker 平台 `amd64` 对应发布资产 `linux-x64`，`arm64` 对应 `linux-arm64`。制作镜像时复用现有二进制，无需再次编译 Chromium。

```bash
docker pull ghcr.io/xiaozhou26/chromix:latest
docker run --rm ghcr.io/xiaozhou26/chromix:latest --version
```

`latest` 按架构选择对应的 `154.0.8037.97` 镜像，原有 `154.0.8037.57` 双架构标签保持不变。镜像提供非 root 浏览器 CLI，SDK 和远程浏览器服务需要另行配置。摘要校验、sandbox 环境要求和 headless 用法见 [Docker 指南](docs/docker.md)。

## 常用配置

### 持久化目录

每个身份使用独立用户目录。SDK 会保存与目录绑定的种子，后续启动继续复用。

```python
from chromix import launch_persistent_context

context = launch_persistent_context(
    "./profiles/demo",
    headless=False,
    locale="zh-CN",
    timezone="Asia/Shanghai",
)
try:
    page = context.new_page()
    page.goto("https://example.com")
finally:
    context.close()
```

Node 对应 `launchPersistentContext({ userDataDir: "./profiles/demo", ... })`。并发浏览器进程应使用不同目录。非持久化测试需要固定种子时，Python 传 `args=["--fingerprint=42"]`，Node 传 `args: ["--fingerprint=42"]`。

### 代理与 GeoIP

```python
import os
from chromix import launch

browser = launch(
    proxy=os.environ["CHROMIX_PROXY"],
    geoip=True,
    locale="zh-CN",
    headless=True,
)
try:
    page = browser.new_page()
    page.goto("https://example.com")
finally:
    browser.close()
```

将 `CHROMIX_PROXY` 设为实际代理 URL，例如 `http://user:pass@proxy.example:8080`。显式语言、时区优先于 GeoIP 推导值；GeoIP 会通过当前代理发起元数据查询。

带认证的 SOCKS5 TCP 需要匹配补丁的 Chromix 二进制。WebRTC 地址展示与流量路由分别处理：修改候选 IP 不会建立 UDP 隧道。具体限制见 [WebRTC 与代理解析约定](docs/fingerprint-flags.md#webrtc-ip-and-proxy-resolution)。

### 输入动作与扩展

Python 使用 `humanize=True`，Node 使用 `humanize: true` 启用 SDK 动作辅助。扩展路径分别使用 `extension_paths`、`extensionPaths`。支持哪些操作、如何选择预设及各驱动限制，以 SDK 文档为准；动作辅助本身不代表网站检测结果。

## 下载与平台

从**同一个 Release** 下载浏览器 ZIP 和对应校验文件。**Linux x64 / ARM64 下载使用 [v155.0.8059.39](https://github.com/xiaozhou26/Chromix/releases/tag/v155.0.8059.39)**。Windows x64 / ARM64 保持 [v154.0.8037.97](https://github.com/xiaozhou26/Chromix/releases/tag/v154.0.8037.97)，macOS x64 / ARM64 保持 [v154.0.8037.57](https://github.com/xiaozhou26/Chromix/releases/tag/v154.0.8037.57)。Docker 双架构仍为 `154.0.8037.97`，不随本次 Linux ZIP 发布更新。

| 目标平台 | 归档 | 解压后的手动启动入口 |
|---|---|---|
| Windows x64 | [`chromix-win-x64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/chromix-win-x64.zip) | `chromix/chromix.cmd` |
| Windows ARM64 | [`chromix-win-arm64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/chromix-win-arm64.zip) | `chromix/chromix.cmd` |
| Linux x64 | [`chromix-linux-x64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v155.0.8059.39/chromix-linux-x64.zip) | `chromix/chromix` |
| Linux ARM64 | [`chromix-linux-arm64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v155.0.8059.39/chromix-linux-arm64.zip) | `chromix/chromix` |
| macOS Intel | [`chromix-mac-x64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.57/chromix-mac-x64.zip) | `chromix/chromix` |
| macOS Apple Silicon | [`chromix-mac-arm64.zip`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.57/chromix-mac-arm64.zip) | `chromix/chromix` |

各平台独立构建和发布。Windows ARM64 在 `windows-2022` 交叉编译，在 `windows-11-arm` 做原生验证。macOS 包没有 Developer ID 分发签名和公证；Linux 需要兼容的系统库及可工作的 Chromium sandbox。

**校验清单可能按平台拆分。** `v155.0.8059.39` 的 Linux x64 对应 [`SHA256SUMS-linux-x64`](https://github.com/xiaozhou26/Chromix/releases/download/v155.0.8059.39/SHA256SUMS-linux-x64)，Linux ARM64 对应 [`SHA256SUMS-linux-arm64`](https://github.com/xiaozhou26/Chromix/releases/download/v155.0.8059.39/SHA256SUMS-linux-arm64)，同一 Release 也提供主清单 [`SHA256SUMS`](https://github.com/xiaozhou26/Chromix/releases/download/v155.0.8059.39/SHA256SUMS)。Windows x64 对应 [`SHA256SUMS-win-x64`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/SHA256SUMS-win-x64)，同一 Release 的主清单 [`SHA256SUMS`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/SHA256SUMS) 也包含该 ZIP。Windows ARM64 对应 [`SHA256SUMS-win-arm64`](https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/SHA256SUMS-win-arm64)。选择包含你下载的 ZIP 名称的清单；通用清单未必覆盖所有平台。

Linux x64 下载两份文件后：

```bash
set -eu
archive=chromix-linux-x64.zip
awk -v name="$archive" '$2 == name || $2 == "*" name { print; count++ } END { if (count != 1) exit 1 }' SHA256SUMS-linux-x64 > SHA256SUMS.selected
sha256sum -c SHA256SUMS.selected
unzip "$archive" -d chromix-linux-x64
./chromix-linux-x64/chromix/chromix --version
```

Linux ARM64 替换归档、解压目录及校验清单名称；macOS 使用 `shasum -a 256 -c`，保留执行权限和 framework 符号链接。Windows 使用 `Get-FileHash -Algorithm SHA256` 对比清单，再执行 `Expand-Archive`。

如果下载的是 Actions artifact，先解开 GitHub 外层 ZIP，再校验内层浏览器归档。完整步骤见 [候选包验证](BUILDING.md#verify-and-run-a-posix-candidate)。

## 使用指定浏览器

在运行 SDK 的同一终端设置 **`CLOAKBROWSER_BINARY_PATH`**，并保留旁边完整的运行时文件。

```powershell
# Windows：指向实际可执行文件
$env:CLOAKBROWSER_BINARY_PATH = "D:\chromix-win-x64\chromix\chrome.exe"
```

```bash
# Linux
export CLOAKBROWSER_BINARY_PATH="/absolute/path/chromix/chrome"

# macOS：在 Mac 上改用这一行
export CLOAKBROWSER_BINARY_PATH="/absolute/path/chromix/Chromium.app/Contents/MacOS/Chromium"
```

随后运行普通启动示例即可，该环境变量适用于两种 SDK。Python `launch(executable_path=...)` 会与包装层参数冲突。Node 普通 Playwright 启动也支持 `launchOptions: { executablePath: "/absolute/path/to/chrome" }` 跳过下载，Puppeteer 入口还支持顶层 `executablePath`。实测设备模式有独立参数限制，详见 Node 指南。

### 分清三类版本

**Python SDK `152.0.7977.82.post5` 和 Node SDK `0.1.5` 已发布**，Linux x64 / ARM64 的 `stable` / `latest` 已切换到 `v155.0.8059.39`；Windows x64 / ARM64 保持 `v154.0.8037.97`，macOS 保持 stable `v151.0.7922.173` 与 latest `v152.0.7977.75`。旧的 post4 / 0.1.4 包仍选择 Linux `v154.0.8037.97`，需要升级 SDK，或通过 `CLOAKBROWSER_BINARY_PATH` 指定已核验的本地浏览器。可使用 `python -m pip install --upgrade chromix` 或 `npm install @xiaoxiaofeihh/chromix@latest` 升级。SDK 包版本与浏览器版本分别管理。

| 版本来源 | 版本 / 目标 | 含义 |
|---|---|---|
| `main` Linux / Windows 源码 | Chromium `154.0.8037.97` | 主源码配置保持不变 |
| Linux 155 构建分支 | Chromium `155.0.8059.39` | 独立发布构建，不代表 `main` 源码固定版本已升级 |
| `main` macOS 源码 | Chromium `154.0.8037.97` | 使用上游运行 `37654894671` 的编译文件树；新版构建验证前保留既有发布下载入口 |
| Docker `latest` / `154.0.8037.97` | amd64 / arm64 `154.0.8037.97` | 双架构使用同一发布版本，旧 `.57` 标签保留 |
| SDK Linux x64 / ARM64 `stable` / `latest` | [`v155.0.8059.39`](https://github.com/xiaozhou26/Chromix/releases/tag/v155.0.8059.39) | 已随 Python `152.0.7977.82.post5` / Node `0.1.5` 发布 |
| SDK Windows x64 `stable` / `latest` | [`v154.0.8037.97`](https://github.com/xiaozhou26/Chromix/releases/tag/v154.0.8037.97) | 已随 Python `152.0.7977.82.post3` / Node `0.1.3` 发布 |
| SDK Windows ARM64 `stable` / `latest` | [`v154.0.8037.97`](https://github.com/xiaozhou26/Chromix/releases/tag/v154.0.8037.97) | 已随 Python `152.0.7977.82.post4` / Node `0.1.4` 发布 |
| SDK macOS `stable` | [`v151.0.7922.173`](https://github.com/xiaozhou26/Chromix/releases/tag/v151.0.7922.173) | 保留既有映射，使用前核对资产可用性 |
| SDK macOS `latest` | [`v152.0.7977.75`](https://github.com/xiaozhou26/Chromix/releases/tag/v152.0.7977.75) | 保留既有映射，使用前核对资产可用性 |

源码配置以 [build/ungoogled-revisions.psd1](build/ungoogled-revisions.psd1) 为准，下载通道见 [Python](sdk/python/chromix/_binary.py) / [Node](sdk/node/_binary.js) 下载器。`CLOAKBROWSER_VERSION` 可选择已配置主版本或四段完整版本；显式通道优先。指定精确版本但该平台资产不存在时会报错，不静默退回其他版本。SDK 版本、浏览器发布版本和源码固定版本分别维护；新原生功能需要相应补丁编译出的浏览器。Windows ARM64 的 `.97` 包复用通过原生重验的 216 补丁构建，不包含当前源码中的新 noise 改动。

Linux `v155.0.8059.39` 双架构均复用各自的原始 226 补丁包，原生 CI gate 已通过，但 `fullAcceptance=false`，仍有 164 项验收缺口。未进行新的 pixel-noise 专项验证；既有补丁的 noise 覆盖仍属未知，不能据此宣称完整验收或 pixel-noise 效果已验证。

## 功能边界与验证

- **身份参数**：支持的开关及默认值见 [参数表](docs/fingerprint-flags.md)。页面 viewport 与 screen 分别配置。
- **GPU 策略**：普通启动使用共享原生策略。身份模板与经过测量的设备记录有不同准入条件，见 [GPU 后端](docs/gpu-backend.md)、[设备池](docs/device-pool.md)。
- **实验选项**：Runtime suppression 和远端 Canvas bridge 需要显式启用。bridge 的 unsafe 模式会改变渲染进程 sandbox，使用前请阅读 [补丁文档](patches/README.md)。
- **验证证据**：工具单测、补丁可应用、浏览器启动和完整运行验收分别说明不同层面的状态，详见 [验收说明](docs/fingerprint-acceptance.md)。

对于已经核验来源的本地二进制，可以运行：

```bash
python3 tools/fingerprint_smoke.py \
  --browser /absolute/path/chromix/chrome \
  --platform linux --locale zh-CN \
  --output /tmp/chromix-fingerprint-smoke.json
```

工具需要 Python Playwright，使用本地测试页面，不会自动下载浏览器。

## 常见问题

| 问题 | 优先检查 |
|---|---|
| SDK 下载旧版或返回 404 | 检查 SDK 通道及对应 Release Assets；指定版本使用 `CLOAKBROWSER_BINARY_PATH` |
| 参数设置没有效果 | 检查实际浏览器版本、对应补丁，并区分身份参数与底层设备能力 |
| Linux 启动失败 | 检查系统库、sandbox、用户权限，以及容器共享内存 |
| 服务器无法打开有界面窗口 | 使用 headless，或配置显示服务 |
| 代理或 GeoIP 失败 | 检查认证、DNS、协议及代理上的元数据访问；可显式设置语言和时区 |
| Windows 刷新后崩溃 | 按 [崩溃诊断](docs/windows-crash-diagnostics.md) 记录具体版本和故障信息 |
| Actions 编译失败 | 使用最近上传且验证通过的文件树检查点续编，参数见 [BUILDING.md](BUILDING.md) |

提交问题时，请提供操作系统和架构、浏览器及 SDK 版本、已移除凭据的启动参数、构建链接与最小复现步骤。

## 文档导航

| 你想做什么 | 文档 |
|---|---|
| 了解功能及选择配置 | [功能指南](docs/features.md) |
| 使用 Python、异步或持久化 context | [Python SDK](sdk/python/README.md) |
| 使用 Node、Playwright 或 Puppeteer | [Node SDK](sdk/node/README.md) |
| 使用 Linux Docker 镜像 | [Docker 指南](docs/docker.md) |
| 查找浏览器启动参数 | [指纹参数](docs/fingerprint-flags.md) |
| 理解图形、字体、媒体策略 | [后端策略](docs/backend-policy.md)、[Canvas](docs/canvas-chain.md)、[GPU](docs/gpu-backend.md) |
| 区分设备模板与实测数据 | [设备池](docs/device-pool.md) |
| 查看实现和验证状态 | [状态记录](FINGERPRINT_STATUS.md)、[覆盖矩阵](docs/fingerprint-coverage-matrix.md)、[验收](docs/fingerprint-acceptance.md) |
| 了解兼容性与开发历史 | [CloakBrowser 对照](docs/cloakbrowser-functionality-comparison.md)、[历史实现批次](docs/functionality-followup.md) |
| 编译、续编、打包与发布 | [构建文档](BUILDING.md) |

## 构建与贡献

源码固定 Chromium、ungoogled 核心和平台覆盖层，再应用 [patches/series](patches/series) 中的 Chromix 补丁。各平台依赖和 GitHub Actions 分阶段构建详见 [BUILDING.md](BUILDING.md)。

Windows 需要 Visual Studio C++ 工具、Windows SDK **10.0.28000.0**（含 Debugging Tools）、Python、Git、PowerShell 7 和 7-Zip。Linux/macOS 使用对应 Chromium 工具链。缓存恢复源码与中间输出，输入变化后 Ninja 仍会重新编译受影响部分。

```powershell
pwsh build/windows/build.ps1 -WorkDir D:\chromix-build -Jobs 8
# 使用原工作目录续编：
pwsh build/windows/build.ps1 -WorkDir D:\chromix-build -Resume -Jobs 8
```

安装相应开发依赖后，可以运行：

```bash
python3 tools/check_patches.py
python3 -m unittest discover -s tools/tests -v
npm --prefix sdk/node test
git diff --check
```

```text
patches/       Chromium 补丁及版本覆盖
build/         平台准备、分阶段编译与打包
docker/        基于 Linux 发布包的容器
site/          GitHub Pages 项目主页
sdk/           Python 和 Node 自动化包装层
tools/         构建校验、诊断与回归测试
docs/          功能指南、设计与验收边界
assets/fonts/  字体资源及来源
```

## 许可证

Chromix 原创代码、补丁集成和 SDK 使用 [BSD 3-Clause License](LICENSE)。Chromium、第三方组件和字体保留各自条款，字体来源见 [assets/fonts/SOURCE.md](assets/fonts/SOURCE.md)。参考项目保留其自身品牌与许可。

[问题反馈](https://github.com/xiaozhou26/Chromix/issues) · [Releases](https://github.com/xiaozhou26/Chromix/releases) · [LINUX DO](https://linux.do)
