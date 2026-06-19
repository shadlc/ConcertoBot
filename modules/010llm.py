"""LLM模块"""
import json
import traceback
from typing import Any, AsyncGenerator, Generator

import httpx

from src.base import Module
from src.utils import Utils


class LLM(Module):
    """LLM模块"""

    ID = "LLM"
    NAME = "LLM模块"
    HELP = {}  # 本模块目前主要为内部其他模块和功能提供LLM接入能力
    GLOBAL_CONFIG = {
        "providers": [
            {
                "name": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key": "",
                "max_retry": 2,
                "timeout": 30,
                "retry_interval": 10,
            }
        ],
        "models": [
            {
                "type": "chat",
                "model": "deepseek-chat",
                "name": "deepseek",
                "provider": "DeepSeek",
            }
        ],
        "system_prompt": None,
    }
    CONV_CONFIG = None
    PERSISTENT = True
    HANDLE_MESSAGE = False

    def __init__(self, event, auth=0):
        """初始化 LLM 配置并输出已启用的模型能力"""
        super().__init__(event, auth)
        if self.is_persisted():
            return

        self.stream_end = object()
        for model_type in ("chat", "stt", "tts"):
            model_map = self.build_model_map(model_type)
            if not model_map:
                continue
            models = ", ".join(
                f"{model['name']}({model['model']})"
                for model in model_map.values()
            )
            self.printf(f"已启用 [{model_type}] 模型 [{models}]")

    def build_model_map(self, model_type: str = "chat") -> dict[str, dict[str, Any]]:
        """构建模型名称到配置的映射"""
        model_map: dict[str, dict[str, Any]] = {}
        for model in self.config["models"]:
            if model.get("type") != model_type:
                continue
            provider = next(
                (p for p in self.config["providers"] if p["name"] == model["provider"]),
                None,
            )
            if provider:
                provider_info = {
                    "max_retry": provider.get("max_retry", 2),
                    "timeout": provider.get("timeout", 30),
                    "retry_interval": provider.get("retry_interval", 3),
                    **provider,
                }
                model_map[model["name"]] = model | provider_info
        return model_map

    def get_request_params(
        self, model_name: str | None = None, model_type: str = "chat"
    ) -> dict[str, Any]:
        """获取请求参数"""
        model_map = self.build_model_map(model_type)
        if not model_map:
            raise ValueError("未找到任何可用模型!")
        if model_name:
            if model_name not in model_map:
                raise ValueError(f"未找到模型[{model_name}]对应的配置!")
            return model_map[model_name]
        return next(iter(model_map.values()))

    def build_headers(self, api_key: str, stream: bool) -> dict[str, str]:
        """构建请求头"""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream" if stream else "application/json",
        }

    def build_payload(
        self, messages: list[dict[str, Any]], model: str, stream: bool
    ) -> dict[str, Any]:
        """构建请求负载"""
        return {"model": model, "messages": messages, "stream": stream}

    def normalize_messages(
        self,
        msg: str | list[dict[str, Any]],
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        """兼容纯文本与完整消息列表两种输入"""
        if isinstance(msg, list):
            messages: list[dict[str, Any]] = msg
        else:
            messages = [{"role": "user", "content": msg}]
        if system_prompt is None:
            system_prompt = self.config["system_prompt"]
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}, *messages]
        return messages

    def build_chat_request(
        self, messages: list[dict[str, Any]], params: dict[str, Any], stream: bool
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """构建聊天请求上下文"""
        return (
            f"{params['base_url']}/chat/completions",
            self.build_headers(params["api_key"], stream),
            self.build_payload(messages, params["model"], stream),
        )

    def extract_chat_content(self, data: dict[str, Any]) -> str:
        """提取非流式响应中的文本内容"""
        return data["choices"][0]["message"]["content"]

    def parse_event(self, data: str) -> object | str | None:
        """解析单个 SSE 事件"""
        if data == "[DONE]":
            return self.stream_end
        try:
            item = json.loads(data)
            return item["choices"][0]["delta"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            return None

    def iter_stream_lines(self, lines: Generator[str, None, None]) -> Generator[str, None, None]:
        """提取同步 SSE 文本片段"""
        for line in lines:
            if not line.startswith("data: "):
                continue
            content = self.parse_event(line[6:].strip())
            if content is self.stream_end:
                return
            if content:
                yield content

    async def aiter_stream_lines(
        self, lines: AsyncGenerator[str, None]
    ) -> AsyncGenerator[str, None]:
        """提取异步 SSE 文本片段"""
        async for line in lines:
            if not line.startswith("data: "):
                continue
            content = self.parse_event(line[6:].strip())
            if content is self.stream_end:
                return
            if content:
                yield content

    def sync_chat(
        self,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        stream: bool = False,
    ) -> str | Generator[str, None, None]:
        """同步API请求核心逻辑"""
        url, headers, payload = self.build_chat_request(messages, params, stream)
        if not stream:
            response = httpx.post(
                url, headers=headers, json=payload, timeout=params["timeout"]
            )
            response.raise_for_status()
            return self.extract_chat_content(response.json())

        def generator():
            """逐块读取同步流式响应内容"""
            with httpx.stream(
                "POST", url, headers=headers, json=payload, timeout=params["timeout"]
            ) as response:
                response.raise_for_status()
                yield from self.iter_stream_lines(response.iter_lines())

        return generator()

    async def async_chat(
        self,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        stream: bool = False,
    ) -> str | AsyncGenerator[str, None]:
        """异步API请求核心逻辑"""
        url, headers, payload = self.build_chat_request(messages, params, stream)
        if not stream:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, headers=headers, json=payload, timeout=params["timeout"]
                )
                response.raise_for_status()
                return self.extract_chat_content(response.json())

        async def generator():
            """逐块读取异步流式响应内容"""
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=payload,
                    timeout=params["timeout"],
                ) as response:
                    response.raise_for_status()
                    async for content in self.aiter_stream_lines(response.aiter_lines()):
                        yield content

        return generator()

    @Utils.export_func
    def llm_chat(
        self,
        msg: str | list[dict[str, Any]],
        model_name: str = None,
        stream: bool = False,
        system_prompt: str | None = None,
    ) -> str | Generator[str, None, None]:
        """同步生成文本"""
        try:
            messages = self.normalize_messages(msg, system_prompt)
            params = self.get_request_params(model_name)
            self.printf(f"调用chat模型 {params['model']}", level="DEBUG")
            return self.sync_chat(messages, params, stream)
        except Exception:  # pylint: disable=broad-exception-caught
            self.errorf(f"LLM请求失败:\n{traceback.format_exc()}")
            return ""

    @Utils.export_func
    async def async_llm_chat(
        self,
        msg: str | list[dict[str, Any]],
        model_name: str = None,
        stream: bool = False,
        system_prompt: str | None = None,
    ) -> str | AsyncGenerator[str, None]:
        """异步生成文本"""
        try:
            messages = self.normalize_messages(msg, system_prompt)
            params = self.get_request_params(model_name)
            self.printf(f"调用chat模型 {params['model']}", level="DEBUG")
            return await self.async_chat(messages, params, stream)
        except Exception:  # pylint: disable=broad-exception-caught
            self.errorf(f"LLM请求失败:\n{traceback.format_exc()}")
            return ""

    @Utils.export_func
    def llm_stt(self, file: dict, model_name: str = None) -> str:
        """同步语音转文本"""
        try:
            params = self.get_request_params(model_name, "stt")
            url = f"{params['base_url']}/audio/transcriptions"
            headers = {"Authorization": f"Bearer {params['api_key']}"}
            payload = {"model": params["model"]}
            self.printf(f"调用stt模型 {params['model']}", level="DEBUG")
            response = httpx.post(
                url,
                data=payload,
                files=file,
                headers=headers,
                timeout=params["timeout"],
            )
            data = response.json()
            return data.get("text") or data.get("message")
        except Exception:  # pylint: disable=broad-exception-caught
            self.errorf(f"LLM请求失败:\n{traceback.format_exc()}")
            return ""

    @Utils.export_func
    def llm_tts(self, text: str, model_name: str = None) -> bytes | str:
        """同步文本转语音"""
        try:
            params = self.get_request_params(model_name, "tts")
            url = f"{params['base_url']}/audio/speech"
            headers = {"Authorization": f"Bearer {params['api_key']}"}
            payload = {
                "model": params["model"],
                "input": text,
                "response_format": "mp3",
                "voice": params["voice"],
            }
            self.printf(f"调用tts模型 {payload}", level="DEBUG")
            response = httpx.post(
                url, json=payload, headers=headers, timeout=params["timeout"]
            )
            if response.status_code == 200:
                return response.content
            if response.text.startswith("{"):
                return response.json()["message"]
            return response.text
        except Exception:  # pylint: disable=broad-exception-caught
            self.errorf(f"LLM请求失败:\n{traceback.format_exc()}")
            return ""
