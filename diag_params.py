# -*- coding: utf-8 -*-
"""测量 Flux 模型各模块参数量，用于设计双卡拆分点。"""
import os

os.environ.setdefault("UNICALLI_T5_DIR", r"E:\ai\unicalli-base-models\xflux_text_encoders")
os.environ.setdefault("UNICALLI_CLIP_DIR", r"E:\ai\unicalli-base-models\clip-vit-large-patch14")
os.environ.setdefault("AE", r"E:\ai\unicalli-base-models\ae.safetensors")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import torch

def count_params(mod):
    if isinstance(mod, torch.nn.Parameter):
        return mod.numel()
    return sum(p.numel() for p in mod.parameters())

from src.flux.model import Flux
from src.flux.util import configs

model_name = "flux-dev"
with torch.device("meta"):
    model = Flux(configs[model_name].params)
model.init_module_embeddings(tokens_num=320, cond_txt_channel=896)

print("=== Flux model parameter distribution ===")
print(f"img_in: {count_params(model.img_in)/1e9:.3f} B")
print(f"time_in: {count_params(model.time_in)/1e9:.3f} B")
print(f"vector_in: {count_params(model.vector_in)/1e9:.3f} B")
print(f"guidance_in: {count_params(model.guidance_in)/1e9:.3f} B")
print(f"txt_in: {count_params(model.txt_in)/1e9:.3f} B")
print(f"pe_embedder: {count_params(model.pe_embedder)/1e9:.3f} B")
print(f"module_embeddings: {count_params(model.module_embeddings)/1e9:.3f} B")
print(f"cond_txt_in: {count_params(model.cond_txt_in)/1e9:.3f} B")
print(f"learnable_txt_ids: {count_params(model.learnable_txt_ids)/1e9:.3f} B")
print(f"final_layer: {count_params(model.final_layer)/1e9:.3f} B")

double = model.double_blocks
single = model.single_blocks
print(f"\ndouble_blocks: {len(double)} blocks")
d_total = sum(count_params(b) for b in double) / 1e9
print(f"  total: {d_total:.3f} B, per-block avg: {d_total/len(double):.3f} B")
for i in range(0, len(double), 4):
    print(f"  double[{i}]..double[{min(i+3,len(double)-1)}]: {sum(count_params(b) for b in double[i:i+4])/1e9:.3f} B")

s_total = sum(count_params(b) for b in single) / 1e9
print(f"\nsingle_blocks: {len(single)} blocks")
print(f"  total: {s_total:.3f} B, per-block avg: {s_total/len(single):.3f} B")
for i in range(0, len(single), 8):
    print(f"  single[{i}]..single[{min(i+7,len(single)-1)}]: {sum(count_params(b) for b in single[i:i+8])/1e9:.3f} B")

total = count_params(model) / 1e9
print(f"\nTOTAL: {total:.3f} B params ({total*2:.1f} GB bf16)")

# 设计拆分点：GPU0 空余 ~21GB（减 CLIP/VAE），GPU1 空余 ~17GB（减 T5 qint8 4.6GB）
# bf16: 2 bytes/param
gb0_target = 10.5  # 目标 GPU0 模型权重 GB (bf16)
print("\n=== candidate splits (bf16 GB) ===")
head = sum(count_params(getattr(model, n)) for n in
           ['img_in','time_in','vector_in','guidance_in','txt_in','pe_embedder','module_embeddings','cond_txt_in','learnable_txt_ids']) * 2 / 1e9
fl = count_params(model.final_layer) * 2 / 1e9
print(f"head (inputs+embeddings): {head:.2f} GB")
print(f"final_layer: {fl:.2f} GB")

for ds in range(0, len(double)+1):
    dgb = sum(count_params(b) for b in double[:ds]) * 2 / 1e9
    for ss in range(0, len(single)+1):
        sgb = sum(count_params(b) for b in single[:ss]) * 2 / 1e9
        g0 = head + dgb + sgb
        g1 = (d_total*2 - dgb) + (s_total*2 - sgb) + fl
        if abs(g0 - gb0_target) < 0.4:
            print(f"double[0:{ds}] + single[0:{ss}] -> GPU0 {g0:.2f} GB | GPU1 {g1:.2f} GB")
