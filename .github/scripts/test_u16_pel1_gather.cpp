// Executable equivalence and microbenchmark for AVX2 uint16 pel=1 addressing.
// Synthetic data only. The user's Zen 3 and full FlowFPS workload must be
// measured separately. No video/quality settings are changed.
#include <immintrin.h>
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <random>
#include <vector>

struct PyramidPlaneLite {
    const uint8_t *plane;
    int pel, hPad, vPad, subPelPlaneOffset, pitch;
};

static inline __m256i OriginalGather(const PyramidPlaneLite &p, __m256i nX, __m256i nY) {
    const int log = 0, mid = 0; // this test specifically covers pel=1
    const __m256i mask = _mm256_set1_epi32(p.pel - 1);
    const __m256i X = _mm256_add_epi32(nX, _mm256_set1_epi32(p.hPad));
    const __m256i Y = _mm256_add_epi32(nY, _mm256_set1_epi32(p.vPad));
    const __m256i idx = _mm256_or_si256(
        _mm256_and_si256(X, mask),
        _mm256_sllv_epi32(_mm256_and_si256(Y, mask), _mm256_set1_epi32(log)));
    __m256i offset = _mm256_mullo_epi32(
        _mm256_sub_epi32(idx, _mm256_set1_epi32(mid)),
        _mm256_set1_epi32(p.subPelPlaneOffset));
    offset = _mm256_add_epi32(offset, _mm256_slli_epi32(_mm256_srai_epi32(X, log), 1));
    offset = _mm256_add_epi32(offset, _mm256_mullo_epi32(
        _mm256_srai_epi32(Y, log), _mm256_set1_epi32(p.pitch)));
    return _mm256_and_si256(
        _mm256_i32gather_epi32(reinterpret_cast<const int *>(p.plane), offset, 1),
        _mm256_set1_epi32(0xFFFF));
}

static inline __m256i OptimizedGather(const PyramidPlaneLite &p, __m256i nX, __m256i nY) {
    const __m256i X = _mm256_add_epi32(nX, _mm256_set1_epi32(p.hPad));
    const __m256i Y = _mm256_add_epi32(nY, _mm256_set1_epi32(p.vPad));
    const __m256i off = _mm256_add_epi32(_mm256_slli_epi32(X, 1),
        _mm256_mullo_epi32(Y, _mm256_set1_epi32(p.pitch)));
    return _mm256_and_si256(
        _mm256_i32gather_epi32(reinterpret_cast<const int *>(p.plane), off, 1),
        _mm256_set1_epi32(0xFFFF));
}

int main() {
    constexpr int width = 3840, height = 2160, pad = 12, stride = width + 2 * pad;
    constexpr int numChunks = 200000;
    std::mt19937 rng(0x6AC3D7u);
    std::vector<uint16_t> pixels(static_cast<size_t>(stride) * (height + 2 * pad) + 16);
    for (auto &x : pixels) x = static_cast<uint16_t>(rng() & 0xFFFFu);
    PyramidPlaneLite p {
        reinterpret_cast<const uint8_t *>(pixels.data()), 1, pad, pad,
        stride * 2, stride * 2
    };
    std::vector<int32_t> coordsX(numChunks * 8), coordsY(numChunks * 8);
    for (int i=0; i<numChunks*8; i++) {
        const int x = (i * 17LL) % width, y = (i * 43LL / width) % height;
        coordsX[i] = std::clamp(x + static_cast<int>(rng() % 21) - 10, 0, width - 1);
        coordsY[i] = std::clamp(y + static_cast<int>(rng() % 21) - 10, 0, height - 1);
    }

    uint64_t checksum = 0;
    for (int k=0; k<numChunks; k++) {
        const auto X = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(coordsX.data()+k*8));
        const auto Y = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(coordsY.data()+k*8));
        const auto orig = OriginalGather(p,X,Y), opt = OptimizedGather(p,X,Y);
        const auto eq = _mm256_cmpeq_epi32(orig, opt);
        if (_mm256_movemask_epi8(eq) != -1) {
            std::fprintf(stderr, "FAIL pixel mismatch at chunk %d\n",k);
            return 1;
        }
        checksum += static_cast<unsigned>(_mm256_extract_epi32(opt,0));
    }
    std::printf("PASS: 1,600,000 16-bit lane values bit-exact; checksum=%llu\n",
                static_cast<unsigned long long>(checksum));
    // Timings are illustrative only (runner CPU != Ryzen 5900XT).
    for (int pass=0;pass<4;pass++) {
        for (int mode=0;mode<2;mode++) {
            uint64_t sink=0;
            auto start=std::chrono::steady_clock::now();
            for (int k=0;k<numChunks;k++) {
                auto X=_mm256_loadu_si256(reinterpret_cast<const __m256i *>(coordsX.data()+k*8));
                auto Y=_mm256_loadu_si256(reinterpret_cast<const __m256i *>(coordsY.data()+k*8));
                auto v=mode==0 ? OriginalGather(p,X,Y) : OptimizedGather(p,X,Y);
                sink += static_cast<unsigned>(_mm256_extract_epi32(v,0));
            }
            double ms=std::chrono::duration<double,std::milli>(
                std::chrono::steady_clock::now()-start).count();
            if (sink != checksum) return 2;
            std::printf("pass=%d mode=%s ms=%.4f checksum=%llu\n",
                        pass, mode==0?"original":"optimized",ms,
                        static_cast<unsigned long long>(sink));
        }
    }
    return 0;
}
