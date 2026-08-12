@echo off
setlocal

for %%I in ("%~dp0.") do set "PROJECT_DIR=%%~fI"
set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "STREAMLIT_EXE=%PROJECT_DIR%\.venv\Scripts\streamlit.exe"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "WITH_MCP=false"
set "CHECK_ONLY=false"
if not defined TRACEABLE_API_PORT set "TRACEABLE_API_PORT=8000"
if not defined TRACEABLE_STREAMLIT_PORT set "TRACEABLE_STREAMLIT_PORT=8501"
if not defined TRACEABLE_MCP_PORT set "TRACEABLE_MCP_PORT=9001"

:parse_args
if "%~1"=="" goto args_parsed
if /I "%~1"=="--with-mcp" (
    set "WITH_MCP=true"
    shift
    goto parse_args
)
if /I "%~1"=="--check" (
    set "CHECK_ONLY=true"
    shift
    goto parse_args
)
if /I "%~1"=="--help" goto usage
echo [ERROR] Unknown option: %~1
goto usage_error

:args_parsed
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python venv not found: %PYTHON_EXE%
    echo Create it with: python -m venv .venv
    exit /b 1
)
if not exist "%STREAMLIT_EXE%" (
    echo [ERROR] Streamlit executable not found: %STREAMLIT_EXE%
    echo Install project dependencies into .venv before starting the demo.
    exit /b 1
)
if not exist "%POWERSHELL_EXE%" (
    echo [ERROR] PowerShell not found: %POWERSHELL_EXE%
    exit /b 1
)
if not exist "%PROJECT_DIR%\app\main.py" (
    echo [ERROR] FastAPI entry point not found: %PROJECT_DIR%\app\main.py
    exit /b 1
)
if not exist "%PROJECT_DIR%\frontend\streamlit_app.py" (
    echo [ERROR] Streamlit entry point not found: %PROJECT_DIR%\frontend\streamlit_app.py
    exit /b 1
)

call :check_port %TRACEABLE_API_PORT%
if errorlevel 1 exit /b 1
call :check_port %TRACEABLE_STREAMLIT_PORT%
if errorlevel 1 exit /b 1

if /I "%WITH_MCP%"=="true" (
    if not exist "%PROJECT_DIR%\scripts\start_mcp_source_pack.ps1" (
        echo [ERROR] MCP startup script not found: %PROJECT_DIR%\scripts\start_mcp_source_pack.ps1
        exit /b 1
    )
    call :check_port %TRACEABLE_MCP_PORT%
    if errorlevel 1 exit /b 1
)

if /I "%CHECK_ONLY%"=="true" (
    if /I "%WITH_MCP%"=="true" (
        echo [OK] Startup checks passed for FastAPI, Streamlit, and optional MCP Source Pack.
    ) else (
        echo [OK] Startup checks passed for FastAPI and Streamlit.
    )
    exit /b 0
)

set "TRACEABLE_PROJECT_DIR=%PROJECT_DIR%"
set "TRACEABLE_PYTHON=%PYTHON_EXE%"
set "TRACEABLE_STREAMLIT=%STREAMLIT_EXE%"
set "STREAMLIT_API_BASE_URL=http://127.0.0.1:%TRACEABLE_API_PORT%"

echo Starting Traceable Research Agent from %PROJECT_DIR%
echo.

if /I "%WITH_MCP%"=="true" (
    set "MCP_CHANNEL_READONLY_SERVERS=source_pack=http://127.0.0.1:%TRACEABLE_MCP_PORT%/mcp"
    set "MCP_REMOTE_REGISTRATION_ATTEMPTS=5"
    set "MCP_REMOTE_REGISTRATION_RETRY_SECONDS=1"
    echo [1/3] Optional MCP Source Pack: http://127.0.0.1:%TRACEABLE_MCP_PORT%/health
    start "Traceable MCP Source Pack" "%POWERSHELL_EXE%" -NoExit -NoProfile -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath $env:TRACEABLE_PROJECT_DIR; & '.\scripts\start_mcp_source_pack.ps1' -Mode real -Port $env:TRACEABLE_MCP_PORT"
    echo Waiting for optional MCP Source Pack readiness...
    "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(45); while((Get-Date) -lt $deadline){ try { $h=Invoke-RestMethod -Uri ('http://127.0.0.1:' + $env:TRACEABLE_MCP_PORT + '/health') -TimeoutSec 2; if([int]$h.tool_count -gt 0){ exit 0 } } catch { }; Start-Sleep -Milliseconds 500 }; exit 1"
    if errorlevel 1 (
        echo [ERROR] Optional MCP Source Pack did not become ready within 45 seconds.
        echo Check the Traceable MCP Source Pack window for errors.
        exit /b 1
    )
    echo [2/3] FastAPI backend: http://127.0.0.1:%TRACEABLE_API_PORT%
) else (
    echo [1/2] FastAPI backend: http://127.0.0.1:%TRACEABLE_API_PORT%
)
start "Traceable FastAPI" "%POWERSHELL_EXE%" -NoExit -NoProfile -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath $env:TRACEABLE_PROJECT_DIR; & $env:TRACEABLE_PYTHON -m uvicorn app.main:app --host 127.0.0.1 --port $env:TRACEABLE_API_PORT"

if /I "%WITH_MCP%"=="true" (
    echo [3/3] Streamlit UI: http://127.0.0.1:%TRACEABLE_STREAMLIT_PORT%
) else (
    echo [2/2] Streamlit UI: http://127.0.0.1:%TRACEABLE_STREAMLIT_PORT%
)
start "Traceable Streamlit" "%POWERSHELL_EXE%" -NoExit -NoProfile -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath $env:TRACEABLE_PROJECT_DIR; & $env:TRACEABLE_STREAMLIT run frontend/streamlit_app.py --server.address 127.0.0.1 --server.port $env:TRACEABLE_STREAMLIT_PORT --server.headless true"

echo.
echo Core services have been launched. Open http://127.0.0.1:%TRACEABLE_STREAMLIT_PORT% after startup.
if /I "%WITH_MCP%"=="true" echo The optional MCP Source Pack was also launched.
echo Close the launched service windows to stop the demo.
exit /b 0

:check_port
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$listener=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,[int]%~1); try { $listener.Start(); exit 0 } catch { exit 1 } finally { $listener.Stop() }"
if errorlevel 1 (
    echo [ERROR] Port %~1 is already in use.
    echo Stop the existing service, free the port, or set a TRACEABLE_*_PORT override.
    exit /b 1
)
exit /b 0

:usage
echo Usage: %~nx0 [--check] [--with-mcp]
echo   --check      Validate local prerequisites and ports without starting services.
echo   --with-mcp   Also start the optional MCP Source Pack on port 9001.
echo Environment overrides: TRACEABLE_API_PORT, TRACEABLE_STREAMLIT_PORT, TRACEABLE_MCP_PORT.
exit /b 0

:usage_error
echo Usage: %~nx0 [--check] [--with-mcp]
exit /b 2
