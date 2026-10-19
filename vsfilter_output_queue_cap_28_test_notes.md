# VapourSynth Filter experiment: cap queued output frames at 28

Target source commit: `a07ccda967261832233185f1e1d9979ba12593c0` (VapourSynth Filter 1.4.10 # a07ccda).

This experiment caps the output map (pending asynchronous requests plus output frames ready but not yet delivered downstream) at 28 entries. The only functional change versus the cap-16 baseline patch is the limit value. The MVTools script, picture quality, source resolution, output FPS, renderer, and configured buffers remain unchanged.

## Controlled test

Use PotPlayer with the same 4K video and the same known-problematic startup/seek locations. Keep:
- output FPS: 90
- `InitialSrcBuffer=100`
- `MinExtraSrcBuffer=100`
- `MaxExtraSrcBuffer=100`
- same interpolation script, renderer and playback settings

Compare cap 24, cap 28, and cap 32 when practical. Repeat the same startup and seek locations three times for each build. Record:
1. whether repeated stop-and-jump occurs or playback becomes smooth;
2. time from startup/seek until playback is continuously smooth;
3. CPU utilization while recovering;
4. whether the throttle log shows the cap was reached;
5. `req_to_ready_us`, `downstream_deliver_us`, and Renderer `total_receive_us`;
6. steady-state smoothness.

Interpretation:
- If 28 behaves like 32 but keeps CPU lower, it may be a better compromise.
- If 28 behaves like 24 and still exhibits stalls, cap 28 may be too restrictive for those segments.
- If the outcomes vary by segment, keep the exact same seek points and compare the corresponding logs.

A successful build confirms only that the test binary compiled; actual performance must still be checked in PotPlayer.
