"""煎蛋网模块"""

import asyncio
import json
import random
import re
import traceback
from typing import Any

import httpx

from src.base import MiniCron, Module
from src.utils import Utils


class Jiandan(Module):
    """煎蛋网模块"""

    ID = "Jiandan"
    NAME = "煎蛋网模块"
    HELP = {
        2: [
            "来张梗图 | 调用煎蛋网获取图片",
        ],
    }
    GLOBAL_CONFIG = {
        "batch_limit": 2,
        "llm_batch_limit": 5,
        "llm_model": None,
        "llm_prompt": (
            "你在帮群聊筛选适合发送的帖子，优先保留有明确笑点、梗感强、适合普通群聊的内容，排除低质截图、包含广告的内容。"
        ),
        "request_headers": {
            "Referer": "https://jandan.net/pic",
        },
    }
    CONV_CONFIG = {
        "hist": [],
        "cron": "",
        "probability": 1,
        "batch_limit": 2,
        "llm_batch_limit": 5,
        "llm_model": None,
        "llm_prompt": None,
    }
    PERSISTENT = True


    def __init__(self, event, auth=0):
        """初始化煎蛋模块并启动定时任务"""
        super().__init__(event, auth)
        if self.is_persisted():
            return
        self.setup_crons()

    def setup_crons(self) -> None:
        """初始化定时任务"""
        for owner, config in self.config.items():
            if not re.fullmatch(r"[ug]\d+", owner):
                continue
            if not isinstance(config, dict):
                continue
            prob = config.get("probability", 0)
            crontab = config.get("cron", "")
            if not crontab or prob == 0:
                continue
            cron = MiniCron(
                crontab,
                lambda o=owner, c=config: self.jiandan_msg_task(o, c),
                loop=self.robot.loop,
                name=f"{owner} 自动煎蛋网(概率{prob:.2%})",
            )
            self.add_cron(cron)

    def get_jiandan_config_value(
        self, config: dict[str, Any], key: str, default: Any = None
    ) -> Any:
        """按 会话配置 > 全局配置 > 默认值 顺序读取"""
        if key in config and config.get(key) is not None:
            return config.get(key)
        return self.config.get(key, default)

    def build_request_headers(self) -> dict[str, str]:
        """构建煎蛋请求头"""
        headers = self.config.get("request_headers", {})
        if not isinstance(headers, dict):
            return {}
        return {str(key): str(value) for key, value in headers.items()}

    def get_jiandan_limit(self, config: dict[str, Any], key: str, default: int) -> int:
        """读取发送数量上限并做兜底"""
        value = self.get_jiandan_config_value(config, key, default)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    def normalize_jiandan_image_url(self, url: str) -> str:
        """规范煎蛋图片地址"""
        url = (url or "").strip()
        if url.startswith("//"):
            url = f"https:{url}"
        return url.replace("/mw600/", "/large/").replace("/thumb180/", "/large/")

    def extract_jiandan_image_urls(self, content: str) -> list[str]:
        """从帖子内容中提取图片地址"""
        urls = re.findall(r"""<img\b[^>]*\bsrc="([^"]+)"[^>]*>""", content or "")
        return [self.normalize_jiandan_image_url(url) for url in urls]

    def render_jiandan_message(self, item: dict, *, include_sub_type: bool = False) -> str:
        """将煎蛋帖子转换成 CQ 消息"""
        msg = (item.get("content") or "").strip()
        img_flag = "sub_type=0," if include_sub_type else ""

        def replace_img(match: re.Match) -> str:
            url = self.normalize_jiandan_image_url(match.group(1))
            return f"[CQ:image,{img_flag}file={url}]" if url else ""

        msg = re.sub(r"""<img\b[^>]*\bsrc="([^"]+)"[^>]*>""", replace_img, msg)
        msg = re.sub(r"<br\s*/?>", "\n", msg)
        msg = re.sub(r"</p\s*>", "\n", msg)
        msg = re.sub(r"<[^>]+>", "", msg)
        return msg.strip()

    def remember_jiandan_items(self, config: dict[str, Any], items: list[dict]) -> None:
        """写入已发送历史，避免重复发送帖子"""
        hist = list(config.get("hist", []))
        hist.extend(item.get("id") for item in items if item.get("id") is not None)
        config["hist"] = hist[-30:]
        self.save_config()

    def send_jiandan_item(self, item: dict, owner_id: str | None = None) -> str:
        """逐条发送煎蛋帖子"""
        msg = self.render_jiandan_message(item, include_sub_type=bool(owner_id))
        if not msg:
            return ""
        if owner_id:
            Utils.reply_back(self.robot, owner_id, msg)
            if notify_maisaka := self.robot.func.get("notify_maisaka"):
                notify_maisaka(msg, owner_id[1:])
        else:
            self.reply(msg)
        return msg

    def parse_jiandan_llm_result(self, text: str) -> tuple[bool, str]:
        """解析 LLM 的筛选结果"""
        text = (text or "").strip()
        if not text:
            return False, "LLM无返回"
        if matched := re.search(r"\{.*\}", text, re.S):
            try:
                data = json.loads(matched.group(0))
                return bool(data.get("send")), str(data.get("reason", "")).strip()
            except json.JSONDecodeError:
                pass
        lowered = text.lower()
        if lowered in {"true", "yes", "pass", "ok"}:
            return True, ""
        if lowered in {"false", "no", "reject"}:
            return False, ""
        return False, text[:30]

    def build_jiandan_llm_messages(self, item: dict, prompt: str) -> list[dict]:
        """构建煎蛋图片的多模态筛选请求"""
        content = item.get("content", "")
        image_urls = self.extract_jiandan_image_urls(content)
        text = (
            f"{prompt}\n\n"
            "请只返回一行 JSON，不要输出额外说明："
            '{"send":true,"reason":"不超过20字"} 或 '
            '{"send":false,"reason":"不超过20字"}。\n\n'
            f"帖子ID: {item.get('id')}\n"
            f"正赞: {item.get('vote_positive', 0)}\n"
            f"负赞: {item.get('vote_negative', 0)}\n"
            f"原始内容: {content}\n"
            "请结合下方图片判断是否适合发送到普通群聊。"
        )
        message_content = [{"type": "text", "text": text}]
        for image_url in image_urls[:4]:
            message_content.append({"type": "image_url", "image_url": {"url": image_url}})
        return [{"role": "user", "content": message_content}]

    async def jiandan_llm_accept(self, item: dict, config: dict[str, Any]) -> bool | None:
        """使用 LLM 对初筛结果进行二次筛选"""
        prompt = self.get_jiandan_config_value(config, "llm_prompt", "")
        if not prompt:
            return False
        model_name = self.get_jiandan_config_value(config, "llm_model")
        messages = self.build_jiandan_llm_messages(item, prompt)
        result = ""
        try:
            if async_llm_chat := self.robot.func.get("async_llm_chat"):
                result = await async_llm_chat(messages, model_name=model_name)
        except Exception as err:  # pylint: disable=broad-exception-caught
            self.warnf(f"[AI筛选] 模型 {model_name or '默认模型'} 调用报错: {err}")
            return None
        if not (result or "").strip():
            self.warnf(f"[AI筛选] 模型 {model_name or '默认模型'} 无有效返回")
            return None
        passed, reason = self.parse_jiandan_llm_result(result)
        self.printf(
            f"[AI筛选] 帖子 {item.get('id')} => {'通过' if passed else '拒绝'}"
            + (f" ({reason})" if reason else "")
        )
        return passed

    async def filter_jiandan_items_with_llm(
        self, items: list[dict], config: dict[str, Any], limit: int
    ) -> list[dict]:
        """通过 LLM 从候选列表中筛选可发送帖子"""
        if not items or limit <= 0:
            return []
        if not self.robot.func.get("async_llm_chat"):
            return []
        selected = []
        for item in items:
            accepted = await self.jiandan_llm_accept(item, config)
            if accepted is None:
                return []
            if accepted:
                selected.append(item)
                if len(selected) >= limit:
                    break
        return selected

    def filter_jiandan_items_locally(self, items: list[dict], batch_limit: int) -> list[dict]:
        """按本地规则筛选并排序煎蛋帖子"""
        blocked_keywords = ("公众号", "推广", "广告", "扫码", "下载", "引流", "淘宝", "拼多多", "京东", "app")
        reviewed: list[tuple[int, dict[str, Any]]] = []
        for item in items:
            if len(reviewed) >= batch_limit:
                break
            content = item.get("content", "")
            text = re.sub(r"""<img\b[^>]*\bsrc="([^"]+)"[^>]*>""", " ", content)
            text = re.sub(r"<br\s*/?>", "\n", text)
            text = re.sub(r"</p\s*>", "\n", text)
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            image_urls = self.extract_jiandan_image_urls(content)
            vote_positive = int(item.get("vote_positive", 0) or 0)
            vote_negative = int(item.get("vote_negative", 0) or 0)
            vote_delta = vote_positive - vote_negative
            if not text and not image_urls:
                self.printf(f"[本地筛选] 帖子 {item.get('id')} => 拒绝 (空内容)")
                continue
            if any(keyword in text.lower() for keyword in blocked_keywords):
                self.printf(f"[本地筛选] 帖子 {item.get('id')} => 拒绝 (疑似广告)")
                continue
            if vote_positive <= vote_negative:
                self.printf(f"[本地筛选] 帖子 {item.get('id')} => 拒绝 (评分偏低)")
                continue
            if vote_negative >= 20:
                self.printf(f"[本地筛选] 帖子 {item.get('id')} => 拒绝 (负反馈过高)")
                continue
            if not image_urls and len(text) < 8:
                self.printf(f"[本地筛选] 帖子 {item.get('id')} => 拒绝 (信息量不足)")
                continue
            score = vote_delta
            if image_urls:
                score += min(len(image_urls), 4) * 3
            if 8 <= len(text) <= 80:
                score += 5
            if re.search(r"[。！？!?~～]", text):
                score += 2
            self.printf(f"[本地筛选] 帖子 {item.get('id')} => 通过 ({score}分)")
            reviewed.append((score, item))
        self.printf(f"[本地筛选] 精选 {len(reviewed)} 条帖子")
        reviewed.sort(key=lambda pair: pair[0], reverse=True)
        selected = [item for _, item in reviewed]
        return selected

    async def pick_jiandan_items(self, config: dict[str, Any]) -> list[dict]:
        """综合历史、本地规则和 LLM 选择本次要发送的帖子"""
        data_list = await self.get_jiandan()
        if not data_list:
            return []
        hist = set(config.get("hist", []))
        candidates = [item for item in data_list if item.get("id") not in hist]
        if not candidates:
            return []
        batch_limit = self.get_jiandan_limit(config, "batch_limit", 2)
        llm_batch_limit = max(batch_limit, self.get_jiandan_limit(config, "llm_batch_limit", 5))
        max_batch_limit = max(batch_limit, llm_batch_limit)
        candidates = self.filter_jiandan_items_locally(candidates, max_batch_limit*2)
        if not candidates:
            return []
        llm_candidates = candidates[: max(llm_batch_limit * 2, batch_limit)]
        llm_selected = await self.filter_jiandan_items_with_llm(llm_candidates, config, llm_batch_limit)
        if llm_selected:
            self.printf(f"[AI筛选] 通过 {len(llm_selected)} 张，使用扩展上限 {llm_batch_limit}")
            return llm_selected[:llm_batch_limit]
        selected = candidates[:batch_limit]
        self.printf(f"[本地筛选] 使用本地结果 {len(selected)} 张")
        return selected

    async def jiandan_msg_task(self, owner: str, config: dict[str, Any]) -> None:
        """自动煎蛋网"""
        ran_int = random.random()
        prob = config.get("probability", 0)
        if ran_int > prob:
            return self.printf(f"[{owner}]因概率未达而未发送({ran_int:.2}>{prob:.2})")
        data_list = await self.pick_jiandan_items(config)
        if not data_list:
            return self.printf(f"[{owner}]因无有效数据而未发送")
        self.remember_jiandan_items(config, data_list)
        for index, item in enumerate(data_list):
            self.send_jiandan_item(item, owner)
            if index < len(data_list) - 1:
                await asyncio.sleep(random.uniform(0.8, 1.6))
        self.printf(f"[{owner}]已发送 {len(data_list)} 张帖子")

    @Utils.handler(
        lambda self: self.au(2)
        and self.at_or_private()
        and self.match(r"^(来|发)(张|个)(无聊|屌|弔|吊|梗)图$")
    )
    def jiandan_msg(self):
        """获取煎蛋网"""
        if not self.is_private():
            Utils.set_emoji(self.robot, self.event.msg_id, 124)
        data_list = self.robot.sync(self.pick_jiandan_items(self.conv_config))
        if not data_list:
            return self.reply("未获取到新的评论")
        self.remember_jiandan_items(self.conv_config, data_list)
        for item in data_list:
            self.send_jiandan_item(item)

    async def get_jiandan(
        self, page=0, page_num=3, raise_error=False
    ) -> list[dict] | None:
        """获取煎蛋网候选列表"""
        page_str = f"第{page}页" if page else "最新一页"
        try:
            url = f"https://jandan.net/api/comment/post/26402?order=desc&page={page}"
            self.printf(f"获取煎蛋网[{page_str}]数据")
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url,
                    timeout=3,
                    headers=self.build_request_headers(),
                )
            resp.raise_for_status()
            data = resp.json().get("data", {}).get("list")
            current_page = resp.json().get("data", {}).get("current_page", 0)
            if not data:
                return []
            if page != 0:
                return data
            if page_num > 0:
                for i in range(1, page_num + 1):
                    data += await self.get_jiandan(current_page - i)
            result = []
            for item in data:
                # 先把多半差评的剔除掉
                if item["vote_positive"] + 1 > item["vote_negative"]:
                    result.append(item)
            result = sorted(result, key=lambda x: x["vote_positive"] - x["vote_negative"], reverse=True)
            self.printf(f"共请求到{len(result)}条有效的帖子")
            return result
        except httpx.ConnectTimeout as e:
            self.errorf(f"获取煎蛋网[{page_str}]时网络请求超时 {e}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(f"获取煎蛋网[{page_str}]失败\n{traceback.format_exc()}")
            if raise_error:
                raise e
            return []
