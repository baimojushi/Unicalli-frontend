@echo off
chcp 65001 >nul
cd /d D:\ai\unicalli
set CUDA_VISIBLE_DEVICES=1
set UNICALLI_T5_DIR=E:\ai\unicalli-base-models\xflux_text_encoders
set UNICALLI_CLIP_DIR=E:\ai\unicalli-base-models\clip-vit-large-patch14
set AE=E:\ai\unicalli-base-models\ae.safetensors
set HF_HUB_OFFLINE=1
set PYTHONIOENCODING=utf-8
set UNICALLI_QUANT=8bit
set UNICALLI_OUTPUT=output_8bit_cached.png
echo [cache-test] starting cached-load generation on GPU1...
D:\EXPRESS_programs_file\conda-envs\unicalli\python.exe predict.py
echo [cache-test] exit code: %ERRORLEVEL%
