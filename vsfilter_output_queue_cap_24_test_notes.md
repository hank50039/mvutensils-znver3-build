# VapourSynth Filter experiment: cap queued output frames at 24

Target source commit: `a07ccda967261832233185f1e1d9979ba12593c0` (VapourSynth Filter 1.4.10 # a07ccda).

This experiment caps the output map (pending asynchronous requests plus output frames ready but not yet delivered downstream) at 24 entries. Compared to cap 16 this allows more work in flight; compared to cap 32 it applies a tighter limit. Only the cap value changes: the MVTools script, picture quality, source resolution, 90 FPS output, renderer and configured buffers remain unchanged.

## Controlled test

Use PotPlayer with the same 4K video and repeat the same startup/seek locations. Keep:
- output FPS: 90
- `InitialSrcBuffer=100`
- `MinExtraSrcBuffer=100`
- `MaxExtraSrcBuffer=100`
- same interpolation script, renderer and other playback settings

Compare cap 16, cap 24 and cap 32 if practical. For each version, repeat the same problematic startup/seek three times. Record:
1. whether repeated stop-and-jump occurs, or playback becomes smooth;
2. time from startup/seek until smooth playback;
3. CPU utilization while recovering;
4. whether the throttle log indicates the cap was reached;
5. `req_to_ready_us`, `downstream_deliver_us` and Renderer `total_receive_us`;
6. steady-state smoothness.

Interpretation:
- If 24 behaves like 32 but keeps CPU lower, it may be a better compromise than 32.
- If 24 behaves like 16, 24 may still restrict output supply for those segments.
- If the outcomes vary by segment, keep the same exact seek points and compare logs rather than changing other parameters at the same time.

This build being successful does not establish performance until tested in PotPlayer.
