import json
import os
import re
from typing import Protocol

import requests

from config import (
    GGUF_FILENAME,
    GGUF_GPU_LAYERS,
    GGUF_N_CTX,
    GGUF_N_THREADS,
    GGUF_REPO,
)

_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


class LLMBackend(Protocol):
    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.7,
        schema: dict | None = None,
    ) -> str: ...


class LlamaCppBackend:
    """Runs a GGUF model via llama-cpp-python with CUDA acceleration.

    Downloads from HuggingFace Hub on first use (cached in ~/.cache/huggingface).
    Default: unsloth/Qwen3-8B-GGUF Q4_K_M
    """

    def __init__(
        self,
        repo_id: str = GGUF_REPO,
        filename: str = GGUF_FILENAME,
        n_ctx: int = GGUF_N_CTX,
        n_gpu_layers: int = GGUF_GPU_LAYERS,
        n_threads: int | None = GGUF_N_THREADS,
        verbose: bool = False,
    ):
        try:
            from llama_cpp import Llama
        except ImportError:
            raise RuntimeError(
                "llama-cpp-python is not installed. "
                "Install the wheel from: https://github.com/abetlen/llama-cpp-python/releases"
            )

        common = dict(n_ctx=n_ctx, n_gpu_layers=n_gpu_layers, verbose=verbose)
        if n_threads is not None:
            common["n_threads"] = n_threads

        # ocean_scorer sets TRANSFORMERS_OFFLINE=1 at import time, which huggingface_hub
        # bakes into a module-level constant. We must reset both the env vars AND that
        # constant before calling from_pretrained, then restore afterwards.
        import huggingface_hub.constants as _hf_const
        from huggingface_hub import hf_hub_download

        _offline_vars = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
        _saved_env = {k: os.environ.pop(k, None) for k in _offline_vars}
        _saved_flag = _hf_const.HF_HUB_OFFLINE
        _hf_const.HF_HUB_OFFLINE = False
        try:
            model_path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                repo_type="model",
            )
            self._llm = Llama(model_path=model_path, **common)
        finally:
            _hf_const.HF_HUB_OFFLINE = _saved_flag
            for k, v in _saved_env.items():
                if v is not None:
                    os.environ[k] = v

    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.7,
        schema: dict | None = None,
    ) -> str:
        # Qwen3 chat template — /no_think suppresses chain-of-thought
        prompt = (
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}\n/no_think<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        kwargs: dict = dict(
            max_tokens=max_tokens,
            temperature=temperature,
            stop=["<|im_end|>", "<|endoftext|>"],
            echo=False,
        )
        if schema is not None:
            kwargs["response_format"] = {"type": "json_object", "schema": schema}
        output = self._llm(prompt, **kwargs)
        text = output["choices"][0]["text"].strip()
        return _THINK_RE.sub("", text).strip()


class OllamaBackend:
    def __init__(
        self,
        model: str | None = None,
        url: str | None = None,
        timeout: int | None = None,
    ):
        self._model = model or os.environ.get("SIM_V2_OLLAMA_MODEL", "qwen3.5:9b")
        self._url = url or os.environ.get(
            "SIM_V2_OLLAMA_URL", "http://localhost:11434/api/chat"
        )
        self._timeout = timeout or int(os.environ.get("SIM_V2_OLLAMA_TIMEOUT", "120"))

    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.7,
        schema: dict | None = None,
    ) -> str:
        payload: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"{user}\n/no_think"},
            ],
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if schema is not None:
            payload["format"] = schema

        response = requests.post(self._url, json=payload, timeout=self._timeout)
        response.raise_for_status()
        data = response.json()
        text = (data.get("message") or {}).get("content", "")
        return _THINK_RE.sub("", text).strip()


class LlamaServerBackend:
    def __init__(
        self,
        model: str | None = None,
        url: str | None = None,
        timeout: int | None = None,
    ):
        self._model = model or os.environ.get("SIM_V2_LLAMA_SERVER_MODEL", "")
        self._url = url or os.environ.get(
            "SIM_V2_LLAMA_SERVER_URL", "http://127.0.0.1:8080/v1/chat/completions"
        )
        self._timeout = timeout or int(
            os.environ.get("SIM_V2_LLAMA_SERVER_TIMEOUT", "120")
        )

    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.7,
        schema: dict | None = None,
    ) -> str:
        payload: dict = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"{user}\n/no_think"},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._model:
            payload["model"] = self._model
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "sim_v2_schema",
                    "schema": schema,
                },
            }

        response = requests.post(self._url, json=payload, timeout=self._timeout)
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        text = message.get("content", "")
        if isinstance(text, list):
            text = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in text
            )
        if isinstance(text, dict):
            text = json.dumps(text)
        return _THINK_RE.sub("", str(text)).strip()


def create_backend(name: str | None = None) -> LLMBackend:
    backend = (name or os.environ.get("SIM_V2_LLM_BACKEND", "llama-cpp")).lower()
    if backend == "ollama":
        return OllamaBackend()
    if backend in {"llama-server", "server"}:
        return LlamaServerBackend()
    if backend in {"llama-cpp", "llamacpp", "cpp"}:
        return LlamaCppBackend()
    raise ValueError(f"Unknown LLM backend: {backend}")
