"""搜图Bot酱模块"""

import io
import re
import time
import traceback

import httpx
from PIL import Image

from src.base import Module
from src.utils import Utils


class Soutubot(Module):
    """搜图Bot酱模块"""

    ID = "Soutubot"
    NAME = "搜图Bot酱模块"
    HELP = {
        2: [
            "图片 + 搜本子 / 回复图片发送搜本子 | 调用搜图Bot酱搜索本子",
        ],
    }
    CONV_CONFIG = {
        "book_search": True,
    }
    PERSISTENT = True

    SITE = "https://soutubot.moe"
    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    )
    RETRY_STATUS = (429, 502, 503, 504)
    BUSY_HINTS = (
        "搜索过于频繁",
        "请求过于频繁",
        "当前搜索任务较多",
        "今日搜索次数已用完",
        "读取搜索结果超时",
        "搜图服务响应超时",
        "无法连接搜图服务",
    )
    GALLERY_ORDER = {"ehentai": 0, "nhentai": 1, "panda": 2, "jmcomic": 3, "danbooru": 4, "gelbooru": 5}
    LEGACY_HOSTS = {"nhentai": "nhentai.net", "ehentai": "e-hentai.org", "panda": "panda.chaika.moe"}

    @Utils.handler(
        lambda self: self.au(2)
        and self.at_or_private()
        and self.conv_config.get("book_search")
        and self.match(r"^(\[.*\])?\s*?(搜索|搜|查询|查|找)(本子|本)\s*?(\[.*\])?$")
    )
    def search_book(self):
        """搜本子"""
        url = ""
        if match := self.match(r"\[CQ:image,.*url=([^,\]]+?),.*\]"):
            url = match.group(1)
        elif msg := self.get_reply():
            if match := re.search(r"\[CQ:image,.*url=([^,\]]+?),.*\]", msg):
                url = match.group(1)
        if url == "":
            return self.reply("请附带搜索图片或回复带图片的消息!")
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            self.printf(f"正在使用搜图Bot酱搜索本子[{url}]...")
            success, data = self.retry(
                self.search_book_soutubot, url, failed_ok=False
            )
            if not success:
                return self.reply(data, reply=True)
            nodes = [self.node(msg) for msg in data]
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 66)
            return self.reply_forward(nodes, source="搜图Bot酱搜索结果")
        except httpx.TimeoutException:
            self.errorf(traceback.format_exc())
            self.reply("搜图Bot酱连接超时，请稍后重试", reply=True)
        except httpx.NetworkError:
            self.errorf(traceback.format_exc())
            self.reply("搜图Bot酱网络连接失败，请稍后重试", reply=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply(f"搜图Bot酱调用失败! {e}", reply=True)

    def search_book_soutubot(
        self, image_url: str, proxies: str = None
    ) -> tuple[bool, str | list]:
        """搜图Bot酱搜本子（免鉴权协议，兼容老版返回）"""
        img = httpx.get(image_url, timeout=15, proxy=proxies)
        img.raise_for_status()
        if not img.content:
            return False, "图片下载为空，请换一张图重试~"
        raw, ctype = img.content, img.headers.get("content-type", "image/jpeg").split(";")[0].lower()
        if ctype in ("image/jpeg", "image/png", "image/webp") and len(raw) <= 5 * 1024:
            data, name = raw, f"image.{ctype.rsplit('/', 1)[-1].replace('jpeg', 'jpg')}"
        else:  # 按网页端规则压缩后上传
            with Image.open(io.BytesIO(raw)) as thumb:
                thumb = thumb.convert("RGB")
                thumb.thumbnail((2000, 2000))
                buf = io.BytesIO()
                thumb.save(buf, format="JPEG", quality=90)
            data, ctype, name = buf.getvalue(), "image/jpeg", "image.jpg"

        timeout = httpx.Timeout(60.0, connect=15.0)
        headers = {"Referer": f"{self.SITE}/", "Origin": self.SITE, "User-Agent": self.UA}
        with httpx.Client(timeout=timeout, proxy=proxies, follow_redirects=True, headers=headers) as client:
            try:
                client.get(f"{self.SITE}/")  # 预热拿 cookies，失败不中断
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            resp, busy = None, ""
            for attempt in range(1, 5):
                try:
                    resp = client.post(
                        f"{self.SITE}/api/search",
                        data={"factor": "1.2"},
                        files={"file": (name, data, ctype)},
                        headers={"Accept": "application/json", "Accept-Language": "zh-CN"},
                    )
                except httpx.TimeoutException:
                    if attempt >= 4:
                        return False, "搜图Bot酱连接超时，请稍后重试"
                    time.sleep(2 * attempt)
                    continue
                except httpx.NetworkError:
                    return False, "搜图Bot酱网络连接失败，请稍后重试"
                if resp.status_code not in self.RETRY_STATUS:
                    break
                try:
                    busy = str(resp.json().get("detail") or "")
                except Exception:  # pylint: disable=broad-exception-caught
                    busy = ""
                if attempt >= 4:
                    return False, busy or "搜图Bot酱繁忙，请稍后重试"
                time.sleep(2 * attempt)
            if resp.status_code == 401:
                return False, "搜图Bot酱鉴权失败，请稍后重试"
            if resp.status_code == 403:
                try:
                    detail = str(resp.json().get("detail") or "")
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = ""
                return False, detail or "搜图Bot酱拒绝跨站请求，请稍后重试"
            if resp.status_code >= 400:
                return False, f"搜图Bot酱请求失败({resp.status_code})，请稍后重试"
            try:
                payload = resp.json()
            except ValueError:
                return False, "搜图Bot酱返回格式异常，请稍后重试"
            if not isinstance(payload, dict):
                return False, "搜图Bot酱返回格式异常，请稍后重试"
            self.printf(f"搜图Bot酱搜索结果: {str(payload)[:2000]}", level="DEBUG")
            if isinstance(payload.get("detail"), str) and payload["detail"].strip():
                detail = payload["detail"].strip()
                if any(hint in detail for hint in self.BUSY_HINTS):
                    return False, f"{detail}，请稍后重试"
                return False, detail
            if isinstance(payload.get("data"), list) and payload["data"]:  # 老版返回兼容
                if msgs := [m for m in (self._fmt_legacy(r) for r in payload["data"][:3]) if m]:
                    return True, msgs
            hits = sorted(
                (h for h in payload.get("results", []) if isinstance(h, dict) and h.get("path_segments")),
                key=lambda h: self._score(h.get("score")),
                reverse=True,
            )[:3]
            if not hits:
                if not payload.get("results"):
                    codes = {w.get("code") for w in payload.get("warnings", []) if isinstance(w, dict)}
                    if "no_search_features" in codes:
                        return False, "图片特征不足，换一张更清晰完整的图再试试~"
                    return False, payload.get("message") or payload.get("error") or "搜图Bot酱返回无结果~"
                return False, "搜图Bot酱返回无有效结果~"
            if not (msgs := [m for m in (self._fmt_hit(h) for h in hits) if m]):
                return False, "搜图Bot酱返回无有效结果~"
            if self._score(hits[0].get("score")) < 28:
                msgs[0] += "\n(低可信度，仅供参考)"
            elif payload.get("partial"):
                msgs[0] += "\n(部分元数据缺失，仅供参考)"
            if payload.get("result_id"):
                msgs[0] += f"\n结果页: {self.SITE}/results/{payload['result_id']}"
            return True, msgs

    @staticmethod
    def _score(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _fmt_hit(self, hit: dict) -> str | None:
        """格式化新版命中的最佳分段"""
        segs = [s for s in hit.get("path_segments", []) if isinstance(s, dict)]
        if not segs:
            return None

        def rank(seg: dict) -> tuple:
            meta = seg.get("metadata") or {}
            key = str((meta.get("source") or {}).get("key") or seg.get("source_key") or "").lower()
            family = str(seg.get("source_family") or "").lower()
            kind = str(meta.get("display_kind") or "").lower()
            found = 0 if seg.get("metadata_status") == "found" and meta else 1
            bucket = 2 if family == "booru" or kind == "booru" else (
                0 if family in ("nhentai", "ehentai", "panda") or kind == "doujinshi" or key in self.GALLERY_ORDER else 1
            )
            return (bucket, found, self.GALLERY_ORDER.get(key, 99))

        seg = sorted(segs, key=rank)[0]
        meta, src = seg.get("metadata") or {}, (seg.get("metadata") or {}).get("source") or {}
        key = str(src.get("key") or seg.get("source_key") or "unknown")
        sid = src.get("id") or seg.get("external_id") or "-"
        title = meta.get("title") or {}
        title = (title.get("primary") or title.get("japanese_or_alias")) if isinstance(title, dict) else title
        lines = [
            re.sub(r"\s+", " ", str(title or "").strip()) or "无标题 (No Title)",
            f"匹配度: {self._score(hit.get('score')):.2f}%",
            f"来源: {src.get('name') or key} #{sid}",
            f"语言: {(meta.get('facts') or {}).get('language') or seg.get('language') or '未知'}",
        ]
        names = []
        for group in ("creators", "works", "characters"):
            for item in meta.get(group) or []:
                name = item.get("name") if isinstance(item, dict) else item
                if name and str(name) not in names:
                    names.append(str(name))
        if names:
            lines.append(f"作者/关联: {'、'.join(names[:6])}")
        url = seg.get("source_url") or src.get("url") or ""
        if url:
            lines.append(f"本子链接: {url}")
        if thumb := seg.get("thumbnail_url"):
            lines.append(f"预览图: {thumb}")
        return "\n".join(lines)

    def _fmt_legacy(self, result: dict) -> str | None:
        """格式化老版返回条目"""
        if not isinstance(result, dict):
            return None
        host = self.LEGACY_HOSTS.get(result.get("source") or "")
        try:
            similarity = f"{float(result.get('similarity', 0)):.2f}%"
        except (TypeError, ValueError):
            similarity = "未知"
        lines = [
            re.sub(r"\s+", " ", str(result.get("title") or "").strip()) or "无标题 (No Title)",
            f"匹配度: {similarity}",
            f"语言: {result.get('language') or '未知'}",
            f"来源: {result.get('source') or '未知'}",
        ]
        if host and result.get("subjectPath"):
            lines.append(f"本子链接: https://{host}{result['subjectPath']}")
        if result.get("previewImageUrl"):
            lines.append(f"预览图: {result['previewImageUrl']}")
        return "\n".join(lines)
