# -*- coding: utf-8 -*-
"""最小化双卡拆分诊断：只加载 + 拆分 + 打印设备分布，不做生成。"""
import os
import sys

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0,1")
os.environ.setdefault("UNICALLI_T5_DIR", r"E:\ai\unicalli-base-models\xflux_text_encoders")
os.environ.setdefault("UNICALLI_CLIP_DIR", r"E:\ai\unicalli-base-models\clip-vit-large-patch14")
os.environ.setdefault("AE", r"E:\ai\unicalli-base-models\ae.safetensors")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import torch

print("=== torch ===", torch.__version__, flush=True)
print("cuda avail:", torch.cuda.is_available(), flush=True)
print("visible devices:", torch.cuda.device_count(), flush=True)
for i in range(torch.cuda.device_count()):
    print(f"  [{i}] {torch.cuda.get_device_name(i)} {torch.cuda.get_device_properties(i).total_memory/2**30:.1f} GiB", flush=True)

# 简单跨卡拷贝测试（NVLink/P2P 路径）
a = torch.randn(512, 512, device="cuda:0")
b = a.to("cuda:1")
print("cross-card copy OK, sum:", b.sum().item(), flush=True)
del a, b
torch.cuda.empty_cache()

from inference import CalligraphyGenerator

print("=== constructing generator ===", flush=True)
gen = CalligraphyGenerator(
    model_name="flux-dev",
    device="cuda",
    offload=False,
    intern_vlm_path="./checkpoints/internvl_embedding",
    checkpoint_path="./checkpoints/unicalli-base_cleaned.bin",
    font_descriptions_path="dataset/chirography.json",
    author_descriptions_path="dataset/calligraphy_styles_en.json",
    use_deepspeed=False,
    use_4bit_quantization=False,
    use_8bit_quantization=False,
    use_dual_gpu=True,
)
print("=== generator constructed OK ===", flush=True)

print("=== device distribution ===", flush=True)
print("T5:", next(gen.t5.parameters()).device, flush=True)
print("CLIP:", next(gen.clip.parameters()).device, flush=True)
print("VAE:", next(gen.vae.parameters()).device, flush=True)
m = gen.model
print("img_in:", next(m.img_in.parameters()).device, flush=True)
print("pe_embedder:", next(m.pe_embedder.parameters()).device, flush=True)
print("module_embeddings:", m.module_embeddings.device, flush=True)
print("double[0]:", next(m.double_blocks[0].parameters()).device, flush=True)
print("double[11]:", next(m.double_blocks[11].parameters()).device, flush=True)
print("double[12]:", next(m.double_blocks[12].parameters()).device, flush=True)
print("single[0]:", next(m.single_blocks[0].parameters()).device, flush=True)
print("final_layer:", next(m.final_layer.parameters()).device, flush=True)
print("split_index:", m._split_index, flush=True)
print("SPLIT_OK", flush=True)

# 验证显存
for i in range(torch.cuda.device_count()):
    used = torch.cuda.memory_allocated(i) / 2**30
    print(f"GPU{i} allocated: {used:.1f} GiB", flush=True)
