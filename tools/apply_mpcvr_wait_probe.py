from pathlib import Path

ROOT = Path("upstream")
CPP = ROOT / "Source" / "VideoRenderer.cpp"
HEADER = ROOT / "Source" / "VideoRenderer.h"
if not CPP.is_file() or not HEADER.is_file():
    raise SystemExit("Expected MPC Video Renderer source files are missing.")

source = CPP.read_text(encoding="utf-8")
header = HEADER.read_text(encoding="utf-8")

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Patch guard failed for {label}: expected one match, found {count}")
    return text.replace(old, new, 1)

# Add only the override declaration; frame delivery itself remains unchanged.
header = replace_once(
    header,
    "\tHRESULT Receive(IMediaSample* pMediaSample) override;",
    "\tHRESULT Receive(IMediaSample* pMediaSample) override;\n\tHRESULT WaitForRenderTime() override;",
    "declare targeted wait probe override",
)

source = replace_once(
    source,
    "#include <atomic>\n#include <optional>",
    "#include <atomic>\n#include <cstdarg>\n#include <mutex>\n#include <optional>",
    "add wait probe includes",
)

helper = r"""
static std::mutex g_waitProbeLogMutex;
static std::atomic_int g_waitProbeBudget{12};

static LONGLONG WaitProbeQpcFrequency()
{
    static const LONGLONG frequency = []() -> LONGLONG {
        LARGE_INTEGER value{};
        return QueryPerformanceFrequency(&value) && value.QuadPart > 0 ? value.QuadPart : 1;
    }();
    return frequency;
}

static LONGLONG WaitProbeMicroseconds(LONGLONG begin, LONGLONG end)
{
    return ((end - begin) * 1000000LL) / WaitProbeQpcFrequency();
}

static void WaitProbeResetBudget()
{
    // Only reset an atomic counter at segment/flush boundaries; no I/O or clock calls here.
    g_waitProbeBudget.store(12, std::memory_order_relaxed);
}

static bool WaitProbeConsumeBudget()
{
    int remaining = g_waitProbeBudget.load(std::memory_order_relaxed);
    while (remaining > 0) {
        if (g_waitProbeBudget.compare_exchange_weak(
                remaining, remaining - 1, std::memory_order_relaxed)) {
            return true;
        }
    }
    return false;
}

static void WaitProbeLog(const wchar_t* format, ...)
{
    std::lock_guard<std::mutex> guard(g_waitProbeLogMutex);

    wchar_t base[MAX_PATH]{};
    const DWORD envLength = GetEnvironmentVariableW(L"LOCALAPPDATA", base, _countof(base));
    if (envLength == 0 || envLength >= _countof(base)) {
        return;
    }

    wchar_t path[MAX_PATH]{};
    if (wcscpy_s(path, _countof(path), base) != 0 ||
        wcscat_s(path, _countof(path), L"\\MPCVideoRendererWaitProbeDiag.txt") != 0) {
        return;
    }

    wchar_t line[2048]{};
    va_list args;
    va_start(args, format);
    _vsnwprintf_s(line, _countof(line), _TRUNCATE, format, args);
    va_end(args);

    const size_t lineLength = wcslen(line);
    if (lineLength + 2 >= _countof(line)) {
        return;
    }
    line[lineLength] = L'\r';
    line[lineLength + 1] = L'\n';
    line[lineLength + 2] = L'\0';

    char utf8[8192]{};
    const int byteCount = WideCharToMultiByte(CP_UTF8, 0, line, -1, utf8,
        static_cast<int>(_countof(utf8)), nullptr, nullptr);
    if (byteCount <= 1) {
        return;
    }

    HANDLE file = CreateFileW(path, FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file != INVALID_HANDLE_VALUE) {
        DWORD written = 0;
        WriteFile(file, utf8, static_cast<DWORD>(byteCount - 1), &written, nullptr);
        CloseHandle(file);
    }
}
"""

source = replace_once(
    source,
    'static std::atomic_int g_nInstance = 0;\nstatic const wchar_t g_szClassName[] = L"VRWindow";',
    'static std::atomic_int g_nInstance = 0;\nstatic const wchar_t g_szClassName[] = L"VRWindow";\n' + helper,
    "add bounded wait probe logger",
)

new_segment_old = (
    'void CMpcVideoRenderer::NewSegment(REFERENCE_TIME startTime)\n'
    '{\n'
    '\tDLog(L"CMpcVideoRenderer::NewSegment()");\n\n'
    '\tm_rtStartTime = startTime;\n'
    '}'
)
new_segment_new = (
    'void CMpcVideoRenderer::NewSegment(REFERENCE_TIME startTime)\n'
    '{\n'
    '\tDLog(L"CMpcVideoRenderer::NewSegment()");\n'
    '\tWaitProbeResetBudget();\n\n'
    '\tm_rtStartTime = startTime;\n'
    '}'
)
source = replace_once(source, new_segment_old, new_segment_new, "reset budget on NewSegment")

end_flush_old = (
    'HRESULT CMpcVideoRenderer::EndFlush()\n'
    '{\n'
    '\tDLog(L"CMpcVideoRenderer::EndFlush()");\n\n'
    '\tm_VideoProcessor->Flush();\n\n'
    '\tHRESULT hr = __super::EndFlush();\n\n'
    '\tm_bFlushing = false;\n\n'
    '\treturn hr;\n'
)
end_flush_new = (
    'HRESULT CMpcVideoRenderer::EndFlush()\n'
    '{\n'
    '\tDLog(L"CMpcVideoRenderer::EndFlush()");\n\n'
    '\tm_VideoProcessor->Flush();\n\n'
    '\tHRESULT hr = __super::EndFlush();\n\n'
    '\tm_bFlushing = false;\n'
    '\tWaitProbeResetBudget();\n\n'
    '\treturn hr;\n'
)
source = replace_once(source, end_flush_old, end_flush_new, "reset budget on EndFlush")

wait_method = r"""
HRESULT CMpcVideoRenderer::WaitForRenderTime()
{
    const bool logEntry = WaitProbeConsumeBudget();
    REFERENCE_TIME sampleStart = 0;
    REFERENCE_TIME sampleStop = 0;
    HRESULT sampleTimeHr = E_POINTER;
    if (logEntry && m_pMediaSample) {
        sampleTimeHr = m_pMediaSample->GetTime(&sampleStart, &sampleStop);
    }

    LARGE_INTEGER entryQpc{};
    QueryPerformanceCounter(&entryQpc);
    if (logEntry) {
        WaitProbeLog(L"WAITPROBE_ENTER qpc=%lld sample_time_hr=0x%08X sample_start_100ns=%lld sample_stop_100ns=%lld state=%d filter_state=%d streaming=%d renderer_flushing=%d abort=%d advise=%llu run_start_100ns=%lld rt_start_100ns=%lld",
            static_cast<long long>(entryQpc.QuadPart),
            static_cast<unsigned int>(sampleTimeHr),
            static_cast<long long>(sampleStart),
            static_cast<long long>(sampleStop),
            static_cast<int>(m_State),
            static_cast<int>(m_filterState),
            static_cast<int>(m_bStreaming),
            static_cast<int>(m_bFlushing),
            static_cast<int>(m_bAbort),
            static_cast<unsigned long long>(m_dwAdvise),
            static_cast<long long>(m_tStart.m_time),
            static_cast<long long>(m_rtStartTime));
    }

    LARGE_INTEGER waitBegin{};
    LARGE_INTEGER waitEnd{};
    QueryPerformanceCounter(&waitBegin);
    // Never poll m_RenderEvent to inspect its state: it is an auto-reset event, and
    // WaitForSingleObject(..., 0) can consume the wake-up needed by the base wait.
    // The base wait below performs the original scheduling/wait behavior.
    HRESULT hr = CBaseVideoRenderer2::WaitForRenderTime();
    QueryPerformanceCounter(&waitEnd);
    const LONGLONG waitUs = WaitProbeMicroseconds(waitBegin.QuadPart, waitEnd.QuadPart);

    if (logEntry || waitUs >= 20000 || FAILED(hr)) {
        REFERENCE_TIME afterStart = 0;
        REFERENCE_TIME afterStop = 0;
        HRESULT afterSampleTimeHr = E_POINTER;
        if (m_pMediaSample) {
            afterSampleTimeHr = m_pMediaSample->GetTime(&afterStart, &afterStop);
        }
        WaitProbeLog(L"WAITPROBE_EXIT qpc_begin=%lld qpc_end=%lld wait_us=%lld wait_hr=0x%08X sample_time_hr=0x%08X sample_start_100ns=%lld sample_stop_100ns=%lld state=%d filter_state=%d streaming=%d renderer_flushing=%d abort=%d advise=%llu run_start_100ns=%lld rt_start_100ns=%lld",
            static_cast<long long>(waitBegin.QuadPart),
            static_cast<long long>(waitEnd.QuadPart),
            static_cast<long long>(waitUs),
            static_cast<unsigned int>(hr),
            static_cast<unsigned int>(afterSampleTimeHr),
            static_cast<long long>(afterStart),
            static_cast<long long>(afterStop),
            static_cast<int>(m_State),
            static_cast<int>(m_filterState),
            static_cast<int>(m_bStreaming),
            static_cast<int>(m_bFlushing),
            static_cast<int>(m_bAbort),
            static_cast<unsigned long long>(m_dwAdvise),
            static_cast<long long>(m_tStart.m_time),
            static_cast<long long>(m_rtStartTime));
    }
    return hr;
}
"""

# Place the override after NewSegment, keeping the original Receive and lifecycle methods intact.
source = replace_once(
    source,
    new_segment_new,
    new_segment_new + "\n\n" + wait_method.strip("\n"),
    "add WaitForRenderTime override",
)

if "WAITPROBE_STATE" in source or "const REFERENCE_TIME wpRunStartBefore" in source:
    raise SystemExit("Old invasive state probes remain in source; refusing to continue.")

CPP.write_text(source, encoding="utf-8", newline="\n")
HEADER.write_text(header, encoding="utf-8", newline="\n")
print(f"Patched {CPP} and {HEADER}: bounded wait tracing without event-handle polling; NewSegment/EndFlush only reset an atomic log budget.")
