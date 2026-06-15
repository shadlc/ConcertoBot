"""机器人基础消息处理模块"""

import base64
import os
import time

from colorama import Fore
import httpx
from src.base import Module
from src.utils import Utils


class Message(Module):
    """基础消息处理模块"""
    ID = "Message"
    NAME = "基础消息处理模块"
    HELP = {
        1: [
            "[对接|删除]本群 | 对接或删除本群",
            "[增加|删除]管理员 [QQ号] | 修改管理员",
            "撤回 | 撤回机器人上一条消息",
            "重启 | 重启机器人",
            "信息 | 获取机器人基础信息",
            "调试 | 开关调试模式",
            "静默 | 开关静默模式",
            "说 [文字] | 让机器人在当前会话发消息",
            "向[QQ号]说 [文字] / 向群[群号]说 [文字] | 操控机器人发消息",
        ],
        2: [
            "测试 / 测试ip [IP] | 进行基础测试或查询IP归属",
            "读 [文字] / 向[群号]发语音 [文字] | 文字转语音",
        ],
        3: [
            "帮助 | 展示机器人全部可用功能",
            "权限 | 查看权限等级",
            "计时[数字] | 进行计时",
        ]
    }

    @Utils.handler(lambda self: self.at_or_private() and self.au(3) and self.match(r"^帮助\d?$"))
    def help(self):
        """汇总当前权限可见的模块帮助并以合并转发发送"""
        auth_level = self.auth
        if result := self.match(r"帮助(\d)"):
            auth_level = max(auth_level, int(result.group(1)))
        help_list = []
        for mod in self.robot.modules.values():
            if mod.NAME is None or not isinstance(mod.HELP, dict):
                continue
            try:
                config_file = os.path.join(
                    self.robot.config.data_path,
                    f"{str(self.ID).lower()}.json"
                )
                config = Utils.import_json(config_file)
                if config.get(self.owner_id, {}).get("enable") is False:
                    continue
            except Exception: # pylint: disable=broad-exception-caught
                pass
            help_text = ""
            max_level = 0
            for i in range(4):
                if auth_level <= i or i == 0:
                    max_level = i
                    for text in mod.HELP.get(i, []):
                        help_text += f"{text}\n"
                        if i == 0:
                            help_text += "\n"
            if help_text and max_level > 0:
                help_text = f"{mod.NAME}帮助\n\n{help_text}"
                help_list.append(self.node(help_text.strip()))
        nodes = help_list
        self.reply_forward(nodes, source="ConcertBot HELP")

    @Utils.handler(lambda self: self.at_or_private() and self.au(1) and self.match(r"^(增加|添加|删除|取消)?\s?管理员"))
    def admin(self):
        """添加或移除机器人管理员账号"""
        if self.match(r"^(增加|添加)\s?管理员\s?[0-9]+"):
            user_id = self.match(r"^(增加|添加)\s?管理员\s?([0-9]+)").group(2)
            user_name = Utils.get_user_name(self.robot, user_id)
            if user_id not in self.robot.config.admin_list:
                self.robot.config.admin_list.append(user_id)
                self.robot.config.save("admin_list", self.robot.config.admin_list)
                msg = f"{user_name}({user_id})已设置为管理员"
                self.printf(msg)
            else:
                msg = f"{user_name}({user_id})已经是管理员！"
                self.warnf(msg)

        elif self.match(r"^(删除|取消)\s?管理员\s?[0-9]+"):
            user_id = self.match(r"^(删除|取消)管理员\s?([0-9]+)").group(2)
            user_name = Utils.get_user_name(self.robot, user_id)
            if user_id in self.robot.config.admin_list:
                self.robot.config.admin_list.remove(user_id)
                self.robot.config.save("admin_list", self.robot.config.admin_list)
                msg = f"{user_name}({user_id})不再是管理员"
                self.printf(msg)
            else:
                msg = f"{user_name}({user_id})不是管理员！"
                self.warnf(msg)
        else:
            msg = "请使用 [增加|删除]管理员 [QQ号] 进行增添管理员"
        self.reply(msg)

    @Utils.handler(lambda self: self.at_or_private() and self.match(r"^权限(等级)?$"))
    def authority(self):
        """回复当前用户在机器人中的权限等级"""
        if self.au(0):
            auth_level = "后台权限"
        elif self.au(1):
            auth_level = "管理员权限"
        elif self.au(2):
            auth_level = "全功能权限"
        elif self.au(3):
            auth_level = "普通权限"
        else:
            auth_level = "未知权限"
        msg = f"您的权限等级为: {auth_level}"
        self.reply(msg)

    @Utils.handler(lambda self: self.group_at() and self.au(1)
         and self.match(r"^(对接|监听|添加|增加|记录|删除|取消|移除)(本群|此群|该群|这个群|这群|群)?$"))
    def connect(self):
        """将当前群加入或移出机器人监听群列表"""
        group_id = str(self.event.group_id)
        group_name = Utils.get_group_name(self.robot, group_id)
        msg = ""
        if self.match(r"(对接|监听|添加|增加|记录)"):
            if group_id not in self.robot.config.rev_group:
                self.robot.config.rev_group.append(group_id)
                self.robot.config.save("rev_group", self.robot.config.rev_group)
                self.printf(f"群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}已添加至对接群列表")
                msg = "已成功对接本群！"
            else:
                msg = "本群已经在对接群列表中！"
        elif self.match(r"(删除|取消|移除)"):
            if group_id in self.robot.config.rev_group:
                self.robot.config.rev_group.remove(group_id)
                self.robot.config.save("rev_group", self.robot.config.rev_group)
                msg = "已成功从对接群列表移除本群！"
                self.printf(
                    f"群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}已从对接群列表中移除"
                )
            else:
                msg = "本群不在对接列表！"
        self.reply(msg, True)

    @Utils.handler(lambda self: self.at_or_private() and self.au(1) and self.match(r"^(开启|关闭)?调试(模式)?$"))
    def debug(self):
        """开启、关闭或切换调试模式"""
        if self.match(r"^开启"):
            self.robot.config.is_debug = True
        elif self.match(r"^关闭"):
            self.robot.config.is_debug = False
        else:
            self.robot.config.is_debug = not self.robot.config.is_debug
        self.robot.config.save("is_debug", self.robot.config.is_debug)
        if self.robot.config.is_debug:
            msg = "调试模式已开启"
            self.warnf("调试模式已开启")
        else:
            msg = "调试模式已关闭"
            self.warnf("调试模式已关闭")
        self.reply(msg, True)

    @Utils.handler(lambda self: self.at_or_private() and self.match(r"^计时[0-9]+"))
    def delay(self):
        """执行简单的计时回复"""
        sleep_time = int(self.match(r"([0-9]+)").group(1))
        msg = f"计时{sleep_time}秒开始"
        self.reply(msg)
        time.sleep(sleep_time)
        msg = f"计时{sleep_time}秒结束"
        self.reply(msg)

    @Utils.handler(lambda self: self.at_or_private() and self.au(1) and self.match(r"^信息$"))
    def info(self):
        """查询协议端版本和当前已安装模块信息"""
        info = Utils.get_version_info(self.robot)
        msg = "=======API版本信息======="
        msg += f"\n应用名：{info["app_name"]}"
        msg += f"\n版本号：{info["app_version"]}"
        msg += f"\n协议版本：{info["protocol_version"]}"
        msg += f"\n已安装模块：{[f"{i.NAME}({i.ID})" for i in self.robot.modules.values()]}"
        self.reply(msg)

    @Utils.handler(lambda self: self.at_or_private() and self.au(1) and self.match(r"^(撤回|闭嘴|嘘)(！|，)?(懂？)?$"))
    def recall(self):
        """撤回机器人最近发送的一条消息"""
        if len(self.robot.self_message):
            rev = self.robot.self_message[-1]
            msg_id = rev["message_id"]
            msg = rev["message"]
            result = Utils.del_msg(self.robot, msg_id)
            self.robot.self_message.pop()
            if Utils.status_ok(result):
                self.printf(f"撤回消息{Fore.MAGENTA}{msg}{Fore.RESET}成功！")
            else:
                msg = f"撤回消息{Fore.MAGENTA}{msg}{Fore.RESET}失败！"
                self.reply(msg)
        else:
            msg = "暂无可撤回的历史消息"
            self.reply(msg)

    @Utils.handler(lambda self: self.at_or_private() and self.au(1) and self.match(r"^重启$"))
    def restart(self):
        """触发机器人重启流程"""
        self.reply("%REBOOTING%")
        self.robot.restart()

    @Utils.handler(lambda self: self.at_or_private() and self.au(1) and self.match(r"说\s(.*)$"))
    def say(self):
        """让机器人向当前会话或指定会话发送文本消息"""
        if self.match(r"^向"):
            inputs = self.match(r"([0-9]+)说\s?(\S*)").groups()
            number = inputs[0]
            send = inputs[1]
            result = False
            if self.match(r"向群[0-9]+"):
                result = Utils.status_ok(
                    Utils.send_msg(self.robot, "group", number, send)
                )
            else:
                result = Utils.status_ok(
                    Utils.send_msg(self.robot, "private", number, send)
                )
            if result:
                msg = f"发送消息{send}成功!"
            else:
                msg = f"发送消息{send}失败!"
        else:
            msg = self.match(r"说\s?(\S*)").group(1)
        self.reply(msg)

    @Utils.handler(lambda self: self.at_or_private() and self.au(1) and self.match(r"^(开启|关闭)?静默(模式)?$"))
    def silence(self):
        """开启、关闭或切换静默模式"""
        if self.match(r"^开启"):
            self.robot.config.is_silence = True
        elif self.match(r"^关闭"):
            self.robot.config.is_silence = False
        else:
            self.robot.config.is_silence = not self.robot.config.is_silence
        self.robot.config.save("is_silence", self.robot.config.is_silence)
        if self.robot.config.is_silence:
            msg = "静默模式已开启"
        else:
            msg = "静默模式已关闭"
        self.warnf(msg)
        self.reply(msg, True)

    @Utils.handler(lambda self: self.at_or_private() and self.au(1) and self.match(r"^测试"))
    def test(self):
        """执行基础连通性、错误或 IP 查询测试"""
        if self.match(r"^测试错误"):
            raise RuntimeError("测试错误")
        elif self.match(r"^测试(ip|IP)\s(\S*)"):
            ip = self.match(r"^测试(ip|IP)\s(\S*)").group(2)
            url = f"https://api.pearktrue.cn/api/ip/high/?ip={ip}"
            msg = ""
            try:
                data = httpx.get(url, timeout=3).json()
                if data.get("code") != 200:
                    msg = f"IP查询返回失败: {data.get("msg")}"
                else:
                    ip = data.get("ip")
                    address = data.get("data").get("address")
                    msg = f"IP: {ip}\n地址: {address}"
            except httpx.DecodingError as e:
                msg = f"返回解析错误！{e}"
            except httpx.ConnectError as e:
                msg = f"服务器请求错误！{e}"
        else:
            thing = self.match(r"^测试(.*)").group(1)
            msg = f"测试{thing}OK!"
        self.reply(msg)

    @Utils.handler(lambda self: self.at_or_private() and self.au(2) and self.match(r"(语音|读)\s(.*)$"))
    def voice(self):
        """将文本转换为语音并发送到当前或指定群聊"""
        text = "后面加上需要让我读出来的字嘛"
        match = self.match(r"向?(\d+)?发?送?(语音|读)\s?(.*)")
        group_id = match.group(1)
        text = match.group(3)
        if llm_tts := self.robot.func.get("llm_tts"):
            record = llm_tts(text)
            if isinstance(record, bytes):
                b64 = base64.b64encode(record).decode()
                msg = f"[CQ:record,file=base64://{b64}]"
            else:
                msg = record
            if group_id:
                result = Utils.reply_id(self.robot, "group", group_id, msg)
            else:
                result = self.reply(msg)
        elif not group_id or self.is_private():
            msg = f"[CQ:tts,text={text}]"
            if group_id:
                result = Utils.reply_id(self.robot, "group", group_id, msg)
            else:
                result = self.reply(msg)
        else:
            result = Utils.send_group_ai_record(self.robot, self.event.group_id, "lucy-voice-xueling", text)
        if not Utils.status_ok(result):
            self.reply(f"语音消息发送失败 {result.get("message")}", reply=True)

    @Utils.handler(lambda self: self.at_or_private() and self.match(r"^(在吗|你好)$"))
    def reply_msg(self):
        """回复常用问候并提示帮助入口"""
        self.reply("%MENTIONED%\n请@我并发送“帮助”来让我帮助您~")
