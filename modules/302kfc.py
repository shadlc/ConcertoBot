"""疯狂星期四模块"""

from datetime import datetime
import random

import httpx

from src.utils import Module, handler, listener

class KFC(Module):
    """疯狂星期四模块"""
    ID = "KFC"
    NAME = "疯狂星期四模块"
    HELP = {
        0: [
            "疯狂星期四模块，每周四有概率触发疯狂星期四彩蛋",
        ],
        1: [
            "(开启|关闭)疯狂星期四 | 开启或关闭本模块功能",
        ],
        2: [
            "(KFC|kfc) | 发送疯狂星期四语句",
        ],
    }
    CONV_CONFIG = {
        "enable": False,
        "probability": 0.01,
        "last_date": "",
    }

    @listener(lambda self: self.au(2)
         and self.conv_config["enable"]
         and datetime.now().weekday() == 3
         and self.conv_config.get("last_date", "") != datetime.now().strftime("%Y%m%d")
         and random.random() < self.conv_config.get("probability", 0.01)
         and self.match(r".*"))
    def prob_kfc(self):
        """概率触发疯狂星期四彩蛋"""
        try:
            resp = httpx.get("https://api.pearktrue.cn/api/kfc")
            if resp.status_code != 200:
                return
            msg = resp.text.replace(r"\\n", "\n")
            self.reply(msg)
            self.conv_config["last_date"] = datetime.now().strftime("%Y%m%d")
            self.save_config()
        except Exception as e: # pylint: disable=broad-exception-caught
            self.errorf(f"KFC模块请求失败: {e}")

    @handler(lambda self: self.group_at() and self.au(2)
         and self.match(r"^(KFC|kfc)$"))
    def kfc(self):
        """疯狂星期四语句"""
        try:
            resp = httpx.get("https://api.pearktrue.cn/api/kfc")
            if resp.status_code != 200:
                return self.reply(f"KFC模块请求失败: 状态码 {resp.status_code}", reply=True)
            msg = resp.text.replace(r"\\n", "\n")
            self.reply(msg, reply=True)
        except Exception as e: # pylint: disable=broad-exception-caught
            return self.reply_forward(self.node(f"{e}"), source="KFC模块请求失败")

    @handler(lambda self: self.group_at() and self.au(1)
         and self.match(r"^(开启|打开|启用|允许|关闭|禁止|取消)?疯狂星期四$"))
    def toggle(self):
        """开启关闭模块"""
        flag = self.conv_config["enable"]
        text = "开启" if self.conv_config["enable"] else "关闭"
        if self.match(r"(开启|打开|启用|允许)"):
            flag = True
            text = "开启"
        elif self.match(r"(关闭|禁止|取消)"):
            flag = False
            text = "关闭"
        msg = f"疯狂星期四已{text}"
        self.conv_config["enable"] = flag
        self.save_config()
        self.reply(msg, reply=True)
