from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_mvutensils_affinity.py <upstream-root>")

root = Path(sys.argv[1])
flow = root / "src/FlowFPS.cpp"
analyse = root / "src/Analyse.cpp"
header = root / "src/VideoAffinityScope.h"
if header.exists():
    raise SystemExit("Unexpected existing VideoAffinityScope.h")

header.write_text(r'''#pragma once

// EXPERIMENTAL CPU-isolation A/B for Ryzen 9 5900XT (32 logical processors).
// Keep all 32 VapourSynth threads and all interpolation/pixel calculations.
// Only while expensive Analyse/FlowFPS pixel work executes, keep it off the two
// highest-numbered logical CPUs. LAV audio, WASAPI and all non-VS work remain
// unrestricted. This is a test of CPU scheduling contention, not an optimization.
// Unlike permanently pinning a VS pool worker, affinity is always restored
// when each callback returns, including early returns and C++ exceptions.
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

class ScopedVideoComputeAffinity final {
    DWORD_PTR previousAffinity_ = 0;

public:
    ScopedVideoComputeAffinity() noexcept {
        DWORD_PTR processMask = 0;
        DWORD_PTR systemMask = 0;
        if (!GetProcessAffinityMask(GetCurrentProcess(), &processMask, &systemMask))
            return;

        unsigned available = 0;
        for (unsigned bit = 0; bit < 8 * sizeof(DWORD_PTR); ++bit) {
            if (processMask & (static_cast<DWORD_PTR>(1) << bit))
                ++available;
        }
        // Scope deliberately limited to the user's unmodified 32-thread machine;
        // skip rather than guessing which CPUs to exclude on other hardware.
        if (available != 32)
            return;

        DWORD_PTR reserved = 0;
        unsigned reservedCount = 0;
        for (int bit = 8 * static_cast<int>(sizeof(DWORD_PTR)) - 1; bit >= 0; --bit) {
            const DWORD_PTR cpu = static_cast<DWORD_PTR>(1) << bit;
            if ((processMask & cpu) != 0) {
                reserved |= cpu;
                if (++reservedCount == 2)
                    break;
            }
        }
        const DWORD_PTR videoMask = processMask & ~reserved;
        if (reservedCount == 2 && videoMask != 0)
            previousAffinity_ = SetThreadAffinityMask(GetCurrentThread(), videoMask);
    }

    ~ScopedVideoComputeAffinity() noexcept {
        if (previousAffinity_ != 0)
            SetThreadAffinityMask(GetCurrentThread(), previousAffinity_);
    }

    ScopedVideoComputeAffinity(const ScopedVideoComputeAffinity&) = delete;
    ScopedVideoComputeAffinity& operator=(const ScopedVideoComputeAffinity&) = delete;
};
#else
class ScopedVideoComputeAffinity final {
public:
    ScopedVideoComputeAffinity() noexcept {}
};
#endif
''', encoding="utf-8")

def replace_once(path: Path, old: str, new: str, label: str):
    s = path.read_text(encoding="utf-8")
    hits = s.count(old)
    if hits != 1:
        raise SystemExit(f"{label}: expected 1 occurrence, got {hits}")
    path.write_text(s.replace(old, new, 1), encoding="utf-8")

replace_once(flow,
    '#include "FlowShared.h"',
    '#include "FlowShared.h"\n#include "VideoAffinityScope.h"',
    "FlowFPS include")
replace_once(flow,
    '    } else if (activationReason == arAllFramesReady) {\n        VSFrame *dst = nullptr;',
    '    } else if (activationReason == arAllFramesReady) {\n        ScopedVideoComputeAffinity isolateVideoWork;\n        VSFrame *dst = nullptr;',
    "FlowFPS scoped affinity")
replace_once(analyse,
    '#include "MotionBlockPyramid.h"',
    '#include "MotionBlockPyramid.h"\n#include "VideoAffinityScope.h"',
    "Analyse include")
replace_once(analyse,
    '    } else if (activationReason == arAllFramesReady) {\n        try {',
    '    } else if (activationReason == arAllFramesReady) {\n        ScopedVideoComputeAffinity isolateVideoWork;\n        try {',
    "Analyse scoped affinity")

for path in (flow, analyse):
    content = path.read_text(encoding="utf-8")
    assert content.count("ScopedVideoComputeAffinity isolateVideoWork;") == 1
assert "SetThreadPriority" not in header.read_text(encoding="utf-8")
assert "SetThreadAffinityMask" in header.read_text(encoding="utf-8")
print("Applied Windows-only scoped 30-of-32 CPU affinity experiment. All 32 VapourSynth workers and pixel algorithms retained; no priority changes or diagnostics.")
