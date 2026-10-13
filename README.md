# MoocReminder v0.1

把 MOOC 作业的截止时间集中起来，在忘记提交之前提醒你。

## 为什么开发这个程序

我们也经常被忘交 MOOC 作业这件事困扰。

同时上几门课时，作业和测验分散在不同课程页面里，截止时间也各不相同。原本想着“过两天再做”，一忙起来就忘了；等到重新打开课程，才发现提交入口已经关闭。明明打算完成，却因为没记住截止时间错过了作业。

因此，我们开发了 **MoocReminder**：自动检查课程中的作业与测验，把截止时间整理到一个窗口，并在到期前提醒。希望少一些“怎么已经截止了”的懊恼，让大家更容易安排自己的学习时间。

![MoocReminder 界面](docs/images/overview.png)

## v0.1 目前能做什么

- **集中查看截止时间**：读取中国大学 MOOC 和校内 SPOC 的作业、测验，按截止时间整理。
- **定期检查与提前提醒**：默认每 30 分钟检查，提前 3 天、1 天、3 小时弹窗提醒。
- **按自己的节奏设置**：支持修改检查间隔、提醒时间、课程主页及补充作业链接。
- **记录完成情况**：支持搜索和本地完成标记，完成后可停止该项提醒。
- **保留已有记录**：单门课程读取失败时保留上次结果，登录配置按账号保存在本机。

主窗口采用 macOS 风格，程序运行在 Windows 上；通过原生 WebView2 窗口登录官网，登录窗口与后台检查共用本地配置。

## 未来希望扩充的方向

现在的提醒发生在电脑上。接下来，我们希望把提醒送到更常看的地方，并进一步打通任务处理流程。

- [ ] **微信提醒**：把即将到期的作业推送到微信，离开电脑后也能及时收到提醒。
- [ ] **邮箱提醒**：支持邮件通知和作业汇总，让截止时间进入日常查看的收件箱。
- [ ] **domooc 联动与自动完成**：探索接入 [domooc](https://domooc.top/domoocreadme)，将作业检测、截止提醒与自动完成流程衔接起来，并同步处理后的任务状态。

这些方向目前处于规划阶段，具体接入方式会逐步验证和完善。我们的长期目标是让“发现任务、收到提醒、及时处理、更新状态”形成连贯的流程，减少遗漏和重复操作。

## 快速开始

### 运行环境

- Windows。
- 64 位 Python 3.11 或以上。
- .NET Desktop Runtime 8 或以上。
- Microsoft Edge WebView2 Runtime。

### 使用步骤

1. 下载 [v0.1 发布包](https://github.com/Dviodj/MoocReminder/releases/tag/v0.1)，解压完整文件夹。
2. 双击 **启动MoocReminder.bat**（也可用 **启动程序.bat**）。首次启动会安装 Python 依赖并验证网页组件。
3. 打开 **提醒设置**，粘贴自己的课程主页链接：`https://www.icourse163.org/home.htm?userId=你的数字账号#/home/course`。默认的 `userId=0` 是占位值，需要替换。
4. 点击 **连接慕课**，在打开的官网窗口中登录。
5. 返回主窗口点击 **检查作业**，之后保持程序运行。

最小化后仍会检查和提醒；关闭程序后停止监测，关机或休眠期间不会提醒。默认检查当前学期，历史学期可在设置中启用。

官网未明确显示提交状态的条目会标为“请核对”。程序读取的是作业提交截止时间，互评截止时间不会列入提交提醒。

## 本地数据与问题排查

设置、作业缓存和完成标记优先保存在 `%LOCALAPPDATA%/MoocDeadlineReminder`；该目录不可写时，使用项目内的 `data` 文件夹。登录配置保存在数据目录下的 `webview2-profiles/<账号>`。

个人登录配置和作业缓存不上传仓库，也不包含在发布包中。换电脑后需要重新登录。启动失败时可查看 `.launcher-state/startup.log`；读取失败时可在主窗口的 **连接详情** 查看错误。

当前版本以源码方式运行。随附的官方 WebView2 SDK DLL 许可证和声明见 [.native-webview/LICENSE.txt](.native-webview/LICENSE.txt) 与 [NOTICE.txt](.native-webview/NOTICE.txt)。

## 开发与验证

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe webview_runtime.py --prepare
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

v0.1 已通过 61 项自动测试，并验证了真实 MOOC/SPOC 的登录与读取。`native_adapter_smoke.py` 验证原生网页组件，`qt_login_click_check.py` 验证实际连接按钮。测试依赖 Windows GUI 和 Edge，应在交互式 Windows 会话运行。

课程读取依赖官网当前页面结构。登录失效或网站结构变化时，程序会显示错误并保留缓存。

## 欢迎一起完善

如果你也被忘交 MOOC 作业困扰，欢迎试用并提出建议。遇到漏读课程、截止时间异常或提醒问题，可以通过 [Issues](https://github.com/Dviodj/MoocReminder/issues) 反馈；也欢迎贡献代码，帮助完善微信、邮件提醒和 domooc 联动。
