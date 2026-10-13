# MoocReminder v0.1

![MoocReminder 界面](docs/images/overview.png)

Windows 桌面版中国大学 MOOC 作业截止提醒工具。采用 macOS 风格界面，通过 Windows 原生 WebView2 登录并读取课程，无需复制浏览器 Cookie。

## 功能

- 读取 MOOC 和校内 SPOC 的作业、测验提交截止时间。
- 默认每 30 分钟检查，提前 3 天、1 天、3 小时弹窗提醒。
- 修改检查间隔、提醒时间和课程链接；支持本地完成标记与搜索。
- 登录配置按账号存储在本机；读取失败保留上次作业记录。
- 最小化后继续监测，关闭程序停止。关机或休眠期间不会提醒。

## 快速开始

需要 Windows、64 位 Python 3.11 或以上、.NET Desktop Runtime 8 或以上、Microsoft Edge WebView2 Runtime。

1. 下载 [v0.1 发布包](https://github.com/Dviodj/MoocReminder/releases/tag/v0.1)，解压完整文件夹。
2. 双击 **启动MoocReminder.bat**（也可用 **启动程序.bat**）。首次启动自动安装 Python 依赖并验证原生网页组件。
3. 打开 **提醒设置**，粘贴自己的课程主页链接，例如 `https://www.icourse163.org/home.htm?userId=你的数字账号#/home/course`。默认 `userId=0` 是占位值。
4. 点击 **连接慕课**，在独立的官网窗口登录。
5. 返回主窗口点击 **检查作业**，之后保持程序运行。

默认检查当前学期；需要历史学期时勾选“检查已结束学期”。官网未明确显示提交状态时显示“请核对”。互评截止时间不作为作业提交截止时间。

程序以源码方式运行，未提供自制 EXE。三个官方 WebView2 SDK DLL 已随源码附带，其许可证和声明见 `.native-webview/LICENSE.txt`、`NOTICE.txt`。

## 本地数据

设置、作业缓存、完成标记优先保存在 `%LOCALAPPDATA%/MoocDeadlineReminder`；无写权限时使用项目的 `data` 文件夹。登录配置保存在数据目录下的 `webview2-profiles/<账号>`。这些文件均不上传仓库，也不包含在发布包中。

启动失败时查看 `.launcher-state/startup.log`。换电脑后需重新登录。

## 开发与验证

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe webview_runtime.py --prepare
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`native_adapter_smoke.py` 验证真实原生网页及 Qt/STA 线程协作；`qt_login_click_check.py` 验证实际连接按钮。测试包含 Windows GUI 和 Edge 浏览器依赖，应在交互式 Windows 会话运行。

v0.1 基于已经实测的原生登录及 MOOC/SPOC 读取实现。DOM 提取规则取决于官网当前结构，登录失效或网站结构变化时会显示错误并保留缓存。
