from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_mvutensils_audio_priority.py <upstream-source-root>")

root = Path(sys.argv[1])
flow = root / "src/FlowFPS.cpp"
analyse = root / "src/Analyse.cpp"
header = root / "src/AudioScheduling.h"
if header.exists():
    raise SystemExit("Refusing to overwrite existing AudioScheduling.h")

header.write_text(r'''#pragma once

// Experimental Windows-only scheduling policy, NOT an algorithm modification.
// VapourSynth executes these getFrame callbacks on its own worker threads.
// Let normal-priority LAV/audio producer threads run ahead of CPU-intensive
// MVUtensils workers when 32 VS threads compete on 32 logical processors.
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
inline void YieldPriorityToAudioProducer() noexcept {
    static thread_local bool checked = false;
    if (!checked) {
        checked = true;
        const HANDLE thread = GetCurrentThread();
        if (GetThreadPriority(thread) == THREAD_PRIORITY_NORMAL) {
            SetThreadPriority(thread, THREAD_PRIORITY_BELOW_NORMAL);
        }
    }
}
#else
inline void YieldPriorityToAudioProducer() noexcept {}
#endif
''', encoding="utf-8")

def change(path: Path, old: str, new: str, label: str):
    s = path.read_text(encoding="utf-8")
    if s.count(old) != 1:
        raise SystemExit(f"{label}: expected one matching source anchor; found {s.count(old)}")
    path.write_text(s.replace(old, new, 1), encoding="utf-8")

change(flow, '#include "FlowShared.h"', '#include "FlowShared.h"\n#include "AudioScheduling.h"', "FlowFPS include")
change(flow,
       '    } else if (activationReason == arAllFramesReady) {\n        VSFrame *dst = nullptr;',
       '    } else if (activationReason == arAllFramesReady) {\n        YieldPriorityToAudioProducer();\n        VSFrame *dst = nullptr;',
       "FlowFPS worker entry")
change(analyse, '#include "MotionBlockPyramid.h"', '#include "MotionBlockPyramid.h"\n#include "AudioScheduling.h"', "Analyse include")
change(analyse,
       '    } else if (activationReason == arAllFramesReady) {\n        try {',
       '    } else if (activationReason == arAllFramesReady) {\n        YieldPriorityToAudioProducer();\n        try {',
       "Analyse/AnalyseMany worker entry")
for p in (flow, analyse):
    s = p.read_text(encoding="utf-8")
    assert s.count('YieldPriorityToAudioProducer();') == 1
    assert 'SetThreadPriority' not in s
assert 'THREAD_PRIORITY_BELOW_NORMAL' in header.read_text(encoding="utf-8")
print("Experimental 32-thread scheduling A/B patch applied: Windows worker priority only; FlowFPS and Analyse pixel processing unchanged.")
