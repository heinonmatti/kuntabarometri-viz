# Deploy the static dashboard to Cloudflare Pages.
#
# Prerequisites (one-off):
#   1. Run `npx wrangler@latest login` and approve in browser.
#   2. The mattiheino.com zone must already exist in your Cloudflare account.
#
# After this script succeeds, add the custom domain in the Cloudflare dashboard:
#   Workers & Pages -> kuntabarometri-mattiheino -> Custom domains -> Set up
#     domain: kuntabarometri.mattiheino.com
#   Cloudflare will auto-create the CNAME because mattiheino.com is on
#   Cloudflare DNS.

param(
    [string]$ProjectName = 'kuntabarometri-mattiheino',
    [string]$BranchName  = 'main'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$siteDir  = Join-Path $repoRoot 'site'

if (-not (Test-Path (Join-Path $siteDir 'index.html'))) {
    throw "No site\index.html. Run: python -m src.run_all --only charts; then copy output\html\kuntabarometri.html to site\index.html"
}

Write-Host "Deploying $siteDir to Cloudflare Pages project $ProjectName (branch $BranchName)..."
npx --yes wrangler@latest pages deploy $siteDir `
    --project-name $ProjectName `
    --branch $BranchName `
    --commit-dirty=true

Write-Host ''
Write-Host 'NEXT STEP: configure custom domain'
Write-Host '  1. Open https://dash.cloudflare.com -> Workers & Pages'
Write-Host "  2. Click '$ProjectName' -> Custom domains -> Set up custom domain"
Write-Host '  3. Enter: kuntabarometri.mattiheino.com'
Write-Host '  4. Confirm. Cloudflare auto-creates the CNAME on mattiheino.com.'
