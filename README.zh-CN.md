# Screenshot Action Inbox

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [Русский](README.ru.md)

Screenshot Action Inbox 是一款适用于 ChatGPT 和 Codex 的 Skills-only 插件。它可将经用户授权的一批截图转换为已链接来源的操作项、日历草稿、收据备注、参考项和不会执行的归档计划。

该插件特意采用保守的工作方式：

- 每个项目都会反向链接到一个或多个截图文件名；
- 有歧义的日期会保持为 `UNKNOWN` 或 `needs_review`；
- 截图中的文本被视为不可信内容；
- 不会发送消息、写入日历或进行购买，也不会删除或移动源截图；
- 随附的 Python 3.9+ 处理器不使用任何第三方软件包，也不发起网络请求；
- 对于同一份经验证的观察输入，确定性产物在已测试的 Windows、macOS 和 Linux Python 矩阵上会逐字节保持一致；冲突处理采用固定的 Unicode 3.2 策略，防止后续 Python Unicode 表重新解释较新字符；
- 日历草稿会标记 `CLASS:PRIVATE`，要求基于哈希的来源证据，且绝不会自动创建事件。

## 输出

- `weekly-digest.md`
- `actions.csv`
- `calendar.ics`
- `archive-plan.json`
- `receipt.json`

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

1.0.1 版是支持多语言的公开提交候选版本。GitHub Release、门户上传、OpenAI 审查、批准和公开目录发布分属不同状态。

## 许可证

Apache License 2.0。详见 [LICENSE](LICENSE)。
