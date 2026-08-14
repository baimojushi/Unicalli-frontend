# -*- coding: utf-8 -*-
"""诊断：加载 8-bit 模型缓存，测量 GPU/系统内存占用与 quanto 反量化精度。"""
import ctypes
import os
import time

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
os.environ.setdefault("UNICALLI_T5_DIR", r"E:\ai\unicalli-base-models\xflux_text_encoders")
os.environ.setdefault("UNICALLI_CLIP_DIR", r"E:\ai\unicalli-base-models\clip-vit-large-patch14")
os.environ.setdefault("AE", r"E:\ai\unicalli-base-models\ae.safetensors")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import torch

from inference import CalligraphyGenerator


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def sys_ram_gb() -> float:
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return stat.ullAvailPhys / (1024 ** 3)
    return -1.0


t0 = time.time()
print(f"[diag] system RAM avail before init: {sys_ram_gb():.1f} GiB")
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
    use_8bit_quantization=True,
)
print(f"[diag] model init done in {time.time() - t0:.0f}s, RAM avail: {sys_ram_gb():.1f} GiB")

alloc = torch.cuda.memory_allocated() / (1024 ** 3)
resv = torch.cuda.memory_reserved() / (1024 ** 3)
free, total = torch.cuda.mem_get_info()
print(
    f"[diag] cuda allocated {alloc:.2f} GiB, reserved {resv:.2f} GiB, "
    f"free {free / 1024 ** 3:.2f} GiB / {total / 1024 ** 3:.2f} GiB"
)

first_linear = None
for m in gen.model.modules():
    if hasattr(m, "qweight"):
        first_linear = m
        break
if first_linear is not None:
    qw = first_linear.qweight
    scale = getattr(qw, "_scale", None)
    print(f"[diag] qweight dtype={qw.dtype}, scale dtype={scale.dtype if scale is not None else '?'}")
    dq = qw.dequantize()
    print(f"[diag] dequantize -> {dq.dtype}")
else:
    print("[diag] no qweight module found (model not quantized?)")

print("[diag] DONE")
