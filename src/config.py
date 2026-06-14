"""配置类"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import logging
import os
import shutil
import tempfile
import time
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

Validator = Callable[[Any], Any]


@dataclass(frozen=True)
class ConfigRule:
    """单个配置项的默认值与校验规则"""

    default: Any
    validator: Validator


def validate_list(value: Any) -> list[Any]:
    """校验列表配置"""
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    raise TypeError("需要列表类型")


def validate_bool(value: Any) -> bool:
    """校验布尔配置"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"true", "1", "yes", "on"}:
            return True
        if value in {"false", "0", "no", "off"}:
            return False
    raise TypeError("需要布尔类型")


def validate_str(value: Any) -> str:
    """校验非空字符串配置"""
    if isinstance(value, str) and value.strip():
        return value
    raise TypeError("需要非空字符串")


def int_validator(
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    name: str = "整数值",
) -> Validator:
    """构造整数校验器"""

    def validate(value: Any) -> int:
        """校验单个整数配置值"""
        if isinstance(value, bool):
            raise TypeError(f"{name}需要整数类型")
        value = int(value)
        if minimum is not None and value < minimum:
            raise ValueError(f"{name}不能小于 {minimum}")
        if maximum is not None and value > maximum:
            raise ValueError(f"{name}不能大于 {maximum}")
        return value

    return validate


def validate_image_color(value: Any) -> str:
    """校验图片颜色模式"""
    value = validate_str(value)
    if value not in ["disabled", "braille", "gray", "colorama", "ansi_256", "true_color"]:
        raise ValueError(f"不支持的图片颜色模式: {value!r}")
    return value


class Config:
    """运行时配置"""

    host: str
    port: int
    api_base: str
    data_path: str
    log_path: str
    rev_group: list[Any]
    admin_list: list[Any]
    blacklist: list[Any]
    is_debug: bool
    is_silence: bool
    is_show_heartbeat: bool
    is_always_reply: bool
    is_show_all_msg: bool
    is_show_image: bool
    is_error_reply: bool
    image_color: Literal["disabled", "gray", "gray", "colorama", "ansi_256", "true_color", "half_block", "braille"]
    min_image_width: int
    max_image_width: int
    handler_workers: int
    disabled: list[Any]

    SCHEMA: dict[str, ConfigRule] = {
        "host": ConfigRule("127.0.0.1", validate_str),
        "port": ConfigRule(3002, int_validator(minimum=1, maximum=65535, name="端口")),
        "api_base": ConfigRule("http://127.0.0.1:3000/", validate_str),
        "data_path": ConfigRule("data", validate_str),
        "log_path": ConfigRule("logs", validate_str),
        "rev_group": ConfigRule([], validate_list),
        "admin_list": ConfigRule([], validate_list),
        "blacklist": ConfigRule([], validate_list),
        "is_debug": ConfigRule(False, validate_bool),
        "is_silence": ConfigRule(False, validate_bool),
        "is_show_heartbeat": ConfigRule(False, validate_bool),
        "is_always_reply": ConfigRule(False, validate_bool),
        "is_show_all_msg": ConfigRule(False, validate_bool),
        "is_show_image": ConfigRule(False, validate_bool),
        "is_error_reply": ConfigRule(True, validate_bool),
        "image_color": ConfigRule("disabled", validate_image_color),
        "min_image_width": ConfigRule(10, int_validator(minimum=10, maximum=1000, name="最小图片宽度")),
        "max_image_width": ConfigRule(100, int_validator(minimum=10, maximum=1000, name="最大图片宽度")),
        "handler_workers": ConfigRule(3, int_validator(minimum=1, name="消息处理线程数")),
        "disabled": ConfigRule([], validate_list),
    }

    def __init__(self, config_file: str) -> None:
        """读取配置文件并应用默认值和校验规则"""
        self.config_file = config_file
        self.default = {key: deepcopy(rule.default) for key, rule in self.SCHEMA.items()}
        self.raw = self._load()
        self._apply()

    def _load(self) -> dict[str, Any]:
        """加载配置，完成规范化，并在必要时回写到磁盘"""
        if path := os.path.dirname(self.config_file):
            os.makedirs(path, exist_ok=True)

        try:
            with open(self.config_file, encoding="utf-8") as file:
                loaded = json.load(file)
        except FileNotFoundError:
            logger.info("未找到配置文件，将创建默认配置: %s", self.config_file)
            loaded = {}
        except json.JSONDecodeError as error:
            loaded = self._handle_damaged("配置文件 JSON 格式无效", error)

        if not isinstance(loaded, dict):
            loaded = self._handle_damaged("配置文件根节点不是对象")

        normalized = self._normalize(loaded)
        if normalized != loaded:
            self._write(normalized)
        return normalized

    def _normalize(self, config: dict[str, Any]) -> dict[str, Any]:
        """合并默认值，并将支持的配置值转换为安全的运行时类型"""
        normalized = {
            key: self._normalize_value(key, config.get(key, deepcopy(rule.default)), rule)
            for key, rule in self.SCHEMA.items()
        }
        if normalized["min_image_width"] > normalized["max_image_width"]:
            logger.warning(
                "%s 中的图片宽度范围无效: min_image_width=%s, max_image_width=%s，将自动对齐",
                self.config_file,
                normalized["min_image_width"],
                normalized["max_image_width"],
            )
            normalized["min_image_width"] = normalized["max_image_width"]
        return normalized

    def _normalize_value(self, key: str, value: Any, rule: ConfigRule) -> Any:
        """规范化单个配置值；若不合法则回退到默认值"""
        try:
            return rule.validator(value)
        except (TypeError, ValueError) as error:
            logger.warning(
                "%s 中的配置项 %s=%r 不合法，将使用默认值 %r(原因：%s)",
                self.config_file,
                key,
                value,
                rule.default,
                error,
            )
            return deepcopy(rule.default)

    def _apply(self) -> None:
        """将规范化后的配置字典应用到实例属性"""
        for key, value in self.raw.items():
            setattr(self, key, value)
        os.makedirs(self.data_path, exist_ok=True)
        os.makedirs(self.log_path, exist_ok=True)

    def _handle_damaged(self, message: str, error: Exception | None = None) -> dict[str, Any]:
        """处理损坏配置；如果备份失败则直接退出程序"""
        try:
            backup_file = f"{self.config_file}.broken.{time.strftime('%Y%m%d%H%M%S')}.bak"
            shutil.copyfile(self.config_file, backup_file)
        except OSError as backup_error:
            logger.critical(
                "%s，且备份损坏配置失败，程序退出。原始错误: %s；备份错误: %s",
                message,
                error or "无",
                backup_error,
            )
            raise SystemExit(1) from backup_error

        logger.error("%s，已备份至 %s%s", message, backup_file, f": {error}" if error else "")
        return {}

    def _write(self, config: dict[str, Any]) -> None:
        """以原子方式写入配置文件，尽量降低损坏风险"""
        directory = os.path.dirname(self.config_file) or "."
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as file:
                json.dump(config, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
                temp_path = file.name
            os.replace(temp_path, self.config_file)
            temp_path = ""
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError as error:
                    logger.warning("清理临时配置文件失败: %s (%s)", temp_path, error)

    def read(self, key: str = "") -> list | dict | str | int | bool | None:
        """读取当前内存中的全部配置，或单个配置项"""
        try:
            return self.raw.get(key) if key else dict(self.raw)
        except (AttributeError, TypeError) as error:
            logger.error("读取配置失败: %s", error)
            return None

    def save(self, key: str, value: list[Any] | dict[str, Any] | str | int | bool = "") -> None:
        """保存配置项，并同步运行时属性"""
        if key not in self.SCHEMA:
            logger.error("保存配置失败: 不允许新增未知配置项 %s", key)
            return

        try:
            normalized = self._normalize({**self.raw, key: value})
            self._write(normalized)
            self.raw = normalized
            self._apply()
        except (OSError, TypeError, ValueError) as error:
            logger.error("保存配置失败: %s=%r，原因：%s", key, value, error)
