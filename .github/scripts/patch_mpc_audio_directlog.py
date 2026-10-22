#!/usr/bin/env python3
"""Add direct-to-file low-frequency audio-only diagnostics. No DbgView required.

Patch this AFTER patch_mpc_audio_exclusive_recovery.py and
patch_mpc_audio_live_diag.py.
Only the MPC audio render thread uses the output file (1 write / 1.5 seconds).
Never modifies video workers, video samples, thread affinity or video priority.
"""
from pathlib import Path
import sys
if len(sys.argv)!=2: raise SystemExit("Usage: patch_mpc_audio_directlog.py <MPC-BE root>")
p=Path(sys.argv[1])/"src/filters/renderer/MpcAudioRenderer/MpcAudioRenderer.cpp"
s=p.read_text(encoding="utf-8-sig")

def once(old,new,label):
    global s
    count=s.count(old)
    if count!=1:
        raise SystemExit(f"{label}: expected exactly one anchor, got {count}")
    s=s.replace(old,new,1)

once('''    OutputDebugStringW(L"MpcAudioDiag: LIVE-V4 RenderThread entered\\n");
    ULONGLONG lastDiagTickMs = 0;''',
'''    OutputDebugStringW(L"MpcAudioDiag: LIVE-V4 RenderThread entered\\n");
    // LIVE-V5: open a single per-PotPlayer PID log. Works without DebugView.
    // File writes are once per 1.5 seconds and buffered by Windows.
    HANDLE liveV5Log = INVALID_HANDLE_VALUE;
    wchar_t liveV5Path[MAX_PATH] = {};
    DWORD nTemp = GetTempPathW(_countof(liveV5Path), liveV5Path);
    if (nTemp && nTemp < _countof(liveV5Path)) {
        wchar_t suffix[96] = {};
        swprintf_s(suffix, _countof(suffix), L"MpcAudioLiveV5_%lu.txt", GetCurrentProcessId());
        if (wcscat_s(liveV5Path, _countof(liveV5Path), suffix) == 0) {
            liveV5Log = CreateFileW(liveV5Path, FILE_APPEND_DATA,
                FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
                FILE_ATTRIBUTE_NORMAL, nullptr);
        }
    }
    if (liveV5Log != INVALID_HANDLE_VALUE) {
        const char* ready = "MpcAudioDiag: LIVE-V5 direct log started on RenderThread\\r\\n";
        DWORD written = 0;
        WriteFile(liveV5Log, ready, static_cast<DWORD>(strlen(ready)), &written, nullptr);
    } else {
        OutputDebugStringW(L"MpcAudioDiag: LIVE-V5 direct log creation FAILED\\n");
    }
    ULONGLONG lastDiagTickMs = 0;''', "render-thread direct log initialization")

once('''            OutputDebugStringW(line);
        }
\t\tCheckBufferStatus();''',
'''            OutputDebugStringW(line);
            if (liveV5Log != INVALID_HANDLE_VALUE) {
                char encoded[1024] = {};
                int bytes = WideCharToMultiByte(CP_UTF8, 0, line, -1,
                    encoded, _countof(encoded), nullptr, nullptr);
                if (bytes > 1) {
                    DWORD written = 0;
                    WriteFile(liveV5Log, encoded,
                        static_cast<DWORD>(bytes - 1), &written, nullptr);
                }
            }
        }
\t\tCheckBufferStatus();''', "heartbeat log writing")

once('''\tif (hTaskHandle != nullptr) {
\t\tpfAvRevertMmThreadCharacteristics(hTaskHandle);
\t}''',
'''    if (liveV5Log != INVALID_HANDLE_VALUE) {
        const char* done = "MpcAudioDiag: LIVE-V5 RenderThread exit\\r\\n";
        DWORD written = 0;
        WriteFile(liveV5Log, done, static_cast<DWORD>(strlen(done)), &written, nullptr);
        CloseHandle(liveV5Log);
    }
\tif (hTaskHandle != nullptr) {
\t\tpfAvRevertMmThreadCharacteristics(hTaskHandle);
\t}''', "close audio log on render thread exit")

assert "MpcAudioLiveV5_%lu.txt" in s
assert "MpcAudioDiag: LIVE-V4 beat" in s
assert "SetThreadAffinityMask" not in s
p.write_text(s,encoding="utf-8-sig")
print("MPC Audio direct log LIVE-V5 added. Per-PID text file at Windows %TEMP%; no external debug monitor.")
