<#
.SYNOPSIS
    Windows task runner - the same targets as the Makefile, for machines
    without GNU make.

.EXAMPLE
    ./make.ps1 up
    ./make.ps1 bootstrap
    ./make.ps1 verify
#>
param(
    [Parameter(Position = 0)]
    [string]$Target = "help"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    & docker compose @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($ComposeArgs -join ' ') failed" }
}

function Invoke-Bash {
    param([string]$Script)
    # Docker Desktop on Windows ships git-bash-compatible tooling; the helper
    # scripts are POSIX sh because they also run inside the containers.
    $bash = Get-Command bash -ErrorAction SilentlyContinue
    if (-not $bash) {
        throw "bash is required for '$Script'. Install Git for Windows, or run the documented docker compose command directly."
    }
    & bash $Script
    if ($LASTEXITCODE -ne 0) { throw "$Script failed" }
}

function New-EnvFile {
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Host "created .env from .env.example"
    }
}

function Get-EnvValue {
    param([string]$Key, [string]$Default)
    if (Test-Path ".env") {
        $line = Select-String -Path ".env" -Pattern "^$Key=" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($line) { return ($line.Line -split "=", 2)[1] }
    }
    return $Default
}

function Set-FiscalFailureMode {
    param([bool]$Enabled)
    $key = Get-EnvValue -Key "GATEWAY_API_KEY" -Default "dev-gateway-api-key-change-me"
    $port = Get-EnvValue -Key "GATEWAY_PORT" -Default "8000"
    $body = @{ enabled = $Enabled } | ConvertTo-Json -Compress
    $response = Invoke-RestMethod -Method Post `
        -Uri "http://localhost:$port/api/v1/admin/mock/fiscal/failure-mode" `
        -Headers @{ "X-API-Key" = $key; "Content-Type" = "application/json" } `
        -Body $body
    Write-Host "mock fiscal provider failure_mode = $($response.failure_mode)"
}

switch ($Target.ToLower()) {
    "help" {
        Write-Host ""
        Write-Host "Mati Retail Platform"
        Write-Host ""
        Write-Host "  up             start postgres, odoo and the gateway"
        Write-Host "  down           stop all services (keeps data)"
        Write-Host "  build          build the images"
        Write-Host "  bootstrap      create and populate the demo database"
        Write-Host "  seed           re-run demo seeding (idempotent)"
        Write-Host "  reset          restore the seeded opening state"
        Write-Host "  demo-reset     full demo restore (re-seed + reset + providers)"
        Write-Host "  verify         run the full end-to-end verification"
        Write-Host "  test           run every test suite"
        Write-Host "  test-gateway   run the gateway test suite"
        Write-Host "  test-odoo      run the Odoo addon tests"
        Write-Host "  logs           tail all logs"
        Write-Host "  ps             show service status"
        Write-Host "  health         check every service health endpoint"
        Write-Host "  fiscal-fail    force the mock fiscal provider to fail"
        Write-Host "  fiscal-ok      restore the mock fiscal provider"
        Write-Host "  nuke           remove containers AND all data (destructive)"
        Write-Host ""
    }
    "env" { New-EnvFile }
    "build" { New-EnvFile; Invoke-Compose @("build") }
    "up" {
        New-EnvFile
        Invoke-Compose @("up", "-d", "--build")
        Write-Host ""
        Write-Host "  Odoo    http://localhost:8069"
        Write-Host "  Gateway http://localhost:8000  (docs at /docs)"
        Write-Host ""
        Write-Host "  Next:  ./make.ps1 bootstrap"
    }
    "down" { Invoke-Compose @("down") }
    "restart" { Invoke-Compose @("restart", "odoo", "gateway") }
    "ps" { Invoke-Compose @("ps") }
    "logs" { Invoke-Compose @("logs", "-f", "--tail=100") }
    "logs-odoo" { Invoke-Compose @("logs", "-f", "--tail=100", "odoo") }
    "logs-gateway" { Invoke-Compose @("logs", "-f", "--tail=100", "gateway") }
    "bootstrap" { Invoke-Bash "./scripts/bootstrap.sh" }
    "seed" { Invoke-Bash "./scripts/seed_demo.sh" }
    "reset" { Invoke-Bash "./scripts/reset_demo.sh" }
    "demo-reset" { Invoke-Bash "./scripts/demo_reset.sh" }
    "verify" { Invoke-Bash "./scripts/verify.sh" }
    "health" { Invoke-Bash "./scripts/health.sh" }
    "test-gateway" {
        Invoke-Compose @("run", "--rm", "--no-deps",
            "-e", "GATEWAY_DATABASE_URL=sqlite+aiosqlite:///./test.db",
            "gateway", "test", "-q")
    }
    "test-odoo" {
        $db = Get-EnvValue -Key "ODOO_DB_NAME" -Default "odoo"
        Invoke-Compose @("run", "--rm", "odoo", "odoo", "-d", $db,
            "--test-enable", "--stop-after-init",
            "--test-tags", "/et_fiscal_odoo,/external_payment_gateway,/external_delivery_gateway,/mati_demo",
            "-u", "et_fiscal_odoo,external_payment_gateway,external_delivery_gateway,mati_demo",
            "--log-level=test")
    }
    "test" { & $PSCommandPath "test-gateway"; & $PSCommandPath "test-odoo" }
    "shell-odoo" {
        $db = Get-EnvValue -Key "ODOO_DB_NAME" -Default "odoo"
        Invoke-Compose @("exec", "odoo", "mati-entrypoint.sh", "odoo", "shell", "-d", $db, "--no-http")
    }
    "shell-gateway" { Invoke-Compose @("exec", "gateway", "/bin/sh") }
    "fiscal-fail" { Set-FiscalFailureMode -Enabled $true }
    "fiscal-ok" { Set-FiscalFailureMode -Enabled $false }
    "clean" { Invoke-Compose @("down", "--remove-orphans") }
    "nuke" {
        Invoke-Compose @("down", "-v", "--remove-orphans")
        Write-Host "all containers and volumes removed; run ./make.ps1 up then ./make.ps1 bootstrap"
    }
    default {
        Write-Host "unknown target '$Target'. Run ./make.ps1 help"
        exit 1
    }
}
