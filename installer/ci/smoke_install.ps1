# Install the built Setup .exe silently on a CI Windows runner, check what it
# did, install it again (upgrade path), uninstall it, and check what is left.
# Run by .github/workflows/installer.yml; needs an elevated shell (CI runners are).
#
#   pwsh installer/ci/smoke_install.ps1 -Setup installer\output\BajaWorker-Setup-x.exe
#
# The first-run step (tailscale up) is skipped in silent installs and needs a
# real enrollment token, so it is not covered here.

param([Parameter(Mandatory = $true)] [string] $Setup)

$ErrorActionPreference = "Stop"
$AppDir = "C:\Program Files\BajaWorker"
$DataDir = "C:\BajaWorker"
$RuleNames = @("Baja Worker file server (TCP 8710)", "Baja Worker studio receiver (TCP 8720)")

function Fail([string] $Message) {
    Write-Host "::error::$Message"
    foreach ($log in Get-ChildItem "$env:RUNNER_TEMP\setup-*.log", "$DataDir\logs\*.log" -ErrorAction SilentlyContinue) {
        Write-Host "----- $($log.FullName) (last 60 lines)"
        Get-Content $log.FullName -Tail 60
    }
    exit 1
}

function Wait-Until([scriptblock] $Condition, [int] $Seconds, [string] $What) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while (-not (& $Condition)) {
        if ((Get-Date) -gt $deadline) { Fail "timed out after $Seconds s waiting for: $What" }
        Start-Sleep -Seconds 2
    }
}

function Install-Once([string] $LogName) {
    $log = "$env:RUNNER_TEMP\$LogName"
    $process = Start-Process -FilePath $Setup -PassThru -Wait `
        -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$log`""
    if ($process.ExitCode -ne 0) { Fail "Setup exited with $($process.ExitCode) (see $LogName)" }
}

function Assert-Installed {
    foreach ($file in "BajaWorker.exe", "nssm.exe", "ffmpeg\ffmpeg.exe", "ffmpeg\ffprobe.exe", "unins000.exe") {
        if (-not (Test-Path "$AppDir\$file")) { Fail "missing $AppDir\$file" }
    }
    if (-not (Test-Path "C:\Program Files\Tailscale\tailscale.exe")) { Fail "Tailscale was not installed" }

    $service = Get-Service -Name BajaWorker -ErrorAction SilentlyContinue
    if (-not $service) { Fail "service BajaWorker is not registered" }
    if ($service.StartType -ne "Automatic") { Fail "service start type is $($service.StartType)" }
    Wait-Until { (Get-Service BajaWorker).Status -eq "Running" } 60 "service Running"

    # Not enrolled yet, so the worker serves the setup form on 8700.
    Wait-Until {
        try { (Invoke-WebRequest "http://127.0.0.1:8700/" -UseBasicParsing -TimeoutSec 5).Content -match "Baja worker setup" }
        catch { $false }
    } 90 "setup page on http://127.0.0.1:8700"

    $config = Get-Content "$DataDir\worker.json" -Raw | ConvertFrom-Json
    if ($config.ffmpeg_path -ne "$AppDir\ffmpeg\ffmpeg.exe") { Fail "worker.json ffmpeg_path is $($config.ffmpeg_path)" }
    if (-not (Test-Path "$DataDir\logs\worker.log")) { Fail "no $DataDir\logs\worker.log" }

    # Only SYSTEM and Administrators, and nothing inherited from C:\.
    $acl = Get-Acl $DataDir
    if (-not $acl.AreAccessRulesProtected) { Fail "$DataDir still inherits permissions" }
    $sids = $acl.Access | ForEach-Object {
        $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
    } | Sort-Object -Unique
    if (($sids -join ",") -ne "S-1-5-18,S-1-5-32-544") { Fail "unexpected ACL on ${DataDir}: $($sids -join ',')" }

    foreach ($name in $RuleNames) {
        $rules = @(Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)
        if ($rules.Count -ne 1) { Fail "expected 1 firewall rule '$name', found $($rules.Count)" }
        if ($rules[0].Direction -ne "Inbound" -or $rules[0].Action -ne "Allow") { Fail "rule '$name' is not inbound allow" }
        if ($rules[0].Profile -ne "Any") { Fail "rule '$name' profile is $($rules[0].Profile)" }
        $port = ($rules[0] | Get-NetFirewallPortFilter).LocalPort
        if ($name -notmatch "TCP $port\)") { Fail "rule '$name' opens port $port" }
    }
    $exe = "$AppDir\BajaWorker.exe"
    & $exe --check
    if ($LASTEXITCODE -ne 0) { Fail "installed BajaWorker.exe --check failed" }
}

Write-Host "== first install"
Install-Once "setup-install.log"
Assert-Installed

Write-Host "== install again (upgrade path: service re-registered, no duplicate rules)"
Install-Once "setup-upgrade.log"
Assert-Installed

Write-Host "== uninstall"
# unins000.exe hands over to a copy of itself in %TEMP% and exits, so wait
# for its effects instead of for the process.
Start-Process -FilePath "$AppDir\unins000.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
Wait-Until { -not (Test-Path "$AppDir\BajaWorker.exe") } 180 "program files removed"
Wait-Until { -not (Get-Service -Name BajaWorker -ErrorAction SilentlyContinue) } 60 "service removed"
foreach ($name in $RuleNames) {
    if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) { Fail "firewall rule '$name' is still there" }
}
if (-not (Test-Path "$DataDir\worker.json")) { Fail "uninstall removed $DataDir (it must keep the raws)" }

Write-Host "installer smoke test passed"
