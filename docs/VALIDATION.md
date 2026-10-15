# 版本验证

## 未发布

2026-10-14，Windows 交互式桌面环境，Python 3.13.9。

### 便携发布：不再依赖用户安装任何组件

- 起因：真实用户报告 `未找到匹配的 64 位 .NET Desktop Runtime（8.0 或以上）`。按其机器情况核对，该机器
  `C:\Program Files\dotnet\shared` 完全不存在——不是检测误判，而是 v0.1.1 把 Python、.NET、WebView2
  三样都推给用户安装，与“下载即用”相矛盾。
- 方案：网页层从 WebView2 换成 **QtWebEngine**，并把 **CPython 内置**进发布包。因此 .NET Desktop
  Runtime、WebView2 Runtime、pythonnet、clr_loader、cffi、pycparser 与官方 WebView2 SDK 全部移除。
- 已实测确认（净室副本，无 `.venv`、无系统 Python、无 .NET、无 WebView2、无 `.native-webview`）：
  用内置解释器启动真实程序，得到 `startup-report: {"native_window": true}` 与
  `smoke-report: {"native_window": true, "name": "MoocReminder"}`，窗口截图完整显示主界面与绑定引导。
- 已实测确认：内置解释器可导入 `QtCore/QtWidgets/QtWebEngineCore/QtWebEngineWidgets`（Qt 6.12.0），
  并成功访问 `www.icourse163.org`（标题「中国大学MOOC_优质在线课程学习平台」）。
- 已实测确认：真实“连接慕课”流程在 QtWebEngine 下正常——登录窗口打开，状态机进入 `waiting_login`，无错误。
- 便携运行包实测 **675.5 MiB 解压**（内置 CPython 3.13.9 + PySide6 Essentials/Addons/WebEngine）。
- **教训（已写成约束）**：不能按“程序没有 import 这个模块”裁剪 Qt 二进制。Qt 的库跨 wheel 分布，
  `QtWebEngineCore` 依赖只随 `PySide6-Addons` 分发的 `Qt6Positioning.dll`、`Qt6WebChannel.dll` 等；
  按绑定名单删库会让内置浏览器无法加载（实测报 `DLL load failed while importing QtWebEngineCore`）。
  现在 `trim_qt()` 只删 `.pyd`/`.pyi`，`verify_qt()` 在打包时校验关键文件齐全。

### 测试与其它修复

- 完整自动测试：**165 项通过**（`python -m unittest discover -s tests`，退出码 0）。此前记录的 83 项是 v0.1.1 时的规模。
- 修复测试发现的真实缺陷：`browser._settle()` 的兜底分支只判断“稳定”、不判断“非空”，会把一个持续为空、始终没有加载出内容的页面当成有效结果，从而误报“这门课没有作业”。现在两处判断都要求非空。
- `test_site` 改为在 QtWebEngine 上运行（应用实际使用的浏览器），覆盖官网脚本解析、分页与弹窗；
  `_settle` 的“延迟内容不能当空”逻辑改用脚本化页面测试，不再依赖浏览器。
- 本轮补齐了完整依赖环境才跑通全部模块：`PySide6-WebEngine` 与 `playwright` 此前未声明或未安装，
  导致 `test_qt_reader`、`test_site` 长期无法在验证中执行。`requirements.txt` 已补上 `PySide6-WebEngine`。
  后来 `requirements.txt` 又清掉了 QtWebEngine 迁移后不再使用的 `playwright`、`pyinstaller`、
  `websocket-client` 与 `pythonnet`——现在没有任何代码或测试 import 它们。
- 发布打包：离线包实测含许可证正文与第三方声明，包内不含 `.build/`、`wheel-cache`、`__pycache__` 或账号数据。
- 邮件提醒：核心层（配置校验、邮件组装、SMTP 发送、DPAPI 凭据、发送队列与去重重试）与桌面设置界面均已完成并测试；授权码只提交用户新输入的值，留空保持原密文，并有「清除已保存的授权码」入口。
- 本机 `pip install` 在该环境会无进度挂死（已复现三次），改用直接下载官方 wheel 并逐个核对 SHA-256 后本地安装；运行包内的 Essentials 与 shiboken6 校验值与清单一致。

### 测试进程退出时崩溃（已修）

- 症状：测试全部通过（`OK`），但进程退出码为 `0xC0000409`，且只在整套运行时出现——单个模块、单个测试类都是 0。
- 定位过程：`git stash` 后基线为 83 项、退出码 0，确认是本轮引入；随后按模块、按测试类、再按类组合二分，锁定到新增的登录窗测试类与其它 Qt 测试类同时运行。
- 两个原因，都不是 Qt 自身缺陷：
  1. `qt_bootstrap.prepare_qt()` 会把进程的 `TEMP`/`TMP` 指向仓库目录（程序里需要这样，测试里不需要），于是其它模块的临时目录全被建在仓库内。该测试模块现在在调用后立即恢复这两个环境变量。
  2. 新增的登录窗与注册框测试从未关闭自己创建的对话框（`WA_DeleteOnClose` 只在关闭时删除），对象活过解释器退出。
- 修复后：连续三次整套运行退出码 0；仓库内不再产生 `tmp*` 残留（修复前已累积 22 个）。
- 教训：只看 `Ran ... OK` 不足以判断测试通过，必须同时看进程退出码。

### 下载文件的“来自网络”标记

- 已实测确认：合并后的 `启动MoocReminder.bat` 能清除标记并启动。在一个带中文与空格的临时目录中，给 3 个文件打上标记后模拟双击：标记数 3 → 0，且 `launch.vbs` 被正常调用。
- 带参数的诊断路径同样实测通过：`启动MoocReminder.bat --check-runtime` 能把参数交给 `source_launcher.py`。
- 未能在本会话实测：由 Python 通过子进程调用 `Unblock-File`。因此该逻辑改由批处理在真实脚本宿主中执行，Python 侧只做检测提示。
  - 更正：此处最初归因为「子进程调用方式受限」，实际原因是**本会话的子进程写不了用户桌面**（沙箱 ACL 只给 `CodexSandboxUsers` 读权限）。同一脚本在外层 PowerShell 里成功，是因为外层进程不受该 ACL 限制。真实用户的进程不受此限。
- 检测会漏报文件名含非 ASCII 字符的文件（PowerShell 输出经子进程的编码问题）；解除不受影响。
- 换行教训：`cmd.exe` 解析 LF 换行的批处理会丢失行首字符并导致启动失败。`.gitattributes` 已要求 `*.bat`、`*.vbs` 为 CRLF，改动这两个文件后需确认换行未被写成 LF。

### 桌面快捷方式

- 图标显示不出来的原因已查明：旧快捷方式的 `TargetPath` 是 `C:\Windows\System32\wscript.exe`，资源管理器按目标可执行文件解析图标，脚本宿主的图标一旦进入 `iconcache_*.db` 就不再刷新。ICO 文件本身正常（7 个尺寸、全部 32 位色深）。
- 改为指向程序自身后，`IconLocation`、`WorkingDirectory`、`Arguments` 都能被正确读取和校验。
- 已实测（用可写目录冒充桌面，本会话无法写真实桌面）：全新创建、二次启动复用不重写、项目改名后自修复到新位置且不含旧路径、拒绝覆盖其他程序的同名快捷方式、`remove` 只删自己的。
- 已实测拒绝路径的提示文案可读：PowerShell 把 `throw` 的中文写进 CLIXML 时用的是**控制台代码页（中文系统为 GBK）而非 UTF-8**，原先按 UTF-8 解码会得到乱码，用户看不到拒绝原因。现在按 UTF-8 优先、失败回退 ANSI 代码页解码。
- 未能在本会话实测：真实桌面上双击快捷方式的最终效果（沙箱不允许子进程写桌面）。真实用户不受此限。

### 测试偶发失败的教训

- 症状：`test_startup_timeout_stops_the_process_it_started` 偶发 `StopIteration`，单独跑必过、全量跑偶尔失败。
- 原因：测试用 `patch('source_launcher.time.monotonic', side_effect=[0, 21])` 打桩，而 `source_launcher.time` 就是全局 `time` 模块，于是这个有限的 `side_effect` 被 `desktop_shortcut` 里同样调用 `time.monotonic()` 的写入循环一起消耗，两边争抢同一个迭代器。
- 修复：改为替换 `source_launcher` 自己的 `time` 名称（`patch('source_launcher.time', SimpleNamespace(monotonic=...))`），不再污染全局模块。
- 教训：`patch('module.attr')` 若 `attr` 是导入的模块对象，改的是**全局**对象；测试要打桩的是「该模块看到的那个名字」。

### 界面改动要靠真实渲染核对

- 本轮六个用户报告的缺陷有四个是**行为**问题（汇总邮件不发、授权码被清空、发信记录被清理、
  粘贴内容识别过严），但另有两个只在**画面上**才看得出来：侧栏五个入口点下去没有页面切换、
  以及发信记录无处可看。新增页面后逐页截图核对（`.build/artifacts/page-*.png`、
  `dialog-settings.png`、`dialog-email.png`），确认导航高亮、面包屑、表格列与两处时间说明都正确。
- **截图又抓出一个测试没覆盖的真缺陷**：`record_email` 在写完一条记录后用**这一条自己的编号**去清理
  发信历史，于是每发一封新提醒就会删掉其它作业的全部记录——发送记录页因此永远只剩一行。
  已改为只做条数上限清理（按编号清理只应由扫描在拿到全部现存编号时执行），并补了两条回归测试。
- 渲染环境注意：本机 Qt 的 offscreen 平台**没有字体库**，截图里中文全是豆腐块。核对文字必须用真实
  窗口（不要设 `QT_QPA_PLATFORM=offscreen`）。
- 另一个陷阱：`prepare_qt()` 会把进程的 `TEMP` 指向仓库内的 `data/runtime-temp`，所以**先 import
  `qt_desktop` 再调用 `tempfile`** 会让临时目录落进仓库。预览脚本因此把临时数据放在该目录下并即时清理，
  且绝不能连带删除 `data/`——本会话沙箱不允许子进程写 `%LOCALAPPDATA%`，程序会回退到这里保存真实账号数据。

### 开机自启动

- 启动项写在 Windows 的「启动」文件夹（`FOLDERID_Startup`），内容与桌面快捷方式同构，
  只是多带一个 `--background`。桌面与启动文件夹因此共用 `desktop_shortcut.py` 的同一套读写与校验代码。
- 已实测（把启动文件夹指向临时目录）：条目落在启动文件夹、参数带 `--background`、目标为 `pythonw.exe`；
  项目改名或换运行时目录后 `ready` 变假而 `enabled` 仍为真，重新写入后恢复正常且仍只有一个 `.lnk`；
  启动文件夹里有其他程序的同名条目时既不覆盖也不删除。
- **后台启动的就绪信号**：启动器原本要求子进程回报 `native_window` 为真，否则判定启动失败并
  `stop_started_app()` 杀掉它。后台模式没有窗口，这个判定会让开机自启动永远起不来（或起来就被杀）。
  现在 `ready` 或 `native_window` 任一为真即视为成功，并新增两条测试钉住这一点。
- 端到端实测（真实 GUI 进程、`--data-dir` 指向临时目录，绝不碰用户数据）：后台实例回报
  `{"ready": true, "native_window": false, "background": true}` 并持续运行；同一数据目录再启动一次，
  第二个实例回报 `{"already_running": true}` 后以 0 退出，第一个实例不受影响继续运行，`.tmp` 报告文件无残留。
- 单实例用 `QLocalServer` 命名管道而非锁文件，因为还要顺带传递“把窗口叫到前面”。
  实测踩到两个坑：① 发送方写完立刻断开时，字节可能已经在缓冲区里，`waitForReadyRead()` 会因为
  “没有新数据”返回假而把请求丢掉——改为让 `readyRead` 与 `disconnected` 两个信号共用一个缓冲，
  谁先看到完整消息谁触发；② 触发后若在析构后再次进入回调会抛
  `RuntimeError: Internal C++ object already deleted`——入口先看 `done` 标志，并在 `deleteLater()` 前断开信号。
- 未在本会话实测：真实登录 Windows 后的自启动效果（沙箱 ACL 只给子进程对本机「启动」文件夹的读权限）。
  真实用户不受此限。

## v0.1.1

2026-10-13，Windows 交互式桌面环境。

- 完整自动测试：83 项通过，覆盖启动缓存失效、并发锁、取消、缓存保存失败、Tk 响应及退出清理、失效 Python 回退和桌面快捷方式。
- 用户已确认资源管理器双击启动后，启动窗口和桌面快捷方式均出现。
- 干净目录首启实测：无 `.venv`、无启动缓存，主窗口就绪 2.838 秒；包含自动关闭的完整检查 4.844 秒。测试拦截 pip/venv 调用，未触发安装，Qt/pythonnet/cffi 均从运行包加载。
- 首启报告：native_window=true、controls_ready=true、version=v0.1.1。该计时在开发电脑进行，不代表不同硬件首次启动速度。
- 预置依赖包仍要求机器安装受支持的 Python x64、.NET Desktop 和 WebView2。依赖包支持 CPython 3.11–3.15；源码包首次可能需要下载依赖。
- 修复并实测预置 Qt 插件路径，避免机器其他 Qt 发行版影响界面启动。
- 预置依赖来自官方 PyPI wheel，并在构建时验证 SHA-256；原生 SDK DLL、组件许可证与声明随包保留。

## v0.1

2026-10-13，Windows 交互式桌面环境。

- 重命名后的完整自动测试：61 项通过。
- 实际桌面启动报告：native_window=true、controls_ready=true、name=MoocReminder、version=v0.1。
- 实际桌面截图检查：名称和页脚版本显示正确，主窗口布局完整。README 截图使用未登录、无个人数据的空状态。
- 原生网页适配器既有实测覆盖 HTML 渲染、JS 执行、弹出页面、初始化超时清理、重复连接及退出；对应报告见 superpowers/plans/task-2-native-smoke.json。
- 该版本继承了已成功实测的真实 MOOC/SPOC 登录与读取实现。发布仓库和源码包不包含个人账号、浏览器配置、作业缓存或诊断日志。
- 仓库默认账号 userId=0 为占位值，默认补充课程列表为空；首次使用须填写自己的课程主页。
