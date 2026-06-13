"""抖音视频模块"""

import re
import time
import traceback
from typing import Any, Callable

import httpx

from src.utils import Module, set_emoji, listener

class Tiktok(Module):
    """抖音视频模块"""

    ID = "Tiktok"
    NAME = "抖音视频模块"
    HELP = {
        0: [
            "本模块用于解析抖音视频，回复视频链接、小程序并@即可获取视频文件",
        ],
    }

    def __init__(self, event, auth = 0):
        self.video_pattern = r"(https?://[^\s&;,\[]*(douyin.com|tiktok.com)/[^\s&;,\"\u4e00-\u9fff\[]*)"
        super().__init__(event, auth)

    @listener(lambda self: self.at_or_private() and self.au(2)
            and (self.is_reply() or self.match(self.video_pattern)))
    def tiktok_download(self):
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
                set_emoji(self.robot, self.event.msg_id, 124)
            play_url = self.retry(lambda url=url: self.get_play_url(url), failed_ok=False)
            if not self.is_private():
                set_emoji(self.robot, self.event.msg_id, 66)
            msg = f"[CQ:video,file={play_url}]"
            self.reply(msg)
        except Exception as e:
            self.errorf(traceback.format_exc())
            nodes = self.node(f"{e}")
            self.robot.admin_notify("视频处理失败", nodes)
            return self.reply_forward(nodes, source="抖音视频处理失败")

    def get_play_url(self, url: str) -> str:
        """获取视频信息"""
        resp = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Linux; Android 6.0;)"})
        if match := re.search(r"\"url_list\"\s*:\s*\[\"([^\]]+snssdk[^\]]+)\"\]", resp.text):
            url = match.group(1)
            url = url.encode("utf-8").decode("unicode_escape")
            url = url.replace("playwm", "play")
            return url
        else:
            raise ReferenceError("未找到有效的视频链接")

    def retry(self, func: Callable[..., Any], name="", max_retries=5, delay=1, failed_ok=True) -> Any:
        """多次尝试执行"""
        for attempt in range(1, max_retries + 1):
            try:
                result = func()
                return result
            except Exception as e:
                func_name = name if name else func.__name__
                self.printf(f"第 {attempt} 次执行 {func_name} 失败: {e}")
                if attempt == max_retries:
                    if failed_ok:
                        return None
                    raise
                else:
                    self.printf(f"{delay} 秒后重试...")
                    time.sleep(delay)

