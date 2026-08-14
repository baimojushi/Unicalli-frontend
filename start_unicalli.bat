@echo off
chcp 936 >nul
title UniCalli 启动器
cd /d D:\ai\unicalli

echo ============================================
echo   UniCalli Gradio UI 启动器
echo   流程: 自检端口 → 清理残留 → GPU 检查 → 启动
echo ============================================

REM ========== 1. 自检端口 55630 ==========
netstat -ano > "%TEMP%\uc_netstat.tmp" 2>nul
findstr /r ":55630.*LISTENING" "%TEMP%\uc_netstat.tmp" >nul 2>&1
if not errorlevel 1 (
    echo [1/4] 端口 55630 被占用，清理残留服务...
    for /f "tokens=5" %%p in ('findstr /r ":55630.*LISTENING" "%TEMP%\uc_netstat.tmp"') do (
        echo    - 终止 PID %%p
        taskkill /f /pid %%p >nul 2>&1
    )
    ping -n 3 127.0.0.1 >nul
) else (
    echo [1/4] 端口 55630 空闲
)
del "%TEMP%\uc_netstat.tmp" >nul 2>&1

REM ========== 2. 兜底清理残留 Gradio 进程 (仅 app.py) ==========
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'app\.py' }; if ($p) { $p | ForEach-Object { Write-Host ('   - 终止残留 python PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } } else { Write-Host '   - 无残留进程' }"

REM ========== 3. GPU0/GPU1 占用检查（双卡全精度需两卡空闲），必要时停止 llama ==========
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits > "%TEMP%\uc_gpu.tmp" 2>nul
set GPU0_MEM=0
set GPU1_MEM=0
for /f "tokens=1,2 delims=," %%a in (%TEMP%\uc_gpu.tmp) do (
    if "%%a"=="0" set GPU0_MEM=%%b
    if "%%a"=="1" set GPU1_MEM=%%b
)
del "%TEMP%\uc_gpu.tmp" >nul 2>&1
if %GPU0_MEM% GTR 5000 goto LLAMA_BUSY
if %GPU1_MEM% GTR 5000 goto LLAMA_BUSY
echo [3/4] 双卡空闲 ^(GPU0=%GPU0_MEM% MiB, GPU1=%GPU1_MEM% MiB^)，可直接使用
goto GPU_OK
:LLAMA_BUSY
echo [3/4] 检测到 GPU0=%GPU0_MEM% MiB / GPU1=%GPU1_MEM% MiB 被占用，停止 llama 释放双卡...
wsl -d Molink-GPU bash /home/molink/.local/share/llama/llama-service.sh stop qwen35
if errorlevel 1 echo   [警告] llama 停止失败，请检查 WSL
ping -n 5 127.0.0.1 >nul
:GPU_OK

REM ========== 4. 启动服务 ==========
set CUDA_VISIBLE_DEVICES=0,1
set UNICALLI_T5_DIR=E:\ai\unicalli-base-models\xflux_text_encoders
set UNICALLI_CLIP_DIR=E:\ai\unicalli-base-models\clip-vit-large-patch14
set AE=E:\ai\unicalli-base-models\ae.safetensors
set HF_HUB_OFFLINE=1
set PYTHONIOENCODING=utf-8
echo [4/4] 启动 UniCalli Gradio UI...
start "UniCalli Gradio" D:\EXPRESS_programs_file\conda-envs\unicalli\python.exe app.py

REM ========== 等待服务就绪 ==========
echo   等待服务监听端口 55630 ...
for /l %%i in (1,1,90) do (
    netstat -ano | findstr ":55630" | findstr "LISTENING" >nul 2>&1
    if not errorlevel 1 (
        echo [OK] 服务已就绪: http://127.0.0.1:55630
        goto READY
    )
    ping -n 2 127.0.0.1 >nul
)
echo [警告] 90 秒内未检测到服务，请查看 python 窗口日志
:READY
echo.
echo ========== 健康检查 /api/health ==========
curl -s http://127.0.0.1:55630/api/health
echo.
echo [OK] 服务健康检查通过。
echo.
echo 打开浏览器访问 UniCalli GUI...
start "" http://127.0.0.1:55630
echo.
if /i not "%~1"=="--nopause" pause