# -*- coding: utf-8 -*-
"""双卡全精度冒烟：验证设备分布 + 小步数生成跨卡正确。"""
import os

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0,1")
os.environ.setdefault("UNICALLI_T5_DIR", r"E:\ai\unicalli-base-models\xflux_text_encoders")
os.environ.setdefault("UNICALLI_CLIP_DIR", r"E:\ai\unicalli-base-models\clip-vit-large-patch14")
os.environ.setdefault("AE", r"E:\ai\unicalli-base-models\ae.safetensors")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from inference import CalligraphyGenerator

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
print("=== device distribution ===")
print("T5:", next(gen.t5.parameters()).device)
print("CLIP:", next(gen.clip.parameters()).device)
print("VAE:", next(gen.vae.parameters()).device)
m = gen.model
print("img_in:", next(m.img_in.parameters()).device)
print("module_embeddings:", m.module_embeddings.device)
print("double[0]:", next(m.double_blocks[0].parameters()).device)
print("double[8]:", next(m.double_blocks[8].parameters()).device)
print("double[9]:", next(m.double_blocks[9].parameters()).device)
print("single[0]:", next(m.single_blocks[0].parameters()).device)
print("single[14]:", next(m.single_blocks[14].parameters()).device)
print("single[15]:", next(m.single_blocks[15].parameters()).device)
print("single[37]:", next(m.single_blocks[37].parameters()).device)
print("final_layer:", next(m.final_layer.parameters()).device)
print("split_index:", m._split_index)
print("split_single_index:", m._split_single_index)

img, cond = gen.generate(
    text="生日快乐喵", font_style="草", author="黄庭坚", num_steps=3, seed=42
)
print("generated:", img.size, "cond:", cond.size)
img.save("smoke_dual_fp.png")
print("SMOKE_OK")
