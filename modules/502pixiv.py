"""Pixiv模块"""

import html
import re
import traceback

import httpx

from src.base import Module
from src.utils import Utils


class Pixiv(Module):
    """Pixiv模块"""

    ID = "Pixiv"
    NAME = "Pixiv模块"
    HELP = {
        0: [
            "本模块用于解析Pixiv作品标题、作者和图片，发送PID或Pixiv作品链接并@机器人即可获取内容",
        ],
        2: [
            "发送pidXXXXXXXXX并@机器人 | 获取Pixiv作品内容",
            "发送Pixiv作品链接并@机器人 | 获取Pixiv作品内容",
        ],
    }

    GLOBAL_CONFIG = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "api": "https://www.pixiv.net/ajax/illust/{pid}",
        "api_timeout": 60,
    }

    def __init__(self, event, auth=0):
        """初始化Pixiv PID和作品链接匹配规则"""
        self.pixiv_pattern = (
            r"(?:\bpid(?P<pid>\d+)\b|"
            r"(?:(?:https?:)?//)?(?:[\w-]+\.)?pixiv\.net/"
            r"(?:[^/\s]+/)?"
            r"(?:(?:artworks|i)/(?P<artwork_id>\d+)|"
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
        """解析并发送Pixiv作品标题、作者和图片"""
        pid = self._get_pid()
        if not pid:
            return
        self.handled = True
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            title, caption, image_urls = self.retry(
                self.get_media,
                pid,
                failed_ok=False,
            )
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 66)
            if not image_urls:
                raise ReferenceError("Pixiv作品中未找到图片")

            result = self._send_content(caption, image_urls, title)
            if not Utils.status_ok(result):
                self.reply(self._build_url_message(caption, image_urls), reply=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            nodes = self.node(f"PID：{pid}\n错误：{e}")
            self.robot.admin_notify("Pixiv内容处理失败", nodes, self.event)
            return self.reply(str(e))

    def _get_pid(self) -> str:
        """从当前消息或被回复消息中提取Pixiv作品ID。"""
        messages = [self.event.text]
        if self.is_reply() and (reply := self.get_reply()):
            messages.append(reply)
        for message in messages:
            match = re.search(self.pixiv_pattern, str(message), re.IGNORECASE)
            if match:
                return (
                    match.group("pid")
                    or match.group("artwork_id")
                    or match.group("legacy_artwork_id")
                )
        return ""

    def get_media(self, pid: str) -> tuple[str, str, list[str]]:
        """读取Pixiv作品元数据和使用pixiv.re的图片地址"""
        api_url = self.config["api"].format(pid=pid)
        headers = {
            "User-Agent": self.config["user_agent"],
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://www.pixiv.net/artworks/{pid}",
        }
        with httpx.Client(
            headers=headers,
            follow_redirects=True,
            timeout=self.config["api_timeout"],
        ) as client:
            response = client.get(api_url)
            response.raise_for_status()
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
        extension = self._get_image_extension(original_url)
        image_urls = [
            self._build_image_url(pid, page, extension)
            for page in range(1, page_count + 1)
        ]
        if not author:
            raise ReferenceError("Pixiv接口返回的作品作者为空")
        return title, self._build_caption(pid, author, title, illust), image_urls

    def _send_content(self, caption: str, image_urls: list[str], source: str):
        """以合并转发发送作品元数据和全部图片"""
        nodes = [self.node(caption)]
        nodes.extend(
            self.node(f"[CQ:image,file={image_url}]")
            for image_url in image_urls
        )
        return self.reply_forward(nodes, source=source, summary="Pixiv")

    @staticmethod
    def _build_url_message(caption: str, image_urls: list[str]) -> str:
        """将作品信息和pixiv.re图片地址组装为普通文本消息"""
        return f"{caption}\n" + "".join(image_urls)

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
        size = f"{width}×{height}" if width and height else "未知"
        return "\n".join(
            [
                f"PID：{pid}",
                f"作品：{title}",
                f"作者：{author}",
                f"简介：{description or '无'}",
                f"标签：{tags or '无'}",
                f"上传日期：{upload_date or '未知'}",
                f"收藏：{Pixiv._format_count(illust.get('bookmarkCount'))}",
                f"点赞：{Pixiv._format_count(illust.get('likeCount'))}",
                f"浏览：{Pixiv._format_count(illust.get('viewCount'))}",
                f"评论：{Pixiv._format_count(illust.get('commentCount'))}",
                f"页数：{Pixiv._format_count(illust.get('pageCount') or 1)}",
                f"尺寸：{size}",
            ]
        )

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
        return Pixiv._get_text(value) or "0"

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
