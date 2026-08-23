# Prompt Enhancer

> **Deliverable:** this document, written to `docs/agent/PROMPT_ENHANCER_PLAN.md`
> (alongside `PROMPT_LIBRARY_PLAN.md` / `PROMPT_EXPANSION_PLAN.md`), then the
> implementation described below.

## Context

Writing a good diffusion prompt is a skill: users type "a cat on a roof" and get a
flat result, while models respond much better to detailed, structured descriptions.
The plugin already helps users *store* and *recall* prompts (Prompt Library,
`docs/agent/PROMPT_LIBRARY_PLAN.md`; inline `/` expansion,
`docs/agent/PROMPT_EXPANSION_PLAN.md`) — but nothing helps them *write* one.

This feature adds a **Prompt Enhancer**: a button next to the positive prompt box
that sends the current prompt to a user-configured LLM together with a chosen
system prompt, and offers the rewritten prompt back for review. The LLM is the
user's own — an **Ollama-compatible** or **OpenAI-compatible** endpoint — so this
stays true to the project's "local, open, free" goal and adds no dependency on the
Interstice cloud service.

There is **no LLM integration anywhere in the plugin today**: the only text→text
feature is `Client.translate()`, a ComfyUI server node (`backend/comfy_client.py:568`),
not a chat API. So this is greenfield, and the design deliberately builds it out of
machinery that already exists: `RequestManager` for HTTP, `eventloop.run` for async,
the `Setting` descriptor + `settings.json` for configuration, and the
`PromptLibrary` JSON model pattern for the system-prompt presets. No new
dependencies — `AGENTS.md:9` forbids them.

Decisions confirmed with the user:
- **Header toolbutton** in the region header, next to "Save prompt to library".
- **Split button + menu**: left-click enhances with the active preset; the dropdown
  lists all presets (active one checked) plus *Configure…*.
- **Preview dialog** with original / editable enhanced text and
  Regenerate / Cancel / Apply — never a silent overwrite.
- **One active backend at a time** (kind + URL + key + model), configured in
  Settings; a preset may override model and temperature.

## Design overview

Four layers, each mirroring an existing one:

| Layer | New code | Modeled on |
|---|---|---|
| HTTP client | `backend/llm_client.py` — `LlmClient` ABC + 2 impls | `backend/client.py` + `comfy_client.py`/`cloud_client.py` |
| Presets | `prompt_enhancer.py` — dataclass + `QAbstractListModel` + JSON | `prompt_library.py` |
| Orchestration | `model/prompt_enhancer.py` — `PromptEnhancer(QObject)` | `model/connection.py` |
| UI | `ui/prompt_enhancer.py` — dialog + settings tab + preset editor; button in `ui/region.py` | `ui/prompt_library.py`, `StylePresets` in `ui/style.py` |

### Constraints found in the existing code that shape the design

- `RequestManager.post()` takes **no timeout** (`network.py:148`); only `http()`/`get()` do.
  All LLM calls must go through `http("POST", url, data, timeout=…, bearer=…)`.
- `RequestManager._finished` decodes JSON only for `application/json` (`network.py:227`);
  both APIs comply, so results arrive as `dict`.
- `NetworkError.from_reply` builds its message from `data["error"]` (`network.py:38`).
  Ollama returns `{"error": "..."}` (str, fine); OpenAI returns
  `{"error": {"message": …}}` (dict → stringified). **Needs its own re-wrap.**
- Cancelling the asyncio task is enough: `_finished` discards results for cancelled
  futures (`network.py:218`) and `_cleanup()` prunes the reply.
- `Settings.load()` type-checks against the default's type — float settings need
  float defaults.
- `settings.restore()` ("Restore Defaults") wipes **everything** in settings.json →
  presets must not live there.
- `SettingsTab.add(name, widget)` only works for real `Settings` attributes; anything
  else is built manually and handled in `_read`/`_write` — as `ConnectionSettings`
  (`ui/settings.py:570-582`) and `StylePresets` already do.

## 1. LLM client — new file `ai_diffusion/backend/llm_client.py`

Base types, both implementations, and shared helpers in one module (each impl is
~50 lines; split into `ollama_client.py`/`openai_client.py` only if they grow).

```python
class LlmBackend(Enum):
    ollama = _("Ollama")
    openai = _("OpenAI compatible")

@dataclass
class LlmModel:
    id: str
    name: str = ""          # display name, falls back to id
    size: int = 0           # bytes, ollama only

@dataclass
class LlmRequest:
    system_prompt: str
    user_prompt: str
    model: str
    temperature: float | None = None    # None -> omit, use server default
    max_tokens: int = 0                 # 0 -> omit

class LlmClient(ABC):
    default_url: str = ""

    def __init__(self, url: str, api_key: str = "", requests: RequestManager | None = None):
        self.url = normalize_url(url or self.default_url)
        self._key = api_key
        self._requests = requests or RequestManager()

    @abstractmethod
    async def chat(self, request: LlmRequest, timeout: float = 120) -> str: ...
    @abstractmethod
    async def list_models(self, timeout: float = 15) -> list[LlmModel]: ...

    async def check_connection(self, timeout: float = 15) -> list[LlmModel]:
        return await self.list_models(timeout)   # cheapest round-trip that also validates auth

def create_llm_client(backend, url, api_key="", requests=None) -> LlmClient: ...
def normalize_url(url: str) -> str: ...      # strip trailing "/", prepend http://, 0.0.0.0 -> 127.0.0.1
def reraise_llm_error(e: NetworkError) -> NoReturn: ...
def strip_reasoning(text: str) -> str: ...   # drop <think>…</think> blocks
```

The injectable `requests` argument exists purely so tests can substitute a fake —
same trick as passing a `Path` into `PromptLibrary.__init__` (`prompt_library.py:61`).

**`OllamaClient`** (`default_url = "http://127.0.0.1:11434"`)
- chat: `POST {url}/api/chat` with `{"model", "messages": [system, user], "stream": False, "think": False}`
  and sampling under `"options": {"temperature", "num_predict"}` (only keys that are set).
  Response `{"message": {"content": …}}`.
- models: `GET {url}/api/tags` → `{"models": [{"model": "qwen3:8b", "name": …, "size": …}]}`.
- auth: none normally; send bearer only when the user filled in a key (Ollama behind a proxy).

**`OpenAiClient`** (`default_url = "https://api.openai.com/v1"`)
- chat: `POST {url}/chat/completions` with top-level `temperature` / `max_completion_tokens`.
  Response `choices[0].message.content` — also handle the content-parts list form
  some gateways return.
- models: `GET {url}/models` → `data[].id`.
- auth: `Authorization: Bearer <key>` via the existing `bearer=` argument (`network.py:100-102`).
- The URL the user enters **includes the version prefix** (`…/v1`), which is how every
  OpenAI-compatible tool asks for it (`OPENAI_BASE_URL`); the help text says so.

Response parsing lives in module-level `parse_chat_response` / `parse_model_list`
functions per backend, so they are unit-testable without any HTTP.

**Why two implementations, not one.** Ollama does expose an OpenAI-compatible `/v1`,
so one impl could technically serve both. Two is still right:
1. `/api/tags` lists *locally installed* models with size/family — exactly what the
   Settings combo needs — and works regardless of the compat shim.
2. Parameter mapping differs (`options.temperature` / `num_predict` / `keep_alive`
   vs. top-level fields); local-model tuning needs the native ones.
3. Error payloads and status semantics differ (Ollama 404 = "model not pulled";
   OpenAI 404 = wrong base URL).
4. Auth defaults differ: OpenAI should fail validation before sending when no key is set.
5. The OpenAI impl must stay lenient for LM Studio / vLLM / llama.cpp / OpenRouter;
   mixing Ollama quirks in makes both worse.

Shared cost is low — URL normalization, error re-wrap, reasoning stripping and the
data types all live in the base module. This is the `ComfyClient`/`CloudClient`
split the repo already uses.

## 2. Presets — new file `ai_diffusion/prompt_enhancer.py`

Structural clone of `prompt_library.py` (dataclass + `from_dict`/`to_dict` +
`QAbstractListModel` + `load`/`save` on mutation).

```python
@dataclass
class EnhancerPreset:
    id: str
    name: str
    system_prompt: str = ""
    model: str = ""                    # "" -> use settings.enhancer_model
    temperature: float | None = None   # None -> server default
    created: float = 0.0
```

`EnhancerPresets(QAbstractListModel)` at `user_data_dir / "enhancer" / "presets.json"`,
with `create` / `add` / `duplicate` / `remove` / `update` / `find` / `find_index` /
`load` / `save` / `__len__` / `__iter__` / `__getitem__`, roles
`system_prompt_role`, `model_role`, `temperature_role`, `is_default_role`, and a
`default_changed` signal.

JSON is an **object wrapper**, not a bare list, so the active-preset marker lives with
the ids it references:

```json
{
  "version": 1,
  "default": "{6c1f…}",
  "presets": [
    { "id": "{6c1f…}", "name": "Detailed", "system_prompt": "…", "model": "",
      "temperature": 0.7, "created": 1755100000.0 }
  ]
}
```

`load()` also accepts a bare list (hand-written / forward-compat), using the first
entry as default; a malformed file logs via `client_logger` and leaves the model
empty rather than raising — same tolerance as `PromptLibrary.load` (`prompt_library.py:156`).

**Active preset = `default` in this file**, not a setting. It survives "Restore
Defaults", and picking a preset from the button menu simply calls `set_default(id)`.

On first run (no file) seed 2–3 built-ins from `builtin_presets()` — e.g. *Detailed*
(expand into a rich comma-separated description), *Concise* (tighten, keep subject
and style tags), *Danbooru tags* — plain `_()`-wrapped strings written to disk on
first save so users can freely edit them.

Owned by the `Root` singleton: `self._enhancer_presets = EnhancerPresets()` in
`Root.init()` (`model/root.py:48`, beside `self._prompts`), exposed as
`root.enhancer_presets`.

## 3. Settings — `ai_diffusion/settings.py`

New `Setting` descriptors, same style as the surrounding block (`settings.py:166+`):

| attr | default | note |
|---|---|---|
| `enhancer_enabled: bool` | `False` | hides the button until configured; opt-in feature |
| `enhancer_backend: LlmBackend` | `ollama` | enum → `ComboBoxSetting` auto-populates |
| `enhancer_url: str` | `""` | empty → the backend's `default_url` |
| `enhancer_api_key: str` | `""` | plaintext, like `access_token` (`settings.py:186`) |
| `enhancer_model: str` | `""` | default model; presets may override |
| `enhancer_timeout: int` | `120` | seconds |

`LlmBackend` is imported into `settings.py` from `backend/llm_client.py`. No cycle:
`llm_client.py` imports only `localization`, `util`, `backend/network` — none of
which import `settings`. (If one ever appears, move `LlmBackend` into `settings.py`
like `ServerMode`.)

**Security housekeeping:** add `"enhancer_api_key"` to the redaction tuple in
`collect_diagnostics` (`model/root.py:240`) so it never reaches a bug report.

## 4. Orchestration — new file `ai_diffusion/model/prompt_enhancer.py`

The client is derived state (a `RequestManager` + three strings, no persistent
connection), so it is built lazily here rather than stored on `Root`.

```python
class EnhancerState(Enum):
    idle = 0
    running = 1
    error = 2

class PromptEnhancer(QObject):
    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    state_changed = pyqtSignal(EnhancerState)

    def __init__(self, presets: EnhancerPresets):
        ...
        settings.changed.connect(self._handle_settings_changed)

    @property
    def is_configured(self) -> bool:
        return settings.enhancer_enabled and bool(settings.enhancer_model)

    def enhance(self, text: str, preset: EnhancerPreset): ...   # cancels any in-flight task
    def cancel(self): ...
    async def list_models(self) -> list[LlmModel]: ...
    def _ensure_client(self) -> LlmClient: ...   # cached; dropped on settings change
```

- `enhance()` builds `LlmRequest(system_prompt=preset.system_prompt, user_prompt=text,
  model=preset.model or settings.enhancer_model, temperature=preset.temperature)` and
  runs it via `eventloop.run(...)`, keeping the task handle so a **Regenerate** click
  cancels and supersedes the previous request.
- The coroutine catches `asyncio.CancelledError` (→ `idle`), `NetworkError` (→
  `error_occurred` with the mapped message) and generic exceptions (via
  `util.log_error`). Errors go to the **dialog that triggered them**, not to the
  generation error box — so `_report_errors` (`model/model.py:1611`) is intentionally
  not reused.
- `_handle_settings_changed` drops the cached client when `enhancer_backend` /
  `enhancer_url` / `enhancer_api_key` change, so a stale URL is impossible.
- The result never touches `region.positive`; only the dialog's **Apply** does.

One instance owned by `Root` (`root.enhancer`), created in `Root.init()` — global
state like the connection, not per-document.

## 5. UI

### Button — `ai_diffusion/ui/region.py`

In `ActiveRegionWidget.__init__`, next to `_save_prompt_button` (`region.py:122-126`):

```python
self._enhance_button = QToolButton(self)
self._enhance_button.setIcon(theme.icon("prompt-enhance"))
self._enhance_button.setAutoRaise(True)
self._enhance_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
self._enhance_button.setMenu(self._enhance_menu)   # repopulated on aboutToShow
self._enhance_button.clicked.connect(self._enhance_prompt)
```

added to `header_layout` before `_save_prompt_button` (`region.py:143`) and shown with
it in `_setup_bindings` (`region.py:273`). The menu is rebuilt in an `aboutToShow`
slot from `root.enhancer_presets`: a checkable action per preset (checked = current
default), a separator, then *Configure…* → `SettingsDialog.instance().show_page(...)`.
Selecting a preset calls `presets.set_default(id)`.

Visibility follows `settings.enhancer_enabled`, refreshed through the existing
`update_settings(key, value)` hook (`region.py:391`). The button is disabled with an
explanatory tooltip when the prompt is empty or `enhancer_model` is unset — the
tooltip points at Settings.

Because the header is hidden for `PromptHeader.icon`/`none` (`region.py:185-186`), the
button is absent in the Live and Custom Workflow docks. Accepted for v1; the overlay
route (`_layout_language_button`, `region.py:457`) stays available later.

**New icons** `ai_diffusion/icons/prompt-enhance-{dark,light}.svg`, following the
naming convention consumed by `theme.icon` (`ui/theme.py:45`). If adding assets is
undesirable, reuse `theme.icon("refine")`.

### Dialog — `ai_diffusion/ui/prompt_enhancer.py`

`PromptEnhanceDialog(QDialog)`, modeled on `PromptSaveDialog` (`ui/prompt_library.py:112`):

```
┌ Enhance prompt ──────────────────────────────┐
│ Preset: [ Detailed          ▾ ]              │
│ Original                                     │
│ ┌──────────────────────────────────────────┐ │  read-only QPlainTextEdit
│ │ a cat on a roof                          │ │
│ └──────────────────────────────────────────┘ │
│ Enhanced                                     │
│ ┌──────────────────────────────────────────┐ │  editable QPlainTextEdit
│ │ Enhancing…                               │ │  (busy on open)
│ └──────────────────────────────────────────┘ │
│ <status>       [Regenerate] [Cancel] [Apply] │
└──────────────────────────────────────────────┘
```

- Opens immediately in a busy state (status label, Apply disabled) and fires the
  request, so a slow model never blocks the UI thread; **Cancel** cancels the
  in-flight task and closes.
- The preset combo (over `root.enhancer_presets`) plus **Regenerate** lets the user
  retry with a different system prompt without reopening the menu.
- `error_occurred` puts the message in the status label in `theme.red` and leaves the
  dialog open for a retry.
- **Apply** writes the (possibly hand-edited) text to `self._region.positive` — the
  property the prompt editor is already bound to (`region.py:241`) — matching how the
  Prompt Library applies entries (`ui/prompt_library.py:290-299`).

### Settings tab — `PromptEnhancerSettings(SettingsTab)`

Same file (the `StylePresets` precedent: a large tab lives in its own module,
`ui/style.py:580`). Registered in `SettingsDialog.__init__` (`ui/settings.py:1170-1194`)
as `create_list_item(_("Prompt Enhancer"), …)` **after "Interface"**, and added to
`SettingsDialog.read()` (`ui/settings.py:1229`).

While here, replace the hardcoded `self._list.setCurrentRow(1)` in `show()`
(`ui/settings.py:1248`) with `self._list.setCurrentRow(self._stack.indexOf(self.styles))`
— `create_list_item` adds widgets to the stack in list order, so `indexOf` is exact —
and add a `show_page(widget)` helper used by the button's *Configure…* action.

Top section — connection. Simple widgets go through `self.add(name, widget)` so
`read`/`write` come free; the rest are manual and handled in `_read`/`_write`:
- `enhancer_enabled` → `SwitchSetting`
- `enhancer_backend` → `ComboBoxSetting` (enum auto-populates, `settings_widgets.py:300`);
  changing it swaps the URL placeholder to the backend's `default_url` and
  enables/disables the API-key row (kept visible for Ollama-behind-proxy, with a
  "usually not required" tooltip)
- `enhancer_url` → `QLineEdit` + **Test connection** button
- `enhancer_api_key` → `QLineEdit` with `EchoMode.Password` and a checkable reveal
  toggle; the setting's `desc` states plainly that it is stored unencrypted in
  `settings.json`
- `enhancer_model` → editable `QComboBox` + **Refresh** button; free text stays
  possible for endpoints without a model list
- `enhancer_timeout` → `SpinBoxSetting`
- status label reusing the green/yellow/red styling of
  `ConnectionSettings.update_server_status` (`ui/settings.py:588-611`)

Both buttons run one coroutine through `eventloop.run`, guarded so only one request is
in flight; it builds the client from the **current widget values** (not saved
settings), calls `check_connection()`, reports `"Connected — N models available"` or
the mapped error, and repopulates the model combo while preserving hand-typed text
(via `SignalBlocker` + `setCurrentText`).

**API keys** stay plaintext in `settings.json`, exactly like `access_token` /
`server_authorization` — the plugin has no keyring and may not add one. Mitigations:
masked echo, explicit reveal toggle, honest help text, and diagnostics redaction.

### Preset editor — `EnhancerPresetEditor(QWidget)`, bottom of the same tab

Modeled on `StylePresets` (combo + tool buttons + `Setting`-driven widgets bound to
the selected object, guarded by `SettingsWriteGuard`). Field metadata as a
`EnhancerPresetSettings` namespace of `Setting` objects, mirroring `StyleSettings`
(`style.py:20`).

- `QComboBox` whose model *is* `root.enhancer_presets` (DisplayRole = name,
  UserRole = id) — renames refresh automatically via `dataChanged`.
- Buttons: New / Duplicate / Delete (Krita icons as in `StylePresets`) plus a
  checkable ★ "Use by default" → `presets.set_default(id)`. Delete is disabled at one
  preset and confirms via `QMessageBox.question` (already imported in
  `ui/prompt_library.py`).
- Fields: Name (`TextSetting`), System prompt (new `TextAreaSetting`, ~8 lines),
  Model override (editable `ComboBoxSetting`, first item `(default)` → `""`,
  populated from the same fetched model list), Temperature (`SliderSetting` 0.0–2.0
  with an "Override" checkbox — unchecked writes `None`, matching the `clip_skip` /
  `preferred_resolution` pattern in `StylePresets`).
- Writes go to `presets.update(id, **changes)`, which saves the JSON. Because the text
  area fires per keystroke, debounce the save behind a ~500 ms `QTimer`.

**New reusable widget** in `ui/settings_widgets.py` — there is no multi-line setting
widget today (`LineEditSetting:368` is the closest):

```python
class TextAreaSetting(QWidget):     # header above, full-width QPlainTextEdit
    value_changed = pyqtSignal()
    def __init__(self, setting: Setting, parent=None, line_count=6): ...
    # setTabChangesFocus(True); setFixedHeight(fm.lineSpacing() * line_count + 10)
    # value setter guards `if v != current` to avoid cursor jumps / feedback loops
```

## Files touched

**New**
- `ai_diffusion/backend/llm_client.py`
- `ai_diffusion/prompt_enhancer.py`
- `ai_diffusion/model/prompt_enhancer.py`
- `ai_diffusion/ui/prompt_enhancer.py`
- `ai_diffusion/icons/prompt-enhance-{dark,light}.svg`
- `tests/mock/http.py`, `tests/test_llm_client.py`, `tests/test_prompt_enhancer.py`
- `docs/agent/PROMPT_ENHANCER_PLAN.md` (this document)

**Modified**
- `ai_diffusion/settings.py` — six `Setting` descriptors
- `ai_diffusion/model/root.py` — `root.enhancer_presets`, `root.enhancer`, redact `enhancer_api_key`
- `ai_diffusion/ui/region.py` — split button + menu + dialog launch
- `ai_diffusion/ui/settings.py` — register the tab, `show_page`, de-hardcode `setCurrentRow(1)`
- `ai_diffusion/ui/settings_widgets.py` — `TextAreaSetting`

**Suggested order:** client + tests → settings descriptors + redaction → presets +
tests → orchestration + tests → `TextAreaSetting` → settings tab + editor → region
button. All new user-facing strings go through `localization.translate as _`.

## Verification

**Unit tests** — headless; `tests/conftest.py` already creates a `QCoreApplication`
and calls `eventloop.setup()`, and provides the `@qtapp` decorator needed for
coroutines (`create_future()` requires a running loop).

`tests/mock/http.py` — a duck-typed `MockRequests` standing in for `RequestManager`
(only `get`/`http`/`post` are used), recording `(method, url, data, timeout, bearer)`
calls and returning canned results or exceptions; injected via
`LlmClient(..., requests=MockRequests())`.

`tests/test_llm_client.py`:
- Ollama chat — exact URL, `stream=False`, system→user message order,
  `options.temperature` present only when set; response parsing; `<think>` stripped;
  empty content → error.
- OpenAI chat — exact URL, bearer header, `temperature` omitted when `None`, both the
  string and content-parts response shapes.
- Model listing — `/api/tags` `model` field vs `/v1/models` `id` normalization; empty list.
- `reraise_llm_error` — OpenAI's nested `error.message`, Ollama's flat string, and the
  401/404/429 hints.
- `normalize_url` — scheme prepended, trailing slash stripped, `0.0.0.0` rewritten.
- Timeout is actually forwarded (guards against the `RequestManager.post` trap).

`tests/test_prompt_enhancer.py` — mirrors `tests/test_prompt_library.py` including its
`EventHandler` for row signals:
- preset create / update / duplicate (new id, "(copy)" name) / remove; roles
- default handling: first preset is default, `set_default`, deleting the default
  re-points, deleting the last preset reseeds built-ins
- JSON round trip in a `TemporaryDirectory`, `temperature=None` survives; bare-list
  JSON accepted; malformed JSON → empty model + logged error, no exception
- fresh path with no file → built-ins seeded and written
- `PromptEnhancer` with a stub `LlmClient`: happy path emits `result_ready`; error path
  emits `error_occurred` and sets `state`; a second `enhance()` supersedes the first;
  `cancel()` returns to `idle`; `preset.model` wins over `settings.enhancer_model`;
  `temperature=None` is omitted from the request.

Run `pytest tests/test_llm_client.py tests/test_prompt_enhancer.py`, then
`pytest tests --ci`, then `ruff check && ruff format && pyright` (`AGENTS.md:43-48`).
Workflows and the cloud client are untouched, so the inference suites are not needed.

The settings tab and preset editor are not unit-testable (they need
`Krita.instance().icon` and the full dialog; the repo has no widget tests). That is
why all real logic — client construction, response parsing, preset resolution — lives
in the three testable modules above and the tab stays thin.

**Manual E2E** (Krita, Generation workspace):
1. `ollama serve` with a small model (`ollama pull llama3.2:3b`).
2. Settings → Prompt Enhancer: enable, backend *Ollama*, URL `http://127.0.0.1:11434`;
   **Refresh** fills the model list; **Test connection** turns green with a model count.
3. Type `a cat on a roof` → click ✨ → dialog opens busy, then shows the rewrite →
   **Apply** → the prompt box contains it.
4. **Regenerate** with a different preset from the dialog combo → new result;
   **Cancel** mid-request closes without touching the prompt.
5. Dropdown arrow → pick another preset → the check mark moves, the next left-click
   uses it, and the choice survives a Krita restart (`enhancer/presets.json` → `default`).
6. New / Duplicate / Delete a preset in Settings → verify
   `<user_data_dir>/enhancer/presets.json` and that the button menu updates.
7. Switch to *OpenAI compatible*, URL `https://api.openai.com/v1` + a key, model
   `gpt-4o-mini` → repeat 2–3. Verify the key renders masked, the reveal toggle works,
   and Help → *Report a bug* diagnostics show `enhancer_api_key: <redacted>`.
8. Error paths: stop Ollama → **Test connection** shows red; enhancing shows the error
   in the dialog and leaves the prompt untouched. Bogus OpenAI key → the 401 message is
   legible ("Authorization failed — check the API key").
9. Empty prompt → button disabled with a tooltip; `enhancer_enabled` off → button
   hidden; **Restore Defaults** clears the connection settings but leaves presets intact.
