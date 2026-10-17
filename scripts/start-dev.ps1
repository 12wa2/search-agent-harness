[CmdletBinding()]
param(
    [switch]$SkipApi
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$exe = Join-Path $root ".venv\Scripts\search-agent.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    $command = Get-Command search-agent -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "search-agent was not found. Activate the virtual environment or install the project first."
    }
    $exe = $command.Source
}

$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

function Test-SearchAgentHealth {
    param([string]$Uri)
    try {
        $response = Invoke-RestMethod -Uri $Uri -TimeoutSec 2
        return $response.status -eq "ok"
    } catch {
        return $false
    }
}

function Wait-ForHealth {
    param([string]$Uri, [int]$Attempts = 30)
    for ($i = 0; $i -lt $Attempts; $i++) {
        Start-Sleep -Seconds 1
        if (Test-SearchAgentHealth -Uri $Uri) {
            return $true
        }
    }
    return $false
}

$gatewayConfig = Join-Path $root "gateway.local.json"
if (-not (Test-Path -LiteralPath $gatewayConfig)) {
    $gatewayConfig = Join-Path $root "examples\gateway.docker.json"
    Write-Host "gateway.local.json not found; using examples/gateway.docker.json"
}

if (-not $env:SEARCH_AGENT_GATEWAY_API_KEY) {
    $env:SEARCH_AGENT_GATEWAY_API_KEY = "local-gateway-key"
}

if (Test-SearchAgentHealth -Uri "http://127.0.0.1:4000/healthz") {
    Write-Host "Gateway already healthy: http://127.0.0.1:4000"
} else {
    $gatewayOut = Join-Path $logs "gateway.out.log"
    $gatewayErr = Join-Path $logs "gateway.err.log"
    $gatewayArgs = @("gateway", "--config", "`"$gatewayConfig`"")
    $gateway = Start-Process -FilePath $exe -ArgumentList $gatewayArgs -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $gatewayOut -RedirectStandardError $gatewayErr -PassThru
    if (-not (Wait-ForHealth -Uri "http://127.0.0.1:4000/healthz")) {
        Write-Error "Gateway failed to start. See $gatewayErr"
        if (Test-Path -LiteralPath $gatewayErr) {
            Get-Content -LiteralPath $gatewayErr -Tail 30
        }
        exit 1
    }
    Write-Host "Gateway started (PID $($gateway.Id)): http://127.0.0.1:4000"
}

if ($SkipApi) {
    exit 0
}

if (Test-SearchAgentHealth -Uri "http://127.0.0.1:8000/healthz") {
    Write-Host "API already healthy: http://127.0.0.1:8000"
    exit 0
}

$env:SEARCH_AGENT_BASE_URL = "http://127.0.0.1:4000/v1"
$env:SEARCH_AGENT_API_KEY = $env:SEARCH_AGENT_GATEWAY_API_KEY
$env:SEARCH_AGENT_MODEL = "search-agent-primary"
$env:SEARCH_AGENT_DISABLE_THINKING = "1"
$env:SEARCH_AGENT_TIME_BUDGET_SEC = "180"

$apiArgs = @("serve", "--host", "127.0.0.1", "--port", "8000")
if (-not ($env:PRIMARY_LLM_API_KEY -or $env:FALLBACK_LLM_API_KEY)) {
    $apiArgs += "--offline"
    Write-Host "No upstream key found; starting the Web API in offline demo mode."
}

$apiOut = Join-Path $logs "api.out.log"
$apiErr = Join-Path $logs "api.err.log"
$api = Start-Process -FilePath $exe -ArgumentList $apiArgs -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr -PassThru
if (-not (Wait-ForHealth -Uri "http://127.0.0.1:8000/healthz")) {
    Write-Error "API failed to start. See $apiErr"
    if (Test-Path -LiteralPath $apiErr) {
        Get-Content -LiteralPath $apiErr -Tail 30
    }
    exit 1
}
Write-Host "API started (PID $($api.Id)): http://127.0.0.1:8000"
Write-Host "Open http://127.0.0.1:8000 for the Web console."
