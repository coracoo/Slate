@echo off
chcp 65001 >nul
rem ============================================================
rem  Slate 工作台 · 安装开机自启（双击即用）
rem  登录本机后自动拉起 keepalive 守护（守护 server.py 8775，
rem  崩溃自动重启；停机方式：在 workbench 目录创建 STOP 文件）。
rem  重复运行会用 /F 覆盖旧任务。
rem  删除自启：schtasks /Delete /TN "SlateWorkbench" /F
rem ============================================================
setlocal
rem 路径取 bat 自身所在目录动态拼接，不写死仓库位置
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
set "KEEP=%ROOT%\workbench\keepalive.py"

if not exist "%PYW%" (
    echo [错误] 找不到 %PYW%
    echo 请先按 README 用 uv 建环境：uv venv --python 3.12 --seed .venv
    pause
    exit /b 1
)
if not exist "%KEEP%" (
    echo [错误] 找不到 %KEEP%
    pause
    exit /b 1
)

schtasks /Create /TN "SlateWorkbench" /SC ONLOGON /RL LIMITED /F /TR "\"%PYW%\" \"%KEEP%\" 8775"
if errorlevel 1 (
    echo [失败] 任务注册失败，请检查权限或手动执行上方命令排查
    pause
    exit /b 1
)
echo.
echo [完成] 已注册开机自启任务 SlateWorkbench
echo   触发：本机登录时    命令："%PYW%" "%KEEP%" 8775
echo   删除：schtasks /Delete /TN "SlateWorkbench" /F
pause
