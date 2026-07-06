@echo off
setlocal

set "PROJECT_DIR=E:\BOSS\traceable-research-agent"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

if not exist "%PROJECT_DIR%\.venv\Scripts\python.exe" (
    echo [ERROR] Python venv not found: %PROJECT_DIR%\.venv\Scripts\python.exe
    pause
    exit /b 1
)

if not exist "%PROJECT_DIR%\.venv\Scripts\streamlit.exe" (
    echo [ERROR] Streamlit executable not found: %PROJECT_DIR%\.venv\Scripts\streamlit.exe
    pause
    exit /b 1
)

if not exist "%PROJECT_DIR%\scripts\start_mcp_source_pack.ps1" (
    echo [ERROR] MCP startup script not found: %PROJECT_DIR%\scripts\start_mcp_source_pack.ps1
    pause
    exit /b 1
)

if not exist "%POWERSHELL_EXE%" (
    echo [ERROR] PowerShell not found: %POWERSHELL_EXE%
    pause
    exit /b 1
)

echo Checking demo ports...
for %%P in (9001 8000 8501) do (
    "%SystemRoot%\System32\netstat.exe" -ano -p tcp | "%SystemRoot%\System32\findstr.exe" /R /C:":%%P .*LISTENING" >nul
    if not errorlevel 1 (
        echo [ERROR] Port %%P is already in use.
        echo Close the existing Traceable demo window or free this port, then run this script again.
        pause
        exit /b 1
    )
)

echo Starting Traceable Research Agent demo...
echo.
set "MCP_CHANNEL_READONLY_SERVERS=source_pack=http://127.0.0.1:9001/mcp"
set "MCP_REMOTE_REGISTRATION_ATTEMPTS=5"
set "MCP_REMOTE_REGISTRATION_RETRY_SECONDS=1"

echo [0/3] MCP Source Pack Bridge: http://127.0.0.1:9001/health
start "Traceable MCP Source Pack" "%POWERSHELL_EXE%" -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%PROJECT_DIR%'; & '.\scripts\start_mcp_source_pack.ps1' -Mode real -Providers 'firecrawl,exa'"

echo Waiting for MCP Source Pack Bridge readiness...
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(45); while((Get-Date) -lt $deadline){ try { $h=Invoke-RestMethod -Uri 'http://127.0.0.1:9001/health' -TimeoutSec 2; if([int]$h.tool_count -gt 0){ exit 0 } } catch { }; Start-Sleep -Milliseconds 500 }; exit 1"
if errorlevel 1 (
    echo [ERROR] MCP Source Pack Bridge did not become ready within 45 seconds.
    echo Check the Traceable MCP Source Pack window for errors, then run this script again.
    pause
    exit /b 1
)

echo [1/3] FastAPI backend: http://127.0.0.1:8000
start "Traceable FastAPI" "%POWERSHELL_EXE%" -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%PROJECT_DIR%'; & '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --port 8000"

echo [2/3] Streamlit UI: http://127.0.0.1:8501
start "Traceable Streamlit" "%POWERSHELL_EXE%" -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%PROJECT_DIR%'; & '.\.venv\Scripts\streamlit.exe' run frontend/streamlit_app.py --server.port 8501"

echo.
echo All three windows have been launched.
echo Streamlit will be available at http://127.0.0.1:8501 after startup.
echo Close the three launched windows to stop the demo.
echo.
endlocal
