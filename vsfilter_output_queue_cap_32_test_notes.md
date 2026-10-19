# VapourSynth Filter experiment: cap queued output frames at 32

Target source commit: `a07ccda967261832233185f1e1d9979ba12593c0` (VapourSynth Filter 1.4.10 # a07ccda).

This is a comparison build to the cap-16 version. It limits the output map (pending async requests plus ready frames not yet delivered downstream) to 32 entries. No MVTools script, output FPS, source resolution, configured buffers, or renderer settings are changed.

Build status will be marked by the workflow artifact. Even if compilation succeeds, only playback comparison determines whether 32 is better.

## Controlled test

Use PotPlayer and keep the same 4K source, script, renderer, 90 FPS output and buffer settings:
- `InitialSrcBuffer=100`
- `MinExtraSrcBuffer=100`
- `MaxExtraSrcBuffer=100`

Compare original build, cap 16, and cap 32 where possible. For each, repeat a cold playback start and the same problematic seek three times. Capture VapourSynth Filter, MPC Video Renderer and DX11 logs.

Record:
1. whether the picture becomes smooth after a few seconds or keeps freezing/jumping;
2. seconds of startup/seek stutter;
3. CPU utilization during the stutter;
4. throttle log entries and output queue sizes;
5. long-tail `req_to_ready_us`, `downstream_deliver_us`, and Renderer `total_receive_us`;
6. steady-state playback smoothness.

Interpretation:
- If cap 32 fixes the repeated stop/jump where cap 16 did not, cap 16 likely under-buffered variable-cost segments.
- If both caps stay choppy, the output-request cap alone is not enough; use the new log to identify whether frame generation/ready times or downstream delivery is causing the starvation.
- If cap 32 reintroduces sustained CPU saturation, the useful limit may be between 16 and 32, or concurrency/bottleneck needs a different fix.
