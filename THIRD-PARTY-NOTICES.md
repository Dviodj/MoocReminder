# 第三方组件声明（THIRD-PARTY NOTICES）

本项目（MoocReminder 源码）采用 [MIT 许可证](LICENSE)。本文件说明随**便携发布包**
（`MoocReminder-*-offline-x64.zip`）一起分发的第三方组件及其许可证。

## 随包分发的组件

| 组件 | 版本 | 许可证 | 正文位置 |
| --- | --- | --- | --- |
| CPython（内置解释器） | 3.13.9 | PSF License Agreement | `.build/runtime-bundle/python/LICENSE.txt` |
| PySide6-Essentials | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | `licenses/LGPL-3.0.txt`、`licenses/GPL-3.0.txt` |
| PySide6-Addons | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | 同上 |
| PySide6-WebEngine | 6.12.0.140 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | 同上 |
| shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | 同上 |

各组件版本、wheel 文件名与 SHA-256，以及 CPython 压缩包的 SHA-256，记录在
`.build/runtime-bundle/bundle.json`，可据此复核来源。

## 关于 Qt / PySide6 的说明

- PySide6 与 shiboken6 以**未经修改的官方 wheel**形式随包分发，未做改动或再链接。
- 二者为三重许可，本项目按 **LGPL-3.0** 使用。
- 官方 wheel 的 `licenses/` 目录只提供 `LicenseRef-Qt-Commercial.txt`，不含 LGPL 正文，
  因此本仓库在 `licenses/` 下额外提供 GNU 官方发布的正文（来源与校验值见 `licenses/README.md`）。
- 打包时只删除了未被使用的 Python 绑定（`.pyd`/`.pyi`），**没有删除任何 Qt 库**：
  Qt 的库跨 wheel 分布，`QtWebEngineCore` 依赖只随 Addons 分发的 DLL。

## 已不再随包分发的组件

早期版本通过 .NET 与 Microsoft Edge WebView2 渲染官网页面，因此曾随包分发 pythonnet、
clr_loader、cffi、pycparser 与官方 WebView2 SDK DLL。网页层现改用 QtWebEngine，
上述组件与依赖它们的 .NET Desktop Runtime、WebView2 Runtime 都已不再需要。

## 源码包的差异

源码包（`MoocReminder-*-source.zip`）**不包含**内置 Python 与 Qt 组件，只含本项目源码。
它需要机器上已有 64 位 Python 3.11–3.15，并由使用者自行执行 `pip install -r requirements.txt`
安装上表中的 Python 组件，组件自带的许可证随其 `.dist-info` 一同安装到本机。
