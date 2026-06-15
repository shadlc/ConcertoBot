# 第三方 Module 开发教程

本文面向希望在 `modules/` 中编写第三方功能的开发者。ConcertoBot 的模块系统非常直接：每个模块是一个继承 `Module` 的 Python 类，框架在收到事件时实例化模块，并按装饰器条件执行对应方法。

## 1. 模块加载规则

启动时，机器人会递归扫描 `modules/` 下的 `.py` 文件，并按文件名排序加载。推荐使用三位数字前缀控制优先级：

```text
modules/
├── 100message.py
├── 500picture.py
├── private/
│   └── 490local_secret.py
└── 950my_module.py
```

加载器会寻找文件中带有 `ID` 和 `NAME` 的类。一个文件可以包含多个模块类，但通常建议一个文件只放一个模块，方便热重载和定位问题。

三位数字前缀是项目插件约定，便于控制加载顺序，例如 `404rednote.py`、`900webhook.py`。

`modules/private/` 是约定的本地私有模块目录。当前加载器会递归扫描 `modules/`，所以 `modules/private/*.py` 会和公开模块一样自动加载；它只是默认被 `.gitignore` 的 `private/` 规则忽略，不会被提交到仓库。换句话说，`private` 表示“私有但启用”，不是“私有且禁用”。

使用私有模块时请注意：

- 私有模块仍需要唯一的 `ID`，不能和公开模块重复。
- 加载顺序按文件名 basename 排序，`modules/private/490local_secret.py` 会按 `490local_secret.py` 参与整体排序。
- 不要让私有模块文件名和公开模块文件名完全相同，否则动态导入时可能覆盖同名 `sys.modules` 条目。
- 如果私有模块包含密钥，优先读取环境变量或 `data/` 下未提交的配置文件，不要把密钥硬编码在模块中。

运行中可在终端使用：

```text
load 模块ID
reload 模块ID
unload 模块ID
enable 模块ID
disable 模块ID
```

`disable` 会把模块 ID 写入 `data/config.json` 的 `disabled` 列表，之后启动时也不会加载。

## 2. 最小模块

新建 `modules/950hello.py`：

```python
from src.base import Module
from src.utils import Utils


class Hello(Module):
    ID = "Hello"
    NAME = "问候模块"
    HELP = {
        "hello": "私聊发送 hello，或群聊 @机器人 hello"
    }

    @Utils.handler(lambda self: self.at_or_private() and self.match(r"^hello$"))
    def hello(self):
        self.reply("Hello from ConcertoBot!", reply=True)
```

重启机器人，或在终端输入：

```text
load Hello
```

如果已经加载过，修改后输入：

```text
reload Hello
```

## 3. Module 类字段

常用字段如下：

| 字段 | 作用 |
| --- | --- |
| `ID` | 模块唯一 ID，热加载、禁用、配置文件名都会用到 |
| `NAME` | 模块显示名 |
| `HELP` | 帮助信息，通常写成字典 |
| `CONFIG` | 自定义配置文件名；不填则使用 `data/<id小写>.json` |
| `GLOBAL_CONFIG` | 全局默认配置 |
| `CONV_CONFIG` | 每个群/用户的默认配置 |
| `AUTO_INIT` | 启动加载后是否立刻初始化一次 |
| `HANDLE_MESSAGE` | 是否处理普通消息，默认 `True` |
| `HANDLE_MESSAGE_SENT` | 是否处理机器人自己发出的消息 |
| `HANDLE_NOTICE` | 是否处理通知事件 |
| `HANDLE_REQUEST` | 是否处理请求事件 |
| `HANDLE_EVENT` | 是否处理元事件 |

示例：

```python
class Counter(Module):
    ID = "Counter"
    NAME = "计数模块"
    GLOBAL_CONFIG = {"reply_prefix": "计数结果："}
    CONV_CONFIG = {"enable": True, "count": 0}
```

`GLOBAL_CONFIG` 会合并到模块配置顶层，`CONV_CONFIG` 会按会话写入 `g群号` 或 `u用户QQ号` 分支。

## 4. 事件和常用上下文

模块实例中最常用的对象：

| 属性 | 说明 |
| --- | --- |
| `self.event` | 当前事件，类型为 `Event` |
| `self.robot` | 当前机器人运行时 |
| `self.auth` | 当前权限等级 |
| `self.config` | 模块全局配置 |
| `self.conv_config` | 当前会话配置 |
| `self.data` | 当前会话的短期消息/通知缓存 |
| `self.owner_id` | 当前会话 ID，例如 `g123456` 或 `u123456` |

常用 `Event` 字段：

| 字段 | 说明 |
| --- | --- |
| `event.post_type` | `message`、`notice`、`request`、`meta_event` 等 |
| `event.msg_type` | `private` 或 `group` |
| `event.notice_type` | 通知类型 |
| `event.sub_type` | 子类型 |
| `event.msg` | 原始消息，保留 CQ 码 |
| `event.text` | 去除机器人 @ 后的文本 |
| `event.user_id` | 发送者 QQ |
| `event.user_name` | 发送者昵称 |
| `event.group_id` | 群号，私聊为空字符串 |
| `event.group_name` | 群名 |
| `event.msg_id` | 消息 ID |
| `event.raw` | OneBot 原始上报数据 |

## 5. handler 和 listener

模块方法需要使用装饰器注册触发条件：

```python
@Utils.handler(lambda self: self.at_or_private() and self.match(r"^ping$"))
def ping(self):
    self.reply("pong")
```

`Utils.handler` 和 `Utils.listener` 的区别：

| 装饰器 | 行为 |
| --- | --- |
| `Utils.handler(condition)` | 条件满足后执行，并把 `self.handled` 设为 `True`，阻止后续模块继续竞争处理同一事件 |
| `Utils.listener(condition)` | 条件满足后执行，但不阻断后续模块 |

建议：

- 明确响应用户命令时使用 `handler`。
- 记录日志、统计、旁路缓存、通知广播时使用 `listener`。
- 条件函数必须尽量轻量，不要在 lambda 中做网络请求或耗时计算。

## 6. 常用触发条件

`Module` 已提供一些便捷判断：

```python
self.group_at()       # 群聊中 @ 机器人
self.at_or_private()  # 私聊，或群聊中 @ 机器人
self.start_with_sign()# 以 # 开头
self.match(pattern)   # 对 event.text 做正则匹配
self.is_private()     # 当前事件是否为私聊
self.is_reply()       # 当前消息是否包含 CQ reply
self.is_self_send()   # 是否是机器人自己发出的事件
self.au(max_level=3, min_level=0)  # 权限判断
```

权限等级约定：

| 等级 | 来源 |
| --- | --- |
| `1` | `admin_list` 中的管理员 |
| `2` | 已对接群 `rev_group` 或好友私聊 |
| `3` | 普通上下文 |

数字越小权限越高。需要管理员才能触发时可写：

```python
@Utils.handler(lambda self: self.at_or_private() and self.au(1) and self.match(r"^重载缓存$"))
def reload_cache(self):
    self.reply("缓存已重载")
```

## 7. 回复和调用 API

优先使用 `Module` 和 `Utils` 的快捷方法：

```python
self.reply("普通回复")
self.reply("引用回复", reply=True)
self.reply_forward([self.node("第一条"), self.node("第二条")])

Utils.send_msg(self.robot, "group", self.event.group_id, "群消息")
Utils.send_msg(self.robot, "private", self.event.user_id, "私聊消息")
Utils.poke(self.robot, self.event.user_id, self.event.group_id)
```

需要读取被回复消息时：

```python
@Utils.handler(lambda self: self.at_or_private() and self.is_reply())
def read_reply(self):
    msg = self.get_reply()
    if msg:
        self.reply(f"你回复的是：{msg}")
```

如果没有现成封装，可以使用底层 API：

```python
from src import api

result = api.get(self.robot, "/get_version_info")
```

## 8. 配置和数据文件

声明默认配置：

```python
class Greeter(Module):
    ID = "Greeter"
    NAME = "问候模块"
    GLOBAL_CONFIG = {"default_text": "你好"}
    CONV_CONFIG = {"enable": True, "text": None}

    @Utils.handler(lambda self: self.at_or_private() and self.match(r"^问候$"))
    def greet(self):
        if not self.conv_config["enable"]:
            return
        text = self.conv_config["text"] or self.config["default_text"]
        self.reply(text)
```

框架会自动生成并维护：

```text
data/greeter.json
```

保存修改：

```python
self.conv_config["enable"] = False
self.save_config(self.conv_config, self.owner_id)
```

写入模块专属文件：

```python
path = self.get_data_path("cache", "items.json")
```

这会自动创建目录，例如：

```text
data/greeter/cache/items.json
```

## 9. 长生命周期模块

如果模块需要启动时注册能力、启动后台线程、开启定时任务或维护连接，可使用 `AUTO_INIT = True`。框架会在加载模块时创建一个持久实例，并自动注册导出函数。后续事件仍会创建普通模块实例，持久实例只负责后台任务和导出能力。

```python
import asyncio


class MyService(Module):
    ID = "MyService"
    NAME = "后台服务模块"
    AUTO_INIT = True

    def __init__(self, event, auth=0):
        super().__init__(event, auth)
        if self.is_persisted():
            return
        self.running = True
        self.schedule_background_task(self.async_worker())
        self.schedule_background_thread(self.worker)

    def premise(self):
        return False

    async def async_worker(self):
        while self.running:
            self.printf("后台协程仍在运行")
            await asyncio.sleep(60)

    def worker(self):
        while self.running:
            ...

    def unload(self):
        self.printf("模块正在卸载，清理资源")
        self.running = False
        super().unload()
```

如果需要主动读取持久实例，可使用 `self.get_persist()`。如果只是判断当前模块是否已经存在持久实例，直接用 `self.is_persisted()` 更清楚。需要“一次性启动”的代码可以放在 `__init__()` 里，并在 `super().__init__()` 之后用 `if self.is_persisted(): return` 提前返回。

卸载时，框架只会调用持久实例上的 `unload()`。如果模块需要清理线程、连接、浏览器实例或外部资源，请重写 `unload()`，并在方法内调用 `super().unload()` 或 `self.stop_background()`。默认清理会取消通过 `schedule_background_task()` 注册的协程、停止通过 `add_cron()` / `track_cron()` 跟踪的 `MiniCron`，并尝试等待通过 `schedule_background_thread()` 注册的线程退出。

## 10. 定时任务

`MiniCron` 支持五段 cron 表达式：

```python
from src.base import MiniCron, Module


class Clock(Module):
    ID = "Clock"
    NAME = "整点提醒"
    AUTO_INIT = True

    def premise(self):
        return False

    def __init__(self, event, auth=0):
        super().__init__(event, auth)
        if self.is_persisted():
            return
        cron = MiniCron("0 * * * *", self.tick, loop=self.robot.loop, name="整点提醒")
        self.add_cron(cron)

    def tick(self):
        self.printf("整点任务触发")
```

通过 `self.add_cron(cron)` 注册的计划任务会自动启动，并在默认 `unload()` 中停止。长生命周期后台协程可在 `__init__()` 中用 `self.schedule_background_task(coro)` 启动；后台线程可用 `self.schedule_background_thread(target, *args, **kwargs)` 启动。`AUTO_INIT` 模块会由基类自动持久化。

如果模块需要自行控制 `cron.run()` 的重试、异常处理或运行时机，不要直接访问 `background_crons`，请至少调用 `self.track_cron(cron)`，确保卸载时框架会执行 `cron.stop()`。如果模块在运行时动态创建内部协程，请用 `self.schedule_background_task(coro)` 纳入卸载清理；动态创建线程请用 `self.schedule_background_thread(target, *args, **kwargs)` 纳入卸载清理。如果模块自己创建线程、socket 或外部连接，仍需要重写 `unload()` 做额外清理，并调用 `super().unload()` 或 `self.stop_background()`。

## 11. HTTP 监听

需要在模块内监听外部 HTTP 回调时，使用 `HttpListener`。它会按 `(host, port)` 复用同一个 server socket，避免每次请求都重新绑定端口：

```python
import json
import socket

from src.base import HttpListener, Module


class WebhookDemo(Module):
    ID = "WebhookDemo"
    NAME = "HTTP 回调示例"
    AUTO_INIT = True

    GLOBAL_CONFIG = {
        "host": "127.0.0.1",
        "port": 3109,
    }

    def __init__(self, event, auth=0):
        super().__init__(event, auth)
        if self.is_persisted():
            return
        self.running = True
        self.schedule_background_thread(self.listen_http)

    def premise(self):
        return False

    def listen_http(self):
        while self.running:
            try:
                header, body = HttpListener.receive_once(
                    self.config["host"],
                    self.config["port"],
                    accept_timeout=1,
                )
            except socket.timeout:
                continue

            if "application/json" not in header.get("Content-Type", ""):
                continue
            data = json.loads(body)
            self.printf(f"收到回调: {data}")

    def unload(self):
        self.running = False
        HttpListener.close(self.config["host"], self.config["port"])
        super().unload()
```

常用方法：

- `HttpListener.receive_once(host, port, timeout=5, accept_timeout=None)`：接收一个 HTTP 请求，返回 `(headers, body)`。
- `HttpListener.close(host, port)`：关闭指定监听端口，模块 `unload()` 时应调用。
- `HttpListener.close_all()`：关闭所有监听器，通常由机器人停止或重启流程调用。

`body` 已经是解码后的请求体。`Transfer-Encoding: chunked` 会在 `HttpListener` 内部完成分块读取和拼接，模块里直接 `json.loads(body)` 即可，不需要再手动拆 chunk。

## 12. 向其他模块暴露能力

可以用 `@Utils.export_func` 标记要暴露的能力。对 `AUTO_INIT = True` 的模块，基类会在持久实例初始化完成后自动注册到 `robot.func`：

```python
from src.base import Module
from src.utils import Utils


class Tools(Module):
    ID = "Tools"
    NAME = "工具能力"
    AUTO_INIT = True

    def premise(self):
        return False

    @Utils.export_func
    def echo(self, text: str) -> str:
        return text
```

其他模块：

```python
echo = self.robot.func.get("echo")
if echo:
    self.reply(echo("hello"))
```

## 13. 依赖管理

模块加载前会扫描 import。如果缺少非可选依赖，模块会跳过加载并在日志中提示缺失包名。

建议：

- 标准库和项目内依赖正常 import。
- 大型或可选依赖放在 `try` 块中，避免没有安装时阻塞整个模块加载。
- 在模块文档或注释中写清楚需要安装的包。

示例：

```python
try:
    import rich
except ModuleNotFoundError:
    rich = None
```

## 14. 调试建议

- 使用终端 `debug` 指令开启调试日志。
- 使用 `self.printf()`、`self.warnf()`、`self.errorf()` 输出带模块 ID 的日志。
- 使用 `test error` 可验证错误反馈链路。
- 修改模块后优先使用 `reload 模块ID`，确认无误后再重启。
- 模块异常会被捕获并输出简化 traceback，但语法错误会导致模块文件无法加载。

## 15. 常见问题

**模块没有触发。**

检查 `HANDLE_*` 是否开启、正则是否匹配 `event.text` 而不是 `event.msg`、群聊是否需要 @、当前权限是否满足 `self.au()`。

**模块触发后其他模块不执行。**

你使用了 `Utils.handler`。如果只是旁路监听，请改用 `Utils.listener`。

**配置修改没有保存。**

直接修改字典后需要调用 `save_config()`。如果修改的是当前会话配置，通常使用 `self.save_config(self.conv_config, self.owner_id)`。

**热卸载后后台任务还在运行。**

长生命周期模块需要实现 `unload()`，并主动停止连接、浏览器实例、socket，或未通过 `schedule_background_task()`、`schedule_background_thread()`、`add_cron()`、`track_cron()` 注册的任务。
