"""推特模块"""

import base64
import re
import subprocess
import tempfile
import traceback
from pathlib import Path

import httpx

from src.base import Module
from src.utils import Utils


class Twitter(Module):
    """推特模块"""

    ID = "Twitter"
    NAME = "推特模块"
    HELP = {
        0: [
            "本模块用于解析推特/X推文正文、图片、视频和GIF，回复链接、并@机器人即可获取内容",
        ],
        2: [
            "发送推特/X链接并@机器人 | 获取推文内容",
            "回复推特/X链接并@机器人 | 获取推文内容",
        ],
    }

    GLOBAL_CONFIG = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "api": "https://api.fxtwitter.com/{username}/status/{tweet_id}",
        "api_timeout": 15,
        "image_timeout": 20,
        "video_timeout": 30,
        "max_image_count": 9,
        "max_image_bytes": 10 * 1024 * 1024,
        "convert_gif": True,
        "ffmpeg_path": "ffmpeg",
        "gif_conversion_timeout": 120,
        "max_gif_source_bytes": 100 * 1024 * 1024,
        "max_gif_bytes": 30 * 1024 * 1024,
        "max_gif_fps": 20,
        "max_nested_tweets": 1,
    }
    NESTED_TWEET_KEYS = (
        "quote",
        "quoted_tweet",
        "retweeted_tweet",
        "retweeted_status",
        "retweet",
    )

    def __init__(self, event, auth=0):
        """初始化推特/X链接匹配规则"""
        self.tweet_pattern = (
            r"https?://(?:www\.)?(?:twitter\.com|x\.com)/"
            r"[A-Za-z0-9_]+/status/\d+"
        )
        super().__init__(event, auth)

    @Utils.listener(
        lambda self: self.at_or_private()
        and self.au(2)
        and (self.is_reply() or self.match(self.tweet_pattern))
    )
    def twitter_download(self):
        """解析并发送推文正文和媒体"""
        url = self._get_tweet_url()
        if not url:
            return
        self.handled = True
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            caption, image_data, video_url, gif_data = self.retry(
                self.get_media,
                url,
                failed_ok=False,
            )
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 66)
            msg = self._build_message(caption, image_data, video_url, gif_data)
            if not msg:
                raise ReferenceError("推文中未找到可发送的正文或媒体")
            if video_url:
                # 视频无法使用引用回复
                return self.reply(msg)
            result = self.reply(msg, reply=True)
            if not Utils.status_ok(result):
                img_list = []
                for data in image_data:
                    img_url = Utils.get_img_url(self.robot, f"base64://{data}")
                    img_list.append(img_url)
                msg = f"{caption}\n" + "\n".join(img_list)
                self.reply(msg, reply=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            source = self._message_source()
            nodes = self.node(f"来源：{source}\nURL：{url}\n错误：{e}")
            self.robot.admin_notify("推特内容处理失败", nodes)
            return self.reply_forward(nodes, source="推特内容处理失败")

    def _get_tweet_url(self) -> str:
        """从当前消息或被回复消息中提取推特/X链接。"""
        messages = [self.event.text]
        if self.is_reply() and (reply := self.get_reply()):
            messages.append(reply)
        for message in messages:
            match = re.search(self.tweet_pattern, str(message), re.IGNORECASE)
            if match:
                return match.group(0)
        return ""

    def get_media(self, url: str) -> tuple[str, list[str], str, list[str]]:
        """读取推文正文、图片、视频地址"""
        username, tweet_id = self._parse_tweet_url(url)
        api_url = self.config["api"].format(username=username, tweet_id=tweet_id)
        headers = {
            "User-Agent": self.config["user_agent"],
            "Accept": "application/json, text/plain, */*",
            "Referer": url,
        }
        with httpx.Client(
            headers=headers,
            follow_redirects=True,
            timeout=self.config["api_timeout"],
        ) as client:
            response = client.get(api_url)
            response.raise_for_status()
            data = response.json()

        if not isinstance(data, dict) or data.get("code") != 200:
            message = data.get("message", "未知错误") if isinstance(data, dict) else "响应格式错误"
            raise ReferenceError(f"FxTwitter接口返回失败: {message}")
        tweet = data.get("tweet")
        if not isinstance(tweet, dict):
            raise ReferenceError("FxTwitter接口未返回推文数据")

        captions, image_urls, video_items = self._collect_tweet_content(tweet)
        image_data = self._download_images_as_base64(
            image_urls[:self.config["max_image_count"]],
            url,
        )

        video_url = ""
        gif_data = []
        for video, media in video_items:
            candidate = video.get("url", "")
            if not isinstance(candidate, str) or not candidate:
                continue
            if not video_url:
                video_url = candidate
            if self.config["convert_gif"] and self._is_gif_media(media, video):
                converted_gif = self._convert_video_to_gif(candidate, url)
                if converted_gif:
                    gif_data.append(converted_gif)
        return "".join(captions), image_data, video_url, gif_data

    def _collect_tweet_content(
        self,
        tweet: dict,
        depth: int = 0,
    ) -> tuple[list[str], list[str], list[tuple[dict, dict]]]:
        """收集当前推文及嵌套转发内容，避免重复解析媒体逻辑。"""
        captions = []
        caption_parts = []
        if author := self._get_tweet_author(tweet):
            caption_parts.append(author)
        if text := self._get_tweet_text(tweet):
            caption_parts.append(text)
        if caption_parts:
            captions.append("\n".join(caption_parts))

        media = tweet.get("media") or {}
        if not isinstance(media, dict):
            media = {}
        image_urls = self._get_image_urls(media)
        video_items = [
            (video, media) for video in self._get_video_items(media)
        ]

        if depth < self.config["max_nested_tweets"]:
            nested_tweet = self._get_nested_tweet(tweet)
            if nested_tweet:
                nested_captions, nested_images, nested_videos = self._collect_tweet_content(
                    nested_tweet,
                    depth + 1,
                )
                if nested_captions:
                    captions.append(f"转发：\n{chr(10).join(nested_captions)}")
                image_urls.extend(nested_images)
                video_items.extend(nested_videos)
        return captions, image_urls, video_items

    @staticmethod
    def _get_tweet_text(tweet: dict) -> str:
        """读取推文正文。"""
        text = tweet.get("text", "")
        return text.strip() if isinstance(text, str) else ""

    @staticmethod
    def _get_tweet_author(tweet: dict) -> str:
        """读取推文作者名称和用户名。"""
        author = tweet.get("author")
        if not isinstance(author, dict):
            return ""
        name = author.get("name")
        username = author.get("screen_name") or author.get("username")
        if isinstance(name, str) and name.strip() and isinstance(username, str) and username.strip():
            return f"{name.strip()}(@{username.strip().lstrip('@')})"
        if isinstance(name, str) and name.strip():
            return name.strip()
        if isinstance(username, str) and username.strip():
            return f"@{username.strip().lstrip('@')}"
        return ""

    @staticmethod
    def _get_image_urls(media: dict) -> list[str]:
        """读取推文中的图片地址。"""
        image_urls = []
        for photo in media.get("photos") or []:
            if isinstance(photo, dict) and isinstance(photo.get("url"), str):
                image_urls.append(photo["url"])
        return image_urls

    @staticmethod
    def _get_video_items(media: dict) -> list[dict]:
        """兼容 FxTwitter 的 videos、gifs 和 all 媒体字段。"""
        videos = media.get("videos") or media.get("gifs") or []
        if videos:
            return [video for video in videos if isinstance(video, dict)]
        return [
            item for item in media.get("all") or []
            if isinstance(item, dict)
            and str(item.get("type", "")).lower() in {"video", "gif"}
        ]

    @classmethod
    def _get_nested_tweet(cls, tweet: dict) -> dict | None:
        """读取 FxTwitter 常见的引用或转发推文字段。"""
        for key in cls.NESTED_TWEET_KEYS:
            nested = tweet.get(key)
            if not isinstance(nested, dict):
                continue
            if isinstance(nested.get("tweet"), dict):
                nested = nested["tweet"]
            return nested
        return None

    @staticmethod
    def _parse_tweet_url(url: str) -> tuple[str, str]:
        """从推文链接中提取用户名和推文 ID。"""
        match = re.search(
            r"https?://(?:www\.)?(?:twitter\.com|x\.com)/"
            r"([A-Za-z0-9_]+)/status/(\d+)",
            url,
            re.IGNORECASE,
        )
        if not match:
            raise ValueError("无效的推特/X推文链接")
        return match.group(1), match.group(2)

    def _download_images_as_base64(self, image_urls: list[str], referer: str) -> list[str]:
        """下载推特图片并转换为 Base64，避免客户端直连图片域名。"""
        headers = {
            "User-Agent": self.config["user_agent"],
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": referer,
        }
        image_data = []
        with httpx.Client(
            headers=headers,
            follow_redirects=True,
            timeout=self.config["image_timeout"],
        ) as client:
            for image_url in image_urls:
                try:
                    response = client.get(image_url)
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    if content_type and not content_type.startswith("image/"):
                        raise ValueError(f"响应类型不是图片: {content_type}")
                    if len(response.content) > self.config["max_image_bytes"]:
                        raise ValueError("图片超过 10 MB 限制")
                    image_data.append(base64.b64encode(response.content).decode("ascii"))
                except (httpx.HTTPError, OSError, ValueError) as error:
                    self.printf(f"图片下载失败: {image_url[:120]}: {error}", level="DEBUG")
        return image_data

    @staticmethod
    def _is_gif_media(media: dict, video: dict) -> bool:
        """判断 FxTwitter 返回的视频是否来自 GIF 帖子。"""
        video_url = str(video.get("url", "")).lower()
        media_type = str(video.get("type", "")).lower()
        media_format = str(video.get("format", "")).lower()
        if (
            media_type == "gif"
            or media_format == "gif"
            or video.get("is_gif") is True
            or "tweet_video" in video_url
        ):
            return True
        for item in media.get("all") or []:
            if not isinstance(item, dict) or item.get("url") != video.get("url"):
                continue
            if str(item.get("type", "")).lower() == "gif":
                return True
        return False

    def _convert_video_to_gif(self, video_url: str, referer: str) -> str:
        """下载视频并转换为保留原尺寸和帧时间的 GIF。"""
        headers = {
            "User-Agent": self.config["user_agent"],
            "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
            "Referer": referer,
        }
        try:
            with tempfile.TemporaryDirectory(prefix="twitter-gif-") as temp_dir:
                source_path = Path(temp_dir) / "source.mp4"
                output_path = Path(temp_dir) / "output.gif"
                self._download_video(video_url, source_path, headers)
                self._run_ffmpeg(source_path, output_path)
                if output_path.stat().st_size > self.config["max_gif_bytes"]:
                    raise ValueError("GIF 超过大小限制")
                return base64.b64encode(output_path.read_bytes()).decode("ascii")
        except (httpx.HTTPError, OSError, subprocess.SubprocessError, ValueError) as error:
            self.printf(f"GIF 转换失败，回退发送视频: {error}", level="DEBUG")
            return ""

    def _download_video(self, video_url: str, target_path: Path, headers: dict[str, str]) -> None:
        """下载 GIF 源视频并限制临时文件大小。"""
        total_bytes = 0
        with httpx.Client(follow_redirects=True, timeout=self.config["video_timeout"]) as client:
            with client.stream("GET", video_url, headers=headers) as response:
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > self.config["max_gif_source_bytes"]:
                    raise ValueError("GIF 源视频超过大小限制")
                with target_path.open("wb") as video_file:
                    for chunk in response.iter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > self.config["max_gif_source_bytes"]:
                            raise ValueError("GIF 源视频超过大小限制")
                        video_file.write(chunk)

    def _run_ffmpeg(self, source_path: Path, output_path: Path) -> None:
        """使用调色板转换 GIF，并限制输出帧率。"""
        max_gif_fps = int(self.config["max_gif_fps"])
        if max_gif_fps <= 0:
            raise ValueError("GIF 最大帧率必须大于 0")
        command = [
            self.config["ffmpeg_path"],
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-filter_complex",
            f"[0:v]fps={max_gif_fps},split[frames][palette];"
            "[palette]palettegen=stats_mode=diff[paletteout];"
            "[frames][paletteout]paletteuse=dither=sierra2_4a",
            "-fps_mode",
            "passthrough",
            "-loop",
            "0",
            "-an",
            str(output_path),
        ]
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.config["gif_conversion_timeout"],
        )
        if result.returncode:
            error = result.stderr.strip()[-500:] or "ffmpeg 未返回错误信息"
            raise subprocess.CalledProcessError(result.returncode, command, stderr=error)

    @staticmethod
    def _build_message(
        caption: str,
        image_data: list[str],
        video_url: str,
        gif_data: list[str] | None = None,
    ) -> str:
        """将正文和媒体组装为 OneBot CQ 消息。"""
        gif_data = gif_data or []
        if video_url:
            # 解析到视频时仅发送视频，避免附带推文正文。
            return f"[CQ:video,file={video_url}]"
        parts = []
        if caption:
            parts.append(caption)
        parts.extend(f"[CQ:image,sub_type=0,file=base64://{data}]" for data in image_data)
        parts.extend(f"[CQ:image,sub_type=0,file=base64://{data}]" for data in gif_data)
        return "".join(parts)

    def _message_source(self) -> str:
        """获取错误通知中的群聊或私聊来源。"""
        group_id = str(getattr(self.event, "group_id", "") or "")
        user_id = str(getattr(self.event, "user_id", "") or "")
        user_name = str(getattr(self.event, "user_name", "") or user_id or "未知用户")
        if group_id:
            group_name = str(getattr(self.event, "group_name", "") or group_id)
            return f"群聊：{group_name}；发送者：{user_name}"
        return f"用户：{user_name}"
