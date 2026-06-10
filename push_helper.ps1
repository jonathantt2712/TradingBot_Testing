# push_helper.ps1 - clone repo, overlay workspace files, commit, push
$ErrorActionPreference = "Stop"

$repoUrl   = "https://github.com/itaitoker64/tradingbot2026.git"
$branch    = "main"
$workspace = "C:\Users\itait\Claude\Projects\trading bot"
$tmpDir    = Join-Path $env:TEMP "tradingbot_push_tmp"
$cloneDir  = Join-Path $tmpDir "repo"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm"
$commitMsg = "feat: equity flow agent, stale-data guard, improved keywords, RISK_OFF cooldown [$timestamp]"

if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

try {
    Write-Host "[1/5] Cloning $repoUrl (branch: $branch)..."
    git clone --branch $branch --depth 1 $repoUrl $cloneDir
    if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE)" }

    Write-Host "[2/5] Copying folders..."

    # Copy trading_bot (exclude secrets and cache)
    $src = Join-Path $workspace "trading_bot"
    $dst = Join-Path $cloneDir "trading_bot"
    if (-not (Test-Path $src)) { throw "Source not found: $src" }
    if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    Get-ChildItem $src -Force | Where-Object {
        $_.Name -notin @('.env', '__pycache__')
    } | ForEach-Object {
        if ($_.Name -eq 'data') {
            # Copy data/ source files (.py) but NOT runtime JSON files
            $dataDst = Join-Path $dst 'data'
            New-Item -ItemType Directory -Path $dataDst -Force | Out-Null
            Get-ChildItem $_.FullName -Force | Where-Object {
                $_.Extension -eq '.py' -and $_.Name -ne '__pycache__'
            } | ForEach-Object {
                Copy-Item $_.FullName (Join-Path $dataDst $_.Name) -Force
            }
        } else {
            Copy-Item $_.FullName (Join-Path $dst $_.Name) -Recurse -Force
        }
    }
    Write-Host "  copied: trading_bot (excluded .env, __pycache__, data/*.json)"

    # Copy trading-dashboard (exclude secrets and build artifacts)
    $dashSrc = Join-Path $workspace "trading-dashboard"
    $dashDst = Join-Path $cloneDir "trading-dashboard"
    if (Test-Path $dashSrc) {
        if (Test-Path $dashDst) { Remove-Item $dashDst -Recurse -Force }
        New-Item -ItemType Directory -Path $dashDst -Force | Out-Null
        Get-ChildItem $dashSrc -Force | Where-Object {
            $_.Name -notin @('node_modules', '.env.local', '.env', '.next', '__pycache__')
        } | ForEach-Object {
            Copy-Item $_.FullName (Join-Path $dashDst $_.Name) -Recurse -Force
        }
        Write-Host "  copied: trading-dashboard (excluded node_modules, .env*, .next)"
    }

    $envSrc = Join-Path $workspace ".env.example"
    if (Test-Path $envSrc) {
        Copy-Item $envSrc (Join-Path $cloneDir ".env.example") -Force
        Write-Host "  copied: .env.example"
    }

    Write-Host "[3/5] Staging changes..."
    Set-Location $cloneDir
    git add .
    if ($LASTEXITCODE -ne 0) { throw "git add failed" }

    $status = git status --short
    if (-not $status) {
        Write-Host "Nothing to commit - already up to date."
        exit 0
    }
    Write-Host $status

    Write-Host "[4/5] Committing..."
    git -c user.email="tokeraaiig@gmail.com" -c user.name="itaitoker64" commit -m $commitMsg
    if ($LASTEXITCODE -ne 0) { throw "git commit failed" }

    Write-Host "[5/5] Pushing to origin/$branch..."
    git push origin $branch
    if ($LASTEXITCODE -ne 0) { throw "git push failed (exit $LASTEXITCODE)" }

    Write-Host ""
    Write-Host "Done! Push successful."
}
catch {
    Write-Host "ERROR: $_" -ForegroundColor Red
    exit 1
}
finally {
    Set-Location $workspace
    Start-Sleep -Milliseconds 500
    if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue }
}
