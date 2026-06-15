"""机器人基础通知处理模块"""

import random

from colorama import Fore

from src.base import Module
from src.utils import Utils


class Notice(Module):
    """基础通知处理模块"""

    ID = "Notice"
    NAME = "基础通知处理模块"
    HELP = None
    GLOBAL_CONFIG = {
        "poke_reply": True,
        "poke_retry_chance": 0.2,
    }
    HANDLE_NOTICE = True
    HANDLE_MESSAGE = False

    def get_poke_retry_chance(self) -> float:
        """读取 0-1 小数形式的反戳概率配置"""
        chance = float(self.config.get("poke_retry_chance", 0.2) or 0)
        return min(max(chance, 0), 1)

    @Utils.listener(lambda self: self.event.notice_type == "notify"
         and self.event.sub_type == "poke"
         and self.config.get("poke_reply")
         and not self.is_self_send())
    def poke(self):
        """处理戳一戳通知并按配置进行回应"""
        if self.event.group_id and self.event.group_id in self.robot.config.rev_group:
            self.printf(
                f"在群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}接收来自"
                f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}的戳一戳"
            )
            chance = self.get_poke_retry_chance()
            if random.random() < chance:
                chance = f"{chance:.0%}"
                self.printf(f"{chance}概率触发，尝试对{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}进行反戳")
                Utils.poke(self.robot, self.event.user_id, self.event.group_id)
                if self.event.target_id == self.robot.self_id:
                    Utils.reply_id(self.robot, "group", self.event.group_id, "%BE_POKED%")
        elif not self.event.group_id:
            self.printf(f"接收来自{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}的戳一戳，尝试进行反戳")
            Utils.poke(self.robot, self.event.user_id)
            if random.random() < self.get_poke_retry_chance():
                Utils.reply_event(self.robot, self.event, "%BE_POKED%")

    @Utils.listener(lambda self: self.event.notice_type == "notify"
         and self.event.sub_type == "input_status"
         and self.event.raw.get("status_text"))
    def typing(self):
        """记录好友或群成员正在输入的状态提示"""
        if status_text := self.event.raw.get("status_text"):
            self.printf(f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}{status_text}")

    @Utils.listener(lambda self: self.event.notice_type == "client_status")
    def client_status(self):
        """记录机器人账号的客户端登录或登出状态"""
        client = self.event.raw.get("client", {})
        device_name = client.get("device_name", "未知设备")
        if self.event.raw.get("online"):
            self.printf(f"检测到本账号在客户端{Fore.MAGENTA}{device_name}{Fore.RESET}登录")
        else:
            self.printf(f"检测到本账号在客户端{Fore.MAGENTA}{device_name}{Fore.RESET}登出")

    @Utils.listener(lambda self: self.event.notice_type == "friend_add")
    def friend_add(self):
        """记录新增好友通知"""
        self.printf(f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}已加为好友")

    @Utils.listener(lambda self: self.event.notice_type == "friend_recall")
    def friend_recall(self):
        """处理好友撤回通知并发送占位回复"""
        self.printf(f"{Fore.MAGENTA}{self.event.operator_name}({self.event.operator_id})撤回了一条消息")
        msg = "%OTHER_RECALL%"
        Utils.reply_event(self.robot, self.event, msg)

    @Utils.listener(lambda self: self.event.notice_type == "notify"
         and self.event.sub_type == "profile_like")
    def profile_like(self):
        """记录主页点赞通知"""
        times = self.event.raw.get("times", 1)
        self.printf(
            f"{Fore.MAGENTA}{self.event.operator_id}({self.event.operator_nick}){Fore.RESET}"
            f"给你的主页点了{Fore.YELLOW}{times}{Fore.RESET}个赞"
        )
