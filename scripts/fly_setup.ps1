# Creates the two Fly apps and loads secrets from .env. Prints secret NAMES only, never values.
#
#   .\scripts\fly_setup.ps1 -Prefix myname          # real run
#   .\scripts\fly_setup.ps1 -Prefix myname -DryRun  # show what would happen, change nothing
#
# App names must be unique across all of Fly, so choose a prefix of your own (letters, digits, dashes).
param(
  [Parameter(Mandatory = $true)][string]$Prefix,
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$fly = if (Get-Command fly -ErrorAction SilentlyContinue) { "fly" } else { Join-Path $env:USERPROFILE ".fly\bin\flyctl.exe" }
$envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
if (-not (Test-Path $envFile)) { throw ".env not found at $envFile" }

# Read NAME=value lines; skip comments and empty values; strip surrounding quotes.
$values = @{}
foreach ($line in Get-Content $envFile) {
  if ($line -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
    $v = $Matches[2]
    if ($v.Length -ge 2 -and $v[0] -eq $v[-1] -and ($v[0] -eq '"' -or $v[0] -eq "'")) { $v = $v.Substring(1, $v.Length - 2) }
    if ($v) { $values[$Matches[1]] = $v }
  }
}

$required = @("DATABASE_URL", "REDIS_URL", "GEMINI_API_KEY")
$apiOnly = @("API_KEY_PEPPER")
$optional = @("OPENAI_API_KEY", "SENTRY_DSN", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")
foreach ($n in $required + $apiOnly) {
  if (-not $values.ContainsKey($n)) { throw "$n is empty in .env. Fill it in first." }
}
if ($values["DATABASE_URL"] -notlike "postgresql+psycopg://*") { throw "DATABASE_URL must start with postgresql+psycopg://" }
if ($values["REDIS_URL"] -notmatch '^rediss?://') { throw "REDIS_URL must start with redis:// or rediss://" }

function Push-Secrets([string]$app, [string[]]$names) {
  $present = $names | Where-Object { $values.ContainsKey($_) }
  Write-Host "  secrets for ${app}: $($present -join ', ')"
  if ($DryRun) { return }
  $payload = ($present | ForEach-Object { "$_=$($values[$_])" }) -join "`n"
  $payload | & $fly secrets import --stage -a $app | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "fly secrets import failed for $app" }
}

foreach ($app in @("$Prefix-api", "$Prefix-worker")) {
  Write-Host "App: $app"
  if (-not $DryRun) {
    & $fly apps create $app
    if ($LASTEXITCODE -ne 0) { throw "Could not create $app (the name may already be taken: try another -Prefix)" }
  }
}
Push-Secrets "$Prefix-api" ($required + $apiOnly + $optional)
Push-Secrets "$Prefix-worker" ($required + $optional)

Write-Host ""
Write-Host $(if ($DryRun) { "Dry run only: nothing was changed." } else { "Done. Next:" })
Write-Host "  fly deploy --config fly.api.toml -a $Prefix-api"
Write-Host "  fly deploy --config fly.worker.toml -a $Prefix-worker"
