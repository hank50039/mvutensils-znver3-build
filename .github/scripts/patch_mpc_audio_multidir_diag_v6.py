#!/usr/bin/env python3
"""Fix V5's single-%TEMP% blind spot. Keep original video pipeline unaffected.

After the V5 patch, provide V6 diagnostic file creation with fallbacks:
%PUBLIC%\Documents, %LOCALAPPDATA%\Temp, and GetTempPathW.
Additionally mark successful MPC Audio filter *construction* before RenderThread
starts. This discriminates 'created but no audio thread' from no file access.
"""
from pathlib import Path
import sys
if len(sys.argv)!=2: raise SystemExit("Usage: patch_mpc_audio_multidir_diag_v6.py <MPC-BE root>")
p=Path(sys.argv[1])/"src/filters/renderer/MpcAudioRenderer/MpcAudioRenderer.cpp"
s=p.read_text(encoding="utf-8-sig")
def replace_once(old,new,label):
    global s
    n=s.count(old)
    if n!=1: raise SystemExit(f"{label}: expected 1 anchor, found {n}")
    s=s.replace(old,new,1)

helper=r'''
// LIVE-V6: use several explicit writable locations instead of assuming the
// running PotPlayer's temp directory equals the collector process's TEMP.
static HANDLE OpenMpcAudioLiveV6Log(wchar_t (&selectedPath)[MAX_PATH])
{
    selectedPath[0] = 0;
    wchar_t folder[MAX_PATH] = {};
    wchar_t candidates[3][MAX_PATH] = {};
    const DWORD pid = GetCurrentProcessId();
    DWORD length = GetEnvironmentVariableW(L"PUBLIC", folder, _countof(folder));
    if (length && length < _countof(folder)) {
        swprintf_s(candidates[0], _countof(candidates[0]),
            L"%ls\\Documents\\MpcAudioLiveV6_%lu.txt", folder, pid);
    }
    length = GetEnvironmentVariableW(L"LOCALAPPDATA", folder, _countof(folder));
    if (length && length < _countof(folder)) {
        swprintf_s(candidates[1], _countof(candidates[1]),
            L"%ls\\Temp\\MpcAudioLiveV6_%lu.txt", folder, pid);
    }
    folder[0] = 0;
    length = GetTempPathW(_countof(folder), folder);
    if (length && length < _countof(folder)) {
        swprintf_s(candidates[2], _countof(candidates[2]),
            L"%lsMpcAudioLiveV6_%lu.txt", folder, pid);
    }
    for (const auto& candidate : candidates) {
        if (!candidate[0]) continue;
        HANDLE handle = CreateFileW(candidate, FILE_APPEND_DATA,
            FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle != INVALID_HANDLE_VALUE) {
            wcscpy_s(selectedPath, candidate);
            return handle;
        }
    }
    return INVALID_HANDLE_VALUE;
}
'''
replace_once('CMpcAudioRenderer::CMpcAudioRenderer(LPUNKNOWN punk, HRESULT *phr)', helper+'\nCMpcAudioRenderer::CMpcAudioRenderer(LPUNKNOWN punk, HRESULT *phr)', "global diagnostic file opener")

replace_once('''\tDLog(L"CMpcAudioRenderer::CMpcAudioRenderer()");''',
'''\\tDLog(L"CMpcAudioRenderer::CMpcAudioRenderer()");
    // Created even before the audio render worker exists; proves filter
    // object instantiation and the actual log path independent of Win32 capture.
    {
        wchar_t path[MAX_PATH] = {};
        HANDLE marker = OpenMpcAudioLiveV6Log(path);
        if (marker != INVALID_HANDLE_VALUE) {
            const char start[] = "MpcAudioDiag: LIVE-V6 filter constructor entered\\r\\n";
            DWORD written = 0;
            WriteFile(marker, start, sizeof(start)-1, &written, nullptr);
            CloseHandle(marker);
        }
    }'''.replace('\\t','\t'), "constructor marker")

start=s.find('''    // LIVE-V5: open a single per-PotPlayer PID log.''')
end=s.find('''    ULONGLONG lastDiagTickMs = 0;''', start)
if start < 0 or end < 0: raise SystemExit("LIVE-V5 logger block not found")
replacement='''    // LIVE-V6: shared logger search locations. One open per audio thread.
    HANDLE liveV6Log = INVALID_HANDLE_VALUE;
    wchar_t liveV6Path[MAX_PATH] = {};
    liveV6Log = OpenMpcAudioLiveV6Log(liveV6Path);
    if (liveV6Log != INVALID_HANDLE_VALUE) {
        const char* ready = "MpcAudioDiag: LIVE-V6 render worker started\\\\r\\\\n";
        DWORD written = 0;
        WriteFile(liveV6Log, ready, static_cast<DWORD>(strlen(ready)), &written, nullptr);
    } else {
        OutputDebugStringW(L"MpcAudioDiag: LIVE-V6 could not open any diagnostic log location\\\\n");
    }
'''
# Emit actual carriage returns via C++ literal escape sequences, not doubled backslash.
replacement=replacement.replace('\\\\r\\\\n',r'\r\n').replace('\\\\n',r'\n')
s=s[:start]+replacement+s[end:]
if s.count("liveV5Log")!=5: raise SystemExit("Expected 5 V5 heartbeat/exit handle usages")
s=s.replace("liveV5Log", "liveV6Log")
s=s.replace("LIVE-V5 RenderThread exit", "LIVE-V6 RenderThread exit")
s=s.replace("MpcAudioDiag: LIVE-V5 direct log started", "MpcAudioDiag: LIVE-V6 direct log started")
assert "MpcAudioLiveV6_%lu.txt" in s
assert "LIVE-V6 filter constructor entered" in s
assert "LIVE-V4 beat" in s
assert "SetThreadAffinityMask" not in s
p.write_text(s,encoding="utf-8-sig")
print("LIVE-V6 patch applied: constructor marker and audio-worker heartbeat log with 3 directory fallbacks.")
