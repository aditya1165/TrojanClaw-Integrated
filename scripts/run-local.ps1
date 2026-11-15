[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Check,
    [switch]$NoBrowser,
    [switch]$Detached,
    [switch]$SkipSetup
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot '.runtime'
$stateFile = Join-Path $runtimeDir 'local-services.json'
$logDir = Join-Path $runtimeDir 'logs'
$siteUrl = 'http://127.0.0.1:5173/'

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $([IO.Path]::GetFileName($Program)). See its output above." }
}

function Get-OwnedProcess {
    param($Entry)
    $candidate = Get-Process -Id $Entry.id -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.StartTime.ToUniversalTime().Ticks.ToString() -eq $Entry.started) {
        return $candidate
    }
    return $null
}

function Stop-OwnedServices {
    if (!(Test-Path -LiteralPath $stateFile)) { return }
    $saved = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    if ($saved.root -ne $projectRoot) { throw 'The saved launcher state belongs to a different project.' }
    foreach ($entry in $saved.services) {
        $owned = Get-OwnedProcess $entry
        if ($owned) { Stop-Process -Id $owned.Id -ErrorAction SilentlyContinue }
    }
    Remove-Item -LiteralPath $stateFile
}

function Test-Url {
    param([string]$Url, [string]$Contains = '')
    try {
        $reply = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return ($reply.StatusCode -eq 200 -and (!$Contains -or $reply.Content.Contains($Contains)))
    } catch { return $false }
}

function Test-Port {
    param([int]$Port)
    $socket = New-Object Net.Sockets.TcpClient
    try { $socket.Connect('127.0.0.1', $Port); return $true }
    catch { return $false }
    finally { $socket.Dispose() }
}

function Install-Archive {
    param([string]$Url, [string]$ChecksumsUrl, [string]$ArchiveName, [string]$Destination)
    $archive = Join-Path $runtimeDir $ArchiveName
    Write-Host "Downloading $ArchiveName from its official publisher..."
    Invoke-WebRequest -Uri $Url -OutFile $archive -UseBasicParsing
    $checksums = (Invoke-WebRequest -Uri $ChecksumsUrl -UseBasicParsing).Content
    $expected = @($checksums -split "`n" | Where-Object {
        $_ -match [regex]::Escape($ArchiveName)
    })
    if (!$expected.Count -and $checksums.Trim() -match '^[a-fA-F0-9]{64}$') { $expected = @($checksums.Trim()) }
    if ($expected.Count -ne 1 -or $expected[0] -notmatch '([a-fA-F0-9]{64})') {
        throw 'The publisher checksum could not be read. No downloaded program was executed.'
    }
    $expectedHash = $Matches[1]
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expectedHash) {
        throw 'Download checksum verification failed. No downloaded program was executed.'
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $Destination -Force
}

function Find-Uv {
    $found = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    foreach ($path in @((Join-Path $env:USERPROFILE '.local\bin\uv.exe'), (Join-Path $runtimeDir 'uv\uv.exe'))) {
        if (Test-Path -LiteralPath $path) { return $path }
    }
    if ($Check) { throw 'uv is missing. Run run.cmd once to install it.' }
    $asset = 'uv-x86_64-pc-windows-msvc.zip'
    $release = 'https://github.com/astral-sh/uv/releases/download/0.11.16'
    Install-Archive "$release/$asset" "$release/$asset.sha256" $asset (Join-Path $runtimeDir 'uv')
    $binary = Get-ChildItem -LiteralPath (Join-Path $runtimeDir 'uv') -Filter uv.exe -Recurse | Select-Object -First 1
    if (!$binary) { throw 'uv installation did not produce uv.exe.' }
    return $binary.FullName
}

function Find-Node {
    $candidates = @()
    $found = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($found) { $candidates += $found.Source }
    $candidates += @(Get-ChildItem -LiteralPath $runtimeDir -Filter 'node-*-win-x64' -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'node.exe' })
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $version = & $candidate -p 'process.versions.node'
            if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]'22.12.0') { return $candidate }
        }
    }
    if ($Check) { throw 'Node.js 22.12+ is missing. Run run.cmd once to install it.' }
    $version = 'v22.23.5'
    $asset = "node-$version-win-x64.zip"
    $release = "https://nodejs.org/dist/$version"
    Install-Archive "$release/$asset" "$release/SHASUMS256.txt" $asset $runtimeDir
    return Join-Path $runtimeDir "node-$version-win-x64\node.exe"
}

function Sync-Npm {
    param([string]$Folder, [string]$Name)
    $lock = Join-Path $Folder 'package-lock.json'
    $fingerprint = (Get-FileHash -LiteralPath $lock -Algorithm SHA256).Hash
    $marker = Join-Path $runtimeDir "$Name-dependencies.sha256"
    if (!(Test-Path (Join-Path $Folder 'node_modules')) -or !(Test-Path $marker) -or
            (Get-Content -LiteralPath $marker -Raw).Trim() -ne $fingerprint) {
        Invoke-Checked $npm @('ci', '--prefix', $Folder)
        Set-Content -LiteralPath $marker -Value $fingerprint
    }
}

function Start-ServiceProcess {
    param([string]$Name, [string]$Program, [string[]]$Arguments, [string]$Folder)
    # Start-Process joins arguments on Windows, so quote each argument explicitly.
    $quoted = @($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' })
    $process = Start-Process -FilePath $Program -ArgumentList $quoted -WorkingDirectory $Folder `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "$Name.out.log") `
        -RedirectStandardError (Join-Path $logDir "$Name.err.log")
    return @{ name = $Name; id = $process.Id; started = $process.StartTime.ToUniversalTime().Ticks.ToString() }
}

$startedHere = $false
try {
    if ($Stop) {
        Stop-OwnedServices
        Write-Host 'Stopped services started by this launcher.'
        exit 0
    }
    New-Item -ItemType Directory -Path $runtimeDir, $logDir -Force | Out-Null
    $uv = Find-Uv
    $node = Find-Node
    $env:PATH = (Split-Path -Parent $node) + [IO.Path]::PathSeparator + $env:PATH
    $npm = Join-Path (Split-Path -Parent $node) 'npm.cmd'
    $env:UV_CACHE_DIR = Join-Path $projectRoot '.uv-cache'
    $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = '1'
    $env:PUPPETEER_SKIP_DOWNLOAD = 'true'
    $backend = Join-Path $projectRoot 'backend'
    $frontend = Join-Path $projectRoot 'frontend'
    $python = Join-Path $backend '.venv\Scripts\python.exe'
    $envFile = Join-Path $backend '.env'

    if (!(Test-Path -LiteralPath $envFile)) {
        Copy-Item -LiteralPath (Join-Path $backend '.env.example') -Destination $envFile
        throw 'Created backend\.env from the example. Fill in your database and API keys, then run again.'
    }
    if ($Check) {
        Write-Host "Tools found: uv and Node.js. Configuration found: backend\.env."
        Write-Host "Backend environment installed: $(Test-Path -LiteralPath $python)"
        Write-Host 'Run run.cmd to install dependencies, validate settings and start the site.'
        exit 0
    }

    if ((Test-Port 8000) -or (Test-Port 5173)) {
        if ((Test-Url 'http://127.0.0.1:8000/healthcheck') -and (Test-Url $siteUrl 'TrojanClaw')) {
            Write-Host "TrojanClaw is already running: $siteUrl"
            if (!$NoBrowser) { Start-Process $siteUrl }
            exit 0
        }
        throw 'Port 8000 or 5173 is occupied. Stop the other service, or use run.cmd -Stop for services this launcher owns.'
    }

    if (!$SkipSetup) {
        Write-Host 'Preparing Python, frontend and MCP connector dependencies...'
        if (!(Test-Path -LiteralPath $python)) {
            Invoke-Checked $uv @('venv', '--python', '3.11', (Join-Path $backend '.venv'))
        }
        Invoke-Checked $uv @('pip', 'install', '--python', $python, '-r', (Join-Path $backend 'requirements-dev.txt'))
        foreach ($provider in @('handshake', 'piazza')) {
            Invoke-Checked $uv @('sync', '--locked', '--no-dev', '--project', (Join-Path $projectRoot "integrations\$provider"), '--python', '3.12')
        }
        Sync-Npm $frontend 'frontend'
        Sync-Npm (Join-Path $projectRoot 'integrations\brightspace') 'brightspace'
        Sync-Npm (Join-Path $projectRoot 'integrations\google-calendar') 'google-calendar'
    }
    Invoke-Checked $python @((Join-Path $PSScriptRoot 'check-local-config.py'))

    $services = @()
    try {
        $services += Start-ServiceProcess 'backend' $python @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--no-access-log') $backend
        $services += Start-ServiceProcess 'frontend' $node @((Join-Path $frontend 'node_modules\vite\bin\vite.js'), '--host', '127.0.0.1', '--port', '5173', '--strictPort') $frontend
    } finally {
        if ($services.Count) {
            @{ root = $projectRoot; services = $services } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $stateFile
            $startedHere = $true
        }
    }
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        foreach ($entry in $services) {
            if (!(Get-OwnedProcess $entry)) { throw "$($entry.name) stopped. Check .runtime\logs\$($entry.name).err.log." }
        }
        if ((Test-Url 'http://127.0.0.1:8000/healthcheck') -and (Test-Url $siteUrl 'TrojanClaw')) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (!$ready) { throw 'Startup timed out. Check .runtime\logs for details.' }
    Write-Host "`nTrojanClaw is ready: $siteUrl" -ForegroundColor Green
    Write-Host 'Logs: .runtime\logs. Browserbase billing/quotas still apply to remote sign-in.'
    if (!$NoBrowser) { Start-Process $siteUrl }
    if ($Detached) {
        Write-Host 'Running in the background. Use run.cmd -Stop to stop it.'
        $startedHere = $false
        exit 0
    }
    Write-Host 'Keep this window open. Press Ctrl+C to stop both services.'
    while ($true) {
        Start-Sleep -Seconds 1
        foreach ($entry in $services) {
            if (!(Get-OwnedProcess $entry)) { throw "$($entry.name) stopped. Check .runtime\logs." }
        }
    }
} catch {
    Write-Host "`n$($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ($startedHere) { Stop-OwnedServices }
}
