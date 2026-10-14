# 许可证正文来源

本目录存放随附第三方组件的许可证**正文**。它们按项目 `package_source.py` 的规则进入
离线发布包，覆盖包内的 PySide6 与 shiboken6。其余组件的许可证正文由各自的
`.dist-info/licenses/` 目录提供，无需在此重复。

| 文件 | 对应组件 | 来源 | 大小 | SHA-256 |
| --- | --- | --- | --- | --- |
| `LGPL-3.0.txt` | PySide6、shiboken6（Qt for Python） | https://www.gnu.org/licenses/lgpl-3.0.txt | 7652 字节 | `e3a994d82e644b03a792a930f574002658412f62407f5fee083f2555c5f23118` |
| `GPL-3.0.txt` | 同上（LGPL-3.0 引用的备选条款） | https://www.gnu.org/licenses/gpl-3.0.txt | 35149 字节 | `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986` |

## 为什么需要单独存放

PySide6 与 shiboken6 的官方 wheel 在 `licenses/` 下**只提供 `LicenseRef-Qt-Commercial.txt`**，
不包含 LGPL-3.0 正文，而两者的 `METADATA` 声明的许可为：

```
License: LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only
```

LGPL 要求分发时随附许可证正文，因此本项目在离线包内额外提供上述文本。两个文件均为
GNU 官方发布的原文，未经改动。

## 复核方式

在任意机器上重新下载并比对校验值即可确认内容一致：

```powershell
Invoke-WebRequest https://www.gnu.org/licenses/lgpl-3.0.txt -OutFile LGPL-3.0.txt
(Get-FileHash LGPL-3.0.txt -Algorithm SHA256).Hash
```
