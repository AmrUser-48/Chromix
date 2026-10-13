# Windows155 已编译检查点：PNG 修复与原生复验

## 原始失败

Run `38175278759` 第7阶段完成Chrome编译，但原生fingerprint门禁失败。原报告保留47条错误，其中PNG为重复套件/作用域中的同类问题；HTTP/2的三条记录是同一个生命周期失败的汇总、具体断言与退出码。

### PNG

独立解码Windows报告中的20对payload后，RGBA、alpha与ICC完全一致，同API重复输出稳定。差异为opaque `toBlob`的RGB PNG和`toDataURL`的RGBA PNG。该现象与Linux155已修复的源码机制一致。

Windows专属追加 `patches/chromium155-windows/0227-opaque-png-export-consistency.patch`，SHA-256 `4c048a50930c3541a3bb9e4442ad7f18e94c04ebccf86c0cfbfc286b15a70751`。只规范化PNG且opaque输入的异步实际转换，不改JPEG/WebP、半透明优化、ICC或噪声开关。原字节一致性门禁保留。

Windows有效序列由224变225，Linux专属controller补丁不加入Windows。

### HTTP/2 生命周期仍需单独验证

原context0五阶段连接ID为 `[1,2,2,3,3]`，context1为`[4,4,4,5,5]`。失败在initial→reuse的连接变化；两个context的GOAWAY、重连和重连后复用均有成功证据。

native NetLog和服务器之间13个请求绑定一致，没有证据证明observer绑定错误。首个连接可能在启动阶段被配置/证书验证器更新标记going-away，但尚无直接调用者证据。不得增加预热、重试至成功、改写initial关系或豁免失败。

本恢复流程不修改该门禁；PNG通过也不代表整套验收通过。

## 固定已编译来源

- Run `38175278759`，stage7，attempt1，producer job `115019936453`。
- Donor源码 `f06673f68e0ca6dc55e81102f0331ab7b6316bcf`。
- `11784517650`：part1，9663676664 bytes，SHA-256 `0586764e680978b54e3e26ee9c77875d3505fae80f72cab9c450c1c58d982827`。
- `11785560392`：part2，3398865370 bytes，SHA-256 `0384eb4b99b3224b8ea895ac937be09a661a905e21ee22333b08ca1aa9e6c386`。

该job验收失败，但Ensure build tree snapshot及实际分卷上传均成功。检查点来源元数据、实际ZIP摘要、7-Zip完整性、目标架构、PGO参数与原源码收据分别校验，任一步失败即停止。

## 增量恢复

`build-win155-x64-stage7-recovery.yml` 从上述文件树恢复，在私有scratch中验证旧224补丁反向/正向与新225补丁反向/正向，再写入仅受新增补丁影响的源文件和新的准备收据。保留对象缓存、`.ninja_log`、原PGO2参数及固定metagen依赖；不会下载上游源码重新准备，也不会清空整个输出目录。

第8阶段执行增量编译、源码验收、原生启动和完整fingerprint gate。报告中的launcher摘要不能单独证明新实现：需同时记录`chrome.dll`和新source receipt，并以新原生运行报告为准。

最终包仅在所有门禁通过时上传为成功产物。失败时保留安全的最新文件树和诊断，不发布Release、不改SDK下载通道。
