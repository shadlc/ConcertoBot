"""简单API模块"""

from urllib.parse import quote

import httpx

from src.base import Module
from src.utils import Utils

class API(Module):
    """简单API模块"""
    ID = "API"
    NAME = "简单API模块"
    HELP = {
        0: [
            "简单API模块，存放各种方便简单调用的API",
        ],
        1: [
            "(开启|关闭)简单API | 开关本模块",
        ],
        2: [
            "(平台)今日热榜 | 如微博今日热榜、原神今日热榜 -- PearAPI",
            "金价 | 查询今日黄金价格 -- PearAPI",
        ],
    }

    @Utils.handler(lambda self: self.group_at() and self.au(2)
         and self.match(r"^(\S{2,16})(热搜|热榜|trending|hot)$"))
    def trending(self):
        """今日热榜"""
        platform = self.match(r"^(\S{2,16})(热搜|热榜|trending|hot)$").group(1)
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            url = f"https://api.pearktrue.cn/api/dailyhot/?title={quote(platform)}"
            resp = httpx.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 200:
                return self.reply(f"今日热榜请求失败: {data.get("msg")}", reply=True)
            msg = ""
            title = data.get("title", "") + data.get("type", "")
            for idx, item in enumerate(data.get("data")):
                msg += f"\n{idx + 1}. " + item.get("title")
            self.reply_forward(self.node(msg.strip()), title)
        except Exception as e: # pylint: disable=broad-exception-caught
            return self.reply_forward(self.node(f"{e}"), source="今日热榜请求失败")

    @Utils.handler(lambda self: self.group_at() and self.au(2)
         and self.match(r"^(金价|黄金价格)$"))
    def gold_price(self):
        """今日黄金价格"""
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            url = "https://api.pearktrue.cn/api/goldprice/"
            resp = httpx.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 200:
                return self.reply(f"今日热榜请求失败: {data.get("msg")}", reply=True)
            msg = ""
            for _, item in enumerate(data.get("data")):
                if item.get("title") == "AU9999":
                    price = item.get("price")
                    opening= item.get("openingprice")
                    max_price = item.get("maxprice")
                    min_price = item.get("minprice")
                    percent = item.get("changepercent")
                    msg += f"当前金价: {price}\n"
                    msg += f"开盘价: {opening}\n"
                    msg += f"最高价: {max_price}\n"
                    msg += f"最低价: {min_price}\n"
                    msg += f"今日涨跌: {percent}%\n"
            if not msg:
                return self.reply("今日黄金价格获取为空", reply=True)
            self.reply_forward(self.node(msg.strip()), "今日金价")
        except Exception as e: # pylint: disable=broad-exception-caught
            return self.reply_forward(self.node(f"{e}"), source="今日热榜请求失败")

    @Utils.handler(lambda self: self.group_at() and self.au(1)
         and self.match(r"^(开启|打开|启用|允许|关闭|禁止|取消)?简单API$"))
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
        msg = f"简单API已{text}"
        self.conv_config["enable"] = flag
        self.save_config()
        self.reply(msg, reply=True)
