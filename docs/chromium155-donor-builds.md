# Chromium 155 指定上游文件树构建

本分支用于用户提供的三个 `155.0.8059.39` 编译缓存。它与仍在处理的 macOS154 构建、已发布的154 Release分别管理，不把版本号替换等同于完整浏览器验收。

## 来源

Core gitlink 均为 `ffeb45a2bb88bc56a1dfed31410aeb10e9efcd5a`，版本 `155.0.8059.39`，revision 1。

| 平台 | 上游运行 | 平台提交 | 文件树 artifact |
|---|---|---|---|
| Linux x64 | `37906884923` | `025d99db1673a21992534551598b73ca79360762` | `11652570743` |
| Linux ARM64 | `37906884923` | 同上 | `11656090008` |
| Windows x64 | `37831345909` | `989b4382364baaddb8b3180e1eb1030ca96a7273` | `11670281902` |

- Linux x64 outer SHA-256：`34182b5fdbec949fbe29d320a7d7200eb9cd4b284c18b8daa0c516ab4816fa1a`。
- Linux ARM64 outer SHA-256：`a4c0bfd8020cd34f27a652ae449f9e8547a468b8b813a11a64acf8a0891db447`。
- Windows x64 outer SHA-256：`4648e4426093106521986dc5a4dc66b47d861250710b3391f5ed7cf73d11db5c`。

完整大小、时间和摘要以 `build/upstream-cache.json` 为准；下载时还必须验证 GitHub 返回的运行、生产者仓库、精确 artifact 身份和实际归档字节。

本分支没有 Windows ARM64 155 文件树；Windows source 只登记 x64，ARM64 请求必须明确拒绝，不把旧154文件树与155 pins混用。main 的已发布版本、SDK下载通道和Docker标签不因本分支适配而更新。

## 补丁适配

保持基础224补丁及历史152/154覆盖不变，新增 `patches/chromium155/`，由精确版本、core和overlay身份选择。

独立下载155固定源码目标文件，V8/WebRTC按其DEPS固定提交处理；应用目标相关core与平台覆盖后，分别测试未域名替换/已域名替换的Linux与Windows完整224补丁链，执行反向/正向回放。该检查证明目标源码的补丁可应用性，不代表全Chromium C++接口编译、链接或运行验收完成。

155的Canvas图像导出实现变化采用对应原生颜色转换路径；不能机械搬用154图像格式假设。噪声结果仍须对最终新浏览器执行 `tools/browser_feature_check.py`。

## 恢复工具

Linux生成器、TypeScript/esbuild外链例外和Windows MIDL loader只能接受已核验版本身份。新的原始源码和包摘要需要重新取得证据；未知字节保持拒绝，不能仅更新版本元组让旧摘要检查失效。

Chromium155固定源码的 `build/vs_toolchain.py` 仍要求 Windows SDK `10.0.28000.0`，因此扩展原有153/154的相同安装器映射到155，保留安装器SHA-256验证。

## 构建

在独立155分支调度以下三个工作流，显式设置 `use_upstream_cache=true`：

- `build-linux-x64.yml`：fast、staged、自动并行。
- `build-linux-arm64.yml`：fast、staged、自动并行，并在原生ARM64上验证最终包。
- `build-win-x64-github.yml`：native、自动并行、`upstream_run_id=37831345909`。

缓存失效、完整性失败或身份不符时禁止冷构建回退。失败后的续编应优先使用该次155运行最新有效检查点；154与155不能仅改配置直接跨版本复用Chromix层快照。

本次无授权声称网站检测效果、完整设备能力等价或发布门禁已通过；以实际构建、包校验、原生验收及像素诊断报告为准。
