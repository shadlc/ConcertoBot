"""麦麦适配器模块"""

from __future__ import annotations

import asyncio
import base64
import json
import html
import io
import logging
import queue
import re
import time
import traceback
from dataclasses import dataclass
from typing import Any, Mapping

from colorama import Fore
from PIL import Image

from maim_message.client import WebSocketClient
from maim_message.client_factory import create_client_config
import maim_message.log_queue as maim_log_queue
from maim_message.log_queue import LogQueueProcessor
from maim_message.message import (
    APIMessageBase,
    BaseMessageInfo,
    GroupInfo,
    MessageDim,
    ReceiverInfo,
    Seg,
    SenderInfo,
    UserInfo,
)

from src.base import Event, Module
from src.utils import Utils


def _safe_str(value: Any) -> str:
    """将可选值安全转换为空字符串或文本"""
    return "" if value is None else str(value)

def _build_message_id(prefix: str, *parts: Any) -> str:
    """根据平台字段构造稳定且可读的消息 ID"""
    normalized = [re.sub(r"[^a-zA-Z0-9_-]+", "_", _safe_str(part)).strip("_") for part in parts if _safe_str(part)]
    suffix = "_".join(part for part in normalized if part)
    if suffix:
        return f"{prefix}_{suffix}_{int(time.time() * 1000)}"
    return f"{prefix}_{int(time.time() * 1000)}"

def _segment_preview(segment: Seg) -> str:
    """生成隐藏二进制内容的消息段日志预览"""
    payload = segment.to_dict()
    text = re.sub(
        r"type='(image|emoji|voice)',\s?data='.*?'",
        r"type='\1', data='Base64File'",
        str(payload)
    )
    return text if len(text) <= 300 else f"{text[:300]}..."

def _patch_log_queue_processor_shutdown() -> None:
    """因为maim_message0.6.8库的缺陷，LogQueueProcessor._process_batch导致无法正常退出
       因此提供最小补丁修复该问题
    """
    if getattr(LogQueueProcessor, "_concerto_shutdown_patch", False):
        return

    maim_log_queue.logging = logging

    async def _process_batch(self) -> None:
        """批量处理日志队列并允许取消时快速退出"""
        batch = []

        try:
            first_msg = await asyncio.to_thread(
                self._queue.get,
                True,
                self._batch_timeout,
            )
            batch.append(first_msg)

            while len(batch) < self._batch_size:
                try:
                    msg = self._queue.get_nowait()
                    batch.append(msg)
                except queue.Empty:
                    break

        except asyncio.CancelledError:
            return
        except queue.Empty:
            pass
        except Exception: # pylint: disable=broad-exception-caught
            pass

        if batch:
            for log_msg in batch:
                await self._process_log_message(log_msg)

    async def _processor_loop(self) -> None:
        """运行可被正常取消的日志队列处理循环"""
        self._running = True
        while self._running:
            try:
                await self._process_batch()
            except asyncio.CancelledError:
                break

    LogQueueProcessor._process_batch = _process_batch
    LogQueueProcessor._processor_loop = _processor_loop
    LogQueueProcessor._concerto_shutdown_patch = True

@dataclass
class IncomingTarget:
    msg_type: str
    target_id: str
    group_id: str = ""
    user_id: str = ""

class MaimClientRuntime:
    """管理 API-Server 客户端生命周期"""

    def __init__(self, owner: MaiSaka, logger: logging.Logger) -> None:
        """保存模块实例和 maim_message 日志器"""
        self._owner = owner
        self._logger = logger
        self._client: WebSocketClient | None = None
        self._start_lock = asyncio.Lock()
        self._ready = False

    async def start(self) -> bool:
        """启动或复用到麦麦 API-Server 的 WebSocket 连接"""
        async with self._start_lock:
            _patch_log_queue_processor_shutdown()

            if self._client and self._client.is_connected():
                self._ready = True
                return True

            if self._client:
                await self._stop_client()

            config = create_client_config(
                self._owner.config["url"],
                self._owner.config["api_key"],
                platform=self._owner.config["platform"],
                on_message=self._owner.handle_api_message,
                auto_reconnect=True,
                max_reconnect_attempts=0,
                reconnect_delay=2.0,
                log_level="WARNING",
                custom_logger=self._logger,
                enable_connection_log=False,
                enable_message_log=False,
            )
            client = WebSocketClient(config)
            await client.start()
            connected = await client.connect()
            self._client = client
            self._ready = bool(connected and client.is_connected())
            if self._ready:
                self._owner.printf(f"已连接到 {self._owner.config['url']}")
            else:
                self._owner.warnf(f"未能连接到麦麦 API-Server: {self._owner.config['url']}")
            return self._ready

    async def reconnect(self) -> bool:
        """关闭现有客户端并重新建立连接"""
        async with self._start_lock:
            await self._stop_client()
            self._ready = False
        return await self.start()

    async def send_message(self, message: APIMessageBase) -> bool:
        """确保连接可用后发送一条 API-Server 消息"""
        if not await self.start():
            return False
        if self._client is None:
            return False
        return await self._client.send_message(message)

    async def stop(self) -> None:
        """停止客户端并清理连接就绪状态"""
        async with self._start_lock:
            await self._stop_client()
            self._ready = False

    async def _stop_client(self) -> None:
        """停止当前 WebSocket 客户端实例"""
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.stop()
            self._owner.printf(f"已从 {self._owner.config['url']} 断开连接")
        except Exception: # pylint: disable=broad-exception-caught
            self._owner.errorf(f"停止麦麦客户端失败:\n{traceback.format_exc()}")

class ConcertoToMaimCodec:
    """将 ConcertoBot 事件编码为 API-Server 消息"""

    def __init__(self, owner: MaiSaka) -> None:
        """绑定麦麦模块实例用于读取配置和调用工具"""
        self.owner = owner

    async def build_message(self, event: Event, *, content_override: str | None = None) -> APIMessageBase | None:
        """将 ConcertoBot 事件构造成麦麦 API 消息"""
        raw_message = content_override if content_override is not None else _safe_str(event.raw.get("message", event.msg))
        raw_message = self._normalize_raw_message(raw_message)

        segments = await self._parse_message_segments(raw_message, event) if raw_message else []
        if not segments:
            segments = self._build_special_notice_segments(event)
        if not segments:
            return None

        return self._build_api_message(event, Seg(type="seglist", data=segments))

    @staticmethod
    def _normalize_raw_message(raw_message: str) -> str:
        """清理协议端附带的无效文本和多余换行"""
        normalized = _safe_str(raw_message)
        if normalized and "\n" in normalized:
            normalized = re.sub(r"(\s)+", "", normalized)
        return normalized.replace("你收到一个专属红包，请在新版手机QQ查看。", "")

    def _build_special_notice_segments(self, event: Event) -> list[Seg]:
        """将戳一戳和禁言等通知转为文本消息段"""
        if event.sub_type == "poke":
            if not event.group_id:
                return []
            raw_info = event.raw.get("raw_info")
            txt = ""
            if isinstance(raw_info, list) and len(raw_info) > 2 and isinstance(raw_info[2], Mapping):
                txt = _safe_str(raw_info[2].get("txt"))
            target_name = _safe_str(Utils.get_user_name(self.owner.robot, event.target_id) or event.target_id)
            return [Seg(type="text", data=f"[{txt}{target_name}]")]

        if event.notice_type == "group_ban":
            target_name = _safe_str(event.user_name or event.target_name or event.target_id) or "未知用户"
            if event.sub_type == "ban":
                duration = _safe_str(event.raw.get("duration"))
                return [Seg(type="text", data=f"[为{target_name}设置了{duration}秒的禁言]")]
            if event.sub_type == "lift_ban":
                return [Seg(type="text", data=f"[为{target_name}解除了禁言]")]
        return []

    async def _parse_message_segments(self, raw_message: str, event: Event) -> list[Seg]:
        """解析 CQ 码和纯文本为麦麦消息段列表"""
        segments: list[Seg] = []
        cursor = 0
        cq_pattern = re.compile(r"\[CQ:(?P<type>[^,\]]+)(?:,(?P<data>[^\]]*))?\]")
        for match in cq_pattern.finditer(raw_message):
            if match.start() > cursor:
                plain_text = raw_message[cursor:match.start()]
                if plain_text:
                    segments.append(Seg(type="text", data=plain_text))

            cq_type = match.group("type")
            cq_data = self._parse_cq_data(match.group("data") or "")
            built = await self._build_cq_segment(cq_type, cq_data, event)
            if built:
                if isinstance(built, list):
                    segments.extend(built)
                else:
                    segments.append(built)
            cursor = match.end()

        if cursor < len(raw_message):
            tail = raw_message[cursor:]
            if tail:
                segments.append(Seg(type="text", data=tail))

        return [segment for segment in segments if self._segment_has_content(segment)]

    @staticmethod
    def _parse_cq_data(payload: str) -> dict[str, str]:
        """解析 CQ 码参数字符串为字典"""
        data: dict[str, str] = {}
        if not payload:
            return data
        for item in payload.split(","):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            data[key] = html.unescape(value)
        return data

    async def _build_cq_segment(self, cq_type: str, data: Mapping[str, Any], event: Event) -> Seg | list[Seg] | None:
        """将单个 CQ 码转换为麦麦消息段"""
        match cq_type:
            case "at":
                return self._build_at_segment(data, event)
            case "reply":
                target_message_id = _safe_str(data.get("id"))
                if not target_message_id:
                    return None
                return Seg(type="reply", data=target_message_id)
            case "image":
                return await self._build_image_segment(data)
            case "record":
                return await self._build_voice_segment(data)
            case "face":
                face_id = _safe_str(data.get("id"))
                face_content: str = self.qq_face.get(face_id)
                if not face_id:
                    return None
                return Seg(type="text", data=face_content)
            case "json":
                json_data = json.loads(html.unescape(data.get("data")))
                detail = next(iter(json_data.get("meta", {}).values()))
                title = detail.get("title", "")
                url = detail.get("qqdocurl", {})
                desc = detail.get("desc", "")
                tag = f"({detail.get('tag')})" if detail.get("tag") else ""
                return Seg(type="text", data=f"分享[小程序<{title}>({url}):{desc}{tag}]")
            case "forward":
                msg_id = _safe_str(data.get("id"))
                info = Utils.get_forward_msg(self.owner.robot, msg_id)
                if Utils.status_ok(info):
                    msg_list = info.get("data", {}).get("messages")
                    return await self._build_forward_segment(msg_list)
                return Seg(type="text", data="[未知转发消息]")
            case "video":
                return Seg(type="text", data="[视频]")
            case "file":
                file = data.get("file")
                return Seg(type="text", data=f"上传文件[{file}]")
            case "rps":
                return Seg(type="text", data="[猜拳]")
            case "dice":
                return Seg(type="text", data="[骰子]")
            case "shake":
                return Seg(type="text", data="[戳一戳]")
            case "anonymous":
                return Seg(type="text", data="[匿名聊天]")
            case "share":
                return Seg(type="text", data="[分享]")
            case "contact":
                return Seg(type="text", data="[名片]")
            case "location":
                return Seg(type="text", data="[定位]")
            case "music":
                return Seg(type="text", data="[音乐]")
            case "redbag":
                return Seg(type="text", data="[红包]")
            case "poke":
                return Seg(type="text", data="[戳一戳]")
            case "gift":
                return Seg(type="text", data="[礼物]")
            case _:
                return Seg(type="text", data=f"[{cq_type}]")

    def _build_at_segment(self, data: Mapping[str, Any], event: Event) -> Seg:
        """构造麦麦 @ 消息段并尽量补齐目标昵称"""
        target_user_id = _safe_str(data.get("qq"))
        target_name = ""

        if target_user_id and target_user_id != "all":
            try:
                if event.group_id:
                    info = Utils.group_member_info(self.owner.robot, event.group_id, target_user_id)
                    if Utils.status_ok(info):
                        member = info.get("data", {})
                        target_name = _safe_str(member.get("card") or member.get("nickname"))
                if not target_name:
                    target_name = Utils.get_user_name(self.owner.robot, target_user_id)
            except Exception: # pylint: disable=broad-exception-caught
                self.owner.warnf(f"解析@目标失败: {target_user_id}")

        if target_user_id == "all":
            target_name = "全体成员"

        if target_user_id:
            payload = {
                "target_user_id": target_user_id,
                "target_user_nickname": target_name or target_user_id,
                "target_user_cardname": target_name or target_user_id,
            }
            return Seg(type="at", data=payload)

        fallback_name = _safe_str(data.get("name"))
        return Seg(type="text", data=f"@{fallback_name}" if fallback_name else "@未知")

    async def _build_image_segment(self, data: Mapping[str, Any]) -> Seg | None:
        """下载或转换 CQ 图片为麦麦图片/表情段"""
        url_or_file = _safe_str(data.get("url") or data.get("file"))
        if not url_or_file:
            return None

        binary_base64 = await self._resolve_binary_content(url_or_file)
        if not binary_base64:
            return Seg(type="text", data="[图片下载失败]")

        sub_type = _safe_str(data.get("sub_type"))
        if sub_type not in ["0", "4", "9"]:
            if Utils.get_image_format(binary_base64) != "gif":
                binary_base64 = self.owner.convert_image_to_gif(binary_base64)
            return Seg(type="emoji", data=binary_base64)
        return Seg(type="image", data=binary_base64)

    async def _build_voice_segment(self, data: Mapping[str, Any]) -> Seg | None:
        """解析 CQ 语音为麦麦语音段"""
        url_or_file = _safe_str(data.get("url") or data.get("file"))
        if not url_or_file:
            return None

        if url_or_file.startswith("base64://"):
            binary_base64 = url_or_file.removeprefix("base64://")
            return Seg(type="voice", data=binary_base64)

        if url_or_file.startswith("http://") or url_or_file.startswith("https://"):
            binary_base64 = await self._resolve_binary_content(url_or_file)
            if binary_base64:
                return Seg(type="voice", data=binary_base64)
            return Seg(type="text", data="[语音下载失败]")

        try:
            record_info = Utils.get_record(self.owner.robot, url_or_file)
            if Utils.status_ok(record_info):
                file_url = _safe_str(record_info.get("data", {}).get("file"))
                if file_url:
                    binary_base64 = await self._resolve_binary_content(file_url)
                    if binary_base64:
                        return Seg(type="voice", data=binary_base64)
        except Exception: # pylint: disable=broad-exception-caught
            self.owner.warnf("获取语音内容失败")

        return Seg(type="text", data="[语音消息]")

    async def _build_forward_segment(self, msg_list: list[Any] | None) -> list[Seg] | None:
        """将合并转发消息展开为麦麦可读的消息段"""
        if not msg_list:
            return None

        async def process_forward_message(items: list[Any], layer: int) -> list[Seg]:
            """递归解析合并转发中的子消息"""
            seg_list: list[Seg] = []
            process_count = 0
            for sub_msg in items:
                if not isinstance(sub_msg, Mapping):
                    continue

                sender_info = sub_msg.get("sender", {})
                if not isinstance(sender_info, Mapping):
                    sender_info = {}
                user_nickname = _safe_str(sender_info.get("nickname")) or "QQ用户"
                user_nickname_str = f"【{user_nickname}】："

                message_list = sub_msg.get("message")
                if not isinstance(message_list, list) or not message_list:
                    continue

                for message_item in message_list:
                    if not isinstance(message_item, Mapping):
                        continue

                    message_type = _safe_str(message_item.get("type"))
                    message_data = message_item.get("data", {})
                    if not isinstance(message_data, Mapping):
                        message_data = {}

                    if message_type == "forward":
                        if layer >= 3:
                            seg_list.append(Seg(type="text", data=("--" * layer) + f"【{user_nickname}】：【转发消息】\n"))
                            continue

                        contents = message_data.get("content")
                        if not isinstance(contents, list):
                            continue

                        seg_data = await process_forward_message(contents, layer + 1)
                        if not seg_data:
                            continue

                        process_count += 1
                        seg_list.append(Seg(type="text", data=("--" * layer) + f"【{user_nickname}】：合并转发消息内容：\n"))
                        seg_list.extend(seg_data)
                        continue

                    if message_type == "text":
                        text_message = _safe_str(message_data.get("text"))
                        if not text_message:
                            continue
                        seg_list.append(Seg(type="text", data=("--" * layer) + user_nickname_str))
                        seg_list.append(Seg(type="text", data=f"{text_message}\n"))
                        continue

                    if message_type == "image":
                        process_count += 1
                        sub_type = _safe_str(message_data.get("sub_type"))
                        image_url = _safe_str(message_data.get("url") or message_data.get("file"))
                        if not image_url:
                            continue

                        if sub_type in {"0", "4", "9"}:
                            if process_count > 5:
                                seg_data = Seg(type="text", data="[图片]\n")
                            else:
                                seg_data = await self._build_image_segment(message_data)
                                if seg_data is None:
                                    seg_data = Seg(type="text", data="[图片]\n")
                        else:
                            if process_count > 3:
                                seg_data = Seg(type="text", data="[表情]\n")
                            else:
                                seg_data = await self._build_image_segment(message_data)
                                if seg_data is None:
                                    seg_data = Seg(type="text", data="[表情]\n")
                        seg_list.append(Seg(type="text", data=("--" * layer) + user_nickname_str))
                        seg_list.append(seg_data)
                        seg_list.append(Seg(type="text", data="\n"))
                        continue

            return seg_list

        seg_list = await process_forward_message(msg_list, 0)
        return seg_list or None

    async def _resolve_binary_content(self, url_or_file: str) -> str:
        """获取 Base64、HTTP 资源中的二进制内容并转为 Base64"""
        if url_or_file.startswith("base64://"):
            return url_or_file.removeprefix("base64://")
        if url_or_file.startswith("http://") or url_or_file.startswith("https://"):
            try:
                return await Utils.async_get_content_base64(self.owner.robot, url_or_file)
            except Exception: # pylint: disable=broad-exception-caught
                self.owner.warnf(f"下载资源失败: {url_or_file}")
                return ""
        return ""

    def _build_api_message(self, event: Event, segment: Seg) -> APIMessageBase:
        """按 API-Server v2 结构组装完整消息对象"""
        platform = self.owner.config["platform"]
        message_id = _safe_str(event.msg_id) or _build_message_id(
            "concerto",
            event.user_id or self.owner.robot.self_id,
            event.group_id or "private",
        )
        sender_info = SenderInfo(
            group_info=GroupInfo(platform=platform, group_id=_safe_str(event.group_id), group_name=_safe_str(event.group_name))
            if event.group_id
            else None,
            user_info=UserInfo(
                platform=platform,
                user_id=_safe_str(event.user_id or self.owner.robot.self_id),
                user_nickname=_safe_str(event.user_name or self.owner.robot.self_name),
                user_cardname=_safe_str(event.user_card or event.user_name or self.owner.robot.self_name),
            ),
        )
        receiver_info = ReceiverInfo(
            group_info=GroupInfo(platform=platform, group_id=_safe_str(event.group_id), group_name=_safe_str(event.group_name))
            if event.group_id
            else None,
            user_info=UserInfo(
                platform=platform,
                user_id=_safe_str(event.group_id or event.user_id or self.owner.robot.self_id),
                user_nickname=_safe_str(event.group_name or event.user_name),
                user_cardname=_safe_str(event.group_name or event.user_card),
            ),
        )
        return APIMessageBase(
            message_info=BaseMessageInfo(
                platform=platform,
                message_id=message_id,
                time=float(event.time) if event.time else time.time(),
                sender_info=sender_info,
                receiver_info=receiver_info,
                additional_config={
                    "concerto_owner_id": self.owner.owner_id,
                    "concerto_msg_type": "group" if event.group_id else "private",
                    "concerto_sub_type": _safe_str(event.sub_type),
                },
            ),
            message_segment=segment,
            message_dim=MessageDim(
                api_key=self.owner.config["api_key"],
                platform=platform,
            ),
        )

    @staticmethod
    def _segment_has_content(segment: Seg) -> bool:
        """判断消息段是否包含可发送内容"""
        if segment.type == "seglist":
            return bool(segment.data)
        if isinstance(segment.data, Mapping):
            return bool(segment.data)
        return bool(_safe_str(segment.data))

    qq_face: dict = {
        "0": "[表情：惊讶]", "1": "[表情：撇嘴]", "2": "[表情：色]", "3": "[表情：发呆]", "4": "[表情：得意]",
        "5": "[表情：流泪]", "6": "[表情：害羞]", "7": "[表情：闭嘴]", "8": "[表情：睡]", "9": "[表情：大哭]",
        "10": "[表情：尴尬]", "11": "[表情：发怒]", "12": "[表情：调皮]", "13": "[表情：呲牙]", "14": "[表情：微笑]",
        "15": "[表情：难过]", "16": "[表情：酷]", "17": "[表情：菜刀]", "18": "[表情：抓狂]", "19": "[表情：吐]",
        "20": "[表情：偷笑]", "21": "[表情：可爱]", "22": "[表情：白眼]", "23": "[表情：傲慢]", "24": "[表情：饥饿]",
        "25": "[表情：困]", "26": "[表情：惊恐]", "27": "[表情：流汗]", "28": "[表情：憨笑]", "29": "[表情：悠闲]",
        "30": "[表情：奋斗]", "31": "[表情：咒骂]", "32": "[表情：疑问]", "33": "[表情： 嘘]", "34": "[表情：晕]",
        "35": "[表情：折磨]", "36": "[表情：衰]", "37": "[表情：骷髅]", "38": "[表情：敲打]", "39": "[表情：再见]",
        "40": "[表情：撇嘴]", "41": "[表情：发抖]", "42": "[表情：爱情]", "43": "[表情：跳跳]", "46": "[表情：猪头]",
        "49": "[表情：拥抱]", "53": "[表情：蛋糕]", "56": "[表情：刀]", "59": "[表情：便便]", "60": "[表情：咖啡]",
        "63": "[表情：玫瑰]", "64": "[表情：凋谢]", "66": "[表情：爱心]", "67": "[表情：心碎]", "74": "[表情：太阳]",
        "75": "[表情：月亮]", "76": "[表情：赞]", "77": "[表情：踩]", "78": "[表情：握手]", "79": "[表情：胜利]",
        "85": "[表情：飞吻]", "86": "[表情：怄火]", "89": "[表情：西瓜]", "96": "[表情：冷汗]", "97": "[表情：擦汗]",
        "98": "[表情：抠鼻]", "99": "[表情：鼓掌]", "100": "[表情：糗大了]", "101": "[表情：坏笑]", "102": "[表情：左哼哼]",
        "103": "[表情：右哼哼]", "104": "[表情：哈欠]", "105": "[表情：鄙视]", "106": "[表情：委屈]", "107": "[表情：快哭了]",
        "108": "[表情：阴险]", "109": "[表情：左亲亲]", "110": "[表情：吓]", "111": "[表情：可怜]", "112": "[表情：菜刀]",
        "114": "[表情：篮球]", "116": "[表情：示爱]", "118": "[表情：抱拳]", "119": "[表情：勾引]", "120": "[表情：拳头]",
        "121": "[表情：差劲]", "123": "[表情：NO]", "124": "[表情：OK]", "125": "[表情：转圈]", "129": "[表情：挥手]",
        "137": "[表情：鞭炮]", "144": "[表情：喝彩]", "146": "[表情：爆筋]", "147": "[表情：棒棒糖]", "169": "[表情：手枪]",
        "171": "[表情：茶]", "172": "[表情：眨眼睛]", "173": "[表情：泪奔]", "174": "[表情：无奈]", "175": "[表情：卖萌]",
        "176": "[表情：小纠结]", "177": "[表情：喷血]", "178": "[表情：斜眼笑]", "179": "[表情：doge]", "181": "[表情：戳一戳]",
        "182": "[表情：笑哭]", "183": "[表情：我最美]", "185": "[表情：羊驼]", "187": "[表情：幽灵]", "201": "[表情：点赞]",
        "212": "[表情：托腮]", "262": "[表情：脑阔疼]", "263": "[表情：沧桑]", "264": "[表情：捂脸]", "265": "[表情：辣眼睛]",
        "266": "[表情：哦哟]", "267": "[表情：头秃]", "268": "[表情：问号脸]", "269": "[表情：暗中观察]", "270": "[表情：emm]",
        "271": "[表情：吃 瓜]", "272": "[表情：呵呵哒]", "273": "[表情：我酸了]", "277": "[表情：汪汪]", "281": "[表情：无眼笑]",
        "282": "[表情：敬礼]", "283": "[表情：狂笑]", "284": "[表情：面无表情]", "285": "[表情：摸鱼]", "286": "[表情：魔鬼笑]",
        "287": "[表情：哦]", "289": "[表情：睁眼]", "293": "[表情：摸锦鲤]", "294": "[表情：期待]", "295": "[表情：拿到红包]",
        "297": "[表情：拜谢]", "298": "[表情：元宝]", "299": "[表情：牛啊]", "300": "[表情：胖三斤]", "302": "[表情：左拜年]",
        "303": "[表情：右拜年]", "305": "[表情：右亲亲]", "306": "[表情：牛气冲天]", "307": "[表情：喵喵]", "311": "[表情：打call]",
        "312": "[表情：变形]", "314": "[表情：仔细分析]", "317": "[表情：菜汪]", "318": "[表情：崇拜]", "319": "[表情： 比心]",
        "320": "[表情：庆祝]", "323": "[表情：嫌弃]", "324": "[表情：吃糖]", "325": "[表情：惊吓]", "326": "[表情：生气]",
        "332": "[表情：举牌牌]", "333": "[表情：烟花]", "334": "[表情：虎虎生威]", "336": "[表情：豹富]", "337": "[表情：花朵脸]",
        "338": "[表情：我想开了]", "339": "[表情：舔屏]", "341": "[表情：打招呼]", "342": "[表情：酸Q]", "343": "[表情：我方了]",
        "344": "[表情：大怨种]", "345": "[表情：红包多多]", "346": "[表情：你真棒棒]", "347": "[表情：大展宏兔]", "349": "[表情：坚强]",
        "350": "[表情：贴贴]", "351": "[表情：敲敲]", "352": "[表情：咦]", "353": "[表情：拜托]", "354": "[表情：尊嘟假嘟]",
        "355": "[表情：耶]", "356": "[表情：666]", "357": "[表情：裂开]", "360": "[表情：亲亲]", "361": "[表情：狗狗笑哭]",
        "362": "[表情：好兄弟]", "363": "[表情：狗狗可怜]", "364": "[表情：超级赞]", "365": "[表情：狗狗生气]", "366": "[表情：芒狗]",
        "367": "[表情：狗狗疑问]", "392": "[表情：龙年 快乐]", "393": "[表情：新年中龙]", "394": "[表情：新年大龙]", "395": "[表情：略略略]",
        "396": "[表情：狼狗]", "397": "[表情：抛媚眼]"}

class MaimToConcertoCodec:
    """将 API-Server 消息转换为 ConcertoBot 行为"""

    def __init__(self, owner: MaiSaka) -> None:
        """绑定麦麦模块实例用于向 ConcertoBot 发送消息"""
        self.owner = owner

    async def dispatch(self, message: APIMessageBase, metadata: Mapping[str, Any]) -> None:
        """分发来自麦麦 API-Server 的消息或命令"""
        segment = message.message_segment
        self.owner.printf(f"{Fore.CYAN}[FROM] {Fore.RESET}{_segment_preview(segment)}")
        if command_segment := self._extract_command_segment(segment):
            await self._handle_command_segment(command_segment, message)
            return
        if segment.type == "command":
            await self._handle_command_segment(segment, message)
            return

        target = self._resolve_target(message)
        if target is None:
            self.owner.warnf("无法解析麦麦回复目标，消息已忽略")
            return

        rendered = await self._render_segment(segment, target.group_id)
        if not rendered:
            return

        info = None
        msg = _safe_str(rendered.get("message"))
        if not msg:
            self.owner.warnf(f"忽略空的消息: {rendered}")
            return
        if re.sub(r"\[CQ:reply.*?\]", "", msg) == "":
            self.owner.warnf("忽略空的 reply 消息")
            return
        target_id = target.target_id
        if len(msg) > 200 and "CQ:" not in msg:
            source = msg.split("\n")[0]
            if target.msg_type == "group":
                info = Utils.send_forward_msg(self.owner.robot, self.owner.node(msg), group_id=target_id, source=source)
            else:
                info = Utils.send_forward_msg(self.owner.robot, self.owner.node(msg), user_id=target_id, source=source)
        else:
            info = Utils.reply_id(self.owner.robot, target.msg_type, target_id, msg)
        if not Utils.status_ok(info):
            self.owner.warnf(f"发送麦麦回复失败: {info}")

    async def _handle_command_segment(self, segment: Seg, message: APIMessageBase) -> None:
        """执行麦麦侧下发的群管理和消息操作命令"""
        data = segment.data if isinstance(segment.data, Mapping) else {}
        command = _safe_str(data.get("name"))
        args = data.get("args", {})
        if not isinstance(args, Mapping):
            args = {}
        args = dict(args)

        target = self._resolve_target(message)
        group_id = _safe_str(args.get("group_id") or (target.group_id if target else ""))
        qq_id = _safe_str(args.get("qq_id") or args.get("user_id"))

        info = None
        try:
            match command:
                case "GROUP_BAN" | "set_group_ban":
                    if group_id and qq_id:
                        info = Utils.set_group_ban(self.owner.robot, group_id, qq_id, int(args.get("duration", 0) or 0))
                case "SET_GROUP_WHOLE_BAN" | "set_group_whole_ban":
                    if group_id:
                        info = Utils.set_group_whole_ban(self.owner.robot, group_id, bool(args.get("enable")))
                case "SET_GROUP_KICK" | "set_group_kick":
                    if group_id and qq_id:
                        info = Utils.set_group_kick(self.owner.robot, group_id, qq_id)
                case "SEND_POKE" | "send_poke":
                    if qq_id:
                        info = Utils.poke(self.owner.robot, qq_id, group_id or None)
                case "DELETE_MSG" | "delete_msg":
                    message_id = _safe_str(args.get("message_id"))
                    if message_id:
                        info = Utils.del_msg(self.owner.robot, message_id)
                case "SEND_GROUP_AI_RECORD" | "send_group_ai_record":
                    if group_id:
                        info = Utils.send_group_ai_record(
                            self.owner.robot,
                            group_id,
                            _safe_str(args.get("character")),
                            _safe_str(args.get("text")),
                        )
                case _:
                    self.owner.warnf(f"收到未知命令: {command}")
                    return
        except Exception: # pylint: disable=broad-exception-caught
            self.owner.errorf(f"执行麦麦命令 {command} 失败:\n{traceback.format_exc()}")
            return

        if info is None:
            self.owner.warnf(f"命令 {command} 缺少必要参数")
            return
        if Utils.status_ok(info):
            self.owner.printf(f"命令 {command} 执行成功")
        else:
            self.owner.warnf(f"命令 {command} 执行失败: {info}")

    def _extract_command_segment(self, segment: Seg) -> Seg | None:
        """从消息段或单元素段列表中提取命令段"""
        if segment.type == "command":
            return segment

        if segment.type == "dict":
            return self._coerce_command_segment(segment.data)

        if segment.type != "seglist":
            return None

        children = self._coerce_seg_list(segment.data)
        if len(children) != 1:
            return None
        child = children[0]
        if child.type == "command":
            return child
        if child.type == "dict":
            return self._coerce_command_segment(child.data)
        return None

    @staticmethod
    def _coerce_command_segment(data: Any) -> Seg | None:
        """将字典形式的命令描述规范化为 command 段"""
        if not isinstance(data, Mapping):
            return None

        command = _safe_str(data.get("type") or data.get("name"))
        if not command:
            return None

        args = data.get("data")
        if not isinstance(args, Mapping):
            args = data.get("args")
        if not isinstance(args, Mapping):
            args = {}

        return Seg(type="command", data={"name": command, "args": dict(args)})

    async def _render_segment(self, segment: Seg, group_id: str) -> dict[str, Any] | None:
        """将麦麦消息段渲染为 OneBot/CQ 可发送内容"""
        if segment.type == "seglist":
            reply_prefix = ""
            payload_parts: list[str] = []
            for child in self._coerce_seg_list(segment.data):
                child_rendered = await self._render_segment(child, group_id)
                if child_rendered is None:
                    continue
                if child_rendered["type"] == "poke":
                    return child_rendered
                if child_rendered.get("prepend"):
                    reply_prefix += child_rendered["message"]
                    continue
                payload_parts.append(child_rendered["message"])
            if not payload_parts:
                if reply_prefix:
                    return {"type": "message", "message": reply_prefix}
                return None
            return {"type": "message", "message": f"{reply_prefix}{''.join(payload_parts)}"}

        if segment.type == "text":
            text = segment.data
            if match := re.search(r"[\(（][@#](.*?)[\)）]", text):
                user_name = match.group(1)
                user_id = Utils.get_user_id(self.owner.robot, user_name, group_id)
                if re.search(r"[\(（]#(.*?)[\)）]", text):
                    Utils.poke(self.owner.robot, user_id, group_id)
                    text = re.sub(r"[\(（]#(.*?)[\)）]", "", text)
                at_msg = f"[CQ:at,qq={user_id}]" if user_id else f"@{user_name}"
                text = re.sub(r"[\(（]@(.*?)[\)）]", at_msg, text)
            if not text:
                return None
            return {"type": "message", "message": _safe_str(text)}

        if segment.type == "at":
            data = segment.data if isinstance(segment.data, Mapping) else {}
            target_user_id = _safe_str(data.get("target_user_id"))
            display_name = _safe_str(
                data.get("target_user_cardname")
                or data.get("target_user_nickname")
                or target_user_id
            )
            if target_user_id and target_user_id != "all":
                return {"type": "message", "message": f"[CQ:at,qq={target_user_id}]"}
            return {"type": "message", "message": f"@{display_name or '全体成员'}"}

        if segment.type == "reply":
            data = segment.data if isinstance(segment.data, Mapping) else {}
            target_message_id = _safe_str(data.get("target_message_id") or segment.data)
            if not target_message_id or target_message_id == "notice":
                return None
            return {
                "type": "message",
                "message": f"[CQ:reply,id={target_message_id}]",
                "prepend": True,
            }

        if segment.type == "image":
            return {"type": "message", "message": f"[CQ:image,file=base64://{_safe_str(segment.data)},sub_type=0]"}

        if segment.type == "emoji":
            emoji_base64 = _safe_str(segment.data)
            if emoji_base64:
                try:
                    if Utils.get_image_format(emoji_base64) != "gif":
                        emoji_base64 = self.owner.convert_image_to_gif(emoji_base64)
                except Exception: # pylint: disable=broad-exception-caught
                    self.owner.warnf("转换动画表情失败")
            return {
                "type": "message",
                "message": f"[CQ:image,file=base64://{emoji_base64},sub_type=1,summary=&#91;动画表情&#93;]",
            }

        if segment.type == "voice":
            return {"type": "message", "message": f"[CQ:record,file=base64://{_safe_str(segment.data)}]"}

        if segment.type == "poke":
            data = segment.data if isinstance(segment.data, Mapping) else {}
            qq_id = _safe_str(
                data.get("qq_id")
                or data.get("target_id")
                or data.get("user_id")
            )
            if not qq_id:
                return None
            return {
                "type": "poke",
                "qq_id": qq_id,
                "group_id": _safe_str(data.get("group_id") or group_id) or None,
            }

        fallback_map = {
            "forward": "[forward]",
            "file": "[file]",
            "video": "[video]",
        }
        return {"type": "message", "message": fallback_map.get(segment.type, f"[{segment.type}]")}

    def _resolve_target(self, message: APIMessageBase) -> IncomingTarget | None:
        """根据消息元数据解析回复目标会话"""
        info = message.message_info
        additional_config = info.additional_config if isinstance(info.additional_config, Mapping) else {}
        target_group_id = _safe_str(additional_config.get("platform_io_target_group_id"))
        target_user_id = _safe_str(additional_config.get("platform_io_target_user_id"))
        if target_group_id:
            return IncomingTarget(msg_type="group", target_id=target_group_id, group_id=target_group_id)
        if target_user_id:
            return IncomingTarget(msg_type="private", target_id=target_user_id, user_id=target_user_id)

        receiver = info.receiver_info
        if receiver is not None:
            if receiver.group_info and receiver.group_info.group_id:
                group_id = _safe_str(receiver.group_info.group_id)
                return IncomingTarget(msg_type="group", target_id=group_id, group_id=group_id)
            if receiver.user_info and receiver.user_info.user_id:
                user_id = _safe_str(receiver.user_info.user_id)
                return IncomingTarget(msg_type="private", target_id=user_id, user_id=user_id)

        sender = info.sender_info
        if sender is not None:
            if sender.group_info and sender.group_info.group_id:
                group_id = _safe_str(sender.group_info.group_id)
                return IncomingTarget(msg_type="group", target_id=group_id, group_id=group_id)
            if sender.user_info and sender.user_info.user_id:
                user_id = _safe_str(sender.user_info.user_id)
                return IncomingTarget(msg_type="private", target_id=user_id, user_id=user_id)

        return None

    @staticmethod
    def _coerce_seg_list(data: Any) -> list[Seg]:
        """将原始列表中的字典元素安全转换为 Seg 对象"""
        if not isinstance(data, list):
            return []
        coerced: list[Seg] = []
        for item in data:
            if isinstance(item, Seg):
                coerced.append(item)
            elif isinstance(item, Mapping):
                try:
                    coerced.append(Seg.from_dict(dict(item)))
                except Exception: # pylint: disable=broad-exception-caught
                    continue
        return coerced

class MaiSaka(Module):
    """麦麦适配器模块 (API-Server v2)"""

    ID = "MaiSaka"
    NAME = "麦麦适配器模块"
    HELP = {
        0: [
            "本模块用于对接麦麦MaiSaka，支持消息、@、戳一戳与群管理指令交互"
        ],
        1: [
            "[开启|关闭]麦麦 | 开启或关闭对接麦麦功能",
            "重新连接麦麦 | 重置麦麦的连接",
        ],
    }
    GLOBAL_CONFIG = {
        "platform": "qq",
        "url": "",
        "api_key": "",
    }
    CONV_CONFIG = {
        "enable": True,
        "blacklist": [],
    }
    HANDLE_NOTICE = True
    AUTO_INIT = True

    def __init__(self, event, auth=0):
        """初始化麦麦适配器、编解码器和运行时连接"""
        super().__init__(event, auth)
        if self.ID in self.robot.persist_mods:
            return
        self.robot.persist_mods[self.ID] = self
        self.failed_times = 0
        self.robot.func["notify_maisaka"] = self.notify_maisaka

        logger = logging.getLogger("maim_message")
        logger.level = logging.WARNING
        Utils.apply_formatter(logger, self.ID)
        self.api_logger = logger
        self.codec_out = ConcertoToMaimCodec(self)
        self.codec_in = MaimToConcertoCodec(self)
        self.runtime = MaimClientRuntime(self, logger)

        if not self.config["url"]:
            self.errorf("未配置麦麦 API-Server 地址，模块已禁用")
            return
        if not self.config["api_key"]:
            self.errorf("未配置麦麦 API Key，模块已禁用")
            return

        asyncio.run_coroutine_threadsafe(self.runtime.start(), self.robot.loop)

    def premise(self):
        """复用持久化适配器状态并检查必要连接配置"""
        if self.ID in self.robot.persist_mods:
            maim: MaiSaka = self.robot.persist_mods[self.ID]
            self.failed_times = maim.failed_times
            self.runtime = maim.runtime
            self.codec_out = maim.codec_out
            self.codec_in = maim.codec_in
        return bool(self.config.get("url") and self.config.get("api_key"))

    def shutdown(self) -> None:
        """停止麦麦 API-Server 连接"""
        asyncio.run_coroutine_threadsafe(self.runtime.stop(), self.robot.loop).result(timeout=5)

    async def handle_api_message(self, message: APIMessageBase, metadata: dict) -> None:
        """处理麦麦 API-Server 推送的消息"""
        try:
            await self.codec_in.dispatch(message, metadata)
        except Exception: # pylint: disable=broad-exception-caught
            self.errorf(f"处理来自麦麦的消息失败:\n{traceback.format_exc()}")

    async def construct_message(self, event: Event | None = None, *, content_override: str | None = None) -> APIMessageBase | None:
        """基于当前或指定事件构造麦麦 API 消息"""
        event = event or self.event
        return await self.codec_out.build_message(event, content_override=content_override)

    async def send_to_maim(self, message: APIMessageBase) -> bool:
        """发送消息到麦麦并维护连续失败计数"""
        try:
            self.printf(f"{Fore.GREEN}[TO] {Fore.RESET}{_segment_preview(message.message_segment)}")
            send_status = await self.runtime.send_message(message)
            persist_mod = self.robot.persist_mods.get(self.ID, self)
            if not send_status:
                persist_mod.failed_times += 1
                self.failed_times = persist_mod.failed_times
                raise RuntimeError("API-Server 发送失败或连接不可用")
            persist_mod.failed_times = 0
            self.failed_times = 0
            return True
        except Exception as exc:
            error_msg = str(exc) if isinstance(exc, RuntimeError) else traceback.format_exc()
            self.errorf(f"发送消息失败: {error_msg}(第{self.failed_times}次)")
            if self.failed_times == 3:
                self.robot.admin_notify(f"多次尝试发送消息至麦麦机器人后失败，请检查连接\n{error_msg}")
            return False

    def convert_image_to_gif(self, image_base64: str) -> str:
        """将 Base64 图片转为 GIF"""
        try:
            image_bytes = base64.b64decode(image_base64)
            image = Image.open(io.BytesIO(image_bytes))
            output_buffer = io.BytesIO()
            image.save(output_buffer, format="GIF")
            output_buffer.seek(0)
            return base64.b64encode(output_buffer.read()).decode("utf-8")
        except Exception as exc:
            self.errorf(f"图片转换为 GIF 失败: {exc}")
            return image_base64

    @Utils.handler(lambda self: self.at_or_private() and self.au(1)
         and self.match(r"^(开启|启用|打开|记录|启动|关闭|禁用|取消)麦麦$"))
    def enable_maibot(self):
        """启用麦麦"""
        if self.match(r"(开启|启用|打开|记录|启动)"):
            self.conv_config["enable"] = True
            self.save_config()
            self.reply("麦麦机器人已开启")
            return
        if self.match(r"(关闭|禁用|取消)"):
            self.conv_config["enable"] = False
            self.save_config()
            self.reply("麦麦机器人已关闭")

    @Utils.handler(lambda self: self.at_or_private() and self.au(1) and self.match(r"^重新连接麦麦$"))
    def restart_maibot(self):
        """重新连接麦麦"""
        try:
            ok = asyncio.run_coroutine_threadsafe(self.runtime.reconnect(), self.robot.loop).result()
            self.reply("已重置连接麦麦服务" if ok else "重连失败，请检查麦麦服务状态")
        except Exception: # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply("重连失败，请检查日志")

    @Utils.handler(lambda self: self.ID in self.robot.persist_mods
         and self.conv_config.get("enable")
         and self.event.user_id not in self.conv_config.get("blacklist")
         and (self.event.msg or self.event.sub_type == "poke"))
    def send_maibot(self):
        """发送至麦麦"""
        async def send_task() -> None:
            """异步构造并发送当前事件到麦麦"""
            try:
                message = await self.construct_message()
                if message is not None:
                    await self.send_to_maim(message)
            except Exception: # pylint: disable=broad-exception-caught
                self.errorf(traceback.format_exc())

        asyncio.run_coroutine_threadsafe(send_task(), self.robot.loop)

    def notify_maisaka(self, content: str, group_id: str):
        """主动通知麦麦 (供其他模块调用)"""
        if self.ID not in self.robot.persist_mods:
            return
        if not self.config.get(f"g{group_id}", {}).get("enable"):
            self.warnf(f"群{group_id}未开启麦麦")
            return

        async def send_task() -> None:
            """构造伪事件并主动推送到麦麦"""
            try:
                fake_event = Event(self.robot)
                fake_event.msg = content
                fake_event.time = time.time()
                fake_event.user_id = str(self.robot.self_id)
                fake_event.user_name = self.robot.self_name
                fake_event.user_card = self.robot.self_name
                fake_event.group_id = str(group_id)
                fake_event.group_name = Utils.get_group_name(self.robot, str(group_id)) or ""
                fake_event.target_id = ""
                fake_event.raw = {
                    "message": content,
                    "group_id": group_id,
                    "user_id": self.robot.self_id,
                    "time": fake_event.time,
                }

                message = await self.construct_message(fake_event, content_override=content)
                if message is not None:
                    await self.send_to_maim(message)
            except Exception: # pylint: disable=broad-exception-caught
                self.errorf(traceback.format_exc())

        asyncio.run_coroutine_threadsafe(send_task(), self.robot.loop)
