"""类库"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import html
import os
from pathlib import Path
import re
import traceback
from typing import TYPE_CHECKING, Callable, Coroutine, Dict, Optional, Set, Union

from colorama import Fore

from src.utils import Utils

if TYPE_CHECKING:
    from src.robot import Concerto


class MiniCron:
    """简单Crontab，支持同步和异步函数"""

    def __init__(
        self,
        expr: str,
        task: Union[Callable[[], None], Callable[[], Coroutine]],
        loop=None,
    ) -> None:
        """
        expr: crontab 表达式 (如 "0 8-12/1 * * *" 表示8点到12点每小时执行)
        task: 要执行的函数，无参数，可以是同步函数或异步函数
        """
        self.expr: str = expr
        self.task: Union[Callable[[], None], Callable[[], Coroutine]] = task
        self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_event_loop()
        self.cron_fields: Dict[str, Set[int]] = self.parse_cron(expr)
        self.is_async = asyncio.iscoroutinefunction(task)
        self.running = False

    def parse_field(self, field: str, min_val: int, max_val: int) -> Set[int]:
        """解析单个字段，返回允许的整数集合"""
        if field == "*":
            return set(range(min_val, max_val + 1))
        values: Set[int] = set()
        # 处理步长表达式 (如 8-12/1)
        if "/" in field:
            range_part, step_part = field.split("/", 1)
            step = int(step_part)
            if range_part == "*":
                # */n 格式
                values.update(range(min_val, max_val + 1, step))
            elif "-" in range_part:
                # start-end/step 格式 (如 8-12/1)
                start_str, end_str = range_part.split("-")
                start = int(start_str)
                end = int(end_str)
                values.update(range(start, end + 1, step))
            else:
                # 单个值/step 格式
                base = int(range_part)
                values.update(range(base, max_val + 1, step))
        else:
            # 没有步长的普通处理
            for part in field.split(","):
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    values.update(range(start, end + 1))
                else:
                    values.add(int(part))
        return values

    def parse_cron(self, expr: str) -> Dict[str, Set[int]]:
        """解析 cron 表达式，返回每个字段允许的整数集合"""
        minute, hour, day, month, weekday = expr.split()
        return {
            "minute": self.parse_field(minute, 0, 59),
            "hour": self.parse_field(hour, 0, 23),
            "day": self.parse_field(day, 1, 31),
            "month": self.parse_field(month, 1, 12),
            "weekday": self.parse_field(weekday, 0, 6),  # 0=周日 … 6=周六
        }

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
                and from_time.day in self.cron_fields["day"]
                and from_time.month in self.cron_fields["month"]
                and from_time.weekday() in self.cron_fields["weekday"]
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
            now: datetime = datetime.now()
            sleep_seconds = (next_run - now).total_seconds()
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)
            await self.execute_task()
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
    AUTO_INIT = None

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
        self.init_config()
        if not self.premise():
            return
        self.activate()

    def premise(self):
        """前置条件"""
        return True

    def activate(self):
        """执行类方法"""
        for attr_name in dir(self):
            if self.handled:
                return
            attr = getattr(self, attr_name)
            if callable(attr) and hasattr(attr, "_is_handler"):
                attr()

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
        except Exception:  # pylint: disable=broad-exception-caught
            self.config = {}
            self.errorf(
                f"配置文件 {self.config_file} 解析发生错误!\n{traceback.format_exc()}"
            )
        self.GLOBAL_CONFIG = self.GLOBAL_CONFIG or {}  # pylint: disable=invalid-name
        self.config = Utils.merge(self.GLOBAL_CONFIG, self.config)
        if self.CONV_CONFIG is None:
            self.save_config()
            return
        if self.owner_id not in self.config:
            self.config[self.owner_id] = {}
        self.CONV_CONFIG = self.CONV_CONFIG or {}  # pylint: disable=invalid-name
        self.config[self.owner_id] = Utils.merge(self.CONV_CONFIG, self.config[self.owner_id])
        self.conv_config = self.config[self.owner_id]
        self.save_config()

    def save_config(self, config_content=None, owner_id=""):
        """保存模块配置"""
        if owner_id and config_content:
            self.config[owner_id] = config_content
        elif config_content:
            self.config = config_content
        if self.config == Utils.import_json(self.config_file):
            return
        try:
            Utils.save_json(self.config_file, self.config)
        except Exception:  # pylint: disable=broad-exception-caught
            self.errorf(
                f"配置文件 {self.config_file} 保存失败!\n{traceback.format_exc()}"
            )

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
        if not Utils.status_ok(result):
            self.errorf(result.get("message"))
        return result

    def reply_forward(self, nodes: list, source=None, summary=None):
        """快捷回复转发消息"""
        result = Utils.send_forward_msg(
            self.robot, nodes, self.event.group_id, self.event.user_id, source, summary
        )
        if not Utils.status_ok(result):
            self.errorf(result.get("message"))
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
