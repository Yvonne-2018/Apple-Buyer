# GitHub 桌面安装包

## 构建范围

- Mac M 系列：`macos-15` arm64 runner，Python 3.12 arm64，输出 DMG。
- Windows 64 位：`windows-2022` x64 runner，Python 3.12 x64，输出 NSIS `-setup.exe`。
- Node 固定 22.23.3、pnpm 11.9.0，依赖使用现有锁文件。worker 在目标系统原生打包，包含 Python、依赖和门店 JSON。
- Intel Mac 不参与构建；Web 工作流仅手动触发。

工作流：`.github/workflows/build.yml`。手动运行或 `v*` 标签触发，不创建 Release。运行结束后下载对应 Artifacts（保留 30 天），解压得到安装文件。

## 构建检查

两种系统均执行完整 worker、Rust、Playwright 测试和冻结 worker 冒烟检查。安装包构建后，Mac 检查 `.app` 内的 worker，Windows 在 runner 临时目录静默安装并检查主程序和 worker。

本机准备阶段的检查：307 项 worker、35 项界面和 22 项 Tauri Rust 测试通过，TypeScript / Vite 构建通过。此前记录的四项失败已处理：

- 数量上限测试使用无其他商品的购物袋夹具，使测试实际走到数量校验；购物袋清理仍由独立用例覆盖。
- 不保存密码场景显式取消“保存在本机文件”，同时保留默认保存密码场景。
- 购买预设保存后同步当前账号的临时购买缓存，保留城市、门店等其他选择，避免旧缓存覆盖刚保存的 SKU / 数量。
- 取消任务测试根据带耗时的状态区域断言“已取消”。

以上替代 `ORDER_TEMPLATES.md` 和 `PICKUP_STORES.md` 中早先的原有失败记录；云端各系统结果以具体 Actions 运行为准。

## 签名与安装

Mac 当前使用 ad hoc 签名，未配置 Apple Developer ID 及公证；Windows 未配置代码签名证书。安装包尚不能宣称已认证发布者，用户可能遇到系统提示。证书可在后续提供后配置，当前工作流不要求上传任何 Apple 账号、商城会话、订单或本机用户数据。
