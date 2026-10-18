$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force dist | Out-Null

Write-Host "Cloning upstream VapourSynth Filter..."
git clone https://github.com/CrendKing/avisynth_filter.git upstream
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

git -C upstream checkout a07ccda967261832233185f1e1d9979ba12593c0
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$sha = git -C upstream rev-parse HEAD
"UPSTREAM_FILTER_COMMIT=$sha" >> $env:GITHUB_ENV
Write-Host "Pinned upstream commit: $sha"

Write-Host "Applying frame-pacing timing instrumentation..."
python scripts/patch_vapoursynth_frame_pacing.py upstream
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

git -C upstream diff --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
git -C upstream diff --stat

Write-Host "Building Release x64 filter..."
pwsh -File upstream/build.ps1 -Configuration Release -Platform x64
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$filter = Get-ChildItem upstream/x64/Release -Filter "vapoursynth_filter*.ax" | Select-Object -First 1
if (-not $filter) {
    Get-ChildItem upstream/x64/Release
    throw "Could not find the built VapourSynth DirectShow filter .ax file."
}
Copy-Item $filter.FullName dist/

$pdb = Get-ChildItem upstream/x64/Release -Filter "vapoursynth_filter*.pdb" | Select-Object -First 1
if ($pdb) { Copy-Item $pdb.FullName dist/ }

@"
Diagnostic build: VapourSynth Filter x64
Upstream repository: https://github.com/CrendKing/avisynth_filter
Upstream commit: $sha
Modification: per-frame timings for request-to-ready, ready-to-worker, output sample preparation, downstream Deliver, inter-delivery interval, and pending/ready queue depths.
No Queue Cap was added. This build is for diagnosis, not a performance-tuned release.
Log marker: VPDIAG
"@ | Set-Content -Encoding utf8 dist/BUILD-INFO.txt

Get-ChildItem dist | Format-Table Name,Length
