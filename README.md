# Codex 限额恢复助手

项目名称：**codex-quota-resume**。Windows 本地 Codex 插件与悬浮条。

修复旧版仅读取会话日志导致的“到了重置时间，余量与时间不更新”。后台直接查询 Codex Desktop 的实时额度，不发起模型对话。额度耗尽后继续监测，服务端确认额度恢复后向**原任务 ID**发送一次继续指令。

## 行为

- 常规 30 秒查询，重置前 30 秒内每 5 秒以内查询，并调度到重置时刻。网络及服务端响应会增加实际延迟，不承诺零毫秒。
- 五小时与周额度一起检查；任何窗口仍满、响应缺失、数据过期或断网，均不触发续跑。
- 只自动恢复**安装以后最新一轮明确因 usage limit 失败**的本地 Codex 任务。
- 旧任务、其他未完成任务必须明确标记。任务有新轮次时旧标记失效。完成、手动中断、正在执行、等待输入的任务不会被自动误判为限额失败。
- 每个失败轮次只尝试发送一次；发送结果不确定时保留 `uncertain`，先检查原任务，不盲目重发。
- 保留模型、思考强度及原有权限。不会购买额度、兑换重置次数、换账号或绕过限额。
- 360×38 逻辑像素的磨砂玻璃悬浮条，高 DPI 绘制，文字保持完全不透明。鼠标离开后收为顶部细条；在系统托盘可选择常驻显示。
- 悬浮条完全点击穿透、不抢焦点；点击被遮住的内容直接落到下面的应用。刷新、暂停、启用、退出均放在系统托盘右键菜单。
- 自动刷新不依赖点击或对话：后台持续查询；倒计时每秒更新。重置后服务端暂未更新时显示“更新中”并每 5 秒重查，不伪造 100% 余量。

## 要求与限制

Windows 10/11、Python 3.11+、Node.js 18+、已登录并打开的 Codex Desktop。安装脚本将 PySide6 Essentials 放到本地专用目录；不修改全局 Python 依赖。原生磨砂效果取决于 Windows 的透明效果设置。
电脑休眠、关机或 Codex 关闭时无法续跑；恢复运行后重新检查。

桌面桥接使用本机捆绑 `codex-app-tools` 所采用的管道协议，已在 Codex Desktop `0.155.0-alpha.9.2` 上验证读取。它不是稳定公开接口，桌面升级可能需要更新适配器；连接失败时明确显示过期，不改用 `resume --last`。

监测当前桌面的 **30 个最近未置顶任务及全部置顶任务**。更多旧任务请先置顶。仅处理本机任务，不启动云端或远程主机任务。`idle` 本身不是“未完成”的证据，限额失败的 `systemError` 和 `notLoaded` 状态也会检查。筛选结果写入本地 `inspections`，便于定位漏检。

## 安装与启动

在项目目录打开 PowerShell：

```powershell
./scripts/install.ps1 -OwnerThreadId '<用于管理插件的真实 Codex 任务 ID>'
```

安装登录启动快捷方式并启动隐藏后台程序。管理任务 ID 用于桌面工具的调用上下文，仅保存在本机配置中。

只启动：`./scripts/start.ps1`；退出：`./scripts/stop.ps1`。
移除登录启动：`./scripts/uninstall-startup.ps1`。移除后保留发送记录，避免重装重复续跑。

如旧版本顶部条仍在运行，应先停止旧版，只保留新版本。

## 控制

```powershell
python control.py status
python control.py probe
python control.py pause
python control.py enable
python control.py refresh
python control.py mark --thread-id '<任务 ID>'
python control.py unmark --thread-id '<任务 ID>'
```

`probe` 仅查询，不发送。`mark` 表示用户明确授权该任务在额度可用时继续，可能立即开始。不能用“最近任务”代替明确 ID。

Codex 插件提供 `quota_resume_status` 与 `quota_resume_control` MCP 工具，以及 quota-resume 技能。安装插件后在新任务中使用，以加载新工具。守护程序独立于 MCP 运行，模型额度耗尽不会使计时器停止。

## 状态与隐私

本地状态：`%LOCALAPPDATA%/CodexQuotaResume`。包括额度缓存、开关、任务/轮次 ID 和发送结果；不会写入登录凭据、账号 ID、对话正文或工具输出。不读取 `auth.json`，不向其他监测服务器传输数据。

`dispatching` / `uncertain`：发送可能已经成功，请先检查原任务。程序保守地不自动重试该轮次。新一轮再次因限额失败仍可恢复。

源码仓库独立管理，按用户要求每五轮对话同步，提交说明包含项目名和提交时间。运行时配置与状态不入库。最新提交时间见 GitHub 提交记录和本地 `git log -1 --format=%cI`。

## 验证

```powershell
python -m unittest discover -v
node --test test_bridge.mjs
python -m compileall -q core.py monitor.py bridge.py control.py mcp_server.py overlay.py glass_overlay.py glass_native.py ui_model.py
python scripts/check_ui.py
```

自动测试覆盖恢复前后、错误状态任务、周额度阻塞、陈旧数据、手动中断、人工标记、重启防重发、未知回执、任务变化与真实本地管道分帧。原生窗口测试检查尺寸、DPI、磨砂配置与点击穿透。已通过真实本地管道向管理任务发送并成功收到标注为自测的消息；下一次自然额度重置的全流程仍需实际观察。详见 [故障修复记录](docs/incident-2026-09-22.md)。

## GitHub 调研与参考

- [roboticsdao/codex-usage-monitor](https://github.com/roboticsdao/codex-usage-monitor)：通过本地 app-server 查询实时额度。
- [omi-last-stand/codex-usage-monitor](https://github.com/omi-last-stand/codex-usage-monitor/blob/main/docs/event-commands.md)：额度真正恢复后触发事件命令。
- [Justin1491/codex-dashboard](https://github.com/Justin1491/codex-dashboard)：自动续跑 CLI 会话，但其 `--last` 方式不足以精确选择多个桌面任务。
- [OpenAI App Server 文档](https://learn.chatgpt.com/docs/app-server)：`account/rateLimits/read` 和任务接口。

- [CrossHair-Overlay](https://github.com/cappuccino8080/CrossHair-Overlay)：Windows 置顶窗口与点击穿透。
- [py-window-styles](https://github.com/Akascape/py-window-styles)：Windows 窗口材质配置。

以上仅参考机制，没有复制第三方项目代码。UI 使用 Qt 和 Windows Acrylic 重写。
