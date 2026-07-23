"""外部请求模块处理

编辑 data/webhook.json 时可参考以下新格式：
1. type_fields: 事件类型字段名列表，大小写不敏感，如 ["type", "event"]。
2. status_event_map: 当请求里没有 type/event 时，可把 status 的值映射成事件名。
3. 当请求里没有上述字段或 status 映射时，可直接使用 title 作为事件名，desp 可作为模板变量。
4. events.<事件名>.aliases: 事件别名列表，大小写不敏感。
5. events.<事件名>.vars: 模板变量提取规则。
   - from: 点路径取值，如 "eventData.name"、"Item.Id"
   - extract: 正则列表，命中后取第一个分组
   - default: 未取到值时的默认值
6. events.<事件名>.require / skip_if: 事件级条件判断。
7. events.<事件名>.send: 发送目标列表，每条消息可单独配置 when 条件。
"""

import json
import os
import re
import socket
import threading
import time
import traceback
from collections import deque

from colorama import Fore

from src.base import HttpListener, Module
from src.utils import Utils


class Webhook(Module):
    """外部请求模块处理"""

    ID = "Webhook"
    NAME = "外部请求模块处理"
    HELP = {
        0: [
            "本模块须在后台配置 WebHook 规则，仅作监听通知使用",
        ],
    }
    GLOBAL_CONFIG = {
        "host": "127.0.0.1",
        "port": 3109,
        "admin_id": "",
        "admin_warning_delay": 3600,
        "type_fields": [
            "type",
            "event",
        ],
        "status_field": "status",
        "status_event_map": {},
        "events": {
            "library.new": {
                "aliases": [
                    "library.new",
                ],
                "skip_if": [
                    {
                        "from": "Item.Type",
                        "equals": "Recording",
                    }
                ],
                "vars": {
                    "name": {
                        "from": "Title",
                        "extract": [
                            "新建 (.+)$",
                            "项到(.+)$",
                        ],
                        "default": "",
                    },
                    "img_id": {
                        "from": "Item.Id",
                        "default": "",
                    },
                },
                "send": [
                    {
                        "to": "group",
                        "id": "",
                        "message": (
                            "[CQ:image,file=http://127.0.0.1:8096/Items/"
                            "{img_id}/Images/Primary]《{name}》更新啦~"
                        ),
                    }
                ],
            },
        },
    }
    PERSISTENT = True
    HANDLE_MESSAGE = False

    def __init__(self, event, auth=0):
        """初始化外部请求监听状态并启动监听线程"""
        super().__init__(event, auth)
        if self.is_persisted():
            return
        self.is_hooking = True
        self.latest_warning_time = 0
        self.msg_deque = deque(maxlen=100)
        self.msg_imm_deque = deque(maxlen=100)
        self.config_mtime = None
        self.reload_runtime_config(force=True)
        self.schedule_background_thread(self.hooking, name="Webhook外部请求监听")

    def hooking(self):
        """Webhook监听外部请求"""
        self.printf(f"正在监听 [{Fore.GREEN}{self.config['host']}:{self.config['port']}{Fore.RESET}]")
        while self.is_hooking:
            try:
                data = self.receive_msg()
                if data is None:
                    continue
            except Exception:  # pylint: disable=broad-exception-caught
                if not self.is_hooking:
                    return
                self.errorf("加载失败, 模块已停止运行!")
                return
            try:
                threading.Thread(target=self.handle_msg, args=(data,), daemon=True).start()
                time.sleep(0.01)
            except Exception:  # pylint: disable=broad-exception-caught
                msg = f"[{self.NAME}]出现致命错误\n{traceback.format_exc()}"
                self.errorf(msg)
                if self.config["admin_id"] and (
                    time.time() - self.latest_warning_time > self.config["admin_warning_delay"]
                ):
                    Utils.send_msg(self.robot, "private", self.config["admin_id"], msg)
                    self.latest_warning_time = time.time()
                time.sleep(5)

    def unload(self):
        """停止外部请求监听循环"""
        self.is_hooking = False
        HttpListener.close(self.config["host"], self.config["port"])
        super().unload()

    def receive_msg(self):
        """接收外部请求并返回数据字典"""
        try:
            header, body = HttpListener.receive_once(
                self.config["host"],
                self.config["port"],
                accept_timeout=1,
            )
            if "application/json" not in header.get("Content-Type", ""):
                self.warnf(f"收到一非JSON数据\n{body}", level="DEBUG")
                return {}
            return json.loads(body)
        except socket.timeout:
            return None
        except socket.gaierror as e:
            self.errorf(f"绑定地址有误！ {self.config['host']} 不是一个正确的可绑定地址 {e}")
            raise e
        except OSError as e:
            if not self.is_hooking:
                return None
            self.errorf(f"端口{self.config['port']}已被占用 {e}")
            raise e
        except json.JSONDecodeError as e:
            self.warnf(f"JSON数据解析失败！ {e}")
            return {}

    def reload_runtime_config(self, force=False):
        """按需重载Webhook配置文件"""
        try:
            file_exists = os.path.exists(self.config_file)
            latest_mtime = os.path.getmtime(self.config_file) if file_exists else None
            if not force and latest_mtime == self.config_mtime:
                return
            loaded_config = Utils.import_json(self.config_file)
            if not isinstance(loaded_config, dict):
                raise TypeError("webhook.json 根节点必须是对象")
            self.config = Utils.merge(self.GLOBAL_CONFIG or {}, loaded_config)
            self.config_mtime = os.path.getmtime(self.config_file)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.warnf(f"重载Webhook配置失败，将继续沿用旧配置 {e}")

    def get_events_config(self):
        """获取事件配置"""
        events = self.config.get("events", {})
        return events if isinstance(events, dict) else {}

    def normalize_msg_type(self, msg_type):
        """标准化外部请求类型，兼容大小写与别名"""
        if not isinstance(msg_type, str):
            return ""
        normalized = msg_type.strip()
        if not normalized:
            return ""
        lowered = normalized.lower()
        for event_name, event_rule in self.get_events_config().items():
            if str(event_name).lower() == lowered:
                return event_name
            if not isinstance(event_rule, dict):
                continue
            aliases = event_rule.get("aliases", [])
            for alias in aliases:
                if isinstance(alias, str) and alias.strip().lower() == lowered:
                    return event_name
        return normalized

    def extract_msg_type(self, data: dict):
        """从Webhook请求中提取事件类型，字段名大小写不敏感"""
        self.reload_runtime_config()
        type_fields = {
            field.lower()
            for field in self.config.get("type_fields", ["type", "event"])
            if isinstance(field, str)
        }
        for type_name, msg_type in data.items():
            if isinstance(type_name, str) and type_name.lower() in type_fields:
                return self.normalize_msg_type(msg_type)
        status_field = self.config.get("status_field", "status")
        status_value = str(data.get(status_field, "")).strip().lower()
        if status_value:
            status_event_map = self.config.get("status_event_map", {})
            mapped_type = self.normalize_msg_type(status_event_map.get(status_value, ""))
            if mapped_type:
                return mapped_type

        # title/desp 是常见的简单通知格式，title 没有专门的事件字段时作为事件名。
        for field_name, title in data.items():
            if isinstance(field_name, str) and field_name.lower() == "title":
                return self.normalize_msg_type(title)
        return ""

    def get_event_rule(self, msg_type: str):
        """获取指定事件类型的配置"""
        canonical_type = self.normalize_msg_type(msg_type)
        for event_name, event_rule in self.get_events_config().items():
            if self.normalize_msg_type(event_name) == canonical_type and isinstance(event_rule, dict):
                return event_rule
        return {}

    def get_data_by_path(self, data, path, default=""):
        """按点路径读取字典或列表中的值"""
        if not path:
            return data
        current = data
        for part in str(path).split("."):
            if isinstance(current, dict):
                current = current.get(part, default)
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                if 0 <= index < len(current):
                    current = current[index]
                else:
                    return default
            else:
                return default
            if current is default:
                return default
        return current

    def get_condition_value(self, condition: dict, data: dict, template_vars: dict):
        """读取条件要比较的值"""
        default = condition.get("default", "")
        if "var" in condition:
            return template_vars.get(condition.get("var"), default)
        return self.get_data_by_path(data, condition.get("from", ""), default)

    def condition_matches(self, condition: dict, data: dict, template_vars: dict | None = None):
        """判断单条条件是否命中"""
        template_vars = template_vars or {}
        value = self.get_condition_value(condition, data, template_vars)
        if condition.get("exists") is True and value in ("", None, [], {}):
            return False
        if condition.get("truthy") is True and not value:
            return False
        if "equals" in condition and value != condition.get("equals"):
            return False
        if "not_equals" in condition and value == condition.get("not_equals"):
            return False
        if regex := condition.get("regex"):
            if not re.search(str(regex), str(value)):
                return False
        return True

    def should_ignore_event(self, data: dict, event_rule: dict):
        """按事件规则判断当前请求是否应忽略"""
        for condition in event_rule.get("require", []):
            if not isinstance(condition, dict) or not self.condition_matches(condition, data):
                return True
        for condition in event_rule.get("skip_if", []):
            if isinstance(condition, dict) and self.condition_matches(condition, data):
                return True
        return False

    def resolve_var(self, data: dict, spec):
        """解析单个模板变量"""
        if isinstance(spec, str):
            value = self.get_data_by_path(data, spec, "")
            return "" if value is None else value
        if not isinstance(spec, dict):
            return "" if spec is None else spec
        value = self.get_data_by_path(data, spec.get("from", ""), spec.get("default", ""))
        for pattern in spec.get("extract", []):
            matched = re.search(pattern, str(value))
            if matched:
                value = matched.group(1) if matched.groups() else matched.group(0)
                break
        if value is None:
            return spec.get("default", "")
        return value

    def build_template_vars(self, data: dict, event_rule: dict):
        """构建消息模板变量字典"""
        # 常见通知字段直接可用；事件规则中的同名变量仍然可以覆盖它们。
        template_vars = {
            "title": data.get("title", ""),
            "desp": data.get("desp", ""),
        }
        for name, spec in event_rule.get("vars", {}).items():
            template_vars[name] = self.resolve_var(data, spec)
        return template_vars

    def should_send_item(self, send_item: dict, data: dict, template_vars: dict):
        """判断单条发送配置是否满足条件"""
        conditions = send_item.get("when", [])
        if isinstance(conditions, dict):
            conditions = [conditions]
        for condition in conditions:
            if not isinstance(condition, dict) or not self.condition_matches(
                condition, data, template_vars
            ):
                return False
        return True

    def send_event_notifications(self, msg_type: str, data: dict, event_rule: dict):
        """按事件配置发送通知"""
        if self.should_ignore_event(data, event_rule):
            return
        template_vars = self.build_template_vars(data, event_rule)
        for send_item in event_rule.get("send", []):
            if not isinstance(send_item, dict):
                continue
            if not self.should_send_item(send_item, data, template_vars):
                continue
            try:
                msg = str(send_item.get("message", "")).format(**template_vars)
            except KeyError as e:
                self.warnf(f"{msg_type}消息模板缺少变量 {e}")
                continue
            Utils.send_msg(
                self.robot,
                send_item.get("to"),
                send_item.get("id"),
                msg,
            )

    def should_cancel_notify(self, msg_type: str, event_rule: dict):
        """判断当前通知是否应按规则取消"""
        repeat_times = int(event_rule.get("cancel_if_repeat", 0) or 0)
        if repeat_times > 0 and self.repeat(times=repeat_times, msg_type=msg_type):
            return True

        for recent_rule in event_rule.get("cancel_if_recent", []):
            recent_type = self.normalize_msg_type(recent_rule.get("event"))
            second = int(recent_rule.get("seconds", 0) or 0)
            imm = bool(recent_rule.get("include_current_batch", False))
            if recent_type and second > 0 and self.happen(recent_type, second, imm=imm):
                return True

        for occur_rule in event_rule.get("cancel_if_occur", []):
            occur_type = self.normalize_msg_type(occur_rule.get("event"))
            second = int(occur_rule.get("seconds", 0) or 0)
            if occur_type and second > 0 and self.occur(occur_type, second):
                return True
        return False

    def handle_msg(self, data: dict):
        """处理外部请求数据字典并进行相应的通知"""
        msg_type = self.extract_msg_type(data)
        msg = json.dumps(data, ensure_ascii=False)

        if msg_type == "":
            self.warnf(f"接收到一条类型未知的外部请求 {msg}")
            return

        self.printf(f"接收到一条类型为{msg_type}的外部请求 {msg}")

        if self.msg_has_reported(data):
            self.warnf("此外部请求近期已经通报过，已忽略")
            return

        self.msg_imm_deque.append({"type": msg_type, "timestamp": time.time(), "msg": msg})
        event_rule = self.get_event_rule(msg_type)
        if not event_rule:
            self.warnf(f"未找到{msg_type}对应的Webhook处理规则")
            return
        if self.should_cancel_notify(msg_type, event_rule):
            self.warnf(f"{msg_type}已取消通告")
            return
        self.send_event_notifications(msg_type, data, event_rule)
        self.msg_deque.append({"type": msg_type, "timestamp": int(time.time()), "msg": msg})

    def msg_has_reported(self, data: dict, period=86400):
        """判断消息是否已经通告过"""
        if not self.msg_deque:
            return False
        msg = json.dumps(data, ensure_ascii=False)
        same_message = any(msg == item["msg"] for item in self.msg_deque)
        series_name = data.get("Item", {}).get("SeriesName")
        same_series = series_name and any(
            series_name in item["msg"] for item in self.msg_deque
        )

        if not (same_message or same_series):
            return False
        return period == 0 or time.time() - self.msg_deque[-1]["timestamp"] >= period

    def repeat(self, times=2, msg_type: str | None = None, msg: str | None = None):
        """重复汇报大于等于给定次数"""
        if not self.msg_deque or not (msg_type or msg):
            return False

        def is_same(item):
            return (
                (not msg_type or msg_type == item["type"])
                and (not msg or msg == item["msg"])
            )

        if not is_same(self.msg_deque[-1]):
            return False
        latest_times = 0
        for item in reversed(self.msg_deque):
            if not is_same(item):
                break
            latest_times += 1
        if latest_times < times:
            return False
        self.warnf(f"重复执行次数达到{times}次而取消通知")
        return True

    def happen(self, msg_type: str, second: int, imm=False):
        """指定类型的通知是否在给定秒数内执行过"""
        msg_deque = list(self.msg_imm_deque)[:-1] if imm else self.msg_deque
        for item in reversed(msg_deque):
            require_second = round(second - time.time() + item["timestamp"], 2)
            if require_second <= 0:
                break
            if msg_type == item["type"]:
                self.warnf(f"因{second}秒内发生过{msg_type}类型通知而取消通知(仍需{require_second}秒)")
                return True
        return False

    def occur(self, msg_type: str, second: int):
        """指定类型的通知是否在给定秒数中被执行"""
        original = list(self.msg_imm_deque)
        time.sleep(second)
        for item in self.msg_imm_deque:
            if item in original:
                continue
            if msg_type == item["type"]:
                self.warnf(f"因在{second}内收到了通知{msg_type}而取消通知")
                return True
        return False
