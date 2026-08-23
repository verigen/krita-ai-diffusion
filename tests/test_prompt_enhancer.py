import json
from pathlib import Path

from PyQt5.QtCore import QModelIndex, Qt

from ai_diffusion.backend.llm_client import LlmClient
from ai_diffusion.backend.network import NetworkError
from ai_diffusion.model.connection import Connection
from ai_diffusion.model.prompt_enhancer import EnhancerState, PromptEnhancer
from ai_diffusion.prompt_enhancer import EnhancerPreset, EnhancerPresets, builtin_presets
from ai_diffusion.settings import settings
from ai_diffusion.util import ensure

from .conftest import qtapp
from .mock.client import MockClient


class EventHandler:
    def __init__(self, presets: EnhancerPresets):
        self.begin_insert = []
        self.end_insert = []
        self.begin_remove = []
        self.end_remove = []
        self.data_changed = []
        self.connect(presets)

    def on_begin_insert(self, parent, start, end):
        self.begin_insert.append((parent, start, end))

    def on_end_insert(self):
        self.end_insert.append(())

    def on_begin_remove(self, parent, start, end):
        self.begin_remove.append((parent, start, end))

    def on_end_remove(self):
        self.end_remove.append(())

    def on_data_changed(self, start, end):
        self.data_changed.append((start, end))

    def connect(self, presets: EnhancerPresets):
        presets.rowsAboutToBeInserted.connect(self.on_begin_insert)
        presets.rowsInserted.connect(self.on_end_insert)
        presets.rowsAboutToBeRemoved.connect(self.on_begin_remove)
        presets.rowsRemoved.connect(self.on_end_remove)
        presets.dataChanged.connect(self.on_data_changed)


def empty_db(path: Path):
    path.write_text(json.dumps({"version": 1, "default": "", "presets": []}))
    return path


def test_create_and_roles(tmp_path: Path):
    presets = EnhancerPresets(empty_db(tmp_path / "presets.json"))
    preset = presets.create("Detailed", "expand the prompt", model="llama3", temperature=0.8)

    assert presets.data(presets.index(0), Qt.ItemDataRole.DisplayRole) == "Detailed"
    assert presets.data(presets.index(0), Qt.ItemDataRole.UserRole) == preset.id
    assert presets.data(presets.index(0), EnhancerPresets.system_prompt_role) == "expand the prompt"
    assert presets.data(presets.index(0), EnhancerPresets.model_role) == "llama3"
    assert presets.data(presets.index(0), EnhancerPresets.temperature_role) == 0.8
    assert presets.data(presets.index(0), EnhancerPresets.is_default_role) is True


def test_add_remove(tmp_path: Path):
    presets = EnhancerPresets(empty_db(tmp_path / "presets.json"))
    events = EventHandler(presets)

    a = presets.create("A")
    b = presets.create("B")

    assert len(presets) == 2
    assert events.begin_insert == [(QModelIndex(), 0, 0), (QModelIndex(), 1, 1)]
    assert len(events.end_insert) == 2

    presets.remove(a.id)

    assert len(presets) == 1
    assert presets[0].id == b.id
    assert events.begin_remove == [(QModelIndex(), 0, 0)]
    assert len(events.end_remove) == 1


def test_update(tmp_path: Path):
    presets = EnhancerPresets(empty_db(tmp_path / "presets.json"))
    preset = presets.create("A")
    events = EventHandler(presets)

    presets.update(preset.id, name="Renamed", system_prompt="new prompt")

    assert preset.name == "Renamed"
    assert preset.system_prompt == "new prompt"
    assert events.data_changed == [(presets.index(0), presets.index(0))]


def test_duplicate(tmp_path: Path):
    presets = EnhancerPresets(empty_db(tmp_path / "presets.json"))
    original = presets.create("A", "prompt a", model="llama3", temperature=0.5)

    copy = presets.duplicate(original.id)

    assert copy is not None
    assert copy.id != original.id
    assert copy.name != original.name
    assert "copy" in copy.name.lower()
    assert copy.system_prompt == original.system_prompt
    assert copy.model == original.model
    assert copy.temperature == original.temperature
    assert len(presets) == 2


def test_from_dict_tolerates_missing_keys():
    preset = EnhancerPreset.from_dict({"id": "abc", "name": "n"})
    assert preset.id == "abc"
    assert preset.name == "n"
    assert preset.system_prompt == ""
    assert preset.model == ""
    assert preset.temperature is None
    assert preset.created == 0.0


# --- default handling -----------------------------------------------------


def test_first_preset_is_default(tmp_path: Path):
    presets = EnhancerPresets(empty_db(tmp_path / "presets.json"))
    assert presets.default == ""

    a = presets.create("A")
    assert presets.default == a.id

    presets.create("B")
    assert presets.default == a.id  # unchanged, only the first preset becomes default


def test_set_default(tmp_path: Path):
    presets = EnhancerPresets(empty_db(tmp_path / "presets.json"))
    presets.create("A")
    b = presets.create("B")

    presets.set_default(b.id)

    assert presets.default == b.id
    assert presets.data(presets.index(0), EnhancerPresets.is_default_role) is False
    assert presets.data(presets.index(1), EnhancerPresets.is_default_role) is True


def test_deleting_default_repoints(tmp_path: Path):
    presets = EnhancerPresets(empty_db(tmp_path / "presets.json"))
    a = presets.create("A")
    b = presets.create("B")
    assert presets.default == a.id

    presets.remove(a.id)

    assert presets.default == b.id


def test_deleting_last_preset_reseeds_builtins(tmp_path: Path):
    presets = EnhancerPresets(empty_db(tmp_path / "presets.json"))
    a = presets.create("A")

    presets.remove(a.id)

    assert len(presets) == len(builtin_presets())
    assert presets.default == presets[0].id


# --- persistence -----------------------------------------------------------


def test_serialization_roundtrip(tmp_path: Path):
    path = empty_db(tmp_path / "presets.json")
    presets = EnhancerPresets(path)
    preset = presets.create("A", "prompt a", model="llama3", temperature=None)
    presets.create("B")
    presets.set_default(preset.id)

    presets2 = EnhancerPresets(path)
    assert len(presets2) == 2
    loaded = ensure(presets2.find(preset.id))
    assert loaded.name == preset.name
    assert loaded.system_prompt == preset.system_prompt
    assert loaded.model == preset.model
    assert loaded.temperature is None
    assert presets2.default == preset.id


def test_bare_list_json_accepted(tmp_path: Path):
    path = tmp_path / "presets.json"
    path.write_text(
        json.dumps([
            {"id": "1", "name": "First"},
            {"id": "2", "name": "Second"},
        ])
    )
    presets = EnhancerPresets(path)

    assert len(presets) == 2
    assert presets.default == "1"


def test_malformed_json_leaves_empty_model(tmp_path: Path):
    path = tmp_path / "presets.json"
    path.write_text("{not valid json")
    presets = EnhancerPresets(path)

    assert len(presets) == 0
    assert presets.default == ""


def test_fresh_path_seeds_builtins(tmp_path: Path):
    path = tmp_path / "presets.json"
    assert not path.exists()

    presets = EnhancerPresets(path)

    assert len(presets) == len(builtin_presets())
    assert presets.default == presets[0].id
    assert path.exists()
    data = json.loads(path.read_text())
    assert len(data["presets"]) == len(builtin_presets())
    assert data["default"] == presets[0].id


# --- PromptEnhancer orchestration ------------------------------------------


class StubLlmClient(LlmClient):
    def __init__(self):
        self.requests: list = []
        self.responses: list = []

    async def chat(self, request, timeout=120):
        self.requests.append(request)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def list_models(self, timeout=15):
        return []


def make_enhancer(tmp_path: Path):
    presets = EnhancerPresets(empty_db(tmp_path / "presets.json"))
    enhancer = PromptEnhancer(presets, Connection())
    stub = StubLlmClient()
    enhancer._client = stub
    return enhancer, stub


@qtapp
async def test_enhance_happy_path(tmp_path: Path):
    enhancer, stub = make_enhancer(tmp_path)
    stub.responses.append("a fluffy cat")
    results = []
    enhancer.result_ready.connect(results.append)

    preset = EnhancerPreset(id="p1", name="Detailed", system_prompt="expand")
    enhancer.enhance("a cat", preset)
    assert enhancer._task is not None
    await enhancer._task

    assert results == ["a fluffy cat"]
    assert enhancer.state == EnhancerState.idle


@qtapp
async def test_enhance_error_path(tmp_path: Path):
    enhancer, stub = make_enhancer(tmp_path)
    stub.responses.append(NetworkError(1, "boom", "http://x"))
    errors = []
    enhancer.error_occurred.connect(errors.append)

    preset = EnhancerPreset(id="p1", name="Detailed", system_prompt="expand")
    enhancer.enhance("a cat", preset)
    assert enhancer._task is not None
    await enhancer._task

    assert errors == ["boom"]
    assert enhancer.state == EnhancerState.error


@qtapp
async def test_second_enhance_supersedes_first(tmp_path: Path):
    enhancer, stub = make_enhancer(tmp_path)
    stub.responses.append("second result")
    results = []
    enhancer.result_ready.connect(results.append)

    preset = EnhancerPreset(id="p1", name="Detailed", system_prompt="expand")
    enhancer.enhance("a cat", preset)
    first_task = enhancer._task
    enhancer.enhance("a dog", preset)
    second_task = enhancer._task

    assert first_task is not second_task
    assert second_task is not None
    await second_task

    assert results == ["second result"]
    assert len(stub.requests) == 1
    assert stub.requests[0].user_prompt == "a dog"
    assert enhancer.state == EnhancerState.idle


def test_cancel_returns_to_idle(tmp_path: Path):
    enhancer, _stub = make_enhancer(tmp_path)
    enhancer.state = EnhancerState.running
    enhancer.cancel()
    assert enhancer.state == EnhancerState.idle


@qtapp
async def test_preset_model_wins_over_settings(tmp_path: Path):
    enhancer, stub = make_enhancer(tmp_path)
    stub.responses.append("result")
    old_model = settings.enhancer_model
    settings.enhancer_model = "settings-model"
    try:
        preset = EnhancerPreset(
            id="p1", name="Detailed", system_prompt="expand", model="preset-model"
        )
        enhancer.enhance("a cat", preset)
        assert enhancer._task is not None
        await enhancer._task
    finally:
        settings.enhancer_model = old_model

    assert stub.requests[0].model == "preset-model"


@qtapp
async def test_settings_model_used_when_preset_has_none(tmp_path: Path):
    enhancer, stub = make_enhancer(tmp_path)
    stub.responses.append("result")
    old_model = settings.enhancer_model
    settings.enhancer_model = "settings-model"
    try:
        preset = EnhancerPreset(id="p1", name="Detailed", system_prompt="expand", model="")
        enhancer.enhance("a cat", preset)
        assert enhancer._task is not None
        await enhancer._task
    finally:
        settings.enhancer_model = old_model

    assert stub.requests[0].model == "settings-model"


@qtapp
async def test_temperature_none_omitted_from_request(tmp_path: Path):
    enhancer, stub = make_enhancer(tmp_path)
    stub.responses.append("result")

    preset = EnhancerPreset(id="p1", name="Detailed", system_prompt="expand", temperature=None)
    enhancer.enhance("a cat", preset)
    assert enhancer._task is not None
    await enhancer._task

    assert stub.requests[0].temperature is None


def test_client_dropped_on_backend_settings_change(tmp_path: Path):
    enhancer, stub = make_enhancer(tmp_path)
    assert enhancer._client is stub

    old_url = settings.enhancer_url
    settings.enhancer_url = "http://example.com:1234"
    try:
        assert enhancer._client is None
    finally:
        settings.enhancer_url = old_url


class TrackingComfyClient(MockClient):
    def __init__(self):
        super().__init__()
        self.free_memory_calls: list[float] = []

    async def free_memory(self, timeout: float = 10):
        self.free_memory_calls.append(timeout)


@qtapp
async def test_frees_comfy_vram_before_enhancing_when_enabled(tmp_path: Path):
    enhancer, stub = make_enhancer(tmp_path)
    stub.responses.append("result")
    comfy_stub = TrackingComfyClient()
    enhancer._connection._client = comfy_stub

    old_enabled = settings.enhancer_free_comfy_vram
    old_timeout = settings.enhancer_free_comfy_vram_timeout
    settings.enhancer_free_comfy_vram = True
    settings.enhancer_free_comfy_vram_timeout = 7
    try:
        preset = EnhancerPreset(id="p1", name="Detailed", system_prompt="expand")
        enhancer.enhance("a cat", preset)
        assert enhancer._task is not None
        await enhancer._task
    finally:
        settings.enhancer_free_comfy_vram = old_enabled
        settings.enhancer_free_comfy_vram_timeout = old_timeout

    assert comfy_stub.free_memory_calls == [7]


@qtapp
async def test_does_not_free_comfy_vram_when_disabled(tmp_path: Path):
    enhancer, stub = make_enhancer(tmp_path)
    stub.responses.append("result")
    comfy_stub = TrackingComfyClient()
    enhancer._connection._client = comfy_stub
    assert settings.enhancer_free_comfy_vram is False

    preset = EnhancerPreset(id="p1", name="Detailed", system_prompt="expand")
    enhancer.enhance("a cat", preset)
    assert enhancer._task is not None
    await enhancer._task

    assert comfy_stub.free_memory_calls == []


@qtapp
async def test_keep_alive_forwarded_to_request(tmp_path: Path):
    enhancer, stub = make_enhancer(tmp_path)
    stub.responses.append("result")
    old = settings.enhancer_ollama_keep_alive
    settings.enhancer_ollama_keep_alive = "0"
    try:
        preset = EnhancerPreset(id="p1", name="Detailed", system_prompt="expand")
        enhancer.enhance("a cat", preset)
        assert enhancer._task is not None
        await enhancer._task
    finally:
        settings.enhancer_ollama_keep_alive = old

    assert stub.requests[0].keep_alive == "0"
