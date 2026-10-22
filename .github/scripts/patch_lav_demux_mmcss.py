#!/usr/bin/env python3
"""Audio-sensitive demux producer MMCSS experiment. Zero video worker restrictions."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_lav_demux_mmcss.py <LAVFilters source root>")
root = Path(sys.argv[1])
path = root / "demuxer/LAVSplitter/LAVSplitter.cpp"
src = path.read_text(encoding="utf-8-sig")

def replace_once(old, new, label):
    global src
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one original anchor, found {n}")
    src = src.replace(old, new, 1)

replace_once('#include "IGraphRebuildDelegate.h"', '''#include "IGraphRebuildDelegate.h"
#include <avrt.h>
#pragma comment(lib, "Avrt.lib")

// The demuxer is the upstream packet producer for both video and audio.
// Schedule this lightweight producer as an MMCSS Audio task so audio
// packets can be supplied even under full 32-thread video interpolation.
// Do NOT change any VapourSynth, video decoder, video output pin, thread
// affinity, priorities or frame paths. Gracefully fall back if MMCSS fails.
namespace {
class ScopedDemuxAudioProducerMMCSS final {
    HANDLE taskHandle_ = nullptr;
public:
    ScopedDemuxAudioProducerMMCSS() noexcept {
        DWORD taskIndex = 0;
        taskHandle_ = AvSetMmThreadCharacteristicsW(L"Audio", &taskIndex);
        if (taskHandle_)
            AvSetMmThreadPriority(taskHandle_, AVRT_PRIORITY_NORMAL);
    }
    ~ScopedDemuxAudioProducerMMCSS() noexcept {
        if (taskHandle_) AvRevertMmThreadCharacteristics(taskHandle_);
    }
    ScopedDemuxAudioProducerMMCSS(const ScopedDemuxAudioProducerMMCSS&) = delete;
    ScopedDemuxAudioProducerMMCSS& operator=(const ScopedDemuxAudioProducerMMCSS&) = delete;
};
}
''', "MMCSS helper")
replace_once('''    SetThreadName(-1, "CLAVSplitter Demux");

    m_pDemuxer->Start();''', '''    SetThreadName(-1, "CLAVSplitter Demux");

    // Audio packet delivery begins with this demux producer, not just OutputPin.
    ScopedDemuxAudioProducerMMCSS scopedAudioDemuxMMCSS;

    m_pDemuxer->Start();''', "register original demux worker")
assert src.count('ScopedDemuxAudioProducerMMCSS scopedAudioDemuxMMCSS;') == 1
assert src.count('AvSetMmThreadCharacteristicsW(L"Audio"') == 1
assert 'AvRevertMmThreadCharacteristics' in src
assert 'SetThreadAffinityMask' not in src
assert 'SetThreadPriority' not in src
path.write_text(src, encoding="utf-8-sig")
print("Added Audio MMCSS to demux producer; audio OutputPin patch retained separately. No changes to video processing or worker resource constraints.")
