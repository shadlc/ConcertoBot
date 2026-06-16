# ConcertoBot

ConcertoBot 是一个面向个人使用和二次开发的 QQ 机器人框架。它通过 HTTP 对接 OneBot 兼容实现，内置终端指令、模块加载、权限分级、会话配置、计划任务和常用 QQ 操作封装，适合把零散脚本逐步整理成可维护的机器人模块。

> 当前项目主要按 NapCat / OneBot HTTP 场景设计，默认使用正向 HTTP API 调用和反向 HTTP 上报。

## 特性

- **模块化扩展**：`modules/` 下的 Python 文件会按文件名排序加载，支持加载、卸载、重载、启用和禁用模块。
- **轻量终端**：不依赖复杂 TUI，直接在终端显示日志、收发消息、调试信息和后台指令。
- **会话配置**：模块可声明全局配置和每个群/用户独立配置，自动落盘到 `data/`。
- **计划任务**：内置 `MiniCron`，可在模块中注册 cron 风格的定时任务。
- **LLM 能力复用**：`LLM` 模块可向其他模块注册聊天、语音转文字、文字转语音能力。
- **图片终端预览**：可将 CQ 图片渲染为灰度、盲文或 ANSI 彩色字符画。

## 项目结构

```text
.
├── main.py                 # 程序入口
├── src/
│   ├── robot.py            # 机器人运行时、模块加载、事件分发
│   ├── base.py             # 基类
│   ├── command.py          # 终端指令
│   ├── config.py           # 运行时配置与校验
│   ├── api.py              # OneBot HTTP API 原始封装
│   └── utils.py            # 常用工具、装饰器、快捷 API
├── modules/                # 内置模块与第三方模块放置目录
│   └── private/            # 本地私有模块目录，默认被 .gitignore 忽略
├── docs/
│   └── module-development.md
├── data/                   # 运行时配置与模块数据
└── logs/                   # 运行日志
```

## 快速开始

### 1. 准备环境

- Python 3.12
- uv
- 一个 OneBot 兼容实现，例如 NapCat

```bash
uv sync
```

如果本机还没有 Python 3.12，也可以先让 uv 安装：

```bash
uv python install 3.12
uv sync
```

### 2. 配置 OneBot HTTP

首次启动会自动生成 `data/config.json`。核心默认值如下：

```json
{
  "host": "127.0.0.1",
  "port": 3002,
  "api_base": "http://127.0.0.1:3000/",
  "rev_group": [],
  "admin_list": []
}
```

在 OneBot 实现中通常需要：

- 正向 HTTP API 地址与 `api_base` 对齐，例如 `http://127.0.0.1:3000/`。
- 反向 HTTP POST 上报地址指向 ConcertoBot，例如 `http://127.0.0.1:3002/`。
- 将需要完整响应的群号加入 `rev_group`，将管理员 QQ 加入 `admin_list`。

### 3. 启动

```bash
uv run python main.py
```

也可以直接运行：

```bash
start.bat
```

Linux / macOS 可使用：

```bash
./start.sh
```

启动后在终端输入 `help` 查看可用后台指令。

## 主要内置模块概览

| 模块 ID | 文件 | 说明 |
| --- | --- | --- |
| `Notice` | `000notice.py` | 基础通知处理 |
| `LLM` | `010llm.py` | LLM 能力注册 |
| `Message` | `100message.py` | 基础消息和帮助 |
| `Chat` | `110chat.py` | 聊天记录、统计、词云等 |
| `Group` | `200group.py` | 群组请求和群成员事件 |
| `Bilibili` | `401bilibili.py` | B站动态和解析 |
| `Ytdlp` | `402ytdlp.py` | 视频下载 |
| `Picture` | `500picture.py` | 图片处理 |
| `Webhook` | `900webhook.py` | 外部请求处理 |
| `Repeater` | `910repeater.py` | 复读机 |
| `MaiSaka` | `998maisaka.py` | 麦麦适配器 |

部分模块依赖额外第三方库。若缺少依赖，加载时会在日志中提示缺失模块名，可按需安装。

## 编写第三方模块

第三方模块只需要放入 `modules/`，继承 `src.base.Module`，并使用 `Utils.handler` 或 `Utils.listener` 声明触发条件。私有模块可放在 `modules/private/`；该目录默认不提交到 Git，但仍会被递归扫描并自动加载。完整教程见 [docs/module-development.md](docs/module-development.md)。

最小示例：

```python
from src.base import Module
from src.utils import Utils


class Hello(Module):
    ID = "Hello"
    NAME = "问候模块"
    HELP = {"hello": "发送 hello，机器人回复一句话"}

    @Utils.handler(lambda self: self.at_or_private() and self.match(r"^hello$"))
    def hello(self):
        self.reply("Hello from ConcertoBot!", reply=True)
```

保存为 `modules/950hello.py` 后，启动时会自动加载；运行中可用 `load Hello`、`reload Hello`、`disable Hello` 等指令管理。

## 截图

<p align="center">
  <img src="example/1.jpg" width="500">
  <img src="example/2.jpg" width="500">
  <img src="example/3.jpg" width="500">
  <img src="example/4.jpg" width="500">
</p>

## 参考

- [OneBot](https://onebot.dev/)
- [NapCat API](https://napcat.apifox.cn/)
- [MaiSaka](https://github.com/Mai-with-u/MaiBot)

## 许可证

本项目使用 [GPL-3.0](LICENSE) 许可证。
