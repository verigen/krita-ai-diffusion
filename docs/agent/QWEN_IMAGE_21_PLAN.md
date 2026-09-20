# Qwen-Image 2.1 — day-0 support

> **Deliverable:** this document, written to `docs/agent/QWEN_IMAGE_21_PLAN.md`
> (alongside `PROMPT_ENHANCER_PLAN.md`), then the implementation described below.
> Two repos change: this one, and Acly's `comfyui-tooling-nodes`.

## Context

ComfyUI merged Qwen-Image 2.1 on 2026-09-20 in
[Comfy-Org/ComfyUI#16400](https://github.com/Comfy-Org/ComfyUI/pull/16400)
(commit `6bfaacc6`, author kijai), **before** the weights shipped. We want the Krita
plugin ready the day they land.

Qwen-Image 2.1 is a new architecture, not a variant of the Qwen-Image / Qwen-Image-Edit
archs the plugin already supports:

| | Qwen-Image 1.x | **Qwen-Image 2.1** |
|---|---|---|
| Latent | 16 ch, ÷8 | **64 ch, ÷16** |
| VAE | Qwen VAE, RGB | **Wan-2.2 layout, temporal kernel 1, RGBA (4 out ch)** |
| Text encoder | Qwen2.5-VL-7B | **Qwen3-VL-8B** |
| Conditioning node | `TextEncodeQwenImageEdit(Plus)` → 1 output | **`TextEncodeQwenImage21`** → `(positive, negative, latent)`, both prompts + up to 16 refs |
| DiT | 2×2 patchify | **patch 1** — one token per latent cell |

One checkpoint does both text-to-image (0 reference images) and editing (1-16), so it maps
to **one new `Arch` member** that follows the existing **Flux 2 pattern**: not `is_edit`,
but `supports_edit` (`resources.py:209-211` already reads `self.is_edit or self.is_flux2`).

Intended outcome: a minimal, reviewable diff that reuses the edit infrastructure already
built for Qwen-Image-Edit-Plus and Flux 2 Klein, plus a reproducible script that fabricates
mock safetensors so the wiring can be smoke-tested before real weights exist.

### Reference workflow — CONFIRMED, do not guess

Two **official** templates were merged the same day
([Comfy-Org/workflow_templates#1276](https://github.com/Comfy-Org/workflow_templates/pull/1276))
and already ship via the pinned `comfyui-workflow-templates==0.11.65`. Verified by
downloading both (the graph lives in `definitions.subgraphs`, not top-level `nodes`):

```
UNETLoader → [QwenImage21Cache] → KSampler.model
CLIPLoader(type="qwen_image") ─┐
VAELoader ─────────────────────┼→ TextEncodeQwenImage21 ─ positive → KSampler.positive
LoadImage × N ─────────────────┘   (prompt, negative_prompt,  ─ negative → KSampler.negative
                                    resolution, image_1..16)  ─ latent   → (edit only)
EmptyLatentImage → KSampler.latent_image   (t2i)
KSampler(steps=25, cfg=1, euler, simple, denoise=1.0) → VAEDecode → Save
```

Nothing relevant was found on Kijai's HF account (`Kijai/QwenImage_experimental` is
Qwen-Image 1.x only). Weights are unreleased **except the text encoder**, which is already
public and is stock Qwen3-VL-8B, shared with Boogu/Ideogram-4:
`https://huggingface.co/Comfy-Org/Qwen3-VL/tree/main/text_encoders` →
`qwen3vl_8b_bf16.safetensors` (17.5 GB; fp8/int8/nvfp4 variants also there).

## Constraints found in the existing code that shape the design

These are the non-obvious facts that make the diff small. **Verify none have changed before
relying on them.**

- **`CLIPLoader` needs no new type string.** `comfy/sd.py:1955` selects the 2.1 text encoder
  by *detection* — `clip_type == CLIPType.QWEN_IMAGE and te_model == TEModel.QWEN3VL_8B`.
  There is no `qwen_image21` option. Keep `type="qwen_image"`.
- **The existing empty-latent path already works.** `comfy/sample.py:45`
  `fix_empty_latent_channels` sees an all-zero latent, repeats channels to 64, and rescales
  by `downscale_ratio_spacial / spacial_downscale_ratio` = 8/16. So `EmptySD3LatentImage`
  (16 ch, ÷8, and it *does* emit `downscale_ratio_spacial: 8`) becomes `[1,64,h/16,w/16]`.
  **No new empty-latent node, in ComfyUI or in tooling-nodes.** Putting the arch in
  `is_qwen_like` routes it there automatically (`comfy_workflow.py:609`).
- **No 2×2 patchify.** `comfy/ldm/qwen_image21/model.py:282,309,353` — `img_in` is a
  `Linear` over `img.flatten(2)`, and `prefix_len = hidden_states.shape[1] - H*W`. The
  target only needs a multiple of **16**, which `resolution.diffusion_multiple = 16`
  (`backend/resolution.py:138`) already guarantees. **Do not make it per-arch.** (The 32-px
  rounding in the node applies to *reference* images only, which it resizes itself.)
- **Regions are already excluded.** `supports_regions` (`resources.py:185-187`) is
  `[sd15, sdxl, illu, illu_v, anima]`, so `ETN_AttentionMask`'s hardcoded ×8 latent
  assumption (`region.py:37`) is never reached by a Qwen arch. No tooling-nodes work.
- **A mock text encoder is impossible.** `Qwen3VL_8BConfig` (`comfy/text_encoders/llama.py:340`)
  hardcodes vocab 151936 / hidden 4096 / 36 layers, and `llama_detect` only reads dtype and
  quantization — never dimensions. ComfyUI instantiates the full 8B model whatever the file
  contains. Use the real, already-public TE instead (see §7).
- **`cfg = 1.0` is the normal operating point.** Both official templates use it, and
  `comfy/samplers.py:610` skips the uncond pass entirely at cfg 1, so the `negative` output
  is computed and discarded. It exists so that, at cfg > 1, the negative inherits the same
  `reference_latents` / `image_slots`. Consequence: `Conditioning.from_input`
  (`workflow.py:425`) sets `negative = None` when `cfg_scale <= 1` — the new code path must
  tolerate that and pass `""`.
- **`'Flux - Euler simple'` already is euler / simple / cfg 1.0** (`presets/samplers.json`).
  Reuse it; do not add a sampler preset. The style file sets `sampler_steps` to 25.
- **`ResourceId.parse` splits on `-` into exactly 3 parts** (`resources.py:459-461`), so an
  `Arch` member *name* must not contain a hyphen. `qwen21` is safe.
- **Qwen models are not managed downloads.** `presets/models.json` has zero Qwen entries;
  they are discovered from locally installed files via `search_paths`. Do not add download
  entries — but *do* add to the `tests/test_resources.py` skip list (§6).
- **`_find_text_encoder_models`** (`backend/comfy_client.py:758-770`) has a hardcoded `tes`
  list. A new TE id that is not in it is **never discovered**. (It uses `qwen_3vl_4b`,
  consistent with `resources.py:827` — there is no pre-existing naming bug here.)
- Styles are auto-discovered from `ai_diffusion/styles/*.json` (`style.py:250,304`) —
  dropping in a file is enough, there is no registry.

## Naming (fixed — both repos must agree)

| Thing | Value |
|---|---|
| `Arch` member | `qwen21 = "Qwen 2.1"` |
| tooling-nodes `base_model` string | `"qwen-image21"` |
| Text-encoder resource id | `"qwen_3vl_8b"` |

---

## 1. `comfyui-tooling-nodes` — the whole change is 2 lines

Without this the plugin can never see the model: `api.py:134` does
`model_names.get(raw_name, "unknown")` keyed on ComfyUI's `supported_models` **class name**,
and `QwenImage21` is absent → `base_model: "unknown"` → `Arch.from_string` returns `None` →
the checkpoint is silently discarded at `comfy_client.py:526`.

This is exactly the established one-line pattern — see `git show 0975922` ("Add ERNIE Image
detection"), `d5812f9` (Krea2), `b2783d8` (Anima), each a single `api.py` insertion.

In `api.py`, `model_names` (ends at `"Krea2": "krea2"`, ~line 62):
```python
    "QwenImage": "qwen-image",
+   "QwenImage21": "qwen-image21",
```
And in `gguf_architectures` (~line 65-68), for GGUF quantisations:
```python
    "qwen_image": "qwen-image",
+   "qwen_image21": "qwen-image21",
```

Then bump the pinned commit in this repo: `backend/resources.py:48`.

**Explicitly out of scope for day-0** (documented here so it is a deliberate choice, not an
oversight). These are real but not on the Edit path:
- `nodes.py:219-221` `ETN_LoadImageCache` `case 4` discards alpha into the mask output, so a
  model-generated transparent image loses alpha if round-tripped as a *reference*. (`c == 2`
  also falls through the `match` → `UnboundLocalError`; pre-existing.)
- `nodes.py:290-304` `ETN_ApplyMaskToImage` overwrites channel 3 rather than appending when
  the image already has 4 channels — arguably the intended behaviour for compositing.
- `nsfw.py:140-143` CLIP feature extractor on a 4-channel tensor. Only reachable with the
  NSFW filter enabled (off by default).

---

## 2. `ai_diffusion/backend/resources.py`

1. **Enum member**, after `qwen_l` (`:97`):
   ```python
   qwen21 = "Qwen 2.1"
   ```
2. **`from_string`** (`:131-139`) — add before/after the `qwen-image` block; the string is
   distinct so order does not matter:
   ```python
   if string == "qwen-image21":
       return Arch.qwen21
   ```
3. **`is_qwen_like`** (`:226-228`) — add `Arch.qwen21`. This one line gives: the shared Qwen
   icon (`theme.py:83`), `is_compatible` grouping for the manual arch override, and the
   correct `EmptySD3LatentImage` routing (`comfy_workflow.py:609`).
4. **`supports_edit`** (`:209-211`) — follow the Flux 2 precedent. Do **not** add to
   `is_edit`: that would force `strength = 1.0` and hide the strength slider, and 2.1 is also
   a text-to-image model.
   ```python
   return self.is_edit or self.is_flux2 or self is Arch.qwen21
   ```
5. **`text_encoders`** (`:230-258`) — a new `case`, *not* added to the existing qwen case
   (2.1 uses a different encoder):
   ```python
   case Arch.qwen21:
       return ["qwen_3vl_8b"]
   ```
6. **`Arch.list()`** (`:260-281`) — add `Arch.qwen21`, else it is invisible to discovery and
   the style dropdown.
7. **`search_paths`** — one TE row (keyed `Arch.all`) and one VAE row (per-arch, always
   required). Order the VAE patterns most-specific-first so they do not collide with the
   Qwen 1.x VAE:
   ```python
   resource_id(ResourceKind.text_encoder, Arch.all, "qwen_3vl_8b"): ["qwen3vl_8b", "qwen_3vl_8b", "qwen3-vl-8b"],
   resource_id(ResourceKind.vae, Arch.qwen21, "default"): ["qwen_image_2.1", "qwen_image_21", "qwen_image21"],
   ```
8. **`required_resource_ids`** (`:849-883`):
   ```python
   ResourceId(ResourceKind.text_encoder, Arch.qwen21, "qwen_3vl_8b"),
   ResourceId(ResourceKind.vae, Arch.qwen21, "default"),
   ```
9. **Bump pins**: `comfy_version` (`:16`) to a commit ≥ `6bfaacc6` — use `5ba116a4`
   (current ComfyUI HEAD in `_tmp`, which also bundles the workflow templates) — and the
   `comfyui-tooling-nodes` pin (`:48`) to the commit from §1.

## 3. `ai_diffusion/backend/comfy_client.py`

`_find_text_encoder_models` (`:758-770`), add to `tes`:
```python
    "qwen_3vl_4b",
+   "qwen_3vl_8b",
```

## 4. `ai_diffusion/backend/comfy_workflow.py` — one new node wrapper

Next to `text_encode_qwen_image_edit_plus` (`:701-715`), mirroring its `image1/image2/...`
unrolling. **Return the node, not `.output(0)`** — the caller needs two outputs. Pass
`count=3` so the node's outputs are addressable:

```python
def text_encode_qwen_image_21(
    self,
    clip: Output,
    vae: Output,
    images: list[Output],
    prompt: str | Output,
    negative_prompt: str | Output,
    resolution: int = 0,
):
    kwargs = {f"image_{i + 1}": img for i, img in enumerate(images[:16])}
    return self.add(
        "TextEncodeQwenImage21",
        3,
        clip=clip,
        vae=vae,
        prompt=prompt,
        negative_prompt=negative_prompt,
        resolution=resolution,
        **kwargs,
    )
```

Notes for the implementer:
- The `images` input is an `io.Autogrow` template named `image_1 … image_16`
  (`comfy_extras/nodes_qwen.py:126-134`) — hence `image_1`, **not** `image1` as in the
  Edit-Plus node. Confirm the exact key against the server's `object_info` when a build with
  the node is available.
- **`vae` is mandatory here**, unlike the Edit-Plus call which passes `vae=None`. With no
  VAE the node sets `keep_vision=True`, emits no `reference_latents`, and the reference
  image conditions the model *through the text encoder alone* — editing silently degrades
  to "generate something vaguely similar". This is the single easiest thing to get wrong.
- `resolution=0` keeps each reference at its own size (rounded to a multiple of 32) instead
  of resampling to 1024×1024, which preserves the canvas geometry the plugin already
  chose. The official template ships 1024; 0 is the better fit here.

## 5. `ai_diffusion/backend/workflow.py`

### 5a. CLIP loading — `load_checkpoint_with_lora` (`:157-167`)
A new `case` (separate from the existing qwen case — different encoder, same loader type):
```python
case Arch.qwen21:
    clip = w.load_clip(te["qwen_3vl_8b"], type="qwen_image")
```

### 5b. Conditioning — `encode_prompt` (`:474-486`)
This is the only structural change. `TextPrompt.encode()` (`:336-362`) is per-prompt and
returns one `Output`; `TextEncodeQwenImage21` needs both prompts at once. Branch in
`encode_prompt` **before** the existing single/multi-region split, reusing the `ref_images`
list that is already assembled at `:480-481` (canvas image + IP-adapter control layers):

```python
def encode_prompt(w, cond, clip, regions, image=None):
    ref_images = [image] if image is not None else []
    ref_images += [c.image.load(w) for c in cond.all_control if c.mode.is_ip_adapter]

    if clip.arch is Arch.qwen21:
        return encode_prompt_qwen21(w, cond, clip, ref_images)
    ...
```

and a small helper alongside it:

```python
def encode_prompt_qwen21(w, cond, clip, ref_images, vae):
    positive = cond.positive.text
    if positive != "" and cond.style_prompt:
        positive = merge_prompt(positive, cond.style_prompt, cond.positive.language)
    negative = cond.negative.text if cond.negative else ""
    if cond.positive.language:
        positive = w.translate(positive)
        negative = w.translate(negative) if negative else negative
    node = w.text_encode_qwen_image_21(clip.model, vae, ref_images, positive, negative)
    return ConditioningOutput(node.output(0), node.output(1))
```

Implementation notes:
- `cond.negative` is `None` whenever `cfg_scale <= 1` (`:425`) — the default for this model.
  Pass `""`.
- The `merge_prompt` / `w.translate` handling is lifted from `TextPrompt.encode`
  (`:343-349`); it is duplicated rather than shared because the caching in `TextPrompt`
  is keyed to a single output. Keep it minimal.
- **The node's third output (`latent`) is deliberately discarded.** It is always square
  `resolution × resolution` when there are no references, and sized from the *first*
  reference otherwise, with batch hardcoded to 1 — none of which matches what Krita needs.
  The plugin keeps its own latent: `w.empty_latent_image(...)` for generate, and the VAE
  encode of the canvas for the edit/refine path. §"Constraints" explains why the existing
  latent node is already correct.
- `encode_prompt` needs a `vae` argument threaded through from its callers, which already
  have it in scope. Check each call site (`:876-878`, `:1180-1182`, and the inpaint/refine
  region paths).

### 5c. `apply_reference_conditioning` (`:716-754`) — **add nothing**
`arch.supports_edit` is now true for `qwen21`, so the function runs, falls through the
`match` with no matching `case`, and returns `prompt` unchanged. That is correct:
`TextEncodeQwenImage21` attaches `reference_latents` internally, so adding `ReferenceLatent`
nodes here would double-apply them. Add a comment saying so, so nobody "fixes" it.

### 5d. Layer references — `prepare_prompts` (`:1606-1610`)
The 2.1 tokenizer builds `<image1><|vision_start|>…` markers and the official edit template
prompts literally read *"Keep the character in `<image1>` … the shirt from `<image2>`"*:
```python
layer_replace = {
    Arch.flux2_4b: "image {}",
    Arch.flux2_9b: "image {}",
    Arch.qwen_e_p: "Picture {}",
+   Arch.qwen21: "<image{}>",
}.get(arch, "")
```

## 6. Style preset, UI and tests

- **New file `ai_diffusion/styles/qwen-image-2.1.json`** — copy `qwen-edit.json` and change:
  `name` `"Qwen Image 2.1"`; `checkpoints` `["Qwen-Image-2.1", "qwen_image_2.1_bf16.safetensors",
  "qwen_image_2.1_int8_convrot.safetensors"]` (filenames from the template's download note);
  `sampler` / `live_sampler` `"Flux - Euler simple"`; `sampler_steps` 25, `live_sampler_steps` 8;
  `cfg_scale` and `live_cfg_scale` **1.0**. No `linked_edit_style` — one arch covers both modes,
  as with Flux 2.
- **`ai_diffusion/ui/style.py:928-940`** — `arch.is_qwen_like` already matches, but the
  `valid_archs` tuple is explicit; add `Arch.qwen21` so the manual override offers it.
- **`ai_diffusion/ui/theme.py`** — nothing to do, `is_qwen_like` (`:83`) covers the icon.
- **`tests/test_resources.py:50-60`** — add `Arch.qwen21` to the skip list in
  `test_resource_ids_exist`, since §2.8 adds required resources with no `models.json`
  download entries. Without this the test **fails**.
- **`ai_diffusion/backend/api.py`** — no change; `CheckpointInput.version` is already `Arch`.

## 7. Mock weights — `scripts/make_mock_models.py`

Purpose: let a developer exercise the whole graph against a real ComfyUI before the weights
exist. Reproducible and committed — not a scratch throwaway.

**Scope: DiT + VAE only. The text encoder is downloaded, not mocked** — see the constraint
above; `Qwen3VL_8BConfig` is hardcoded, so a "tiny" TE cannot exist. The real encoder is
already public, so the script should **print the download URL** rather than attempt a fake.
Verified variants under `https://huggingface.co/Comfy-Org/Qwen3-VL/tree/main/text_encoders`:

| File | Use |
|---|---|
| `qwen3vl_8b_fp8_scaled.safetensors` | **default recommendation** — lowest VRAM of the widely-compatible options |
| `qwen3vl_8b_bf16.safetensors` | 17.5 GB, full precision |
| `qwen3vl_8b_int8_convrot` / `_nvfp4` / `_w4a8` | smaller still, but need matching hardware/kernel support |

The `search_paths` pattern `"qwen3vl_8b"` from §2.7 matches every one of these filenames.

**No torch.** The repo's venv deliberately has none (`requirements.txt`; `AGENTS.md:9`
forbids third-party libs in the plugin). Write the safetensors container by hand — it is a
little-endian `u64` header length, a JSON header mapping name →
`{dtype, shape, data_offsets}`, then the raw buffer. Use `numpy` (already a dev dependency)
with `dtype: "F32"`; the models are small enough that fp32 is fine, and it sidesteps bf16.
Write to `tests/data/`, which `.gitignore` already covers (`tests/data/*.safetensors`).

**DiT** — `comfy/model_detection.py:962` requires all of `txt_in.text_norm.weight`,
`modulation.1.weight`, `transformer_blocks.0.attn.norm_q.weight`, `img_in.weight`,
`proj_out.weight`, plus one of `transformer_blocks.0.img_mlp.{gate_up,proj}.weight`.
Hard constraints: `in_channels = out_channels = 64`; `head_dim = 128` (ComfyUI hardcodes
`axes_dims_rope=(16,56,56)`, summing to 128 — a smaller head dim will crash `EmbedND`);
`context_in_dim = 4096` (the TE hidden size). Free: `num_attention_heads`, `num_layers`,
`mlp_ratio`. Use 1/1/1 → `D = 128`, ~14 tensors, a few MB. No bias tensors anywhere;
`img_norm1`/`img_norm2`/`norm_out.norm` are `LayerNorm(elementwise_affine=False)` and have
no parameters. Full key list is in `comfy/ldm/qwen_image21/model.py`; derive it there rather
than from this document.

**VAE** — detection (`comfy/sd.py:831-843`) needs `decoder.middle.0.residual.0.gamma` and
`decoder.upsamples.0.upsamples.0.residual.2.weight` present, plus
`decoder.head.2.weight` **5-D with `shape[2] == 1`** (this is what distinguishes it from a
real Wan 2.2 VAE) and `shape[0] == 4` for RGBA, and `encoder.conv1.weight` `[dim,4,1,3,3]`.
Everything else is hardcoded (`dim_mult=[1,2,4,8,8]`, `num_res_blocks=2`, `z_dim=64`,
`patch_size=1`, `temporal_kernel=1`); only `dim`/`dec_dim` can shrink — try 8.
`load_state_dict(strict=False)` means missing keys only warn, but generate the full key set
from the `WanVAE` constructor shape list so decode does not hit uninitialised `meta`
tensors.

Useful cross-check: `https://huggingface.co/optimum-intel-internal-testing/tiny-random-qwen-image-2.1`
is a public tiny random 2.1 whose **transformer key names match ComfyUI's exactly** (its VAE
uses diffusers naming, so it is not directly usable, and its `attention_head_dim=16` will
not load in ComfyUI).

Output is noise. The point is that every node, link, dtype and tensor shape is exercised.

## Verification

Per `AGENTS.md`, from `source .venv/bin/activate`:

1. `ruff check && ruff format && pyright`
2. `pytest tests --ci` — the fast, no-inference suite. Must stay green; in particular
   `tests/test_resources.py::test_resource_ids_exist` (needs the §6 skip-list entry) and
   `test_resources_json`.
3. Add a CPU-only assertion next to the existing
   `test_prepare_prompt_layers` (`tests/test_workflow.py:369`, parametrized over
   `[Arch.sd15, Arch.qwen_e_p]`) — extend it with `Arch.qwen21` and assert the positive
   prompt renders as `"prompt <image2> for <image3>"`. This is the one test that covers the
   new `layer_replace` entry and needs no server.
4. **Manual, not automated** — `tests/test_workflow.py` runs real inference and is
   deliberately excluded from this plan. To smoke-test by hand:
   - `python scripts/make_mock_models.py` → mock DiT + VAE into `tests/data/`
   - copy them into the ComfyUI `models/diffusion_models/` and `models/vae/` dirs, and fetch
     `qwen3vl_8b_fp8_scaled.safetensors` into `models/text_encoders/`. The mock DiT and VAE
     are a few MB, so the text encoder is the only real VRAM cost of this smoke test.
   - start ComfyUI at a commit ≥ `6bfaacc6`, restart the plugin, confirm the checkpoint is
     detected as **Qwen 2.1** (this alone validates the tooling-nodes change end-to-end),
     then run one generate and one edit and confirm the job completes and returns an image.
   Expect noise. A crash means a wiring bug; a completed job means the graph is right.
5. When the real weights land, re-run step 4 with them and check output quality, then
   revisit `resolution=0` (§4) and the style's step/cfg defaults.
