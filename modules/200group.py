"""群组处理模块"""

from src.base import Module
from src.utils import Utils


class Group(Module):
    """群组处理模块"""

    ID = "Group"
    NAME = "群组处理模块"
    HELP = {
        1: [
            "为[QQ账号或昵称](设置)头衔[头衔] | 为用户设置专属头衔(机器人需为群主)",
            "(开启|关闭)群成员广播 | 如果是管理员，支持将群成员变动消息广播到群内(默认关闭)",
        ],
    }
    CONV_CONFIG = {
        "member_broadcast": {
            "enable": False
        }
    }
    HANDLE_NOTICE = True
    HANDLE_REQUEST = True

    def premise(self):
        if not hasattr(self.robot, "group_decrease_broadcasts"):
            self.robot.group_decrease_broadcasts = {}
        return True

    @Utils.handler(lambda self: self.group_at() and self.au(1)
        and self.match(r"^(为|给|替)\s*(\S+)\s*(设置|添加|增加|颁发|设立)(专属)*(头衔|称号)\s*(\S+)$"))
    def special_title(self):
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
        msg = ""
        if self.match(r"(开启|启用|打开|记录|启动)"):
            self.conv_config["member_broadcast"]["enable"] = True
            msg = "入群广播已开启"
        elif self.match(r"(关闭|禁用|取消)"):
            self.conv_config["member_broadcast"]["enable"] = False
            msg = "入群广播已关闭"
        self.save_config()
        self.reply(msg)

    @Utils.handler(lambda self: self.conv_config["member_broadcast"]["enable"]
         and self.event.notice_type == ("group_decrease"))
    def group_decrease(self):
        group_id = self.event.group_id
        pending = self.robot.group_decrease_broadcasts.setdefault(group_id, {"events": [], "timer": None})

        # 取消已有的定时器（重置延迟）
        if pending["timer"] is not None:
            pending["timer"].cancel()
        # 记录当前退出成员信息
        pending["events"].append((self.event.user_id, self.event.user_name))
        # 设置新的定时器，3秒后统一发送
        pending["timer"] = self.robot.loop.call_later(3, self.send_group_decrease_broadcast, group_id)

    def send_group_decrease_broadcast(self, group_id):
        """延迟发送群成员退出广播"""
        # 取出该群组的待处理数据
        pending = self.robot.group_decrease_broadcasts.pop(group_id, None)
        if pending is None:
            return

        events = pending["events"]
        if not events:
            return

        # 构建合并后的消息
        if len(events) == 1:
            uid, name = events[0]
            msg = f"{name}({uid})已退出群聊"
        else:
            members = [f"{name}({uid})" for uid, name in events]
            msg = ", ".join(members) + "已退出群聊"

        # 发送广播
        Utils.reply_id(self.robot, "group", group_id, msg)

    @Utils.handler(lambda self: self.conv_config["member_broadcast"]["enable"]
         and self.event.raw.get("request_type") == "group")
    def group_request(self):
        if self.event.raw.get("request_type") == "group":
            comment = self.event.raw.get("comment")
            Utils.reply_id(self.robot, "group", self.event.group_id,
                f"{self.event.user_name}({self.event.user_id})申请入群~\n{comment}")
