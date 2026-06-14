"""遇见API模块"""

import base64
import random
import time
import traceback
from typing import Callable, Any
import httpx

from src.base import Module
from src.utils import Utils


class YUJN(Module):
    """遇见API模块"""

    ID = "Yujn"
    NAME = "遇见API模块"
    HELP = {
        2: [
            "小姐姐 | 获取小姐姐视频",
            "黑丝 | 获取黑丝视频",
            "白丝 | 获取白丝视频",
            "欲梦 | 获取欲梦视频",
            "甜妹 | 获取甜妹视频",
            "双倍快乐 | 获取双倍快乐视频",
            "纯情女高 | 获取纯情女高视频",
            "萝莉 | 获取萝莉视频",
            "玉足 | 获取玉足视频",
            "帅哥 | 获取帅哥视频",
            "热舞 | 获取热舞视频",
            "吊带 | 获取吊带视频",
            "汉服 | 获取汉服视频",
            "狱卒 | 获取极品狱卒视频",
            "清纯 | 获取清纯视频",
            "快手变装 | 获取快手变装视频",
            "抖音变装 | 获取抖音变装视频",
            "萌娃 | 获取萌娃视频",
            "穿搭 | 获取穿搭视频",
            "完美身材 | 获取完美身材视频",
        ],
    }

    YUJN_URL = "https://api.yujn.cn"
    # 视频类API
    URL_MAP = {
        # 图片类API
        "写真": "https://cyapi.top/API/wdxz.php",
        # 视频类API
        "小姐姐": f"{YUJN_URL}/api/xjj.php?type=video",
        "黑丝": f"{YUJN_URL}/api/heisis.php?type=video",
        "白丝": f"{YUJN_URL}/api/baisis.php?type=video",
        "欲梦": f"{YUJN_URL}/api/ndym.php?type=video",
        "甜妹": f"{YUJN_URL}/api/tianmei.php?type=video",
        "双倍快乐": f"{YUJN_URL}/api/sbkl.php?type=video",
        "女高": f"{YUJN_URL}/api/nvgao.php?type=video",
        "萝莉": f"{YUJN_URL}/api/luoli.php?type=video",
        "玉足": f"{YUJN_URL}/api/yuzu.php?type=video",
        "帅哥": f"{YUJN_URL}/api/xgg.php?type=video",
        "热舞": f"{YUJN_URL}/api/rewu.php?type=video",
        "吊带": f"{YUJN_URL}/api/diaodai.php?type=video",
        "汉服": f"{YUJN_URL}/api/hanfu.php?type=video",
        "狱卒": f"{YUJN_URL}/api/jpmt.php?type=video",
        "清纯": f"{YUJN_URL}/api/qingchun.php?type=video",
        "快手变装": f"{YUJN_URL}/api/ksbianzhuang.php?type=video",
        "抖音变装": f"{YUJN_URL}/api/bianzhuang.php?type=video",
        "萌娃": f"{YUJN_URL}/api/mengwa.php?type=video",
        "穿搭": f"{YUJN_URL}/api/chuanda.php?type=video",
        "完美身材": f"{YUJN_URL}/api/wmsc.php?type=video",
        # 语音类API
        "御姐": f"{YUJN_URL}/api/yujie.php",
        "绿茶": f"{YUJN_URL}/api/lvcha.php",
        "怼人": f"{YUJN_URL}/api/duiren.php",
    }
    PICTURE_PATTERN = "(写真)"
    VIDEO_PATTERN = "(小姐姐|黑丝|白丝|欲梦|甜妹|双倍快乐|女高|萝莉|玉足|帅哥|热舞|吊带|汉服|狱卒|清纯|快手变装|抖音变装|萌娃|穿搭|完美身材)"
    VOICE_PATTERN = "(御姐|绿茶|怼人)"

    CONV_CONFIG = {
        "enable": True,
        "probability": 0,
        "active_func": [],
    }

    @Utils.listener(
        lambda self: self.au(2)
        and self.conv_config["enable"]
        and self.conv_config.get("probability", 0)
        and len(self.conv_config.get("active_func", []))
        and random.random() < self.conv_config.get("probability", 0)
        and self.match(r".*")
    )
    def prob_msg(self):
        """概率触发遇见API"""
        try:
            func = random.choice(self.conv_config.get("active_func", []))
            api_url = self.URL_MAP[func]

            def api_req():
                resp = httpx.get(api_url, timeout=5, follow_redirects=True)
                resp.raise_for_status()
                b64 = base64.b64encode(resp.content).decode("utf-8")
                return f"base64://{b64}"

            b64 = self.retry(api_req)
            self.reply(f"[CQ:video,file={b64}]")
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.errorf(f"遇见API请求失败: {e}")

    @Utils.handler(
        lambda self: self.au(2)
        and self.match(rf"^(来|发)(点|只|张|个|位){self.PICTURE_PATTERN}$")
    )
    def picture_handler(self):
        """图片类功能处理"""
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            cmd = self.match(self.PICTURE_PATTERN).group(1)
            if cmd not in self.URL_MAP:
                return self.reply(f"未知命令: {cmd}")
            api_url = self.URL_MAP[cmd]
            return self.reply(f"[CQ:image,file={api_url}]")
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.errorf(f"遇见API请求失败: {e}")

    @Utils.handler(
        lambda self: self.au(2)
        and self.match(rf"^(来|发)(点|只|张|个|位){self.VIDEO_PATTERN}$")
    )
    def video_handler(self):
        """视频类功能处理"""
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            cmd = self.match(self.VIDEO_PATTERN).group(1)
            if cmd not in self.URL_MAP:
                return self.reply(f"未知命令: {cmd}")
            video_url = self.URL_MAP[cmd]

            def api_req():
                resp = httpx.get(video_url, timeout=5, follow_redirects=True)
                resp.raise_for_status()
                b64 = base64.b64encode(resp.content).decode("utf-8")
                return f"base64://{b64}"

            b64 = self.retry(api_req)
            result = self.reply(f"[CQ:video,file={b64}]")
            if not Utils.status_ok(result):
                self.reply("视频失效了~请再试一次吧~")
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.errorf(f"遇见API请求失败: {e}")

    @Utils.handler(
        lambda self: self.au(2)
        and self.match(rf"^(来|发)(点|只|个|位|句){self.VOICE_PATTERN}(语音|声音)?$")
    )
    def voice_handler(self):
        """语音类功能处理"""
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            cmd = self.match(self.VOICE_PATTERN).group(1)
            if cmd not in self.URL_MAP:
                return self.reply(f"未知命令: {cmd}")
            voice_url = self.URL_MAP[cmd]

            def api_req():
                resp = httpx.get(voice_url, timeout=5, follow_redirects=True)
                resp.raise_for_status()
                b64 = base64.b64encode(resp.content).decode("utf-8")
                return f"base64://{b64}"

            b64 = self.retry(api_req)
            self.reply(f"[CQ:record,file={b64}]")
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.errorf(f"遇见API请求失败: {e}")

    @Utils.handler(
        lambda self: self.group_at()
        and self.au(1)
        and self.match(r"^(开启|关闭)遇见API$")
    )
    def toggle(self):
        """开启关闭模块"""
        flag = self.conv_config.get("enable", True)
        text = "开启" if flag else "关闭"
        if self.match(r"(开启|打开|启用|允许)"):
            flag = True
            text = "开启"
        elif self.match(r"(关闭|禁止|取消)"):
            flag = False
            text = "关闭"
        msg = f"遇见API模块已{text}"
        self.conv_config["enable"] = flag
        self.save_config()
        self.reply(msg, reply=True)

    def retry(
        self, func: Callable[..., Any], name="", max_retries=5, delay=1, failed_ok=True
    ) -> Any:
        """多次尝试执行"""
        for attempt in range(1, max_retries + 1):
            try:
                result = func()
                return result
            except Exception as e:  # pylint: disable=broad-exception-caught
                func_name = name if name else func.__name__
                self.printf(f"第 {attempt} 次执行 {func_name} 失败: {e}")
                if attempt == max_retries:
                    if failed_ok:
                        return None
                    raise
                else:
                    self.printf(f"{delay} 秒后重试...")
                    time.sleep(delay)
