#!/usr/bin/env python3
"""LIVE-V7: high-resolution timing of MPC Audio Renderer WASAPI PUSH loop.

Apply after LIVE-V6. The main audio render loop samples QPC around each
RenderWasapiBuffer() call and timed wait. It aggregates metrics in thread-local
variables and writes a single summary every 1.5 seconds into existing V6 log.
No new per-buffer file I/O, changes to buffers, priority, affinity, scheduling,
PCM samples, or MVUtensils video.
"""
from pathlib import Path
import sys
if len(sys.argv)!=2: raise SystemExit("usage: patch_mpc_audio_push_timing_v7.py <MPC-BE source root>")
path=Path(sys.argv[1])/"src/filters/renderer/MpcAudioRenderer/MpcAudioRenderer.cpp"
s=path.read_text(encoding="utf-8-sig")
def patch(old,new,label):
    global s
    n=s.count(old)
    if n!=1: raise SystemExit(f"{label}: expected one anchor, found {n}")
    s=s.replace(old,new,1)

patch('''    ULONGLONG lastDiagTickMs = 0;''',
'''    ULONGLONG lastDiagTickMs = 0;

    LARGE_INTEGER v7Freq = {};
    QueryPerformanceFrequency(&v7Freq);
    LONGLONG v7PreviousCallQpc = 0;
    ULONGLONG v7Calls = 0, v7Failures = 0;
    ULONGLONG v7LongCallGapCount = 0, v7LateWaitCount = 0;
    ULONGLONG v7MaxCallGapUs = 0, v7MaxRenderCallUs = 0;
    ULONGLONG v7MaxWaitOverrunUs = 0;
    auto v7ElapsedUs = [&](LONGLONG start, LONGLONG stop) -> ULONGLONG {
        if (!start || stop < start || v7Freq.QuadPart <= 0) return 0;
        return static_cast<ULONGLONG>(
            (static_cast<long double>(stop-start) * 1000000.0L)
            / static_cast<long double>(v7Freq.QuadPart));
    };''', "QPC interval counter declarations")

patch('''            OutputDebugStringW(line);
            if (liveV6Log != INVALID_HANDLE_VALUE) {''',
'''            OutputDebugStringW(line);
            if (m_WasapiMethod == WASAPI_METHOD::PUSH) {
                WCHAR v7Line[440] = {};
                const ULONGLONG expectedWaitUs =
                    static_cast<ULONGLONG>(m_hnsBufferDuration / 10000 / 2) * 1000ULL;
                swprintf_s(v7Line, _countof(v7Line),
                    L"MpcAudioDiag: LIVE-V7 PUSH calls=%llu failures=%llu expectedWaitUs=%llu maxCallGapUs=%llu longGapCount=%llu maxRenderUs=%llu maxWaitOverrunUs=%llu delayedWaitCount=%llu\\n",
                    v7Calls, v7Failures, expectedWaitUs,
                    v7MaxCallGapUs, v7LongCallGapCount, v7MaxRenderCallUs,
                    v7MaxWaitOverrunUs, v7LateWaitCount);
                OutputDebugStringW(v7Line);
                if (liveV6Log != INVALID_HANDLE_VALUE) {
                    char v7Utf8[1024] = {};
                    const int bytes = WideCharToMultiByte(CP_UTF8, 0, v7Line, -1,
                        v7Utf8, _countof(v7Utf8), nullptr, nullptr);
                    if (bytes > 1) {
                        DWORD written = 0;
                        WriteFile(liveV6Log, v7Utf8,
                            static_cast<DWORD>(bytes-1), &written, nullptr);
                    }
                }
                v7Calls = v7Failures = 0;
                v7LongCallGapCount = v7LateWaitCount = 0;
                v7MaxCallGapUs = v7MaxRenderCallUs = v7MaxWaitOverrunUs = 0;
            }
            if (liveV6Log != INVALID_HANDLE_VALUE) {''', "V7 periodic summary")

patch('''\t\tif (m_WasapiMethod == WASAPI_METHOD::PUSH) {
\t\t\tHRESULT hr = RenderWasapiBuffer();
\t\t\tif (hr == AUDCLNT_E_DEVICE_INVALIDATED) {
\t\t\t\tSetReinitializeAudioDevice(TRUE);
\t\t\t}

\t\t\tresult = WaitForMultipleObjects(std::size(renderHandles) - 1, renderHandles, FALSE, m_hnsBufferDuration / 10000 / 2);
\t\t} else {''',
'''        if (m_WasapiMethod == WASAPI_METHOD::PUSH) {
            LARGE_INTEGER v7Now = {}, v7AfterCall = {}, v7AfterWait = {};
            QueryPerformanceCounter(&v7Now);
            const ULONGLONG expectedWaitUs =
                static_cast<ULONGLONG>(m_hnsBufferDuration / 10000 / 2) * 1000ULL;
            if (v7PreviousCallQpc != 0 && m_filterState == State_Running
                && m_bIsAudioClientStarted) {
                const ULONGLONG gapUs = v7ElapsedUs(v7PreviousCallQpc, v7Now.QuadPart);
                v7MaxCallGapUs = std::max(v7MaxCallGapUs, gapUs);
                if (gapUs > 2 * expectedWaitUs + 10000ULL) ++v7LongCallGapCount;
            }
            v7PreviousCallQpc = v7Now.QuadPart;
            HRESULT hr = RenderWasapiBuffer();
            QueryPerformanceCounter(&v7AfterCall);
            if (m_filterState == State_Running && m_bIsAudioClientStarted) {
                ++v7Calls;
                if (FAILED(hr)) ++v7Failures;
                v7MaxRenderCallUs = std::max(v7MaxRenderCallUs,
                    v7ElapsedUs(v7Now.QuadPart, v7AfterCall.QuadPart));
            }
            if (hr == AUDCLNT_E_DEVICE_INVALIDATED) {
                SetReinitializeAudioDevice(TRUE);
            }

            result = WaitForMultipleObjects(std::size(renderHandles) - 1,
                renderHandles, FALSE, m_hnsBufferDuration / 10000 / 2);
            QueryPerformanceCounter(&v7AfterWait);
            if (result == WAIT_TIMEOUT && m_filterState == State_Running
                && m_bIsAudioClientStarted) {
                const ULONGLONG elapsedUs =
                    v7ElapsedUs(v7AfterCall.QuadPart, v7AfterWait.QuadPart);
                const ULONGLONG overrunUs =
                    (elapsedUs > expectedWaitUs) ? elapsedUs - expectedWaitUs : 0;
                v7MaxWaitOverrunUs = std::max(v7MaxWaitOverrunUs, overrunUs);
                if (overrunUs >= 10000ULL) ++v7LateWaitCount;
            }
        } else {''',"timed PUSH render and wait loop")

for phrase in ("LIVE-V7 PUSH calls=", "v7ElapsedUs(", "maxRenderUs=", "delayedWaitCount="):
    assert phrase in s
assert "SetThreadAffinityMask" not in s
path.write_text(s,encoding="utf-8-sig")
print("PUSH V7 patched: QPC wait overrun, render elapsed, render gap and failure counters, one log summary per 1.5 seconds.")
