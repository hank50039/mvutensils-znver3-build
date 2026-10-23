#!/usr/bin/env python3
"""Stage 1: add *read-only* kernel dispatch auditing to pinned MVUtensils source.

Instrumentation never substitutes a kernel, changes pixel values, alters
scheduling or changes frame-request behavior. Counter overhead is nonzero,
so this build is for routing diagnosis, not a direct performance benchmark.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("upstream")
flow = root / "src/FlowShared.h"
fps = root / "src/FlowFPS.cpp"
src = flow.read_text(encoding="utf-8")
code = fps.read_text(encoding="utf-8")

header = Path(__file__).with_name("mvu_flowpath_probe.h").read_text(encoding="utf-8")
(root / "src/FlowPathProbe.h").write_text(header, encoding="utf-8")

def replace_once(s, old, new, label):
    count = s.count(old)
    if count != 1:
        raise SystemExit(f"Cannot patch {label}: expected one anchor, got {count}")
    return s.replace(old, new, 1)

src = replace_once(src, '#include "CPU.h"', '#include "CPU.h"\n#include "FlowPathProbe.h"', "dispatch header")

# Record the actual branch, not merely CPUID support. The scalar fall-through
# also counts gather-address overflow. AVX2 width<8 uses scalar internally.
start = src.index("static MVU_FORCE_INLINE void FlowInter(")
end = src.index("static MVU_FORCE_INLINE void FlowInterExtra(", start)
normal = src[start:end]
normal = replace_once(normal,
    'if (g_cpuinfo & MVU_CPU_AVX512_BASE) {',
    'if (g_cpuinfo & MVU_CPU_AVX512_BASE) {\n            MvuFlowPathProbe::Count(8);',
    "FlowInter avx512")
normal = replace_once(normal,
    'if (g_cpuinfo & MVU_CPU_AVX2) {',
    '''if (g_cpuinfo & MVU_CPU_AVX2) {
            if constexpr (sizeof(PixelType) == 1)
                MvuFlowPathProbe::Count(width < 8 ? 10 : 4);
            else if constexpr (sizeof(PixelType) == 2)
                MvuFlowPathProbe::Count(width < 8 ? 11 : 5);''',
    "FlowInter avx2")
normal = replace_once(normal,
    'FlowInter_scalar<PixelType>(pdst8,',
    '''if constexpr (sizeof(PixelType) == 1)
        MvuFlowPathProbe::Count(6);
    else if constexpr (sizeof(PixelType) == 2)
        MvuFlowPathProbe::Count(7);
    FlowInter_scalar<PixelType>(pdst8,''',
    "FlowInter scalar")
src = src[:start] + normal + src[end:]

start = src.index("static MVU_FORCE_INLINE void FlowInterExtra(")
end = src.index("static MVU_FORCE_INLINE void FlowFetch(", start)
extra = src[start:end]
extra = replace_once(extra,
    'if (g_cpuinfo & MVU_CPU_AVX512_BASE) {',
    'if (g_cpuinfo & MVU_CPU_AVX512_BASE) {\n            MvuFlowPathProbe::Count(9);',
    "FlowInterExtra avx512")
extra = replace_once(extra,
    'if (g_cpuinfo & MVU_CPU_AVX2) {',
    '''if (g_cpuinfo & MVU_CPU_AVX2) {
            if constexpr (sizeof(PixelType) == 1)
                MvuFlowPathProbe::Count(width < 8 ? 10 : 0);
            else if constexpr (sizeof(PixelType) == 2)
                MvuFlowPathProbe::Count(width < 8 ? 11 : 1);''',
    "FlowInterExtra avx2")
extra = replace_once(extra,
    'FlowInterExtra_scalar<PixelType>(pdst8,',
    '''if constexpr (sizeof(PixelType) == 1)
        MvuFlowPathProbe::Count(2);
    else if constexpr (sizeof(PixelType) == 2)
        MvuFlowPathProbe::Count(3);
    FlowInterExtra_scalar<PixelType>(pdst8,''',
    "FlowInterExtra scalar")
src = src[:start] + extra + src[end:]

code = replace_once(code,
    '''    VSFilterDependency deps[4] = {''',
    '''    MvuFlowPathProbe::LogConfig(d->vi.format.bitsPerSample, d->vi.width, d->vi.height,
                              static_cast<unsigned>(g_cpuinfo));
    VSFilterDependency deps[4] = {''',
    "FlowFPS clip configuration")
code = replace_once(code,
    '''                return dst;
            } else {
                if (d->blend) {''',
    '''                if (n % 90 == 1)
                    MvuFlowPathProbe::LogSnapshot(n, d->vi.format.bitsPerSample);
                return dst;
            } else {
                if (d->blend) {''',
    "FlowFPS snapshot")
flow.write_text(src, encoding="utf-8", newline="\n")
fps.write_text(code, encoding="utf-8", newline="\n")
for needle in ("Count(width < 8 ? 11 : 1);", "MvuFlowPathProbe::Count(3);",
               "MvuFlowPathProbe::LogConfig(", "MvuFlowPathProbe::LogSnapshot("):
    if needle not in src + code:
        raise SystemExit(f"Probe anchor missing after patch: {needle}")
print("Stage1 dispatch audit patched: direct kernel counts; no image kernel changes")
