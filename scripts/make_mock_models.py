#!/usr/bin/env python3
"""Fabricates mock Qwen-Image 2.1 DiT and VAE safetensors files, so the plugin's wiring for
the new architecture can be smoke-tested against a real ComfyUI build before real weights
are released. Output is noise - the point is that every node, link, dtype and tensor shape
in the graph is exercised, not that generation produces anything meaningful.

The plugin runtime forbids third-party libraries other than Qt/websockets (see AGENTS.md),
but this script runs outside that runtime during development and may use numpy, already a
dev dependency. There is no `safetensors` package available either way, so the container is
written by hand: an 8-byte little-endian header length, the JSON header, then the raw
tensor bytes, per the documented safetensors format.

The real text encoder (Qwen3-VL-8B) can't be mocked: `Qwen3VL_8BConfig` in ComfyUI hardcodes
vocab/hidden/layer sizes and always instantiates the full 8B model. Download a real variant,
e.g. https://huggingface.co/Comfy-Org/Qwen3-VL/blob/main/text_encoders/qwen3vl_8b_fp8_scaled.safetensors
"""

import json
import struct
from pathlib import Path

import numpy as np

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "data"
TEXT_ENCODER_URL = (
    "https://huggingface.co/Comfy-Org/Qwen3-VL/blob/main/text_encoders/"
    "qwen3vl_8b_fp8_scaled.safetensors"
)

# DiT: num_layers=1, attention_head_dim=128 (hardcoded via ComfyUI's axes_dims_rope=(16,56,56),
# never overridden for this arch - a smaller head dim crashes EmbedND), num_attention_heads=1
# (inner_dim=128), in/out_channels=64, context_in_dim=4096 (TE hidden size), mlp_ratio=1,
# fused_mlp=True (gate_up variant). Derived from comfy/ldm/qwen_image21/model.py.
DIT_SHAPES = {
    "txt_in.text_norm.weight": (4096,),
    "txt_in.in_layer.weight": (128, 4096),
    "txt_in.out_layer.weight": (128, 128),
    "img_in.weight": (128, 64),
    "modulation.1.weight": (512, 128),
    "time_text_embed.timestep_embedder.linear_1.weight": (128, 256),
    "time_text_embed.timestep_embedder.linear_2.weight": (128, 128),
    "transformer_blocks.0.attn.to_q.weight": (128, 128),
    "transformer_blocks.0.attn.to_k.weight": (128, 128),
    "transformer_blocks.0.attn.to_v.weight": (128, 128),
    "transformer_blocks.0.attn.to_out.0.weight": (128, 128),
    "transformer_blocks.0.attn.norm_q.weight": (128,),
    "transformer_blocks.0.attn.norm_k.weight": (128,),
    "transformer_blocks.0.img_mlp.gate_up.weight": (256, 128),
    "transformer_blocks.0.img_mlp.out.weight": (128, 128),
    "norm_out.linear.weight": (128, 128),
    "proj_out.weight": (64, 128),
    # img_norm1/img_norm2/norm_out.norm are LayerNorm(elementwise_affine=False): no tensors.
    # pe_embedder (EmbedND) is pure rotary math: no tensors.
}

# VAE: WanVAE(dim=8, dec_dim=8, z_dim=64, dim_mult=[1,2,4,8,8], num_res_blocks=2,
# attn_scales=[], temperal_downsample=[False,True,True,True], image_channels=4,
# patch_size=1, temporal_kernel=1). Derived from comfy/ldm/wan/vae2_2.py + vae.py.
VAE_DIM = 8
VAE_DEC_DIM = 8
VAE_Z_DIM = 64
VAE_ENC_DIMS = [VAE_DIM, VAE_DIM, VAE_DIM * 2, VAE_DIM * 4, VAE_DIM * 8, VAE_DIM * 8]
VAE_DEC_DIMS = [
    VAE_DEC_DIM * 8,
    VAE_DEC_DIM * 8,
    VAE_DEC_DIM * 8,
    VAE_DEC_DIM * 4,
    VAE_DEC_DIM * 2,
    VAE_DEC_DIM,
]
VAE_TEMPERAL_DOWNSAMPLE = [False, True, True, True]
VAE_TEMPERAL_UPSAMPLE = VAE_TEMPERAL_DOWNSAMPLE[::-1]


def rng_tensor(rng: np.random.Generator, shape: tuple[int, ...]) -> np.ndarray:
    return (rng.standard_normal(shape) * 0.02).astype(np.float32)


def write_safetensors(path: Path, tensors: dict[str, np.ndarray]) -> None:
    header: dict[str, object] = {}
    buffers: list[bytes] = []
    offset = 0
    for name, array in tensors.items():
        array = np.ascontiguousarray(array, dtype=np.float32)
        nbytes = array.nbytes
        header[name] = {
            "dtype": "F32",
            "shape": list(array.shape),
            "data_offsets": [offset, offset + nbytes],
        }
        buffers.append(array.tobytes())
        offset += nbytes
    header_bytes = json.dumps(header).encode("utf-8")
    header_bytes += b" " * (-len(header_bytes) % 8)  # pad to 8-byte boundary
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(header_bytes)))
        f.write(header_bytes)
        f.writelines(buffers)


def make_dit(rng: np.random.Generator) -> dict[str, np.ndarray]:
    return {name: rng_tensor(rng, shape) for name, shape in DIT_SHAPES.items()}


def add_residual_block(
    tensors: dict[str, np.ndarray], rng: np.random.Generator, prefix: str, in_dim: int, out_dim: int
) -> None:
    tensors[f"{prefix}.residual.0.gamma"] = rng_tensor(rng, (in_dim, 1, 1, 1))
    tensors[f"{prefix}.residual.2.weight"] = rng_tensor(rng, (out_dim, in_dim, 1, 3, 3))
    tensors[f"{prefix}.residual.2.bias"] = rng_tensor(rng, (out_dim,))
    tensors[f"{prefix}.residual.3.gamma"] = rng_tensor(rng, (out_dim, 1, 1, 1))
    tensors[f"{prefix}.residual.6.weight"] = rng_tensor(rng, (out_dim, out_dim, 1, 3, 3))
    tensors[f"{prefix}.residual.6.bias"] = rng_tensor(rng, (out_dim,))
    if in_dim != out_dim:
        tensors[f"{prefix}.shortcut.weight"] = rng_tensor(rng, (out_dim, in_dim, 1, 1, 1))
        tensors[f"{prefix}.shortcut.bias"] = rng_tensor(rng, (out_dim,))


def add_attention_block(
    tensors: dict[str, np.ndarray], rng: np.random.Generator, prefix: str, dim: int
) -> None:
    tensors[f"{prefix}.norm.gamma"] = rng_tensor(rng, (dim, 1, 1))
    tensors[f"{prefix}.to_qkv.weight"] = rng_tensor(rng, (3 * dim, dim, 1, 1))
    tensors[f"{prefix}.to_qkv.bias"] = rng_tensor(rng, (3 * dim,))
    tensors[f"{prefix}.proj.weight"] = rng_tensor(rng, (dim, dim, 1, 1))
    tensors[f"{prefix}.proj.bias"] = rng_tensor(rng, (dim,))


def add_resample_block(
    tensors: dict[str, np.ndarray], rng: np.random.Generator, prefix: str, dim: int, mode: str
) -> None:
    tensors[f"{prefix}.resample.1.weight"] = rng_tensor(rng, (dim, dim, 3, 3))
    tensors[f"{prefix}.resample.1.bias"] = rng_tensor(rng, (dim,))
    if mode == "downsample3d":
        tensors[f"{prefix}.time_conv.weight"] = rng_tensor(rng, (dim, dim, 1, 1, 1))
        tensors[f"{prefix}.time_conv.bias"] = rng_tensor(rng, (dim,))
    elif mode == "upsample3d":
        tensors[f"{prefix}.time_conv.weight"] = rng_tensor(rng, (2 * dim, dim, 1, 1, 1))
        tensors[f"{prefix}.time_conv.bias"] = rng_tensor(rng, (2 * dim,))


def make_vae(rng: np.random.Generator) -> dict[str, np.ndarray]:
    tensors: dict[str, np.ndarray] = {}

    # WanVAE top-level quant_conv / post_quant_conv (1x1x1 convs around the latent).
    tensors["conv1.weight"] = rng_tensor(rng, (2 * VAE_Z_DIM, 2 * VAE_Z_DIM, 1, 1, 1))
    tensors["conv1.bias"] = rng_tensor(rng, (2 * VAE_Z_DIM,))
    tensors["conv2.weight"] = rng_tensor(rng, (VAE_Z_DIM, VAE_Z_DIM, 1, 1, 1))
    tensors["conv2.bias"] = rng_tensor(rng, (VAE_Z_DIM,))

    # Encoder3d: in_channels = image_channels(4) * patch_size**2(1) = 4 (RGBA input).
    tensors["encoder.conv1.weight"] = rng_tensor(rng, (VAE_DIM, 4, 1, 3, 3))
    tensors["encoder.conv1.bias"] = rng_tensor(rng, (VAE_DIM,))
    for i in range(5):
        in_dim, out_dim = VAE_ENC_DIMS[i], VAE_ENC_DIMS[i + 1]
        add_residual_block(tensors, rng, f"encoder.downsamples.{i}.downsamples.0", in_dim, out_dim)
        add_residual_block(tensors, rng, f"encoder.downsamples.{i}.downsamples.1", out_dim, out_dim)
        if i != 4:
            mode = "downsample3d" if VAE_TEMPERAL_DOWNSAMPLE[i] else "downsample2d"
            add_resample_block(
                tensors, rng, f"encoder.downsamples.{i}.downsamples.2", out_dim, mode
            )
    add_residual_block(tensors, rng, "encoder.middle.0", VAE_ENC_DIMS[-1], VAE_ENC_DIMS[-1])
    add_attention_block(tensors, rng, "encoder.middle.1", VAE_ENC_DIMS[-1])
    add_residual_block(tensors, rng, "encoder.middle.2", VAE_ENC_DIMS[-1], VAE_ENC_DIMS[-1])
    tensors["encoder.head.0.gamma"] = rng_tensor(rng, (VAE_ENC_DIMS[-1], 1, 1, 1))
    tensors["encoder.head.2.weight"] = rng_tensor(rng, (2 * VAE_Z_DIM, VAE_ENC_DIMS[-1], 1, 3, 3))
    tensors["encoder.head.2.bias"] = rng_tensor(rng, (2 * VAE_Z_DIM,))

    # Decoder3d: conv1 maps the sampled latent (z_dim) into the first decoder level.
    tensors["decoder.conv1.weight"] = rng_tensor(rng, (VAE_DEC_DIMS[0], VAE_Z_DIM, 1, 3, 3))
    tensors["decoder.conv1.bias"] = rng_tensor(rng, (VAE_DEC_DIMS[0],))
    add_residual_block(tensors, rng, "decoder.middle.0", VAE_DEC_DIMS[0], VAE_DEC_DIMS[0])
    add_attention_block(tensors, rng, "decoder.middle.1", VAE_DEC_DIMS[0])
    add_residual_block(tensors, rng, "decoder.middle.2", VAE_DEC_DIMS[0], VAE_DEC_DIMS[0])
    for i in range(5):
        in_dim, out_dim = VAE_DEC_DIMS[i], VAE_DEC_DIMS[i + 1]
        add_residual_block(tensors, rng, f"decoder.upsamples.{i}.upsamples.0", in_dim, out_dim)
        add_residual_block(tensors, rng, f"decoder.upsamples.{i}.upsamples.1", out_dim, out_dim)
        add_residual_block(tensors, rng, f"decoder.upsamples.{i}.upsamples.2", out_dim, out_dim)
        if i != 4:
            mode = "upsample3d" if VAE_TEMPERAL_UPSAMPLE[i] else "upsample2d"
            add_resample_block(tensors, rng, f"decoder.upsamples.{i}.upsamples.3", out_dim, mode)
    tensors["decoder.head.0.gamma"] = rng_tensor(rng, (VAE_DEC_DIMS[-1], 1, 1, 1))
    tensors["decoder.head.2.weight"] = rng_tensor(rng, (4, VAE_DEC_DIMS[-1], 1, 3, 3))
    tensors["decoder.head.2.bias"] = rng_tensor(rng, (4,))

    return tensors


def main() -> None:
    rng = np.random.default_rng(0)

    dit_path = OUTPUT_DIR / "qwen_image_2.1_mock_dit.safetensors"
    vae_path = OUTPUT_DIR / "qwen_image_2.1_mock_vae.safetensors"
    write_safetensors(dit_path, make_dit(rng))
    write_safetensors(vae_path, make_vae(rng))

    print(f"Wrote mock DiT to {dit_path}")
    print(f"Wrote mock VAE to {vae_path}")
    print()
    print("Copy them into a ComfyUI install (commit >= 6bfaacc6) at:")
    print(f"  models/diffusion_models/{dit_path.name}")
    print(f"  models/vae/{vae_path.name}")
    print()
    print("The text encoder can't be mocked (Qwen3VL_8BConfig is hardcoded). Download:")
    print(f"  {TEXT_ENCODER_URL}")
    print("  into models/text_encoders/, then restart ComfyUI and the plugin.")


if __name__ == "__main__":
    main()
