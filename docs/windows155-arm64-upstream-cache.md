# Windows155 ARM64 固定缓存构建

## 构建来源

- Chromium：`155.0.8059.39`。
- 上游仓库：`ungoogled-software/ungoogled-chromium-windows`。
- Run：`37831345827`，attempt 1，`completed/success`。
- Workflow：`.github/workflows/build-arm.yml`。
- Head：`989b4382364baaddb8b3180e1eb1030ca96a7273`。
- Core：`ffeb45a2bb88bc56a1dfed31410aeb10e9efcd5a`。
- Artifact：`11702070420`，`build-artifact-arm`，16004417072 bytes。
- SHA-256：`08244eb203c3fc6551c761f70bcb4744ec55735c4e1d1a6f51c67a77c12d4f50`。
- 到期时间：`2026-10-15T11:49:46Z`。

元数据已核验。下载后的实际压缩包摘要、恢复后源文件身份和构建输出由云端任务继续核验；元数据核验本身不代表包内字节已在本地验证。

## 恢复与编译

使用 `build-win-arm64-github.yml`，指定 `build_profile=native`、`use_upstream_cache=true`、`upstream_run_id=37831345827`。恢复源文件及 `out/Default`，保留原 PGO 参数和可复用编译输出。必需缓存恢复失败时停止任务。后续阶段继续使用本次任务保存的文件树。

Windows155 选择根补丁及 Windows 专属 PNG 追加补丁，共225个补丁。MIDL 修复同时绑定 ARM64 artifact 身份与精确脚本摘要；脚本摘要来自同版本源码重建，实际解包字节不匹配时停止。

编译在 `windows-2022` x64 宿主交叉生成 ARM64 产物。V8 metagen 使用宿主 AMD64 Python 和 AMD64 `libclang.dll`；工具校验进程、宿主及 PE 架构，目标 ARM64 不改变宿主工具架构要求。

## 验收

编译完成后在 `windows-11-arm` 执行包摘要、PE 架构、版本、原生 headless 退出和完整指纹验收。编译成功与原生验收分别记录；仅完整验收通过的产物具备后续发布资格。

x64 使用独立旧检查点恢复，详见 [Windows155 第7阶段恢复](windows155-stage7-png-recovery.md)。本 ARM64 缓存配置不修改正在运行的 x64 恢复分支。
