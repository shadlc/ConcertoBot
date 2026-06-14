"""复读机模块"""

import random
import re

from colorama import Fore
from src.base import Module
from src.utils import Utils


class Repeater(Module):
    """复读机模块"""

    ID = "Repeater"
    NAME = "复读机模块"
    HELP = {
        0: [
            "顾名思义，会以一定概率复读重复说过的话，短时间不会重复复读",
        ],
        1: [
            "[开启|关闭]复读机 | 打开或关闭复读机",
            "禁止复读[关键词] | 不再对指定关键词进行复读",
        ],
    }
    GLOBAL_CONFIG = {}
    CONV_CONFIG = {
        "repeat": True,
        "exclude": [],
    }

    @Utils.listener(lambda self: not self.at_or_private() and self.au(2)
        and self.conv_config["repeat"]
        and self.event.text not in self.conv_config.get("exclude")
        and re.sub(r",url=.*?]","]", str(self.data.past_message)).count(f"'message': '{self.event.msg}") > 1
        and self.event.msg != "")
    def repeat(self):
        """复读机"""
        self.printf(f"在群{Fore.MAGENTA}{self.event.group_name}({self.event.group_id}){Fore.RESET}检测到来自"
                    f"{Fore.MAGENTA}{self.event.user_name}({self.event.user_id}){Fore.RESET}的多次复读：{self.event.msg}")
        if str(self.data.past_message).count("#Already_Repeat"):
            self.printf("近期该群已复读，不再进行复读")
        elif self.event.msg in [i["message"] for i in self.robot.self_message]:
            self.printf("近期已有相同发言，不再进行复读")
        elif self.match(r"请使用QQ最新版本"):
            self.printf("该消息包含无效消息，不进行复读")
        else:
            def get_chance(repeat_count=2):
                """根据连续复读次数计算本次跟读概率"""
                p0 = 0.4 # 初始概率
                return 1 - (1 - p0) * (0.5 ** (repeat_count - 2))

            repeat_count = re.sub(r",url=.*?]","]", str(self.data.past_message)).count(f"'message': '{self.event.msg}")
            chance = get_chance(repeat_count)
            if random.random() < chance:
                self.data.past_message[-1]["message"] += "#Already_Repeat"
                msg = self.event.msg
                self.reply(msg)
                self.printf(f"本次概率{f"{round(chance*100,2)}%"}, 复读成功")
                if notify_maisaka := self.robot.func.get("notify_maisaka"):
                    notify_maisaka(msg, self.owner_id[1:])
            else:
                self.printf(f"本次概率{f"{round(chance*100,2)}%"}, 复读失败")

    @Utils.handler(lambda self: self.group_at() and self.au(1) and self.match(r"^(开启|打开|启用|允许|关闭|禁止|不允许|取消)?复读机$"))
    def toggle(self):
        """设置复读机"""
        flag = self.conv_config["repeat"]
        text = "开启" if self.conv_config["repeat"] else "关闭"
        if self.match(r"(开启|打开|启用|允许)"):
            flag = True
            text = "开启"
        elif self.match(r"(关闭|禁止|不允许|取消)"):
            flag = False
            text = "关闭"
        msg = f"复读机功能已{text}"
        self.conv_config["repeat"] = flag
        self.save_config()
        self.reply(msg)

    @Utils.handler(lambda self: self.group_at() and self.au(2) and self.match(r"^(不|禁止)?(复读|复读)\s+(\S+)$"))
    def exclude(self):
        """复读排除"""
        text = self.match(r"^(不|禁止)?(复读|复读)\s+(\S+)$").group(3)
        if self.match(r"(不|禁止)"):
            self.conv_config["exclude"].append(text)
            self.save_config()
            msg = f"成功将[{text}]添加到复读屏蔽词中!"
            self.reply(msg)
        else:
            if text in self.conv_config["exclude"]:
                self.reply(f"[{text}]从未在复读屏蔽词中存在!")
            else:
                self.conv_config["exclude"].remove(text)
                msg = f"成功将[{text}]从复读屏蔽词中移除!"
                self.reply(msg)
        self.printf(f"会话[{self.owner_id}]{msg}")
