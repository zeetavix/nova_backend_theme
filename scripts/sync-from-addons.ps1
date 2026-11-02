# Mirror the three theme addons from a working Odoo addons path into this
# repo, so the repository is what gets pushed and the addons path stays the
# place you develop in.
#
# Usage:
#   .\scripts\sync-from-addons.ps1 -Source "C:\path\to\custom_addons"
#
param(
    [Parameter(Mandatory = $true)]
    [string]$Source
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$modules = @(
    "nova_backend_theme",
    "nova_backend_theme_mail",
    "nova_backend_theme_portal"
)

foreach ($module in $modules) {
    $from = Join-Path $Source $module
    if (-not (Test-Path -LiteralPath $from)) {
        Write-Host "skip (missing): $module"
        continue
    }
    $to = Join-Path $repo $module
    robocopy $from $to /MIR /XD ".git" "__pycache__" ".idea" ".vscode" `
        /XF "*.pyc" "*.pyo" /NFL /NDL /NJH /NJS /NP | Out-Null
    Write-Host "synced: $module"
}

Write-Host "`nNow review and push:"
Write-Host "  git -C `"$repo`" status"
Write-Host "  git -C `"$repo`" add -A"
Write-Host "  git -C `"$repo`" commit -m `"describe the change`""
Write-Host "  git -C `"$repo`" push origin 20.0"
