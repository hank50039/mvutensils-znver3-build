# MPC Video Renderer Stage Probe (experimental, bounded)

This workflow builds a diagnostic-only x64 MPC Video Renderer from a pinned upstream revision:

- Upstream repository: https://github.com/clsid2/MPCVideoRenderer
- Upstream commit: `e55dd16fda83de20a4b4856538d8efc870ab1cb4`
- Build: Windows x64 Release, Visual Studio MSBuild
- Output DLL: `MpcVideoRenderer64.ax`
- Log: `%LOCALAPPDATA%\MPCVideoRendererWaitProbeDiag.txt`

## What it records

The single text log can contain:

- `WAITPROBE_ENTER/EXIT`: original `WaitForRenderTime()` wait duration, sample timestamp and state snapshots from both before and after the wait, HRESULT, renderer instance pointer, process ID and thread ID. Entry snapshots are taken for every wait; the first 12 full entry records after each segment/flush boundary are captured, while later exit records are kept only for slow waits or failures.
- `RECEIVEPROBE`: total synchronous `Receive()` duration, return phase, sample timestamps from entry and exit, state snapshots, renderer instance pointer, process ID and thread ID, only when slow or failing. Rejected samples may include `scheduler_context`, which distinguishes a renderer late-drop decision from a `GetSampleTimes` or `AdviseTime` scheduling failure.
- `PROCESSPROBE`: total `DoRenderSample()` / video-processor duration, renderer instance pointer, process ID and thread ID.
- `DX11STAGE`: total DX11 `ProcessSample()` duration split into `CopySample()`, first render pass and optional second render pass, with processor instance pointer, process ID and thread ID. `MAPPROBE` and `PRESENTPROBE` also carry these identifiers.
- `MAPPROBE`: individual staging-texture `Map()` calls inside `MemCopyToTexSrcVideo()`, including the plane label, duration and HRESULT.
- `PRESENTPROBE`: DXGI `Present()` duration.

Stage records normally require at least 20 ms or a failing HRESULT. Stalls of at least 100 ms use a separate bounded budget. The counters reset on segment/flush boundaries; normal frames do not produce stage log writes.

## Safety and interpretation

- The patch does not poll `m_RenderEvent` or `m_ThreadSignal`. Polling the auto-reset render event can consume the wake-up needed by the renderer and freeze presentation.
- The patch does not add diagnostic I/O to `Run`, `Pause`, `Stop`, `BeginFlush` or `EndFlush`; those methods only reset atomic sampling budgets at existing segment/flush boundary hooks.
- The original rendering/wait decisions are left intact. Scheduler instrumentation stores context in thread-local memory and appends it to selected existing `RECEIVEPROBE` records without per-frame scheduler file I/O. Timing calls add a small amount of overhead; stage file I/O only occurs on slow or failing calls, so treat exact timings as diagnostics rather than benchmarks.
- This remains an experimental diagnostic build, not an official upstream release. Back up the existing renderer file before testing and restore the original `.ax` afterwards.

Keep PotPlayer, the same 4K source, VapourSynth script, output FPS, buffers, and unrelated settings unchanged during comparison. Do not combine this renderer experiment with changes to the VapourSynth Filter or script.
