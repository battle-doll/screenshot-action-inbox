# Screenshot Action Inbox

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [Русский](README.ru.md)

把零散截图转换为有来源、可审阅的下一步行动。Screenshot Action Inbox 面向保存会议截图、邀请函、收据、提醒和参考卡片，并希望快速看清重点、重复项及每条建议来源的用户。

## 使用方法

1. 打开 [ChatGPT 中的 Screenshot Action Inbox](https://chatgpt.com/plugins/plugins_6a7cbf30f0208191b29866d20a69743a)。
2. 提供经用户授权的截图批次、文件夹或 ZIP。
3. 请求有来源的行动项、日期、重复组、不确定性标记或仅供审阅的日历草稿。

## 试一试

- `把这些会议截图整理成任务清单，并在每项旁标出源文件名。`
- `合并重复行动，并标出需要确认的日期。`
- `Turn these screenshots into sourced actions and show what needs review.`

## 关键边界

- 每个项目都关联一个或多个源文件名；有歧义的事实保留为 `UNKNOWN` 或 `needs_review`。
- 截图文字是不可信内容。本插件不用于监控、身份推断、单图创意编辑、仅 OCR 转录、邮件分拣、代码影响分析或语音通知设置。
- 不发送消息、不创建真实日历事件、不购买、不删除截图，也不移动文件。日历与归档输出仅为草稿。
- 随附的 Python 3.9+ 处理器不使用第三方软件包或网络请求；同一份经验证输入会在已测试的 Windows、macOS 和 Linux 矩阵上生成逐字节相同的结果。

## 输出

- `weekly-digest.md`
- `actions.csv`
- `calendar.ics`
- `archive-plan.json`
- `receipt.json`

## 代码本体

可通过[交互式代码本体图](docs/code-ontology/index.html)探索仓库结构。这个自包含工作台支持搜索、有界的 2D 结构视图、可选的 3D 星座视图以及源码证据查看。GitHub 文件查看器不会运行 HTML，而只会显示源码，因此请下载该文件并在本地浏览器中打开。

该图由 [Code Ontology Companion](https://github.com/battle-doll/code-ontology-companion) 0.5.2 基于源码修订版 `b42d168b6d45213edb886b683ac5c5ec06942454` 生成（快照 `20260815T090018Z-49018a955a1c`），包含 940 个节点和 2,756 条关系，且没有解析警告。

图中保留了符号标识符、仓库相对路径、行范围和定性的静态分析证据；不包含源码正文、注释、本地绝对路径、逐文件源码指纹、凭据或模型输出。这些关系是用于代码导航的证据，不是运行时跟踪、安全性结论或因果关系证明。

## 本地开发

运行完整验证套件：

macOS/Linux：

```bash
python3 -X utf8 scripts/verify.py all
```

Windows：

```powershell
py -3 -X utf8 scripts/verify.py all
```

要构建可安全上传至门户的 Skills-only ZIP，请将 `all` 替换为 `build`：

macOS/Linux：

```bash
python3 -X utf8 scripts/verify.py build
```

Windows：

```powershell
py -3 -X utf8 scripts/verify.py build
```

插件源码位于 [`plugins/screenshot-action-inbox`](plugins/screenshot-action-inbox)。生成的发行包会写入 `dist/`。

## 隐私

本插件没有由发布者运营的服务器、连接器、账户、遥测或分析。宿主产品会根据其自身条款和保留控制处理用户提供的图像。确定性处理器接收的是结构化 JSON，而不是图像文件。详见 [PRIVACY.md](PRIVACY.md)。

## 状态

截至 2026-08-29，版本 1.0.2 在 OpenAI Platform 中为 **Published**，并会出现在公开目录的精确名称搜索结果中。可通过[直接目录 URL](https://chatgpt.com/plugins/plugins_6a7cbf30f0208191b29866d20a69743a)打开。这确认了发布和精确名称搜索可见性；尚未测量自动 selector 调用或更广泛查询的 routing 成功率。

本仓库中的版本 1.0.2 是该已发布更新的 source version。

## 许可证

Apache License 2.0。详见 [LICENSE](LICENSE)。
