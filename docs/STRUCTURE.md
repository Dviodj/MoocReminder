# 仓库结构

这份文档说明每个目录和文件的用途，以及哪些文件属于**当前运行链路**、哪些是**历史遗留**。
整理原则只有一条：仓库根目录只放被版本控制的源码和发布入口，一切构建、发布、实测产物都收进 `.build/`。

## 目录一览

```
MoocReminder/
├─ 启动MoocReminder.bat                 唯一的用户入口：先解除下载标记，再启动；带参数时转给启动器做诊断
├─ launch.bat                           源码检出的等价入口，转调上面的脚本
├─ launch.vbs                           挑一个能加载 tkinter 的 python，拉起启动窗口（无控制台窗口）
├─ source_launcher.py                   启动器主体：选运行环境、校验依赖、建快捷方式、拉起主窗口
├─ launcher_ui.py                       Tk 启动窗口（阶段进度、取消、重试、日志入口）
├─ desktop_shortcut.py                  快捷方式读写：桌面与「启动」文件夹共用，创建 / 复用 / 移动后自修复，只碰本程序自己的
├─ autostart.py                         开机自启动：在「启动」文件夹里放置带 --background 的启动项
├─ qt_desktop.py                        PySide6 主窗口，桌面版真正的界面（含托盘与单实例保护）
├─ portal.py                            界面状态机与提醒规则，向界面提供快照
├─ worker.py                            串行浏览器工作线程，界面通过队列通信
├─ browser.py                           读取编排：发现课程、逐门读取作业（必须由调用方提供内置浏览器）
├─ site_scripts.py                      注入官网页面执行的 JS 选择器，页面结构变化时改这里
├─ qt_reader.py                         网页层：QtWebEngine 适配器，登录窗口与后台读取共用
├─ core.py                              截止时间解析、提醒窗口计算（纯逻辑，无界面依赖）
├─ storage.py                           本地数据原子读写、损坏备份、账号隔离、邮件设置校验
├─ profiles.py                          账号档案：账号列表、当前账号、共用设置与按账号隔离的数据
├─ emailer.py                           邮件提醒：配置校验、邮件组装、SMTP 发送、发送队列（可扩展通道接口）
├─ mail_log.py                          把内部发送记录键译成人能看懂的「时刻 / 类型 / 结果 / 说明」（无界面依赖）
├─ mail_guide.py                        各服务商授权码获取步骤（图文教程内容，按发件邮箱域名选择）
├─ secrets_store.py                     邮箱授权码的本机加密（Windows DPAPI，缺失时明确标注回退）
├─ qt_bootstrap.py                      固定 Qt 的 TEMP/ICU，并设置 QtWebEngine 必需的沙箱与写入目录
├─ bundle_runtime.py                    构建便携运行包：内置 Python + Qt wheel（发布者用）
├─ package_source.py                    打出源码包 / 离线包 ZIP（发布者用）
├─ version.py                           产品名与版本号，界面与打包共用
├─ requirements.txt                     开发环境依赖（PySide6 与 QtWebEngine）
├─ requirements-runtime.txt             运行期依赖，随包版本校验用
├─ LICENSE                              本项目 MIT 许可证
├─ THIRD-PARTY-NOTICES.md               随包分发的第三方组件与许可证声明（发布包必带）
├─ email_sender.example.json            发件邮箱配置模板（仓库只留空模板，真实配置不提交）
├─ licenses/                            Qt 的 LGPL-3.0 / GPL-3.0 正文（发布包必带，见其 README）
├─ assets/                              图标
├─ docs/                                验证记录与本文件
├─ tests/                               自动测试
├─ .launcher-state/                     **本机运行时状态**：启动日志、运行缓存、快捷方式记录
└─ .build/                              **本机产物**：构建、发布、实测的一切输出（见下）
```

## 便携运行包

发布包不要求用户安装任何东西：`bundle_runtime.py` 把**内置 CPython** 与 **Qt wheel** 放进
`.build/runtime-bundle/`，`source_launcher.py` 优先使用它。

```
.build/runtime-bundle/
├─ python/python.exe                  内置解释器（含 tkinter，启动窗口需要）
├─ python/Lib/site-packages/PySide6    Qt 与 QtWebEngine
└─ bundle.json                         组件版本与 CPython 压缩包的 SHA-256
```

**为什么 .NET 与 WebView2 都不再需要**：网页层已从 WebView2 换成 **QtWebEngine**，而 QtWebEngine
随 PySide6 一起分发。因此 pythonnet、clr_loader、cffi、pycparser 与官方 WebView2 SDK DLL 全部移除。

`bundle_runtime.py` 只删**未被使用的 Python 绑定**（`.pyd`/`.pyi`），**不删任何 Qt 库**：
Qt 的库跨 wheel 分布，`QtWebEngineCore` 依赖只随 `PySide6-Addons` 分发的
`Qt6Positioning.dll`、`Qt6WebChannel.dll` 等。按“程序没有 import 这个模块”去删库会把内置浏览器弄坏，
`verify_qt()` 会在打包时拦住这种残缺包。

## `.build/` 里放什么

| 子目录 | 内容 | 说明 |
| --- | --- | --- |
| `runtime-bundle/` | 内置 CPython 与 PySide6 / QtWebEngine，以及 `bundle.json` | 便携包的核心；由 `bundle_runtime.py` 生成 |
| `wheel-cache/` | 已校验 SHA-256 的官方 wheel | 构建运行包时的下载缓存 |
| `releases/` | 源码包 / 离线包 ZIP | 由 `package_source.py` 生成 |
| `temp/` | 启动与实测用的临时目录 | 启动器在需要安装依赖时创建 |
| `artifacts/` | 桌面启动实测报告（`*-smoke.json`）与界面截图 | 人工排查用，不参与运行 |

整个 `.build/` 已被 `.gitignore` 排除，可以随时删除，只影响下次构建或实测的速度（重建运行包需要重新下载约 250 MB）。

### 留在根目录的例外

**`.launcher-state/`**：启动器运行时目录，已发布的离线包按当前布局读写它，移动会让已发布的包失效。为兼容它留在根目录。

### 旧布局兼容

`runtime-bundle/` 在 v0.1.1 及更早版本中的位置是仓库根目录的 `.runtime-bundle/`，且当时的运行包里
只有 Python 组件、依赖系统安装的 Python、.NET 与 WebView2。
`source_launcher.py` 的 `artifact_root()` 会**先找 `.build/`，再回退到旧位置**，因此已经发布出去的旧包也能找到自己的运行包。

### 发布包内的路径规则

`package_source.py` 打离线包时会**剥掉本机的 `.build/` 层级**，包内固定为 `runtime-bundle/...`。
这一点关系到包能否生效：启动器按包根寻找运行包，若包内带上 `.build/` 前缀，用户解压后程序就找不到随包组件。
`tests/test_packaging.py` 守住这条规则、许可证随包、以及组件许可证缺失时拒绝出包。

## 当前链路与历史遗留

### 当前桌面版实际使用的模块

`source_launcher.py` → `launcher_ui.py` → `qt_desktop.py` → `portal.py` → `worker.py` → `browser.py` → `site_scripts.py` + `qt_reader.py`，配套 `core.py`、`storage.py`、`profiles.py`、`qt_bootstrap.py`、`desktop_shortcut.py`、`autostart.py`、`version.py`。

`browser.py` 不再自带浏览器：它必须由调用方通过 `context_provider` 提供，桌面版注入的就是
`qt_reader.py` 的 QtWebEngine 适配器。没有适配器时它会明确报错，而不是静默失败。

邮件提醒是旁路通道：`portal.py` 复用 `core.due_reminders` / `core.due_digests` 判断该发什么，把任务交给 `emailer.Mailer` 的队列；`emailer` 只依赖标准库与 `secrets_store`，凭据经 DPAPI 加密后随其它设置存在本机数据目录，缺失 DPAPI 时会写入明确标注的回退格式。

发件与收件是分开的：**发件账号由发布者配置**在 `email_sender.json`（见 [EMAIL_SENDER.md](EMAIL_SENDER.md)），
用户只填自己的收件邮箱。`emailer.resolve()` 把用户留空的项用预置值补齐，用户填了就以用户的为准；
`resolve_password()` 同理决定用哪一份凭据。`package_source.py` 会拒绝打出未配置发件账号的包。

## 本机数据布局

数据目录（默认 `%LOCALAPPDATA%/MoocDeadlineReminder`，不可写时用项目内 `data/`）：

```
app.json                     当前账号，以及所有账号共用的设置（检查间隔、提醒窗口等）
accounts/<账号>/data.json    一个账号：它的课程主页、邮箱绑定、作业缓存、完成标记、提醒记录
webview2-profiles/<userId>/  一个账号的登录状态（Cookies 等）
```

`profiles.py` 负责这套布局：`list_profiles()` 列出账号，`account_options()` 给出登录窗应显示的账号
（排除尚未被认领的占位档案），`register()` 用账号名建号，`login()` / `set_active()` 选定当前账号，
`load_store()` 取出某个账号的档案，`replace_store()`（在 `portal.py`）让界面在不重启程序的前提下换到另一个账号。

账号**不需要密码**：它们只存在这台电脑上，账号名就是唯一标识。绑定状态由 `core.binding_state()` 判定——
慕课主页必须指向真实 userId（默认占位 `userId=0` 不算），邮箱必须已填；两项齐了才允许检查作业。

账号设置与共用设置的分界见 `profiles.split_settings()`：课程主页、邮箱、作业数据属于账号；
检查间隔、提前提醒小时数、是否包含测验/历史学期属于共用。

首次以旧布局（数据目录下只有一份 `data.json`）启动时，`Profiles` 会自动把它收进
`accounts/<账号>/data.json`，并把原文件改名为 `data.migrated.json` 留档，内容不丢。

## 以后要把账号放到服务器上

账号数据层目前只在本机，但界面只通过 `Profiles` 的这几个方法访问账号，因此上云时替换这一层的实现即可，
界面不需要改：

| 方法 | 职责 |
| --- | --- |
| `account_options()` / `list_profiles()` | 可登录的账号清单 |
| `register(name)` | 新建账号 |
| `login(key)` / `set_active(key)` | 选定当前账号 |
| `load_store(key)` | 取出账号档案 |
| `save_shared(settings)` | 保存所有账号共用的偏好 |

### 历史遗留与已清理

早期方案留下、又不在桌面运行链路上的文件已删除：

| 已删除 | 原用途 | 删除原因 |
| --- | --- | --- |
| `gui.py`、`tests/test_gui.py` | 第一版 Tkinter 界面 | 桌面版用 `qt_desktop.py`；这两个文件只互相引用 |
| `web/`（`index.html`、`app.css`、`app.js`）、`portal.create_server` | 本机 HTTP 服务与网页版界面 | 桌面版直接调 `Controller`，HTTP 服务只被测试用到；页脚版本注入随之取消，界面版本号仍由 `version.py` 统一提供 |
| `qt_login_click_check.py` | 登录按钮的人工实测脚本 | 直接运行程序点一次即可验证，不需要专用脚本 |
| `docs/superpowers/plans/task-2-native-smoke.json` | 某次 native 适配实测的输出快照 | 实测产物，全仓库零引用 |

`launch.bat` **保留**：它是源码检出的等价入口，转调 `启动MoocReminder.bat`；发布包只含后者。

**更早一次迁移中删除**：`webview2_reader.py`、`webview_runtime.py`、`dom_adapter.py`、
`native_browser.py`、`native_adapter_smoke.py`、`.native-webview/`（5 个 SDK 文件），
以及 `browser.py` 的 Playwright 回退分支。它们的整套依赖（.NET Desktop Runtime 与
WebView2 Runtime）也随之不再需要。

## 相关约定

- 仓库根目录保持扁平，模块直接放在根目录；如需改成 `src/` 包结构，必须同步 `source_launcher.py`、`launch.vbs` 与打包脚本，并重跑启动实测。
- 个人登录配置、作业缓存、浏览器配置按账号存在本机数据目录，绝不提交，也绝不进入发布包；`package_source.py` 会校验跟踪文件里不含这些路径。
- **Qt 的库不能按“有没有 import”来裁剪**：库跨 wheel 分布，`QtWebEngineCore` 依赖只随 `PySide6-Addons` 分发的 DLL。`bundle_runtime.trim_qt()` 只删 `.pyd`/`.pyi`，`verify_qt()` 在打包时校验关键文件齐全。
- 自动测试需要 64 位 Python 3.11–3.15 并安装 `requirements.txt`；缺少 PySide6-WebEngine 时，相关测试模块会导入失败，收集到的用例数会变少。这是环境未就绪，不是代码缺陷。
