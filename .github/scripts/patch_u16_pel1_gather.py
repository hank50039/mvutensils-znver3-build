#!/usr/bin/env python3
"""Bit-preserving pel=1 address fast-path for AVX2 16-bit gather.

Only touches FlowShared_AVX2.cpp / mc_gather<2>. The four motion-compensated
samples, masks, arithmetic, timing, and 8-bit kernel are unchanged.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv)>1 else Path("upstream-opt")
p = root / "src/FlowShared_AVX2.cpp"
s = p.read_text(encoding="utf-8")
old = """template <int S>
static MVU_FORCE_INLINE __m256i mc_gather(const PyramidPlane &p, __m256i nX, __m256i nY) {
    const int log = ilog2(p.nPel);
"""
new = """template <int S>
static MVU_FORCE_INLINE __m256i mc_gather(const PyramidPlane &p, __m256i nX, __m256i nY) {
    // At pel=1 the original subpel-plane index is always zero and shifts are
    // zero; compute the EXACT SAME 32-bit byte offset without generic subpel
    // bookkeeping. In particular, do not change rounding or sampled pixels.
    // This applies only to uint16 (10/12/16-bit) on the AVX2 kernel.
    if constexpr (S == 2) {
        if (p.nPel == 1) {
            const __m256i X = _mm256_add_epi32(nX, _mm256_set1_epi32(p.nHPaddingPel));
            const __m256i Y = _mm256_add_epi32(nY, _mm256_set1_epi32(p.nVPaddingPel));
            const __m256i byteOffset = _mm256_add_epi32(
                _mm256_slli_epi32(X, 1),
                _mm256_mullo_epi32(Y, _mm256_set1_epi32(static_cast<int>(p.nPitch))));
            return _mm256_and_si256(
                _mm256_i32gather_epi32(reinterpret_cast<const int *>(p.pPlane[0]), byteOffset, 1),
                _mm256_set1_epi32(0xFFFF));
        }
    }
    const int log = ilog2(p.nPel);
"""
if s.count(old) != 1:
    raise SystemExit(f"Unsafe patch abort: expected one mc_gather anchor, found {s.count(old)}")
s = s.replace(old,new,1)
assert s.count("if (p.nPel == 1)") == 1
assert s.count("FlowInterExtra_avx2_u16") == 1
p.write_text(s,encoding="utf-8",newline="\n")
print("Applied uint16 pel=1-only address optimization; original generic path preserved")
