"""机器人类定义"""

import asyncio
import ast
import importlib
import json
import logging
import os
import random
import sys
import time
import threading
import traceback

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from colorama import Fore
import httpx

from src import api
from src.config import Config
from src.placeholders import PLACEHOLDER_DICT
from src.utils import (
    Event,
    Module,
    format_to_log,
    get_handler_amount,
    handle_placeholder,
    reply_event,
    scan_missing_modules,
    send_forward_msg,
    simplify_traceback,
    submit_msg_img2char,
    receive_msg,
    send_msg,
    status_ok,
)
from src.command import ExecuteCmd

logger = logging.getLogger()

class Memory(object):
    """独立聊天记录存储"""
    def __init__(self):
        self.past_message = deque(maxlen=20)
        self.past_notice = deque(maxlen=20)


class Concerto:
    """机器人类定义"""

    def __init__(self):
        self.is_running = True
        self.is_restart = False

        self.config_file = "data/config.json"
        self.config = Config(self.config_file)
        self.cmd = {}

        self.func = {}
        self.func_owner = {}
        self.modules = {}
        self.module_files = {}
        self.module_names = {}
        self.persist_mods = {}
        self.placeholder_dict = dict(PLACEHOLDER_DICT)

        self.api_name = ""
        self.self_id = ""
        self.self_name = ""
        self.at_info = ""
        self.request_list = deque(maxlen=20)
        self.self_message = deque(maxlen=20)
        self.user_dict = {}
        self.group_dict = {}
        self.data: dict[str, Memory] = {}
        self.past_message = deque(maxlen=20)
        self.past_notice = deque(maxlen=20)
        self.past_request = deque(maxlen=20)
        self.latest_data = {}
        self.loop = asyncio.new_event_loop()
        self.message_executor = ThreadPoolExecutor(
            max_workers=self.config.handler_workers,
            thread_name_prefix="message-handler",
        )
        self.start_info = """
    __                           __        
   /  )                  _/_    /  )    _/_
  /   __ ____  _. _  __  /  __ /--<  __ /  
 (__/(_)/ / <_(__</_/ (_<__(_)/___/_(_)<__ 
        """
        self.printf(
            random.choice([Fore.RED,Fore.GREEN,Fore.YELLOW,Fore.BLUE,Fore.MAGENTA,Fore.CYAN,Fore.WHITE])
            + self.start_info + Fore.RESET, flush=True)

    def update_robot_name_placeholder(self) -> None:
        """更新机器人名称"""

    def init(self) -> bool:
        """初始化并尝试连接到API"""
        self.printf(f"正在连接API[{Fore.GREEN}{self.config.api_base}{Fore.RESET}]...", end="", console=False)
        connected = False
        while not connected:
            self.printf(".", end="", flush=True)
            try:
                result = api.get(self, "/get_version_info")
                connected = status_ok(result)
                app_name = result.get("data",{}).get("app_name")
                app_version = result.get("data",{}).get("app_version")
                self.printf(f"已连接至 {Fore.YELLOW}{app_name}v{app_version}{Fore.RESET}", flush=True)
                self.api_name = f"{app_name}v{app_version}"
                result = api.get(self, "/get_login_info")
                self.self_name = result["data"]["nickname"]
                self.self_id = str(result["data"]["user_id"])
                self.at_info = "[CQ:at,qq=" + str(self.self_id) + "]"
                self.placeholder_dict["ROBOT_NAME"] = [self.self_name]
                self.printf(f"已接入账号: {Fore.MAGENTA}{self.self_name}({self.self_id}){Fore.RESET}")
            except httpx.RequestError:
                time.sleep(1)
                continue
            time.sleep(1)
        self.import_modules()
        threading.Thread(target=self.listening_msg, daemon=True, name="消息监听").start()
        threading.Thread(target=self.listening_console, daemon=True, name="键盘监听").start()
        self.printf(f"已成功唤醒{self.self_name}, 加载模块{len(self.modules)}个, 注册处理函数{get_handler_amount(self)}个!")
        return connected

    def stop(self):
        """关闭机器人"""
        self.is_running = False
        self.is_restart = False

    def restart(self) -> None:
        """重启机器人"""
        self.is_running = False
        self.is_restart = True

    async def main_loop(self):
        """主事件循环"""
        try:
            while self.is_running:
                await asyncio.sleep(0.1)
        except asyncio.exceptions.CancelledError:
            return

    def run(self) -> None:
        """运行机器人"""
        self.init()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.main_loop())
        self.message_executor.shutdown(wait=False, cancel_futures=True)
        self.warnf("正在关闭程序...")
        sys.exit(self.is_restart)

    def listening_console(self):
        """监听来自终端的输入并处理"""
        while self.is_running:
            try:
                rev = input(f"\r{Fore.GREEN}<console> {Fore.RESET}")
            except (EOFError, KeyboardInterrupt, OSError):
                self.stop()
                break
            self.handle_console(rev)

    def listening_msg(self):
        """监听来自服务端的请求"""
        self.printf(f"正在监听: {Fore.GREEN}{self.config.host}:{self.config.port}{Fore.RESET}")
        while self.is_running:
            rev = receive_msg(self)
            self.message_executor.submit(self.handle_msg, rev)

    def handle_msg(self, rev: dict):
        """消息处理接口主函数"""

        if not rev or rev == {}:
            return

        event = Event(self, rev)
        user_id = event.user_id
        group_id = event.group_id

        # 如果是调试模式，输出所有接收到的原始信息
        if self.config.is_debug and not event.post_type == "meta_event":
            self.printf(
                f"{Fore.YELLOW}[DATA]{Fore.RESET} 接收数据包 "
                f"{Fore.YELLOW}{json.dumps(rev, ensure_ascii=False)}{Fore.RESET}"
            )

        # 数据存储到对应的data中, 并获取data
        data = {}
        if user_id in self.config.blacklist:
            pass
        elif group_id:
            if ("g" + str(group_id)) not in self.data:
                self.data["g" + str(group_id)] = Memory()
            data = self.data["g" + str(group_id)]
            self.latest_data = "g" + str(group_id)
        elif user_id:
            if ("u" + str(user_id)) not in self.data:
                self.data["u" + str(user_id)] = Memory()
            data = self.data["u" + str(user_id)]
            self.latest_data = "u" + str(user_id)
        else:
            if ("u" + str(self.self_id)) not in self.data:
                self.data["u" + str(self.self_id)] = Memory()
            data = self.data["u" + str(self.self_id)]

        # 分类处理消息，不处理自身与黑名单用户
        if user_id in self.config.blacklist:
            pass
        elif event.post_type == "message":
            data.past_message.append(rev.copy())
            if str(user_id) in self.config.admin_list:
                return self.message(event, 1)
            elif group_id:
                if group_id in self.config.rev_group:
                    return self.message(event, 2)
                else:
                    return self.message(event)
            else:
                if event.sub_type == "friend":
                    return self.message(event, 2)
                else:
                    return self.message(event)
        elif event.post_type == "message_sent":
            self.self_message.append(rev)
            return self.message_sent(event)
        elif event.post_type == "notice":
            data.past_notice.append(rev)
            return self.notice(event)
        elif event.post_type == "request":
            self.past_request.append(rev)
            return self.request(event)
        elif event.post_type == "meta_event":
            return self.event(event)

    def handle_console(self, rev):
        """终端命令处理"""
        if rev:
            logger.info("%s", f"<console> {rev}")
        return ExecuteCmd(rev, self)

    def module_handle(self, event: Event, handle_type: str, auth=3):
        """具体模块处理"""
        try:
            if handle_type == "message":
                for mod in list(self.modules.values()):
                    if mod.HANDLE_MESSAGE:
                        if mod(event, auth).handled:
                            break
            elif handle_type == "message_sent":
                for mod in list(self.modules.values()):
                    if mod.HANDLE_MESSAGE_SENT:
                        if mod(event, auth).handled:
                            break
            elif handle_type == "notice":
                for mod in list(self.modules.values()):
                    if mod.HANDLE_NOTICE:
                        if mod(event, auth).handled:
                            break
            elif handle_type == "request":
                for mod in list(self.modules.values()):
                    if mod.HANDLE_REQUEST:
                        if mod(event, auth).handled:
                            break
            elif handle_type == "event":
                for mod in list(self.modules.values()):
                    if mod.HANDLE_EVENT:
                        if mod(event, auth).handled:
                            break
        except Exception: # pylint: disable=broad-exception-caught
            if not self.config.is_error_reply:
                return
            error_msg = f"%FATAL_ERROR%\n{simplify_traceback(traceback.format_exc())}"
            if event.group_id == "":
                reply_event(self, event, error_msg)
            else:
                if self.admin_notify(error_msg):
                    return
                if event.group_id not in self.config.rev_group:
                    return
                reply_event(self, event, error_msg)

    def message(self, event: Event, auth=3):
        """处理消息事件

        Args:
            event (Event): 事件数据
            auth (int, optional): 权限等级
        """
        if event.user_id not in self.user_dict and event.user_name and event.user_id:
            self.user_dict[event.user_id] = event.user_name
        if not event.group_id:
            self.printf(
                f"{Fore.GREEN}[RECEIVE] {Fore.RESET}"
                f"{Fore.MAGENTA}{event.user_name}({event.user_id}){Fore.RESET}: {event.msg}"
            )
        elif event.group_id:
            if self.at_info in event.msg:
                self.printf(
                    f"{Fore.GREEN}[RECEIVE] {Fore.RESET}群"
                    f"{Fore.MAGENTA}{event.group_name}({event.group_id}){Fore.RESET}内"
                    f"{Fore.MAGENTA}{event.user_name}({event.user_id}){Fore.RESET}: {event.msg}"
                )
            elif self.config.is_show_all_msg:
                self.printf(
                    f"{Fore.GREEN}[RECEIVE] {Fore.RESET}群"
                    f"{Fore.MAGENTA}{event.group_name}({event.group_id}){Fore.RESET}内"
                    f"{Fore.MAGENTA}{event.user_name}({event.user_id}){Fore.RESET}: {event.msg}"
                )
        self.module_handle(event, "message", auth)

    def message_sent(self, event: Event, auth=3):
        """处理发送消息事件

        Args:
            event (Event): 事件数据
        """
        msg = event.msg[:20] + "..." if len(event.msg) > 20 else event.msg
        self.printf(
            f"{Fore.GREEN}[SENT] {Fore.RESET}"
            f"{Fore.MAGENTA}{event.target_name}({event.target_id}){Fore.RESET} "
            f"{Fore.MAGENTA}(msg_id:{event.msg_id}){Fore.RESET}: {msg}"
        )
        self.module_handle(event, "message_sent", auth)

    def notice(self, event: Event, auth=3):
        """处理通知事件

        Args:
            event (Event): 事件数据
            auth (int, optional): 权限等级
        """
        notice_type = event.sub_type or event.notice_type
        self.printf(f"{Fore.GREEN}[NOTICE] {Fore.RESET}收到了{Fore.MAGENTA}{event.user_id}{Fore.RESET}"
                    f"的{Fore.MAGENTA}{notice_type}{Fore.RESET}类型通知", level="DEBUG")
        self.module_handle(event, "notice", auth)

    def request(self, event: Event, auth=3):
        """处理请求事件

        Args:
            event (Event): 事件数据
            auth (int, optional): 权限等级
        """
        request_type = event.raw.get("request_type")
        comment = event.raw.get("comment")
        if request_type == "friend":
            self.printf(
                f"{Fore.CYAN}[REQUEST] {Fore.RESET}{Fore.MAGENTA}{event.user_name}({event.user_id}){Fore.RESET}发送好友请求"
                f"{Fore.MAGENTA}{comment}{Fore.RESET}，使用 {Fore.CYAN}add agree/deny 备注{Fore.RESET} 同意或拒绝此请求"
            )
        elif request_type == "group":
            self.printf(
                f"{Fore.CYAN}[REQUEST] {Fore.RESET}{Fore.MAGENTA}{event.user_name}({event.user_id}){Fore.RESET}发送加群请求"
                f"{Fore.MAGENTA}{comment}{Fore.RESET}，使用 {Fore.CYAN}add agree/deny 理由{Fore.RESET} 同意或拒绝此请求"
            )
        self.module_handle(event, "request", auth)

    def event(self, event: Event, auth=3):
        """处理元事件

        Args:
            event (Event): 事件数据
            auth (int, optional): 权限等级
        """
        if self.config.is_show_heartbeat:
            received = event.raw["status"]["stat"]["PacketReceived"]
            self.printf(f"{Fore.CYAN}[EVENT] {Fore.RESET}接收到API的第{Fore.MAGENTA}{received}{Fore.RESET}个心跳包")
        self.module_handle(event, "event", auth)

    def activate_func(self, func: Callable):
        """添加可调用函数"""
        func_name = func.__name__
        self.func[func_name] = func
        owner = getattr(getattr(func, "__self__", None), "ID", None)
        if owner:
            self.func_owner[func_name] = owner
        self.printf(f"新增可调用函数: {Fore.MAGENTA}{func_name}{Fore.RESET}", level="DEBUG")

    def import_modules(self):
        """从modules目录导入插件模块"""
        os.makedirs("modules", exist_ok=True)
        for item_path in self.module_py_files("modules"):
            self.load_module_file(item_path)

    def module_py_files(self, folder_path: str = "modules") -> list[str]:
        """返回排序后的Python插件文件列表"""
        py_files = []
        for root, _, files in os.walk(folder_path):
            py_files += [os.path.join(root, f) for f in files if f.endswith(".py")]
        py_files.sort(key=os.path.basename)
        return py_files

    def module_info_from_file(self, item_path: str) -> list[dict[str, str]]:
        """从模块文件中读取插件ID和名称而不导入它"""
        try:
            with open(item_path, encoding="utf-8") as file:
                tree = ast.parse(file.read(), filename=item_path)
        except (OSError, SyntaxError):
            return []
        modules = []
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            values = {}
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id in ("ID", "NAME"):
                        try:
                            values[target.id] = ast.literal_eval(stmt.value)
                        except (ValueError, TypeError):
                            pass
            if values.get("ID") and values.get("NAME"):
                modules.append({
                    "id": str(values["ID"]),
                    "name": str(values["NAME"]),
                    "file": os.path.basename(item_path),
                    "path": os.path.abspath(item_path),
                })
        return modules

    def find_module_info(self, target: str) -> dict[str, str] | None:
        """通过ID、文件名或路径查找插件元数据"""
        target = target.strip().strip("\"'")
        if not target:
            return None
        if target in self.module_files:
            module = self.modules.get(target)
            return {
                "id": target,
                "name": module.NAME if module else "",
                "file": os.path.basename(self.module_files[target]),
                "path": self.module_files[target],
            }
        for module_id, item_path in self.module_files.items():
            if module_id.lower() == target.lower():
                module = self.modules.get(module_id)
                return {
                    "id": module_id,
                    "name": module.NAME if module else "",
                    "file": os.path.basename(item_path),
                    "path": item_path,
                }
        candidate = target
        if not os.path.isabs(candidate):
            candidate = os.path.join("modules", candidate)
        if os.path.isfile(candidate):
            infos = self.module_info_from_file(candidate)
            return infos[0] if infos else None
        if not target.endswith(".py"):
            candidate = os.path.join("modules", f"{target}.py")
            if os.path.isfile(candidate):
                infos = self.module_info_from_file(candidate)
                return infos[0] if infos else None
        target_lower = target.lower()
        for item_path in self.module_py_files("modules"):
            item = os.path.basename(item_path)
            item_name = os.path.splitext(item)[0]
            infos = self.module_info_from_file(item_path)
            for info in infos:
                if target_lower in (info["id"].lower(), item.lower(), item_name.lower()):
                    return info
        return None

    def load_module_file(self, item_path: str, respect_disabled: bool = True) -> list[str]:
        """从单个模块文件加载插件"""
        item_path = os.path.abspath(item_path)
        item = os.path.basename(item_path)
        module_name = os.path.splitext(item)[0]
        missing = scan_missing_modules(item_path)
        if missing:
            self.errorf(f"无法加载模块{Fore.YELLOW}{item}{Fore.RESET}，缺少依赖: {', '.join(missing)}")
            return []
        spec = importlib.util.spec_from_file_location(module_name, item_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        is_module = False
        disabled = False
        loaded = []
        for _, obj in list(vars(module).items()):
            if isinstance(obj, type) and hasattr(obj, "ID") and obj.ID and hasattr(obj, "NAME") and obj.NAME:
                if respect_disabled and obj.ID in self.config.disabled:
                    self.printf(f"{Fore.YELLOW}[{obj.ID}]{Fore.RESET} {Fore.RESET}{obj.NAME}({item})已禁用❌")
                    disabled = True
                    continue
                is_module = True
                if self.module_enable(obj, item):
                    self.module_files[obj.ID] = item_path
                    self.module_names[obj.ID] = module_name
                    loaded.append(obj.ID)
                    if getattr(obj, "AUTO_INIT", False):
                        obj(Event(self))
        if not is_module and not disabled:
            self.warnf(f"文件[{item}]内没有有效模块，已跳过")
        return loaded

    def load_plugin(self, target: str) -> bool:
        """通过ID加载插件"""
        info = self.find_module_info(target)
        if not info:
            self.warnf(f"插件未找到: {target}")
            return False
        if info["id"] in self.config.disabled:
            self.warnf(f"插件 {Fore.MAGENTA}{info['id']}{Fore.YELLOW} 已被禁用，请先使用 enable {info['id']} 启用")
            return False
        if info["id"] in self.modules:
            self.warnf(f"插件 {Fore.MAGENTA}{info['id']}{Fore.YELLOW} 已加载过")
            return False
        return bool(self.load_module_file(info["path"]))

    def unload_plugin(self, target: str) -> bool:
        """通过ID卸载插件"""
        module_id = self.resolve_loaded_module_id(target)
        if not module_id:
            self.warnf(f"插件 {Fore.MAGENTA}{target}{Fore.YELLOW} 未加载❌")
            return False
        module = self.modules.get(module_id)
        instance = self.persist_mods.get(module_id)
        if instance:
            for hook_name in ("shutdown", "stop", "close", "on_unload", "unload"):
                hook = getattr(instance, hook_name, None)
                if not callable(hook):
                    continue
                try:
                    result = hook()
                    if asyncio.iscoroutine(result):
                        asyncio.run_coroutine_threadsafe(result, self.loop).result(timeout=5)
                except Exception:  # pylint: disable=broad-exception-caught
                    self.errorf(f"卸载插件 {Fore.MAGENTA}{module_id}{Fore.RESET} 执行卸载方法 {hook_name} 时失败:\n{traceback.format_exc()}")
                break
        for func_name, func in list(self.func.items()):
            owner = self.func_owner.get(func_name)
            bound_owner = getattr(getattr(func, "__self__", None), "ID", None)
            if owner == module_id or bound_owner == module_id or func_name == module_id:
                self.func.pop(func_name, None)
                self.func_owner.pop(func_name, None)
        self.persist_mods.pop(module_id, None)
        self.modules.pop(module_id, None)
        self.module_files.pop(module_id, None)
        module_name = self.module_names.pop(module_id, None)
        if module_name:
            sys.modules.pop(module_name, None)
        module_name = module.NAME if module else module_id
        self.printf(f"插件 {Fore.MAGENTA}{module_name}{Fore.YELLOW} 已卸载")
        return True

    def reload_plugin(self, target: str) -> bool:
        """重载插件"""
        if target.strip().lower() == "all":
            module_ids = list(self.modules.keys())
            ok = True
            for module_id in module_ids:
                ok = self.reload_plugin(module_id) and ok
            return ok
        info = self.find_module_info(target)
        if not info:
            self.warnf(f"插件未找到: {target}")
            return False
        if info["id"] in self.modules:
            self.unload_plugin(info["id"])
        return self.load_plugin(info["id"])

    def disable_plugin(self, target: str) -> bool:
        """禁用插件并在需要时卸载它"""
        info = self.find_module_info(target)
        module_id = info["id"] if info else self.resolve_loaded_module_id(target)
        if not module_id:
            self.warnf(f"插件未找到: {target}")
            return False
        if module_id not in self.config.disabled:
            self.config.disabled.append(module_id)
            self.config.save("disabled", self.config.disabled)
        if module_id in self.modules:
            self.unload_plugin(module_id)
        self.printf(f"插件 {Fore.MAGENTA}{module_id}{Fore.YELLOW} 已禁用❌")
        return True

    def enable_plugin(self, target: str) -> bool:
        """启用插件并在找到时加载它"""
        info = self.find_module_info(target)
        module_id = info["id"] if info else target.strip()
        for disabled_id in list(self.config.disabled):
            if disabled_id.lower() == module_id.lower():
                self.config.disabled.remove(disabled_id)
                module_id = disabled_id
                self.config.save("disabled", self.config.disabled)
                break
        info = self.find_module_info(module_id)
        if not info:
            self.printf(f"插件 {Fore.MAGENTA}{module_id}{Fore.RESET} 已启用，但未找到有效内部模块")
            return False
        self.printf(f"插件 {Fore.MAGENTA}{info['id']}{Fore.RESET} 已启用✔️")
        if info["id"] not in self.modules:
            self.load_plugin(info["id"])
        return True

    def resolve_loaded_module_id(self, target: str) -> str | None:
        """解析已加载的插件ID，忽略大小写"""
        target = target.strip()
        if target in self.modules:
            return target
        target_lower = target.lower()
        for module_id in self.modules:
            if module_id.lower() == target_lower:
                return module_id
        return None

    def module_enable(self, module: Module, module_file: str):
        """
        启用组件
        :param module: 组件方法
        :param module_file: 组件文件名
        """
        if module.ID in self.modules:
            self.errorf(
                f"{Fore.RED}[{module.ID}]{Fore.RESET} 重名模块{Fore.YELLOW}{module.NAME}({module_file}){Fore.RESET}载入失败！❌"
            )
            return False
        self.modules[module.ID] = module
        self.printf(f"{Fore.CYAN}[{module.ID}]{Fore.RESET} {module.NAME}({module_file})已接入✔️")
        return True

    def sync(self, func: Callable) -> Any:
        """在主线程中同步执行异步函数并返回结果"""
        future = asyncio.run_coroutine_threadsafe(func, self.loop)
        result = future.result()
        return result

    def admin_notify(self, msg, nodes: None|dict = None) -> bool:
        """向管理员发送通知消息"""
        if not self.config.is_error_reply:
            return
        if len(self.config.admin_list) == 0:
            self.warnf("无可用管理员进行通知")
            return False
        if nodes:
            send_forward_msg(self, nodes, None, self.config.admin_list[0], msg)
        else:
            send_msg(self, "private", self.config.admin_list[0], msg)
        return True

    def printf(self, msg, end="\n", console=True, flush=False, level="INFO"):
        """
        向控制台输出通知级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        if level == "DEBUG" and not self.config.is_debug:
            return
        msg = handle_placeholder(str(msg), self.placeholder_dict)
        prefix = f"\r[{time.strftime('%H:%M:%S', time.localtime())} INFO] "
        if self.config.is_show_image:
            submit_msg_img2char(self, msg)
        if flush:
            print(msg, end=end,flush=flush)
        else:
            print(f"{prefix}{msg}", end=end, flush=flush)
        if console:
            print(f"\r{Fore.GREEN}<console> {Fore.RESET}", end="")
        logger.info("%s", format_to_log(f"{prefix}{msg}"))

    def warnf(self, msg, end="\n", console=True, level="INFO"):
        """
        向控制台输出警告级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        if level == "DEBUG" and not self.config.is_debug:
            return
        msg = handle_placeholder(str(msg), self.placeholder_dict)
        msg = msg.replace(Fore.RESET, Fore.YELLOW)
        prefix = f"\r[{time.strftime('%H:%M:%S', time.localtime())} WARN] "
        msg = f"{Fore.YELLOW}{prefix}{msg}{Fore.RESET}"
        print(msg, end=end)
        logger.info("%s", format_to_log(msg))
        if console:
            print(f"\r{Fore.GREEN}<console> {Fore.RESET}", end="")

    def errorf(self, msg, end="\n", console=True, level="INFO"):
        """
        向控制台输出错误级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        if level == "DEBUG" and not self.config.is_debug:
            return
        msg = handle_placeholder(str(msg), self.placeholder_dict)
        prefix = f"\r[{time.strftime('%H:%M:%S', time.localtime())} ERROR] "
        msg = f"{Fore.RED}{prefix}{msg}{Fore.RESET}"
        print(msg, end=end)
        logger.info("%s", format_to_log(msg))
        if console:
            print(f"\r{Fore.GREEN}<console> {Fore.RESET}", end="")
