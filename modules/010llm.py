"""LLM模块"""

import json
import traceback
from typing import Dict, List, AsyncGenerator, Generator, Union

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
    AUTO_INIT = True
    HANDLE_MESSAGE = False

    def __init__(self, event, auth=0):
        """初始化 LLM 配置并输出已启用的模型能力"""
        super().__init__(event, auth)
        if self.is_persisted():
            return
        for model_type in ("chat", "stt", "tts"):
            model_map = self.build_model_map(model_type)
            if not model_map:
                continue
            models = ", ".join(
                f"{model['name']}({model['model']})"
                for model in model_map.values()
            )
            self.printf(f"已启用 [{model_type}] 模型 [{models}]")

    def build_model_map(self, model_type: str = "chat") -> Dict[str, Dict]:
        """构建模型名称到配置的映射"""
        model_map = {}
        for model in self.config["models"]:
            if model.get("type") != model_type:
                continue
            provider = next(
                (p for p in self.config["providers"] if p["name"] == model["provider"]),
                None,
            )
            if provider:
                model_map[model["name"]] = model.copy()
                provider["max_retry"] = provider.get("max_retry", 2)
                provider["timeout"] = provider.get("timeout", 30)
                provider["retry_interval"] = provider.get("retry_interval", 3)
                model_map[model["name"]] |= provider
        return model_map

    def get_request_params(
        self, model_name: str | None = None, model_type: str = "chat"
    ) -> Dict:
        """获取请求参数"""
        model_map = self.build_model_map(model_type)
        if len(model_map) == 0:
            raise ValueError("未找到任何可用模型!")
        if model_name:
            if model_name not in model_map:
                raise ValueError(f"未找到模型[{model_name}]对应的配置!")
            model_info = model_map[model_name]
        else:
            model_info = next(iter(model_map.values()))
        return model_info

    def build_headers(self, api_key: str, stream: bool) -> Dict[str, str]:
        """构建请求头"""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream" if stream else "application/json",
        }

    def build_payload(self, messages: List[Dict], model: str, stream: bool) -> Dict:
        """构建请求负载"""
        return {"model": model, "messages": messages, "stream": stream}

    def parse_event(self, data: str) -> str:
        """解析单个 SSE 事件"""
        if data == "[DONE]":
            return None
        try:
            item = json.loads(data)
            return item["choices"][0]["delta"]["content"]
        except json.JSONDecodeError:
            return None

    def sync_chat(
        self, messages: List[Dict], params: Dict, stream: bool = False
    ) -> Union[Dict, Generator]:
        """同步API请求核心逻辑"""
        headers = self.build_headers(params["api_key"], stream)
        payload = self.build_payload(messages, params["model"], stream)
        url = f"{params['base_url']}/chat/completions"
        if not stream:
            response = httpx.post(
                url, headers=headers, json=payload, timeout=params["timeout"]
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

        def generator():
            """逐块读取同步流式响应内容"""
            with httpx.stream(
                "POST", url, headers=headers, json=payload, timeout=params["timeout"]
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        content = self.parse_event(line[6:].strip())
                        if content is None:
                            return
                        yield content

        return generator()

    async def async_chat(
        self, messages: List[Dict], params: Dict, stream: bool = False
    ) -> Union[Dict, AsyncGenerator]:
        """异步API请求核心逻辑"""
        headers = self.build_headers(params["api_key"], stream)
        payload = self.build_payload(messages, params["model"], stream)
        url = f"{params['base_url']}/chat/completions"
        if not stream:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, headers=headers, json=payload, timeout=params["timeout"]
                )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

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
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            content = self.parse_event(line[6:].strip())
                            if content is None:
                                return
                            yield content

        return generator()

    @Utils.export_func
    def llm_chat(
        self, msg: str, model_name: str = None, stream: bool = False
    ) -> Union[str, Generator]:
        """同步生成文本"""
        try:
            messages: List[Dict] = [{"role": "user", "content": msg}]
            if system_prompt := self.config["system_prompt"]:
                messages = [{"role": "system", "content": system_prompt}, *messages]
            params = self.get_request_params(model_name)
            self.printf(f"调用chat模型 {params['model']}")
            return self.sync_chat(messages, params, stream)
        except Exception:  # pylint: disable=broad-exception-caught
            self.errorf(f"LLM请求失败:\n{traceback.format_exc()}")
            return ""

    @Utils.export_func
    async def async_llm_chat(
        self, msg: str, model_name: str = None, stream: bool = False
    ) -> Union[str, AsyncGenerator]:
        """异步生成文本"""
        try:
            messages: List[Dict] = [{"role": "user", "content": msg}]
            if system_prompt := self.config["system_prompt"]:
                messages = [{"role": "system", "content": system_prompt}, *messages]
            params = self.get_request_params(model_name)
            self.printf(f"调用chat模型 {params['model']}")
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
            self.printf(f"调用stt模型 {params['model']}")
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
            self.printf(f"调用tts模型 {payload}")
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
