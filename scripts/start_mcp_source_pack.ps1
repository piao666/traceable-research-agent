param(
    [ValidateSet("fake", "real")]
    [string]$Mode = "fake",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 9001,
    [string]$Providers = "firecrawl,exa,context7"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "Python not found. Create .venv or add python to PATH."
    }
    $Python = $cmd.Source
}

$env:MCP_BRIDGE_HOST = $HostName
$env:MCP_BRIDGE_PORT = [string]$Port
$env:MCP_BRIDGE_ENABLED_PROVIDERS = $Providers
$env:MCP_BRIDGE_FAKE_MODE = $(if ($Mode -eq "fake") { "true" } else { "false" })

Write-Host "MCP Source Pack Bridge"
Write-Host "  mode      = $Mode"
Write-Host "  providers = $Providers"
Write-Host "  mcp       = http://$HostName`:$Port/mcp"
Write-Host "  health    = http://$HostName`:$Port/health"
Write-Host ""
Write-Host "Keep this window running, then restart the FastAPI backend so it can rediscover remote MCP tools."
Write-Host ""

& $Python scripts\mcp_source_pack_server.py --host $HostName --port $Port --fake-mode $env:MCP_BRIDGE_FAKE_MODE --providers $Providers

