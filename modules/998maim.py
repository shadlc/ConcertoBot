"""麦麦适配器模块"""

import asyncio
import base64
import json
import html
import io
import logging
import re
import threading
import time
import traceback
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, asdict, field

from colorama import Fore
from PIL import Image

try:
    from maim_message import (
        MessageBase,
        BaseMessageInfo,
        FormatInfo,
        UserInfo,
        GroupInfo,
        Router,
        RouteConfig,
        TargetConfig,
        SenderInfo,
        Seg,
    )
except ImportError:
    class MessageBase: pass
    class BaseMessageInfo: pass
    class FormatInfo: pass
    class UserInfo: pass
    class GroupInfo: pass
    class Router: pass
    class RouteConfig: pass
    class TargetConfig: pass
    class SenderInfo: pass
    class Seg: pass

from src.utils import (
    Event,
    Module,
    apply_formatter,
    del_msg,
    get_forward_msg,
    async_get_content_base64,
    get_group_name,
    get_image_format,
    get_msg,
    get_record,
    get_user_id,
    get_user_name,
    group_member_info,
    poke,
    reply_id,
    send_forward_msg,
    send_group_ai_record,
    send_msg,
    set_group_ban,
    set_group_kick,
    set_group_whole_ban,
    status_ok,
    via,
)

class ReplyContentType:
    TEXT = "text"
    IMAGE = "image"
    EMOJI = "emoji"
    COMMAND = "command"
    VOICE = "voice"
    FORWARD = "forward"
    HYBRID = "hybrid"

@dataclass
class ReplyContent:
    content_type: str
    content: Union[str, Dict, List, Any]

    def to_dict(self):
        return asdict(self)


class Maim(Module):
    """麦麦适配器模块 (适配 Maimbot v0.3.3+ 架构)"""

    ID = "Maim"
    NAME = "麦麦适配器模块"
    HELP = {
        0: [
            "本模块用于对接新版麦麦机器人，支持混合消息与指令交互"
        ],
        1: [
            "[开启|关闭]麦麦 | 开启或关闭麦麦机器人功能",
            "重新连接麦麦 | 重置麦麦机器人的连接",
        ],
    }
    GLOBAL_CONFIG = {
        "platform": "qq",
        "url": "",
    }
    CONV_CONFIG = {
        "enable": True,
        "blacklist": [],
    }
    HANDLE_NOTICE = True
    AUTO_INIT = True

    def __init__(self, event, auth=0):
        super().__init__(event, auth)
        if self.ID in self.robot.persist_mods:
            return
        self.robot.persist_mods[self.ID] = self
        if not self.config["url"]:
            self.errorf("未配置MaiMBot链接地址，模块已禁用")
            return
        self.robot.func["notify_maimbot"] = self.notify_maimbot
        logger = logging.getLogger("maim_message")
        apply_formatter(logger, self.ID)
        self.loop = asyncio.get_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        
        # 初始化连接
        try:
            target_config = TargetConfig(url=self.config["url"], token=None)
            route_config = RouteConfig({self.config["platform"]: target_config})
            self.router = Router(route_config)
            self.router.register_class_handler(self.handle_maimbot_message)
        except Exception as e:
            self.errorf(f"初始化 Router 失败: {e}")
            
        self.failed_times = 0
        self.listening()

    def premise(self):
        if self.ID in self.robot.persist_mods:
            maim: Maim = self.robot.persist_mods[self.ID]
            self.failed_times = maim.failed_times
            self.router = maim.router
            self.loop = maim.loop
        return self.config["url"]

    def listening(self):
        """开启监听"""
        if hasattr(self, 'router'):
            asyncio.run_coroutine_threadsafe(self.router.run(), self.loop)

    async def handle_maimbot_message(self, raw_message: dict):
        """
        处理 MaiMBot 回复的消息 (接收逻辑)
        新版 Maimbot 返回的数据通常包含 reply_data 列表
        """
        try:
            '''提取消息段列表'''
            message_segments = []
            if "reply_data" in raw_message:
                message_segments = raw_message.get("reply_data", [])
            elif "message_segment" in raw_message:
                seg = raw_message.get("message_segment")
                message_segments = [seg] if isinstance(seg, dict) else seg
            
            '''日志打印简化'''
            log_str = json.dumps(message_segments, ensure_ascii=False)
            if len(log_str) > 200:
                log_str = log_str[:200] + "..."
            self.printf(f"{Fore.CYAN}[FROM Maim] {Fore.RESET}{log_str}")

            for segment in message_segments:
                '''统一获取 type'''
                c_type = segment.get("content_type") or segment.get("type")
                
                if c_type == ReplyContentType.COMMAND:
                    await self.send_command(segment, raw_message)
                else:
                    await self.send_content(segment, raw_message)

        except Exception:
            self.errorf(f"处理来自MaiMBot的消息失败!\n{traceback.format_exc()}")

    async def send_command(self, segment: Dict, raw_message: Dict) -> None:
        """处理命令类消息"""
        content = segment.get("content") or segment.get("data")
        if not content:
            return

        command: str = content.get("name")
        args: Dict = content.get("args", {})
        
        group_id = args.get("group_id")
        qq_id = args.get("qq_id")

        if not group_id:
             if raw_message.get("chat_info_platform") == "group":
                 group_id = raw_message.get("chat_id")
             elif raw_message.get("group_info"):
                 group_id = raw_message.get("group_info", {}).get("group_id")

        info = None
        try:
            match command:
                case "GROUP_BAN":
                    info = set_group_ban(self.robot, group_id, qq_id, args.get("duration"))
                case "set_group_whole_ban":
                    info = set_group_whole_ban(self.robot, group_id, args.get("enable"))
                case "set_group_kick":
                    info = set_group_kick(self.robot, group_id, qq_id)
                case "send_poke":
                    info = poke(self.robot, qq_id, group_id)
                case "delete_msg":
                    info = del_msg(self.robot, args.get("message_id"))
                case "send_group_ai_record":
                    info = send_group_ai_record(
                        self.robot, group_id, args.get("character"), args.get("text")
                    )
                case _:
                    self.warnf(f"收到未知命令: {command}")
                    return
        except Exception as e:
            self.errorf(f"处理命令 {command} 时发生错误: {e}")
            return

        if status_ok(info):
            self.printf(f"命令 {command} 执行成功")
        else:
            self.warnf(f"命令 {command} 执行失败: {info}")

    async def send_content(self, segment: Dict, raw_message: Dict) -> None:
        """处理内容发送 (修复ID解析版)"""
        
        target_id = raw_message.get("chat_id")
        msg_type = "private"
        chat_platform = raw_message.get("chat_info_platform")

        msg_info = raw_message.get("message_info", {})
        
        if not target_id:
            group_data = msg_info.get("group_info")
            if group_data and group_data.get("group_id"):
                target_id = group_data.get("group_id")
                chat_platform = "group"
            

            if not target_id:
                user_data = msg_info.get("sender_info", {}).get("user_info") or \
                            msg_info.get("user_info")
                if user_data and user_data.get("user_id"):
                    target_id = user_data.get("user_id")
                    chat_platform = "private"


        if chat_platform == "group":
            msg_type = "group"
        elif not chat_platform:

            if msg_info.get("group_info"): 
                msg_type = "group"
            else: 
                msg_type = "private"


        if not target_id:
            self.warnf(f"无法解析目标ID，原始数据结构: {list(raw_message.keys())}")
            return

        msg_str = self.parse_reply_content(segment)
        if not msg_str:
            return

        if len(self.robot.self_message) > 0:
            past_msg = self.robot.self_message[-1].get("message")
            if re.sub(r"\[CQ:.*?\]", "", msg) != "" and re.sub(r"\[CQ:.*?\]", "", msg) == re.sub(r"\[CQ:.*?\]", "", past_msg):
                self.warnf("消息与上一条消息相同，未发送")
                return

        info = None

        if len(msg_str) > 100 and "CQ:" not in msg_str:
             source = msg_str.split("\n")[0]
             if msg_type == "group":
                 info = send_forward_msg(self.robot, self.node(msg_str), group_id=target_id, source=source)
             else:
                 info = send_forward_msg(self.robot, self.node(msg_str), user_id=target_id, source=source)
        else:
            info = reply_id(self.robot, msg_type, target_id, msg_str)

        if status_ok(info):
            qq_message_id = info["data"].get("message_id")
            mmc_message_id = message_base.message_info.message_id
            message_base.message_segment = Seg(
                type="notify",
                data={
                    "sub_type": "echo",
                    "echo": mmc_message_id,
                    "actual_id": qq_message_id,
                },
            )
            await self.send_to_maim(message_base)

    def handle_seg(self, segment: Seg, group_id: int | None = None) -> str:
        """处理消息结构"""
        def build_payload(payload: str, msg: str, is_reply: bool = False) -> list:
            """构建发送的消息体"""
            if is_reply:
                temp = ""
                temp += msg
                for i in payload:
                    if i.get("type") == "reply":
                        # 多个回复，使用最新的回复
                        continue
                    temp += i
                return temp
            else:
                payload += msg
                return payload

        def process_message(seg: Seg, payload: str) -> str:
            new_payload = payload
            if seg.type == "reply":
                target_id = seg.data
                if target_id == "notice":
                    return payload
                new_payload = build_payload(payload, f"[CQ:reply,id={target_id}]", True)
            elif seg.type == "text":
                text = seg.data
                if match := re.search(r"[\(（][@#](.*?)[\)）]", text):
                    user_name = match.group(1)
                    user_id = get_user_id(self.robot, user_name, group_id)
                    if re.search(r"[\(（]#(.*?)[\)）]", text):
                        poke(self.robot, user_id, group_id)
                        text = re.sub(r"[\(（]#(.*?)[\)）]", "", text)
                    at_msg = f"[CQ:at,qq={user_id}]" if user_id else f"@{user_name}"
                    text = re.sub(r"[\(（]@(.*?)[\)）]", at_msg, text)
                if not text:
                    return payload
                new_payload = build_payload(payload, text, False)
            elif seg.type == "face":
                face_id = seg.data
                new_payload = build_payload(payload, f"[CQ:face,id={face_id}]", False)
            elif seg.type == "image":
                image = seg.data
                new_payload = build_payload(payload, f"[CQ:image,file=base64://{image},sub_type=0]", False)
            elif seg.type == "emoji":
                emoji = seg.data
                image_format = get_image_format(emoji)
                if image_format != "gif":
                    emoji = self.convert_image_to_gif(emoji)
                new_payload = build_payload(payload, f"[CQ:image,file=base64://{emoji},sub_type=1,summary=&#91;动画表情&#93;]", False)
            elif seg.type == "voice":
                voice = seg.data
                new_payload = build_payload(payload, f"[CQ:voice,file=base64://{voice}]", False)
            elif seg.type == "voiceurl":
                voice_url = seg.data
                new_payload = build_payload(payload, f"[CQ:record,file={voice_url}]", False)
            elif seg.type == "music":
                song_id = seg.data
                new_payload = build_payload(payload, f"[CQ:music,file={song_id}]", False)
            elif seg.type == "videourl":
                video_url = seg.data
                new_payload = build_payload(payload, f"[CQ:video,file={video_url}]", False)
            elif seg.type == "file":
                file_path = seg.data
                new_payload = build_payload(payload, f"[CQ:file,file=file://{file_path}]", False)
            return new_payload

    def parse_reply_content(self, segment: Dict) -> str:
        """递归解析 ReplyContent 为 CQ 码字符串"""
        c_type = segment.get("content_type") or segment.get("type")
        content = segment.get("content") or segment.get("data")
        
        payload = ""
        
        if c_type == ReplyContentType.TEXT:
            payload = str(content)
            
        elif c_type == ReplyContentType.IMAGE:
            payload = f"[CQ:image,file=base64://{content},sub_type=0]"
            
        elif c_type == ReplyContentType.EMOJI:

            try:
                 fmt = get_image_format(content)
                 if fmt != "gif":
                     content = self.convert_image_to_gif(content)
            except Exception:
                pass
            payload = f"[CQ:image,file=base64://{content},sub_type=1]"
            
        elif c_type == ReplyContentType.VOICE:
            payload = f"[CQ:record,file=base64://{content}]"
            
        elif c_type == ReplyContentType.HYBRID:

            if isinstance(content, list):
                for item in content:
                    payload += self.parse_reply_content(item)
                    
        elif c_type == ReplyContentType.FORWARD:

            payload = "[转发消息]"
            
        return payload

    async def handle_msg_to_maim(self, raw: dict, in_reply: bool = False) -> List[Dict]:
        """
        处理实际消息 -> 转为 Maimbot 识别的 ReplyContent 列表
       
        """
        msg: str = raw.get("message")
        if not msg:
            return []
            
        msg = re.sub(r"(\s)+", "", msg) if msg and "\n" in msg else msg or ""
        reply_contents: List[Dict] = []
        
        '''解析 CQ 码'''
        while re.search(r"(\[CQ:(.+?),(.+?)\])", msg):
            match_obj = re.search(r"(\[CQ:(.+?),(.+?)\])", msg)
            if not match_obj: break
            
            cq_code, cq_type, cq_data = match_obj.groups()
            
            data = {}
            for item in cq_data.split(","):
                if "=" in item:
                    k, v = item.split("=", maxsplit=1)
                    data[k] = html.unescape(v)

            rc = None
            match cq_type:
                case "face":
                    face_id = str(data.get("id"))
                    # 将 QQ 表情转为文本形式供 LLM 理解
                    rc = ReplyContent(ReplyContentType.TEXT, f"[Face:{face_id}]")
                case "image":
                    url = data.get("url") or data.get("file")
                    sub_type = data.get("sub_type")
                    try:
                        img_b64 = await async_get_content_base64(self.robot, url)
                        if sub_type == "1": # 动画表情
                             rc = ReplyContent(ReplyContentType.EMOJI, img_b64)
                        else:
                             rc = ReplyContent(ReplyContentType.IMAGE, img_b64)
                    except Exception as e:
                        self.warnf(f"图片下载失败: {e}")
                        rc = ReplyContent(ReplyContentType.TEXT, "[图片下载失败]")
                case "record":
                    rc = ReplyContent(ReplyContentType.TEXT, "[语音消息]")
                case "at":
                    qq = data.get("qq")
                    user_name = get_user_name(self.robot, qq)
                    rc = ReplyContent(ReplyContentType.TEXT, f"@{user_name}")
                case "reply":
                    pass
                case "json":
                    rc = ReplyContent(ReplyContentType.TEXT, "[分享卡片]")
                case _:
                    rc = ReplyContent(ReplyContentType.TEXT, f"[{cq_type}]")

            if rc:
                reply_contents.append(rc.to_dict())
            
            msg = msg.replace(cq_code, "", 1)

        if msg:
            reply_contents.append(ReplyContent(ReplyContentType.TEXT, msg).to_dict())
            
        return reply_contents

    async def construct_message(self, event: Event = None) -> Dict | None:
        """
        构造发送给 Maimbot 的消息体
        适配 message_data_model.py 中的 MessageAndActionModel 结构
        """
        event = event or self.event
        
        user_id = str(event.user_id)

        chat_id = str(event.group_id) if event.group_id else user_id

        reply_contents = await self.handle_msg_to_maim(event.raw)
        if not reply_contents:
            return None
            
        '''构造符合 MessageAndActionModel 的字典'''
        message_payload = {
            "chat_id": chat_id,
            "time": time.time(),
            "user_id": user_id,
            "user_platform": self.config["platform"],
            "user_nickname": event.user_name,
            "user_cardname": event.user_card,
            
            "chat_info_platform": "group" if event.group_id else "private",
            
            "processed_plain_text": event.msg,
            "display_message": event.msg,
            
            "reply_data": reply_contents,
            
            "message_segment": reply_contents, 
        }
        
        return message_payload

    async def send_to_maim(self, payload: Dict) -> bool:
        '''发送消息到 MaiMBot'''
        try:
            log_payload = payload.copy()
            if "reply_data" in log_payload:
                 log_payload["reply_data"] = "[Content Data]"
            self.printf(f"{Fore.GREEN}[TO Maim] {Fore.RESET}{json.dumps(log_payload, ensure_ascii=False)}")
            
            '''构造 UserInfo'''
            user_info = UserInfo(
                platform=str(payload.get("user_platform", "qq")),
                user_id=str(payload.get("user_id", "")),
                user_nickname=str(payload.get("user_nickname", "")),
                user_cardname=str(payload.get("user_cardname", ""))
            )

            '''构造 GroupInfo'''
            group_info = None
            
            if payload.get("chat_info_platform") == "group":
                group_info = GroupInfo(
                    platform=str(payload.get("user_platform", "qq")),
                    group_id=str(payload.get("chat_id", ""))
                )
            
            '''构造 SenderInfo'''
            sender_info = SenderInfo(
                group_info=group_info,
                user_info=user_info
            )

            '''构造 MessageSegment'''
            raw_segments = payload.get("reply_data") or payload.get("message_segment", [])
            seg_list = []
            
            for item in raw_segments:
                s_type = item.get("type") or item.get("content_type")
                s_data = item.get("data") or item.get("content")
                
                if s_type == "text":
                    s_data = str(s_data)
                
                seg_list.append(Seg(type=s_type, data=s_data))

            main_segment = Seg(type="seglist", data=seg_list)

            '''构造 BaseMessageInfo'''
            base_info = BaseMessageInfo(
                platform=str(payload.get("user_platform", "qq")),
                message_id=str(int(payload.get("time", 0))),
                time=float(payload.get("time", time.time())),
                sender_info=sender_info,

                user_info=user_info,
                group_info=group_info
            )

            '''构造最终的 MessageBase'''
            message_obj = MessageBase(
                message_info=base_info,
                message_segment=main_segment,
                raw_message=payload.get("display_message", "")
            )

            '''发送'''
            send_status = await self.router.send_message(message_obj)

            maim = self.robot.persist_mods[self.ID]
            if not send_status:
                maim.failed_times += 1
                self.failed_times = maim.failed_times
                raise RuntimeError("路由未正确配置或连接异常")
            
            maim.failed_times = 0
            return send_status

        except Exception as e:
            error_msg = f"发送消息失败: {traceback.format_exc()}"
            if isinstance(e, RuntimeError):
                error_msg = f"{e}"
            self.errorf(f"{error_msg}(第{self.failed_times}次)")
            
            if self.failed_times == 3:
                self.robot.admin_notify(f"多次尝试发送消息至麦麦机器人后失败，请检查连接。\n{error_msg}")
            return False

    def convert_image_to_gif(self, image_base64: str) -> str:
        """将Base64图片转为GIF"""
        try:
            image_bytes = base64.b64decode(image_base64)
            image = Image.open(io.BytesIO(image_bytes))
            output_buffer = io.BytesIO()
            image.save(output_buffer, format="GIF")
            output_buffer.seek(0)
            return base64.b64encode(output_buffer.read()).decode("utf-8")
        except Exception as e:
            self.errorf(f"图片转换为GIF失败: {e}")
            return image_base64

    @via(lambda self: self.at_or_private() and self.au(1)
         and self.match(r"^(开启|启用|打开|记录|启动|关闭|禁用|取消)麦麦$"))
    def enable_maimbot(self):
        """启用麦麦"""
        msg = ""
        if self.match(r"(开启|启用|打开|记录|启动)"):
            self.config[self.owner_id]["enable"] = True
            msg = "麦麦机器人已开启"
            self.save_config()
        elif self.match(r"(关闭|禁用|取消)"):
            self.config[self.owner_id]["enable"] = False
            msg = "麦麦机器人已关闭"
            self.save_config()
        self.reply(msg)

    @via(lambda self: self.at_or_private() and self.au(1) and self.match(r"^重新连接麦麦$"))
    async def restart_maimbot(self):
        """重新连接麦麦"""
        try:
            if self.router.clients.get(self.config["platform"]):
                await self.router.clients[self.config["platform"]].stop()
            await self.router.stop()
        except Exception:
            self.errorf(traceback.format_exc())
            
        self.listening()
        self.reply("已尝试重置连接")

    @via(lambda self: self.ID in self.robot.persist_mods
         and self.config[self.owner_id]["enable"]
         and self.event.user_id not in self.config[self.owner_id].get("blacklist")
         and (self.event.msg or self.event.sub_type == "poke"))
    def send_maimbot(self):
        """发送至麦麦"""
        async def send_msg_task():
            try:
                if payload := await self.construct_message():
                    await self.send_to_maim(payload)
            except Exception:
                self.errorf(traceback.format_exc())
        self.loop.call_soon_threadsafe(lambda: asyncio.create_task(send_msg_task()))

    def notify_maimbot(self, content: str, group_id: str):
        """主动通知麦麦 (Api调用)"""
        if not self.ID in self.robot.persist_mods:
            return
        if not self.config[f"g{group_id}"]["enable"]:
            return
            
        async def send_msg_task():
            try:
                fake_event = Event(self.robot)
                fake_event.msg = content
                fake_event.user_id = self.robot.self_id
                fake_event.user_name = self.robot.self_name
                fake_event.group_id = group_id
                fake_event.raw = {"message": content}
                
                if payload := await self.construct_message(fake_event):
                    await self.send_to_maim(payload)
            except Exception:
                self.errorf(traceback.format_exc())
        self.loop.call_soon_threadsafe(lambda: asyncio.create_task(send_msg_task()))

qq_face: dict = {
    "0": "[表情：惊讶]",
    "1": "[表情：撇嘴]",
    "2": "[表情：色]",
    "3": "[表情：发呆]",
    "4": "[表情：得意]",
    "5": "[表情：流泪]",
    "6": "[表情：害羞]",
    "7": "[表情：闭嘴]",
    "8": "[表情：睡]",
    "9": "[表情：大哭]",
    "10": "[表情：尴尬]",
    "11": "[表情：发怒]",
    "12": "[表情：调皮]",
    "13": "[表情：呲牙]",
    "14": "[表情：微笑]",
    "15": "[表情：难过]",
    "16": "[表情：酷]",
    "17": "[表情：菜刀]",
    "18": "[表情：抓狂]",
    "19": "[表情：吐]",
    "20": "[表情：偷笑]",
    "21": "[表情：可爱]",
    "22": "[表情：白眼]",
    "23": "[表情：傲慢]",
    "24": "[表情：饥饿]",
    "25": "[表情：困]",
    "26": "[表情：惊恐]",
    "27": "[表情：流汗]",
    "28": "[表情：憨笑]",
    "29": "[表情：悠闲]",
    "30": "[表情：奋斗]",
    "31": "[表情：咒骂]",
    "32": "[表情：疑问]",
    "33": "[表情： 嘘]",
    "34": "[表情：晕]",
    "35": "[表情：折磨]",
    "36": "[表情：衰]",
    "37": "[表情：骷髅]",
    "38": "[表情：敲打]",
    "39": "[表情：再见]",
    "40": "[表情：撇嘴]",
    "41": "[表情：发抖]",
    "42": "[表情：爱情]",
    "43": "[表情：跳跳]",
    "46": "[表情：猪头]",
    "49": "[表情：拥抱]",
    "53": "[表情：蛋糕]",
    "56": "[表情：刀]",
    "59": "[表情：便便]",
    "60": "[表情：咖啡]",
    "63": "[表情：玫瑰]",
    "64": "[表情：凋谢]",
    "66": "[表情：爱心]",
    "67": "[表情：心碎]",
    "74": "[表情：太阳]",
    "75": "[表情：月亮]",
    "76": "[表情：赞]",
    "77": "[表情：踩]",
    "78": "[表情：握手]",
    "79": "[表情：胜利]",
    "85": "[表情：飞吻]",
    "86": "[表情：怄火]",
    "89": "[表情：西瓜]",
    "96": "[表情：冷汗]",
    "97": "[表情：擦汗]",
    "98": "[表情：抠鼻]",
    "99": "[表情：鼓掌]",
    "100": "[表情：糗大了]",
    "101": "[表情：坏笑]",
    "102": "[表情：左哼哼]",
    "103": "[表情：右哼哼]",
    "104": "[表情：哈欠]",
    "105": "[表情：鄙视]",
    "106": "[表情：委屈]",
    "107": "[表情：快哭了]",
    "108": "[表情：阴险]",
    "109": "[表情：左亲亲]",
    "110": "[表情：吓]",
    "111": "[表情：可怜]",
    "112": "[表情：菜刀]",
    "114": "[表情：篮球]",
    "116": "[表情：示爱]",
    "118": "[表情：抱拳]",
    "119": "[表情：勾引]",
    "120": "[表情：拳头]",
    "121": "[表情：差劲]",
    "123": "[表情：NO]",
    "124": "[表情：OK]",
    "125": "[表情：转圈]",
    "129": "[表情：挥手]",
    "137": "[表情：鞭炮]",
    "144": "[表情：喝彩]",
    "146": "[表情：爆筋]",
    "147": "[表情：棒棒糖]",
    "169": "[表情：手枪]",
    "171": "[表情：茶]",
    "172": "[表情：眨眼睛]",
    "173": "[表情：泪奔]",
    "174": "[表情：无奈]",
    "175": "[表情：卖萌]",
    "176": "[表情：小纠结]",
    "177": "[表情：喷血]",
    "178": "[表情：斜眼笑]",
    "179": "[表情：doge]",
    "181": "[表情：戳一戳]",
    "182": "[表情：笑哭]",
    "183": "[表情：我最美]",
    "185": "[表情：羊驼]",
    "187": "[表情：幽灵]",
    "201": "[表情：点赞]",
    "212": "[表情：托腮]",
    "262": "[表情：脑阔疼]",
    "263": "[表情：沧桑]",
    "264": "[表情：捂脸]",
    "265": "[表情：辣眼睛]",
    "266": "[表情：哦哟]",
    "267": "[表情：头秃]",
    "268": "[表情：问号脸]",
    "269": "[表情：暗中观察]",
    "270": "[表情：emm]",
    "271": "[表情：吃 瓜]",
    "272": "[表情：呵呵哒]",
    "273": "[表情：我酸了]",
    "277": "[表情：汪汪]",
    "281": "[表情：无眼笑]",
    "282": "[表情：敬礼]",
    "283": "[表情：狂笑]",
    "284": "[表情：面无表情]",
    "285": "[表情：摸鱼]",
    "286": "[表情：魔鬼笑]",
    "287": "[表情：哦]",
    "289": "[表情：睁眼]",
    "293": "[表情：摸锦鲤]",
    "294": "[表情：期待]",
    "295": "[表情：拿到红包]",
    "297": "[表情：拜谢]",
    "298": "[表情：元宝]",
    "299": "[表情：牛啊]",
    "300": "[表情：胖三斤]",
    "302": "[表情：左拜年]",
    "303": "[表情：右拜年]",
    "305": "[表情：右亲亲]",
    "306": "[表情：牛气冲天]",
    "307": "[表情：喵喵]",
    "311": "[表情：打call]",
    "312": "[表情：变形]",
    "314": "[表情：仔细分析]",
    "317": "[表情：菜汪]",
    "318": "[表情：崇拜]",
    "319": "[表情： 比心]",
    "320": "[表情：庆祝]",
    "323": "[表情：嫌弃]",
    "324": "[表情：吃糖]",
    "325": "[表情：惊吓]",
    "326": "[表情：生气]",
    "332": "[表情：举牌牌]",
    "333": "[表情：烟花]",
    "334": "[表情：虎虎生威]",
    "336": "[表情：豹富]",
    "337": "[表情：花朵脸]",
    "338": "[表情：我想开了]",
    "339": "[表情：舔屏]",
    "341": "[表情：打招呼]",
    "342": "[表情：酸Q]",
    "343": "[表情：我方了]",
    "344": "[表情：大怨种]",
    "345": "[表情：红包多多]",
    "346": "[表情：你真棒棒]",
    "347": "[表情：大展宏兔]",
    "349": "[表情：坚强]",
    "350": "[表情：贴贴]",
    "351": "[表情：敲敲]",
    "352": "[表情：咦]",
    "353": "[表情：拜托]",
    "354": "[表情：尊嘟假嘟]",
    "355": "[表情：耶]",
    "356": "[表情：666]",
    "357": "[表情：裂开]",
    "360": "[表情：亲亲]",
    "361": "[表情：狗狗笑哭]",
    "362": "[表情：好兄弟]",
    "363": "[表情：狗狗可怜]",
    "364": "[表情：超级赞]",
    "365": "[表情：狗狗生气]",
    "366": "[表情：芒狗]",
    "367": "[表情：狗狗疑问]",
    "392": "[表情：龙年 快乐]",
    "393": "[表情：新年中龙]",
    "394": "[表情：新年大龙]",
    "395": "[表情：略略略]",
    "396": "[表情：狼狗]",
    "397": "[表情：抛媚眼]",
    "😊": "[表情：嘿嘿]",
    "😌": "[表情：羞涩]",
    "😚": "[ 表情：亲亲]",
    "😓": "[表情：汗]",
    "😰": "[表情：紧张]",
    "😝": "[表情：吐舌]",
    "😁": "[表情：呲牙]",
    "😜": "[表情：淘气]",
    "☺": "[表情：可爱]",
    "😍": "[表情：花痴]",
    "😔": "[表情：失落]",
    "😄": "[表情：高兴]",
    "😏": "[表情：哼哼]",
    "😒": "[表情：不屑]",
    "😳": "[表情：瞪眼]",
    "😘": "[表情：飞吻]",
    "😭": "[表情：大哭]",
    "😱": "[表情：害怕]",
    "😂": "[表情：激动]",
    "💪": "[表情：肌肉]",
    "👊": "[表情：拳头]",
    "👍": "[表情 ：厉害]",
    "👏": "[表情：鼓掌]",
    "👎": "[表情：鄙视]",
    "🙏": "[表情：合十]",
    "👌": "[表情：好的]",
    "👆": "[表情：向上]",
    "👀": "[表情：眼睛]",
    "🍜": "[表情：拉面]",
    "🍧": "[表情：刨冰]",
    "🍞": "[表情：面包]",
    "🍺": "[表情：啤酒]",
    "🍻": "[表情：干杯]",
    "☕": "[表情：咖啡]",
    "🍎": "[表情：苹果]",
    "🍓": "[表情：草莓]",
    "🍉": "[表情：西瓜]",
    "🚬": "[表情：吸烟]",
    "🌹": "[表情：玫瑰]",
    "🎉": "[表情：庆祝]",
    "💝": "[表情：礼物]",
    "💣": "[表情：炸弹]",
    "✨": "[表情：闪光]",
    "💨": "[表情：吹气]",
    "💦": "[表情：水]",
    "🔥": "[表情：火]",
    "💤": "[表情：睡觉]",
    "💩": "[表情：便便]",
    "💉": "[表情：打针]",
    "📫": "[表情：邮箱]",
    "🐎": "[表情：骑马]",
    "👧": "[表情：女孩]",
    "👦": "[表情：男孩]",
    "🐵": "[表情：猴]",
    "🐷": "[表情：猪]",
    "🐮": "[表情：牛]",
    "🐔": "[表情：公鸡]",
    "🐸": "[表情：青蛙]",
    "👻": "[表情：幽灵]",
    "🐛": "[表情：虫]",
    "🐶": "[表情：狗]",
    "🐳": "[表情：鲸鱼]",
    "👢": "[表情：靴子]",
    "☀": "[表情：晴天]",
    "❔": "[表情：问号]",
    "🔫": "[表情：手枪]",
    "💓": "[表情：爱 心]",
    "🏪": "[表情：便利店]",
}
