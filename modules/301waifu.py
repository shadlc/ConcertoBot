"""抽老婆模块"""
import base64
import html
import io
import os
import random
import datetime
import re
import sqlite3
import traceback

import httpx
from PIL import Image
from src.base import Module
from src.utils import Utils

class Waifu(Module):
    """抽老婆模块"""
    ID = "Waifu"
    NAME = "抽老婆模块"
    HELP = {
        0: [
            "抽老婆/老公模块，无需使用@，直接发送关键词即可",
        ],
        1: [
            "(开启|关闭)抽老婆/抽老公 | 开启或关闭本模块功能(需要@)",
        ],
        2: [
            "抽老婆 | 看看今天的二次元老婆是谁",
            "抽老公 | 看看今天的二次元老公是谁",
            "添老婆 [老婆名] + 图片 | 添加老婆",
            "添老公 [老公名] + 图片 | 添加老公",
            "删老婆 [老婆名] | 删除老婆",
            "删老公 [老公名] | 删除老公",
            "查老婆 @某人 | 查询别人今天抽到的老婆",
            "查老婆 [老婆名] | 查询老婆是否存在",
            "查老公 @某人 | 查询别人今天抽到的老公",
            "查老公 [老公名] | 查询老公是否存在",
        ],
    }
    GLOBAL_CONFIG = {
        "pic_path": "waifu",
        "history_days": 30,
    }
    CONV_CONFIG = {
        "enable": True,
        "add_auth": 1,
        "user_rate": 0,
    }
    ROLE_LABELS = {
        "waifu": "老婆",
        "husband": "老公",
    }
    IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")

    def premise(self):
        """未开启群聊功能时，仅允许通过 @ 触发管理命令"""
        return self.group_at() or self.conv_config.get("enable")

    @Utils.handler(lambda self: self.group_at() and self.au(1) and self.match(r"^(开启|打开|启用|允许|关闭|禁止|不允许|取消)?抽(老婆|老公)$"))
    def toggle(self):
        """设置抽取功能"""
        label = "老公" if self.match(r"老公$") else "老婆"
        flag = self.conv_config["enable"]
        text = "开启" if self.conv_config["enable"] else "关闭"
        if self.match(r"(开启|打开|启用|允许)"):
            flag = True
            text = "开启"
        elif self.match(r"(关闭|禁止|不允许|取消)"):
            flag = False
            text = "关闭"
        msg = f"抽{label}功能已{text}"
        self.conv_config["enable"] = flag
        self.save_config()
        self.reply(msg, reply=True)

    def get_today_waifus(self):
        """获取今天已分配的老婆列表"""
        today = datetime.date.today().strftime("%Y%m%d")
        return self._get_draws_by_dates("waifu", {today})

    def _get_draws_by_dates(self, role, dates):
        """获取指定日期已分配的角色列表"""
        if not dates:
            return []
        placeholders = ",".join("?" for _ in dates)
        params = [self.owner_id, role, *dates]
        conn = self.get_history_db()
        try:
            rows = conn.execute(
                f"SELECT target FROM draw_history "
                f"WHERE owner_id=? AND role=? AND draw_date IN ({placeholders})",
                params,
            ).fetchall()
        finally:
            conn.close()
        return [row[0] for row in rows]

    def _get_history_start(self):
        """获取历史抽取限制的起始日期"""
        history_days = max(int(self.config.get("history_days", 30)), 0)
        return datetime.date.today() - datetime.timedelta(days=history_days)

    def get_recent_targets(self, role, user_id):
        """获取用户在限制周期内抽到过的角色"""
        history_days = max(int(self.config.get("history_days", 30)), 0)
        if history_days == 0:
            return set()
        start_date = self._get_history_start().strftime("%Y%m%d")
        conn = self.get_history_db()
        try:
            rows = conn.execute(
                "SELECT target FROM draw_history "
                "WHERE owner_id=? AND user_id=? AND role=? AND draw_date>=?",
                (self.owner_id, str(user_id), role, start_date),
            ).fetchall()
        finally:
            conn.close()
        return {row[0] for row in rows}

    def get_recent_draw_users(self, role):
        """获取限制周期内抽取过该角色的用户"""
        history_days = max(int(self.config.get("history_days", 30)), 0)
        if history_days == 0:
            return []
        start_date = self._get_history_start().strftime("%Y%m%d")
        conn = self.get_history_db()
        try:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM draw_history "
                "WHERE owner_id=? AND role=? AND draw_date>=?",
                (self.owner_id, role, start_date),
            ).fetchall()
        finally:
            conn.close()
        return [row[0] for row in rows]

    def get_recent_waifus(self):
        """获取今天和昨天已分配的老婆列表"""
        today = datetime.date.today()
        dates = {
            today.strftime("%Y%m%d"),
            (today - datetime.timedelta(days=1)).strftime("%Y%m%d"),
        }
        return self._get_draws_by_dates("waifu", dates)

    def get_available_waifus(self):
        """获取今天可用的老婆列表"""
        return self.get_available_people("waifu")

    def get_available_people(self, role):
        """获取今天可用的角色列表"""
        pic_path = self.get_path(role)
        files = [f for f in os.listdir(pic_path) if f.lower().endswith(self.IMAGE_EXTENSIONS)]

        if not files:
            return []

        # 排除今天和昨天已经分配过的角色
        recent_people = set(self._get_draws_by_dates(
            role,
            {
                datetime.date.today().strftime("%Y%m%d"),
                (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d"),
            },
        ))
        recent_people.update(self.get_recent_targets(role, self.event.user_id))
        recent_names = {os.path.splitext(person)[0] for person in recent_people}
        available_people = [
            person for person in files
            if os.path.splitext(person)[0] not in recent_names
        ]

        return available_people

    @Utils.handler(lambda self: self.au(2) and self.conv_config.get("enable") and self.match(r"^抽取?老婆$"))
    def draw_waifu(self):
        """抽取二次元老婆"""
        self.draw_person("waifu")

    @Utils.handler(lambda self: self.au(2) and self.conv_config.get("enable") and self.match(r"^抽取?老公$"))
    def draw_husband(self):
        """抽取二次元老公"""
        self.draw_person("husband")

    def draw_person(self, role):
        """抽取二次元角色"""
        today = datetime.date.today().strftime("%Y%m%d")
        user_id = str(self.event.user_id)
        label = self.ROLE_LABELS[role]
        person = self.get_draw_record(role, user_id, today)

        # 检查用户今天是否已经抽过角色
        if person is None:
            # 从可用角色中随机选择
            user_rate = self.conv_config.get("user_rate", 0)
            recent_targets = self.get_recent_targets(role, user_id)
            # 如果抽的是群角色，只允许近30天内抽过角色的群友
            recent_targets.update(self._get_draws_by_dates(
                role,
                {
                    datetime.date.today().strftime("%Y%m%d"),
                    (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d"),
                },
            ))
            user_list = [
                target for target in self.get_recent_draw_users(role)
                if target != user_id and target not in recent_targets
            ]

            # 是否抽取群角色
            is_user_person = user_rate > 0 and user_list and random.random() <= user_rate
            if is_user_person:
                # 抽群友
                person = random.choice(user_list)
            else:
                # 抽普通角色
                available_people = self.get_available_people(role)
                if not available_people:
                    return self.reply(f"今天的{label}已经被抽光啦，明天再来吧!", reply=True)
                person = random.choice(available_people)

            # 记录用户今天的角色
            self.record_draw(role, user_id, person, today)

        person_name, person_img, is_group_person = self.get_person_info(person, role)
        msg = f"你今天的二次元{label}是 {person_name} 哒~"
        if is_group_person:
            msg = f"你今天的群{label}是 {person_name} 哒~"

        image_source = f"base64://{person_img}" if person_img else f"http://q1.qlogo.cn/g?b=qq&nk={person}&s=640"
        person_cq = f"[CQ:image,file={image_source}]"
        result = self.reply_media(f"{msg}\n{person_cq}", "image", image_source, msg)

        if self.event.group_id and Utils.status_ok(result):
            if notify_maisaka := self.robot.func.get("notify_maisaka"):
                msg = f"{self.event.user_name}进行了今日的二次元抽{label}，今天的二次元{label}是{person_name}哒~"
                notify_maisaka(msg, self.event.group_id, self.event)

    @Utils.handler(lambda self: self.au(2) and self.conv_config.get("enable") and self.match(r"^查寻?老婆"))
    def check_waifu(self):
        """查询二次元老婆"""
        self.check_person("waifu")

    @Utils.handler(lambda self: self.au(2) and self.conv_config.get("enable") and self.match(r"^查寻?老公"))
    def check_husband(self):
        """查询二次元老公"""
        self.check_person("husband")

    def check_person(self, role):
        """查询二次元角色"""
        today = datetime.date.today().strftime("%Y%m%d")
        label = self.ROLE_LABELS[role]
        user_id = str(self.event.user_id)

        # 检查是否是查询用户角色
        if match := re.search(r"\[CQ:at,qq=(.*?)\]", self.event.msg):
            user_id = str(match.group(1))
            user_name = Utils.get_user_name(self.robot, user_id)
            person = self.get_draw_record(role, user_id, today)
            if person is None:
                return self.reply(f"未找到{user_name}的{label}信息!", reply=True)

            person_name, person_img, is_group_person = self.get_person_info(person, role)
            msg = f"{user_name}今天的二次元{label}是{person_name}哒~"
            if is_group_person:
                msg = f"{user_name}今天的群{label}是{person_name}哒~"

            image_source = f"base64://{person_img}" if person_img else f"http://q1.qlogo.cn/g?b=qq&nk={person}&s=640"
            person_cq = f"[CQ:image,file={image_source}]"
            self.reply_media(f"{msg}\n{person_cq}", "image", image_source, msg)
            return

        # 检查是否是查询角色是否存在
        else:
            # 提取角色名称
            person_name = re.sub(f"查寻?{label}", "", self.event.msg).strip()
            if not person_name:
                return self.reply(f"请输入要查询的{label}名称~", reply=True)

            # 检查角色是否存在并获取所有相关文件
            person_files = self.get_person_files(role, person_name)

            if person_files:
                # 如果只有一个文件，正常回复
                if len(person_files) == 1:
                    person_img = self.get_person_file(person_files[0], role)
                    self.reply(f"{person_name}已存在~[CQ:image,file=base64://{person_img}]", reply=True)
                else:
                    # 如果有多个文件，回复所有版本
                    reply_msg = f"{person_name}已存在，共有{len(person_files)}个格式："
                    for person_file in person_files:
                        person_img = self.get_person_file(person_file, role)
                        reply_msg += f"\n{person_file} [CQ:image,file=base64://{person_img}]"
                    self.reply(reply_msg, reply=True)
            else:
                self.reply(f"{person_name}不存在，可以添加哦~", reply=True)

    @Utils.handler(lambda self: self.au(self.conv_config.get("add_auth"))
         and self.conv_config.get("enable")
         and self.match(r"添加?老婆 "))
    def add_waifu(self):
        """添加二次元老婆"""
        self.add_person("waifu")

    @Utils.handler(lambda self: self.au(self.conv_config.get("add_auth"))
         and self.conv_config.get("enable")
         and self.match(r"添加?老公 "))
    def add_husband(self):
        """添加二次元老公"""
        self.add_person("husband")

    def add_person(self, role):
        """添加二次元角色"""
        person_name = ""
        try:
            img_url = ""
            if ret := self.match(r"\[CQ:image.*?url=(.*),.*\]"):
                img_url = ret.group(1)
            elif reply_msg := self.get_reply():
                if img_match := re.search(r"\[CQ:image.*?url=(.*),.*\]", reply_msg):
                    img_url = img_match.group(1)
            label = self.ROLE_LABELS[role]
            person_name = re.sub(rf"(添加?{label}|\[.*?\])", "", self.event.msg).strip()
            if not person_name:
                return self.reply(f"请注明二次元{label}名称~", reply=True)
            elif not img_url:
                return self.reply(f"请附带或回复二次元{label}图片~", reply=True)

            url = html.unescape(img_url)
            self.save_person(url, person_name, role)

            # 添加成功后，检查该角色名是否有多个版本
            person_files = self.get_person_files(role, person_name)

            # 回复添加成功消息并显示所有版本
            if len(person_files) == 1:
                self.reply(f"{person_name}已增加~", reply=True)
            else:
                reply_msg = f"{person_name}已增加~ 当前共有{len(person_files)}个格式："
                for person_file in person_files:
                    person_img = self.get_person_file(person_file, role)
                    reply_msg += f"\n{person_file} [CQ:image,file=base64://{person_img}]"
                self.reply(reply_msg, reply=True)

        except Exception: # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply(f"{person_name}添加失败!", reply=True)

    @Utils.handler(lambda self: self.au(self.conv_config.get("add_auth"))
        and self.conv_config.get("enable")
        and self.match(r"^删(除)?老婆"))
    def del_waifu(self):
        """删除二次元老婆"""
        self.del_person("waifu")

    @Utils.handler(lambda self: self.au(self.conv_config.get("add_auth"))
        and self.conv_config.get("enable")
        and self.match(r"^删(除)?老公"))
    def del_husband(self):
        """删除二次元老公"""
        self.del_person("husband")

    def del_person(self, role):
        """删除二次元角色"""
        person_input = ""
        try:
            # 提取角色名称和格式
            label = self.ROLE_LABELS[role]
            person_input = re.sub(f"删(除)?{label}", "", self.event.msg).strip()
            if not person_input:
                return self.reply(f"请输入要删除的{label}名称和格式，例如：删{label} {label}名.jpg", reply=True)

            # 支持的图片格式
            supported_formats = self.IMAGE_EXTENSIONS

            # 检查是否指定了格式
            target_format = None
            for fmt in supported_formats:
                if person_input.lower().endswith(fmt):
                    target_format = fmt
                    break

            # 提取角色名（移除格式后缀）
            person_name = person_input[:-len(target_format)] if target_format else person_input
            if not person_name:
                return self.reply(f"请输入有效的{label}名称", reply=True)

            pic_path = self.get_path(role)
            if target_format:
                person_file = f"{person_name}{target_format}"
            else:
                # 未指定格式时，仅允许删除唯一匹配的角色文件
                person_files = [
                    f"{person_name}{fmt}"
                    for fmt in supported_formats
                    if os.path.exists(os.path.join(pic_path, f"{person_name}{fmt}"))
                ]
                if not person_files:
                    return self.reply(f"未找到{label} {person_name}", reply=True)
                if len(person_files) > 1:
                    return self.reply(f"请指定要删除的图片格式，例如：删{label} {label}名.jpg\n支持的格式有：jpg、jpeg、png", reply=True)
                person_file = person_files[0]

            file_path = os.path.join(pic_path, person_file)

            if os.path.exists(file_path):
                os.remove(file_path)
                self.reply(f"成功删除{label} {person_file}", reply=True)
            else:
                self.reply(f"未找到{label} {person_file}", reply=True)

        except Exception: # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply(f"{person_input}删除失败!", reply=True)

    def get_path(self, role="waifu"):
        """获取二次元角色路径"""
        path = None
        if os.path.isabs(self.config["pic_path"]):
            path = self.config["pic_path"]
        else:
            path = os.path.join(self.robot.config.data_path, self.config["pic_path"])
        path = os.path.join(path, role)
        os.makedirs(path, exist_ok=True)
        return path

    def get_person_files(self, role, name):
        """获取二次元角色的所有图片"""
        return [
            f"{name}{ext}"
            for ext in self.IMAGE_EXTENSIONS
            if os.path.exists(os.path.join(self.get_path(role), f"{name}{ext}"))
        ]

    def get_person_file(self, filename: str, role):
        """读取二次元角色"""
        pic_path = self.get_path(role)
        filepath = os.path.join(pic_path, filename)
        if not os.path.exists(filepath):
            return None
        with open(filepath, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def save_person(self, url: str, name: str, role):
        """保存二次元角色"""
        pic_path = self.get_path(role)
        data = httpx.get(url, timeout=10)
        data.raise_for_status()
        with Image.open(io.BytesIO(data.content)) as image:
            fmt = image.format.lower()
        file_path = os.path.join(pic_path, f"{name}.{fmt}")
        with open(file_path, "wb") as f:
            f.write(data.content)

    def get_user_file(self, uid: str, role):
        """获取群友角色图片"""
        pic_path = self.get_path(role)

        for ext in self.IMAGE_EXTENSIONS:
            filepath = os.path.join(pic_path, f"{uid}{ext}")
            if os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
        return None

    def get_person_info(self, person, role):
        """获取角色名称、图片和类型"""
        if re.search(r"^[0-9]+$", person):
            return Utils.get_user_name(self.robot, person), self.get_user_file(person, role), True
        return os.path.splitext(person)[0], self.get_person_file(person, role), False

    def get_history_db(self):
        """获取抽取历史数据库连接"""
        conn = sqlite3.connect(self.get_data_path("history.db"))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS draw_history (
                owner_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                target TEXT NOT NULL,
                draw_date TEXT NOT NULL,
                PRIMARY KEY (owner_id, user_id, role, draw_date)
            )
            """
        )
        conn.commit()
        return conn

    def get_draw_record(self, role, user_id, draw_date):
        """获取用户指定日期的抽取记录"""
        conn = self.get_history_db()
        try:
            row = conn.execute(
                "SELECT target FROM draw_history "
                "WHERE owner_id=? AND user_id=? AND role=? AND draw_date=?",
                (self.owner_id, str(user_id), role, draw_date),
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None

    def record_draw(self, role, user_id, target, draw_date):
        """保存抽取记录"""
        conn = self.get_history_db()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO draw_history "
                "(owner_id, user_id, role, target, draw_date) VALUES (?, ?, ?, ?, ?)",
                (self.owner_id, str(user_id), role, target, draw_date),
            )
            conn.commit()
        finally:
            conn.close()

    def get_waifu_file(self, filename: str):
        """读取二次元老婆"""
        return self.get_person_file(filename, "waifu")

    def save_waifu(self, url: str, name: str):
        """保存二次元老婆"""
        self.save_person(url, name, "waifu")

    def get_user_waifu_file(self, uid: str):
        """获取群友老婆图片"""
        return self.get_user_file(uid, "waifu")
