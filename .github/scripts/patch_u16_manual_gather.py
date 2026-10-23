#!/usr/bin/env python3
"""Experimental exactly equivalent 16-bit per-lane gather in FlowInterExtra AVX2.

Changes only the load mechanism after the ORIGINAL offset arithmetic. Pixel
values (including full 16-bit) and all mask/interpolation arithmetic unchanged.
The default native gather remains for all other paths and for the u8 path.
"""
from pathlib import Path
import sys
p=Path(sys.argv[1] if len(sys.argv)>1 else "upstream-opt")/"src/FlowShared_AVX2.cpp"
s=p.read_text(encoding="utf-8")
def once(s,old,new,name):
    n=s.count(old)
    if n != 1: raise SystemExit(f"{name}: anchor count {n}, abort")
    return s.replace(old,new,1)
s=once(s,'#include <immintrin.h>','#include <immintrin.h>\n#include <cstring>',"cstring")
s=once(s,'template <int S>\nstatic MVU_FORCE_INLINE __m256i mc_gather(',
       'template <int S, bool ManualU16 = false>\nstatic MVU_FORCE_INLINE __m256i mc_gather(',"gather declaration")
old="""    __m256i g = _mm256_i32gather_epi32((const int *)p.pPlane[mid], off, 1);
    return _mm256_and_si256(g, _mm256_set1_epi32(S == 1 ? 0xFF : (S == 2 ? 0xFFFF : -1))); // float (S==4): keep all 32 bits
"""
new="""    if constexpr (S == 2 && ManualU16) {
        // AVX2 vpgatherdd fetches 32 bits and then masks away the high 16.
        // Eight independent 16-bit loads return the exact same useful pixels.
        // Keep the original signed 32-bit byte offsets untouched.
        alignas(32) int32_t offsets[8];
        _mm256_store_si256(reinterpret_cast<__m256i *>(offsets), off);
        const uint8_t *base = p.pPlane[mid];
        uint16_t pix[8];
        for (int i = 0; i < 8; ++i)
            std::memcpy(&pix[i], base + offsets[i], sizeof(uint16_t));
        return _mm256_setr_epi32(pix[0], pix[1], pix[2], pix[3],
                                 pix[4], pix[5], pix[6], pix[7]);
    } else {
        __m256i g = _mm256_i32gather_epi32((const int *)p.pPlane[mid], off, 1);
        return _mm256_and_si256(g, _mm256_set1_epi32(S == 1 ? 0xFF : (S == 2 ? 0xFFFF : -1))); // float (S==4): keep all 32 bits
    }
"""
s=once(s,old,new,"16bit manual gather");
begin=s.index("static void flowinterextra_impl(")
end=s.index("// ---- 32-bit float blends.",begin)
sub=s[begin:end]
assert sub.count("mc_gather<S>(")==4,sub.count("mc_gather<S>(")
sub=sub.replace("mc_gather<S>(", "mc_gather<S, true>(")
s=s[:begin]+sub+s[end:]
assert s.count("mc_gather<S, true>(")==4
assert s.count("mc_gather<S>(")>=2
p.write_text(s,encoding="utf-8",newline="\n")
print("PASS: exclusively FlowInterExtra, u16 manual16 gather; u8 and other kernels unchanged")
