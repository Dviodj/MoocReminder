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
- 未能在本会话实测：由 Python 通过子进程调用 `Unblock-File`。本会话的进程在 `Zone.Identifier` 数据流上被系统拒绝（写入、删除、经子进程调用均失败，而同一命令在外层 PowerShell 中成功），因此该逻辑改由批处理在真实脚本宿主中执行，Python 侧只做检测提示。
- 检测会漏报文件名含非 ASCII 字符的文件（PowerShell 输出经子进程的编码问题）；解除不受影响。
- 换行教训：`cmd.exe` 解析 LF 换行的批处理会丢失行首字符并导致启动失败。`.gitattributes` 已要求 `*.bat`、`*.vbs` 为 CRLF，改动这两个文件后需确认换行未被写成 LF。

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
