"""类库"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import html
import inspect
import logging
import os
from pathlib import Path
import re
import socket
import threading
import time
import traceback
from typing import TYPE_CHECKING, Callable, Coroutine, Dict, Optional, Set, Union

from colorama import Fore

from src.utils import Utils

if TYPE_CHECKING:
    from src.robot import Concerto


class HttpListener:
    """复用 server socket 的轻量 HTTP 请求监听器"""

    _instances: dict[tuple[str, int], HttpListener] = {}
    _instances_lock = threading.Lock()

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = int(port)
        self.closed = False
        self.accept_lock = threading.Lock()
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((host, self.port))
        self.server.listen()

    @classmethod
    def get(cls, host: str, port: int) -> HttpListener:
        """读取或创建指定地址的长生命周期监听器"""
        key = (host, int(port))
        with cls._instances_lock:
            listener = cls._instances.get(key)
            if listener is None or listener.closed:
                listener = cls(host, int(port))
                cls._instances[key] = listener
            return listener

    @classmethod
    def receive_once(
        cls,
        host: str,
        port: int,
        timeout: int = 5,
        accept_timeout: float | None = None,
    ) -> tuple[dict, str]:
        """通过指定地址的监听器接收一个 HTTP 请求"""
        return cls.get(host, int(port)).receive(timeout, accept_timeout)

    @classmethod
    def close(cls, host: str, port: int) -> None:
        """关闭并移除指定地址的监听器"""
        key = (host, int(port))
        with cls._instances_lock:
            listener = cls._instances.pop(key, None)
        if listener:
            listener._close()  # pylint: disable=protected-access

    @classmethod
    def close_all(cls) -> None:
        """关闭所有 HTTP 请求监听器"""
        with cls._instances_lock:
            listeners = list(cls._instances.values())
            cls._instances.clear()
        for listener in listeners:
            listener._close()  # pylint: disable=protected-access

    def receive(
        self,
        timeout: int = 5,
        accept_timeout: float | None = None,
    ) -> tuple[dict, str]:
        """接收并解析一个 HTTP 请求"""
        client = None
        with self.accept_lock:
            if self.closed:
                raise OSError(f"监听器已关闭: {self.host}:{self.port}")
            self.server.settimeout(accept_timeout)
            client, _ = self.server.accept()
        try:
            client.settimeout(timeout)
            headers, body = self._read_request(client)
            client.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Connection: close\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
            return headers, body.decode("utf-8")
        finally:
            if client:
                client.close()

    def _close(self) -> None:
        """关闭监听端口"""
        self.closed = True
        try:
            self.server.close()
        except OSError:
            pass

    def _read_request(self, client: socket.socket) -> tuple[dict, bytes]:
        """读取请求头与请求体"""
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = client.recv(1024)
            if not chunk:
                break
            response.extend(chunk)
        if b"\r\n\r\n" not in response:
            return {}, b""
        header_bytes, remaining = response.split(b"\r\n\r\n", 1)
        headers = self._parse_headers(header_bytes)
        content_length = int(headers.get("Content-Length", 0) or 0)
        transfer_encoding = headers.get("Transfer-Encoding", "").lower()
        if transfer_encoding == "chunked":
            return headers, self._read_chunked_body(client, remaining)
        body = bytearray(remaining)
        while len(body) < content_length:
            chunk = client.recv(1024)
            if not chunk:
                break
            body.extend(chunk)
        return headers, bytes(body)

    def _parse_headers(self, header_bytes: bytes) -> dict:
        """解析 HTTP 请求头"""
        lines = header_bytes.decode("iso-8859-1").splitlines()
        if not lines:
            return {}
        request_line = lines[0].split(" ", 2)
        if len(request_line) != 3:
            return {}
        method, path, version = request_line
        headers = {"Method": method, "Path": path, "HTTP-Version": version}
        for line in lines[1:]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = "-".join(part.capitalize() for part in key.strip().split("-"))
            headers[key] = value.strip()
        return headers

    def _read_chunked_body(
        self,
        client: socket.socket,
        remaining: bytes | bytearray,
    ) -> bytes:
        """读取 Transfer-Encoding: chunked 的请求体"""
        body = bytearray()
        buffer = bytearray(remaining)
        while True:
            while b"\r\n" not in buffer:
                chunk = client.recv(1024)
                if not chunk:
                    return bytes(body)
                buffer.extend(chunk)
            line, _, buffer = buffer.partition(b"\r\n")
            chunk_size = int(line.decode("ascii"), 16)
            if chunk_size == 0:
                while len(buffer) < 2:
                    chunk = client.recv(1024)
                    if not chunk:
                        return bytes(body)
                    buffer.extend(chunk)
                break
            while len(buffer) < chunk_size + 2:
                chunk = client.recv(1024)
                if not chunk:
                    return bytes(body)
                buffer.extend(chunk)
            body.extend(buffer[:chunk_size])
            buffer = buffer[chunk_size + 2 :]
        return bytes(body)


class MiniCron:
    """简单Crontab，支持同步和异步函数"""

    def __init__(
        self,
        expr: str,
        task: Union[Callable[[], None], Callable[[], Coroutine]],
        loop=None,
        name: str | None = None,
    ) -> None:
        """
        expr: crontab 表达式 (如 "0 8-12/1 * * *" 表示8点到12点每小时执行)
        task: 要执行的函数，无参数，可以是同步函数或异步函数
        name: 计划任务名称，用于日志展示；不传则使用 cron 表达式
        """
        self.expr: str = expr
        self.name = name
        self.task: Union[Callable[[], None], Callable[[], Coroutine]] = task
        try:
            self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_running_loop()
        except RuntimeError:
            self.loop = loop or asyncio.get_event_loop()
        self.cron_fields: Dict[str, Set[int]] = self.parse_cron(expr)
        _, _, day, _, weekday = expr.split()
        self.day_is_any = day == "*"
        self.weekday_is_any = weekday == "*"
        self.is_async = inspect.iscoroutinefunction(task)
        self.running = False

    @property
    def display_name(self) -> str:
        """返回用于日志展示的计划任务名称"""
        return self.name or self.expr

    @staticmethod
    def _normalize_weekday(value: int) -> int:
        """将 cron 星期字段中的 7 归一化为周日 0"""
        return 0 if value == 7 else value

    def parse_field(
        self,
        field: str,
        min_val: int,
        max_val: int,
        *,
        allow_sunday_7: bool = False,
    ) -> Set[int]:
        """解析单个字段，返回允许的整数集合"""
        if field == "*":
            return set(range(min_val, max_val + 1))
        values: Set[int] = set()
        field_max = 7 if allow_sunday_7 else max_val
        for part in field.split(","):
            if not part:
                raise ValueError(f"无效 cron 字段: {field}")
            if "/" in part:
                range_part, step_part = part.split("/", 1)
                step = int(step_part)
                if step <= 0:
                    raise ValueError(f"cron 步长必须大于 0: {part}")
            else:
                range_part = part
                step = 1
            if range_part == "*":
                start, end = min_val, max_val
            elif "-" in range_part:
                start, end = map(int, range_part.split("-", 1))
                if start > end:
                    raise ValueError(f"cron 范围起始值不能大于结束值: {part}")
            else:
                start = end = int(range_part)
            if start < min_val or end > field_max:
                raise ValueError(
                    f"cron 字段超出范围 {min_val}-{field_max}: {part}"
                )
            for value in range(start, end + 1, step):
                if allow_sunday_7:
                    values.add(self._normalize_weekday(value))
                else:
                    values.add(value)
        return values

    def parse_cron(self, expr: str) -> Dict[str, Set[int]]:
        """解析 cron 表达式，返回每个字段允许的整数集合"""
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(f"cron 表达式必须包含 5 段: {expr}")
        minute, hour, day, month, weekday = parts
        try:
            parsed = {
            "minute": self.parse_field(minute, 0, 59),
            "hour": self.parse_field(hour, 0, 23),
            "day": self.parse_field(day, 1, 31),
            "month": self.parse_field(month, 1, 12),
            "weekday": self.parse_field(weekday, 0, 6, allow_sunday_7=True),
        }
        except Exception as e:
            raise ValueError("cron 表达式解析失败！") from e
        return parsed

    def _match_day(self, current_time: datetime) -> bool:
        """按 cron 常见语义匹配日和星期字段"""
        day_match = current_time.day in self.cron_fields["day"]
        cron_weekday = (current_time.weekday() + 1) % 7
        weekday_match = cron_weekday in self.cron_fields["weekday"]
        if self.day_is_any:
            return weekday_match
        if self.weekday_is_any:
            return day_match
        return day_match or weekday_match

    def next_time(self, from_time: Optional[datetime] = None) -> datetime:
        """计算下一个匹配 cron 表达式的时间点"""
        if from_time is None:
            from_time = datetime.now().replace(second=0, microsecond=0) + timedelta(
                minutes=1
            )
        else:
            from_time = from_time.replace(second=0, microsecond=0) + timedelta(
                minutes=1
            )
        max_attempts = 100000  # 防止无限循环
        attempts = 0
        while attempts < max_attempts:
            attempts += 1
            if (
                from_time.minute in self.cron_fields["minute"]
                and from_time.hour in self.cron_fields["hour"]
                and from_time.month in self.cron_fields["month"]
                and self._match_day(from_time)
            ):
                return from_time
            from_time += timedelta(minutes=1)
        raise ValueError("无法找到下一个执行时间, 请检查cron表达式")

    async def execute_task(self) -> None:
        """执行任务，支持同步和异步函数"""
        if self.is_async:
            await self.task()
        else:
            result = await self.loop.run_in_executor(None, self.task)
            if asyncio.iscoroutine(result):
                await result

    async def run(self) -> None:
        """开始循环执行任务"""
        self.running = True
        next_run: datetime = self.next_time()
        while self.running:
            try:
                now: datetime = datetime.now()
                sleep_seconds = (next_run - now).total_seconds()
                if sleep_seconds > 0:
                    await asyncio.sleep(sleep_seconds)
                if not self.running:
                    break
                await self.execute_task()
            except asyncio.CancelledError:
                self.running = False
                raise
            except Exception:  # pylint: disable=broad-exception-caught
                logging.getLogger(__name__).exception(
                    "计划任务 [%s] 执行失败", self.display_name
                )
            next_run = self.next_time(datetime.now())

    def stop(self) -> None:
        """停止任务执行"""
        self.running = False


class Event:
    """基础事件结构"""

    def __init__(self, robot: Concerto, raw=None):
        """从 OneBot 原始上报数据构造统一事件对象"""
        # 机器人本类
        self.robot = robot
        # 原始数据结构
        self.raw = raw = raw or {}
        # 上报类型 message消息 notice系统提示 request请求
        self.post_type = raw.get("post_type", "")
        # 事件发生的时间戳
        self.time = raw.get("time", "")
        # 机器人自身QQ号
        self.self_id = str(raw.get("self_id", ""))
        # 消息类型 private私聊 group群聊
        self.msg_type = raw.get("message_type", "")
        # 通知类型 notify常用通知 essence群精华消息 group_upload群文件上传 group_admin群变动
        # group_decrease群成员减少 group_increase群成员增加 group_ban群禁言 friend_add好友添加
        # group_recall群消息撤回 friend_recall好友消息撤回 group_card群成员名片更新
        # offline_file离线文件 client_status客户端状态变更
        self.notice_type = raw.get("notice_type", "")
        # 消息子类型 friend好友 group群临时会话 group_self群聊 other其他 normal普通 anonymous匿名 notice系统提示
        self.sub_type = raw.get("sub_type", "")
        # 消息 ID
        self.msg_id = raw.get("message_id", "")
        # 原始消息内容
        self.msg = html.unescape(raw.get("message", ""))
        if "CQ:json" in self.msg:
            self.msg = re.sub(r"(\s)+", "", self.msg)
        # 消息内容，去除AT信息
        self.text = self.msg.replace(self.robot.at_info, "").strip()
        # 发送者ID
        self.sender_id = str(raw.get("sender_id", ""))
        # 发送者QQ号
        self.user_id = str(raw.get("user_id", ""))
        # 发送者名称
        self.user_name = raw.get("sender", {}).get("nickname", "")
        if self.user_name == "" and self.user_id.isdigit():
            self.user_name = Utils.get_user_name(robot, self.user_id)
        # 发送者昵称
        self.user_card = raw.get("sender", {}).get("card", "")
        # 群号
        self.group_id = str(raw.get("group_id", ""))
        if self.group_id == "0":
            self.group_id = ""
        # 群名
        self.group_name = Utils.get_group_name(robot, self.group_id)
        # 目标QQ号
        self.target_id = str(raw.get("target_id", ""))
        # 目标昵称
        self.target_name = (
            Utils.get_user_name(robot, self.target_id)
            if self.msg_type == "private"
            else Utils.get_group_name(robot, self.group_id)
        )
        # 操作者QQ号
        self.operator_id = str(raw.get("operator_id", ""))
        # 操作者昵称
        self.operator_name = Utils.get_user_name(robot, self.operator_id)
        self.operator_nick = raw.get("operator_nick", "")


class Module:
    """模块基类"""

    ID = None
    NAME = None
    HELP = None
    CONFIG = None
    GLOBAL_CONFIG = None
    CONV_CONFIG = None
    PERSISTENT = None

    HANDLE_MESSAGE = True
    HANDLE_MESSAGE_SENT = False
    HANDLE_NOTICE = False
    HANDLE_REQUEST = False
    HANDLE_EVENT = False

    def __init__(self, event: Event, auth: int = 0):
        """初始化模块上下文、配置并按前置条件激活处理器"""
        self.name = self.__class__.NAME
        self.handled = False
        self.event = event
        self.robot = event.robot
        self.auth = int(auth)
        self.owner_id = ""
        self.config_file = ""
        self.config = {}
        self.conv_config = {}
        self.data = {}
        self.background_tasks = {}
        self.background_crons = []
        self.background_threads = []
        self.init_config()
        if not self.premise():
            return
        self.activate()

    def premise(self):
        """模块执行的前置条件"""
        return True

    def activate(self):
        """执行类方法"""
        for attr_name in dir(self):
            if self.handled:
                return
            attr = getattr(self, attr_name)
            if callable(attr) and hasattr(attr, "_is_handler"):
                attr()

    def retry(
        self,
        func: Callable[..., object],
        *func_args,
        name: str = "",
        max_retries: int = 3,
        delay: int | float = 1,
        failed_ok: bool = True,
        **func_kwargs,
    ) -> object:
        """多次尝试执行函数，失败时打印日志并按需重试"""
        for attempt in range(1, max_retries + 1):
            try:
                return func(*func_args, **func_kwargs)
            except Exception:  # pylint: disable=broad-exception-caught
                func_name = name if name else func.__name__
                self.printf(f"第 {attempt} 次执行 {func_name} 失败: {Utils.get_error()}")
                if attempt == max_retries:
                    if failed_ok:
                        return None
                    raise
                self.printf(f"{delay} 秒后重试...")
                time.sleep(delay)

    def get_persist(self):
        """读取当前模块 ID 对应的长生命周期实例"""
        return self.robot.get_persist_mod(self.ID)

    def is_persisted(self) -> bool:
        """判断当前模块是否已经存在长生命周期实例"""
        return self.get_persist() is not None

    def auto_bootstrap(self) -> None:
        """PERSISTENT 模块创建完成后注册为持久实例"""
        if not getattr(type(self), "PERSISTENT", False):
            return
        persist = self.robot.get_persist_mod(self.ID)
        if persist is not None and persist is not self:
            return
        if persist is None and not self.robot.register_persist_mod(self):
            return
        self._register_exported_funcs()

    def schedule_background_task(
        self,
        coroutine: Coroutine,
        *,
        name: str | None = None,
    ):
        """在机器人事件循环中注册后台协程，并在卸载时统一取消"""
        task_name = name or "未命名后台协程"
        future = asyncio.run_coroutine_threadsafe(coroutine, self.robot.loop)
        self.background_tasks[future] = task_name
        self.printf(f"启动后台协程 [{Fore.MAGENTA}{task_name}{Fore.RESET}]")
        return future

    def schedule_background_thread(
        self,
        target: Callable,
        *args,
        name: str | None = None,
        daemon: bool = True,
        **kwargs,
    ) -> threading.Thread:
        """启动并跟踪模块后台线程，卸载时由基类尝试 join"""
        thread = threading.Thread(
            target=target,
            args=args,
            kwargs=kwargs,
            daemon=daemon,
            name=name,
        )
        self.background_threads.append(thread)
        self.printf(f"启动后台线程 [{Fore.MAGENTA}{thread.name}{Fore.RESET}]")
        thread.start()
        return thread

    def track_cron(self, cron: MiniCron) -> MiniCron:
        """记录 MiniCron，供自定义运行逻辑在卸载时统一停止"""
        if cron not in self.background_crons:
            self.background_crons.append(cron)
        return cron

    def add_cron(self, cron: MiniCron):
        """注册 MiniCron 并在卸载时统一停止"""
        self.track_cron(cron)
        return self.schedule_background_task(
            cron.run(),
            name=f"Cron ({cron.expr}) {cron.display_name}",
        )

    def _iter_exported_methods(self):
        """按 MRO 顺序返回通过 Utils.export_func 标记的方法"""
        seen = set()
        for cls in type(self).mro():
            for attr_name, raw_attr in cls.__dict__.items():
                if attr_name in seen:
                    continue
                seen.add(attr_name)
                if callable(raw_attr) and hasattr(raw_attr, "_is_exported_func"):
                    yield attr_name, raw_attr, getattr(self, attr_name)

    def _register_exported_funcs(self) -> None:
        """注册通过 Utils.export_func 标记的模块能力"""
        for attr_name, raw_attr, method in self._iter_exported_methods():
            func_name = getattr(raw_attr, "_exported_func_name", attr_name)
            self.robot.register_func(method, func_name)
            self.printf(f"新增全局可调用函数 [{Fore.MAGENTA}{func_name}{Fore.RESET}]")

    def stop_background(self) -> None:
        """停止当前模块记录的后台任务和定时任务"""
        for cron in self.background_crons:
            self.printf(f"停止计划任务 [{cron.display_name}]")
            cron.stop()
        for future, task_name in self.background_tasks.items():
            if not future.done():
                self.printf(f"停止后台协程 [{task_name}]")
                future.cancel()
        for thread in self.background_threads:
            if thread is not threading.current_thread() and thread.is_alive():
                self.printf(f"等待后台线程退出 [{thread.name}]")
                thread.join(timeout=2)
                if thread.is_alive():
                    self.warnf(f"后台线程未在超时时间内退出 [{thread.name}]")
                else:
                    self.printf(f"后台线程已退出 [{thread.name}]")
        self.background_crons.clear()
        self.background_tasks.clear()
        self.background_threads.clear()

    def unload(self) -> None:
        """默认卸载钩子：清理通过基类注册的后台任务"""
        self.stop_background()

    def au(self, max_level=3, min_level=0):
        """检查权限等级"""
        return min_level <= self.auth <= max_level

    def group_at(self):
        """仅群聊@消息触发"""
        return self.robot.at_info in self.event.msg

    def at_or_private(self):
        """群聊@消息以及私聊消息触发"""
        return self.event.group_id == "" or self.robot.at_info in self.event.msg

    def start_with_sign(self):
        """开头#触发"""
        return re.search(r"^#\S+", self.event.msg)

    def match(self, pattern: str):
        """消息规则匹配"""
        return re.search(pattern, self.event.text)

    def is_self_send(self):
        """判断是不是自己发送的数据"""
        return self.robot.self_id in [self.event.user_id, self.event.sender_id]

    def is_private(self):
        """是否私聊"""
        return self.event.group_id == ""

    def is_reply(self):
        """是否包含回复消息"""
        return self.match(r"\[CQ:reply,id=([^\]]+?)\]")

    def init_config(self):
        """初始化模块数据"""
        # 设定会话的owner_id
        if self.event.group_id:
            self.owner_id = f"g{self.event.group_id}"
        elif self.event.user_id:
            self.owner_id = f"u{self.event.user_id}"
        else:
            self.owner_id = f"u{self.robot.self_id}"
        # 读取指定会话的数据与配置文件
        self.data = self.robot.data.get(self.owner_id)
        if self.GLOBAL_CONFIG is None and self.CONV_CONFIG is None:
            return
        config_name = self.CONFIG
        if config_name is None:
            config_name = f"{str(self.ID).lower()}.json"
        self.config_file = os.path.join(self.robot.config.data_path, config_name)
        try:
            self.config = Utils.import_json(self.config_file)
            if not isinstance(self.config, dict):
                raise TypeError(f"配置文件根节点必须是对象，实际为 {type(self.config).__name__}")
        except Exception as e:
            self.config = {}
            raise TypeError(f"配置文件 {self.config_file} 解析发生错误!") from e
        global_default = self.GLOBAL_CONFIG or {}
        self.config = Utils.merge(global_default, self.config)
        if self.CONV_CONFIG is None:
            self.save_config()
            return
        if self.owner_id not in self.config:
            self.config[self.owner_id] = {}
        conv_default = self.CONV_CONFIG or {}
        self.config[self.owner_id] = Utils.merge(conv_default, self.config[self.owner_id])
        self.conv_config = self.config[self.owner_id]
        self.save_config()

    def save_config(self, config_content=None, owner_id=""):
        """保存模块配置"""
        if owner_id and config_content is not None:
            self.config[owner_id] = config_content
        elif config_content is not None:
            self.config = config_content
        try:
            if self.config == Utils.import_json(self.config_file):
                return
            Utils.save_json(self.config_file, self.config)
            persist = self.get_persist()
            if persist is not None and persist is not self:
                persist.config = self.config.copy()
        except Exception as e:
            raise TypeError(f"配置文件 {self.config_file} 保存失败!") from e

    def get_data_path(self, *paths: str) -> str:
        """获取配置文件夹"""
        config_path = self.CONFIG if self.CONFIG else str(self.ID).lower()
        path = os.path.join(self.robot.config.data_path, config_path, *paths)
        dir_path = path if os.path.splitext(path)[1] == "" else os.path.dirname(path)
        os.makedirs(dir_path, exist_ok=True)
        return Path(path).as_posix()

    def node(self, *args, **kwargs) -> dict:
        """
        生成一个转发节点
        user_id,nickname,content
        """
        if len(args) == 1 and isinstance(args[0], str):
            content = Utils.handle_placeholder(args[0], self.robot.placeholder_dict)
            return Utils.build_node(content, **kwargs)
        return Utils.build_node(*args, **kwargs)

    def reply(self, msg, reply=False, force=False):
        """快捷回复消息"""
        if self.robot.config.is_always_reply:
            reply = True
        result = Utils.reply_event(self.robot, self.event, msg, reply=reply, force=force)
        return result

    def reply_forward(self, nodes: list, source=None, summary=None):
        """快捷回复转发消息"""
        result = Utils.send_forward_msg(self.robot, nodes, self.event.group_id, self.event.user_id, source, summary)
        return result

    def get_reply(
        self,
    ) -> str | None:
        """读取可能存在的回复消息"""
        reply_match = self.is_reply()
        if not reply_match:
            return
        msg_id = reply_match.group(1)
        reply_msg = Utils.get_msg(self.robot, msg_id)
        if not Utils.status_ok(reply_msg):
            return
        msg = html.unescape(reply_msg["data"]["message"])
        msg = re.sub(r"[\\\r\n]+", "", msg)
        return msg

    def printf(self, msg, end="\n", console=True, flush=False, level="INFO"):
        """
        向控制台输出通知级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        if not flush:
            msg = f"{Fore.CYAN}[{self.ID}]{Fore.RESET} {msg}"
        self.robot.printf(msg=msg, end=end, console=console, flush=flush, level=level)

    def warnf(self, msg, end="\n", console=True, level="INFO"):
        """
        向控制台输出警告级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        self.robot.warnf(
            f"{Fore.CYAN}[{self.ID}]{Fore.YELLOW} {msg}",
            end=end,
            console=console,
            level=level,
        )

    def errorf(self, msg, end="\n", console=True, level="INFO"):
        """
        向控制台输出错误级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        self.robot.errorf(
            f"{Fore.CYAN}[{self.ID}]{Fore.RED} {msg}",
            end=end,
            console=console,
            level=level,
        )
