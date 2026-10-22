#!/usr/bin/env python3
"""MPC Audio Renderer live instrumentation, without changing video pipeline or scheduling.
Patch is applied AFTER existing audio-only recovery patch."""
from pathlib import Path
import sys
if len(sys.argv)!=2: raise SystemExit("usage: patch_mpc_audio_live_diag.py <MPC-BE source root>")
root=Path(sys.argv[1])
cpp=root/"src/filters/renderer/MpcAudioRenderer/MpcAudioRenderer.cpp"
hdr=root/"src/filters/renderer/MpcAudioRenderer/MpcAudioRenderer.h"
s=cpp.read_text(encoding="utf-8-sig")
h=hdr.read_text(encoding="utf-8-sig")
def once(input, old, new, label):
    count=input.count(old)
    if count != 1: raise SystemExit(f"{label}: expected single anchor, got {count}")
    return input.replace(old,new,1)

h=once(h, "#include <optional>", "#include <optional>\n#include <atomic>", "atomic header")
h=once(h, "    unsigned          m_exclusiveBufferFailures = 0;", """    unsigned          m_exclusiveBufferFailures = 0;

    // Diagnostic-only timestamps. No periodic disk I/O, no video change.
    std::atomic<ULONGLONG> m_diagLastTransformTickMs{0};
    std::atomic<ULONGLONG> m_diagLastAudioPayloadTickMs{0};
""", "diag timestamps")

s=once(s,'''\tDLog(L"CMpcAudioRenderer::RenderThread() - start, thread ID = 0x%x", m_nThreadId);''',
'''\tDLog(L"CMpcAudioRenderer::RenderThread() - start, thread ID = 0x%x", m_nThreadId);
    OutputDebugStringW(L"MpcAudioDiag: LIVE-V4 RenderThread entered\\n");
    ULONGLONG lastDiagTickMs = 0;
''',"RenderThread startup")

s=once(s,'''\twhile (!bExit) {
\t\tCheckBufferStatus();''',
'''\twhile (!bExit) {
        const ULONGLONG currentDiagTickMs = GetTickCount64();
        if (currentDiagTickMs - lastDiagTickMs >= 1500) {
            lastDiagTickMs = currentDiagTickMs;
            const ULONGLONG transformMs = m_diagLastTransformTickMs.load(std::memory_order_relaxed);
            const ULONGLONG payloadMs = m_diagLastAudioPayloadTickMs.load(std::memory_order_relaxed);
            WCHAR line[480] = {};
            swprintf_s(line, _countof(line),
                L"MpcAudioDiag: LIVE-V4 beat exclusive=%d mode=%d running=%d clientStarted=%d queueBytes=%Iu lastTransformAgoMs=%llu lastPayloadAgoMs=%llu recoverPending=%d/%d\\n",
                m_DeviceModeCurrent == MODE_WASAPI_EXCLUSIVE ? 1 : 0,
                static_cast<int>(m_WasapiMethod),
                m_filterState == State_Running ? 1 : 0,
                m_bIsAudioClientStarted ? 1 : 0,
                WasapiQueueSize(),
                transformMs ? currentDiagTickMs-transformMs : 0,
                payloadMs ? currentDiagTickMs-payloadMs : 0,
                m_bNeedReinitialize != FALSE ? 1 : 0,
                m_bNeedReinitializeFull != FALSE ? 1 : 0);
            OutputDebugStringW(line);
        }
\t\tCheckBufferStatus();''', "periodic render thread heartbeat")

s=once(s, '''\tif (m_bNeedReinitializeFull) {
\t\tReinitializeAudioDevice(TRUE);
\t}''', '''    m_diagLastTransformTickMs.store(GetTickCount64(), std::memory_order_relaxed);
\tif (m_bNeedReinitializeFull) {
        OutputDebugStringW(L"MpcAudioDiag: LIVE-V4 Transform consuming FULL recovery request\\n");
\t\tReinitializeAudioDevice(TRUE);
\t}''', "Transform heartbeat and full recovery")
s=once(s,'''\tif (m_bNeedReinitialize) {
\t\tReinitializeAudioDevice();
\t}''','''\tif (m_bNeedReinitialize) {
        OutputDebugStringW(L"MpcAudioDiag: LIVE-V4 Transform consuming recovery request\\n");
\t\tReinitializeAudioDevice();
\t}''', "normal recovery")

s=once(s,'''\t\tif (!nWritenBytes) {
\t\t\tdwFlags = AUDCLNT_BUFFERFLAGS_SILENT;''',
'''        if (nWritenBytes) {
            m_diagLastAudioPayloadTickMs.store(GetTickCount64(), std::memory_order_relaxed);
        }
\t\tif (!nWritenBytes) {
\t\t\tdwFlags = AUDCLNT_BUFFERFLAGS_SILENT;''', "real payload timestamp")
for marker in ["MpcAudioDiag: LIVE-V4 beat", "LIVE-V4 Transform consuming FULL", "m_diagLastAudioPayloadTickMs.store"]:
    assert marker in s
assert "SetThreadAffinityMask" not in s
cpp.write_text(s,encoding="utf-8-sig")
hdr.write_text(h,encoding="utf-8-sig")
print("Instrumented renderer heartbeat each 1.5s, Transform receipt, payload supply, pending reinit. Existing recovery patch retained; video unchanged.")
