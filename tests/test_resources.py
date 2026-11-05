import json
from itertools import chain

import ai_diffusion.backend.resources as res
from ai_diffusion.backend.resources import Arch, ModelResource

from .config import result_dir


def test_resources_json():
    result = {}
    result["required"] = ModelResource.as_list(res.required_models)
    result["checkpoints"] = ModelResource.as_list(res.default_checkpoints)
    result["upscale"] = ModelResource.as_list(res.upscale_models)
    result["optional"] = ModelResource.as_list(res.optional_models)
    result["prefetch"] = ModelResource.as_list(res.prefetch_models)
    result["deprecated"] = ModelResource.as_list(res.deprecated_models)

    original = res._models_file.read_text()
    result_string = json.dumps(result, indent=2)
    (result_dir / "resources.json").write_text(result_string)
    assert result_string == original


def test_json_no_duplicates():
    d = json.load(res._models_file.open())
    all_ids = set()
    all_names = set()
    for cat in d.values():
        for m in cat:
            assert m["name"] not in all_names, f"Duplicate model name: {m['name']}"
            all_names.add(m["name"])
            ids = m["id"]
            ids = [ids] if isinstance(ids, str) else ids
            for id in ids:
                assert id not in all_ids, f"Duplicate model id: {id}"
            all_ids.update(ids)


def test_same_name_same_model():
    for m in res.all_models(include_deprecated=True):
        for o in res.all_models(include_deprecated=True):
            if m is not o and m.name == o.name:
                assert all(mf.url == of.url for mf, of in zip(m.files, o.files))


def test_resource_ids_exist():
    ids = chain(res.required_resource_ids, res.recommended_resource_ids)
    for resource_id in ids:
        if resource_id.arch in (
            Arch.sd3,
            Arch.qwen,
            Arch.qwen_e,
            Arch.qwen_e_p,
            Arch.qwen2,
            Arch.flux2_9b,
            Arch.anima,
            Arch.ernie,
            Arch.krea2,
            Arch.flux3,
            Arch.moss,
            Arch.fibo2,
        ):
            continue  # no model downloads yet
        model = res.find_resource(resource_id)
        assert model is not None, f"Resource ID {resource_id} not found"


def test_resource_id_parse_roundtrip():
    # All known resource ids must parse (e.g. string identifiers like PiD upscale models)
    for id in res.search_paths:
        assert res.ResourceId.parse(id).string == id


def test_find_pid_model_requires_4step():
    from ai_diffusion.backend.comfy_client import _find_pid_models

    full = "PID/pid_1.5_qwenimage_1024_to_4096_int8_convrot.safetensors"
    distilled = "PID/pid_1.5_qwenimage_1024_to_4096_4step_int8_convrot.safetensors"
    id = res.resource_id(res.ResourceKind.upscaler, Arch.all, "pid_qwenimage")
    assert _find_pid_models([full, distilled, "KREA2/krea2_turbo.safetensors"])[id] == distilled
    assert _find_pid_models([full])[id] is None  # full model needs other sampler settings
