"""抽老婆模块"""
import base64
import html
import imghdr # pylint: disable=deprecated-module
import os
import random
import datetime
import re
import traceback

import httpx
from src.base import Module
from src.utils import Utils

class Waifu(Module):
    """抽老婆模块"""
    ID = "Waifu"
    NAME = "抽老婆模块"
    HELP = {
        0: [
            "抽老婆模块，无需使用@，直接发送关键词即可",
        ],
        1: [
            "(开启|关闭)抽老婆 | 开启或关闭本模块功能(需要@)",
        ],
        2: [
            "抽老婆 | 看看今天的二次元老婆是谁",
            "添老婆+老婆名+图片 | 添加老婆",
            "删老婆+老婆名 | 删除老婆",
            "查老婆@某人 | 查询别人老婆",
            "查老婆+老婆名 | 查询老婆是否存在",
        ],
    }
    GLOBAL_CONFIG = {
        "pic_path": "waifu",
    }
    CONV_CONFIG = {
        "enable": True,
        "add_auth": 1,
        "waifu": {}
    }

    def premise(self):
        return self.group_at() or self.conv_config.get("enable")

    @Utils.handler(lambda self: self.group_at() and self.au(1) and self.match(r"^(开启|打开|启用|允许|关闭|禁止|不允许|取消)?抽老婆$"))
    def toggle(self):
        """设置抽老婆"""
        flag = self.conv_config["enable"]
        text = "开启" if self.conv_config["enable"] else "关闭"
        if self.match(r"(开启|打开|启用|允许)"):
            flag = True
            text = "开启"
        elif self.match(r"(关闭|禁止|不允许|取消)"):
            flag = False
            text = "关闭"
        msg = f"抽老婆功能已{text}"
        self.conv_config["enable"] = flag
        self.save_config()
        self.reply(msg, reply=True)

    def get_today_waifus(self):
        """获取今天已分配的老婆列表"""
        today = datetime.date.today().strftime("%Y%m%d")
        waifu_data = self.conv_config["waifu"]

        # 从waifu数据中筛选出今天分配的老婆
        today_waifus = []
        for waifu_name, date in waifu_data.values():
            if date == today:
                today_waifus.append(waifu_name)

        return today_waifus

    def get_available_waifus(self):
        """获取今天可用的老婆列表"""
        pic_path = self.get_path()
        exts = (".jpg", ".jpeg", ".png")
        files = [f for f in os.listdir(pic_path) if f.lower().endswith(exts)]

        if not files:
            return []

        # 排除今天已经分配过的老婆
        today_waifus = self.get_today_waifus()
        available_waifus = [f for f in files if f not in today_waifus]

        return available_waifus

    @Utils.handler(lambda self: self.au(2) and self.conv_config.get("enable") and self.match(r"^抽取?老婆$"))
    def draw_waifu(self):
        """抽取二次元老婆"""
        today = datetime.date.today().strftime("%Y%m%d")
        user_id = self.event.user_id
        config = self.conv_config
        waifu = None

        # 检查用户今天是否已经抽过老婆
        if user_id in config["waifu"]:
            waifu_name, data_date = config["waifu"][user_id]
            if data_date == today:
                waifu = waifu_name

        if waifu is None:
            # 从可用老婆中随机选择
            available_waifus = self.get_available_waifus()
            if not available_waifus:
                return self.reply("今天的老婆已经被抽光啦，明天再来吧!", reply=True)

            waifu = random.choice(available_waifus)
            # 记录用户今天的老婆
            config["waifu"][user_id] = [waifu, today]
            self.save_config()

        waifu_name = waifu.split(".")[0]
        waifu_img = self.get_waifu_file(waifu)
        result = self.reply(f"你今天的二次元老婆是{waifu_name}哒~\n[CQ:image,file=base64://{waifu_img}]", reply=True)
        if not Utils.status_ok(result):
            qq_url = Utils.get_img_url(self.robot, f"base64://{waifu_img}")
            self.reply(f"你今天的二次元老婆是{waifu_name}哒~\n{qq_url}", reply=True)

    @Utils.handler(lambda self: self.au(2) and self.conv_config.get("enable") and self.match(r"^查寻?老婆"))
    def check_waifu(self):
        """查询二次元老婆"""
        today = datetime.date.today().strftime("%Y%m%d")
        user_id = self.event.user_id

        # 检查是否是查询用户老婆
        if match := re.search(r"\[CQ:at,qq=(.*?)\]", self.event.msg):
            user_id = match.group(1)
            user_name = Utils.get_user_name(self.robot, user_id)
            waives = self.conv_config["waifu"]
            waifu = None

            if user_id in waives:
                waifu_name, data_date = waives[user_id]
                if data_date == today:
                    waifu = waifu_name
                else:
                    return self.reply(f"{user_name}的老婆已过期!", reply=True)

            if waifu is None:
                return self.reply(f"未找到{user_name}的老婆信息!", reply=True)

            waifu_name = waifu.split(".")[0]
            waifu_img = self.get_waifu_file(waifu)
            result = self.reply(f"{user_name}今天的二次元老婆是{waifu_name}哒~[CQ:image,file=base64://{waifu_img}]", reply=True)
            if not Utils.status_ok(result):
                qq_url = Utils.get_img_url(self.robot, f"base64://{waifu_img}")
                self.reply(f"{user_name}今天的二次元老婆是{waifu_name}哒~\n{qq_url}", reply=True)

        # 检查是否是查询老婆是否存在
        else:
            # 提取老婆名称
            waifu_name = re.sub(r"查寻?老婆", "", self.event.msg).strip()
            if not waifu_name:
                return self.reply("请输入要查询的老婆名称~", reply=True)

            # 检查老婆是否存在并获取所有相关文件
            pic_path = self.get_path()
            exts = (".jpg", ".jpeg", ".png")
            waifu_files = []

            for ext in exts:
                file_path = os.path.join(pic_path, f"{waifu_name}{ext}")
                if os.path.exists(file_path):
                    waifu_files.append(f"{waifu_name}{ext}")

            if waifu_files:
                # 如果只有一个文件，正常回复
                if len(waifu_files) == 1:
                    waifu_img = self.get_waifu_file(waifu_files[0])
                    self.reply(f"{waifu_name}已存在~[CQ:image,file=base64://{waifu_img}]", reply=True)
                else:
                    # 如果有多个文件，回复所有版本
                    reply_msg = f"{waifu_name}已存在，共有{len(waifu_files)}个格式："
                    for waifu_file in waifu_files:
                        waifu_img = self.get_waifu_file(waifu_file)
                        reply_msg += f"\n{waifu_file} [CQ:image,file=base64://{waifu_img}]"
                    self.reply(reply_msg, reply=True)
            else:
                self.reply(f"{waifu_name}不存在，可以添加哦~", reply=True)

    @Utils.handler(lambda self: self.au(self.conv_config.get("add_auth"))
         and self.conv_config.get("enable")
         and self.match(r"添加?老婆 "))
    def add_waifu(self):
        """添加二次元老婆"""
        try:
            img_url = ""
            if ret := self.match(r"\[CQ:image.*?url=(.*),.*\]"):
                img_url = ret.group(1)
            elif reply_msg := self.get_reply():
                if img_match := re.search(r"\[CQ:image.*?url=(.*),.*\]", reply_msg):
                    img_url = img_match.group(1)
            waifu_name = re.sub(r"(添加?老婆|\[.*?\])", "", self.event.msg).strip()
            if not waifu_name:
                return self.reply("请注明二次元老婆名称~", reply=True)
            elif not img_url:
                return self.reply("请附带或回复二次元老婆图片~", reply=True)

            url = html.unescape(img_url)
            self.save_waifu(url, waifu_name)

            # 添加成功后，检查该老婆名是否有多个版本
            pic_path = self.get_path()
            exts = (".jpg", ".jpeg", ".png")
            waifu_files = []

            for ext in exts:
                file_path = os.path.join(pic_path, f"{waifu_name}{ext}")
                if os.path.exists(file_path):
                    waifu_files.append(f"{waifu_name}{ext}")

            # 回复添加成功消息并显示所有版本
            if len(waifu_files) == 1:
                self.reply(f"{waifu_name}已增加~", reply=True)
            else:
                reply_msg = f"{waifu_name}已增加~ 当前共有{len(waifu_files)}个格式："
                for waifu_file in waifu_files:
                    waifu_img = self.get_waifu_file(waifu_file)
                    reply_msg += f"\n{waifu_file} [CQ:image,file=base64://{waifu_img}]"
                self.reply(reply_msg, reply=True)

        except Exception: # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply(f"{waifu_name}添加失败!", reply=True)

    @Utils.handler(lambda self: self.au(self.conv_config.get("add_auth"))
        and self.conv_config.get("enable")
        and self.match(r"^删(除)?老婆"))
    def del_waifu(self):
        """删除二次元老婆（必须指定格式）"""
        try:
            # 提取老婆名称和格式
            waifu_input = re.sub(r"删(除)?老婆", "", self.event.msg).strip()
            if not waifu_input:
                return self.reply("请输入要删除的老婆名称和格式，例如：删老婆 老婆名.jpg", reply=True)

            # 支持的图片格式
            supported_formats = [".jpg", ".jpeg", ".png"]

            # 检查是否指定了格式
            target_format = None
            for fmt in supported_formats:
                if waifu_input.lower().endswith(fmt):
                    target_format = fmt
                    break

            if not target_format:
                return self.reply("请指定要删除的图片格式，例如：删老婆 老婆名.jpg\n支持的格式有：jpg、jpeg、png", reply=True)

            # 提取老婆名（移除格式后缀）
            waifu_name = waifu_input[:-len(target_format)]
            if not waifu_name:
                return self.reply("请输入有效的老婆名称", reply=True)

            # 查找并删除指定格式的文件
            pic_path = self.get_path()
            file_path = os.path.join(pic_path, f"{waifu_name}{target_format}")

            if os.path.exists(file_path):
                os.remove(file_path)
                self.reply(f"成功删除老婆 {waifu_name}{target_format}", reply=True)
            else:
                self.reply(f"未找到老婆 {waifu_name}{target_format}", reply=True)

        except Exception: # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply(f"{waifu_input}删除失败!", reply=True)

    def get_path(self):
        """获取二次元老婆路径"""
        path = None
        if self.config["pic_path"].startswith("/"):
            path = self.config["pic_path"]
        else:
            path = os.path.join(self.robot.config.data_path, self.config["pic_path"])
        os.makedirs(path, exist_ok=True)
        return path

    def get_waifu_file(self, filename: str):
        """读取二次元老婆"""
        pic_path = self.get_path()
        filepath = os.path.join(pic_path, filename)
        if not os.path.exists(filepath):
            return None
        with open(filepath, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def save_waifu(self, url: str, name: str):
        """保存二次元老婆"""
        pic_path = self.get_path()
        data = httpx.get(url, timeout=10)
        data.raise_for_status()
        fmt = imghdr.what(None, h=data.content)
        file_path = os.path.join(pic_path, f"{name}.{fmt}")
        with open(file_path, "wb") as f:
            f.write(data.content)
