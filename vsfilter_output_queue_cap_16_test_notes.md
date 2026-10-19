# VapourSynth Filter experiment: cap queued output frames at 16

Target source commit: `a07ccda967261832233185f1e1d9979ba12593c0` (VapourSynth Filter 1.4.10 # a07ccda).

The experiment caps `_outputFrames` at 16 entries. This map tracks pending async requests and frames that are ready but have not finished downstream delivery. The patch does not change the MVTools script, interpolation quality, source resolution, output FPS, or configured source buffers.

The patch also:
- logs once per flush cycle when the cap first engages;
- reads the logged output queue size while holding `_outputMutex`, avoiding an unsynchronized read during concurrent queue changes.

The patch has not been compiled or playback-tested yet.

## Controlled test

Use the patched x64 Release build in PotPlayer, keeping the same 4K source, script, renderer, 90 FPS output, and buffer settings:
- `InitialSrcBuffer=100`
- `MinExtraSrcBuffer=100`
- `MaxExtraSrcBuffer=100`

Compare the unmodified build against cap 16. Repeat a cold playback start and a seek three times each. Capture the VapourSynth Filter, MPC Video Renderer, and DX11 logs. Compare:
1. time from script reload/first output-ready frame to stable playback;
2. duration of CPU usage at 100% during startup and seek;
3. presence of the `Throttle output requests ... limit 16 reached` log;
4. output queue sizes;
5. long-tail `req_to_ready_us`, `downstream_deliver_us`, and Renderer `total_receive_us`;
6. steady-state smoothness at 90 FPS.

If cap 16 improves startup/seek without harming steady playback, build variants at 32 and 8 for comparison. A lower queue count alone is not proof of improvement.
