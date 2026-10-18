[CmdletBinding()]
param(
    [switch]$SkipApi,
    [switch]$StartOnline
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

function Import-PersistentWindowsEnv {
    param([string[]]$Names)
    $loaded = @()
    foreach ($name in $Names) {
        if ([Environment]::GetEnvironmentVariable($name, "Process")) {
            continue
        }
        $value = [Environment]::GetEnvironmentVariable($name, "User")
        if (-not $value) {
            $value = [Environment]::GetEnvironmentVariable($name, "Machine")
        }
        if ($value) {
            Set-Item -Path "Env:$name" -Value $value
            $loaded += $name
        }
    }
    if ($loaded.Count -gt 0) {
        Write-Host "Loaded persistent Windows environment: $($loaded -join ', ')"
    }
}

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding utf8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) {
            continue
        }
        $name = $trimmed.Substring(0, $separator).Trim()
        $value = $trimmed.Substring($separator + 1).Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$' -or [string]::IsNullOrEmpty($value)) {
            continue
        }
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if (-not [Environment]::GetEnvironmentVariable($name)) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
    return $true
}

Import-PersistentWindowsEnv -Names @(
    "PRIMARY_LLM_API_KEY",
    "FALLBACK_LLM_API_KEY",
    "SEARCH_AGENT_GATEWAY_API_KEY"
)

$dotEnvPath = Join-Path $root ".env"
if (Import-DotEnv -Path $dotEnvPath) {
    Write-Host "Loaded local environment from .env (existing environment values take precedence)."
}

$hasUpstreamKeys = [bool]($env:PRIMARY_LLM_API_KEY -or $env:FALLBACK_LLM_API_KEY)

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

$gatewayHealthy = Test-SearchAgentHealth -Uri "http://127.0.0.1:4000/healthz"
if ($gatewayHealthy) {
    Write-Host "Gateway already healthy: http://127.0.0.1:4000"
} elseif ($hasUpstreamKeys) {
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
        $gatewayHealthy = $true
        Write-Host "Gateway started (PID $($gateway.Id)): http://127.0.0.1:4000"
} else {
    Write-Host "No upstream model key found; gateway skipped, the quick demo remains available."
}

if ($StartOnline -and -not $gatewayHealthy) {
    throw "Cannot start full online: start the gateway or set PRIMARY_LLM_API_KEY / FALLBACK_LLM_API_KEY."
}

if ($SkipApi) {
    exit 0
}

if (Test-SearchAgentHealth -Uri "http://127.0.0.1:8000/healthz") {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/healthz" -TimeoutSec 2
    if ($null -eq $health.onlineConfigured -or $null -eq $health.offlineBackend) {
        Write-Warning "API already healthy but runs an older build without dual-preset switching. Stop it and rerun this script."
    } else {
        Write-Host "API already healthy: http://127.0.0.1:8000 (mode=$($health.mode), backend=$($health.backend), onlineConfigured=$($health.onlineConfigured))"
    }
    exit 0
}

if ($gatewayHealthy) {
    $env:SEARCH_AGENT_BASE_URL = "http://127.0.0.1:4000/v1"
    $env:SEARCH_AGENT_API_KEY = $env:SEARCH_AGENT_GATEWAY_API_KEY
    $env:SEARCH_AGENT_MODEL = "search-agent-primary"
}
$env:SEARCH_AGENT_DISABLE_THINKING = "1"
$env:SEARCH_AGENT_TIME_BUDGET_SEC = "180"

$apiArgs = @("serve", "--host", "127.0.0.1", "--port", "8000")
if ($StartOnline) {
    Write-Host "Starting the Web API with the full-online preset."
} else {
    $apiArgs += "--offline"
    if ($hasUpstreamKeys) {
        Write-Host "Starting the Web API with the quick demo; switch to full online from the Web console when needed."
    } else {
        Write-Host "Starting the Web API with the quick demo."
    }
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
