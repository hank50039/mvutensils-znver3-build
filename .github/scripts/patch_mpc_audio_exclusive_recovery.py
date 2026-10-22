#!/usr/bin/env python3
"""Patch MPC Audio Renderer ONLY: guard persistent exclusive-mode underrun or buffer errors.
No changes to video/interpolation and no thread scheduling alterations.
This is a conservative audio-only auto-recovery experiment, not proven remediation."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_mpc_audio_exclusive_recovery.py <MPC-BE root>")
root = Path(sys.argv[1])
cpp = root / "src/filters/renderer/MpcAudioRenderer/MpcAudioRenderer.cpp"
hdr = root / "src/filters/renderer/MpcAudioRenderer/MpcAudioRenderer.h"
s = cpp.read_text(encoding="utf-8-sig")
h = hdr.read_text(encoding="utf-8-sig")

def only(inp, old, new, label):
    n = inp.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected 1 anchor; got {n}")
    return inp.replace(old, new, 1)

h=only(h, 'class __declspec(uuid("601D2A2B-9CDE-40bd-8650-0485E3522727"))', 'class __declspec(uuid("601D2A2B-9CDE-40bd-8650-0485E3522727"))', "noop") if False else h
h=only(h, '\tCCritSec          m_csRender;', '''\t// Accessed only under m_csRender (audio rendering thread or synchronized startup).
    // 32 video workers and Windows scheduler remain unrestricted.
    ULONGLONG         m_exclusiveEmptySinceMs = 0;
    ULONGLONG         m_exclusiveRecoveryCooldownMs = 0;
    unsigned          m_exclusiveBufferFailures = 0;

\tCCritSec          m_csRender;''', "audio-only watchdog state")
s=only(s,
'''\thr = m_pRenderClient->GetBuffer(numFramesAvailable, &pData);
\tif (FAILED(hr)) {
#if defined(DEBUG_OR_LOG) && DBGLOG_LEVEL > 1
\t\tDLog(L"CMpcAudioRenderer::RenderWasapiBuffer() - GetBuffer() failed with size %u (0x%08x)", numFramesAvailable, hr);
#endif
\t\treturn hr;
\t}''',
'''\thr = m_pRenderClient->GetBuffer(numFramesAvailable, &pData);
\tif (FAILED(hr)) {
        // Only request audio-side restart on sustained error sequences.
        if (m_DeviceModeCurrent == MODE_WASAPI_EXCLUSIVE
            && m_bIsAudioClientStarted && m_filterState == State_Running) {
            if (++m_exclusiveBufferFailures >= 4) {
                const ULONGLONG now = GetTickCount64();
                if (now >= m_exclusiveRecoveryCooldownMs) {
                    m_exclusiveRecoveryCooldownMs = now + 5000;
                    SetReinitializeAudioDevice(TRUE);
                    OutputDebugStringW(L"MpcAudioRecovery: repeated exclusive GetBuffer failures; requesting audio-only reinit\\n");
                }
                m_exclusiveBufferFailures = 0;
            }
        }
#if defined(DEBUG_OR_LOG) && DBGLOG_LEVEL > 1
\t\tDLog(L"CMpcAudioRenderer::RenderWasapiBuffer() - GetBuffer() failed with size %u (0x%08x)", numFramesAvailable, hr);
#endif
\t\treturn hr;
\t}''', "exclusive GetBuffer failures")
s=only(s,
'''\tconst BOOL bFlushing = m_FlushEvent.Check();
\tif (!pData || !nWasapiQueueSize || m_filterState != State_Running || bFlushing) {''',
'''\tconst BOOL bFlushing = m_FlushEvent.Check();
    const bool exclusivePCMActive = (m_DeviceModeCurrent == MODE_WASAPI_EXCLUSIVE)
        && !m_bIsBitstream && m_bIsAudioClientStarted
        && m_filterState == State_Running && !bFlushing;
    if (exclusivePCMActive && nWasapiQueueSize == 0) {
        const ULONGLONG now = GetTickCount64();
        if (!m_exclusiveEmptySinceMs) {
            m_exclusiveEmptySinceMs = now;
        } else if (now - m_exclusiveEmptySinceMs >= 350
                   && now >= m_exclusiveRecoveryCooldownMs) {
            // This is a persistent audio starvation episode, not a single
            // normal 10-ms empty period. Flag a reinit on the audio input
            // path; don't Stop/Reset while holding the render lock.
            m_exclusiveRecoveryCooldownMs = now + 5000;
            SetReinitializeAudioDevice(TRUE);
            OutputDebugStringW(L"MpcAudioRecovery: 350ms exclusive audio starvation; requesting audio-only reinit\\n");
        }
    } else {
        m_exclusiveEmptySinceMs = 0;
    }
\tif (!pData || !nWasapiQueueSize || m_filterState != State_Running || bFlushing) {''', "prolonged queue-empty watchdog")
s=only(s,
'''\thr = m_pRenderClient->ReleaseBuffer(numFramesAvailable, dwFlags);
#if defined(DEBUG_OR_LOG) && DBGLOG_LEVEL > 1''',
'''\thr = m_pRenderClient->ReleaseBuffer(numFramesAvailable, dwFlags);
    if (SUCCEEDED(hr)) {
        m_exclusiveBufferFailures = 0;
    } else if (m_DeviceModeCurrent == MODE_WASAPI_EXCLUSIVE
               && m_bIsAudioClientStarted && m_filterState == State_Running
               && ++m_exclusiveBufferFailures >= 4) {
        const ULONGLONG now = GetTickCount64();
        if (now >= m_exclusiveRecoveryCooldownMs) {
            m_exclusiveRecoveryCooldownMs = now + 5000;
            SetReinitializeAudioDevice(TRUE);
            OutputDebugStringW(L"MpcAudioRecovery: repeated exclusive ReleaseBuffer failures; requesting audio-only reinit\\n");
        }
        m_exclusiveBufferFailures = 0;
    }
#if defined(DEBUG_OR_LOG) && DBGLOG_LEVEL > 1''', "release errors")
for x in ["GetTickCount64()", "m_exclusiveRecoveryCooldownMs", "350ms exclusive audio starvation"]:
    assert x in s
assert "m_exclusiveEmptySinceMs" in h
assert "SetThreadAffinityMask" not in s
cpp.write_text(s,encoding="utf-8-sig")
hdr.write_text(h,encoding="utf-8-sig")
print("Patched audio Renderer only, no video changes; guarded reinit requests on prolonged exclusive audio starvation or repeated WASAPI buffer errors.")
