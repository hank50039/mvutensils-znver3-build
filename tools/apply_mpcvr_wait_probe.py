from pathlib import Path

ROOT = Path("upstream")
CPP = ROOT / "Source" / "VideoRenderer.cpp"
if not CPP.is_file():
    raise SystemExit(f"Expected MPC Video Renderer source file is missing: {CPP}")

source = CPP.read_text(encoding="utf-8")

def replace_once(old: str, new: str, label: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"Patch guard failed for {label}: expected one match, found {count}")
    source = source.replace(old, new, 1)

replace_once(
    "#include <atomic>\n#include <optional>",
    "#include <atomic>\n#include <cstdarg>\n#include <mutex>\n#include <optional>",
    "add WaitProbe includes",
)

helper = r"""
static std::mutex g_waitProbeLogMutex;

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

#define WAITPROBE_STATE(eventName, phaseName) do { \
    LARGE_INTEGER wps_qpc{}; \
    QueryPerformanceCounter(&wps_qpc); \
    REFERENCE_TIME wps_clock = 0; \
    HRESULT wps_clock_hr = m_pClock ? m_pClock->GetTime(&wps_clock) : E_POINTER; \
    const int wps_input_flush = m_pInputPin ? (m_pInputPin->IsFlushing() ? 1 : 0) : -1; \
    const DWORD wps_render_event = WaitForSingleObject(m_RenderEvent, 0); \
    const DWORD wps_thread_signal = WaitForSingleObject(m_ThreadSignal, 0); \
    WaitProbeLog(L"STATE qpc=%lld event=%ls phase=%ls state=%d filter_state=%d streaming=%d renderer_flushing=%d input_flushing=%d abort=%d advise=%llu run_start_100ns=%lld rt_start_100ns=%lld clock_hr=0x%08X clock_now_100ns=%lld render_event=%lu thread_signal=%lu", \
        static_cast<long long>(wps_qpc.QuadPart), eventName, phaseName, \
        static_cast<int>(m_State), static_cast<int>(m_filterState), static_cast<int>(m_bStreaming), \
        static_cast<int>(m_bFlushing), wps_input_flush, static_cast<int>(m_bAbort), \
        static_cast<unsigned long long>(m_dwAdvise), static_cast<long long>(m_tStart.m_time), \
        static_cast<long long>(m_rtStartTime), static_cast<unsigned int>(wps_clock_hr), \
        static_cast<long long>(wps_clock), static_cast<unsigned long>(wps_render_event), \
        static_cast<unsigned long>(wps_thread_signal)); \
} while (0)
"""

replace_once(
    'static std::atomic_int g_nInstance = 0;\nstatic const wchar_t g_szClassName[] = L"VRWindow";',
    'static std::atomic_int g_nInstance = 0;\nstatic const wchar_t g_szClassName[] = L"VRWindow";\n' + helper,
    "add WaitProbe logger",
)

replace_once(
    'void CMpcVideoRenderer::NewSegment(REFERENCE_TIME startTime)\n{\n\tDLog(L"CMpcVideoRenderer::NewSegment()");\n\n\tm_rtStartTime = startTime;\n}',
    'void CMpcVideoRenderer::NewSegment(REFERENCE_TIME startTime)\n{\n\tDLog(L"CMpcVideoRenderer::NewSegment()");\n\tWAITPROBE_STATE(L"NewSegment", L"enter");\n\n\tm_rtStartTime = startTime;\n\tWAITPROBE_STATE(L"NewSegment", L"leave");\n}',
    "instrument NewSegment",
)

replace_once(
    'HRESULT CMpcVideoRenderer::BeginFlush()\n{\n\tDLog(L"CMpcVideoRenderer::BeginFlush()");\n\n\tm_bFlushing = true;\n\treturn __super::BeginFlush();\n}',
    'HRESULT CMpcVideoRenderer::BeginFlush()\n{\n\tDLog(L"CMpcVideoRenderer::BeginFlush()");\n\tWAITPROBE_STATE(L"BeginFlush", L"enter");\n\n\tm_bFlushing = true;\n\tWAITPROBE_STATE(L"BeginFlush", L"renderer_flag_set");\n\tHRESULT hr = __super::BeginFlush();\n\tWAITPROBE_STATE(L"BeginFlush", L"leave");\n\treturn hr;\n}',
    "instrument BeginFlush",
)

replace_once(
    'HRESULT CMpcVideoRenderer::EndFlush()\n{\n\tDLog(L"CMpcVideoRenderer::EndFlush()");\n\n\tm_VideoProcessor->Flush();\n\n\tHRESULT hr = __super::EndFlush();\n\n\tm_bFlushing = false;\n\n\treturn hr;\n}',
    'HRESULT CMpcVideoRenderer::EndFlush()\n{\n\tDLog(L"CMpcVideoRenderer::EndFlush()");\n\tWAITPROBE_STATE(L"EndFlush", L"enter");\n\n\tm_VideoProcessor->Flush();\n\n\tHRESULT hr = __super::EndFlush();\n\n\tm_bFlushing = false;\n\tWAITPROBE_STATE(L"EndFlush", L"leave");\n\n\treturn hr;\n}',
    "instrument EndFlush",
)

replace_once(
    'STDMETHODIMP CMpcVideoRenderer::Run(REFERENCE_TIME rtStart)\n{\n\tDLog(L"CMpcVideoRenderer::Run()");\n\n\tif (m_State == State_Running) {\n\t\treturn NOERROR;\n\t}\n\n\tCAutoLock cVideoLock(&m_InterfaceLock);\n\tm_filterState = State_Running;\n\n\treturn CBaseVideoRenderer2::Run(rtStart);\n}',
    'STDMETHODIMP CMpcVideoRenderer::Run(REFERENCE_TIME rtStart)\n{\n\tDLog(L"CMpcVideoRenderer::Run()");\n\tWAITPROBE_STATE(L"Run", L"enter");\n\n\tif (m_State == State_Running) {\n\t\tWAITPROBE_STATE(L"Run", L"already_running");\n\t\treturn NOERROR;\n\t}\n\n\tCAutoLock cVideoLock(&m_InterfaceLock);\n\tm_filterState = State_Running;\n\n\tHRESULT hr = CBaseVideoRenderer2::Run(rtStart);\n\tWAITPROBE_STATE(L"Run", L"leave");\n\treturn hr;\n}',
    "instrument Run",
)

replace_once(
    'STDMETHODIMP CMpcVideoRenderer::Pause()\n{\n\tDLog(L"CMpcVideoRenderer::Pause()");\n\n\tm_filterState = State_Paused;\n\n\treturn CBaseVideoRenderer2::Pause();\n}',
    'STDMETHODIMP CMpcVideoRenderer::Pause()\n{\n\tDLog(L"CMpcVideoRenderer::Pause()");\n\tWAITPROBE_STATE(L"Pause", L"enter");\n\n\tm_filterState = State_Paused;\n\n\tHRESULT hr = CBaseVideoRenderer2::Pause();\n\tWAITPROBE_STATE(L"Pause", L"leave");\n\treturn hr;\n}',
    "instrument Pause",
)

replace_once(
    'STDMETHODIMP CMpcVideoRenderer::Stop()\n{\n\tDLog(L"CMpcVideoRenderer::Stop()");\n\n\tm_filterState = State_Stopped;\n\tm_bValidBuffer = false;\n\n\t{\n\t\tCAutoLock cRendererLock(&m_RendererLock);\n\t\tm_VideoProcessor->Flush();\n\t}\n\n\treturn CBaseVideoRenderer2::Stop();\n}',
    'STDMETHODIMP CMpcVideoRenderer::Stop()\n{\n\tDLog(L"CMpcVideoRenderer::Stop()");\n\tWAITPROBE_STATE(L"Stop", L"enter");\n\n\tm_filterState = State_Stopped;\n\tm_bValidBuffer = false;\n\n\t{\n\t\tCAutoLock cRendererLock(&m_RendererLock);\n\t\tm_VideoProcessor->Flush();\n\t}\n\n\tHRESULT hr = CBaseVideoRenderer2::Stop();\n\tWAITPROBE_STATE(L"Stop", L"leave");\n\treturn hr;\n}',
    "instrument Stop",
)

receive_block = r"""
    REFERENCE_TIME wpSampleStart = 0;
    REFERENCE_TIME wpSampleStop = 0;
    const HRESULT wpSampleTimeHr = pSample->GetTime(&wpSampleStart, &wpSampleStop);
    REFERENCE_TIME wpClockBefore = 0;
    const HRESULT wpClockHrBefore = m_pClock ? m_pClock->GetTime(&wpClockBefore) : E_POINTER;
    const REFERENCE_TIME wpRunStartBefore = m_tStart.m_time;
    const REFERENCE_TIME wpRtStartBefore = m_rtStartTime;
    const int wpStateBefore = static_cast<int>(m_State);
    const int wpFilterStateBefore = static_cast<int>(m_filterState);
    const int wpStreamingBefore = static_cast<int>(m_bStreaming);
    const int wpRendererFlushingBefore = m_bFlushing ? 1 : 0;
    const int wpInputFlushingBefore = m_pInputPin ? (m_pInputPin->IsFlushing() ? 1 : 0) : -1;
    const int wpAbortBefore = static_cast<int>(m_bAbort);
    const unsigned long long wpAdviseBefore = static_cast<unsigned long long>(m_dwAdvise);
    const DWORD wpRenderEventBefore = WaitForSingleObject(m_RenderEvent, 0);
    const DWORD wpThreadSignalBefore = WaitForSingleObject(m_ThreadSignal, 0);

    LARGE_INTEGER wpQpcBefore{};
    LARGE_INTEGER wpQpcAfter{};
    QueryPerformanceCounter(&wpQpcBefore);
    hr = WaitForRenderTime();
    QueryPerformanceCounter(&wpQpcAfter);
    const LONGLONG wpWaitUs = WaitProbeMicroseconds(wpQpcBefore.QuadPart, wpQpcAfter.QuadPart);

    REFERENCE_TIME wpClockAfter = 0;
    const HRESULT wpClockHrAfter = m_pClock ? m_pClock->GetTime(&wpClockAfter) : E_POINTER;
    const REFERENCE_TIME wpRunStartAfter = m_tStart.m_time;
    const REFERENCE_TIME wpRtStartAfter = m_rtStartTime;
    const int wpStateAfter = static_cast<int>(m_State);
    const int wpFilterStateAfter = static_cast<int>(m_filterState);
    const int wpStreamingAfter = static_cast<int>(m_bStreaming);
    const int wpRendererFlushingAfter = m_bFlushing ? 1 : 0;
    const int wpInputFlushingAfter = m_pInputPin ? (m_pInputPin->IsFlushing() ? 1 : 0) : -1;
    const int wpAbortAfter = static_cast<int>(m_bAbort);
    const unsigned long long wpAdviseAfter = static_cast<unsigned long long>(m_dwAdvise);
    const DWORD wpRenderEventAfter = WaitForSingleObject(m_RenderEvent, 0);
    const DWORD wpThreadSignalAfter = WaitForSingleObject(m_ThreadSignal, 0);

    const REFERENCE_TIME wpTargetBefore = SUCCEEDED(wpSampleTimeHr) ? wpRunStartBefore + wpSampleStart : 0;
    const REFERENCE_TIME wpTargetAfter = SUCCEEDED(wpSampleTimeHr) ? wpRunStartAfter + wpSampleStart : 0;
    const LONGLONG wpAheadBefore = (SUCCEEDED(wpSampleTimeHr) && SUCCEEDED(wpClockHrBefore))
        ? static_cast<LONGLONG>(wpTargetBefore) - static_cast<LONGLONG>(wpClockBefore) : 0;
    const LONGLONG wpAheadAfter = (SUCCEEDED(wpSampleTimeHr) && SUCCEEDED(wpClockHrAfter))
        ? static_cast<LONGLONG>(wpTargetAfter) - static_cast<LONGLONG>(wpClockAfter) : 0;

    if (wpWaitUs >= 20000 || FAILED(hr)) {
        WaitProbeLog(L"WAITPROBE qpc_before=%lld qpc_after=%lld wait_us=%lld wait_hr=0x%08X sample_time_hr=0x%08X sample_start_100ns=%lld sample_stop_100ns=%lld run_start_before_100ns=%lld run_start_after_100ns=%lld rt_start_before_100ns=%lld rt_start_after_100ns=%lld clock_hr_before=0x%08X clock_before_100ns=%lld clock_hr_after=0x%08X clock_after_100ns=%lld target_before_100ns=%lld ahead_before_100ns=%lld target_after_100ns=%lld ahead_after_100ns=%lld state_before=%d state_after=%d filter_state_before=%d filter_state_after=%d streaming_before=%d streaming_after=%d renderer_flushing_before=%d renderer_flushing_after=%d input_flushing_before=%d input_flushing_after=%d abort_before=%d abort_after=%d advise_before=%llu advise_after=%llu render_event_before=%lu render_event_after=%lu thread_signal_before=%lu thread_signal_after=%lu",
            static_cast<long long>(wpQpcBefore.QuadPart), static_cast<long long>(wpQpcAfter.QuadPart),
            static_cast<long long>(wpWaitUs), static_cast<unsigned int>(hr),
            static_cast<unsigned int>(wpSampleTimeHr), static_cast<long long>(wpSampleStart),
            static_cast<long long>(wpSampleStop), static_cast<long long>(wpRunStartBefore),
            static_cast<long long>(wpRunStartAfter), static_cast<long long>(wpRtStartBefore),
            static_cast<long long>(wpRtStartAfter), static_cast<unsigned int>(wpClockHrBefore),
            static_cast<long long>(wpClockBefore), static_cast<unsigned int>(wpClockHrAfter),
            static_cast<long long>(wpClockAfter), static_cast<long long>(wpTargetBefore),
            static_cast<long long>(wpAheadBefore), static_cast<long long>(wpTargetAfter),
            static_cast<long long>(wpAheadAfter), wpStateBefore, wpStateAfter,
            wpFilterStateBefore, wpFilterStateAfter, wpStreamingBefore, wpStreamingAfter,
            wpRendererFlushingBefore, wpRendererFlushingAfter, wpInputFlushingBefore,
            wpInputFlushingAfter, wpAbortBefore, wpAbortAfter, wpAdviseBefore, wpAdviseAfter,
            static_cast<unsigned long>(wpRenderEventBefore), static_cast<unsigned long>(wpRenderEventAfter),
            static_cast<unsigned long>(wpThreadSignalBefore), static_cast<unsigned long>(wpThreadSignalAfter));
    }

    if (FAILED(hr)) {
"""
# Preserve the original failure block after the line we replace.
receive_block = receive_block.lstrip("\n")\nold_receive = "\thr = WaitForRenderTime();\n\tif (FAILED(hr)) {"
replace_once(old_receive, receive_block, "instrument WaitForRenderTime")

CPP.write_text(source, encoding="utf-8", newline="\n")
print(f"Patched {CPP}: targeted wait timing + Run/Pause/Stop/Flush/NewSegment state traces.")
