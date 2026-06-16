"""函数库"""

from __future__ import annotations

import ast
import asyncio
import base64
from functools import wraps
import importlib
import io
import logging
import os
import re
import html
import json
import random
import tempfile
import threading
import traceback

from typing import TYPE_CHECKING, Callable, Coroutine

import httpx
from PIL import Image
from colorama import Back, Fore, Style

from src import api

if TYPE_CHECKING:
    from src.robot import Concerto
    from src.base import Event, Module


def import_json(file: str):
    """导入json"""
    try:
        content = "{}"
        if not os.path.exists(file):
            save_json(file, {})
        if temp := open(file, "r", encoding="utf-8").read():
            content = temp
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise e


def save_json(file_name: str, data):
    """原子化导出json"""
    directory = os.path.dirname(file_name) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, delete=False
        ) as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
            temp_path = f.name
        os.replace(temp_path, file_name)
        temp_path = ""
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def merge(d1: dict, d2: dict) -> dict:
    """简单字典合并"""
    result = d1.copy()
    for key, value in d2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def apply_formatter(logger: logging.Logger, mid: str):
    """给传入的 logger 应用彩色格式化器"""

    class ColorFormatter(logging.Formatter):
        """日志格式化器，添加颜色"""

        COLORS = {
            logging.DEBUG: Fore.BLUE,
            logging.WARNING: Fore.YELLOW,
            logging.ERROR: Fore.RED,
            logging.CRITICAL: Fore.MAGENTA + Style.BRIGHT,
        }

        def format(self, record):
            """为日志记录添加颜色并交给父类格式化"""
            color = self.COLORS.get(record.levelno, "")
            reset = Style.RESET_ALL
            record.asctime = f"{color}{record.levelname}{reset}"
            record.levelname = f"{color}{record.levelname}{reset}"
            record.msg = f"{color}{record.msg}{reset}"
            return super().format(record)

    fmt = f"\r[%(asctime)s %(levelname)s] {Fore.CYAN}[{mid}]{Fore.RESET} %(message)s"
    fmt += f"\n\r{Fore.GREEN}<console> {Fore.RESET}"
    formatter = ColorFormatter(fmt=fmt, datefmt="%H:%M:%S")
    logger.propagate = False
    if len(logger.handlers) == 0:
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        ch.terminator = ""
        logger.addHandler(ch)
    else:
        logger.handlers[0].terminator = ""
        logger.handlers[0].setFormatter(formatter)
    return logger


def calc_time(sec: int) -> str:
    """格式化时间"""
    units = [("天", 86400), ("小时", 3600), ("分", 60), ("秒", 1)]
    parts = []
    for name, div in units:
        if sec >= div:
            parts.append(f"{int(sec//div)}{name}")
            sec %= div
    return "".join(parts) or "0秒"


def calc_size(byte: int) -> str:
    """格式化文件大小"""
    if byte == 0:
        return "0 Bytes"
    symbols = ("KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    prefix = dict()
    for a, s in enumerate(symbols):
        prefix[s] = 1 << (a + 1) * 10
    for s in reversed(symbols):
        if int(byte) >= prefix[s]:
            value = float(byte) / prefix[s]
            return f"{value:.2f}{s}"
    return f".{byte}B"


def format_to_log(text: str) -> str:
    """
    格式化为日志友好型文本
    :param text: 输入文本
    """
    text = re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"((\s*█)+|(\s*▀)+)", "[图片]", text)
    return text.strip()

def target_image_size(img: Image.Image, mode: str, min_width: int, max_width: int):
    """计算渲染前的终端适配图片尺寸"""
    width, height = img.size
    target_w = sorted([min_width, width, max_width])[1]
    if mode == "braille":
        target_rows = max(1, int(target_w * height / width * 0.5))
        return target_w * 2, target_rows * 4
    half_block_modes = {"colorama", "ansi_256", "true_color"}
    scale = 1 if mode in half_block_modes else 0.5
    target_h = max(1, int(target_w * height / width * scale))
    if mode in half_block_modes and target_h % 2:
        target_h += 1
    return target_w, target_h


def rgb_to_ansi_256(rgb: list):
    """将 RGB 颜色转换为 ANSI 256 色的颜色代码"""
    r, g, b = [x / 255.0 for x in rgb]
    r_ = int(r * 5)
    g_ = int(g * 5)
    b_ = int(b * 5)
    return 16 + 36 * r_ + 6 * g_ + b_


def rgb_to_colorama(rgb: list, background: bool = False) -> str:
    """将 RGB 颜色转换为 colorama 的前景色或背景色代码"""
    colors = (
        ((0, 0, 0), Fore.BLACK, Back.BLACK),
        ((128, 0, 0), Fore.RED, Back.RED),
        ((0, 128, 0), Fore.GREEN, Back.GREEN),
        ((128, 128, 0), Fore.YELLOW, Back.YELLOW),
        ((0, 0, 128), Fore.BLUE, Back.BLUE),
        ((128, 0, 128), Fore.MAGENTA, Back.MAGENTA),
        ((0, 128, 128), Fore.CYAN, Back.CYAN),
        ((192, 192, 192), Fore.WHITE, Back.WHITE),
    )
    r, g, b = rgb
    _, foreground, back = min(
        colors,
        key=lambda item: (
            (r - item[0][0]) ** 2
            + (g - item[0][1]) ** 2
            + (b - item[0][2]) ** 2
        ),
    )
    return back if background else foreground


def build_half_block(top_rgb: list, bottom_rgb: list, mode: str) -> str:
    """构建一个半块字符"""
    upper_half_block = chr(0x2580)
    if mode == "colorama":
        return (
            f"{rgb_to_colorama(top_rgb)}"
            f"{rgb_to_colorama(bottom_rgb, background=True)}{upper_half_block}"
        )
    if mode == "ansi_256":
        top_code = rgb_to_ansi_256(top_rgb)
        bottom_code = rgb_to_ansi_256(bottom_rgb)
        return (
            f"\033[38;5;{top_code}m"
            f"\033[48;5;{bottom_code}m{upper_half_block}"
        )
    if mode == "true_color":
        tr, tg, tb = top_rgb
        br, bg, bb = bottom_rgb
        return (
            f"\033[38;2;{tr};{tg};{tb}m"
            f"\033[48;2;{br};{bg};{bb}m{upper_half_block}"
        )
    raise ValueError(f"Unsupported half block mode: {mode!r}")


def img_to_half_blocks(img: Image.Image, mode: str):
    """使用半块字符输出图片"""
    buf = []
    target_w, target_h = img.size
    pixels = img.load()
    for y in range(0, target_h - 1, 2):
        for x in range(target_w):
            buf.append(build_half_block(pixels[x, y], pixels[x, y + 1], mode))
        buf.append("\033[0m\n")
    return buf


def img_to_braille(img: Image.Image):
    """使用盲文点输出字符"""
    buf = []
    target_w, target_h = img.size
    gray = img.convert("L")
    gp = gray.load()
    for y in range(0, target_h, 4):
        for x in range(0, target_w, 2):
            value = 0

            if gp[x, min(y, target_h - 1)] < 128:
                value |= 0x01
            if gp[x, min(y + 1, target_h - 1)] < 128:
                value |= 0x02
            if gp[x, min(y + 2, target_h - 1)] < 128:
                value |= 0x04

            if gp[min(x + 1, target_w - 1), min(y, target_h - 1)] < 128:
                value |= 0x08
            if gp[min(x + 1, target_w - 1), min(y + 1, target_h - 1)] < 128:
                value |= 0x10
            if gp[min(x + 1, target_w - 1), min(y + 2, target_h - 1)] < 128:
                value |= 0x20

            if gp[x, min(y + 3, target_h - 1)] < 128:
                value |= 0x40
            if gp[min(x + 1, target_w - 1), min(y + 3, target_h - 1)] < 128:
                value |= 0x80

            buf.append(chr(0x2800 + value))
        buf.append("\n")
    return buf


def img_to_gray(img: Image.Image):
    """使用灰度字符输出图片"""
    ascii_chars = r" .,:;+*?#%@"
    buf = []
    target_w, target_h = img.size
    gray = img.convert("L")
    values = [
        [float(gray.getpixel((x, y))) for x in range(target_w)]
        for y in range(target_h)
    ]
    levels = len(ascii_chars) - 1
    for y in range(target_h):
        for x in range(target_w):
            old = max(0, min(255, int(values[y][x])))
            idx = round(old * levels / 255)
            new = idx * 255 / levels
            err = old - new
            values[y][x] = new
            buf.append(ascii_chars[idx])
            if x + 1 < target_w:
                values[y][x + 1] += err * 7 / 16
            if y + 1 < target_h:
                if x:
                    values[y + 1][x - 1] += err * 3 / 16
                values[y + 1][x] += err * 5 / 16
                if x + 1 < target_w:
                    values[y + 1][x + 1] += err * 1 / 16
        buf.append("\n")
    return buf


def render_image_chars(img: Image.Image, mode: str, min_width: int, max_width: int) -> str:
    """将PIL图片渲染为终端字符"""
    target_w, target_h = target_image_size(img, mode, min_width, max_width)
    img = img.resize((target_w, target_h), Image.Resampling.LANCZOS).convert("RGB")
    renderers = {
        "braille": img_to_braille,
        "gray": img_to_gray,
        "colorama": lambda image: img_to_half_blocks(image, "colorama"),
        "ansi_256": lambda image: img_to_half_blocks(image, "ansi_256"),
        "true_color": lambda image: img_to_half_blocks(image, "true_color"),
    }
    if mode in renderers:
        return "".join(renderers[mode](img)).rstrip()
    raise ValueError(f"Unsupported image render mode: {mode!r}")


def msg_img2char(robot: Concerto, msg: str, print_img: bool = False):
    """
    检测CQ码中有图片并转化为字符画
    :param robot: 机器人类
    :param msg: 收到的消息
    :param print_img: 是否直接print输出
    :return: 转化为字符画的消息
    """
    mode = robot.config.image_color
    render_modes = {"braille", "gray", "colorama", "ansi_256", "true_color"}
    if mode == "disabled":
        return msg
    if mode not in render_modes:
        robot.warnf(f"不支持的图片显示模式: {mode}", level="DEBUG")
        return msg

    matches = re.findall(r"(\[CQ:image.*?url=([^,]*).*\])", msg)
    for cq, url in matches:
        try:
            response = httpx.get(url, timeout=10)
            response.raise_for_status()
            img = Image.open(io.BytesIO(response.content)).convert("RGB")
            min_image_width = robot.config.min_image_width
            max_image_width = robot.config.max_image_width
            char = render_image_chars(img, mode, min_image_width, max_image_width)
            if print_img:
                robot.printf("\r" + char, flush=True)
            msg = msg.replace(cq, char)

        except Exception:  # pylint: disable=broad-exception-caught
            robot.errorf(f"图片转字符画失败!\n{traceback.format_exc()}", level="DEBUG")
    return msg


def submit_msg_img2char(robot: Concerto, msg: str) -> bool:
    """提交CQ图片到字符画后台转换任务"""
    if robot.config.image_color == "disabled":
        return False
    if "[RECEIVE]" not in msg:
        return False
    if not re.search(r"\[CQ:image.*?url=([^,]*).*?\]", msg):
        return False
    threading.Thread(target=msg_img2char, args=(robot, msg, True),daemon=True).start()
    return True


def resize_image(image: bytes, size=(640, 360), file_format: str = "JPEG") -> bytes:
    """
    缩放图片
    :param image: 原始图片
    :param size: 目标大小，默认(640, 360)
    :return: 输出图片
    """
    with Image.open(io.BytesIO(image)).convert("RGB") as img:
        img.thumbnail((size[0], size[1]))
        new_img = Image.new("RGB", size, (0, 0, 0))
        offset_x = (size[0] - img.width) // 2
        offset_y = (size[1] - img.height) // 2
        new_img.paste(img, (offset_x, offset_y))
    buf = io.BytesIO()
    new_img.save(buf, format=file_format)
    return buf.getvalue()


async def async_get_content_base64(
    robot: Concerto, url: str, timeout: str = 3, max_retries: str = 3
) -> str:
    """获取url所指内容的Base64"""
    for attempt in range(max_retries):
        try:
            response = await httpx.AsyncClient().get(url, timeout=timeout)
            if response.status_code != 200:
                raise httpx.HTTPError(response.text)
            return base64.b64encode(response.content).decode("utf-8")
        except httpx.TimeoutException:
            robot.printf(f"请求内容超时重试 {attempt + 1}/{max_retries}")
            if attempt + 1 == max_retries:
                raise


def get_content_base64(
    robot: Concerto, url: str, timeout: str = 3, max_retries: str = 3
) -> str:
    """获取url所指内容的Base64"""
    if not url:
        return ""
    for attempt in range(max_retries):
        try:
            response = httpx.get(url, timeout=timeout)
            if response.status_code != 200:
                raise httpx.HTTPError(response.text)
            return base64.b64encode(response.content).decode("utf-8")
        except httpx.TimeoutException:
            robot.printf(f"请求内容超时重试 {attempt + 1}/{max_retries}")
            if attempt + 1 == max_retries:
                raise


def get_image_format(data: str) -> str:
    """
    从Base64编码的数据中确定图片的格式
    Parameters:
        raw_data: str: Base64编码的图片数据
    Returns:
        format: str: 图片的格式（例如 "jpeg", "png", "gif"）
    """
    image_bytes = base64.b64decode(data)
    return Image.open(io.BytesIO(image_bytes)).format.lower()


def status_ok(response: dict):
    """
    检测API接口是否返回正常
    :param respond: API返回的json信息
    :return: 此接口是否正常执行
    """
    if response and response.get("status") == "ok":
        return True
    else:
        return False


def get_version_info(robot: Concerto):
    """获取API版本和相关信息"""
    return api.get_version_info(robot)


def get_login_info(robot: Concerto):
    """获取登录号信息"""
    return api.get_login_info(robot)


def bot_exit(robot: Concerto):
    """退出机器人"""
    return api.bot_exit(robot)


def handle_placeholder(text: str, placeholder_dict: dict):
    """替换标记的字符串"""
    pattern = re.compile(r"(%\S+?%)")
    flags = pattern.findall(str(text))
    for flag in flags:
        if flag.replace("%", "") in placeholder_dict:
            result_list = placeholder_dict[flag.replace("%", "")]
            text = re.sub(flag, random.choice(result_list), str(text))
            text = handle_placeholder(text, placeholder_dict)
    return text


def build_msg(text: str):
    """生成一个消息节点"""
    data = {"type": "text", "data": {"text": text}}
    return data


def build_node(*args, **kwargs) -> dict:
    """
    生成一个转发节点
    user_id,nickname,content
    """
    content = args[0] if len(args) == 1 else list(args)
    user_id = kwargs.get("user_id")
    nickname = kwargs.get("nickname")
    if user_id is None and nickname is None:
        nickname = " "
    data = {
        "type": "node",
        "data": {"user_id": user_id, "nickname": nickname, "content": content},
    }
    return data


def build_forward(text: str, user_id: str):
    """生成一个聊天记录节点"""
    data = {"type": "forward", "data": {"id": user_id, "content": text}}
    return data


def reply_event(robot: Concerto, event: Event, msg: str, reply=False, force=False):
    """
    快捷回复消息
    :param robot: 机器人类
    :param event: 接收到的消息事件
    :param msg: 发送的消息内容
    :param force: 无视静默模式发送消息
    :return: 发送消息后返回的json信息
    """
    msg = handle_placeholder(str(msg), robot.placeholder_dict)
    if reply:
        msg = f"[CQ:reply,id={event.msg_id}]{msg}"
    simple_msg = re.sub(r"\[CQ:(.*?),(file|url)=base64.*\]", r"[CQ:\1,\2=Base64]", msg)
    if event.post_type == "message" and (not robot.config.is_silence or force):
        if event.msg_type == "group":
            group_id = event.group_id
            group_name = get_group_name(robot, group_id)
            robot.printf(
                f"{Fore.GREEN}[SEND] {Fore.RESET}向群{Fore.MAGENTA}{group_name}({group_id}){Fore.RESET}发送消息：{simple_msg}"
            )
            resp_dict = {"msg_type": "group", "number": group_id, "msg": msg}
            return api.send_msg(robot, resp_dict)
        else:
            user_id = event.user_id
            user_name = get_user_name(robot, user_id)
            robot.printf(
                f"{Fore.GREEN}[SEND] {Fore.RESET}向{Fore.MAGENTA}{user_name}({user_id}){Fore.RESET}发送消息：{simple_msg}"
            )
            resp_dict = {"msg_type": "private", "number": user_id, "msg": msg}
            return api.send_msg(robot, resp_dict)


def reply_id(robot: Concerto, msg_type: str, uid: str, msg: str, force=False):
    """
    按id回复消息
    :param robot: 机器人类
    :param msg_type: 发送类型 group,private
    :param uid: 发送的对象id
    :param msg: 发送的消息内容
    :return: 发送消息后返回的json信息
    """
    msg = handle_placeholder(str(msg), robot.placeholder_dict)
    simple_msg = re.sub(
        r"\[CQ:(.*?),(file|url)=base64.*\]", r"[CQ:\1,file=Base64]", msg
    )
    if not robot.config.is_silence or force:
        if msg_type == "group":
            robot.printf(
                f"{Fore.GREEN}[SEND] {Fore.RESET}向群{Fore.MAGENTA}{get_group_name(robot, uid)}({uid}){Fore.RESET}发送消息：{simple_msg}"
            )
            resp_dict = {"msg_type": "group", "number": uid, "msg": msg}
            return api.send_msg(robot, resp_dict)
        else:
            robot.printf(
                f"{Fore.GREEN}[SEND] {Fore.RESET}向{Fore.MAGENTA}{get_user_name(robot, uid)}({uid}){Fore.RESET}发送消息：{simple_msg}"
            )
            resp_dict = {"msg_type": "private", "number": uid, "msg": msg}
            return api.send_msg(robot, resp_dict)


def reply_back(robot: Concerto, owner_id: str, msg: str):
    """
    对reply_id方法的封装，对owner_id发送消息
    :param robot: 机器人类
    :param owner_id: 用户识别ID
    :param msg: 发送的消息内容
    """
    if owner_id[:1] == "u":
        reply_id(robot, "private", owner_id[1:], msg)
    else:
        reply_id(robot, "group", owner_id[1:], msg)


def quick_reply(robot: Concerto, raw: dict, msg: str):
    """
    调用“.handle_quick_operation”接口的快捷回复消息
    :param robot: 机器人类
    :param raw: 接收到的消息json信息
    :param msg: 发送的消息内容
    :return: 发送消息后返回的json信息
    """
    msg = handle_placeholder(str(msg), robot.placeholder_dict)
    if raw["post_type"] == "message":
        resp_dict = {"context": raw, "operation": {"reply": msg}}
        return api.handle_quick_operation(robot, resp_dict)


def send_msg(
    robot: Concerto, msg_type: str, number: str, msg: str, group_id: str = None
):
    """
    发送消息
    :param robot: 机器人类
    :param msg_type: 消息类型
    :param number: 对方ID
    :param msg: 消息内容
    :return: 消息内容
    """
    msg = handle_placeholder(str(msg), robot.placeholder_dict)
    resp_dict = {
        "msg_type": msg_type,
        "number": number,
        "msg": msg,
        "group_id": group_id,
    }
    result = api.send_msg(robot, resp_dict)
    return result


def get_msg(robot: Concerto, msg_id: str):
    """
    获取消息内容
    :param robot: 机器人类
    :param msg_id: 消息ID
    :return: 消息内容
    """
    resp_dict = {"message_id": msg_id}
    return api.get_msg(robot, resp_dict)


def del_msg(robot: Concerto, msg_id: str):
    """
    撤回消息
    :param robot: 机器人类
    :param msg_id: 消息ID
    """
    resp_dict = {"message_id": msg_id}
    return api.del_msg(robot, resp_dict)


def get_forward_msg(robot: Concerto, msg_id: str):
    """
    获取转发消息内容
    :param robot: 机器人类
    :param msg_id: 转发消息ID
    :return: 转发消息内容
    """
    if msg_id == 0:
        return None
    resp_dict = {"message_id": msg_id}
    return api.get_forward_msg(robot, resp_dict)


def send_forward_msg(
    robot: Concerto, nodes: list, group_id=None, user_id=None, source=None, summary=None
):
    """
    发送转发消息
    :param robot: 机器人类
    :param node: 转发消息内容物
    :param group_id: 发送到群ID
    :param user_id: 发送到用户ID
    :param source: 来源字段
    :return: 发送消息后返回的json信息
    """
    if not summary:
        summary = "ConcertBot"
    resp_dict = {"messages": nodes, "source": source, "summary": summary}
    simple_msg = re.sub(
        r"\[CQ:(.*?),(file|url)=base64.*\]",
        r"[CQ:\1,file=Base64]",
        json.dumps(nodes, ensure_ascii=False),
    )
    if group_id:
        robot.printf(
            f"{Fore.GREEN}[SEND] {Fore.RESET}向群{Fore.MAGENTA}{get_group_name(robot, group_id)}({group_id}){Fore.RESET}发送消息：{simple_msg}"
        )
        resp_dict["group_id"] = group_id
    elif user_id:
        robot.printf(
            f"{Fore.GREEN}[SEND] {Fore.RESET}向{Fore.MAGENTA}{get_user_name(robot, user_id)}({user_id}){Fore.RESET}发送消息：{simple_msg}"
        )
        resp_dict["user_id"] = user_id
    else:
        return
    return api.send_forward_msg(robot, resp_dict)


def send_private_forward_msg(robot: Concerto, node: dict, user_id: str):
    """
    发送私聊转发消息
    :param robot: 机器人类
    :param node: 转发消息内容物
    :param user_id: 发送到的用户ID
    :return: 发送消息后返回的json信息
    """
    resp_dict = {"user_id": user_id, "messages": node}
    return api.send_private_forward_msg(robot, resp_dict)


def send_group_forward_msg(robot: Concerto, node: dict, group_id: str):
    """
    发送群聊转发消息
    :param robot: 机器人类
    :param group_id: 发送到群ID
    :param node: 转发消息内容物
    :return: 发送消息后返回的json信息
    """
    resp_dict = {"group_id": group_id, "messages": node}
    return api.send_group_forward_msg(robot, resp_dict)


def get_group_msg_history(robot: Concerto, group_id: str):
    """
    获取群消息历史
    :param robot: 机器人类
    :param group_id: 群ID
    :return: 消息json信息
    """
    resp_dict = {"group_id": group_id}
    return api.get_group_msg_history(robot, resp_dict)


def reply_add(robot: Concerto, raw: dict, accept: str, msg: str):
    """
    回复添加请求
    :param robot: 机器人类
    :param raw: 接收到的请求json信息
    :param accept: 是否接受
    :param msg: 操作理由
    :return: 发送消息后返回的json信息
    """
    if raw["post_type"] == "request":
        return api.handle_quick_operation(
            robot,
            {
                "context": raw,
                "operation": {"approve": accept, "remark": msg, "reason": msg},
            },
        )


def get_user_name(robot: Concerto, uid: str):
    """
    获取用户信息
    :param robot: 机器人类
    :param uid: 用户ID
    :return: 用户信息
    """
    if not uid:
        return
    uid = str(uid)
    if uid in robot.user_dict:
        return robot.user_dict[uid]
    else:
        resp_dict = {"user_id": uid}
        result = api.get_stranger_info(robot, resp_dict)
        if status_ok(result):
            name = result["data"]["nickname"]
            robot.user_dict[uid] = name
            return name
        return ""


def get_user_id(robot: Concerto, user_name: str, group_id: str = None) -> str:
    """使用用户名获取用户ID"""
    if group_id:
        member_list = get_group_member_list(robot, group_id).get("data", [])
        for member in member_list:
            if user_name == member["card"] or user_name == member["nickname"]:
                return member["user_id"]
    for uid, name in robot.user_dict.items():
        if name == user_name:
            return uid


def get_group_info(robot: Concerto, group_id: str):
    """
    获取群信息
    :param robot: 机器人类
    :param id: 群号
    :return: 群信息
    """
    resp_dict = {"group_id": group_id}
    return api.get_group_info(robot, resp_dict)


def set_group_ban(robot: Concerto, group_id: str, user_id: str, duration: int):
    """
    设置群禁言
    :param robot: 机器人类
    :param group_id: 群号
    :param user_id: 用户
    :param duration: 时长
    """
    resp_dict = {
        "group_id": int(group_id),
        "user_id": int(user_id),
        "duration": int(duration),
    }
    return api.set_group_ban(robot, resp_dict)


def set_group_whole_ban(robot: Concerto, group_id: str, enable: bool):
    """
    设置群禁言
    :param robot: 机器人类
    :param group_id: 群号
    :param user_id: 用户
    :param duration: 时长
    """
    resp_dict = {"group_id": int(group_id), "enable": enable}
    return api.set_group_whole_ban(robot, resp_dict)


def set_group_kick(robot: Concerto, group_id: str, user_id: str):
    """
    设置群禁言
    :param robot: 机器人类
    :param group_id: 群号
    :param user_id: 用户
    """
    resp_dict = {"group_id": int(group_id), "user_id": int(user_id)}
    return api.set_group_kick(robot, resp_dict)


def get_group_member_list(robot: Concerto, group_id: str):
    """
    获取群内用户列表
    :param robot: 机器人类
    :param id: 群号
    """
    if not group_id:
        return
    resp_dict = {"group_id": group_id, "no_cache": False}
    return api.get_group_member_list(robot, resp_dict)


def get_group_name(robot: Concerto, group_id: str) -> str:
    """
    获取群名称
    :param robot: 机器人类
    :param id: 群号
    """
    if not group_id:
        return
    group_id = str(group_id)
    if group_id in robot.group_dict:
        return robot.group_dict[group_id]
    else:
        result = get_group_info(robot, group_id)
        if status_ok(result):
            name = result["data"]["group_name"]
            robot.group_dict[group_id] = name
            return name
        else:
            return ""


def get_image(robot: Concerto, file: str) -> dict:
    """
    获取图片
    :param robot: 机器人类
    :param file: 文件的标识码
    """
    resp_dict = {"file": file}
    return api.get_image(robot, resp_dict)


def get_record(robot: Concerto, file_id: str, out_format: str = "mp3") -> dict:
    """
    获取图片
    :param robot: 机器人类
    :param file_id: 文件的标识码
    :param out_format: 文件的标识码
    :return: 文件下载链接
    """
    resp_dict = {"file_id": file_id, "out_format": out_format}
    return api.get_record(robot, resp_dict)


def poke(robot: Concerto, user_id: str, group_id: str | None = None):
    """
    戳一戳
    :param robot: 机器人类
    :param user_id: 用户ID
    :param group_id: 群ID
    """
    if group_id:
        return api.group_poke(robot, {"user_id": user_id, "group_id": group_id})
    else:
        return api.friend_poke(robot, {"user_id": user_id})


def set_model_show(robot: Concerto, device: str, model_show: str):
    """
    贴表情
    :param robot: 机器人类
    :param device: 设备名
    :param model_show: 展示名称
    """
    resp_dict = {"model": device, "model_show": model_show}
    return api.set_model_show(robot, resp_dict)


def set_emoji(robot: Concerto, message_id: str, emoji_id: str, is_set=True):
    """
    贴表情
    :param robot: 机器人类
    :param message_id: 消息ID
    :param set: 贴上/取下
    """
    resp_dict = {"message_id": message_id, "emoji_id": emoji_id, "set": is_set}
    return api.set_msg_emoji_like(robot, resp_dict)


def group_sign(robot: Concerto, group_id: str):
    """
    贴表情
    :param robot: 机器人类
    :param group_id: 群ID
    """
    resp_dict = {"group_id": group_id}
    return api.set_group_sign(robot, resp_dict)


def send_group_notice(robot: Concerto, group_id: str, notice: str):
    """
    发送群公告
    :param robot: 机器人类
    :param group_id: 群ID
    :param notice: 群公告内容
    """
    resp_dict = {"group_id": group_id, "content": notice}
    return api.send_group_notice(robot, resp_dict)


def send_like(robot: Concerto, user_id: str, times: int):
    """
    贴表情
    :param robot: 机器人类
    :param user_id: 用户ID
    :param times: 次数
    """
    resp_dict = {"user_id": user_id, "times": times}
    return api.send_like(robot, resp_dict)


def upload_file(
    robot: Concerto,
    file: str,
    name: str,
    user_id: str | None = None,
    group_id: str | None = None,
    folder_id: str | None = None,
):
    """
    上传文件
    :param file: 文件路径
    :param name: 文件名
    :param user_id: 用户ID
    :param group_id: 群ID
    """
    if group_id:
        return api.upload_group_file(
            robot,
            {"group_id": group_id, "file": file, "name": name, "folder_id": folder_id},
        )
    else:
        return api.upload_private_file(
            robot, {"user_id": user_id, "file": file, "name": name}
        )


def del_file(robot: Concerto, file_id: str, group_id: str):
    """
    删除文件
    :param file_id: 文件ID
    :param group_id: 群ID
    """
    return api.del_group_file(robot, {"file_id": file_id, "group_id": group_id})


def send_group_ai_record(robot: Concerto, group_id: str, character: str, text: str):
    """
    发送群AI语音
    :param robot: 机器人类
    :param group_id: 群ID
    :param character: AI音色
    :param text: 文本
    """
    resp_dict = {"group_id": group_id, "character": character, "text": text}
    return api.send_group_ai_record(robot, resp_dict)


def group_member_info(robot: Concerto, group_id: str, user_id: str):
    """
    获取群成员信息
    :param robot: 机器人类
    :param group_id: 群ID
    :param user_id: 用户ID
    """
    resp_dict = {"group_id": group_id, "user_id": user_id}
    return api.get_group_member_info(robot, resp_dict)


def group_special_title(
    robot: Concerto, group_id: str, user_id: str, special_title: str
):
    """
    设置群成员专属头衔
    :param robot: 机器人类
    :param group_id: 群ID
    """
    resp_dict = {
        "group_id": group_id,
        "user_id": user_id,
        "special_title": special_title,
    }
    return api.set_group_special_title(robot, resp_dict)


def get_stranger_info(robot: Concerto, user_id: int):
    """
    获取用户信息
    :param robot: 机器人类
    :param group_id: 群ID
    """
    resp_dict = {"user_id": user_id}
    return api.get_stranger_info(robot, resp_dict)


def ocr_image(robot: Concerto, img_id: str):
    """
    OCR图片识别
    :param robot: 机器人类
    :param img_id: 消息ID
    """
    resp_dict = {"image": img_id}
    return api.ocr_image(robot, resp_dict)


def get_img_url(robot: Concerto, url: str) -> str:
    """获取QQ链接"""
    try:
        robot.printf(f"获取QQ图片链接...url={url}")
        result = send_msg(robot, "private", robot.self_id, f"[CQ:image,file={url}]")
        if not status_ok(result):
            return url
        msg_id = result.get("data").get("message_id")
        result = get_msg(robot, msg_id)
        if not status_ok(result):
            return url
        msg = html.unescape(result.get("data").get("message"))
        if match := re.search(r"\[CQ:image,.*url=([^,\]]+?),.*\]", msg):
            url = match.group(1)
        return url
    except Exception:  # pylint: disable=broad-exception-caught
        robot.errorf(f"获取腾讯图床链接失败\n{traceback.format_exc()}")
        return url

def get_handler_amount(robot: Concerto):
    """
    获取事件的处理方法数量
    :param robot: 机器人类
    :return: 处理方法数量
    """
    count = 0
    for module in robot.modules.values():
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if callable(attr) and hasattr(attr, "_is_handler"):
                count += 1
    return count

def simplify_traceback():
    """
    获取错误报告并简化
    :param tb: 获取的错误报告
    :return: 易读的错误报告
    """
    result = "按从执行顺序排序有\n"
    tb = traceback.format_exc().strip().split("\n")
    exclude = True
    for excepts in tb[1:]:
        if exclude and "__init__" not in excepts:
            continue
        exclude = False
        if re.search(r"(\\|/)(\w*?\.py).*line\s([0-9]+).*in\s(.*)", excepts):
            temp = re.search(
                r"(\\|/)(\w*?\.py).*line\s([0-9]+).*in\s(.*)", excepts
            ).groups()
            result += f"文件{temp[1]}中第{temp[2]}行的“{temp[3]}”方法出错\n"
    result += f"导致最终错误为“{tb[-1]}”"
    return result


def get_error():
    """
    获取错误原因
    :return: 直接的错误原因
    """
    return traceback.format_exc().strip().rsplit("\n", maxsplit=1)[-1]


def scan_missing_modules(file_path: str):
    """
    扫描单个py文件返回缺失模块列表
    """
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=file_path)
    missing = set()
    optional = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for stmt in node.body:
                if isinstance(stmt, ast.Import):
                    for alias in stmt.names:
                        optional.add(alias.name.split(".")[0])
                elif isinstance(stmt, ast.ImportFrom) and stmt.module:
                    optional.add(stmt.module.split(".")[0])

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name.split(".")[0]
                if module_name not in optional:
                    try:
                        importlib.import_module(module_name)
                    except ModuleNotFoundError as e:
                        missing.add(e.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module_name = node.module.split(".")[0]
                if module_name not in optional:
                    try:
                        importlib.import_module(module_name)
                    except ModuleNotFoundError as e:
                        missing.add(e.name)
    return missing


def handler(condition):
    """竞争执行方法装饰器"""
    return _register_handler(condition, handled=True)


def listener(condition):
    """监听执行方法装饰器"""
    return _register_handler(condition, handled=False)


def _register_handler(condition, handled=True):
    """模块方法装饰器"""

    def decorator(func):
        """为模块方法生成带条件判断的包装器"""
        @wraps(func)
        def wrapper(self: Module, *args, **kwargs):
            """执行条件满足时调用模块方法并维护 handled 状态"""
            method_name = f"{self.ID}.{func.__name__}"
            if not condition(self):
                # self.robot.printf(f"未满足[{method_name}]的执行条件", level="DEBUG")
                return None
            self.printf(f"执行{Fore.YELLOW}[{method_name}]{Fore.RESET}方法", level="DEBUG")
            try:
                self.handled = handled
                result = func(self, *args, **kwargs)
                if asyncio.iscoroutine(result):
                    return run_coroutine_sync(result)
                if handled:
                    self.printf(f"{Fore.YELLOW}[{method_name}]{Fore.RESET}方法已处理该事件", level="DEBUG")
                return result
            except Exception:  # pylint: disable=broad-exception-caught
                self.errorf(
                    f"{Fore.RED}执行{Fore.YELLOW}[{method_name}]{Fore.RED}方法发生错误！"
                )
                self.errorf(Fore.RED + traceback.format_exc())
                self.handled = False

        wrapper._is_handler = True  # pylint: disable=protected-access
        return wrapper

    return decorator


def run_coroutine_sync(coroutine: Coroutine):
    """在同步上下文中安全执行协程"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    raise RuntimeError("当前事件循环正在运行，无法在同步装饰器中阻塞执行协程")


def export_func(func: Callable | None = None, *, name: str | None = None):
    """标记一个方法为可注册到 robot.func 的模块能力"""
    def decorator(target: Callable):
        target._is_exported_func = True   # pylint: disable=protected-access
        target._exported_func_name = name or target.__name__  # pylint: disable=protected-access
        return target

    if func is None:
        return decorator
    return decorator(func)


class Utils:
    """用于 IDE 自动补全的静态工具入口"""
    import_json = staticmethod(import_json)
    save_json = staticmethod(save_json)
    merge = staticmethod(merge)
    apply_formatter = staticmethod(apply_formatter)
    calc_time = staticmethod(calc_time)
    calc_size = staticmethod(calc_size)
    format_to_log = staticmethod(format_to_log)
    target_image_size = staticmethod(target_image_size)
    rgb_to_ansi_256 = staticmethod(rgb_to_ansi_256)
    rgb_to_colorama = staticmethod(rgb_to_colorama)
    build_half_block = staticmethod(build_half_block)
    img_to_half_blocks = staticmethod(img_to_half_blocks)
    img_to_braille = staticmethod(img_to_braille)
    img_to_gray = staticmethod(img_to_gray)
    render_image_chars = staticmethod(render_image_chars)
    msg_img2char = staticmethod(msg_img2char)
    submit_msg_img2char = staticmethod(submit_msg_img2char)
    resize_image = staticmethod(resize_image)
    async_get_content_base64 = staticmethod(async_get_content_base64)
    get_content_base64 = staticmethod(get_content_base64)
    get_image_format = staticmethod(get_image_format)
    status_ok = staticmethod(status_ok)
    get_version_info = staticmethod(get_version_info)
    get_login_info = staticmethod(get_login_info)
    bot_exit = staticmethod(bot_exit)
    handle_placeholder = staticmethod(handle_placeholder)
    build_msg = staticmethod(build_msg)
    build_node = staticmethod(build_node)
    build_forward = staticmethod(build_forward)
    reply_event = staticmethod(reply_event)
    reply_id = staticmethod(reply_id)
    reply_back = staticmethod(reply_back)
    quick_reply = staticmethod(quick_reply)
    send_msg = staticmethod(send_msg)
    get_msg = staticmethod(get_msg)
    del_msg = staticmethod(del_msg)
    get_forward_msg = staticmethod(get_forward_msg)
    send_forward_msg = staticmethod(send_forward_msg)
    send_private_forward_msg = staticmethod(send_private_forward_msg)
    send_group_forward_msg = staticmethod(send_group_forward_msg)
    get_group_msg_history = staticmethod(get_group_msg_history)
    reply_add = staticmethod(reply_add)
    get_user_name = staticmethod(get_user_name)
    get_user_id = staticmethod(get_user_id)
    get_group_info = staticmethod(get_group_info)
    set_group_ban = staticmethod(set_group_ban)
    set_group_whole_ban = staticmethod(set_group_whole_ban)
    set_group_kick = staticmethod(set_group_kick)
    get_group_member_list = staticmethod(get_group_member_list)
    get_group_name = staticmethod(get_group_name)
    get_image = staticmethod(get_image)
    get_record = staticmethod(get_record)
    poke = staticmethod(poke)
    set_model_show = staticmethod(set_model_show)
    set_emoji = staticmethod(set_emoji)
    group_sign = staticmethod(group_sign)
    send_group_notice = staticmethod(send_group_notice)
    send_like = staticmethod(send_like)
    upload_file = staticmethod(upload_file)
    del_file = staticmethod(del_file)
    send_group_ai_record = staticmethod(send_group_ai_record)
    group_member_info = staticmethod(group_member_info)
    group_special_title = staticmethod(group_special_title)
    get_stranger_info = staticmethod(get_stranger_info)
    ocr_image = staticmethod(ocr_image)
    get_img_url = staticmethod(get_img_url)
    get_handler_amount = staticmethod(get_handler_amount)
    simplify_traceback = staticmethod(simplify_traceback)
    get_error = staticmethod(get_error)
    scan_missing_modules = staticmethod(scan_missing_modules)
    handler = staticmethod(handler)
    listener = staticmethod(listener)
    _register_handler = staticmethod(_register_handler)
    run_coroutine_sync = staticmethod(run_coroutine_sync)
    export_func = staticmethod(export_func)
