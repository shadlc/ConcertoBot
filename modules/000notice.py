"""机器人基础通知处理模块"""

from collections import deque
import random
import time

from colorama import Fore

from src.utils import Module, calc_size, poke, reply_event, reply_id, via, get_user_name


class Notice(Module):
    """基础通知处理模块"""

    ID = "Notice"
    NAME = "基础通知处理模块"
    HELP = None
    GLOBAL_CONFIG = {
        "poke_reply": True,
        "self_introduction": True,
        "self_intro": "你的名字叫{self_name}，你申请加入了群聊{group_name}，现在请简短礼貌的向大家介绍自己，无需其他内容",
        "welcome": "你的名字叫{self_name}，刚刚有新成员{user_name}加入了群聊{group_name}，现在请作为群友简短友好诙谐地欢迎，无需其他内容",
    }
    CONV_CONFIG = {
        "welcome_newbie": True,
        # 可以为每个群聊设置独立欢迎提示词
        "welcome": None
    }
    HANDLE_NOTICE = True
    HANDLE_MESSAGE = False

    def premise(self):
        if not hasattr(self.robot, "last_notice_timestamp"):
            self.robot.last_notice_timestamp = 0
        return True

    @via(lambda self: self.event.notice_type == "notify"
         and self.event.sub_type == "poke"
         and self.config.get("poke_reply")
         and not self.is_self_send(), success=False)
    def poke(self):
        if self.event.group_id and self.event.group_id in self.robot.config.rev_group:
            self.printf(
                f"在群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}接收来自"
                f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}的戳一戳"
            )
            if random.choice(range(5)) == 0:
                self.printf(
                    f"20%概率触发，尝试对{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}进行反戳"
                )
                poke(self.robot, self.event.user_id, self.event.group_id)
                if self.event.target_id == self.robot.self_id:
                    reply_id(self.robot, "group", self.event.group_id, "%BE_POKED%")
        elif not self.event.group_id:
            self.printf(
                f"接收来自"
                f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}的戳一戳"
            )
            self.printf(
                f"尝试对{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}进行反戳"
            )
            poke(self.robot, self.event.user_id)
            if random.choice(range(5)) == 0:
                reply_event(self.robot, self.event, "%BE_POKED%")

    @via(lambda self: self.event.notice_type == "notify"
         and self.event.sub_type == "input_status"
         and self.event.raw.get("status_text"), success=False)
    def typing(self):
        if status_text := self.event.raw.get("status_text"):
            self.printf(
                f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}{status_text}"
            )

    @via(lambda self: self.event.notice_type == "client_status", success=False)
    def client_status(self):
        if self.event.raw["online"]:
            self.printf(
                f"检测到本账号在客户端{Fore.MAGENTA}{self.event.raw["client"]["device_name"]}{Fore.RESET}登录"
            )
        else:
            self.printf(
                f"检测到本账号在客户端{Fore.MAGENTA}{self.event.raw["client"]["device_name"]}{Fore.RESET}登出"
            )

    @via(lambda self: self.event.notice_type == "friend_add", success=False)
    def friend_add(self):
        self.printf(f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}已加为好友")

    @via(lambda self: self.event.notice_type == "friend_recall", success=False)
    def friend_recall(self):
        self.printf(f"{Fore.MAGENTA}{self.event.operator_name}({self.event.operator_id})撤回了一条消息")
        msg = "%OTHER_RECALL%"
        reply_event(self.robot, self.event, msg)

    @via(lambda self: self.event.notice_type == "group_recall", success=False)
    def group_recall(self):
        self.printf(f"在群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}检测到一条撤回消息")
        recall_time = time.strftime(
            "%Y年%m月%d日%H:%M:%S", time.localtime(self.event.time)
        )
        if self.event.group_id not in self.robot.config.rev_group:
            return
        if (
            self.event.user_id == self.robot.self_id
            and self.event.operator_id != self.robot.self_id
            and self.event.operator_id not in self.robot.config.admin_list
            and random.randint(0, 2) == 0
            and time.time() - self.robot.last_notice_timestamp > 1
        ):
            self.robot.last_notice_timestamp = time.time()
            msg = f"{self.event.operator_name}在{recall_time}将%ROBOT_NAME%的消息撤回，%ROBOT_NAME%很难过"
            reply_event(self.robot, self.event, msg)
        elif self.event.user_id != self.robot.self_id:
            for message in self.data.past_message:
                if self.event.msg_id == message.get("message_id"):
                    if not self.robot.data.get("latest_recall"):
                        self.robot.data["latest_recall"] = {}
                    if not self.robot.data.get("latest_recall", {}).get(self.owner_id):
                        self.robot.data["latest_recall"][self.owner_id] = deque(maxlen=20)
                    self.robot.data["latest_recall"][self.owner_id].append(message)

    @via(lambda self: self.event.notice_type == "group_upload", success=False)
    def group_upload(self):
        file_name = self.event.raw["file"]["name"]
        file_size = calc_size(self.event.raw["file"]["size"])
        self.printf(
            f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}内"
            f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}上传了"
            f"文件{Fore.YELLOW}{file_name}({file_size})"
        )

    @via(lambda self: self.event.notice_type == "group_admin", success=False)
    def group_admin(self):
        if self.event.sub_type == "set":
            self.printf(
                f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}内"
                f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}被设为管理员"
            )
        elif self.event.sub_type == "unset":
            self.printf(
                f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}内"
                f"管理员{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}被取缔"
            )

    @via(lambda self: self.event.notice_type == "group_decrease", success=False)
    def group_decrease(self):
        if self.event.sub_type == "leave":
            self.printf(
                f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}主动退"
                f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}"
            )
        elif self.event.sub_type == "kick":
            self.printf(
                f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}被踢出"
                f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}"
            )
        elif self.event.sub_type == "disband":
            operator_name = get_user_name(self.robot, self.event.operator_id)
            self.printf(
                f"{Fore.MAGENTA}{operator_name}({self.event.operator_id}){Fore.RESET}已将"
                f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}解散"
            )

    @via(lambda self: self.event.notice_type == "group_increase", success=False)
    def group_increase(self):
        if self.event.sub_type == "approve":
            self.printf(
                f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}已被同意加入"
                f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}"
            )
        elif self.event.sub_type == "invite":
            self.printf(
                f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}已被邀请加入"
                f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}"
            )
        llm_chat = self.robot.func.get("llm_chat")
        print(self.event.group_id in self.robot.config.rev_group, self.config[self.owner_id].get("welcome_newbie"))
        print(time.time() - self.robot.last_notice_timestamp < 1)
        if self.event.user_id == self.robot.self_id and self.config.get("self_introduction"):
            msg = "%SELF_INTRODUCTION%"
            if llm_chat:
                self_intro = self.config.get("self_intro")
                self_intro = self_intro.format(
                    self_name=self.robot.self_name,
                    group_name=self.event.group_name)
                msg = llm_chat(self_intro)
            reply_id(self.robot, "group", self.event.group_id, msg)
        elif self.event.group_id in self.robot.config.rev_group and self.config[self.owner_id].get("welcome_newbie"):
            if time.time() - self.robot.last_notice_timestamp < 1:
                return
            self.robot.last_notice_timestamp = time.time()
            msg = self.event.user_name + " %WELCOME_NEWBIE%"
            if llm_chat:
                welcome = self.config[self.owner_id].get("welcome") or self.config.get("welcome")
                welcome = welcome.format(
                    self_name=self.robot.self_name,
                    user_name=self.event.user_name,
                    group_name=self.event.group_name)
                msg = llm_chat(welcome)
            reply_id(self.robot, "group", self.event.group_id, msg)

    @via(lambda self: self.event.notice_type == "group_ban", success=False)
    def group_ban(self):
        duration = self.event.raw["duration"]
        if duration:
            duration = str(duration) + "秒" if int(duration) < 268435455 else "永久"
        if self.event.user_id == 0:
            if self.event.sub_type == "ban":
                self.printf(
                    f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}内"
                    f"{Fore.MAGENTA}{self.event.operator_name}({self.event.operator_id}){Fore.RESET}设置了"
                    f"{Fore.YELLOW}{duration}{Fore.RESET}的全员禁言"
                )
            elif self.event.sub_type == "lift_ban":
                self.printf(
                    f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}内"
                    f"{Fore.MAGENTA}{self.event.operator_name}({self.event.operator_id}){Fore.RESET}解除了全员禁言"
                )
        else:
            if self.event.sub_type == "ban":
                self.printf(
                    f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}内"
                    f"{Fore.MAGENTA}{self.event.operator_name}({self.event.operator_id}){Fore.RESET}为"
                    f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}设置了{Fore.YELLOW}{duration}{Fore.RESET}的禁言"
                )
            elif self.event.sub_type == "lift_ban":
                self.printf(
                    f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}内"
                    f"{Fore.MAGENTA}{self.event.operator_name}({self.event.operator_id}){Fore.RESET}解除了"
                    f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}的禁言"
                )

    @via(lambda self: self.event.notice_type == "notify"
         and self.event.sub_type == "profile_like", success=False)
    def profile_like(self):
        times = self.event.raw.get("times")
        self.printf(
            f"{Fore.MAGENTA}{self.event.operator_id}({self.event.operator_nick}){Fore.RESET}给你的主页点了{Fore.YELLOW}{times}{Fore.RESET}个赞"
        )
