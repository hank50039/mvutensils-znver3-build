# Cap-28 diagnostic build: trace DirectShow dynamic media-type changes

This build preserves the cap-28 output queue behavior and adds one diagnostic record whenever the upstream delivers a sample carrying a dynamic media type.

The new line begins with `DYNAMIC_MT` and records:
- sample start/stop timestamps and whether the sample is marked discontinuous;
- whether core media-type header fields are unchanged (`same_header`);
- whether the format block's bytes are unchanged (`same_format_block`);
- old/new major type, subtype, format type, format-block size and sample size.

No media-type behavior is changed by this diagnostic build: it logs the comparison and then follows the original reconfiguration path. This is intentional; first we need to establish whether the upstream is sending a redundant media-type notification or a genuine format-block change at the problematic position.

## What to test

Keep PotPlayer settings unchanged (cap 28, 90 FPS, same 4K file, same MVTools script and buffers). Start playback from the position that previously made the picture freeze at the target segment. Let it pass the target and then close PotPlayer so the log is flushed.

Upload the resulting `VapourSynthFilter.txt` plus the matching MPC Video Renderer and schedule logs. Inspect the `DYNAMIC_MT` line around the stall:
- `same_header=1 same_format_block=1` means the upstream attached a media type effectively identical to the previous one, while this filter still forced StopStreaming/reconfiguration.
- If either is 0, the trace shows that some media-type metadata really did differ; the old/new detailed fields will narrow the next investigation.
- Compare the `DYNAMIC_MT start` timestamp against `Output frame: frameNb 0 startTime` and the MPC schedule's `scheduled_ahead_100ns`.

This is a diagnostic binary, not a fix, and should be used only for this test. The original cap-28 binary remains the preferred playback version until we verify the cause.
