param(
    [Parameter(Mandatory = $false)]
    [string]$Message,

    [Parameter(Mandatory = $false)]
    [switch]$SkipPull
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Run-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )

    & git @Args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Args -join ' ') failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path ".git")) {
    throw "Run this script from the repository root."
}

if (-not $SkipPull) {
    Write-Host "Fetching latest from origin..."
    Run-Git -Args @("fetch", "origin")

    Write-Host "Rebasing local main on origin/main..."
    Run-Git -Args @("pull", "--rebase", "origin", "main")
}

Write-Host "Staging all changes..."
Run-Git -Args @("add", "-A")

# If nothing is staged, exit cleanly.
$staged = (& git diff --cached --name-only)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect staged files."
}
if (-not $staged) {
    Write-Host "No changes to commit. Repository is already up to date."
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Message)) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm"
    $Message = "chore: update app ($timestamp)"
}

Write-Host "Committing: $Message"
Run-Git -Args @("commit", "-m", $Message)

Write-Host "Pushing to origin/main..."
Run-Git -Args @("push", "origin", "main")

Write-Host "Done. GitHub is updated." -ForegroundColor Green
