# -*- coding: utf-8 -*-
"""实测双 3090 之间 PyTorch P2P / NVLink 拷贝带宽。"""
import time

import torch

print(f"cuda devices: {torch.cuda.device_count()}")
print(f"P2P 0->1: {torch.cuda.can_device_access_peer(0, 1)}")
print(f"P2P 1->0: {torch.cuda.can_device_access_peer(1, 0)}")

a = torch.randn(32, 1024, 2048, device="cuda:0")
torch.cuda.synchronize(0)
gb = a.numel() * 4 / 2**30
t = time.time()
b = a.to("cuda:1")
torch.cuda.synchronize()
dt = time.time() - t
print(f"copy 0->1 {gb:.2f} GiB in {dt * 1000:.1f} ms = {gb / dt:.1f} GiB/s")

t = time.time()
c = b.to("cuda:0")
torch.cuda.synchronize()
dt = time.time() - t
print(f"copy 1->0 {gb:.2f} GiB in {dt * 1000:.1f} ms = {gb / dt:.1f} GiB/s")
