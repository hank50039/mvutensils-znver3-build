# MPC Video Renderer Wait Probe (experimental)

This repository workflow builds a **diagnostic-only** x64 MPC Video Renderer from the pinned upstream revision:

- Upstream repository: https://github.com/clsid2/MPCVideoRenderer
- Upstream commit: `e55dd16fda83de20a4b4856538d8efc870ab1cb4`
- Build configuration: Windows x64 Release, Visual Studio 2022
- Changes: adds high-resolution timing around `WaitForRenderTime()`, captures sample timestamps, reference clock, calculated target-time delta, renderer/filter state and event handles before/after the wait, and logs Run/Pause/Stop/Flush/NewSegment state transitions.
- It does not intentionally change frame scheduling, rendering choices, sample timestamps, or color handling.

## Output

After installing this experimental renderer and reproducing the existing 4K/90 FPS stutter, it writes:

`%LOCALAPPDATA%\MPCVideoRendererWaitProbeDiag.txt`

The file records waits lasting at least 20 ms, plus any failed wait. A `STATE` row is written around Run/Pause/Stop/Flush/NewSegment events. The existing diagnostic logs remain available.

## Test safety

This is not an official MPC Video Renderer release. Keep a backup of the currently registered x64 renderer file, close PotPlayer before replacement, replace only `MpcVideoRenderer64.ax`, and restore the backup after collecting logs. Do not replace the VapourSynth filter for this test; keep the BT.709 Fast Path, Cap-28, 4K source, 90 FPS and buffers at 100 unchanged.

## Source and license

The source change is produced by `tools/apply_mpcvr_wait_probe.py` against the upstream commit listed above. The release contains the patch diff and source-reproduction script; MPC Video Renderer is GPL-3.0-or-later, and the upstream source is available at the link above. This is an experimental build, not endorsed by the upstream maintainers.
