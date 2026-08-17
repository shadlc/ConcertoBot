"""机器人类定义"""

import asyncio
import ast
import importlib
import json
import logging
import os
import random
import signal
import socket
import sys
import time
import threading
import traceback

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Callable
from colorama import Fore, just_fix_windows_console
import httpx

from src import api
from src.config import Config
from src.placeholders import PLACEHOLDER_DICT
from src.base import Event, HttpListener, Module
from src.utils import Utils
from src.command import ExecuteCmd

logger = logging.getLogger()


def configure_logging(log_path: str) -> None:
    """每个进程仅配置一次文件日志"""
    logger.setLevel(logging.INFO)
    os.makedirs(log_path, exist_ok=True)
    log_file = os.path.abspath(os.path.join(log_path, "bot.log"))
    for handler in logger.handlers:
        if (
            isinstance(handler, TimedRotatingFileHandler)
            and getattr(handler, "baseFilename", "") == log_file
        ):
            return
    handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


class Memory:
    """独立聊天记录存储"""

    def __init__(self):
        """初始化消息和通知的短期历史缓存"""
        self.past_message = deque(maxlen=20)
        self.past_notice = deque(maxlen=20)


class Concerto:
    """机器人类定义"""

    def __init__(self):
        """初始化机器人"""
        self.is_running = True
        self.is_restart = False

        self.config_file = "data/config.json"
        self.config = Config(self.config_file)
        self.cmd = {}
        ExecuteCmd("", self)

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

    def setup_console_completion(self) -> None:
        """命令自动补全"""
        try:
            import readline  # pylint: disable=import-error,import-outside-toplevel
        except ImportError:
            logger.warning("未安装库readline，命令自动补全功能已禁用")
            return None
        def completer(text: str, state: int) -> str | None:
            """为控制台命令提供补全候选"""
            matches = []
            if state == 0:
                matches = sorted(cmd for cmd in self.cmd if cmd.startswith(text))
            if state < len(matches):
                return matches[state]
            return None
        readline.parse_and_bind("tab: complete")
        readline.set_completer(completer)



    def setup_signal_handler(self) -> None:
        """捕获Ctrl C信号"""
        def handle_signal(signum, frame): # pylint: disable=unused-argument
            """在收到 Ctrl+C 时停止机器人并退出进程"""
            self.stop()
            raise SystemExit()

        signal.signal(signal.SIGINT, handle_signal)

    def setup_runtime(self) -> None:
        """配置进程级运行时行为"""
        just_fix_windows_console()
        configure_logging(self.config.log_path)
        self.setup_console_completion()
        self.setup_signal_handler()

    def init(self) -> bool:
        """初始化并尝试连接到API"""
        self.printf(f"正在连接API [{Fore.GREEN}{self.config.api_base}{Fore.RESET}]...")
        connected = False
        while not connected:
            self.printf(".", end="", flush=True)
            try:
                result = api.get(self, "/get_version_info")
                connected = Utils.status_ok(result)
                app_name = result.get("data",{}).get("app_name")
                app_version = result.get("data",{}).get("app_version")
                self.printf(f"已连接至 {Fore.YELLOW}{app_name}v{app_version}{Fore.RESET}")
                self.api_name = f"{app_name}v{app_version}"
                result = api.get(self, "/get_login_info")
                self.self_name = result["data"]["nickname"]
                self.self_id = str(result["data"]["user_id"])
                self.at_info = "[CQ:at,qq=" + str(self.self_id) + "]"
                self.placeholder_dict["ROBOT_NAME"] = [self.self_name]
            except httpx.RequestError:
                time.sleep(1)
                continue
            time.sleep(1)
        self.import_modules()
        # 全部模块加载完再监听消息，避免消息处理逻辑遗留
        threading.Thread(target=self.listening_msg, daemon=True, name="消息监听").start()
        threading.Thread(target=self.listening_console, daemon=True, name="键盘监听").start()
        self.printf(
            f"已成功唤醒{Fore.MAGENTA}{self.self_name}({self.self_id}){Fore.RESET}, 模块加载完成~"
            f"加载模块{Fore.MAGENTA}{len(self.modules)}{Fore.RESET}个, "
            f"注册处理函数{Fore.MAGENTA}{Utils.get_handler_amount(self)}{Fore.RESET}个!"
        )
        return connected

    def stop(self):
        """关闭机器人"""
        self.is_running = False
        self.is_restart = False
        HttpListener.close_all()

    def restart(self) -> None:
        """重启机器人"""
        self.is_running = False
        self.is_restart = True
        HttpListener.close_all()

    async def main_loop(self):
        """主事件循环"""
        try:
            while self.is_running:
                await asyncio.sleep(0.1)
        except asyncio.exceptions.CancelledError:
            return

    def run(self) -> None:
        """运行机器人"""
        try:
            self.init()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.main_loop())
        finally:
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
        """监听来自API的请求"""
        self.printf(f"反向监听地址已启用 [{Fore.GREEN}{self.config.host}:{self.config.port}{Fore.RESET}]")
        while self.is_running:
            rev = self.receive_msg()
            if rev:
                self.message_executor.submit(self.handle_msg, rev)

    def receive_msg(self):
        """接收反向 HTTP 上报数据"""
        body = None
        try:
            header, body = HttpListener.receive_once(
                self.config.host,
                int(self.config.port),
                accept_timeout=1,
            )
            if "application/json" not in header.get("Content-Type", ""):
                self.warnf(f"收到一非JSON数据\n{body}", level="DEBUG")
                return {}
            return json.loads(body)
        except socket.timeout:
            return {}
        except socket.gaierror as e:
            self.errorf(f"绑定地址有误! {self.config.host} 不是一个正确的可绑定地址，程序终止! {e}")
            self.stop()
        except OSError as e:
            if not self.is_running:
                return {}
            self.errorf(f"端口{self.config.port}已被占用，程序终止! {e}")
            self.stop()
        except json.JSONDecodeError:
            self.warnf(f"{body} JSON数据解析失败! {traceback.format_exc()}")
            return {}

    def handle_msg(self, rev: dict):
        """消息处理接口主函数"""

        if not rev or rev == {}:
            return

        event = Event(self, rev)
        user_id = event.user_id
        group_id = event.group_id

        # 如果是调试模式，输出所有接收到的原始信息
        self.printf(
            f"{Fore.YELLOW}[DATA]{Fore.RESET} 接收数据包 "
            f"{Fore.YELLOW}{json.dumps(rev, ensure_ascii=False)}{Fore.RESET}",
            level="DEBUG"
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
            handle_attr = {
                "message": "HANDLE_MESSAGE",
                "message_sent": "HANDLE_MESSAGE_SENT",
                "notice": "HANDLE_NOTICE",
                "request": "HANDLE_REQUEST",
                "event": "HANDLE_EVENT",
            }.get(handle_type)
            if not handle_attr:
                return
            for mod in list(self.modules.values()):
                if getattr(mod, handle_attr):
                    module = self.create_module(mod, event, auth)
                    if module and module.handled:
                        break
        except Exception: # pylint: disable=broad-exception-caught
            if not self.config.is_error_reply:
                return
            error_msg = f"%FATAL_ERROR%\n{Utils.simplify_traceback()}"
            if event.group_id == "":
                Utils.reply_event(self, event, error_msg)
            else:
                if self.admin_notify(error_msg):
                    return
                if event.group_id not in self.config.rev_group:
                    return
                Utils.reply_event(self, event, error_msg)

    def create_module(
        self,
        module_class: type[Module],
        event: Event,
        auth: int = 0,
    ) -> Module | None:
        """创建模块实例，并在初始化完成后执行框架级自动注册"""
        try:
            module = module_class(event, auth)
            module.auto_bootstrap()
            return module
        except Exception:  # pylint: disable=broad-exception-caught
            module_name = getattr(module_class, "NAME", module_class.__name__)
            module_id = getattr(module_class, "ID", module_class.__name__)
            self.errorf(f"模块 [{module_id}] {module_name} 初始化失败，已禁用 ❌\n{traceback.format_exc()}")
            self.modules.pop(module_id, None)
            return None

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

    def register_func(self, func: Callable, name: str | None = None):
        """添加可调用函数"""
        func_name = name or func.__name__
        self.func[func_name] = func
        owner = getattr(getattr(func, "__self__", None), "ID", None)
        if owner:
            self.func_owner[func_name] = owner

    def get_persist_mod(self, module_id: str):
        """读取长生命周期模块实例"""
        return self.persist_mods.get(module_id)

    def register_persist_mod(self, module: Module) -> bool:
        """注册长生命周期模块实例，避免模块直接操作 persist_mods"""
        module_id = module.ID
        if module_id in self.persist_mods:
            return False
        self.persist_mods[module_id] = module
        return True

    def unregister_persist_mod(self, module_id: str):
        """移除长生命周期模块实例"""
        return self.persist_mods.pop(module_id, None)

    def import_modules(self):
        """从modules目录导入模块"""
        os.makedirs("modules", exist_ok=True)
        for item_path in self.module_py_files("modules"):
            self.load_module_file(item_path)

    def module_py_files(self, folder_path: str = "modules") -> list[str]:
        """返回排序后的Python模块文件列表"""
        py_files = []
        for root, _, files in os.walk(folder_path):
            py_files += [os.path.join(root, f) for f in files if f.endswith(".py")]
        py_files.sort(key=os.path.basename)
        return py_files

    def module_info_from_file(self, item_path: str) -> list[dict[str, str]]:
        """从模块文件中读取模块ID和名称而不导入它"""
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
        """通过ID、文件名或路径查找模块元数据"""
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
        """从单个文件加载模块"""
        item_path = os.path.abspath(item_path)
        item = os.path.basename(item_path)
        module_name = os.path.splitext(item)[0]
        missing = Utils.scan_missing_modules(item_path)
        if missing:
            self.errorf(f"缺少依赖: {', '.join(missing)} 无法加载模块{item} ❌")
            return []
        spec = importlib.util.spec_from_file_location(module_name, item_path)
        if spec is None or spec.loader is None:
            self.errorf(f"导入规格创建失败，无法加载模块{item} ❌")
            return []
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:  # pylint: disable=broad-exception-caught
            sys.modules.pop(spec.name, None)
            self.errorf(f"模块文件 {Fore.YELLOW}{item}{Fore.RESET} 导入失败 ❌\n{traceback.format_exc()}")
            return []
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
                    if getattr(obj, "PERSISTENT", False):
                        self.create_module(obj, Event(self))
        if not is_module and not disabled:
            self.warnf(f"文件[{item}]内没有有效模块，已跳过")
        return loaded

    def load_plugin(self, target: str) -> bool:
        """通过ID加载模块"""
        info = self.find_module_info(target)
        if not info:
            self.warnf(f"模块未找到: {target}")
            return False
        if info["id"] in self.config.disabled:
            self.warnf(f"模块 {Fore.MAGENTA}{info['id']}{Fore.YELLOW} 已被禁用，请先使用 enable {info['id']} 启用")
            return False
        if info["id"] in self.modules:
            self.warnf(f"模块 {Fore.MAGENTA}{info['id']}{Fore.YELLOW} 已加载过")
            return False
        loaded = bool(self.load_module_file(info["path"]))
        if loaded:
            module_order = sorted(
                self.modules,
                key=lambda module_id: os.path.basename(self.module_files[module_id]),
            )
            self.reorder_modules(module_order)
        return loaded

    def reorder_modules(self, module_order: list[str]) -> None:
        """按指定顺序整理已加载模块，并保留未列出的模块"""
        current_modules = self.modules.copy()
        self.modules.clear()
        self.modules.update({
            module_id: current_modules[module_id]
            for module_id in module_order
            if module_id in current_modules
        })
        self.modules.update({
            module_id: module
            for module_id, module in current_modules.items()
            if module_id not in module_order
        })

    def unload_plugin(self, target: str) -> bool:
        """通过ID卸载模块"""
        module_id = self.resolve_loaded_module_id(target)
        if not module_id:
            self.warnf(f"模块 {Fore.MAGENTA}{target}{Fore.YELLOW} 未加载❌")
            return False
        module = self.modules.get(module_id)
        instance = self.get_persist_mod(module_id)
        if instance:
            hook = getattr(instance, "unload", None)
            if callable(hook):
                try:
                    result = hook()
                    if asyncio.iscoroutine(result):
                        asyncio.run_coroutine_threadsafe(result, self.loop).result(timeout=5)
                except Exception:  # pylint: disable=broad-exception-caught
                    self.errorf(f"卸载模块 {Fore.MAGENTA}{module_id}{Fore.RESET} 执行卸载方法时失败\n{traceback.format_exc()}")
        for func_name, func in list(self.func.items()):
            owner = self.func_owner.get(func_name)
            bound_owner = getattr(getattr(func, "__self__", None), "ID", None)
            if owner == module_id or bound_owner == module_id or func_name == module_id:
                self.func.pop(func_name, None)
                self.func_owner.pop(func_name, None)
        self.unregister_persist_mod(module_id)
        self.modules.pop(module_id, None)
        self.module_files.pop(module_id, None)
        module_name = self.module_names.pop(module_id, None)
        if module_name:
            sys.modules.pop(module_name, None)
        module_name = module.NAME if module else module_id
        self.printf(f"模块 {Fore.MAGENTA}{module_name}{Fore.YELLOW} 已卸载")
        return True

    def reload_plugin(self, target: str) -> bool:
        """重载模块"""
        if target.strip().lower() == "all":
            module_ids = list(self.modules.keys())
            ok = True
            for module_id in module_ids:
                ok = self.reload_plugin(module_id) and ok
            return ok
        info = self.find_module_info(target)
        if not info:
            self.warnf(f"模块未找到: {target}")
            return False
        if info["id"] in self.modules:
            self.unload_plugin(info["id"])
        return self.load_plugin(info["id"])

    def disable_plugin(self, target: str) -> bool:
        """禁用模块并在需要时卸载它"""
        info = self.find_module_info(target)
        module_id = info["id"] if info else self.resolve_loaded_module_id(target)
        if not module_id:
            self.warnf(f"模块未找到: {target}")
            return False
        if module_id not in self.config.disabled:
            self.config.disabled.append(module_id)
            self.config.save("disabled", self.config.disabled)
        if module_id in self.modules:
            self.unload_plugin(module_id)
        self.printf(f"模块 {Fore.MAGENTA}{module_id}{Fore.YELLOW} 已禁用❌")
        return True

    def enable_plugin(self, target: str) -> bool:
        """启用模块并在找到时加载它"""
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
            self.warnf(f"模块 {Fore.MAGENTA}{module_id}{Fore.RESET} 已加载，但未找到有效内部模块 ⚠️")
            return False
        self.printf(f"模块 {Fore.MAGENTA}{info['id']}{Fore.RESET} 已启用✔️")
        if info["id"] not in self.modules:
            self.load_plugin(info["id"])
        return True

    def resolve_loaded_module_id(self, target: str) -> str | None:
        """解析已加载的模块ID，忽略大小写"""
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
                f"{Fore.RED}[{module.ID}]{Fore.RESET} 重名模块{Fore.YELLOW}{module.NAME}({module_file}){Fore.RESET}载入失败! ❌"
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

    def admin_notify(
        self,
        msg,
        nodes: None|dict = None,
        event: Event | None = None,
    ) -> bool:
        """向管理员发送通知消息"""
        if not self.config.is_error_reply:
            return
        if len(self.config.admin_list) == 0:
            self.warnf("无可用管理员进行通知")
            return False
        if nodes:
            if event:
                group_id = str(getattr(event, "group_id", "") or "")
                user_id = str(getattr(event, "user_id", "") or "")
                user_name = str(getattr(event, "user_name", "") or user_id or "未知用户")
                if group_id:
                    group_name = str(getattr(event, "group_name", "") or group_id)
                    source = f"群聊：{group_name}；发送者：{user_name}"
                else:
                    source = f"用户：{user_name}"
                content = nodes.get("data", {}).get("content", "")
                nodes = Utils.build_node(f"来源：{source}\n{content}")
            Utils.send_forward_msg(self, nodes, None, self.config.admin_list[0], msg)
        else:
            Utils.send_msg(self, "private", self.config.admin_list[0], msg)
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
        msg = Utils.handle_placeholder(str(msg), self.placeholder_dict)
        prefix = f"\r[{time.strftime('%H:%M:%S', time.localtime())} INFO] "
        if self.config.is_show_image:
            Utils.submit_msg_img2char(self, msg)
        if flush:
            print(msg, end=end,flush=flush)
        else:
            print(f"{prefix}{msg}", end=end, flush=flush)
        if console:
            print(f"\r{Fore.GREEN}<console> {Fore.RESET}", end="")
        logger.info("%s", Utils.format_to_log(f"{prefix}{msg}"))

    def warnf(self, msg, end="\n", console=True, level="INFO"):
        """
        向控制台输出警告级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        if level == "DEBUG" and not self.config.is_debug:
            return
        msg = Utils.handle_placeholder(str(msg), self.placeholder_dict)
        msg = msg.replace(Fore.RESET, Fore.YELLOW)
        prefix = f"\r[{time.strftime('%H:%M:%S', time.localtime())} WARN] "
        msg = f"{Fore.YELLOW}{prefix}{msg}{Fore.RESET}"
        print(msg, end=end)
        logger.info("%s", Utils.format_to_log(msg))
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
        msg = Utils.handle_placeholder(str(msg), self.placeholder_dict)
        prefix = f"\r[{time.strftime('%H:%M:%S', time.localtime())} ERROR] "
        msg = f"{Fore.RED}{prefix}{msg}{Fore.RESET}"
        print(msg, end=end)
        logger.info("%s", Utils.format_to_log(msg))
        if console:
            print(f"\r{Fore.GREEN}<console> {Fore.RESET}", end="")
