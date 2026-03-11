"""
Claude API 请求模拟客户端
通过 curl_cffi 模拟 Anthropic SDK 的 TLS 指纹 + HTTP Headers

策略：
  - TLS 层：使用 curl_cffi impersonate="chrome" 产生浏览器级 JA3/JA4
  - Header 层：精确复制 Anthropic Python SDK 0.84.0 的 header 格式和顺序
  - H2 层：curl_cffi chrome 模式自带 H2 指纹

用法：
  from claude_client import ClaudeClient
  client = ClaudeClient(api_key="sk-ant-xxx", base_url="https://your-anyrouter-url")
  resp = client.messages_create(model="claude-sonnet-4-20250514", ...)
"""

import json
import sys
import time
import uuid
from typing import Any, Generator

from curl_cffi import requests as cf_requests


# Anthropic SDK 0.84.0 的默认 header 模板
_SDK_VERSION = "0.84.0"
_ANTHROPIC_VERSION = "2023-06-01"

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": f"Anthropic/Python {_SDK_VERSION}",
    "X-Stainless-Lang": "python",
    "X-Stainless-Package-Version": _SDK_VERSION,
    "X-Stainless-OS": "MacOS",
    "X-Stainless-Arch": "arm64",
    "X-Stainless-Runtime": "CPython",
    "X-Stainless-Runtime-Version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    "X-Stainless-Async": "false",
    "anthropic-version": _ANTHROPIC_VERSION,
}


class ClaudeClient:
    """模拟 Anthropic Python SDK 请求特征的 HTTP 客户端"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        impersonate: str = "chrome",
        timeout: int = 120,
        max_retries: int = 2,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.impersonate = impersonate
        self.timeout = timeout
        self.max_retries = max_retries

        self._session = cf_requests.Session(impersonate=self.impersonate)

    def _build_headers(self, extra_headers: dict | None = None) -> dict:
        headers = {
            **_DEFAULT_HEADERS,
            "x-api-key": self.api_key,
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        json_data: dict | None = None,
        stream: bool = False,
        extra_headers: dict | None = None,
    ) -> cf_requests.Response:
        url = f"{self.base_url}{path}"
        headers = self._build_headers(extra_headers)

        if stream:
            headers["Accept"] = "text/event-stream"

        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json_data,
                    timeout=self.timeout,
                    stream=stream,
                )
                return resp
            except Exception as e:
                last_exc = e
                if attempt < self.max_retries:
                    wait = min(0.5 * (2 ** attempt), 8.0)
                    time.sleep(wait)

        raise last_exc

    def messages_create(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = 1024,
        stream: bool = False,
        system: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        **kwargs,
    ) -> dict | Generator:
        """调用 /v1/messages 接口"""
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            **kwargs,
        }
        if system is not None:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        if top_p is not None:
            body["top_p"] = top_p
        if stream:
            body["stream"] = True

        resp = self._request("POST", "/v1/messages", json_data=body, stream=stream)

        if stream:
            return self._iter_sse(resp)

        resp.raise_for_status()
        return resp.json()

    def _iter_sse(self, resp: cf_requests.Response) -> Generator[dict, None, None]:
        """解析 SSE 流"""
        buffer = ""
        for chunk in resp.iter_content():
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            buffer += chunk
            while "\n\n" in buffer:
                event_str, buffer = buffer.split("\n\n", 1)
                lines = event_str.strip().split("\n")
                event_type = None
                data = None
                for line in lines:
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data = line[6:]
                if data and data != "[DONE]":
                    try:
                        parsed = json.loads(data)
                        yield {"event": event_type, "data": parsed}
                    except json.JSONDecodeError:
                        pass

    def verify_fingerprint(self) -> dict:
        """验证当前客户端的 TLS 指纹"""
        resp = self._session.get(
            "https://tls.peet.ws/api/all",
            impersonate=self.impersonate,
            timeout=15,
        )
        data = resp.json()
        return {
            "ja3_hash": data.get("tls", {}).get("ja3_hash"),
            "ja4": data.get("tls", {}).get("ja4"),
            "h2_fingerprint": data.get("http2", {}).get("akamai_fingerprint_hash"),
            "http_version": data.get("http_version"),
            "user_agent": data.get("user_agent"),
        }

    def close(self):
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ---- 快捷用法 ----

def create_client(
    api_key: str,
    base_url: str = "https://api.anthropic.com",
    impersonate: str = "chrome",
) -> ClaudeClient:
    return ClaudeClient(api_key=api_key, base_url=base_url, impersonate=impersonate)


if __name__ == "__main__":
    # 指纹验证模式
    print("验证 TLS 指纹...\n")
    client = ClaudeClient(api_key="test", base_url="https://api.anthropic.com")
    fp = client.verify_fingerprint()
    print("当前客户端指纹:")
    for k, v in fp.items():
        print(f"  {k}: {v}")
    client.close()
