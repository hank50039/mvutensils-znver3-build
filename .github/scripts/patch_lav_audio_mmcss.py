#!/usr/bin/env python3
"""Only patch LAV Splitter audio output thread with Windows MMCSS, no video changes."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_lav_audio_mmcss.py <LAVFilters source root>")
root = Path(sys.argv[1])
path = root / "demuxer/LAVSplitter/OutputPin.cpp"
src = path.read_text(encoding="utf-8-sig")

def one(before, after, label):
    global src
    n = src.count(before)
    if n != 1:
        raise SystemExit(f"{label}: expected 1 anchor, found {n}")
    src = src.replace(before, after, 1)

one('#include "PacketAllocator.h"', '''#include "PacketAllocator.h"
#include <avrt.h>
#pragma comment(lib, "Avrt.lib")

// AUDIO OUTPUT THREAD ONLY. MMCSS is reverted automatically on all paths.
// VIDEO PIN, VapourSynth workers, video timestamps and frame handling untouched.
// If the Audio MMCSS task is unavailable, use the normal default scheduling.
namespace {
class ScopedAudioMMCSS final {
    HANDLE taskHandle_ = nullptr;
public:
    explicit ScopedAudioMMCSS(bool isAudioPin) noexcept {
        if (!isAudioPin) return;
        DWORD taskIndex = 0;
        taskHandle_ = AvSetMmThreadCharacteristicsW(L"Audio", &taskIndex);
        if (taskHandle_)
            AvSetMmThreadPriority(taskHandle_, AVRT_PRIORITY_NORMAL);
    }
    ~ScopedAudioMMCSS() noexcept {
        if (taskHandle_) AvRevertMmThreadCharacteristics(taskHandle_);
    }
    ScopedAudioMMCSS(const ScopedAudioMMCSS&) = delete;
    ScopedAudioMMCSS& operator=(const ScopedAudioMMCSS&) = delete;
};
}
''', "Audio MMCSS helper")
one('''    SetThreadName(-1, name.c_str());

    m_hrDeliver = S_OK;''', '''    SetThreadName(-1, name.c_str());

    // Only prioritize the source audio packet delivery & synchronous decoder.
    // Do not change the video output pin's scheduling or availability.
    ScopedAudioMMCSS audioMMCSS(IsAudioPin());

    m_hrDeliver = S_OK;''', "Audio-only registration")
assert src.count('ScopedAudioMMCSS audioMMCSS(IsAudioPin());') == 1
assert 'AvSetMmThreadCharacteristicsW(L"Audio"' in src
assert 'SetThreadPriority' not in src
assert 'SetThreadAffinityMask' not in src
assert 'AvRevertMmThreadCharacteristics' in src
path.write_text(src, encoding="utf-8-sig")
print("Patched only LAVSplitter/OutputPin.cpp: audio-only MMCSS with RAII automatic revert, no video scheduler or algorithm changes.")
