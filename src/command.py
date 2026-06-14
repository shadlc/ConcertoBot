"""命令实现"""

from __future__ import annotations

import re
import time
import json
import traceback

from typing import TYPE_CHECKING
from colorama import Fore

from src.utils import Utils
from src.api import (
    get,
    get_version_info,
    get_login_info,
    bot_exit,
    post,
)

if TYPE_CHECKING:
    from src.robot import Concerto


class ExecuteCmd(object):
    """执行命令"""

    def __init__(self, cmd, robot: Concerto):
        self.robot = robot
        self.robot.cmd = {
            "add": "对添加请求进行操作",
            "aivoice": "发送AI声聊消息",
            "api": "查看向API请求的历史记录",
            "debug": "开关调试模式",
            "deop": "取消管理员权限",
            "device": "设置在线机型",
            "emoji": "对某条消息ID贴表情(默认❤)(默认上一条消息)",
            "exit": "退出机器人",
            "get": "获取用户或群的信息",
            "group": "修改对接群列表",
            "gmsg": "发送群聊消息",
            "gvoice": "发送群语音消息",
            "help": "打开帮助菜单",
            "history": "查看历史消息",
            "info": "查看API版本和相关信息",
            "like": "对用户主页点赞",
            "llm": "调用首个配置的大语言模型进行对话(如果可以)",
            "msg": "发送私聊消息",
            "notice": "发送群公告",
            "ocr": "识别图片中的文字",
            "op": "增加管理员权限",
            "poke": "私聊戳一戳(默认上一条消息)",
            "read": "读取转发消息内容",
            "recall": "撤回消息",
            "restart": "重启程序",
            "reload": "重载插件",
            "load": "加载插件",
            "unload": "卸载插件",
            "enable": "启用插件",
            "disable": "禁用插件",
            "request": "手动调用API",
            "reply": "回复上一条消息",
            "qreply": "快捷回复上一条消息(不支持快捷撤回)",
            "say": "快捷向主对接群发送消息",
            "set": "设置变量",
            "sign": "进行群打卡",
            "silence": "静默模式",
            "stop": "关闭程序",
            "test": "测试接口",
            "tmpmsg": "发送临时消息",
            "voice": "发送语音消息",
        }
        if cmd:
            argv = cmd.strip().split(" ", 1)
            try:
                if not hasattr(self, argv[0]):
                    self.unknown()
                else:
                    method = getattr(self, argv[0])
                    if len(argv) > 1:
                        method(argv[1])
                    else:
                        method()
            except Exception:  # pylint: disable=broad-exception-caught
                self.errorf(Fore.RED + traceback.format_exc())

    def add(self, argv=""):
        """同意或拒绝好友/群添加请求"""
        if re.search(r"(agree|deny)\s?(.*)", argv):
            inputs = re.search(r"(agree|deny)\s?(.*)", argv).groups()
            if len(self.robot.past_request) == 0:
                self.warnf("未寻找到上一条请求记录！")
            else:
                rev = self.robot.past_request[-1]
                user_id = rev["user_id"]
                user_name = Utils.get_user_name(self.robot, user_id)
                result = ""
                if inputs[0] == "agree":
                    result = Utils.reply_add(self.robot, rev, "true", inputs[1])
                else:
                    result = Utils.reply_add(self.robot, rev, "false", inputs[1])
                if Utils.status_ok(result):
                    self.printf(f"已对{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}的请求作出回应")
                else:
                    self.printf(f"回应失败！{result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}add agree/deny 备注{Fore.RESET} 同意或拒绝申请")

    def api(self, argv=""): # pylint: disable=unused-argument
        """查看向API请求的历史记录"""
        self.printf(f"向 {Fore.GREEN}{self.robot.config.api_base}{Fore.RESET} {Fore.YELLOW}{self.robot.api_name}{Fore.RESET} 请求的历史记录:")
        for request in self.robot.request_list:
            if re.search(r"^GET", request):
                self.printf(f"{Fore.MAGENTA}[GET]{Fore.RESET} {request.replace("GET", "")}{Fore.MAGENTA}{Fore.RESET}")
            else:
                self.printf(f"{Fore.MAGENTA}[POST]{Fore.RESET} {request.replace("POST", "")}{Fore.MAGENTA}{Fore.RESET}")

    def debug(self, argv=""): # pylint: disable=unused-argument
        """开关调试模式"""
        self.robot.config.is_debug = not self.robot.config.is_debug
        self.robot.config.save("is_debug", self.robot.config.is_debug)
        if self.robot.config.is_debug:
            self.warnf("DEBUG模式已开启")
        else:
            self.warnf("DEBUG模式已关闭")

    def deop(self, argv=""):
        """取消管理员权限"""
        if re.search(r"(\d+)", argv):
            user_id = re.search(r"^(\d+)", argv).group(1)
            user_name = Utils.get_user_name(self.robot, user_id)
            if user_id in self.robot.config.admin_list:
                self.robot.config.admin_list.remove(user_id)
                self.robot.config.save("admin_list", self.robot.config.admin_list)
                self.printf(f"{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}不再是管理员")
            else:
                self.warnf(f"{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}不是管理员！")
        else:
            self.printf(f"请使用 {Fore.CYAN}deop 用户QQ{Fore.RESET} 取消管理员")

    def device(self, argv=""):
        """设置在线机型"""
        if re.search(r"(.+)", argv):
            device = re.search(r"(.+)", argv).group(1)
            result = Utils.set_model_show(self.robot, device, device)
            if Utils.status_ok(result):
                self.printf(f"成功设置新登陆设备为{Fore.MAGENTA}{device}{Fore.RESET}")
            else:
                self.printf(f"设置失败！{result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}device 型号{Fore.RESET} 设置登陆设备型号")

    def emoji(self, argv=""):
        """对某条消息ID贴表情(默认❤)(默认上一条消息)"""
        if re.search(r"^(\d+)", argv):
            inputs = re.search(r"(\d+)\s?(\d+)", argv).groups()
            msg_id = inputs[0]
            emoji_id = inputs[1] if inputs[1] else 66
            result = Utils.set_emoji(self.robot, msg_id, emoji_id)
            if err := result.get("message", ""):
                self.warnf(f"贴表情出错 {err}")
            elif err := result.get("data", {}).get("errMsg"):
                self.warnf(f"贴表情出错 {err}")
            else:
                self.printf(f"向消息{Fore.MAGENTA}[message_id: {msg_id}]{Fore.RESET}贴了表情[id: {emoji_id}]")
        elif (
            self.robot.latest_data
            and self.robot.data[self.robot.latest_data].past_message[-1]
        ):
            rev = self.robot.data[self.robot.latest_data].past_message[-1]
            msg_id = rev.get("message_id")
            raw_msg = rev.get("message")
            result = Utils.set_emoji(self.robot, msg_id, emoji_id)
            if Utils.status_ok(result):
                self.printf(f"向消息{Fore.MAGENTA}(mg_id: {msg_id}){raw_msg}{Fore.RESET}贴了表情❤")
            else:
                self.warnf(f"贴表情出错 {result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}emoji 消息ID{Fore.RESET} 进行贴表情")

    def exit(self, argv=""): # pylint: disable=unused-argument
        """退出机器人"""
        result = bot_exit(self.robot)
        if Utils.status_ok(result):
            self.printf("账号已退出")
        else:
            self.warnf("账号退出失败")

    def get(self, argv=""): # pylint: disable=unused-argument
        """获取用户或群的信息"""
        if re.search(r"user\s+(\d+)", argv):
            user_id = re.search(r"user\s+(\d+)", argv).group(1)
            result = Utils.get_stranger_info(self.robot, user_id)
            if Utils.status_ok(result):
                result = result["data"]
                if result["sex"] == "male":
                    result["sex"] = "男"
                elif result["sex"] == "female":
                    result["sex"] = "女"
                else:
                    result["sex"] = "其他"
                self.printf(f"用户{Fore.MAGENTA}{result["nickname"]}({user_id}){Fore.RESET}信息")
                self.printf(f"性别：{result["sex"]}")
                self.printf(f"年龄：{result["age"]}岁")
                self.printf(f"QQ等级：{result["qqLevel"]}级")
                self.printf(f"QID：{result["qid"]}")
                self.printf(f"邮箱：{result["eMail"]}")
                self.printf(f"手机号：{result["phoneNum"]}")
                self.printf(f"生日：{result["birthday_year"]}-{result["birthday_month"]}-{result["birthday_day"]}")
                self.printf(f"签名：{result["longNick"]}")
            else:
                self.printf(f"查无此号！{result.get("message")}")
        elif re.search(r"group\s+(\d+)", argv):
            group_id = re.search(r"group\s+(\d+)", argv).group(1)
            result = Utils.get_group_info(self.robot, group_id)
            if Utils.status_ok(result):
                data = result.get("data")
                self.printf(f"群{Fore.MAGENTA}{data.get("group_name")}({group_id}){Fore.RESET}信息")
                self.printf(f"群简介：{data.get("fingerMemo", "")}")
                self.printf(f"群创建时间：{time.strftime("%Y年%m月%d日 %H:%M:%S", time.localtime(data.get("groupCreateTime", 0)))}")
                self.printf(f"群等级：{data.get("groupGrade", "未知")}级")
                self.printf(f"群人数：{data.get("member_count")}人")
                self.printf(f"入群问题：{data.get("groupQuestion", "无")}")
            else:
                self.printf(f"查无此群！{data.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}get user/group QQ号/群号{Fore.RESET} 获取信息")

    def group(self, argv=""):
        """修改对接群列表"""
        if re.search(r"add\s+(\d+)", argv):
            group_id = re.search(r"\s+(\d+)", argv).group(1).strip()
            group_name = Utils.get_group_name(self.robot, group_id)
            if group_id not in self.robot.config.rev_group:
                self.robot.config.rev_group.append(group_id)
                self.robot.config.save("rev_group", self.robot.config.rev_group)
                self.printf(f"群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}已添加至对接群列表")
            else:
                self.warnf(f"群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}已经在对接群列表中！")
        elif re.search(r"remove\s+(\d+)", argv):
            group_id = re.search(r"\s+(\d+)", argv).group(1).strip()
            group_name = Utils.get_group_name(self.robot, group_id)
            if group_id in self.robot.config.rev_group:
                self.robot.config.rev_group.remove(group_id)
                self.robot.config.save("rev_group", self.robot.config.rev_group)
                self.printf(f"群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}已从对接群列表中移除")
            else:
                self.warnf(f"群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}不在对接群列表中！")
        elif re.search(r"main\s+(\d+)", argv):
            group_id = re.search(r"\s+(\d+)", argv).group(1).strip()
            group_name = Utils.get_group_name(self.robot, group_id)
            if group_id in self.robot.config.rev_group:
                self.robot.config.rev_group.remove(group_id)
            self.robot.config.rev_group.insert(0, group_id)
            self.robot.config.save("rev_group", self.robot.config.rev_group)
            self.printf(f"群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}已设置为主对接群")
        else:
            self.printf(f"请使用 {Fore.CYAN}group add/remove 群号{Fore.RESET} 增加或删除对接群")
            self.printf(f"请使用 {Fore.CYAN}group main 群号{Fore.RESET} 设置主对接群")

    def gmsg(self, argv=""):
        """发送群聊消息"""
        if re.search(r"(\d+)\s+(.+)", argv):
            inputs = re.search(r"(\d+)\s+(.+)", argv).groups()
            group_id = inputs[0]
            group_name = Utils.get_group_name(self.robot, group_id)
            msg = inputs[1]
            result = Utils.send_msg(self.robot, "group", group_id, msg)
            if Utils.status_ok(result):
                self.printf(f"向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}发送消息：{Fore.YELLOW}{msg}")
            else:
                self.warnf(f"向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}发送消息出错！{result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}groupmsg 群号 消息内容{Fore.RESET} 发送消息")

    def gvoice(self, argv=""):
        """发送群语音消息"""
        if re.search(r"(\d+)\s+(.+)", argv):
            inputs = re.search(r"(\d+)\s+(.+)", argv).groups()
            group_id = inputs[0]
            group_name = Utils.get_group_name(self.robot, group_id)
            msg = "[CQ:tts,text=" + inputs[1] + " ]"
            result = Utils.send_msg(self.robot, "group", group_id, msg)
            if Utils.status_ok(result):
                self.printf(f"向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}发送文本转语音消息：{Fore.YELLOW}{inputs[1]}")
            else:
                self.warnf(f"向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}发送文本转语音消息出错！{result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}groupvoice 群号 文本{Fore.RESET} 发送文本转语音")

    def aivoice(self, argv=""):
        """发送AI声聊消息"""
        if re.search(r"(\S+)\s+(\d+)\s?(\S+)?", argv):
            match = re.search(r"(\S+)\s+(\d+)\s?(\S+)?", argv).groups()
            text = match[0]
            group_id = match[1]
            character = match[2] or "lucy-voice-xueling"
            group_name = Utils.get_group_name(self.robot, group_id)
            result = Utils.send_group_ai_record(self.robot, group_id, character, text)
            if Utils.status_ok(result):
                self.printf(f"向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}发送AI声聊消息：{Fore.YELLOW}{match[1]}")
            else:
                self.warnf(f"向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}发送AI声聊消息出错！{result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}aivoice 文本 群号 声色{Fore.RESET} 发送AI声聊")

    def help(self, argv=""):
        """打开帮助菜单"""
        all_page = int(len(self.robot.cmd) / 10 + 1)
        page = 1
        if re.search(r"(\d+)", argv):
            page = sorted([1, int(re.search(r"(\d+)", argv).group(1)), all_page])[1]
        if re.search(r"^(all)$", argv):
            page = 0
        self.printf("============帮助============")
        if page == 0:
            for cmd in list(self.robot.cmd.keys()):
                self.printf(f"{Fore.CYAN}{cmd}{Fore.RESET}：{self.robot.cmd[cmd]}")
        else:
            for cmd in list(self.robot.cmd.keys())[(page - 1) * 10 : page * 10]:
                self.printf(f"{Fore.CYAN}{cmd}{Fore.RESET}：{self.robot.cmd[cmd]}")
            self.printf(f"========第{page}页|共{all_page}页========")

    def history(self, argv=""):
        """查看历史消息"""
        if re.search(r"(\d+)$", argv):
            uid = re.search(r"(\d+)$", argv).group(1)
            if "u" + uid in self.robot.data:
                self.printf(f"与{Fore.MAGENTA}{Utils.get_user_name(self.robot, uid)}{uid}{Fore.RESET}的历史消息:")
                past_msg = self.robot.data["u" + uid].past_message
                for one_msg in past_msg:
                    msg_time = time.strftime("%m-%d %H:%M:%S", time.localtime(one_msg["time"]))
                    msg = one_msg["raw_message"]
                    msg_id = one_msg["message_id"]
                    self.printf(f"[{msg_time}] {Fore.MAGENTA}(msg_id:{msg_id}){Fore.RESET} {msg}")
            else:
                data = Utils.get_group_msg_history(self.robot, uid).get("data")
                if not data:
                    self.printf(f"您未加入群{Fore.MAGENTA}{Utils.get_group_name(self.robot, str(uid))}({uid}){Fore.RESET}")
                    return
                self.printf(f"群{Fore.MAGENTA}{Utils.get_group_name(self.robot, str(uid))}({uid}){Fore.RESET}中的历史消息:")
                past_msg = data.get("messages") 
                for one_msg in past_msg:
                    msg_time = time.strftime("%m-%d %H:%M:%S", time.localtime(one_msg["time"]))
                    msg_id = one_msg["message_id"]
                    name = Utils.get_user_name(self.robot, one_msg["user_id"])
                    msg = one_msg["raw_message"] or "[已撤回]"
                    self.printf(f"[{msg_time} {name}] "
                                f"{Fore.MAGENTA}(msg_id:{msg_id}){Fore.RESET} {msg}")
        elif re.search(r"self", argv):
            if len(self.robot.self_message):
                self.printf("自己发送的历史消息:")
                past_msg = self.robot.self_message
                for one_msg in past_msg:
                    msg_time = time.strftime("%m-%d %H:%M:%S", time.localtime(one_msg["time"]))
                    msg = one_msg["message"]
                    msg_id = one_msg["message_id"]
                    uname = uid = ""
                    if group_id := one_msg.get("group_id"):
                        uid = group_id
                        uname = Utils.get_group_info(self.robot, group_id).get("data").get("group_name")
                    else:
                        uid = one_msg.get("user_id")
                        uname = Utils.get_user_name(self.robot, uid)
                    self.printf(f"[{msg_time} TO {uname}({uid})] "
                                f"{Fore.MAGENTA}(msg_id:{msg_id}){Fore.RESET} {msg}")
            else:
                self.printf("没有自己发送的历史消息")
        else:
            self.printf(f"请使用 {Fore.CYAN}history QQ号/群号/self{Fore.RESET} 获取历史消息")

    def info(self, argv=""): # pylint: disable=unused-argument
        """查看API版本和相关信息"""
        info = get_version_info(self.robot)
        module_count = len(self.robot.modules)
        modules = [i.ID for i in self.robot.modules.values()]
        handler_amount = Utils.get_handler_amount(self.robot)
        image_range = (
            f"({self.robot.config.min_image_width}:{self.robot.config.max_image_width})"
        )
        self.printf("=======API版本信息=======")
        self.printf(f"应用名：{Fore.YELLOW}{info["app_name"]}{Fore.RESET}")
        self.printf(f"版本号：{Fore.YELLOW}{info["app_version"]}{Fore.RESET}")
        self.printf(f"协议版本：{Fore.YELLOW}{info["protocol_version"]}{Fore.RESET}")
        self.printf("==========变量信息==========")
        self.printf(f"调试模式：{Fore.YELLOW}{self.robot.config.is_debug}{Fore.RESET}")
        self.printf(f"静默模式：{Fore.YELLOW}{self.robot.config.is_silence}{Fore.RESET}")
        self.printf(f"心跳包显示：{Fore.YELLOW}{self.robot.config.is_show_heartbeat}{Fore.RESET}")
        self.printf(f"消息回复引用：{Fore.YELLOW}{self.robot.config.is_always_reply}{Fore.RESET}")
        self.printf(f"报错消息反馈：{Fore.YELLOW}{self.robot.config.is_error_reply}{Fore.RESET}")
        self.printf(f"对接机器人：{Fore.YELLOW}{self.robot.self_name}({self.robot.self_id}){Fore.RESET}")
        self.printf(f"管理员列表：{Fore.YELLOW}{self.robot.config.admin_list}{Fore.RESET}")
        self.printf(f"黑名单列表：{Fore.YELLOW}{self.robot.config.blacklist}{Fore.RESET}")
        self.printf(f"对接群列表：{Fore.YELLOW}{self.robot.config.rev_group}{Fore.RESET}")
        self.printf(f"显示全部群消息：{Fore.YELLOW}{self.robot.config.is_show_all_msg}{Fore.RESET}")
        self.printf(f"已安装模块({module_count}个)：{Fore.YELLOW}{modules}{Fore.RESET}")
        self.printf(f"已注册处理函数：{Fore.YELLOW}{handler_amount}{Fore.RESET}")
        self.printf("=========字符画信息=========")
        self.printf(f"显示字符画：{Fore.YELLOW}{self.robot.config.is_show_image}{Fore.RESET}")
        self.printf(f"彩色显示模式：{Fore.YELLOW}{self.robot.config.image_color}{Fore.RESET}")
        self.printf(f"字符画宽度范围：{Fore.YELLOW}{image_range}{Fore.RESET}")
        self.printf("==========临时字典==========")
        self.printf(f"用户字典：{Fore.YELLOW}{self.robot.user_dict}{Fore.RESET}")
        self.printf(f"群字典：{Fore.YELLOW}{self.robot.group_dict}{Fore.RESET}")
        self.printf(f"数据字典：{Fore.YELLOW}{list(self.robot.data.keys())}{Fore.RESET}")
        self.printf("============================")

    def like(self, argv=""):
        """对用户主页点赞"""
        if re.search(r"([^\s]+)\s?(\d+)?", argv):
            inputs = re.search(r"([^\s]+)\s?(\d+)?", argv).groups()
            user_id = inputs[0]
            times = inputs[1] if inputs[1] else 20
            user_name = Utils.get_user_name(self.robot, user_id)
            result = Utils.send_like(self.robot, user_id, times)
            if Utils.status_ok(result):
                self.printf(f"向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}进行{times}次点赞")
            else:
                self.warnf(f"向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}进行{times}次点赞出错！{result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}like 用户QQ (次数){Fore.RESET} 进行点赞")

    def llm(self, argv=""):
        """调用首个配置的大语言模型进行对话"""
        if argv == "":
            self.printf(f"请使用 {Fore.CYAN}llm 文本内容{Fore.RESET} 调用首个配置的大语言模型进行对话(如果可以)")
        elif llm_chat := self.robot.func.get("llm_chat"):
            try:
                self.printf("LLM: ", end="", console=False)
                for chunk in llm_chat(argv, stream=True):
                    self.printf(chunk, end="", console=False, flush=True)
                self.printf("", flush=True)
            except Exception as e:  # pylint: disable=broad-exception-caught
                self.printf(f"LLM调用失败! {e}")
        else:
            self.printf("LLM模块未导入或未启用!")

    def msg(self, argv=""):
        """发送私聊消息"""
        if re.search(r"(\d+)\s+(.+)", argv):
            inputs = re.search(r"(\d+)\s+(.+)", argv).groups()
            user_id = inputs[0]
            user_name = Utils.get_user_name(self.robot, user_id)
            msg = inputs[1]
            result = Utils.send_msg(self.robot, "private", user_id, msg)
            if Utils.status_ok(result):
                self.printf(f"向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}发送消息：{msg}")
            else:
                self.warnf(f"向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}发送消息出错！{result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}msg QQ号 消息内容{Fore.RESET} 发送消息")

    def tmpmsg(self, argv=""):
        """发送临时消息"""
        if re.search(r"(\d+)\s+(\d+)\s+(.+)", argv):
            inputs = re.search(r"(\d+)\s+(\d+)\s+(.+)", argv).groups()
            user_id = inputs[0]
            user_name = Utils.get_user_name(self.robot, user_id)
            group_id = inputs[1]
            group_name = Utils.get_group_name(self.robot, group_id)
            msg = inputs[2]
            result = Utils.send_msg(self.robot, "private", user_id, msg, group_id)
            if Utils.status_ok(result):
                self.printf(f"通过群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}发送消息：{msg}")
            else:
                self.warnf(f"通过群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}发送消息失败！{result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}tmpmsg 群号 QQ号 消息内容{Fore.RESET} 发送群临时消息")

    def notice(self, argv=""):
        """发送群公告"""
        if re.search(r"(\d+)\s+(.+)$", argv):
            inputs = re.search(r"(\d+)\s+(.+)$", argv).groups()
            group_id = inputs[0]
            group_name = Utils.get_group_name(self.robot, group_id)
            notice = inputs[1]
            result = Utils.send_group_notice(self.robot, group_id, notice)
            if Utils.status_ok(result):
                self.printf(f"向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}发布公告：{Fore.YELLOW}{notice}")
            else:
                self.warnf(f"向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}发布公告失败！ {result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}notice 群号 公告{Fore.RESET} 发布公告")

    def ocr(self, argv=""):
        """识别图片中的文字"""
        if re.search(r"(\d+)", argv):
            img_id = re.search(r"(.+)", argv).group(1)
            result = Utils.ocr_image(self.robot, img_id)
            if Utils.status_ok(result):
                result = result["data"]
                self.printf(f"图片{Fore.MAGENTA}({img_id}){Fore.RESET}识别结果")
                self.printf(f"文字语音：{result["language"]}")
                self.printf("-------------------------------------")
                for i in result["texts"]:
                    self.printf(f"文字内容：{i["text"]}")
                    self.printf(f"结果置信度：{i["confidence"]}%")
                    self.printf("-------------------------------------")
            else:
                self.printf(f"调用OCR失败！结果为：{result}")
        else:
            self.printf(f"未识别到文字！请使用 {Fore.CYAN}ocr 图片ID{Fore.RESET} 识别图片内文字(图片ID即为[CQ:image,file=XXX]中的XXX)")

    def op(self, argv=""): # pylint: disable=unused-argument
        """增加管理员权限"""
        if re.search(r"(\d+)", argv):
            user_id = re.search(r"(\d+)", argv).group(1)
            user_name = Utils.get_user_name(self.robot, user_id)
            if user_id not in self.robot.config.admin_list:
                self.robot.config.admin_list.append(user_id)
                self.robot.config.save("admin_list", self.robot.config.admin_list)
                self.printf(f"{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}已设置为管理员")
            else:
                self.warnf(f"{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}已经是管理员！")
        else:
            self.printf(f"请使用 {Fore.CYAN}op 用户QQ{Fore.RESET} 设置管理员")

    def read(self, argv=""):
        """读取转发消息内容"""
        if re.search(r"(.+)", argv):
            msg_id = re.search(r"(.+)", argv).group(1)
            data = Utils.get_forward_msg(self.robot, msg_id).get("data", {})
            if data:
                group_id = 0
                msg_list = data.get("messages")
                for msg in msg_list:
                    if group_id != msg["group_id"]:
                        group_id = msg["group_id"]
                        self.printf(f"{Fore.MAGENTA}{Fore.RESET}转发自群{Fore.MAGENTA}{Utils.get_group_name(self.robot, group_id)}({group_id}){Fore.RESET}中的消息")
                    msg_time = time.strftime("%m-%d %H:%M:%S", time.localtime(msg["time"]))
                    content = msg["raw_message"]
                    name = msg["sender"]["nickname"]
                    self.printf(f"[{msg_time} {Fore.MAGENTA}{name}{Fore.RESET}] {Fore.MAGENTA}{Fore.RESET} {Fore.YELLOW}{content}{Fore.RESET}")
            elif message := data.get("message"):
                self.printf(message)
            else:
                self.printf("读取转发消息失败或仅支持读取群聊转发")
        else:
            self.printf(f"请使用 {Fore.CYAN}read message_id{Fore.RESET} 读取转发消息")

    def poke(self, argv=""):
        """戳一戳用户(默认上一条消息的发送者)"""
        if re.search(r"(.+)", argv):
            user_id = re.search(r"(.+)", argv).group(1)
            user_name = Utils.get_user_name(self.robot, user_id)
            result = Utils.poke(self.robot, user_id)
            if Utils.status_ok(result):
                self.printf(f"向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}发送戳一戳")
            else:
                self.warnf(f"向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}发送戳一戳出错！{result.get("message")}")
        elif (
            self.robot.latest_data
            and self.robot.data[self.robot.latest_data].past_message[-1]
        ):
            rev = self.robot.data[self.robot.latest_data].past_message[-1]
            user_id = rev.get("user_id")
            user_name = rev.get("sender", {}).get("nickname")
            group_id = rev.get("group_id")
            result = Utils.poke(self.robot, user_id, group_id)
            if Utils.status_ok(result):
                self.printf(f"向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}发送戳一戳")
            else:
                self.warnf(f"贴表情出错 {result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}poke 用户QQ{Fore.RESET} 发送戳一戳")

    def recall(self, argv=""):
        """撤回消息(默认撤回上一条自己发送的消息)"""
        if re.search(r"(.+)", argv):
            msg_id = re.search(r"(.+)", argv).group(1)
            msg = ""
            past_msg = self.robot.self_message
            for one_msg in past_msg:
                if msg_id == str(one_msg["message_id"]):
                    msg = one_msg["message"]
                    break
            result = Utils.del_msg(self.robot, msg_id)
            if Utils.status_ok(result):
                self.printf(f"撤回消息{Fore.MAGENTA}(message_id:{msg_id}) {Fore.YELLOW}{msg}{Fore.RESET}成功！")
            else:
                self.warnf(f"撤回消息{Fore.MAGENTA}(message_id:{msg_id}) {Fore.YELLOW}{msg}{Fore.RESET}出错！")
        elif len(self.robot.self_message):
            rev = self.robot.self_message[-1]
            msg_id = rev["message_id"]
            msg = rev["message"]
            result = Utils.del_msg(self.robot, msg_id)
            if Utils.status_ok(result):
                self.robot.self_message.pop()
                self.printf(f"撤回消息{Fore.MAGENTA}{msg}{Fore.RESET}成功！(使用 {Fore.CYAN}history self{Fore.RESET} 查看其他历史消息)")
            else:
                self.printf(f"撤回消息{Fore.MAGENTA}{msg}{Fore.RESET}出错！(reply消息不支持快捷撤回)")
        else:
            self.warnf(f"未寻找到上一条可撤回的信息记录！请使用 {Fore.CYAN}recall (消息ID){Fore.RESET} 快速撤回或撤回指定消息")

    def restart(self, argv=""):
        """重启程序"""
        if argv == "":
            self.printf("正在重启程序...")
            self.robot.restart()
        else:
            self.printf(f"请使用 {Fore.CYAN}restart{Fore.RESET} 重启本程序")

    def reload(self, argv=""):
        """重载插件"""
        if argv.strip():
            self.robot.reload_plugin(argv.strip())
        else:
            self.printf(f"请使用 {Fore.CYAN}reload 插件ID/all{Fore.RESET} 重载插件")

    def load(self, argv=""):
        """加载插件"""
        if argv.strip():
            self.robot.load_plugin(argv.strip())
        else:
            self.printf(f"请使用 {Fore.CYAN}load 插件ID/文件名{Fore.RESET} 加载插件")

    def unload(self, argv=""):
        """卸载插件"""
        if argv.strip():
            self.robot.unload_plugin(argv.strip())
        else:
            self.printf(f"请使用 {Fore.CYAN}unload 插件ID{Fore.RESET} 卸载插件")

    def disable(self, argv=""):
        """禁用插件"""
        if argv.strip():
            self.robot.disable_plugin(argv.strip())
        else:
            self.printf(f"请使用 {Fore.CYAN}disable 插件ID{Fore.RESET} 禁用插件")

    def enable(self, argv=""):
        """启用插件"""
        if argv.strip():
            self.robot.enable_plugin(argv.strip())
        else:
            self.printf(f"请使用 {Fore.CYAN}enable 插件ID{Fore.RESET} 启用插件")

    def reply(self, argv=""):
        """回复上一条消息"""
        if (
            len(self.robot.data) == 0
            or self.robot.latest_data not in self.robot.data
            or len(self.robot.data[self.robot.latest_data].past_message) == 0
        ):
            self.warnf("未寻找到上一条信息记录！")
        elif re.search(r"(.+)", argv):
            rev = self.robot.data[self.robot.latest_data].past_message[-1]
            msg_id = rev.get("message_id")
            user_id = rev.get("user_id")
            user_name = Utils.get_user_name(self.robot, user_id)
            group_id = rev.get("group_id")
            group_name = Utils.get_group_name(self.robot, group_id)
            reply_msg = re.search(r"(.+)", argv).group(1)
            reply_msg = f"[CQ:reply,id={msg_id}]{reply_msg}"
            reply_type = "group" if group_id else "private"
            target_id = group_id if group_id else user_id
            result = Utils.reply_id(self.robot, reply_type, target_id, reply_msg)
            if Utils.status_ok(result):
                target_str = f"{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}"
                if group_id:
                    target_str = (
                        f"群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}内"
                        + target_str
                    )
                self.printf(f"回复 -> {target_str}: {Fore.MAGENTA}{reply_msg}{Fore.RESET}")
            else:
                self.warnf(f"回复消息出错！{result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}reply 消息内容{Fore.RESET} 回复上一段信息")

    def qreply(self, argv=""):
        """快捷回复上一条消息"""
        if (
            len(self.robot.data) == 0
            or self.robot.latest_data not in self.robot.data
            or len(self.robot.data[self.robot.latest_data].past_message) == 0
        ):
            self.warnf("未寻找到上一条信息记录！")
        elif re.search(r"(.+)", argv):
            rev = self.robot.data[self.robot.latest_data].past_message[-1]
            user_id = rev["user_id"]
            user_name = Utils.get_user_name(self.robot, user_id)
            group_id = rev["group_id"]
            group_name = Utils.get_group_name(self.robot, group_id)
            reply_msg = re.search(r"(.+)", argv).group(1)
            result = Utils.quick_reply(self.robot, rev, reply_msg)
            if Utils.status_ok(result):
                target_str = f"{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}"
                if group_id:
                    target_str = (
                        f"群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}内"
                        + target_str
                    )
                self.printf(f"回复 -> {target_str}: {Fore.MAGENTA}{reply_msg}{Fore.RESET}")
            else:
                self.warnf(f"回复消息出错！{result}")
        else:
            self.printf(f"请使用 {Fore.CYAN}reply 消息内容{Fore.RESET} 回复上一段信息")

    def request(self, argv=""):
        """向API发送请求"""
        if re.search(r"(get|GET)\s+(.+)", argv):
            url = re.search(r"\s+(.+)", argv).group(1)
            result = get(self.robot, url)
            if Utils.status_ok(result):
                self.printf(f"GET请求发送成功，返回为{Fore.YELLOW}{result}{Fore.RESET}")
            else:
                self.warnf(f"GET发送请求出错，返回为{Fore.YELLOW}{result}{Fore.RESET}")
        elif re.search(r"(post|POST)\s+(\S+)\s+(.+)", argv):
            temp = re.search(r"(post|POST)\s+(\S+)\s+(.+)", argv).groups()[1:]
            url = temp[0]
            data = temp[1]
            result = post(self.robot, url, json.loads(data))
            if Utils.status_ok(result):
                self.printf(f"POST请求发送成功，返回为{Fore.YELLOW}{result}{Fore.RESET}")
            elif result != {}:
                self.warnf(f"POST发送请求出错，返回为{Fore.YELLOW}{result}{Fore.RESET}")
        else:
            self.printf(
                f"请使用 {Fore.CYAN}request [GET/POST] 请求API (POST请求体){Fore.RESET} "
                + "向API发送请求(参考https://napneko.github.io/develop/api/doc)"
            )

    def say(self, argv=""):
        """快捷向主对接群发送消息"""
        if self.robot.config.rev_group:
            if re.search(r"(.+)", argv):
                msg = re.search(r"(.+)", argv).group(1)
                group_id = self.robot.config.rev_group[0]
                group_name = Utils.get_group_name(self.robot, group_id)
                result = Utils.send_msg(self.robot, "group", group_id, msg)
                if Utils.status_ok(result):
                    self.printf(f"向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}发送消息：{msg}")
                else:
                    self.warnf(f"向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}发送消息出错！{result.get("message")}")
            else:
                self.printf(f"请使用 {Fore.CYAN}say 消息内容{Fore.RESET} 向主对接群发送消息")
        else:
            self.printf(f"请使用 {Fore.CYAN}group main 群号{Fore.RESET} 设置主对接群")

    def set(self, argv=""):
        """设置机器人相关信息和配置"""
        if re.search(r"self\s+(\S+)$", argv):
            if re.search(r"self\s+(\d+)$", argv):
                self.robot.self_id = re.search(r"self\s(\d+)$", argv).group(1)
                self.robot.self_name = Utils.get_user_name(self.robot, self.robot.self_id)
                self.robot.at_info = "[CQ:at,qq=" + str(self.robot.self_id) + "]"
                self.printf(f"设置机器人为{Fore.MAGENTA}{self.robot.self_name}({self.robot.self_id}){Fore.RESET}成功")
            elif re.search(r"self\s+auto$", argv):
                result = get_login_info(self.robot)
                self.robot.self_name = result["data"]["nickname"]
                self.robot.self_id = result["data"]["user_id"]
                self.robot.at_info = "[CQ:at,qq=" + str(self.robot.self_id) + "]"
                self.printf(f"自动设置机器人为{Fore.MAGENTA}{self.robot.self_name}({self.robot.self_id}){Fore.RESET}成功")
            else:
                self.printf(f"请使用 {Fore.CYAN}set self QQ号/auto{Fore.RESET} 设置/自动设置机器人QQ")
        elif re.search(r"show", argv):
            if re.search(r"show\s+all", argv):
                self.robot.config.is_show_all_msg = True
                self.robot.config.save(
                    "is_show_all_msg", self.robot.config.is_show_all_msg
                )
                self.printf("设置显示所有信息成功")
            elif re.search(r"show\s+brief", argv):
                self.robot.config.is_show_all_msg = False
                self.robot.config.save(
                    "is_show_all_msg", self.robot.config.is_show_all_msg
                )
                self.printf("设置仅显示私聊与群@信息成功")
            else:
                self.printf(f"请使用 {Fore.CYAN}set show all/brief{Fore.RESET} 设置显示信息类别")
        elif re.search(r"heartbeat", argv):
            if re.search(r"(true|True)", argv):
                self.robot.config.is_show_heartbeat = True
            elif re.search(r"(false|False)", argv):
                self.robot.config.is_show_heartbeat = False
            else:
                self.robot.config.is_show_heartbeat = not self.robot.config.is_show_heartbeat
            self.robot.config.save("is_show_heartbeat", self.robot.config.is_show_heartbeat)
            if self.robot.config.is_show_heartbeat:
                self.warnf("心跳包接收显示已开启")
            else:
                self.warnf("心跳包接收显示已关闭")
        elif re.search(r"error_reply", argv):
            if re.search(r"(true|True)", argv):
                self.robot.config.is_error_reply = True
            elif re.search(r"(false|False)", argv):
                self.robot.config.is_error_reply = False
            else:
                self.robot.config.is_error_reply = not self.robot.config.is_error_reply
            self.robot.config.save("is_error_reply", self.robot.config.is_error_reply)
            if self.robot.config.is_error_reply:
                self.warnf("报错消息反馈已开启")
            else:
                self.warnf("报错消息反馈已关闭")
        elif re.search(r"reply", argv):
            if re.search(r"(true|True)", argv):
                self.robot.config.is_always_reply = True
            elif re.search(r"(false|False)", argv):
                self.robot.config.is_always_reply = False
            else:
                self.robot.config.is_always_reply = (
                    not self.robot.config.is_always_reply
                )
            self.robot.config.save("is_always_reply", self.robot.config.is_always_reply)
            if self.robot.config.is_always_reply:
                self.warnf("消息回复强制引用原文已开启")
            else:
                self.warnf("消息回复强制引用已关闭")
        elif re.search(r"image\s+color", argv):
            if re.search(r"(false|null|disabled|no)", argv, re.IGNORECASE):
                self.robot.config.image_color = "disabled"
                self.warnf("彩色显示模式已关闭")
            elif re.search(r"(braille)", argv, re.IGNORECASE):
                self.robot.config.image_color = "braille"
            elif re.search(r"(gray)", argv, re.IGNORECASE):
                self.robot.config.image_color = "gray"
            elif re.search(r"(colorama|8|standard|colorama\(8\))", argv, re.IGNORECASE):
                self.robot.config.image_color = "colorama"
            elif re.search(r"(ansi_256|ansi256|ansi 256|ansi|256|ansi\(256\))", argv, re.IGNORECASE):
                self.robot.config.image_color = "ansi_256"
            elif re.search(r"(true_color|truecolor|true color)", argv, re.IGNORECASE):
                self.robot.config.image_color = "true_color"
            else:
                self.warnf("图片显示模式: disabled, braille, gray, colorama(8), ansi(256), true_color")
                return
            self.robot.config.save("image_color", self.robot.config.image_color)
            if self.robot.config.image_color != "disabled":
                self.warnf(f"彩色显示模式已切换为{self.robot.config.image_color}")
        elif re.search(r"image\s+minsize", argv):
            if re.search(r"minsize\s+(\d+)", argv):
                self.robot.config.min_image_width = sorted(
                    [
                        10,
                        int(re.search(r"minsize\s+(\d+)", argv).group(1)),
                        self.robot.config.max_image_width,
                    ]
                )[1]
            self.robot.config.save("min_image_width", self.robot.config.min_image_width)
            self.printf(f"图片字符画最小宽度已设置为{self.robot.config.min_image_width}")
        elif re.search(r"image\s+maxsize", argv):
            if re.search(r"size\s+(\d+)", argv):
                self.robot.config.max_image_width = sorted(
                    [
                        self.robot.config.min_image_width,
                        int(re.search(r"size\s(\d+)", argv).group(1)),
                        1000,
                    ]
                )[1]
            self.robot.config.save("max_image_width", self.robot.config.max_image_width)
            self.printf(f"图片字符画最大宽度已设置为{self.robot.config.max_image_width}")
        elif re.search(r"image\s+size", argv):
            if re.search(r"(\d+)[^\d](\d+)", argv):
                size = re.search(r"(\d+)[^\d](\d+)", argv).groups()
                self.robot.config.min_image_width = sorted(
                    [10, int(size[0]), int(size[1]), 1000]
                )[1]
                self.robot.config.max_image_width = sorted(
                    [10, int(size[0]), int(size[1]), 1000]
                )[2]
            elif re.search(r"\s+(\d+)", argv):
                size = int(re.search(r"\s(\d+)", argv).group(1))
                self.robot.config.min_image_width = sorted([10, size, 1000])[1]
                self.robot.config.max_image_width = self.robot.config.min_image_width
            self.robot.config.save("min_image_width", self.robot.config.min_image_width)
            self.robot.config.save("max_image_width", self.robot.config.max_image_width)
            self.printf(f"图片字符画大小已设置为({self.robot.config.min_image_width}:{self.robot.config.max_image_width})")
        elif re.search(r"image", argv):
            if re.search(r"(true|True)", argv):
                self.robot.config.is_show_image = True
            elif re.search(r"(false|False)", argv):
                self.robot.config.is_show_image = False
            else:
                self.robot.config.is_show_image = not self.robot.config.is_show_image
            self.robot.config.save("is_show_image", self.robot.config.is_show_image)
            if self.robot.config.is_show_image:
                self.warnf("图片字符画显示已开启")
            else:
                self.warnf("图片字符画显示已关闭")
        else:
            self.printf("==========设置列表==========")
            self.printf(f"{Fore.CYAN}set self QQ号/auto{Fore.RESET} 设置机器人QQ号(用于辨别@信息)")
            self.printf(f"{Fore.CYAN}set show all/brief{Fore.RESET} 设置显示信息类别")
            self.printf(f"{Fore.CYAN}set image (true/false){Fore.RESET} 设置图片字符画显示")
            self.printf(f"{Fore.CYAN}set image color {Fore.RESET} 设置彩色图片显示")
            self.printf(f"{Fore.CYAN}set image minsize/maxsize 数字{Fore.RESET} 设置图片字符画最小/最大宽度")
            self.printf(f"{Fore.CYAN}set image size 数字 数字{Fore.RESET} 设置图片字符画最小/最大宽度")
            self.printf(f"{Fore.CYAN}set heartbeat (true/false){Fore.RESET} 设置接收心跳包是否显示")
            self.printf(f"{Fore.CYAN}set reply (true/false){Fore.RESET} 设置回复消息是否强制引用原文")
            self.printf(f"{Fore.CYAN}set error_reply (true/false){Fore.RESET} 设置报错消息是否在QQ反馈")

    def silence(self, argv=""): # pylint: disable=unused-argument
        """切换静默模式（开启后将不再输出任何消息到控制台，除非是错误消息且error_reply开启）"""
        self.robot.config.is_silence = not self.robot.config.is_silence
        self.robot.config.save("is_silence", self.robot.config.is_silence)
        if self.robot.config.is_silence:
            self.warnf("静默模式已开启")
        else:
            self.warnf("静默模式已关闭")

    def sign(self, argv=""):
        """群签到"""
        if re.search(r"(\d+)", argv):
            group_id = re.search(r"(\d+)", argv).group(1)
            group_name = Utils.get_group_name(self.robot, group_id)
            if not group_name:
                self.warnf(f"未查找到群聊ID: {group_id}")
                return
            result = Utils.group_sign(self.robot, group_id)
            if Utils.status_ok(result):
                self.printf(f"向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}进行打卡签到")
            else:
                self.warnf(f"群签到出错 {result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}sign 群聊ID{Fore.RESET} 进行群签到")

    def stop(self, argv=""): # pylint: disable=unused-argument
        """关闭程序"""
        self.printf("正在关闭程序...")
        self.robot.stop()

    def test(self, argv=""):
        """测试指令，用于测试API调用和错误反馈等功能"""
        if re.search(r"(错误|error|ERROR)", argv):
            raise RuntimeError("手动触发了一个运行时错误")
        else:
            thing = re.search(r"(.*)", argv).group(1)
            if not thing:
                thing = "测试"
            msg = f"{thing} OK!"
        self.printf(msg)

    def voice(self, argv=""):
        """发送文本转语音消息"""
        if re.search(r"(\d+)\s+(.+)", argv):
            inputs = re.search(r"(\d+)\s+(.+)", argv).groups()
            user_id = inputs[0]
            user_name = Utils.get_user_name(self.robot, user_id)
            msg = "[CQ:tts,text=" + inputs[1] + " ]"
            result = Utils.send_msg(self.robot, "private", user_id, msg)
            if Utils.status_ok(result):
                self.printf(f"向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}发送文本转语音消息：{inputs[1]}")
            else:
                self.warnf(f"向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}发送文本转语音消息出错！{result.get("message")}")
        else:
            self.printf(f"请使用 {Fore.CYAN}voice QQ号 文本{Fore.RESET} 发送文本转语音消息")

    def unknown(self, argv=""): # pylint: disable=unused-argument
        """未知指令"""
        self.warnf(f"未知指令！请输入 {Fore.CYAN}help{Fore.RESET} 获取帮助！")

    def printf(self, msg, end="\n", console=True, flush=False):
        """
        向控制台输出通知级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        if not flush:
            msg = f"{Fore.CYAN}[CMD]{Fore.RESET} {msg}"
        self.robot.printf(msg=msg, end=end, console=console, flush=flush)

    def warnf(self, msg, end="\n", console=True):
        """
        向控制台输出警告级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        self.robot.warnf(
            f"{Fore.CYAN}[CMD]{Fore.RESET} {msg}", end=end, console=console
        )

    def errorf(self, msg, end="\n", console=True):
        """
        向控制台输出错误级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        self.robot.errorf(f"{Fore.CYAN}[CMD]{Fore.RED} {msg}", end=end, console=console)
