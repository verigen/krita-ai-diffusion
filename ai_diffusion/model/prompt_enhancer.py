from __future__ import annotations

import asyncio
from enum import Enum

from PyQt5.QtCore import QObject, pyqtSignal

from .. import eventloop, util
from ..backend.llm_client import LlmClient, LlmModel, LlmRequest, create_llm_client
from ..backend.network import NetworkError
from ..prompt_enhancer import EnhancerPreset, EnhancerPresets
from ..settings import settings
from .connection import Connection


class EnhancerState(Enum):
    idle = 0
    running = 1
    error = 2


class PromptEnhancer(QObject):
    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    state_changed = pyqtSignal(EnhancerState)

    def __init__(self, presets: EnhancerPresets, connection: Connection):
        super().__init__()
        self._presets = presets
        self._connection = connection
        self._client: LlmClient | None = None
        self._task: asyncio.Task | None = None
        self._state = EnhancerState.idle
        settings.changed.connect(self._handle_settings_changed)

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value: EnhancerState):
        if self._state != value:
            self._state = value
            self.state_changed.emit(value)

    @property
    def is_configured(self) -> bool:
        return settings.enhancer_enabled and bool(settings.enhancer_model)

    def enhance(self, text: str, preset: EnhancerPreset):
        self.cancel()
        request = LlmRequest(
            system_prompt=preset.system_prompt,
            user_prompt=text,
            model=preset.model or settings.enhancer_model,
            temperature=preset.temperature,
            keep_alive=settings.enhancer_ollama_keep_alive or None,
        )
        self.state = EnhancerState.running
        self._task = eventloop.run(self._enhance(request))

    def cancel(self):
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self.state = EnhancerState.idle

    async def list_models(self) -> list[LlmModel]:
        return await self._ensure_client().list_models()

    async def _enhance(self, request: LlmRequest):
        task = asyncio.current_task()
        try:
            if settings.enhancer_free_comfy_vram:
                if client := self._connection.client_if_connected:
                    await client.free_memory(timeout=settings.enhancer_free_comfy_vram_timeout)
            result = await self._ensure_client().chat(request, timeout=settings.enhancer_timeout)
        except asyncio.CancelledError:
            if self._task is task:
                self.state = EnhancerState.idle
            return
        except NetworkError as e:
            if self._task is task:
                self.state = EnhancerState.error
                self.error_occurred.emit(e.message)
            return
        except Exception as e:
            if self._task is task:
                self.state = EnhancerState.error
                self.error_occurred.emit(util.log_error(e))
            return
        if self._task is task:
            self.state = EnhancerState.idle
            self.result_ready.emit(result)

    def _ensure_client(self) -> LlmClient:
        if self._client is None:
            self._client = create_llm_client(
                settings.enhancer_backend, settings.enhancer_url, settings.enhancer_api_key
            )
        return self._client

    def _handle_settings_changed(self, key: str, value: object):
        if key in ("enhancer_backend", "enhancer_url", "enhancer_api_key"):
            self._client = None
