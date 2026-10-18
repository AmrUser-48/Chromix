# Windows155 x64 第8阶段续编

## donor230 → target231 接口修复

Run `38387174079`（源码 `c05c458c021f0f0964ec031c23395e1b86deb46f`）在
`render_thread_impl.cc:863` 编译失败：`blink::WebString::FromUTF8` 不存在。
新增单文件补丁 `0233-renderer-timezone-webstring-api.patch` 仅将该调用改为
`FromUtf8`，Windows manifest 和 selector 绑定更新为231补丁。0228–0232、0227
和根224补丁保持原字节，已上传 donor230 历史不重写。本提交不修改 workflow 或
migration；latest-donor 迁移由独立变更接入，不能直接将旧225→230迁移当作已支持231。

接口核对使用精确 [Chromium 155.0.8059.39 web_string.h](https://chromium.googlesource.com/chromium/src/+/155.0.8059.39/third_party/blink/public/platform/web_string.h)：
`static WebString FromUtf8(std::string_view s);`，以及 Blink 内部
`operator String() const;`。同时核对 `String::empty()`、`String::Ascii()`、
`base::AutoLock`、补丁提供的 `UxrConfig::Get()` 与 controller 静态方法声明。
新增 ICU 调用与精确版本原有 `SetIcuTimeZoneAndNotifyV8()` 的调用一致；公共桥接及
controller 新接口由实际补丁后的声明参与编译检查。未发现其他同类拼写问题。

专有测试保存完整上游头文件及 SHA256，独立重新下载核对全部14份源码；native harness
直接包含未经改写的 `web_string.h`，不再自建 WebString 声明。只对其依赖、存储实现及
scheduler/V8 使用测试 shim。编译负例要求错误大小写 `FromUTF8` 和错误参数类型
`const std::string&` 均被真实声明拒绝；原有 ICU/DevTools/通知测试及补丁正反重放保留。
这些测试不等价于 Windows x64 编译或浏览器原生验收。

本地验证：时区、155 selector、通用 selector、patch linter 四组测试合计
114 passed、4 skipped；跳过项是未设置 `CHROMIX_155_SOURCES` 的全栈独立重放，
时区专有的独立源码核对已运行通过。`tools/check_patches.py` 和 `git diff --check`
通过，Windows 选中231补丁。Windows x64 续编及浏览器验收仍须后续工作流执行。

## 历史225检查点

- Donor run：`38355909882`，attempt 1，job `115158636797`。
- Donor SHA：`b8821cd2f782ca4da9b998b437fc3acb09cb3fea`。
- Part 1：artifact `11798220854`，9663676664 bytes，SHA256 `6b1c328297a9b7c9565e7bf14b9e70d18c733962783e8463a539acf78fbfc890`。
- Part 2：artifact `11797504531`，3400591127 bytes，SHA256 `9307555b8e6680cfafaa41dc8467688cf00603731334dc37f1adb1006ca86957`。

该文件树已包含 PNG 修复及编译输出。Donor 的 Canvas 和 device 检查通过，整体原生验收仍因跨进程子框架时区和 HTTP/2 首连接复用失败。检查点用于继续修复，不作为已验收发布包。

## 本轮变更

Windows155 在原225补丁后追加0228–0232，共230补丁。配置安装完成后通过 Blink 公共接口同步应用 launch 时区，保持 ICU、V8、worker 通知及 DevTools override 优先级。历史224根补丁和0227字节保持不变。

HTTP/2 仅增加关闭事件与证书验证器候选事件的非判定诊断。请求顺序、首次握手、复用、GOAWAY、会话恢复等断言保持原样；候选事件时间重合不作为因果证明。HTTP/2 根因仍待额外证据确认。

## 恢复与验收

工作流：`.github/workflows/build-win155-x64-stage8-recovery.yml`。

校验固定元数据、下载摘要、压缩包、旧迁移收据和源码身份后，在私有临时目录执行完整补丁反向及正向校验。仅允许精确的五补丁追加和 manifest 摘要更新；迁移保留 `out/Default`、GN 参数、PGO 和已完成对象。任一迁移失败保留阻断标记，必须重新提取有效检查点。

阶段8执行增量编译及完整原生验收。有效树在失败时再次保存，完整验收成功后才上传最终包。源码级测试、ICU测试及迁移测试均不能替代 Windows 原生编译和浏览器验收。
