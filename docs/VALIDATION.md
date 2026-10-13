# v0.1 验证

2026-10-13，Windows 交互式桌面环境。

- 重命名后的完整自动测试：61 项通过。
- 实际桌面启动报告：native_window=true、controls_ready=true、name=MoocReminder、version=v0.1。
- 实际桌面截图检查：名称和页脚版本显示正确，主窗口布局完整。README 截图使用未登录、无个人数据的空状态。
- 原生网页适配器既有实测覆盖 HTML 渲染、JS 执行、弹出页面、初始化超时清理、重复连接及退出；对应报告见 superpowers/plans/task-2-native-smoke.json。
- 该版本继承了已成功实测的真实 MOOC/SPOC 登录与读取实现。发布仓库和源码包不包含个人账号、浏览器配置、作业缓存或诊断日志。
- 仓库默认账号 userId=0 为占位值，默认补充课程列表为空；首次使用须填写自己的课程主页。
