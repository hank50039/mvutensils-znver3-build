// Copyright (c) 2026: stage-one local dispatch audit. No image modifications.
// Runtime logging is deliberately sparse; per-tile instrumentation only adds
// relaxed atomic counters. Restore the original DLL after obtaining logs.
#pragma once
#include <windows.h>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace MvuFlowPathProbe {
enum Route : int {
    EXTRA_U8_AVX2 = 0,
    EXTRA_U16_AVX2 = 1,
    EXTRA_U8_SCALAR = 2,
    EXTRA_U16_SCALAR = 3,
    NORMAL_U8_AVX2 = 4,
    NORMAL_U16_AVX2 = 5,
    NORMAL_U8_SCALAR = 6,
    NORMAL_U16_SCALAR = 7,
    NORMAL_AVX512 = 8,
    EXTRA_AVX512 = 9,
    NARROW_U8_SCALAR = 10,
    NARROW_U16_SCALAR = 11,
    ROUTE_COUNT = 12
};

inline std::atomic<unsigned long long> counts[ROUTE_COUNT] {};

inline void Count(int route) noexcept {
    if (route >= 0 && route < ROUTE_COUNT)
        counts[route].fetch_add(1ULL, std::memory_order_relaxed);
}

inline void WriteLine(const char *line) noexcept {
    if (!line)
        return;
    if (!CreateDirectoryA("D:\\MVtools", nullptr) &&
        GetLastError() != ERROR_ALREADY_EXISTS)
        return;
    char filename[MAX_PATH] {};
    std::snprintf(filename, sizeof(filename),
                  "D:\\MVtools\\MVU_FlowPathAudit_%lu.txt",
                  static_cast<unsigned long>(GetCurrentProcessId()));
    HANDLE f = CreateFileA(filename, FILE_APPEND_DATA,
                           FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE)
        return;
    DWORD written = 0;
    WriteFile(f, line, static_cast<DWORD>(std::strlen(line)), &written, nullptr);
    CloseHandle(f);
}

inline void LogConfig(int bits, int w, int h, unsigned cpuFlags) noexcept {
    char line[224] {};
    std::snprintf(line, sizeof(line),
        "stage=FlowFPSConfig bits=%d width=%d height=%d cpu_flags=0x%08X avx2_detected=%d\r\n",
        bits, w, h, cpuFlags, (cpuFlags & (1u << 0)) ? 1 : 0);
    // avx2_detected is only informational; route counters are authoritative.
    WriteLine(line);
}

inline void LogSnapshot(int n, int bits) noexcept {
    char line[768] {};
    const auto get = [](int i) -> unsigned long long {
        return counts[i].load(std::memory_order_relaxed);
    };
    std::snprintf(line, sizeof(line),
        "stage=DispatchSnapshot frame=%d bits=%d "
        "extra_u8_avx2=%llu extra_u16_avx2=%llu "
        "extra_u8_scalar=%llu extra_u16_scalar=%llu "
        "normal_u8_avx2=%llu normal_u16_avx2=%llu "
        "normal_u8_scalar=%llu normal_u16_scalar=%llu "
        "normal_avx512=%llu extra_avx512=%llu "
        "narrow_u8_scalar=%llu narrow_u16_scalar=%llu\r\n",
        n, bits,
        get(0), get(1), get(2), get(3),
        get(4), get(5), get(6), get(7),
        get(8), get(9), get(10), get(11));
    WriteLine(line);
}
} // namespace MvuFlowPathProbe
