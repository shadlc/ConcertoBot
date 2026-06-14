"""视频下载模块"""

import base64
import os
from pathlib import Path
import re
from http.cookiejar import LoadError
import time

import traceback
import urllib
from yt_dlp import YoutubeDL, DownloadError

from src.base import Module
from src.utils import Utils

class Ytdlp(Module):
    """视频下载模块"""

    ID = "Ytdlp"
    NAME = "视频下载模块"
    HELP = {
        0: [
            "本模块主要是为了方便在不打开视频链接的情况下观看视频使用，回复视频链接、小程序并@即可获取视频文件，如果同时带有“视频详情”四个字，则仅解析视频详情"
            "\n本模块基于ytdlp，支持多数网站的视频下载",
        ],
        1: [
            "[开启|关闭]视频解析 | 开关本模块功能",
        ],
    }
    GLOBAL_CONFIG = {
        "video_path": "", # 如果不使用base64传输，则填入此路径，应该保证API部分程序能获取到相同的对应路径下的文件
        "headers": {},
        "ydl": {
            "paths": {
                "home": "data/ytdlp",
            },
            "outtmpl": {
                "default": "%(title)s [%(extractor)s %(id)s]",
                "chapter": "%(title)s - %(section_number)03d %(section_title)s [%(extractor)s %(id)s]"
            },
            "proxy": "",
            "playlist_items": "1",
            "extractor_args": "youtubetab:skip=authcheck youtube:player-client=web",
            "merge_output_format": "mp4",
            "format": "bv*[vcodec!*=av01]+ba/b",
            "restrictfilenames": True
        },
    }
    CONV_CONFIG = {
        "enable": True,
        "cool_down": 600,
        "tasks": {},
    }

    def __init__(self, event, auth = 0):
        # self.video_pattern = r"(https?://[^\s&;,\[]*(b23.tv|bilibili.com/video|youtu.be|youtube.com|x.com|v.qq.com|douyin.com|tiktok.com)[^\s&;,\"\[]*)"
        self.video_pattern = r"(https?://[^\s&;,\[]*(b23.tv|bilibili.com/video|youtu.be|x.com|youtube.com|v.qq.com)[^\s&;,\"\u4e00-\u9fff\[]*)"
        super().__init__(event, auth)

    @Utils.listener(lambda self: self.at_or_private() and self.au(2)
            and self.conv_config["enable"]
            and self.match("视频详情"))
    def video_info(self):
        """获取视频详情"""
        url = ""
        if match := self.match(self.video_pattern):
            url = match.group(1)
        elif msg := self.get_reply():
            if match := re.search(self.video_pattern, msg):
                url = match.group(1)
        if url == "":
            return
        elif not self.match("视频详情"):
            return
        self.handled = True
        opts = self.get_options(url)
        try:
            Utils.set_emoji(self.robot, self.event.msg_id, 124)
            info = self.get_info(url, opts)
            msg = self.parse_info(info)
            msg = f"视频解析成功!\n{msg}"
            self.reply(msg, reply=True)
        except LoadError:
            # http://fileformats.archiveteam.org/wiki/Netscape_cookies.txt
            return self.reply("Cookie载入失败! 请联系管理员", reply=True)
        except DownloadError as e:
            nodes = self.node(f"{Utils.format_to_log(e.msg)}")
            return self.reply_forward(nodes, source="视频解析失败")
        except Exception as e: # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            if "风控策略" in f"{e}":
                return self.reply("412 由于触发哔哩哔哩安全风控策略，该次访问请求被拒绝", reply=True)
            nodes = self.node(f"{e}")
            self.robot.admin_notify(f"[{self.event.group_name or self.event.user_name}]视频处理失败", nodes)
            return self.reply_forward(nodes, source="视频处理失败")

    @Utils.listener(lambda self: self.at_or_private() and self.au(2)
            and self.conv_config["enable"]
            and (self.is_reply() or self.match(rf"(【.*】\s)?{self.video_pattern}")))
    def video_download(self):
        """下载视频"""
        url = ""
        if match := self.match(self.video_pattern):
            url = match.group(1)
        elif msg := self.get_reply():
            if match := re.search(self.video_pattern, msg):
                url = match.group(1)
        if url == "":
            return
        elif self.match("视频详情"):
            return
        self.handled = True
        file_path = ""
        tasks = self.conv_config["tasks"]
        opts = self.get_options(url)
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            info = self.get_info(url, opts)
            if not info:
                return self.reply("请求失败或不支持的链接!", reply=True)
            elif info.get("type") == "playlist" and ("bilibili.com" in url or "b23.tv" in url):
                url = info["url"] + "?p=1"
            elif info.get("type") == "playlist":
                msg = self.parse_info(info)
                msg = f"解析成功，但不支持下载视频合辑，请使用单集链接!\n{msg}"
                return self.reply(msg, reply=True)
            user_id = self.event.user_id
            user_task = tasks.get(user_id)
            cool_down = self.conv_config["cool_down"]
            if self.event.user_id in self.robot.config.admin_list:
                pass
            elif user_task and time.time() - int(user_task[-1][2]) < cool_down:
                remain = int(cool_down - time.time() + int(user_task[-1][2]))
                return self.reply(f"视频解析功能每{cool_down}秒才能使用一次，你还剩{remain}秒", reply=True)
            self.record_download(user_id, info["url"])
            name = info["title"]
            if series := info["series"]:
                name = f"{series} {name}"
            if uploader := info["uploader"]:
                name = f"{uploader}上传的视频[{name}]"
            else:
                name = f"视频[{name}]"
            if info["site"] == "youtube":
                opts["format"] = "bv*[filesize<=80M]+ba/b"
            elif info["duration"] > 600:
                msg = f"视频大于十分钟,仅能使用最低分辨率下载，正在解析{name}，请耐心等待"
                self.reply(msg, reply=True)
                opts["format"] = "wv+ba/w"
            elif info["duration"] > 300:
                opts["format"] = "bv[height<=1000]+ba/b"
            elif info["duration"] > 120:
                opts["format"] = "bv[height<=1400]+ba/b"
            if self.is_private():
                msg = self.parse_info(info)
                msg = f"正在解析\n{msg}"
                self.reply(msg, reply=True)
            else:
                Utils.set_emoji(self.robot, self.event.msg_id, 60)
            ext = self.config["ydl"]["merge_output_format"]
            file_path = Path(self.download_video(url, opts)).as_posix()
            if not os.path.exists(file_path):
                file_path = f"{file_path}.{ext}"
            if not os.path.exists(file_path):
                return self.reply("啊咧~视频不见啦，下载失败惹~", reply=True)
            video_name = file_path.split("/").pop()
            file_size = os.path.getsize(file_path)
            self.printf(f"视频{video_name}下载完成，大小{Utils.calc_size(file_size)}")
            if file_size > 100 * 1024 * 1024 and opts["format"] != "wv+ba/w":
                self.reply(f"视频体积太大({Utils.calc_size(file_size)})，尝试使用最低画质下载~", reply=True)
                # 视频大小太大则尝试使用最低画质下载
                if os.path.exists(file_path):
                    os.remove(file_path)
                opts["format"] = "wv+ba/w"
                self.download_video(url, opts)
                if not os.path.exists(file_path):
                    file_path = f"{file_path}.{ext}"
                video_name = file_path.split("/").pop()
                file_size = os.path.getsize(file_path)
                if file_size > 100 * 1024 * 1024:
                    # 视频大小依然太大则上传失败
                    return self.reply(f"视频体积太大({Utils.calc_size(file_size)})，上传失败~", reply=True)
            elif file_size > 100 * 1024 * 1024:
                return self.reply(f"视频体积太大({Utils.calc_size(file_size)})，上传失败~", reply=True)
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 66)
            if video_path := self.config["video_path"]:
                video_path = Path(os.path.join(video_path, video_name)).as_posix()
                msg = f"[CQ:video,file=file://{urllib.parse.quote(video_path)}]"
                self.reply(msg)
            else:
                with open(file_path, "rb") as video_file:
                    video_bytes = video_file.read()
                    b64_bytes = base64.b64encode(video_bytes)
                    b64 = b64_bytes.decode("utf-8")
                    msg = f"[CQ:video,file=base64://{b64}]"
                    self.reply(msg)
        except LoadError:
            # http://fileformats.archiveteam.org/wiki/Netscape_cookies.txt
            return self.reply("Cookie载入失败! 请联系管理员", reply=True)
        except DownloadError as e:
            nodes = self.node(f"{Utils.format_to_log(e.msg)}")
            return self.reply_forward(nodes, source="视频解析失败")
        except Exception as e: # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            if "风控策略" in f"{e}":
                return self.reply("412 由于触发哔哩哔哩安全风控策略，该次访问请求被拒绝", reply=True)
            nodes = self.node(f"{e}")
            self.robot.admin_notify(f"[{self.event.group_name or self.event.user_name}]视频处理失败", nodes)
            return self.reply_forward(nodes, source="视频处理失败")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    @Utils.handler(
        lambda self: self.at_or_private() and self.au(1)
        and self.match(r"^(开启|启用|打开|记录|启动|关闭|禁用|取消)视频(解析|下载)?(功能)?$")
    )
    def toggle(self):
        """启用视频模块功能"""
        msg = ""
        if self.match(r"(开启|启用|打开|记录|启动)"):
            self.conv_config["enable"] = True
            msg = "视频解析功能已开启"
            self.save_config()
        elif self.match(r"(关闭|禁用|取消)"):
            self.conv_config["enable"] = False
            msg = "视频解析功能已关闭"
            self.save_config()
        self.reply(msg)

    def record_download(self, user_id: str, url: str):
        if user_id not in self.conv_config["tasks"]:
            self.conv_config["tasks"][user_id] = []
        self.conv_config["tasks"][user_id] = self.conv_config["tasks"][user_id][:10]
        self.conv_config["tasks"][user_id].append([
            url, user_id, int(time.time())
        ])
        self.save_config()

    def get_options(self, url: str) -> dict:
        """获取配置参数"""
        opts = self.config["ydl"].copy()
        cookie_path = self.get_cookie(url)
        if cookie_path:
            opts["cookiefile"] = cookie_path
        return opts

    def get_cookie(self, url: str) -> str:
        """获取Cookie"""
        cookies_path = self.get_data_path("cookies")
        os.makedirs(cookies_path, exist_ok=True)
        path = None
        if re.search(r"(b23.tv|bilibili.com)", url):
            path = os.path.join(cookies_path, "bilibili.txt")
        elif re.search(r"(youtu.be|youtube.com)", url):
            path = os.path.join(cookies_path, "youtube.txt")
        elif re.search(r"(v.qq.com)", url):
            path = os.path.join(cookies_path, "qqvideo.txt")
        elif re.search(r"(douyin.com)", url):
            path = os.path.join(cookies_path, "douyin.txt")
        elif re.search(r"(tiktok.com)", url):
            path = os.path.join(cookies_path, "tiktok.txt")
        if path and os.path.exists(path):
            return path

    def get_info(self, url: str, opts: dict, max_retries=3, delay=1) -> dict:
        """获取信息"""
        try:
            req = urllib.request.Request(url, headers=self.config.get("headers"))
            with urllib.request.urlopen(req) as response:
                url = response.url
        except Exception as e: # pylint: disable=broad-exception-caught
            self.warnf(f"获取重定向url失败! {e}", level="DEBUG")
        info = {}
        for attempt in range(1, max_retries + 1):
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False, process=False)
            except Exception as e: # pylint: disable=broad-exception-caught
                self.printf(f"第 {attempt} 次解析视频失败: {e}")
                if attempt == max_retries:
                    return info
                else:
                    self.printf(f"{delay} 秒后重试...")
                    time.sleep(delay)
        url = info.get("webpage_url", "")
        url = url.split("&")[0]
        if "bilibili.com" in url:
            url = url.split("?")[0]
        site = info.get("extractor", "")
        series = info.get("series", "")
        title = info.get("title", "[未知]")
        uploader = info.get("uploader", "")
        thumbnail = info.get("thumbnail", "")
        img = Utils.get_content_base64(self.robot, thumbnail)
        duration = info.get("duration", 0)
        if info.get("_type") == "playlist":
            description = info.get("description", "")
            return {
                "type": "playlist",
                "url": url,
                "site": site,
                "series": series,
                "title": title,
                "uploader": uploader,
                "description": description,
                "duration": duration,
                "img": img,
            }
        else:
            description = info.get("description", "")
            ext = info.get("ext", "[未知格式]")
            resolution = info.get("resolution", "[未知分辨率]")
            size = info.get("size", 0)
            view_count = info.get("view_count", 0)
            return {
                "type": "video",
                "url": url,
                "site": site,
                "series": series,
                "title": title,
                "uploader": uploader,
                "duration": duration,
                "img": img,
                "ext": ext,
                "resolution": resolution,
                "size": size,
                "view_count": view_count,
            }

    def parse_info(self, info: dict) -> str:
        """格式化视频信息为文本"""
        video_type = info.get("type")
        url = info.get("url")
        site = info.get("site")
        series = info.get("series")
        title = info.get("title")
        description = info.get("description")
        uploader = info.get("uploader")
        duration = info.get("duration")
        img = info.get("img")
        ext = info.get("ext")
        resolution = info.get("resolution")
        size = info.get("size")
        view_count = info.get("view_count")
        if view_count:
            view_count = f"{round(view_count/10000,2)}万" if view_count >= 10000 else view_count
        if video_type == "playlist":
            msg = f"链接: {url}"
            msg += f"\n平台: {site}"
            msg += f"\n标题: {title}"
            if description:
                msg += f"\n简介: {description}"
            return msg
        else:
            msg = f"链接: {url}"
            msg += f"\n平台: {site}"
            if series:
                msg += f"\n标题: {series} {title}"
            else:
                msg += f"\n标题: {title}"
            msg += f"\n作者: {uploader or "[未知]"}"
            msg += f"\n时长: {Utils.calc_time(int(duration)) or "[未知]"}"
            msg += f"\n播放量: {view_count}"
            if img:
                msg += f"\n[CQ:image,file=base64://{img},subtype=0]"
            if size:
                msg += "\n获取到的最佳格式为: "
                msg += f"{resolution} {ext} {Utils.calc_size(int(size))}"
            msg += "\n@我并回复本条消息开始下载视频"
            return msg

    def download_video(self, url: str, opts: dict, max_retries=5, delay=1) -> str:
        """下载视频"""
        for attempt in range(1, max_retries + 1):
            try:
                with YoutubeDL(opts) as ydl:
                    info_dict = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info_dict)
                    return filename
            except Exception as e: # pylint: disable=broad-exception-caught
                self.printf(f"第 {attempt} 次下载视频失败: {e}")
                if attempt == max_retries:
                    raise
                else:
                    self.printf(f"{delay} 秒后重试...")
                    time.sleep(delay)
