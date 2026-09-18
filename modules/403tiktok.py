"""抖音模块"""

import base64
import html
import json
import re
import traceback
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import unquote, urlencode, urljoin, urlparse

import httpx

from src.base import Module
from src.utils import Utils


class Tiktok(Module):
    """抖音模块"""

    ALLOWED_HOSTS = frozenset({
        "douyin.com",
        "iesdouyin.com",
        "tiktok.com",
    })
    REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
    COOKIE_FILES = {
        "douyin.com": "douyin.txt",
        "iesdouyin.com": "douyin.txt",
        "tiktok.com": "tiktok.txt",
    }
    JSON_SCRIPT_IDS = (
        "RENDER_DATA",
        "SIGI_STATE",
        "__UNIVERSAL_DATA_FOR_REHYDRATION__",
        "__NEXT_DATA__",
    )
    IMAGE_KEYS = frozenset({
        "images",
        "image_list",
        "imagelist",
        "imageurl",
        "image_url",
        "origin_url",
    })
    VIDEO_URL_KEYS = frozenset({
        "playaddr",
        "play_addr",
        "downloadaddr",
        "download_addr",
        "playurl",
        "downloadurl",
        "url_list",
    })

    ID = "Tiktok"
    NAME = "抖音模块"
    HELP = {
        0: [
            "本模块用于解析抖音视频和图文，回复链接、小程序并@即可获取媒体",
        ],
        2: [
            "发送抖音/TikTok链接并@机器人 | 获取媒体",
            "回复抖音/TikTok链接并@机器人 | 获取媒体",
        ],
    }

    GLOBAL_CONFIG = {
        "user_agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
            "Mobile/15E148 Safari/604.1"
        ),
        "page_timeout": 15,
        "image_timeout": 20,
        "max_redirects": 5,
        "max_image_bytes": 10 * 1024 * 1024,
        "ytdlp_socket_timeout": 30,
    }

    def __init__(self, event, auth=0):
        """初始化抖音和 TikTok 链接匹配规则"""
        self.video_pattern = (
            r"https?://(?:[a-z0-9-]+\.)?(?:douyin\.com|iesdouyin\.com|"
            r"tiktok\.com)/[^\s&;,\"\u4e00-\u9fff\[\]<>]+"
        )
        super().__init__(event, auth)

    @Utils.listener(
        lambda self: self.at_or_private()
        and self.au(2)
        and (self.is_reply() or self.match(self.video_pattern))
    )
    def tiktok_download(self):
        """下载视频或发送图文图片"""
        url = self._extract_source_url()
        if not url:
            return
        self.handled = True
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            caption, image_data, video_url = self.retry(self.get_media, url, failed_ok=False)
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 66)
            if image_data:
                image_message = "\n".join(
                    f"[CQ:image,file=base64://{data}]" for data in image_data
                )
                msg = f"{caption}\n{image_message}" if caption else image_message
                return self.reply_media(
                    msg,
                    "image",
                    image_data,
                    caption,
                    source="TikTok",
                    forward=len(image_data) > 3,
                )
            msg = f"[CQ:video,file={video_url}]"
            return self.reply_media(msg, "video", video_url)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            nodes = self.node(f"URL：{url}\n错误：{e}")
            self.robot.admin_notify("抖音处理失败", nodes, self.event)
            return self.reply(str(e), reply=True)

    def _extract_source_url(self) -> str:
        """从当前消息或被回复消息中提取抖音/TikTok链接。"""
        messages = [self.event.text]
        if self.is_reply() and (reply := self.get_reply()):
            messages.append(reply)
        for message in messages:
            match = re.search(self.video_pattern, message, re.IGNORECASE)
            if match:
                return match.group(0).rstrip(".,，。!！?？)）]>")
        return ""

    def get_media(self, url: str) -> tuple[str, list[str], str]:
        """解析并返回正文、图片数据和视频地址。"""
        page_url = url
        try:
            page_url, page = self._fetch_page(url)
            if self._is_music_page(page_url):
                raise ReferenceError("这是汽水音乐歌曲分享链接，当前仅支持视频和图文链接")
            if self._is_image_page(page_url):
                return self._parse_image_media(page_url, page)
            video_url = self._extract_video_url(page)
            if video_url:
                return "", [], video_url
        except httpx.HTTPError as error:
            self.printf(f"请求视频页面失败，尝试备用解析器: {error}", level="DEBUG")

        try:
            return "", [], self._extract_video_url_with_ytdlp(page_url)
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            raise ReferenceError("未找到有效的抖音地址，可能是页面风控或链接已失效") from error

    def _parse_image_media(self, page_url: str, page: str) -> tuple[str, list[str], str]:
        """解析图文页面并下载图片。"""
        image_urls = self._extract_image_urls(page)
        caption = self._extract_image_caption(page)
        if not image_urls and (detail := self._fetch_image_detail(page_url)):
            image_urls = self._extract_image_urls_from_data(detail)
            if not caption:
                caption = self._extract_image_caption_from_data(detail)
        if not image_urls:
            raise ReferenceError("未找到图文地址")

        image_data = self._download_images_as_base64(image_urls, page_url)
        if not image_data:
            raise ReferenceError("图文下载失败")
        return caption, image_data, ""

    @staticmethod
    def _is_image_page(url: str) -> bool:
        """根据最终页面路径识别抖音图文或 TikTok 图片帖。"""
        path = urlparse(url).path.lower().rstrip("/")
        return any(part in path for part in ("/note/", "/slides/", "/photo/"))

    @staticmethod
    def _is_music_page(url: str) -> bool:
        """识别汽水音乐分享页，避免将歌曲链接交给视频解析器。"""
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        return hostname == "music.douyin.com" or parsed.path.lower().startswith("/qishui/")

    def _fetch_page(self, url: str, *, user_agent: str | None = None) -> tuple[str, str]:
        """在抖音/TikTok域名内手动跟踪重定向并获取页面。"""
        current_url = url
        with httpx.Client(follow_redirects=False, timeout=self.config["page_timeout"]) as client:
            for _ in range(self.config["max_redirects"]):
                if not self._host_is_allowed(current_url):
                    raise ValueError(f"链接跳转到了非预期域名: {urlparse(current_url).hostname}")
                headers = self._build_request_headers(
                    current_url,
                    "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                )
                if user_agent:
                    headers["User-Agent"] = user_agent
                response = client.get(current_url, headers=headers)
                if response.status_code in self.REDIRECT_STATUS:
                    location = response.headers.get("Location", "").strip()
                    if not location:
                        raise RuntimeError("页面重定向缺少 Location")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                return str(response.url), response.text
        raise RuntimeError("页面重定向次数超过限制")

    def _fetch_image_detail(self, page_url: str) -> dict | list | None:
        """从抖音作品页或详情接口补充分享页中缺失的图文数据。"""
        if not self._is_douyin_url(page_url):
            return None
        match = re.search(r"/(?:note|slides|video)/(\d+)", urlparse(page_url).path)
        if not match:
            return None

        # 手机分享页可能只有路由数据，公开的服务端渲染页仍包含完整图集。
        # 使用爬虫 UA 获取结构化数据，无需浏览器执行脚本或登录 Cookie。
        try:
            _, page = self._fetch_page(
                f"https://www.douyin.com/note/{match.group(1)}",
                user_agent="Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            )
            for data in self._iter_json_documents(page):
                if self._extract_image_urls_from_data(data):
                    return data
        except httpx.HTTPError as error:
            self.printf(f"请求抖音图文作品页失败: {error}", level="DEBUG")

        query = urlencode({
            "aweme_id": match.group(1),
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
        })
        api_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?{query}"
        headers = self._build_request_headers(
            api_url,
            "application/json, text/plain, */*",
            referer=page_url,
            cookie_url=page_url,
        )

        try:
            with httpx.Client(timeout=self.config["page_timeout"]) as client:
                response = client.get(api_url, headers=headers)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            self.printf(f"请求抖音图文详情失败: {error}", level="DEBUG")
            return None
        return data if isinstance(data, dict) and isinstance(data.get("aweme_detail"), dict) else None

    def _build_request_headers(
        self,
        url: str,
        accept: str,
        *,
        referer: str | None = None,
        cookie_url: str | None = None,
    ) -> dict[str, str]:
        """生成页面、接口和图片请求共用的请求头。"""
        headers = {
            "User-Agent": self.config["user_agent"],
            "Accept": accept,
            "Referer": referer or self._get_site_referer(url),
        }
        if cookie := self._get_cookie_header(cookie_url or url):
            headers["Cookie"] = cookie
        return headers

    @classmethod
    def _get_site_referer(cls, url: str) -> str:
        """根据站点生成默认 Referer。"""
        return "https://www.douyin.com/" if cls._is_douyin_url(url) else "https://www.tiktok.com/"

    @classmethod
    def _is_douyin_url(cls, url: str) -> bool:
        """判断链接是否属于抖音站点。"""
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
        return any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in ("douyin.com", "iesdouyin.com")
        )

    @classmethod
    def _get_cookie_filename(cls, url: str) -> str:
        """根据站点选择本模块的 Cookie 文件。"""
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
        for domain, filename in cls.COOKIE_FILES.items():
            if hostname == domain or hostname.endswith(f".{domain}"):
                return filename
        return ""

    def _get_cookie_header(self, url: str) -> str:
        """读取本模块目录下的 Netscape Cookie 文件并生成请求头。"""
        cookie_path = self._get_cookie_path(url)
        if cookie_path is None:
            return ""

        cookie_jar = MozillaCookieJar(cookie_path)
        try:
            cookie_jar.load(ignore_discard=True, ignore_expires=True)
        except (OSError, ValueError) as error:
            self.printf(f"读取 Cookie 失败: {error}", level="DEBUG")
            return ""
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in cookie_jar)

    def _get_cookie_path(self, url: str) -> Path | None:
        """获取本模块目录下对应站点的 Cookie 文件路径。"""
        filename = self._get_cookie_filename(url)
        if not filename:
            return None
        cookie_path = Path(self.get_data_path("cookies")) / filename
        return cookie_path if cookie_path.is_file() else None

    def _download_images_as_base64(self, image_urls: list[str], referer: str) -> list[str]:
        """携带 Cookie 下载图片并转换为 Base64，避免客户端直连抖音图片域名。"""
        image_data = []
        with httpx.Client(follow_redirects=True, timeout=self.config["image_timeout"]) as client:
            for image_url in image_urls:
                try:
                    response = client.get(
                        image_url,
                        headers=self._build_request_headers(
                            image_url,
                            "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                            referer=referer,
                            cookie_url=referer,
                        ),
                    )
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

    @classmethod
    def _host_is_allowed(cls, url: str) -> bool:
        """校验页面请求的域名，避免短链跳转到无关站点。"""
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
        return any(hostname == host or hostname.endswith(f".{host}") for host in cls.ALLOWED_HOSTS)

    @classmethod
    def _extract_video_url(cls, page: str) -> str:
        """从抖音/TikTok页面的多种状态数据中提取播放地址。"""
        candidates: list[tuple[int, str]] = []
        for data in cls._iter_json_documents(page):
            cls._collect_video_urls(data, candidates)

        # 页面结构变化时仍保留对常见 url_list 数组的兼容。
        for match in re.finditer(r"\"(?:url_list|playAddr|downloadAddr)\"\s*:\s*(\[[^\]]*\])", page):
            try:
                values = json.loads(html.unescape(match.group(1)))
            except (json.JSONDecodeError, TypeError):
                continue
            cls._append_url_values(values, candidates, priority=1, media_only=True)

        # 部分页面会把地址直接作为转义字符串嵌入 HTML。
        for value in re.findall(r"https?://[^\"'\\\s<>]+", page):
            if cls._looks_like_media_url(value):
                candidates.append((3, value))

        for _, value in sorted(enumerate(candidates), key=lambda item: (item[1][0], item[0])):
            video_url = cls._normalise_url(value[1])
            if video_url:
                return video_url
        return ""

    @classmethod
    def _extract_image_urls(cls, page: str) -> list[str]:
        """从图文页面状态数据中提取并去重图片地址。"""
        for data in cls._iter_json_documents(page):
            image_urls = cls._extract_image_urls_from_data(data)
            if image_urls:
                return image_urls
        return []

    @classmethod
    def _find_aweme_images(cls, value) -> list | None:
        """定位当前作品的 aweme.detail.images 图集。"""
        if isinstance(value, dict):
            aweme = value.get("aweme")
            if isinstance(aweme, dict):
                detail = aweme.get("detail")
                if isinstance(detail, dict) and isinstance(detail.get("images"), list):
                    return detail["images"]
            aweme_detail = value.get("aweme_detail")
            if isinstance(aweme_detail, dict) and isinstance(aweme_detail.get("images"), list):
                return aweme_detail["images"]
            for nested in value.values():
                image_items = cls._find_aweme_images(nested)
                if image_items is not None:
                    return image_items
        elif isinstance(value, list):
            for nested in value:
                image_items = cls._find_aweme_images(nested)
                if image_items is not None:
                    return image_items
        return None

    @classmethod
    def _extract_image_urls_from_data(cls, data) -> list[str]:
        """从作品状态、结构化图文或详情接口中提取图片地址。"""
        if isinstance(data, dict) and str(data.get("@type", "")).lower() == "article":
            images = data.get("image")
            if isinstance(images, list):
                return cls._normalise_image_urls([url for url in images if isinstance(url, str)])
        image_items = cls._find_aweme_images(data)
        if image_items is None and isinstance(data, dict):
            image_items = data.get("images")
        return cls._extract_image_urls_from_items(image_items)

    @classmethod
    def _extract_image_urls_from_items(cls, images) -> list[str]:
        """从当前作品的图集条目中提取图片地址。"""
        if not isinstance(images, list):
            return []
        candidates = []
        for image in images:
            if not isinstance(image, dict):
                continue
            url_list = image.get("urlList") or image.get("url_list")
            if isinstance(url_list, list):
                for url in url_list:
                    if isinstance(url, str):
                        candidates.append(url)
                        break
        return cls._normalise_image_urls(candidates)

    @classmethod
    def _normalise_image_urls(cls, candidates: list[str]) -> list[str]:
        """统一还原并去重图片地址。"""
        image_urls = []
        seen = set()
        for value in candidates:
            image_url = cls._normalise_url(value)
            if image_url and image_url not in seen:
                seen.add(image_url)
                image_urls.append(image_url)
        return image_urls

    @classmethod
    def _extract_image_caption_from_data(cls, data) -> str:
        """从作品状态或详情接口数据中提取图文配文。"""
        caption = cls._find_image_caption(data)
        return html.unescape(caption).strip()[:1000] if caption else ""

    @classmethod
    def _extract_image_caption(cls, page: str) -> str:
        """提取与图片条目关联的配文，避免误取背景音乐标题。"""
        for data in cls._iter_json_documents(page):
            caption = cls._find_image_caption(data)
            if caption:
                return html.unescape(caption).strip()[:1000]
        return ""

    @classmethod
    def _find_image_caption(cls, value) -> str:
        """在包含图片字段的对象中查找 desc、content 等配文。"""
        if isinstance(value, dict):
            if str(value.get("@type", "")).lower() == "article" and isinstance(value.get("image"), list):
                for key in ("articleBody", "headline", "description"):
                    text = value.get(key)
                    if isinstance(text, str) and text.strip():
                        return text
            normalized_keys = {key.replace("-", "_").lower() for key in value}
            if normalized_keys & cls.IMAGE_KEYS:
                for key in ("desc", "description", "content", "title"):
                    text = value.get(key)
                    if isinstance(text, str) and text.strip():
                        return text
            for nested in value.values():
                caption = cls._find_image_caption(nested)
                if caption:
                    return caption
        elif isinstance(value, list):
            for nested in value:
                caption = cls._find_image_caption(nested)
                if caption:
                    return caption
        return ""

    @classmethod
    def _iter_json_documents(cls, page: str):
        """读取页面中常见的内嵌 JSON 状态对象。"""
        patterns = [
            r"window\._ROUTER_DATA\s*=\s*(?P<data>\{.*?\})\s*;?\s*</script>",
            r"window\.__INITIAL_STATE__\s*=\s*(?P<data>\{.*?\})\s*;?\s*</script>",
            r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(?P<data>.*?)</script>",
        ]
        script_ids = "|".join(re.escape(script_id) for script_id in cls.JSON_SCRIPT_IDS)
        patterns.append(
            rf"<script[^>]+id=[\"'](?:{script_ids})[\"'][^>]*>(?P<data>.*?)</script>"
        )
        seen = set()
        for pattern in patterns:
            for match in re.finditer(pattern, page, re.IGNORECASE | re.DOTALL):
                raw = html.unescape(match.group("data")).strip().rstrip(";")
                if raw.startswith("%"):
                    raw = unquote(raw)
                if raw in seen:
                    continue
                seen.add(raw)
                try:
                    data, _ = json.JSONDecoder().raw_decode(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                yield data

        # 新版抖音页面将作品状态放在 React Flight 数据中。
        flight_pattern = (
            r"self\.__pace_f\.push\(\[\d+,(?P<data>\"(?:\\.|[^\"\\])*\")\]\)"
        )
        for match in re.finditer(flight_pattern, page):
            try:
                row = json.loads(match.group("data"))
                _, separator, raw = row.partition(":")
                if not separator:
                    continue
                data, _ = json.JSONDecoder().raw_decode(raw.strip())
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
            if raw in seen:
                continue
            seen.add(raw)
            yield data

    @classmethod
    def _collect_video_urls(cls, value, candidates: list[tuple[int, str]]) -> None:
        """递归读取视频字段，兼容新旧页面的字段命名。"""
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized_key = key.replace("-", "_").lower()
                if normalized_key in cls.VIDEO_URL_KEYS:
                    priority = 2 if "download" in normalized_key else 0
                    cls._append_url_values(
                        nested,
                        candidates,
                        priority,
                        media_only=normalized_key == "url_list",
                    )
                elif normalized_key == "url" and isinstance(nested, str):
                    if cls._looks_like_media_url(nested):
                        candidates.append((3, nested))
                cls._collect_video_urls(nested, candidates)
        elif isinstance(value, list):
            for nested in value:
                cls._collect_video_urls(nested, candidates)
        elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                cls._collect_video_urls(json.loads(value), candidates)
            except (json.JSONDecodeError, TypeError):
                pass

    @classmethod
    def _append_url_values(
        cls,
        value,
        candidates: list[tuple[int, str]],
        priority: int,
        media_only: bool = False,
    ) -> None:
        """将地址字段中的字符串或 url_list 统一加入候选列表。"""
        if isinstance(value, str):
            if not media_only or cls._looks_like_media_url(value):
                candidates.append((priority, value))
        elif isinstance(value, list):
            for item in value:
                cls._append_url_values(item, candidates, priority, media_only)
        elif isinstance(value, dict):
            for key, nested in value.items():
                normalized_key = key.replace("-", "_").lower()
                if normalized_key in {"url_list", "urllist", "url", "uri"}:
                    cls._append_url_values(nested, candidates, priority, media_only)

    @staticmethod
    def _normalise_url(value: str) -> str:
        """还原 HTML/JSON 转义并去除抖音水印播放地址标记。"""
        value = html.unescape(value).strip()
        try:
            value = json.loads(f'"{value}"')
        except (json.JSONDecodeError, TypeError):
            value = value.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
        if not value.startswith(("http://", "https://")):
            value = unquote(value)
        if not value.startswith(("http://", "https://")):
            return ""
        return value.replace("http://", "https://", 1).replace("playwm", "play")

    @staticmethod
    def _looks_like_media_url(value: str) -> bool:
        """判断普通 URL 字符串是否更像视频媒体地址。"""
        lowered = value.lower()
        return any(marker in lowered for marker in (
            "snssdk",
            "tiktokcdn",
            "muscdn",
            "bytecdn",
            "douyinvod",
            "play",
            ".mp4",
        ))

    def _extract_video_url_with_ytdlp(self, url: str) -> str:
        """使用已安装的 yt-dlp 作为页面结构变化时的备用解析器。"""
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError

        options = {
            "format": "best[ext=mp4]/best",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": self.config["ytdlp_socket_timeout"],
        }
        if cookie_path := self._get_cookie_path(url):
            options["cookiefile"] = str(cookie_path)
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as error:
            raise RuntimeError(f"yt-dlp 解析失败: {error}") from error
        if not isinstance(info, dict):
            raise RuntimeError("yt-dlp 未返回视频信息")
        entries = info.get("entries")
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            info = entries[0]
        video_url = info.get("url")
        if not isinstance(video_url, str) or not video_url:
            raise RuntimeError("yt-dlp 未返回可下载的视频地址")
        return self._normalise_url(video_url) or video_url
