# Prompt Library (MVP)

## Context

Users of the Krita AI Diffusion plugin frequently reuse prompts that must take a
very specific form (weighting syntax like `(masterpiece:1.2)`, LoRA trigger sets,
quality/negative boilerplate). Today there is no way to save these — users keep an
external notebook open and copy/paste. This feature adds a **Prompt Library**: a
persistent, searchable collection of saved positive prompts that can be recalled
into the prompt box at any time, and that stays usable as the collection grows.

Scope was agreed with the user:
- **Positive prompt text only** — no negative prompt support in entries.
- Lives as a **dedicated dock page / workspace** (like Live / Upscale), selected
  from the existing `WorkspaceSelectWidget`.
- **MVP first**: save, search, recall (replace). Later phases (inline `/`
  autocomplete, template variables, grouped Favorites/Recent sections) are
  explicitly out of scope but the design leaves hooks for them.

The library persists **globally** (across documents), so it follows the
`Styles` / `FileCollection` precedent (JSON under `user_data_dir`), NOT the
per-document `Property(persist=True)` path.

## Design overview

- A single global collection persisted to `user_data_dir/"prompts"/"library.json"`.
  Single file (not file-per-entry): entries are tiny, there's no external tooling
  editing them, and it makes ordering / atomic save / roundtrip tests trivial —
  exactly the `loras.json` pattern in `files.py`.
- Data layer mirrors `files.py`: a `PromptEntry` dataclass, a
  `PromptLibrary(QAbstractListModel)`, and a `PromptFilter(QSortFilterProxyModel)`
  for search — so the new page gets filtering with no custom view logic.
- The library is owned by the `Root` singleton (`root.prompts`), constructed in
  `Root.init()` right beside `self._files = FileLibrary.load()`.
- A new `Workspace.prompts` page (`PromptLibraryWidget`) is added to the
  `QStackedWidget` in `ImageDiffusionWidget`, reachable from `WorkspaceSelectWidget`.
- Apply writes into `root.active_model.regions.active_or_root.positive` (the object
  the prompt editor is already bound to), then switches back to `Workspace.generation`.

## Data model — new file `ai_diffusion/prompt_library.py`

Mirror `ai_diffusion/files.py` (`File` @ `files.py:30`, `FileCollection` @ `files.py:92`,
`FileFilter` @ `files.py:249`).

**`PromptEntry`** (`@dataclass`, with `from_dict`/`to_dict` like `File.to_dict` @ `files.py:63`):
- `id: str` — stable uuid (`QUuid.createUuid().toString()`), used for selection role + dedupe
- `name: str`
- `positive: str` — the prompt text (positive only; no negative field exists)
- `category: str = ""` — single folder/group string; MVP uses it as a filter facet only
- `tags: list[str]` (default empty)
- `favorite: bool = False`
- `use_count: int = 0`
- `last_used: float | None = None` — epoch seconds
- `created: float` — epoch seconds

`to_dict` drops `None`/defaults; `from_dict` uses defensive `.get(key, default)`
(copy the tolerant style of `Style.load` @ `style.py:161`) for forward/back compat.

**`PromptLibrary(QAbstractListModel)`** — clone `FileCollection`:
- `__init__(database: Path | None = None)`, default `user_data_dir/"prompts"/"library.json"`
- `rowCount`, `data` roles: `DisplayRole`/`EditRole`→name, `UserRole`→id, plus custom
  `Qt.UserRole+N` roles for `favorite`, `category`, `positive` (used by the filter/delegate)
- `add`, `remove`, `update` (find by id, emit `dataChanged`), `find`, `find_index`,
  `save`, `load` — mirror `FileCollection` (`files.py:143-192`)
- `create(name, positive, category="", tags=None)` — fresh uuid, `beginInsertRows`/
  `endInsertRows`, save, return entry
- `mark_used(id)` — increments `use_count`, sets `last_used = time.time()`, `dataChanged`, `save`
- `__len__`/`__iter__`/`__getitem__` like `FileCollection` @ `files.py:215`

**`PromptFilter(QSortFilterProxyModel)`** — clone `FileFilter` (`files.py:249`):
- `search_text` (matches name OR positive OR tags, case-insensitive),
  `category` (exact, `""` = all), `favorites_only` (bool); each setter calls `invalidateFilter()`
- `filterAcceptsRow` reads the custom roles (shape of `FileFilter.filterAcceptsRow` @ `files.py:276`)
- Default `sort(0)` case-insensitive by name (MVP ships name sort only)
- `__getitem__` maps proxy→source like `FileFilter` @ `files.py:289`

## Workspace page

- **Enum**: add `prompts = 5` to `Workspace` (`model/model.py:83`). Append at the end —
  do NOT renumber existing values (`workspace` is persisted as an int).
- **Icons**: add `icons/workspace-prompts-dark.svg` + `icons/workspace-prompts-light.svg`
  (match existing `workspace-*-{dark,light}.svg`; `theme.icon("workspace-prompts")` resolves them).
- **Selector** (`WorkspaceSelectWidget`, `ui/widget.py:883`): add
  `Workspace.prompts: theme.icon("workspace-prompts")` to `_icons` (line 884) and
  `menu.addAction(self._create_action(_("Prompts"), Workspace.prompts))` after the Graph entry (line 902).
- **Host** (`ImageDiffusionWidget`, `ui/diffusion.py:279`): construct
  `self._prompts = PromptLibraryWidget()`, `self._frame.addWidget(self._prompts)` (near lines 288-297),
  and add branch in `update_content`: `elif model.workspace is Workspace.prompts:
  self._prompts.model = model; self._frame.setCurrentWidget(self._prompts)` (after the animation branch ~line 338).

**`PromptLibraryWidget(QWidget)`** — new file `ai_diffusion/ui/prompt_library.py`,
structured like `LiveWidget` (`ui/live.py:102`) / `UpscaleWidget`:
- `_model`, `_model_bindings` + a `model` property/setter that tears down with
  `Binding.disconnect_all(self._model_bindings)` (pattern from `generation.py:828`).
  Only per-model binding is `workspace` → the top-bar `WorkspaceSelectWidget` (one-way).
- Top bar: `WorkspaceSelectWidget(self)` (so users can switch away, like every page).
- Search row: `QLineEdit` (placeholder `_("Search prompts…")`) → `PromptFilter.search_text`;
  a favorites-only `QToolButton` (star); optional category `QComboBox` (distinct categories).
- Main: a `QListView` whose model is `PromptFilter(root.prompts)` (set once in `__init__` —
  the library outlives documents). A small `QStyledItemDelegate` renders name (bold) +
  truncated preview + favorite star using `theme` color tokens. Selecting a row shows the
  full `positive` in a read-only `QPlainTextEdit` preview pane. Flat filterable list for MVP
  (grouped Favorites/Recent deferred — filter facets already cover it).
- Action buttons (bottom), built with the `_create_tool_button` idiom (`custom_workflow.py:709`):
  **Apply** (`theme.icon("apply")`), **New** (add icon), **Edit** (inline `QLineEdit` panel toggled
  like `custom_workflow.py:740` `_workflow_edit_widgets`), **Delete** (`theme.icon("discard")`,
  with confirm), **Favorite** toggle.

## Save flow

Two capture points sharing one save/edit panel:
- **From the prompt box (primary)**: add a small `QToolButton` (`theme.icon("save")`,
  tooltip `_("Save prompt to library")`) to the region prompt widget in `ui/region.py`
  (header layout near line 121, beside `self.positive` @ 133). On click it reads
  `self.region.positive` (the widget already holds `self.region` and binds its `positive`
  @ `region.py:224`) and opens a small save popup: name `QLineEdit` (default = first ~40
  chars of the prompt, cf. `region.py:329`), optional editable category `QComboBox`, optional
  tags `QLineEdit` (comma-split), Save/Cancel → `root.prompts.create(...)`.
- **From the Prompts page**: **New** opens the same panel empty (type/paste text directly);
  **Edit** reuses it for the selected entry.

Negative text is never captured anywhere.

## Recall / apply flow

Target path (verified): `root.active_model` (`model/root.py:107`, falls back to `_null_model`)
→ `model.regions` (`RootRegion`, `model/region.py:151`) → `model.regions.active_or_root`
(`region.py:209`) = the active `Region` or the `RootRegion` — the object whose `positive`
the editor is bound to.

On Apply (button / double-click):
1. `entry = self._filter[selected_row]`
2. `model = root.active_model`; if it's the null model (no document) → Apply disabled / status hint
3. `model.regions.active_or_root.positive = entry.positive` (**Apply = replace**, MVP). Because
   `positive` is an observable `Property` with `positive_changed`, the bound `TextPromptWidget`
   updates automatically.
4. `root.prompts.mark_used(entry.id)`
5. `model.workspace = Workspace.generation` (drives `ImageDiffusionWidget.update_content` via
   `workspace_changed`) so the user sees the result.

Future hook: an "Insert at cursor" secondary action would insert into the focused
`TextPromptWidget` instead of setting the property — where `/` autocomplete converges later.

## Ownership / wiring

Own `PromptLibrary` on `Root` (`model/root.py`), constructed in `Root.init()` beside
`self._files = FileLibrary.load()` (~line 46), exposed as `@property prompts`. UI reaches it
via `root.prompts` (consistent with `root.files` / `root.workflows`). The list view's model is
`PromptFilter(root.prompts)`, set once; only `workspace` is per-model bound.

## Files

**Create:**
- `ai_diffusion/prompt_library.py` — `PromptEntry`, `PromptLibrary`, `PromptFilter` + JSON persistence
- `ai_diffusion/ui/prompt_library.py` — `PromptLibraryWidget` workspace page
- `ai_diffusion/icons/workspace-prompts-dark.svg`, `ai_diffusion/icons/workspace-prompts-light.svg`
- `tests/test_prompt_library.py` — data-model unit tests

**Modify:**
- `ai_diffusion/model/model.py` — add `prompts = 5` to `Workspace` (line 83)
- `ai_diffusion/model/root.py` — construct library in `init()`; add `root.prompts` property
- `ai_diffusion/ui/widget.py` — `WorkspaceSelectWidget` `_icons` + menu action (lines 884, 902)
- `ai_diffusion/ui/diffusion.py` — add page to stack + `Workspace.prompts` routing branch
- `ai_diffusion/ui/region.py` — "save to library" button on the region prompt widget
- `ai_diffusion/extension.py` — (optional) register a `switch_workspace_prompts` Krita action for consistency (~line 76)

## Verification

**Unit tests** (`tests/test_prompt_library.py`, following `tests/test_files.py`; headless, uses existing `conftest.py`):
- `test_create_and_roles` — roles return name/id/favorite/positive (cf. `test_files.py:47`)
- `test_add_remove` — `rowsInserted`/`rowsRemoved` via an `EventHandler` clone (`test_files.py:9`)
- `test_serialization_roundtrip` — write to `tmp_path/"library.json"`, reload in a second
  `PromptLibrary`, assert full field equality incl. tags/category/favorite/use_count
  (cf. `test_files.py:148`); assert `from_dict` tolerates missing optional keys
- `test_mark_used` — `use_count` increments, `last_used` set + persisted
- `test_search_filter` — `PromptFilter` matches name vs body vs tag; `favorites_only` + `category` facets (cf. `test_files.py:164`)
- `test_create_unique_ids` — distinct ids

Run: `pytest tests/test_prompt_library.py` (config in `tests/pytest.ini`).

**Manual E2E:**
1. Open Krita → workspace selector shows "Prompts" with icon in both light/dark themes.
2. In Generate, type a prompt → click save-to-library → popup pre-fills name → Save → entry appears on Prompts page.
3. Prompts workspace: search by name, by a word in the body, and by tag → filters correctly; favorites toggle filters correctly.
4. Select entry → preview pane shows full text.
5. Apply → switches to Generate and the active region's positive box contains the saved text; repeat with a sub-region active → confirm it targets that region (`active_or_root`).
6. Reopen page / inspect `library.json` → `use_count`/`last_used` updated.
7. Edit name/category, Delete an entry, restart Krita → changes persisted in `user_data_dir/prompts/library.json`.
8. No document open (null model) → Apply disabled, no crash.
9. Confirm the negative prompt is never touched or captured.
