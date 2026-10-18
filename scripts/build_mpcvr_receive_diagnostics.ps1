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

git -C MPCVideoRenderer diff --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
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
MPC Video Renderer Receive timing diagnostic build (x64)
Upstream repository: https://github.com/clsid2/MPCVideoRenderer
Pinned upstream commit: $sourceCommit
Instrumentation: logs only Receive calls taking 20 ms or longer.
Log path: %LOCALAPPDATA%\MPCVideoRendererDiag.log
Log marker: MRDIAG
The MRDIAG record separates PrepareReceive, WaitForRenderTime, paused processing, Render, and other Receive overhead.
This is an experimental diagnostic build, not an official release.
"@ | Set-Content -Encoding utf8 dist/BUILD-INFO.txt

@"
Test instructions:
1. Close PotPlayer and any application using MPC Video Renderer.
2. Back up the current registered x64 renderer file before replacing it.
3. Replace only the existing MpcVideoRenderer64.ax with the supplied file, keeping the same installed path and filename.
4. Reproduce start-playback and one seek stutter once.
5. Close PotPlayer and attach %LOCALAPPDATA%\MPCVideoRendererDiag.log along with the existing VapourSynthFilter log.
6. Restore the original renderer file after testing.

The test build is based on upstream commit $sourceCommit. It logs only Receive calls whose total duration is at least 20 ms to minimize logging overhead. Restore the original .ax after this experiment.
"@ | Set-Content -Encoding utf8 dist/INSTALL-TEST.txt

Compress-Archive -Path dist/MpcVideoRenderer64.ax,dist/BUILD-INFO.txt,dist/INSTALL-TEST.txt -DestinationPath dist/MPCVideoRenderer-Receive-Diagnostics-Test.zip -Force
Get-ChildItem dist | Format-Table Name,Length
