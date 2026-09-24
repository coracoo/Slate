@echo off
chcp 65001 >nul
rem 双击即用：以正确解释器(.venv Python 3.12)后台拉起 keepalive 守护，无窗口
set "ROOT=%~dp0"
set "PYW=%ROOT%.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
    echo [错误] 找不到 %PYW% —— 请先按 README 用 uv 建 3.12 环境
    pause
    exit /b 1
)
powershell -NoProfile -Command "Start-Process -FilePath '%PYW%' -ArgumentList '\"%ROOT%workbench\keepalive.py\"','8775' -WindowStyle Hidden -WorkingDirectory '%ROOT%workbench'"
echo [完成] 工作台正在后台启动：http://127.0.0.1:8775（几秒后浏览器打开即可）
timeout /t 3 >nul
start "" http://127.0.0.1:8775
