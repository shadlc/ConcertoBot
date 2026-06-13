"""函数库"""

from __future__ import annotations

import ast
import asyncio
import base64
from datetime import datetime, timedelta
from functools import wraps
import importlib
import io
import logging
import os
from pathlib import Path
import re
import html
import socket
import json
import random
import traceback

from typing import TYPE_CHECKING, Callable, Coroutine, Dict, Optional, Set, Union

import httpx
from PIL import Image
from colorama import Fore, Style

from src import api

if TYPE_CHECKING:
    from src.robot import Concerto


def listening(host: str, port: int, timeout: int = 5) -> tuple[dict | str]:
    """监听指定地址与端口"""
    try:
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen()
        client, _ = server.accept()
        client.settimeout(timeout)
        # client.setblocking(False)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = client.recv(1024)
            if not chunk:
                break
            response.extend(chunk)
        header_bytes, remaining = response.split(b"\r\n\r\n", 1)
        lines = header_bytes.decode("iso-8859-1").splitlines()
        method, path, version = lines[0].split(" ", 2)
        headers = {"Method": method, "Path": path, "HTTP-Version": version}
        content_length = 0
        transfer_encoding = None
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                key, value = key.strip(), value.strip()
                headers[key] = value
                if key.lower() == "content-length":
                    content_length = int(value)
                elif key.lower() == "transfer-encoding":
                    transfer_encoding = value.lower()
        body = bytearray()
        if transfer_encoding != "chunked":
            body.extend(remaining)
            while len(body) < content_length:
                chunk = client.recv(1024)
                if not chunk:
                    break
                body.extend(chunk)
        else:
            buffer = bytearray(remaining)
            while True:
                while b"\r\n" not in buffer:
                    buffer.extend(client.recv(1024))
                line, _, buffer = buffer.partition(b"\r\n")
                chunk_size = int(line.decode("ascii"), 16)
                if chunk_size == 0:
                    while len(buffer) < 2:
                        buffer.extend(client.recv(1024))
                    buffer = buffer[2:]
                    break
                while len(buffer) < chunk_size + 2:
                    buffer.extend(client.recv(1024))
                body.extend(buffer[:chunk_size])
                buffer = buffer[chunk_size + 2 :]
        client.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
        body = body.decode("utf-8")
    finally:
        client.close()
        server.close()
    return headers, body


def receive_msg(robot: Concerto):
    """接收数据"""
    body = None
    try:
        header, body = listening(robot.config.host, int(robot.config.port))
        if header.get("Content-Type") != "application/json":
            robot.warnf(f"收到一非JSON数据\n{body}", level="DEBUG")
            return {}
        rev_json = json.loads(body)
        return rev_json
    except OSError as e:
        robot.errorf(f"端口{robot.config.port}已被占用，程序终止！ {e}")
        robot.stop()
    except socket.gaierror as e:
        robot.errorf(
            f"绑定地址有误！ {robot.config.host} 不是一个正确的可绑定地址，程序终止！ {e}"
        )
        robot.stop()
    except json.JSONDecodeError as e:
        robot.warnf(f"{body} JSON数据解析失败！ {traceback.format_exc()}")
        return {}


def import_json(file: str):
    """导入json"""
    try:
        content = "{}"
        if not os.path.exists(file):
            open(file, "w", encoding="utf-8").write("{}")
        if temp := open(file, "r", encoding="utf-8").read():
            content = temp
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise e


def save_json(file_name: str, data: str):
    """导出json"""
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
    text = re.sub(r"(\s?█+\s*)+", "[图片]", text)
    return text.strip()


def char_colorama(char: str, rgb: list):
    """
    为字符添加标准8色
    :param char: 字符
    :param rgb: (R, G, B) 取值 0–255
    :return: 彩色字符（Standard 8 色）
    """
    r, g, b = rgb
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    diff = max_c - min_c
    if diff <= 50:
        return char
    color = ""
    if r == max_c:
        if g > b:
            color = Fore.YELLOW
        elif b > g:
            color = Fore.MAGENTA
        else:
            color = Fore.RED
    elif g == max_c:
        if r > b:
            color = Fore.YELLOW
        elif b > r:
            color = Fore.CYAN
        else:
            color = Fore.GREEN
    elif b == max_c:
        if r > g:
            color = Fore.MAGENTA
        elif g > r:
            color = Fore.BLUE
        else:
            color = Fore.BLUE
    return color + char + Fore.RESET


def char_ansi_256(char: str, rgb: list):
    """
    使用 ANSI 256 色 输出字符
    :param char: 字符
    :param rgb: (R, G, B) 取值 0–255
    :return: 彩色字符（ANSI 256 色）
    """
    r, g, b = [x / 255.0 for x in rgb]
    r_ = int(r * 5)
    g_ = int(g * 5)
    b_ = int(b * 5)
    color_code = 16 + 36 * r_ + 6 * g_ + b_
    return f"\033[38;5;{color_code}m{char}\033[0m"


def char_true_color(char: str, rgb: list):
    """
    使用 TrueColor 输出字符
    :param char: 字符
    :param rgb: (R, G, B) 取值 0–255
    :return: 彩色字符（24bit）
    """
    r, g, b = rgb
    return f"\033[38;2;{r};{g};{b}m{char}\033[0m"


def msg_img2char(robot: Concerto, msg: str, show_url=False):
    """
    检测CQ码中有图片并转化为字符画
    :param robot: 机器人类
    :param msg: 收到的消息
    :param color: 是否渲染颜色
    :return: 转化为字符画的消息
    """
    if "[RECEIVE]" not in msg:
        return msg
    matches = re.findall(r"(\[CQ:image.*?url=([^,]*).*\])", msg)
    for cq, url in matches:
        try:
            data = httpx.get(url, timeout=10)
            img = Image.open(io.BytesIO(data.content)).convert("RGB")
            w, h = img.size
            ratio = h / float(w)
            target_w = sorted(
                [robot.config.min_image_width, w, robot.config.max_image_width]
            )[1]
            target_h = int(target_w * ratio * 0.5)
            img = img.resize((target_w, target_h))
            pixels = img.getdata()
            char = "\n"
            row = 0
            for i in pixels:
                pixel_gray = (i[0] * 38 + i[1] * 75 + i[2] * 15) >> 7
                if robot.config.image_color == "colorama":
                    image_ascii = list(".,:;+*?#%@")
                    unit = (256 + 1) / len(image_ascii)
                    single_char = image_ascii[int(pixel_gray // unit)]
                    char += char_colorama(single_char, i)
                elif robot.config.image_color == "ansi_256":
                    char += char_ansi_256("█", i)
                elif robot.config.image_color == "true_color":
                    char += char_true_color("█", i)
                else:
                    image_ascii = list(".,:;+*?#%@")
                    unit = (256 + 1) / len(image_ascii)
                    single_char = image_ascii[int(pixel_gray // unit)]
                    char += single_char
                row += 1
                if row >= target_w:
                    row = 0
                    char += "\n"
            if show_url:
                char = f"{url}{char}"
            msg = msg.replace(cq, char)
        except Exception:  # pylint: disable=broad-exception-caught
            robot.errorf(f"图片转字符画失败!\n{traceback.format_exc()}", level="DEBUG")
    return msg


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
    return ocr_image(robot, resp_dict)


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
        robot.errorf(f"获取腾讯图床链接失败 {traceback.format_exc()}")
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

def simplify_traceback(tb: str):
    """
    获取错误报告并简化
    :param tb: 获取的错误报告
    :return: 易读的错误报告
    """
    result = "按从执行顺序排序有\n"
    tb = tb.strip().split("\n")
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
        @wraps(func)
        def wrapper(self: Module, *args, **kwargs):
            method_name = f"{self.ID}.{func.__name__}"
            if not condition(self):
                # self.robot.printf(f"未满足[{method_name}]的执行条件", level="DEBUG")
                return None
            self.printf(
                f"执行{Fore.YELLOW}[{method_name}]{Fore.RESET}方法", level="DEBUG"
            )
            try:
                self.handled = handled
                result = func(self, *args, **kwargs)
                if asyncio.iscoroutine(result):
                    return run_coroutine_sync(result)
                if handled:
                    self.printf(
                        f"{Fore.YELLOW}[{method_name}]{Fore.RESET}方法已处理该事件",
                        level="DEBUG",
                    )
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


class MiniCron:
    """简单Crontab，支持同步和异步函数"""

    def __init__(
        self,
        expr: str,
        task: Union[Callable[[], None], Callable[[], Coroutine]],
        loop=None,
    ) -> None:
        """
        expr: crontab 表达式 (如 "0 8-12/1 * * *" 表示8点到12点每小时执行)
        task: 要执行的函数，无参数，可以是同步函数或异步函数
        """
        self.expr: str = expr
        self.task: Union[Callable[[], None], Callable[[], Coroutine]] = task
        self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_event_loop()
        self.cron_fields: Dict[str, Set[int]] = self.parse_cron(expr)
        self.is_async = asyncio.iscoroutinefunction(task)
        self.running = False

    def parse_field(self, field: str, min_val: int, max_val: int) -> Set[int]:
        """解析单个字段，返回允许的整数集合"""
        if field == "*":
            return set(range(min_val, max_val + 1))
        values: Set[int] = set()
        # 处理步长表达式 (如 8-12/1)
        if "/" in field:
            range_part, step_part = field.split("/", 1)
            step = int(step_part)
            if range_part == "*":
                # */n 格式
                values.update(range(min_val, max_val + 1, step))
            elif "-" in range_part:
                # start-end/step 格式 (如 8-12/1)
                start_str, end_str = range_part.split("-")
                start = int(start_str)
                end = int(end_str)
                values.update(range(start, end + 1, step))
            else:
                # 单个值/step 格式
                base = int(range_part)
                values.update(range(base, max_val + 1, step))
        else:
            # 没有步长的普通处理
            for part in field.split(","):
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    values.update(range(start, end + 1))
                else:
                    values.add(int(part))
        return values

    def parse_cron(self, expr: str) -> Dict[str, Set[int]]:
        """解析 cron 表达式，返回每个字段允许的整数集合"""
        minute, hour, day, month, weekday = expr.split()
        return {
            "minute": self.parse_field(minute, 0, 59),
            "hour": self.parse_field(hour, 0, 23),
            "day": self.parse_field(day, 1, 31),
            "month": self.parse_field(month, 1, 12),
            "weekday": self.parse_field(weekday, 0, 6),  # 0=周日 … 6=周六
        }

    def next_time(self, from_time: Optional[datetime] = None) -> datetime:
        """计算下一个匹配 cron 表达式的时间点"""
        if from_time is None:
            from_time = datetime.now().replace(second=0, microsecond=0) + timedelta(
                minutes=1
            )
        else:
            from_time = from_time.replace(second=0, microsecond=0) + timedelta(
                minutes=1
            )
        max_attempts = 100000  # 防止无限循环
        attempts = 0
        while attempts < max_attempts:
            attempts += 1
            if (
                from_time.minute in self.cron_fields["minute"]
                and from_time.hour in self.cron_fields["hour"]
                and from_time.day in self.cron_fields["day"]
                and from_time.month in self.cron_fields["month"]
                and from_time.weekday() in self.cron_fields["weekday"]
            ):
                return from_time
            from_time += timedelta(minutes=1)
        raise ValueError("无法找到下一个执行时间, 请检查cron表达式")

    async def execute_task(self) -> None:
        """执行任务，支持同步和异步函数"""
        if self.is_async:
            await self.task()
        else:
            result = await self.loop.run_in_executor(None, self.task)
            if asyncio.iscoroutine(result):
                await result

    async def run(self) -> None:
        """开始循环执行任务"""
        self.running = True
        next_run: datetime = self.next_time()
        while self.running:
            now: datetime = datetime.now()
            sleep_seconds = (next_run - now).total_seconds()
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)
            await self.execute_task()
            next_run = self.next_time(datetime.now())

    def stop(self) -> None:
        """停止任务执行"""
        self.running = False


class Event:
    """基础事件结构"""

    def __init__(self, robot: Concerto, raw=None):
        # 机器人本类
        self.robot = robot
        # 原始数据结构
        self.raw = raw = raw or {}
        # 上报类型 message消息 notice系统提示 request请求
        self.post_type = raw.get("post_type", "")
        # 事件发生的时间戳
        self.time = raw.get("time", "")
        # 机器人自身QQ号
        self.self_id = str(raw.get("self_id", ""))
        # 消息类型 private私聊 group群聊
        self.msg_type = raw.get("message_type", "")
        # 通知类型 notify常用通知 essence群精华消息 group_upload群文件上传 group_admin群变动 group_decrease群成员减少 group_increase群成员增加 group_ban群禁言 friend_add好友添加 group_recall群消息撤回 friend_recall好友消息撤回 group_card群成员名片更新 offline_file离线文件 client_status客户端状态变更
        self.notice_type = raw.get("notice_type", "")
        # 消息子类型 friend好友 group群临时会话 group_self群聊 other其他 normal普通 anonymous匿名 notice系统提示
        self.sub_type = raw.get("sub_type", "")
        # 消息 ID
        self.msg_id = raw.get("message_id", "")
        # 原始消息内容
        self.msg = html.unescape(raw.get("message", ""))
        if "CQ:json" in self.msg:
            self.msg = re.sub(r"(\s)+", "", self.msg)
        # 消息内容，去除AT信息
        self.text = self.msg.replace(self.robot.at_info, "").strip()
        # 发送者ID
        self.sender_id = str(raw.get("sender_id", ""))
        # 发送者QQ号
        self.user_id = str(raw.get("user_id", ""))
        # 发送者名称
        self.user_name = raw.get("sender", {}).get("nickname", "")
        if self.user_name == "" and self.user_id.isdigit():
            self.user_name = get_user_name(robot, self.user_id)
        # 发送者昵称
        self.user_card = raw.get("sender", {}).get("card", "")
        # 群号
        self.group_id = str(raw.get("group_id", ""))
        if self.group_id == "0":
            self.group_id = ""
        # 群名
        self.group_name = get_group_name(robot, self.group_id)
        # 目标QQ号
        self.target_id = str(raw.get("target_id", ""))
        # 目标昵称
        self.target_name = (
            get_user_name(robot, self.target_id)
            if self.msg_type == "private"
            else get_group_name(robot, self.group_id)
        )
        # 操作者QQ号
        self.operator_id = str(raw.get("operator_id", ""))
        # 操作者昵称
        self.operator_name = get_user_name(robot, self.operator_id)
        self.operator_nick = raw.get("operator_nick", "")


class Module:
    """模块基类"""

    ID = None
    NAME = None
    HELP = None
    CONFIG = None
    GLOBAL_CONFIG = None
    CONV_CONFIG = None
    AUTO_INIT = None

    HANDLE_MESSAGE = True
    HANDLE_MESSAGE_SENT = False
    HANDLE_NOTICE = False
    HANDLE_REQUEST = False
    HANDLE_META_EVENT = False

    def __init__(self, event: Event, auth: int = 0):
        self.name = self.__class__.NAME
        self.handled = False
        self.event = event
        self.robot = event.robot
        self.auth = int(auth)
        self.owner_id = ""
        self.config_file = ""
        self.config = {}
        self.conv_config = {}
        self.data = {}
        self.init_config()
        if not self.premise():
            return
        self.activate()

    def premise(self):
        """前置条件"""
        return True

    def activate(self):
        """执行类方法"""
        for attr_name in dir(self):
            if self.handled:
                return
            attr = getattr(self, attr_name)
            if callable(attr) and hasattr(attr, "_is_handler"):
                attr()

    def au(self, max_level=3, min_level=0):
        """检查权限等级"""
        return min_level <= self.auth <= max_level

    def group_at(self):
        """仅群聊@消息触发"""
        return self.robot.at_info in self.event.msg

    def at_or_private(self):
        """群聊@消息以及私聊消息触发"""
        return self.event.group_id == "" or self.robot.at_info in self.event.msg

    def start_with_sign(self):
        """开头#触发"""
        return re.search(r"^#\S+", self.event.msg)

    def match(self, pattern: str):
        """消息规则匹配"""
        return re.search(pattern, self.event.text)

    def is_self_send(self):
        """判断是不是自己发送的数据"""
        return self.robot.self_id in [self.event.user_id, self.event.sender_id]

    def is_private(self):
        """是否私聊"""
        return self.event.group_id == ""

    def is_reply(self):
        """是否包含回复消息"""
        return self.match(r"\[CQ:reply,id=([^\]]+?)\]")

    def init_config(self):
        """初始化模块数据"""
        # 设定会话的owner_id
        if self.event.group_id:
            self.owner_id = f"g{self.event.group_id}"
        elif self.event.user_id:
            self.owner_id = f"u{self.event.user_id}"
        else:
            self.owner_id = f"u{self.robot.self_id}"
        # 读取指定会话的数据与配置文件
        self.data = self.robot.data.get(self.owner_id)
        if self.GLOBAL_CONFIG is None and self.CONV_CONFIG is None:
            return
        config_name = self.CONFIG
        if config_name is None:
            config_name = f"{str(self.ID).lower()}.json"
        self.config_file = os.path.join(self.robot.config.data_path, config_name)
        try:
            self.config = import_json(self.config_file)
        except Exception:  # pylint: disable=broad-exception-caught
            self.config = {}
            self.errorf(
                f"配置文件 {self.config_file} 解析发生错误!\n{traceback.format_exc()}"
            )
        self.GLOBAL_CONFIG = self.GLOBAL_CONFIG or {} # pylint: disable=invalid-name
        self.config = merge(self.GLOBAL_CONFIG, self.config)
        if self.CONV_CONFIG is None:
            self.save_config()
            return
        if self.owner_id not in self.config:
            self.config[self.owner_id] = {}
        self.CONV_CONFIG = self.CONV_CONFIG or {} # pylint: disable=invalid-name
        self.config[self.owner_id] = merge(self.CONV_CONFIG, self.config[self.owner_id])
        self.conv_config = self.config[self.owner_id]
        self.save_config()

    def save_config(self, config_content=None, owner_id=""):
        """保存模块配置"""
        if owner_id and config_content:
            self.config[owner_id] = config_content
        elif config_content:
            self.config = config_content
        if self.config == import_json(self.config_file):
            return
        try:
            save_json(self.config_file, self.config)
        except Exception:  # pylint: disable=broad-exception-caught
            self.errorf(
                f"配置文件 {self.config_file} 保存失败!\n{traceback.format_exc()}"
            )

    def get_data_path(self, *paths: str) -> str:
        """获取配置文件夹"""
        config_path = self.CONFIG if self.CONFIG else str(self.ID).lower()
        path = os.path.join(self.robot.config.data_path, config_path, *paths)
        dir_path = path if os.path.splitext(path)[1] == "" else os.path.dirname(path)
        os.makedirs(dir_path, exist_ok=True)
        return Path(path).as_posix()

    def node(self, *args, **kwargs) -> dict:
        """
        生成一个转发节点
        user_id,nickname,content
        """
        if len(args) == 1 and isinstance(args[0], str):
            content = handle_placeholder(args[0], self.robot.placeholder_dict)
            return build_node(content, **kwargs)
        return build_node(*args, **kwargs)

    def reply(self, msg, reply=False, force=False):
        """快捷回复消息"""
        if self.robot.config.is_always_reply:
            reply = True
        result = reply_event(self.robot, self.event, msg, reply=reply, force=force)
        if not status_ok(result):
            self.errorf(result.get("message"))
        return result

    def reply_forward(self, nodes: list, source=None, summary=None):
        """快捷回复转发消息"""
        result = send_forward_msg(
            self.robot, nodes, self.event.group_id, self.event.user_id, source, summary
        )
        if not status_ok(result):
            self.errorf(result.get("message"))
        return result

    def get_reply(
        self,
    ) -> str | None:
        """读取可能存在的回复消息"""
        reply_match = self.is_reply()
        if not reply_match:
            return
        msg_id = reply_match.group(1)
        reply_msg = get_msg(self.robot, msg_id)
        if not status_ok(reply_msg):
            return
        msg = html.unescape(reply_msg["data"]["message"])
        msg = re.sub(r"[\\\r\n]+", "", msg)
        return msg

    def printf(self, msg, end="\n", console=True, flush=False, level="INFO"):
        """
        向控制台输出通知级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        if not flush:
            msg = f"{Fore.CYAN}[{self.ID}]{Fore.RESET} {msg}"
        self.robot.printf(msg=msg, end=end, console=console, flush=flush, level=level)

    def warnf(self, msg, end="\n", console=True, level="INFO"):
        """
        向控制台输出警告级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        if level == "DEBUG" and not self.robot.config.is_debug:
            return
        self.robot.warnf(
            f"{Fore.CYAN}[{self.ID}]{Fore.YELLOW} {msg}",
            end=end,
            console=console,
            level=level,
        )

    def errorf(self, msg, end="\n", console=True, level="INFO"):
        """
        向控制台输出错误级别的消息
        :param msg: 信息
        :param end: 末尾字符
        :param console: 是否增加一行<console>
        """
        if level == "DEBUG" and not self.robot.config.is_debug:
            return
        self.robot.errorf(
            f"{Fore.CYAN}[{self.ID}]{Fore.RED} {msg}",
            end=end,
            console=console,
            level=level,
        )
