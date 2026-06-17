"""小红书视频模块"""

import re
import traceback

import httpx

from src.base import Module
from src.utils import Utils

class Rednote(Module):
    """小红书视频模块"""

    ID = "Rednote"
    NAME = "小红书视频模块"
    HELP = {
        0: [
            "本模块用于解析小红书视频，回复视频链接、小程序并@即可获取视频文件",
        ],
        2: [
            "发送小红书链接并@机器人 | 下载视频",
            "回复小红书链接并@机器人 | 下载视频",
        ],
    }

    def __init__(self, event, auth = 0):
        """初始化小红书链接匹配规则"""
        self.video_pattern = r"(https?://[^\s&;,\[]*(xhslink.com/o|xiaohongshu.com/)[^\s;,\"\u4e00-\u9fff\[]*)"
        super().__init__(event, auth)

    @Utils.listener(lambda self: self.at_or_private() and self.au(2)
            and (self.is_reply() or self.match(self.video_pattern)))
    def rednote_download(self):
        """下载视频"""
        url = ""
        if match := self.match(rf"({self.video_pattern})"):
            url = match.group(1)
        elif msg := self.get_reply():
            if match := re.search(rf"({self.video_pattern})", msg):
                url = match.group(1)
        if url == "":
            return
        self.handled = True
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            play_url = self.retry(self.get_play_url, url, failed_ok=False)
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 66)
            msg = f"[CQ:video,file={play_url}]"
            self.reply(msg)
        except Exception as e: # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            return self.reply_forward(self.node(f"{e}"), source="小红书视频处理失败")

    def get_play_url(self, url: str) -> str:
        """获取视频信息"""
        resp = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Linux; Android 6.0;)"})
        if match := re.search(r"\"backupUrls\":\s*\[\"(.*?)\"", resp.text):
            url = match.group(1)
            url = url.encode("utf-8").decode("unicode_escape")
            return url
        else:
            raise ReferenceError(f"未在{url}找到有效的视频链接")

