# macOS154 原生退出问题：当前证据

## Intel Helper 缺件已修复，但包仍未通过启动门禁

- 原运行 `38165640844` 完成浏览器编译，原 ZIP SHA-256 `4c47b9da62ea977cfb52714abebeb5db42bb75eb11c80a14391e64d876ed005e`。
- 普通 `Chromium Helper.app` 缺少其 `CFBundleExecutable`；其他现存 Mach-O 均为 x86_64。该包的 version 检查可通过，但 headless 子进程 launch error 1003，随后异常退出。
- 恢复运行 `38188570246` 从原第2阶段检查点 `11719847390,11719568394` 修复普通Helper及Framework复制输出，保留对象缓存，源代码前后验收一致。
- 修复后 ZIP SHA-256 `57ddd56c9d7e6f85ef1df63b7564149dc0bbffa318f92b7ac1e0c6982635aec9`，结构校验发现16个Mach-O且均为x64。
- `--version` 正常；原样 sandboxed headless smoke 已打印期望DOM，GPU/network launch1003消失，但60秒内不自然退出，故仍然失败。不能以marker出现代替正常退出门禁。

## 本机 ARM64 证据与 Intel 证据分开

ARM64 `154.0.8037.97` 原失败包的两次本机复现中，主线程已进入 ThreadPool 的 `CompleteShutdown()`；worker 停在 `KeychainKeyProvider` → `KeychainPassword` → `SecItemCopyMatching` → securityd IPC。关闭进程由内部 watchdog 返回2，不是正常退出。

诊断对照中，mock keychain 或关闭对应Keychain provider分别可自然退出，但它们仅用于定位，**不作为生产修复、默认配置或发布门禁参数**。Keychain key 查询还可能在not-found分支创建数据，不能盲目把全部任务改成不等待，也不能改成明文/固定密钥存储。

Intel 云端sample受旧工具2秒限制未得到有效主线程栈，且超时仍有Helper子进程。因此ARM64的Keychain结论不能直接外推到Intel；新诊断必须在原生Intel上提前采样。

## 固定成品免编译诊断

`.github/workflows/diagnose-macos-x64-exit.yml` 只读取修复后的既有成品 artifact `11721013885`，固定其run/source/producer步骤/大小/外层摘要及内层ZIP摘要；执行严格bundle验证后，原生Intel诊断相同退出场景。

- 不重新编译、不改源补丁、不重签、不给 `--no-sandbox`。
- 原退出要求仍为60秒、自然rc0且DOM标记存在。
- 提前采样到实际文件，不用 `/dev/stdout`，同时记录真实parent退出及子进程状态。
- 捕获输出应有界，但不能限制浏览器自身profile写入来改变问题。
- 新诊断不改写旧报告、不上传Release；需要明确修复并重新执行正式原生门禁后才能评估发布。

当前两个macOS154候选包均不能因为编译完成就声明完整可用；公开下载入口保留原已发布版本。
