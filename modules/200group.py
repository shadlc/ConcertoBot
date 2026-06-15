"""群组处理模块"""

from collections import deque
import random
import time

from colorama import Fore

from src.base import Module
from src.utils import Utils


class Group(Module):
    """群组处理模块"""

    ID = "Group"
    NAME = "群组处理模块"
    HELP = {
        1: [
            "为[QQ号]设置头衔[头衔] | 为用户设置专属头衔(机器人需为群主)",
            "(开启|关闭)群成员广播 | 将退群和入群申请消息广播到群内(默认关闭)",
        ],
    }
    GLOBAL_CONFIG = {
        "group_decrease_delay": 3,
        "self_introduction": True,
        "self_intro": "你的名字叫{self_name}，你申请加入了群聊{group_name}，现在请简短礼貌的向大家介绍自己，无需其他内容",
        "welcome": "你的名字叫{self_name}，刚刚有新成员{user_name}加入了群聊{group_name}，现在请作为群友简短友好诙谐地欢迎，无需其他内容",
    }
    CONV_CONFIG = {
        "notice_cooldown_until": 0,
        "welcome_newbie": True,
        "welcome": None,
        "member_broadcast": {
            "enable": False
        }
    }
    HANDLE_NOTICE = True
    HANDLE_REQUEST = True

    def get_group_decrease_broadcasts(self) -> dict:
        """读取群成员变动广播缓存"""
        broadcasts = self.robot.data.get("group_decrease_broadcasts")
        if broadcasts is None:
            broadcasts = {}
            self.robot.data["group_decrease_broadcasts"] = broadcasts
        return broadcasts

    def render_llm_notice(self, template: str, **kwargs) -> str:
        """按模板渲染并调用导出 LLM 能力生成群通知文案"""
        if not template:
            return ""
        llm_chat = self.robot.func.get("llm_chat")
        if not llm_chat:
            return ""
        return llm_chat(template.format(**kwargs))

    def group_prefix(self) -> str:
        """生成统一的群日志前缀"""
        return f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}内"

    def can_send_notice(self) -> bool:
        """检查群通知节流是否已结束"""
        return time.time() >= self.conv_config.get("notice_cooldown_until", 0)

    def mark_notice_sent(self) -> None:
        """记录最近一次群通知发送时间"""
        self.conv_config["notice_cooldown_until"] = time.time() + 1
        self.save_config()

    @Utils.handler(lambda self: self.group_at() and self.au(1)
        and self.match(r"^(为|给|替)\s*(\S+)\s*(设置|添加|增加|颁发|设立)(专属)*(头衔|称号)\s*(\S+)$"))
    def special_title(self):
        """为指定群成员设置专属头衔"""
        member_info = Utils.group_member_info(
            self.robot, self.event.group_id, self.event.self_id
        )
        if member_info.get("data", {}).get("role") != "owner":
            self.reply("设置失败，仅群主可以为成员设置专属头衔")
            return
        inputs = self.match(
            r"^(为|给|替)\s*(\S+)\s*(设置|添加|增加|颁发|设立)(专属)*(头衔|称号)\s*(\S+)$"
        ).groups()
        user_id = inputs[1]
        title = inputs[5]
        if user_id == "我":
            user_id = self.event.user_id
        info = Utils.group_special_title(self.robot, self.event.group_id, user_id, title)
        if Utils.status_ok(info):
            self.reply(f"为{user_id}设置群头衔[{title}]成功!")
        else:
            self.reply(f"为{user_id}设置群头衔[{title}]失败!")

    @Utils.handler(lambda self: self.group_at() and self.au(1)
        and self.match(r"^(开启|启用|打开|记录|启动|关闭|禁用|取消)群成员广播"))
    def group_member_broadcast(self):
        """开启或关闭当前群的成员变动广播"""
        msg = ""
        if self.match(r"(开启|启用|打开|记录|启动)"):
            self.conv_config["member_broadcast"]["enable"] = True
            msg = "入群广播已开启"
        elif self.match(r"(关闭|禁用|取消)"):
            self.conv_config["member_broadcast"]["enable"] = False
            msg = "入群广播已关闭"
        self.save_config()
        self.reply(msg)

    @Utils.listener(lambda self: self.event.notice_type == "group_decrease")
    def group_decrease(self):
        """记录群成员退出、被踢或群解散通知，并按需广播"""
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
            operator_name = Utils.get_user_name(self.robot, self.event.operator_id)
            self.printf(
                f"{Fore.MAGENTA}{operator_name}({self.event.operator_id}){Fore.RESET}已将"
                f"群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}解散"
            )
            return
        if not self.conv_config["member_broadcast"]["enable"]:
            return
        group_id = self.event.group_id
        pending = self.get_group_decrease_broadcasts().setdefault(
            group_id, {"events": [], "timer": None}
        )

        # 取消已有的定时器（重置延迟）
        if pending["timer"] is not None:
            pending["timer"].cancel()
        # 记录当前退出成员信息
        pending["events"].append(
            (self.event.sub_type, self.event.user_id, self.event.user_name)
        )
        # 设置新的定时器，3秒后统一发送
        delay = max(float(self.config.get("group_decrease_delay", 3) or 0), 0)
        pending["timer"] = self.robot.loop.call_later(
            delay, self.send_group_decrease_broadcast, group_id
        )

    def send_group_decrease_broadcast(self, group_id):
        """延迟发送群成员退出广播"""
        # 取出该群组的待处理数据
        pending = self.get_group_decrease_broadcasts().pop(group_id, None)
        if pending is None:
            return

        events = pending["events"]
        if not events:
            return

        leaves = [f"{name}({uid})" for sub_type, uid, name in events if sub_type == "leave"]
        kicks = [f"{name}({uid})" for sub_type, uid, name in events if sub_type == "kick"]
        lines = []
        if kicks:
            lines.append("以下成员被移出群聊：")
            lines.append("、".join(kicks))
        if leaves:
            lines.append("以下成员已退出群聊：")
            lines.append("、".join(leaves))
        if not lines:
            return

        # 发送广播
        Utils.reply_id(self.robot, "group", group_id, "\n".join(lines))

    @Utils.listener(lambda self: self.event.notice_type == "group_recall")
    def group_recall(self):
        """记录群撤回消息，并缓存可供后续查询的撤回内容"""
        self.printf(
            f"在群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}检测到一条撤回消息"
        )
        recall_time = time.strftime("%Y年%m月%d日%H:%M:%S", time.localtime(self.event.time))
        if self.event.group_id not in self.robot.config.rev_group:
            return
        if (
            self.event.user_id == self.robot.self_id
            and self.event.operator_id != self.robot.self_id
            and self.event.operator_id not in self.robot.config.admin_list
            and random.randint(0, 2) == 0
            and self.can_send_notice()
        ):
            self.mark_notice_sent()
            msg = f"{self.event.operator_name}在{recall_time}将{self.robot.self_name}的消息撤回，{self.robot.self_name}很难过"
            Utils.reply_event(self.robot, self.event, msg)
            return
        if self.event.user_id == self.robot.self_id:
            return
        for message in getattr(self.data, "past_message", []):
            if self.event.msg_id != message.get("message_id"):
                continue
            latest_recall = self.robot.data.setdefault("latest_recall", {})
            if self.owner_id not in latest_recall:
                latest_recall[self.owner_id] = deque(maxlen=20)
            latest_recall[self.owner_id].append(message)
            break

    @Utils.listener(lambda self: self.event.notice_type == "group_upload")
    def group_upload(self):
        """记录群文件上传通知"""
        file_name = self.event.raw["file"]["name"]
        file_size = Utils.calc_size(self.event.raw["file"]["size"])
        self.printf(
            f"{self.group_prefix()}"
            f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}上传了"
            f"文件{Fore.YELLOW}{file_name}({file_size})"
        )

    @Utils.listener(lambda self: self.event.notice_type == "group_admin")
    def group_admin(self):
        """记录群管理员变更通知"""
        if self.event.sub_type == "set":
            self.printf(
                f"{self.group_prefix()}"
                f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}被设为管理员"
            )
        elif self.event.sub_type == "unset":
            self.printf(
                f"{self.group_prefix()}"
                f"管理员{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}被取缔"
            )

    @Utils.listener(lambda self: self.event.notice_type == "group_increase")
    def group_increase(self):
        """处理群成员加入通知，并按配置欢迎新人或自我介绍"""
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
        if self.event.user_id == self.robot.self_id and self.config.get("self_introduction"):
            msg = "%SELF_INTRODUCTION%"
            llm_msg = self.render_llm_notice(
                self.config.get("self_intro"),
                self_name=self.robot.self_name,
                group_name=self.event.group_name,
            )
            if llm_msg:
                msg = llm_msg
            Utils.reply_id(self.robot, "group", self.event.group_id, msg)
            return
        if (
            self.event.group_id in self.robot.config.rev_group
            and self.conv_config.get("welcome_newbie")
            and self.can_send_notice()
        ):
            self.mark_notice_sent()
            msg = self.event.user_name + " %WELCOME_NEWBIE%"
            welcome_template = self.conv_config.get("welcome") or self.config.get("welcome")
            llm_msg = self.render_llm_notice(
                welcome_template,
                self_name=self.robot.self_name,
                user_name=self.event.user_name,
                group_name=self.event.group_name,
            )
            if llm_msg:
                msg = llm_msg
            Utils.reply_id(self.robot, "group", self.event.group_id, msg)

    @Utils.listener(lambda self: self.event.notice_type == "group_ban")
    def group_ban(self):
        """记录群禁言和全员禁言状态变化"""
        duration = self.event.raw.get("duration", 0)
        if duration:
            duration = "永久" if int(duration) >= 268435455 else f"{duration}秒"
        if self.event.user_id == 0:
            if self.event.sub_type == "ban":
                self.printf(
                    f"{self.group_prefix()}"
                    f"{Fore.MAGENTA}{self.event.operator_name}({self.event.operator_id}){Fore.RESET}设置了"
                    f"{Fore.YELLOW}{duration}{Fore.RESET}的全员禁言"
                )
            elif self.event.sub_type == "lift_ban":
                self.printf(
                    f"{self.group_prefix()}"
                    f"{Fore.MAGENTA}{self.event.operator_name}({self.event.operator_id}){Fore.RESET}解除了全员禁言"
                )
        else:
            if self.event.sub_type == "ban":
                self.printf(
                    f"{self.group_prefix()}"
                    f"{Fore.MAGENTA}{self.event.operator_name}({self.event.operator_id}){Fore.RESET}为"
                    f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}设置了{Fore.YELLOW}{duration}{Fore.RESET}的禁言"
                )
            elif self.event.sub_type == "lift_ban":
                self.printf(
                    f"{self.group_prefix()}"
                    f"{Fore.MAGENTA}{self.event.operator_name}({self.event.operator_id}){Fore.RESET}解除了"
                    f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}的禁言"
                )

    @Utils.handler(lambda self: self.conv_config["member_broadcast"]["enable"]
         and self.event.raw.get("request_type") == "group")
    def group_request(self):
        """将入群申请信息广播到当前群"""
        if self.event.raw.get("request_type") == "group":
            comment = self.event.raw.get("comment")
            Utils.reply_id(self.robot, "group", self.event.group_id,
                f"{self.event.user_name}({self.event.user_id})申请入群~\n{comment}")
