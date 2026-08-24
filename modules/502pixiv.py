"""Pixiv模块"""

import base64
import html
from http.cookiejar import MozillaCookieJar
import io
import json
from pathlib import Path
import re
import traceback
from urllib.request import Request
import zipfile

import httpx
from PIL import Image

from src.base import Module
from src.utils import Utils


class Pixiv(Module):
    """Pixiv模块"""

    ID = "Pixiv"
    NAME = "Pixiv模块"
    HELP = {
        0: [
            "本模块用于解析Pixiv作品",
        ],
        2: [
            "发送Pixiv作品或PID并@机器人 | 获取Pixiv作品内容",
        ],
    }

    GLOBAL_CONFIG = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "api": "https://www.pixiv.net/ajax/illust/{pid}",
        "api_timeout": 120,
    }

    def __init__(self, event, auth=0):
        """初始化Pixiv PID和作品链接匹配规则"""
        self.pixiv_pattern = (
            r"(?:\bpid(?P<pid>\d+)\b|"
            r"(?:(?:https?:)?//)?(?:[\w-]+\.)?pixiv\.net/"
            r"(?:[^/\s]+/)?"
            r"(?:collections/(?P<collection_id>\d+)|"
            r"(?:artworks|i)/(?P<artwork_id>\d+)|"
            r"member_illust\.php\?[^#\s]*?"
            r"illust_id=(?P<legacy_artwork_id>\d+)))"
        )
        super().__init__(event, auth)

    @Utils.listener(
        lambda self: self.at_or_private()
        and self.au(2)
        and (self.is_reply() or self.match(self.pixiv_pattern))
    )
    def pixiv_download(self):
        """解析并发送Pixiv作品或收藏集"""
        target = self._get_target()
        if not target:
            return
        self.handled = True
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            target_type, target_id = target
            if target_type == "collection":
                source, contents = self.retry(
                    self.get_collection_media,
                    target_id,
                    failed_ok=False,
                )
            else:
                title, caption, image_urls = self.retry(
                    self.get_media,
                    target_id,
                    failed_ok=False,
                )
                source = title
                contents = [(caption, image_urls)]
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 66)
            if not contents or not any(images for _, images in contents):
                raise ReferenceError("Pixiv作品中未找到图片")

            result = self._send_content(contents, source)
            if not Utils.status_ok(result):
                self._send_url_content(contents, source)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            nodes = self.node(f"PID：{target_id}\n错误：{e}")
            self.robot.admin_notify("Pixiv内容处理失败", nodes, self.event)
            return self.reply(str(e), reply=True)

    def _get_target(self) -> tuple[str, str] | None:
        """从当前消息或被回复消息中提取Pixiv作品或集合ID。"""
        messages = [self.event.text]
        if self.is_reply() and (reply := self.get_reply()):
            messages.append(reply)
        for message in messages:
            match = re.search(self.pixiv_pattern, str(message), re.IGNORECASE)
            if match:
                if collection_id := match.group("collection_id"):
                    return "collection", collection_id
                pid = (
                    match.group("pid")
                    or match.group("artwork_id")
                    or match.group("legacy_artwork_id")
                )
                if pid:
                    return "artwork", pid
        return None

    def get_media(self, pid: str) -> tuple[str, str, list[str]]:
        """读取Pixiv作品元数据和使用pixiv.re的图片地址"""
        api_url = self.config["api"].format(pid=pid)
        headers = self._get_request_headers(
            api_url,
            "application/json, text/plain, */*",
            referer=f"https://www.pixiv.net/artworks/{pid}",
        )
        with httpx.Client(
            follow_redirects=True,
            timeout=self.config["api_timeout"],
        ) as client:
            response = client.get(api_url, headers=headers)
            self._raise_for_status(response)
            data = response.json()

        if not isinstance(data, dict) or data.get("error"):
            message = data.get("message", "未知错误") if isinstance(data, dict) else "响应格式错误"
            raise ReferenceError(f"Pixiv接口返回失败: {message}")
        illust = data.get("body")
        if not isinstance(illust, dict):
            raise ReferenceError("Pixiv接口未返回作品数据")

        title = self._get_text(illust.get("illustTitle") or illust.get("title")) or f"PID{pid}"
        author = self._get_text(illust.get("userName"))
        page_count = illust.get("pageCount", 1)
        try:
            page_count = max(1, int(page_count))
        except (TypeError, ValueError):
            page_count = 1
        original_url = ""
        urls = illust.get("urls")
        if isinstance(urls, dict):
            original_url = self._get_text(urls.get("original"))
        if not author:
            raise ReferenceError("Pixiv接口返回的作品作者为空")
        if illust.get("isUgoira") or str(illust.get("illustType")) == "2":
            if gif_data := self._get_ugoira_gif(pid):
                image_urls = [f"base64://{gif_data}"]
            else:
                image_urls = [self._build_image_url(pid, 1, "jpg")]
        else:
            extension = self._get_image_extension(original_url)
            image_urls = [
                self._build_image_url(pid, page, extension)
                for page in range(1, page_count + 1)
            ]
        return title, self._build_caption(pid, author, title, illust), image_urls

    def _get_ugoira_gif(self, pid: str) -> str:
        """下载Pixiv动图帧并转换为Base64 GIF"""
        meta_url = f"{self.config['api'].format(pid=pid)}/ugoira_meta"
        referer = f"https://www.pixiv.net/artworks/{pid}"
        with httpx.Client(
            follow_redirects=True,
            timeout=self.config["api_timeout"],
        ) as client:
            response = client.get(
                meta_url,
                headers=self._get_request_headers(
                    meta_url,
                    "application/json, text/plain, */*",
                    referer=referer,
                ),
            )
            try:
                self._raise_for_status(response)
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 404:
                    raise
                self.printf(f"Pixiv动图元数据不可用，回退发送静态封面: PID{pid}", level="DEBUG")
                return ""
            data = response.json()
            if not isinstance(data, dict) or data.get("error"):
                message = data.get("message", "未知错误") if isinstance(data, dict) else "响应格式错误"
                raise ReferenceError(f"Pixiv动图接口返回失败: {message}")
            ugoira = data.get("body")
            if not isinstance(ugoira, dict):
                raise ReferenceError("Pixiv动图接口未返回动图数据")
            zip_url = self._get_text(ugoira.get("originalSrc") or ugoira.get("src"))
            frames = ugoira.get("frames")
            if not zip_url or not isinstance(frames, list):
                raise ReferenceError("Pixiv动图接口未返回完整帧数据")
            zip_response = client.get(
                zip_url,
                headers=self._get_request_headers(
                    zip_url,
                    "application/zip, application/octet-stream, */*",
                    referer=referer,
                ),
            )
            self._raise_for_status(zip_response)

        gif_data = self._build_ugoira_gif(zip_response.content, frames)
        return base64.b64encode(gif_data).decode("ascii")

    def _get_request_headers(
        self, url: str, accept: str, *, referer: str
    ) -> dict[str, str]:
        """生成Pixiv请求头并按目标域名附加Cookie"""
        headers = {
            "User-Agent": self.config["user_agent"],
            "Accept": accept,
            "Referer": referer,
        }
        if cookie := self._get_cookie_header(url):
            headers["Cookie"] = cookie
        return headers

    def _get_cookie_header(self, url: str) -> str:
        """读取Netscape Cookie文件并生成匹配当前地址的请求头"""
        cookie_path = Path(self.get_data_path("cookies", "pixiv.txt"))
        if not cookie_path.is_file():
            self.printf("未找到Pixiv Cookie文件，使用匿名请求", level="DEBUG")
            return ""

        cookie_jar = MozillaCookieJar(str(cookie_path))
        try:
            cookie_jar.load(ignore_discard=True, ignore_expires=True)
        except (OSError, ValueError) as error:
            self.printf(f"读取Pixiv Cookie失败: {error}", level="DEBUG")
            return ""
        for cookie in cookie_jar:
            cookie.expires = None
        request = Request(url)
        cookie_jar.add_cookie_header(request)
        return request.get_header("Cookie", "")

    @staticmethod
    def _raise_for_status(response) -> None:
        """处理Pixiv登录态相关的HTTP错误"""
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (401, 403):
                raise ReferenceError("Pixiv登录态无效或没有访问权限") from error
            raise

    @staticmethod
    def _build_ugoira_gif(zip_data: bytes, frames: list) -> bytes:
        """按Pixiv动图帧数据生成GIF"""
        images = []
        durations = []
        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
                for frame in frames:
                    if not isinstance(frame, dict):
                        continue
                    file_name = Pixiv._get_text(frame.get("file"))
                    if not file_name:
                        continue
                    try:
                        delay = max(1, int(frame.get("delay", 0)))
                        frame_data = archive.read(file_name)
                    except (KeyError, TypeError, ValueError) as error:
                        raise ReferenceError(f"Pixiv动图帧数据无效: {file_name}") from error
                    with Image.open(io.BytesIO(frame_data)) as image:
                        images.append(image.convert("RGBA"))
                    durations.append(delay)
            if not images:
                raise ReferenceError("Pixiv动图中未找到有效帧")

            output = io.BytesIO()
            images[0].save(
                output,
                format="GIF",
                save_all=True,
                append_images=images[1:],
                duration=durations,
                loop=0,
                disposal=2,
                optimize=True,
            )
            return output.getvalue()
        finally:
            for image in images:
                image.close()

    def get_collection_media(
        self, collection_id: str
    ) -> tuple[str, list[tuple[str, list[str]]]]:
        """读取Pixiv收藏集元数据和图片地址"""
        collection_url = f"https://www.pixiv.net/collections/{collection_id}"
        headers = self._get_request_headers(
            collection_url,
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            referer=collection_url,
        )
        with httpx.Client(
            follow_redirects=True,
            timeout=self.config["api_timeout"],
        ) as client:
            response = client.get(collection_url, headers=headers)
            self._raise_for_status(response)

        match = re.search(
            r'<script[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>',
            response.text,
            re.DOTALL,
        )
        if not match:
            raise ReferenceError("Pixiv收藏集页面未返回结构化数据")
        try:
            page_data = json.loads(match.group(1))
        except json.JSONDecodeError as error:
            raise ReferenceError("Pixiv收藏集页面数据解析失败") from error

        if not isinstance(page_data, dict):
            raise ReferenceError("Pixiv收藏集页面数据格式错误")
        collection = page_data.get("props", {}).get("pageProps", {}).get("collection")
        if not isinstance(collection, dict):
            raise ReferenceError("Pixiv接口未返回收藏集数据")
        title = self._get_text(collection.get("title")) or f"Collection{collection_id}"
        contents = [(self._build_collection_caption(collection_id, collection), [])]
        work_ids = []
        seen_ids = set()
        for tile in collection.get("tiles", []):
            if not isinstance(tile, dict) or tile.get("type") != "Work":
                continue
            work_id = self._get_text(tile.get("workId"))
            if work_id and work_id not in seen_ids:
                seen_ids.add(work_id)
                work_ids.append(work_id)
        for work_id in work_ids:
            media = self.retry(self.get_media, work_id, failed_ok=True)
            if not media:
                continue
            _, _, image_urls = media
            contents.append(("", image_urls))
        if len(contents) == 1:
            raise ReferenceError("Pixiv收藏集中未找到可用作品")
        return title, contents

    @staticmethod
    def _build_collection_caption(collection_id: str, collection: dict) -> str:
        """生成收藏集元数据"""
        description = Pixiv._clean_description(collection.get("caption"))
        tags = Pixiv._get_tags(collection.get("tags"))
        work_count = len(
            {
                tile.get("workId")
                for tile in collection.get("tiles", [])
                if isinstance(tile, dict)
                and tile.get("type") == "Work"
                and tile.get("workId")
            }
        )
        fields = [
            f"收藏集：{Pixiv._get_text(collection.get('title')) or f'Collection{collection_id}'}",
            f"收藏集ID：{collection_id}",
        ]
        if author := Pixiv._get_text(collection.get("userName")):
            fields.append(f"作者：{author}")
        if description:
            fields.append(f"简介：{description}")
        if tags:
            fields.append(f"标签：{tags}")
        if published_date := Pixiv._get_text(collection.get("publishedDateTime")):
            fields.append(f"创建日期：{published_date}")
        for label, value in (
            ("收藏", collection.get("bookmarkCount")),
            ("浏览", collection.get("viewCount")),
        ):
            if (formatted := Pixiv._format_count(value)):
                fields.append(f"{label}：{formatted}")
        if work_count:
            fields.append(f"作品数：{work_count}")
        return "\n".join(fields)

    def _send_content(self, contents: list[tuple[str, list[str]]], source: str):
        """以合并转发发送作品元数据和全部图片"""
        nodes = []
        for caption, image_urls in contents:
            if caption:
                nodes.append(self.node(caption))
            nodes.extend(
                self.node(f"[CQ:image,file={image_url}]")
                for image_url in image_urls
            )
        return self.reply_forward(nodes, source=source, summary="Pixiv")

    def _send_url_content(self, contents: list[tuple[str, list[str]]], source: str) -> str:
        """将作品信息和pixiv.re图片地址组装为普通文本消息"""
        nodes = []
        for caption, image_urls in contents:
            if caption:
                nodes.append(self.node(caption))
            for image_url in image_urls:
                if image_url.startswith("base64://"):
                    image_url = Utils.get_img_url(self.robot, image_url)
                nodes.append(self.node(image_url))
        return self.reply_forward(nodes, source=source, summary="Pixiv")

    @staticmethod
    def _build_caption(pid: str, author: str, title: str, illust: dict) -> str:
        """生成作品元数据"""
        description = Pixiv._clean_description(
            illust.get("illustComment") or illust.get("description")
        )
        tags = Pixiv._get_tags(illust.get("tags"))
        upload_date = Pixiv._get_text(illust.get("uploadDate") or illust.get("createDate"))
        width = illust.get("width")
        height = illust.get("height")
        fields = [f"PID：{pid}", f"作品：{title}", f"作者：{author}"]
        if description:
            fields.append(f"简介：{description}")
        if tags:
            fields.append(f"标签：{tags}")
        if upload_date:
            fields.append(f"上传日期：{upload_date}")
        for label, value in (
            ("收藏", illust.get("bookmarkCount")),
            ("点赞", illust.get("likeCount")),
            ("浏览", illust.get("viewCount")),
            ("评论", illust.get("commentCount")),
        ):
            if (formatted := Pixiv._format_count(value)):
                fields.append(f"{label}：{formatted}")
        if width and height:
            fields.append(f"尺寸：{width}×{height}")
        return "\n".join(fields)

    @staticmethod
    def _get_text(value) -> str:
        """读取接口返回的文本字段"""
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _clean_description(value) -> str:
        """清理作品简介中的HTML标签"""
        description = html.unescape(Pixiv._get_text(value))
        description = re.sub(r"<br\s*/?>", "\n", description, flags=re.IGNORECASE)
        return re.sub(r"<[^>]+>", "", description).strip()

    @staticmethod
    def _get_tags(value) -> str:
        """读取接口返回的作品标签"""
        tags = value.get("tags") if isinstance(value, dict) else value
        if not isinstance(tags, list):
            return ""
        tag_names = []
        for tag in tags:
            if isinstance(tag, dict):
                name = Pixiv._get_text(tag.get("tag"))
            else:
                name = Pixiv._get_text(tag)
            if name:
                tag_names.append(name)
        return ", ".join(tag_names)

    @staticmethod
    def _format_count(value) -> str:
        """格式化接口返回的统计数量"""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return Pixiv._get_text(value)

    @staticmethod
    def _get_image_extension(original_url: str) -> str:
        """从Pixiv原图地址中读取扩展名"""
        match = re.search(r"\.([a-z0-9]+)(?:[?#].*)?$", original_url, re.IGNORECASE)
        return match.group(1).lower() if match else "jpg"

    @staticmethod
    def _build_image_url(pid: str, page: int, extension: str) -> str:
        """生成pixiv.re原图地址"""
        page_suffix = "" if page == 1 else f"-{page}"
        return f"https://pixiv.re/{pid}{page_suffix}.{extension}"
