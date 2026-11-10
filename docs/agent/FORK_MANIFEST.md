# Fork manifest: features unique to this fork

Lists every change this fork (`verigen/krita-ai-diffusion`) carries on top of upstream
(`Acly/krita-ai-diffusion`). Use it before each rebase to decide, for each feature, whether to
**keep**, **drop** or **fuse** it with an upstream equivalent.

- Snapshot: 2026-11-10, branch `qt6-feature/pid-upscale` (HEAD `108415c`)
- Base: `upstream/main` @ `828b70c` (= `v1.54.0-pre`, Qt6 / Krita 6 only)
- Next upstream target: `upstream/next` (36 commits, Generation workspace UI redesign, still 1.54.0)

Regenerate the commit list with `git log --reverse --oneline upstream/main..HEAD`.
Keep this file up to date whenever a fork feature is added, changed or dropped.

## Summary

| # | Feature | Commits | Kind | Upstream overlap (as of snapshot) |
|---|---------|---------|------|-----------------------------------|
| F1 | Prompt Library workspace + `/` inline expansion | a6bdf18, 7e0442c, 8dfe9fb | feature | none known; `next` reworks `region.py` / `diffusion.py` (conflicts) |
| F2 | LLM prompt enhancer (Ollama / OpenAI-compatible) | 97258ad | feature | none known; `next` reworks `region.py` (conflicts) |
| F3 | Qwen-Image 2.1 RGBA → RGB guards (`ensure_rgb`) | 8e27214 | fix | upstream 26aec6c handles alpha too, so re-check if this is still needed |
| F4 | Resolution multiplier limit 1.5x → 2.0x | 47257f4 | tweak | none; `next` changes `widget.py` QueuePopup |
| F5 | Custom workflow selection modifiers: pass Arch, not InpaintContext | 5b40e5a | fix | bug still present in `next`; good upstream PR candidate |
| F6 | FLUX.3 [klein] model support | 53f211f | model | **upstream PR #2641** (draft) |
| F7 | MOSS-Image model support | e2b5552 | model | **upstream PR #2640** (draft), #2643 (qt5) |
| F8 | Fibo 2.0 model support | a49d8df | model | **upstream PR #2646** (draft) |
| F9 | PiD latent upscale method (Upscale workspace) | 108415c | feature | none known; upstream PR #2619 (RTX VSR) also edits `ui/upscale.py` |

Merge commits af755ac and 480e679 only combine the F1 and F2 feature branches and add no content.

---

## F1: Prompt Library workspace + inline `/` expansion

**Commits:** a6bdf18 (feature, squashed from `feature/qwen_image_21` and ported from Qt5 to Qt6),
7e0442c (fix: the completion inserted the top-ranked entry instead of the selected one, so the entry
is now captured from `activated(QModelIndex)`), 8dfe9fb (the `/` trigger character is removed on insert).

**What it does:** a searchable, persistent library of saved positive prompts in a new dock workspace.
You can filter by name, body, tags, category and favorites. Prompts can be saved from the region
editor or the library page and applied back into the active prompt. Typing `/` in a positive prompt
box opens a popup of library entries and expands the typed `/token` in place, using the existing
autocomplete engine.

**Files:**
- new: `ai_diffusion/prompt_library.py` (storage/model), `ai_diffusion/ui/prompt_library.py`,
  `icons/workspace-prompts-{dark,light}.svg`, `tests/test_prompt_library.py`,
  `docs/agent/PROMPT_LIBRARY_PLAN.md`, `docs/agent/PROMPT_EXPANSION_PLAN.md`
- hooks into upstream files: `extension.py` (workspace registration), `model/root.py`, `model/model.py`
  (workspace enum), `ui/diffusion.py`, `ui/region.py` (save-to-library action in prompt widget),
  `ui/widget.py`, `ui/autocomplete.py` (`/` completer)

**Rebase risk:** high. `upstream/next` rewrites `ui/region.py` (edit mode now has a separate prompt and
no regions of its own) and adds to `ui/diffusion.py`. A trial merge conflicts in both files. The
save action and the `/` completer have to be re-attached to the new prompt widgets, including the
edit-mode prompt.

**Default decision:** keep.

## F2: LLM-backed prompt enhancer

**Commit:** 97258ad

**What it does:** rewrites text prompts with an Ollama or OpenAI-compatible LLM before generation.
It has its own "Prompt Enhancer" settings tab (backend, URL, API key, model, timeout, VRAM handling,
e.g. unloading ComfyUI models before calling the LLM).

**Files:**
- new: `backend/llm_client.py`, `model/prompt_enhancer.py`, `prompt_enhancer.py`, `ui/prompt_enhancer.py`,
  `tests/test_llm_client.py`, `tests/test_prompt_enhancer.py`, `tests/mock/http.py`,
  `docs/agent/PROMPT_ENHANCER_PLAN.md`, `docs/agent/problem.png`
- hooks into upstream files: `backend/client.py` + `backend/comfy_client.py` (free-VRAM hook), `model/root.py`,
  `settings.py` (new settings keys), `ui/settings.py` (new tab), `ui/settings_widgets.py`,
  `ui/region.py` (enhance button in prompt widget)

**Rebase risk:** medium-high. Same `ui/region.py` hotspot as F1. `ui/settings_widgets.py` also changed
upstream (Krita slider+spin widgets).

**Default decision:** keep.

## F3: Qwen-Image 2.1 RGBA → RGB guards

**Commit:** 8e27214 (authored 2026-09-28, before upstream merged Qwen 2.1)

**What it does:** adds `ComfyWorkflow.ensure_rgb()`, which composites onto an opaque background to drop
alpha. It is applied before `VAEEncode`, `ImageUpscaleWithModel` and `INPAINT_ColorMatch` in `scale`,
`scale_refine_and_decode`, `inpaint`, `refine` and `refine_region` for archs with `supports_alpha`.
It fixes the "expected 3 channels, got 4" crashes.

**Note:** the commit message mentions bumping the tooling-nodes and inpaint-nodes pins, but the diff
only touches `backend/comfy_workflow.py` and `backend/workflow.py`. The pins came from upstream
(5116677).

**Rebase risk:** medium. Upstream 26aec6c ("Make transparency/alpha for Qwen 2.1 work properly") covers
the same ground. Before each rebase, test Qwen 2.1 inpaint, refine and upscale without F3. If
upstream already handles it, **drop F3**. Otherwise keep it and consider sending it upstream.

**Default decision:** re-verify, then likely fuse or drop.

## F4: Resolution multiplier limit 2.0x

**Commit:** 47257f4. `ui/settings.py` (`SliderSetting` range 0.3–2.0) and `ui/widget.py`
(QueuePopup slider `setRange(3, 20)`).

**Rebase risk:** low, but `next` edits `ui/widget.py`. If upstream replaces the QSlider with Krita's
slider+spin widget, re-apply the new maximum to the new widget.

**Default decision:** keep (two-line re-apply).

## F5: Custom workflow selection modifiers fix

**Commit:** 5b40e5a. In `model/model.py` (custom workflow path, `ETN_KritaSelection`),
`get_selection_modifiers` was passed an `InpaintContext` where an `Arch` was expected. The fix passes
`ctx` only for `InpaintContext.mask_bounds` and `self.arch` otherwise.

**Rebase risk:** low, though `next` changes about 300 lines in `model/model.py`. The bug is still present
in `upstream/next` (`get_selection_modifiers(ctx, ...)`).

**Default decision:** keep. Good candidate for an upstream PR; once merged upstream, drop it.

## F6 / F7 / F8: New model architectures

All three follow the same pattern and build on each other in this order: F6, then F7, then F8. Each
one rewrites the same lines and bumps `comfy_version`.

| | F6 FLUX.3 [klein] (53f211f) | F7 MOSS-Image (e2b5552) | F8 Fibo 2.0 (a49d8df) |
|-|-|-|-|
| `Arch` | `flux3` ("Flux 3 Klein"), `is_flux3` | `moss` ("MOSS Image") | `fibo2` ("Fibo 2") |
| Text encoder | `qwen_3vl_4b` | `qwen_35_9b` (new resource id + filename aliases) | `qwen_3vl_4b` |
| VAE | `flux2` | `flux2-vae_small` / `flux2` | `flux2` |
| Edit-capable | yes (`edit-flux3.json`) | no | yes (`edit-fibo2.json`) |
| Styles | `flux3-klein.json` | `moss-image.json` | `fibo2.json` |
| comfy_version | → 45bf90d | → eb460bb | → 87ed5a0 |

**Files touched by each:** `backend/api.py`, `backend/comfy_workflow.py` (loader/encode nodes),
`backend/resources.py` (Arch enum, resource ids, required models, `comfy_version`),
`backend/workflow.py`, `presets/samplers.json`, `icons/sd-version-*-{dark,light}.svg`,
`ui/theme.py`, `tests/test_resources.py`. F6 also touches `ui/style.py`. F7 also touches
`backend/comfy_client.py`.

**Shared hotspots** (these conflict with any upstream model addition): the `Arch` enum and `from_string`,
`Arch.is_edit`-style predicates (`... or self.is_flux3 or self in (Arch.qwen2, Arch.fibo2)`),
`case Arch.krea2 | Arch.flux3 | Arch.fibo2:` match arms, the `arch.is_flux2 or ...` checks in
`resources.py`, the `Arch.list()` entries, and `comfy_version`.

**Rebase risk:** high once the upstream PRs land. Draft PRs #2641 (Flux 3 Klein), #2640 (MOSS) and
#2646 (Fibo 2.0) implement the same models.

**Default decision, per model:**
- upstream merged the model → **drop** the fork version and take upstream's `Arch` name, resource ids
  and style files. Before dropping, check fork-only details worth fusing into upstream's version:
  edit-style presets, sampler presets, filename aliases (e.g. the `qwen_35_9b` alias list) and icons.
  Users' saved styles reference the arch string (`flux3`, `moss-image`, `fibo2`), so check whether
  upstream uses the same strings. If not, either add an alias in `Arch.from_string` or accept that
  those styles break.
- upstream has not merged it → **keep**, rebased onto upstream's current `Arch` list.
- `comfy_version`: always take the newer of upstream's and the fork's pins. Never drop a pin a kept
  model needs.

## F9: PiD latent upscale method

**Commit:** 108415c (latest)

**What it does:** adds a "PiD (latent upscale)" entry to the Upscale workspace model list. The canvas
is VAE-encoded with the style's VAE and optionally refined at a configurable scale (default 1.5x,
capped by max pixels). The latent is then upscaled by the custom node `VerigenPiDTiledUpscale`
(Verigen custom node), decoded with the pixel_space VAE and resized to the target size, all in one
job. The PiD model is chosen by VAE latent family (currently `qwenimage`: Krea 2, Qwen, Anima). With
refine off, no diffusion model is loaded. The method is fail-safe: if the node, the PiD model or the
`gemma_2_2b` encoder is missing, the entry is disabled and the tooltip gives the reason.

**Files:**
- `backend/api.py`: `UpscaleInput.refine`, `refine_scale`, `pid_tile_size`
- `backend/workflow.py`: `pid_tile_count`, `pid_upscale_and_decode`, `pid_upscale_extent`, `upscale_pid`
- `backend/comfy_workflow.py`: PiD node wrappers
- `backend/resources.py`: PiD model and encoder resources
- `backend/client.py`, `backend/comfy_client.py`: detecting the PiD node and models
- `model/model.py`: job dispatch for the PiD method
- `ui/upscale.py`: model list item, refine controls, availability tooltip
- `tests/test_resources.py`, `tests/test_workflow.py`

**External dependency:** the Verigen PiD custom node pack must be installed on the ComfyUI server.
It is not part of upstream's managed install.

**Rebase risk:** medium. A trial merge with `next` conflicts in `ui/upscale.py`. Upstream renamed
`model.style` / `style_changed` to `model.active_style` / `active_style_changed`, and F9 uses the old
names (`self.style`, `model.style_changed.connect(self._update_pid_item)`), so they must be renamed
during the rebase. Upstream PR #2619 (RTX VSR upscale) also adds an upscale method in the same widget
and model list. If it lands, combine both methods in one list.

**Default decision:** keep.

---

## Rebase checklist

1. `git fetch upstream` and update the snapshot header (base commit, target branch).
2. For each feature row, re-check upstream for overlap: open/merged PRs, `git log upstream/<target>`,
   grep for the feature's key identifiers (`Arch.flux3`, `ensure_rgb`, `get_selection_modifiers(ctx`,
   `PromptLibrary`, `llm_client`, `pid_`).
3. Record a decision per feature (keep / drop / fuse) in the table below before rewriting history.
4. Rebase in the order F3 → F5 → F4 → F6 → F7 → F8 → F1 → F2 → F9 (fixes first, then models,
   then UI features), dropping features as decided.
5. Run `ruff check`, `ruff format`, `pyright`, `pytest tests --ci` and `python scripts/design.py --exit`.
   If F3, F6–F9 or workflows were touched, also run `pytest tests/test_workflow.py`.
6. Update this manifest: new commit hashes, removed features, new overlaps.

## Decision log

| Date | Upstream target | Feature | Decision | Notes |
|------|-----------------|---------|----------|-------|
| | | | | |
