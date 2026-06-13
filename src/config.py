"""配置类"""

import os
import sys
import time
import json
import logging

logger = logging.getLogger(__name__)

class Config:
    """配置类"""
    def __init__(self, config_file) -> None:
        self.config_file = config_file
        self.default = {
            "host": "127.0.0.1",
            "port": 3002,
            "api_base": "http://127.0.0.1:3000/",
            "data_path": "data",
            "log_path": "logs",
            "rev_group": [],
            "admin_list": [],
            "blacklist": [],
            "is_debug": False,
            "is_silence": False,
            "is_show_heartbeat": False,
            "is_always_reply": False,
            "is_show_all_msg": False,
            "is_show_image": False,
            "is_error_reply": True,
            "image_color": "disabled",
            "min_image_width": 10,
            "max_image_width": 100,
            "disabled": [],
        }
        self.init_config()
        self.raw = self.read()
        self.host = self.raw.get("host", self.default["host"])
        self.port = self.raw.get("port", self.default["port"])
        self.api_base = self.raw.get("api_base", self.default["api_base"])
        self.data_path = self.raw.get("data_path", self.default["data_path"])
        self.log_path = self.raw.get("log_path", self.default["log_path"])
        self.rev_group = self.raw.get("rev_group", self.default["rev_group"])
        self.admin_list = self.raw.get("admin_list", self.default["admin_list"])
        self.blacklist = self.raw.get("blacklist", self.default["blacklist"])
        self.is_debug = self.raw.get("is_debug", self.default["is_debug"])
        self.is_silence = self.raw.get("is_silence", self.default["is_silence"])
        self.is_show_heartbeat = self.raw.get("is_show_heartbeat", self.default["is_show_heartbeat"])
        self.is_always_reply = self.raw.get("is_always_reply", self.default["is_always_reply"])
        self.is_show_all_msg = self.raw.get("is_show_all_msg", self.default["is_show_all_msg"])
        self.is_show_image = self.raw.get("is_show_image", self.default["is_show_image"])
        self.is_error_reply = self.raw.get("is_error_reply", self.default["is_error_reply"])
        self.image_color = self.raw.get("image_color", self.default["image_color"])
        self.min_image_width = self.raw.get("min_image_width", self.default["min_image_width"])
        self.max_image_width = self.raw.get("max_image_width", self.default["max_image_width"])
        self.disabled = self.raw.get("disabled", self.default["disabled"])

    def init_config(self):
        """初始化配置文件"""
        try:
            if path := os.path.dirname(self.config_file):
                os.makedirs(path, exist_ok=True)
            open(self.config_file, encoding="utf-8")
        except (FileNotFoundError, FileNotFoundError, json.JSONDecodeError):
            json.dump(self.default, open(self.config_file, mode="w", encoding="utf-8"), ensure_ascii=False, indent=2)

    def read(self, key: str = "") -> list | dict | str | int | bool:
        """获取指定配置"""
        try:
            if key:
                json_data = json.load(open(self.config_file, encoding="utf-8")).get(key)
            else:
                json_data = json.load(open(self.config_file, encoding="utf-8"))
            return json_data
        except FileNotFoundError as e:
            logger.error("配置文件未找到: %s", e)
        except json.JSONDecodeError as e:
            logger.error("解析配置文件失败: %s，程序会在5秒后自动退出", e)
            time.sleep(5)
            sys.exit(0)

    def save(self, key, value: list | dict | str | int | bool = "") -> None:
        """保存指定配置文件"""
        try:
            json_data = json.load(open(self.config_file, encoding="utf-8"))
            json_data[key] = value
            json.dump(json_data, open(self.config_file, mode="w", encoding="utf-8"), ensure_ascii=False, indent=2)
        except FileNotFoundError as e:
            logger.error("配置文件未找到: %s", e)
        except json.JSONDecodeError as e:
            logger.error("解析配置文件失败: %s", e)
        except (OSError, TypeError) as e:
            logger.error("保存配置文件发生错误: %s", e)
