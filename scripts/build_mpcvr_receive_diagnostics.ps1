$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force dist | Out-Null

$repoUrl = "https://github.com/clsid2/MPCVideoRenderer.git"
$sourceCommit = "e55dd16fda83de20a4b4856538d8efc870ab1cb4"

Write-Host "Cloning MPC Video Renderer..."
git clone $repoUrl MPCVideoRenderer
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

git -C MPCVideoRenderer checkout $sourceCommit
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

git -C MPCVideoRenderer submodule update --init --recursive
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Applying slow-Receive diagnostic instrumentation..."
python scripts/patch_mpcvr_receive_diagnostics.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Applying nested D3D11 render-stage instrumentation..."
python scripts/patch_mpcvr_dx11_render_diagnostics.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Upstream contains mixed line endings/trailing spaces; build validation below is the meaningful check.
git -C MPCVideoRenderer diff --stat

Write-Host "Building MPC Video Renderer x64 (upstream build script also builds x86)..."
Push-Location MPCVideoRenderer
try {
    cmd.exe /c "build_mpcvr.cmd NoWait"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

$filter = "MPCVideoRenderer\MpcVideoRenderer64.ax"
if (-not (Test-Path $filter)) {
    $filter = "MPCVideoRenderer\_bin\Filter_x64\MpcVideoRenderer64.ax"
}
if (-not (Test-Path $filter)) {
    $found = Get-ChildItem MPCVideoRenderer -Recurse -Filter "MpcVideoRenderer64.ax" | Select-Object -First 1
    if (-not $found) {
        Get-ChildItem MPCVideoRenderer -Recurse -Filter "*.ax" | Select-Object FullName,Length | Format-Table
        throw "Could not find MpcVideoRenderer64.ax after the build."
    }
    $filter = $found.FullName
}
Copy-Item $filter dist/MpcVideoRenderer64.ax

$pdb = Get-ChildItem MPCVideoRenderer -Recurse -Filter "MpcVideoRenderer64.pdb" | Select-Object -First 1
if ($pdb) { Copy-Item $pdb.FullName dist/ }

@"
MPC Video Renderer nested rendering diagnostics build (x64)
Upstream repository: https://github.com/clsid2/MPCVideoRenderer
Pinned upstream commit: $sourceCommit
Instrumentation A: MRDIAG entries for Receive calls taking at least 20 ms.
Log A: %LOCALAPPDATA%\MPCVideoRendererDiag.log
Instrumentation B: DX11DIAG entries for ProcessSample/Render stages taking at least 20 ms.
Log B: %LOCALAPPDATA%\MPCVideoRendererDX11Diag.log
DX11DIAG separates sample copying, internal Render, Process pipeline, vertical-blank wait, frame-time sync, and Present.
This is an experimental diagnostic build, not an official release.
"@ | Set-Content -Encoding utf8 dist/BUILD-INFO.txt

@"
Test instructions:
1. Close PotPlayer and any application using MPC Video Renderer.
2. Back up the current registered x64 renderer file before replacing it.
3. Replace only the existing MpcVideoRenderer64.ax with the supplied file, keeping the same installed path and filename.
4. Keep the same VapourSynth script/settings; play the same section and reproduce one startup stutter plus one seek stutter.
5. Close PotPlayer and attach all three logs:
   - %LOCALAPPDATA%\MPCVideoRendererDiag.log
   - %LOCALAPPDATA%\MPCVideoRendererDX11Diag.log
   - the new VapourSynthFilter.log
6. Restore the original renderer file after testing.

The build is based on upstream commit $sourceCommit. It only writes diagnostic records for slow calls/stages (20 ms threshold) to limit logging overhead. This is not an official release; restore the original .ax after testing.
"@ | Set-Content -Encoding utf8 dist/INSTALL-TEST.txt

Compress-Archive -Path dist/MpcVideoRenderer64.ax,dist/BUILD-INFO.txt,dist/INSTALL-TEST.txt -DestinationPath dist/MPCVideoRenderer-Deep-Render-Diagnostics-Test.zip -Force
Get-ChildItem dist | Format-Table Name,Length
