# Apple-Buyer 桌面安装包

## 应用与安装包名称

- 应用、窗口与系统安装项显示为 `Apple-Buyer`。
- Mac M 系列：`Apple-Buyer_0.1.0_aarch64.dmg`，内含 `Apple-Buyer.app`。
- Windows 64 位：`Apple-Buyer_0.1.0_x64-setup.exe`，主程序为 `apple-buyer.exe`。
- GitHub Artifacts：`Apple-Buyer-mac-arm64`、`Apple-Buyer-win-x64`。
- 内置业务进程为 `apple-buyer-worker`（Windows 带 `.exe`）；本地 Web 程序为 `apple-buyer-web`。
- 保留原应用标识 `com.niulai.desktop`、Web 数据目录和凭据服务名，升级改名后仍读取原有账号、模板与设备信任。

## 构建范围

- Mac M 系列：`macos-15` arm64 runner，Python 3.12 arm64，输出 DMG。
- Windows 64 位：`windows-2022` x64 runner，Python 3.12 x64，输出 NSIS `-setup.exe`。
- Node 固定 22.23.3、pnpm 11.9.0，依赖使用现有锁文件。worker 在目标系统原生打包，包含 Python、依赖和门店 JSON。
- 流水线统一启用 Python UTF-8，避免 Windows 英文 runner 输出中文时使用 CP1252 报错。
- Intel Mac 不参与构建；Web 工作流仅手动触发。

工作流：`.github/workflows/build.yml`。手动运行或 `v*` 标签触发，不创建 Release。运行结束后下载对应 Artifacts（保留 30 天），解压得到安装文件。

## Release 下载

普通使用者请从 [GitHub Releases](https://github.com/Yvonne-2018/Apple-Buyer/releases/latest) 直接下载 DMG 或 EXE。

- [Apple-Buyer v0.1.0](https://github.com/Yvonne-2018/Apple-Buyer/releases/tag/v0.1.0) 提供 Mac M 系列和 Windows 64 位安装包。
- 标签 `v0.1.0` 指向实际构建安装包的代码版本 `171433f57416de4b328d5f2ab4dc3eebe653528b`，对应下方的更名后验证记录。
- 本次手动发布已验证的安装包，不改变工作流的自动发布行为；上传前按原构建记录核对两份安装包的 SHA-256。

## 构建检查

两种系统均执行完整 worker、Rust、Playwright 测试和冻结 worker 冒烟检查。安装包构建后，Mac 检查 `.app` 内的 worker，Windows 在 runner 临时目录静默安装并检查主程序和 worker。

本机准备阶段的检查：307 项 worker、35 项界面和 22 项 Tauri Rust 测试通过，TypeScript / Vite 构建通过。此前记录的四项失败已处理：

- 数量上限测试使用无其他商品的购物袋夹具，使测试实际走到数量校验；购物袋清理仍由独立用例覆盖。
- 不保存密码场景显式取消“保存在本机文件”，同时保留默认保存密码场景。
- 购买预设保存后同步当前账号的临时购买缓存，保留城市、门店等其他选择，避免旧缓存覆盖刚保存的 SKU / 数量。
- 取消任务测试根据带耗时的状态区域断言“已取消”。

以上替代 `ORDER_TEMPLATES.md` 和 `PICKUP_STORES.md` 中早先的原有失败记录；云端各系统结果以具体 Actions 运行为准。

## 2026-10-15 更名后验证

[本次 GitHub Actions 构建与安装包](https://github.com/Yvonne-2018/Apple-Buyer/actions/runs/38595694470)，代码版本 `171433f`，Mac M 系列与 Windows 64 位均成功：

- 两个平台各通过 307 项 worker 与 35 项界面测试；Mac 通过 22 项 Rust 测试，Windows 通过 21 项。
- Mac 包内 worker 检查通过；本机另以只读方式挂载下载的 DMG，确认 `Apple-Buyer.app`、主程序 `apple-buyer`、原应用标识、arm64 主程序及 worker，严格签名校验和包内离线检查均通过。
- Windows runner 使用 x86_64 Rust 工具链；EXE 静默安装成功，检查安装后的 `apple-buyer.exe` 和 `apple-buyer-worker.exe`，包内离线检查通过。未在本机 macOS 上运行 Windows 应用。
- Artifacts 为 `Apple-Buyer-mac-arm64` 与 `Apple-Buyer-win-x64`，均已下载并核对安装包文件名。

## 签名与安装

Mac 当前使用 ad hoc 签名，未配置 Apple Developer ID 及公证；Windows 未配置代码签名证书。安装包尚不能宣称已认证发布者，用户可能遇到系统提示。证书可在后续提供后配置，当前工作流不要求上传任何 Apple 账号、商城会话、订单或本机用户数据。
