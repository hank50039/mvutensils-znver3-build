// Cross-pel exact 16-bit native gather vs eight 16-bit loads.
// Bench is isolated and NOT representative of full FlowFPS throughput.
#include <immintrin.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>

struct Plane {
    const uint8_t *pPlane[16]{};
    int nPel=1,nHPaddingPel=12,nVPaddingPel=12;
    int nPitch=0,subPelPlaneOffset=0;
};
static inline int ilog2n(int x) { return x==4?2:(x==2?1:0); }
template <bool Manual>
static inline __m256i Gather(const Plane &p,__m256i nX,__m256i nY) {
    int log=ilog2n(p.nPel),mid=(p.nPel*p.nPel)/2;
    auto mask=_mm256_set1_epi32(p.nPel-1);
    auto X=_mm256_add_epi32(nX,_mm256_set1_epi32(p.nHPaddingPel));
    auto Y=_mm256_add_epi32(nY,_mm256_set1_epi32(p.nVPaddingPel));
    auto idx=_mm256_or_si256(_mm256_and_si256(X,mask),_mm256_sllv_epi32(_mm256_and_si256(Y,mask),_mm256_set1_epi32(log)));
    auto off=_mm256_mullo_epi32(_mm256_sub_epi32(idx,_mm256_set1_epi32(mid)),_mm256_set1_epi32(p.subPelPlaneOffset));
    off=_mm256_add_epi32(off,_mm256_slli_epi32(_mm256_srai_epi32(X,log),1));
    off=_mm256_add_epi32(off,_mm256_mullo_epi32(_mm256_srai_epi32(Y,log),_mm256_set1_epi32(p.nPitch)));
    if constexpr (Manual) {
        alignas(32) int32_t v[8];_mm256_store_si256((__m256i*)v,off);
        uint16_t a[8];
        for(int i=0;i<8;++i)std::memcpy(&a[i],p.pPlane[mid]+v[i],sizeof(uint16_t));
        return _mm256_setr_epi32(a[0],a[1],a[2],a[3],a[4],a[5],a[6],a[7]);
    } else {
        auto g=_mm256_i32gather_epi32((const int*)p.pPlane[mid],off,1);
        return _mm256_and_si256(g,_mm256_set1_epi32(0xffff));
    }
}
static uint32_t xorshift(uint32_t &x) {x^=x<<13;x^=x>>17;x^=x<<5;return x;}
int main() {
    constexpr int N=24000, width=512, height=384, pad=24;
    uint64_t checksum=0;
    for(int pel:{1,2,4}) {
        int stride=width+2*pad, planeH=height+2*pad;
        std::vector<uint16_t> storage((size_t)stride*planeH*pel*pel+64);
        uint32_t seed=0x0deba118u+pel;
        for(auto &v:storage)v=(uint16_t)xorshift(seed); // Include all 16-bit values.
        Plane p;p.nPel=pel;p.nHPaddingPel=pad*pel;p.nVPaddingPel=pad*pel;
        p.nPitch=stride*2;p.subPelPlaneOffset=stride*planeH*2;
        const uint8_t *base=(const uint8_t *)storage.data();
        for(int k=0;k<pel*pel;k++)p.pPlane[k]=base+(size_t)k*p.subPelPlaneOffset;
        for(int i=0;i<N;++i) {
            alignas(32) int32_t xv[8],yv[8];
            for(int lane=0;lane<8;lane++) {
                xv[lane]=(int)(xorshift(seed)%(width*pel));
                yv[lane]=(int)(xorshift(seed)%(height*pel));
            }
            auto x=_mm256_load_si256((const __m256i *)xv),y=_mm256_load_si256((const __m256i*)yv);
            auto a=Gather<false>(p,x,y),b=Gather<true>(p,x,y);
            if(_mm256_movemask_epi8(_mm256_cmpeq_epi32(a,b))!=-1) {
                std::fprintf(stderr,"FAIL pel=%d chunk=%d\n",pel,i);return 1;
            }
            checksum+=(unsigned)_mm256_extract_epi32(b,0);
        }
        std::printf("PASS pel=%d %d gathers, %d lanes, checksum=%llu\n",pel,N,N*8,(unsigned long long)checksum);
    }
    // Four distinct scattered reads (F,FF,B,BB), like FlowInterExtra's hot loop.
    constexpr int W=3840,H=2160,STR=W+64,CHUNKS=100000;
    std::vector<uint16_t> pixels((size_t)STR*(H+64)+64);
    uint32_t seed=0x345aba98u;
    for(auto &v:pixels)v=(uint16_t)xorshift(seed);
    Plane p;
    p.nPel=1;p.nHPaddingPel=32;p.nVPaddingPel=32;
    p.nPitch=STR*2;p.subPelPlaneOffset=STR*(H+64)*2;
    p.pPlane[0]=(const uint8_t *)pixels.data();
    std::vector<int32_t> X((size_t)CHUNKS*8*4),Y((size_t)CHUNKS*8*4);
    for(int k=0;k<CHUNKS;k++) {
        const int baseX=(k*8)%W,baseY=((k*8)/W)%H;
        for(int g=0;g<4;g++)for(int lane=0;lane<8;lane++) {
            int dx=(int)(xorshift(seed)%49)-24,dy=(int)(xorshift(seed)%49)-24;
            int index=k*32+g*8+lane;
            X[index]=std::clamp(baseX+lane+dx,0,W-1);
            Y[index]=std::clamp(baseY+dy,0,H-1);
        }
    }
    uint64_t results[2]{};
    double med[2]{};
    for(int mode=0;mode<2;mode++) {
        double ms[5]{};
        for(int round=0;round<5;round++) {
            // Consume every lane: extracting lane 0 alone lets LLVM prune or
            // scalarize almost all AVX2 gathers, corrupting the timing.
            __m256i vector_sink=_mm256_setzero_si256();
            auto start=std::chrono::steady_clock::now();
            for(int k=0;k<CHUNKS;k++) {
                __m256i samples[4];
                for(int g=0;g<4;g++) {
                    auto xv=_mm256_loadu_si256((const __m256i *)(X.data()+k*32+g*8));
                    auto yv=_mm256_loadu_si256((const __m256i *)(Y.data()+k*32+g*8));
                    samples[g]=(mode==0)?Gather<false>(p,xv,yv):Gather<true>(p,xv,yv);
                }
                auto mixed=_mm256_add_epi32(_mm256_min_epi32(samples[0],samples[1]),
                                             _mm256_max_epi32(samples[2],samples[3]));
                // Force every vector to remain observable at every iteration.
                // Without this compiler barrier, Clang can fold most of the
                // native-gather benchmark away (impossible ~0.03ms for 400k gathers).
                // Both modes pay the same barrier cost.
                __asm__ __volatile__("" : : "x"(mixed) : "memory");
                vector_sink=_mm256_add_epi32(vector_sink,mixed);
            }
            ms[round]=std::chrono::duration<double,std::milli>(
                std::chrono::steady_clock::now()-start).count();
            alignas(32) uint32_t lanes[8];
            _mm256_store_si256(reinterpret_cast<__m256i*>(lanes),vector_sink);
            uint64_t sink=0;
            for (uint32_t lane:lanes) sink+=lane;
            if(round==4)results[mode]=sink;
        }
        std::sort(ms,ms+5);med[mode]=ms[2];
        std::printf("mode=%s median_ms=%.5f checksum=%llu\n",
                    mode?"manual16":"native32",med[mode],(unsigned long long)results[mode]);
    }
    if(results[0]!=results[1])return 2;
    std::printf("PASS 4-gather outputs match; manual/native ratio=%.5f\n",med[1]/med[0]);
    return 0;
}
