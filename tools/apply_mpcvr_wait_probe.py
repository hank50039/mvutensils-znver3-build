from pathlib import Path

ROOT = Path("upstream")
CPP = ROOT / "Source" / "VideoRenderer.cpp"
HEADER = ROOT / "Source" / "VideoRenderer.h"
DX11_CPP = ROOT / "Source" / "DX11VideoProcessor.cpp"
RENBASE2_CPP = ROOT / "Source" / "renbase2.cpp"
BASE_RENDERER_CPP = ROOT / "external" / "BaseClasses" / "renbase.cpp"
if not all(path.is_file() for path in (CPP, HEADER, DX11_CPP, RENBASE2_CPP, BASE_RENDERER_CPP)):
    raise SystemExit("Expected MPC Video Renderer and BaseClasses scheduler sources are missing.")

source = CPP.read_text(encoding="utf-8")
header = HEADER.read_text(encoding="utf-8")
dx11_source = DX11_CPP.read_text(encoding="utf-8")
renbase2_source = RENBASE2_CPP.read_text(encoding="utf-8")
base_renderer_source = BASE_RENDERER_CPP.read_text(encoding="utf-8")

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

header = replace_once(
    header,
    '#include "SubPic/ISubPic.h"',
    '#include "SubPic/ISubPic.h"\n\nvoid MPCVRProbeLog(const wchar_t* format, ...);\nLONGLONG MPCVRProbeElapsedUs(LONGLONG begin, LONGLONG end);\nbool MPCVRProbeShouldLogStage(LONGLONG elapsedUs, HRESULT hr);\nvoid MPCVRProbeSetSchedulerContext(const wchar_t* context);\nvoid MPCVRProbeAppendSchedulerContext(const wchar_t* context);\nvoid MPCVRProbeConsumeSchedulerContext(wchar_t* dest, size_t destCount);',
    "declare stage probe helpers",
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
static std::atomic_int g_stageProbeBudget{128};
static std::atomic_int g_hardStageProbeBudget{64};
static thread_local wchar_t g_schedulerProbeContext[1024]{};

void MPCVRProbeSetSchedulerContext(const wchar_t* context)
{
    if (!context) {
        g_schedulerProbeContext[0] = L'\0';
        return;
    }
    wcsncpy_s(g_schedulerProbeContext, _countof(g_schedulerProbeContext), context, _TRUNCATE);
}

void MPCVRProbeAppendSchedulerContext(const wchar_t* context)
{
    if (!context || !context[0]) {
        return;
    }
    const size_t currentLength = wcslen(g_schedulerProbeContext);
    if (currentLength == 0) {
        wcsncpy_s(g_schedulerProbeContext, _countof(g_schedulerProbeContext), context, _TRUNCATE);
        return;
    }
    if (currentLength + 2 >= _countof(g_schedulerProbeContext)) {
        return;
    }
    g_schedulerProbeContext[currentLength] = L';';
    g_schedulerProbeContext[currentLength + 1] = L'\0';
    wcsncat_s(g_schedulerProbeContext, _countof(g_schedulerProbeContext), context, _TRUNCATE);
}

void MPCVRProbeConsumeSchedulerContext(wchar_t* dest, size_t destCount)
{
    if (!dest || destCount == 0) {
        return;
    }
    wcsncpy_s(dest, destCount, g_schedulerProbeContext, _TRUNCATE);
    g_schedulerProbeContext[0] = L'\0';
}

static LONGLONG WaitProbeQpcFrequency()
{
    static const LONGLONG frequency = []() -> LONGLONG {
        LARGE_INTEGER value{};
        return QueryPerformanceFrequency(&value) && value.QuadPart > 0 ? value.QuadPart : 1;
    }();
    return frequency;
}

LONGLONG MPCVRProbeElapsedUs(LONGLONG begin, LONGLONG end)
{
    return ((end - begin) * 1000000LL) / WaitProbeQpcFrequency();
}

static bool WaitProbeConsume(std::atomic_int& budget)
{
    int remaining = budget.load(std::memory_order_relaxed);
    while (remaining > 0) {
        if (budget.compare_exchange_weak(
                remaining, remaining - 1, std::memory_order_relaxed)) {
            return true;
        }
    }
    return false;
}

bool MPCVRProbeShouldLogStage(LONGLONG elapsedUs, HRESULT hr)
{
    if (elapsedUs >= 100000) {
        return WaitProbeConsume(g_hardStageProbeBudget);
    }
    if (elapsedUs < 20000 && SUCCEEDED(hr)) {
        return false;
    }
    return WaitProbeConsume(g_stageProbeBudget);
}

static void WaitProbeResetBudget()
{
    // Reset counters only; never perform diagnostic I/O from lifecycle methods.
    g_waitProbeBudget.store(12, std::memory_order_relaxed);
    g_stageProbeBudget.store(128, std::memory_order_relaxed);
    g_hardStageProbeBudget.store(64, std::memory_order_relaxed);
}

static bool WaitProbeConsumeBudget()
{
    return WaitProbeConsume(g_waitProbeBudget);
}

void MPCVRProbeLog(const wchar_t* format, ...)
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
    void* rendererInstance = static_cast<void*>(this);
    const DWORD waitProcessId = GetCurrentProcessId();
    const DWORD waitThreadId = GetCurrentThreadId();
    REFERENCE_TIME sampleStartAtEntry = 0;
    REFERENCE_TIME sampleStopAtEntry = 0;
    HRESULT sampleTimeHrAtEntry = m_pMediaSample
        ? m_pMediaSample->GetTime(&sampleStartAtEntry, &sampleStopAtEntry)
        : E_POINTER;
    const int waitStateAtEntry = static_cast<int>(m_State);
    const int waitFilterStateAtEntry = static_cast<int>(m_filterState);
    const int waitStreamingAtEntry = static_cast<int>(m_bStreaming);
    const int waitFlushingAtEntry = static_cast<int>(m_bFlushing);
    const int waitAbortAtEntry = static_cast<int>(m_bAbort);
    const unsigned long long waitAdviseAtEntry = static_cast<unsigned long long>(m_dwAdvise);
    const REFERENCE_TIME waitRunStartAtEntry = m_tStart.m_time;
    const REFERENCE_TIME waitRtStartAtEntry = m_rtStartTime;

    LARGE_INTEGER entryQpc{};
    QueryPerformanceCounter(&entryQpc);
    if (logEntry) {
        MPCVRProbeLog(L"WAITPROBE_ENTER renderer_instance=%p process_id=%lu thread_id=%lu qpc=%lld sample_time_hr=0x%08X sample_start_100ns=%lld sample_stop_100ns=%lld state=%d filter_state=%d streaming=%d renderer_flushing=%d abort=%d advise=%llu run_start_100ns=%lld rt_start_100ns=%lld",
            rendererInstance,
            static_cast<unsigned long>(waitProcessId),
            static_cast<unsigned long>(waitThreadId),
            static_cast<long long>(entryQpc.QuadPart),
            static_cast<unsigned int>(sampleTimeHrAtEntry),
            static_cast<long long>(sampleStartAtEntry),
            static_cast<long long>(sampleStopAtEntry),
            waitStateAtEntry,
            waitFilterStateAtEntry,
            waitStreamingAtEntry,
            waitFlushingAtEntry,
            waitAbortAtEntry,
            waitAdviseAtEntry,
            static_cast<long long>(waitRunStartAtEntry),
            static_cast<long long>(waitRtStartAtEntry));
    }

    LARGE_INTEGER waitBegin{};
    LARGE_INTEGER waitEnd{};
    QueryPerformanceCounter(&waitBegin);
    // Never poll m_RenderEvent to inspect its state: it is an auto-reset event, and
    // WaitForSingleObject(..., 0) can consume the wake-up needed by the base wait.
    // The base wait below performs the original scheduling/wait behavior.
    HRESULT hr = CBaseVideoRenderer2::WaitForRenderTime();
    QueryPerformanceCounter(&waitEnd);
    const LONGLONG waitUs = MPCVRProbeElapsedUs(waitBegin.QuadPart, waitEnd.QuadPart);

    if (logEntry || waitUs >= 20000 || FAILED(hr)) {
        REFERENCE_TIME afterStart = 0;
        REFERENCE_TIME afterStop = 0;
        HRESULT afterSampleTimeHr = E_POINTER;
        if (m_pMediaSample) {
            afterSampleTimeHr = m_pMediaSample->GetTime(&afterStart, &afterStop);
        }
        MPCVRProbeLog(L"WAITPROBE_EXIT renderer_instance=%p process_id=%lu thread_id=%lu qpc_begin=%lld qpc_end=%lld wait_us=%lld wait_hr=0x%08X sample_begin_time_hr=0x%08X sample_begin_start_100ns=%lld sample_begin_stop_100ns=%lld sample_end_time_hr=0x%08X sample_end_start_100ns=%lld sample_end_stop_100ns=%lld state_begin=%d state_end=%d filter_state_begin=%d filter_state_end=%d streaming_begin=%d streaming_end=%d flushing_begin=%d flushing_end=%d abort_begin=%d abort_end=%d advise_begin=%llu advise_end=%llu run_start_begin_100ns=%lld run_start_end_100ns=%lld rt_start_begin_100ns=%lld rt_start_end_100ns=%lld",
            rendererInstance,
            static_cast<unsigned long>(waitProcessId),
            static_cast<unsigned long>(waitThreadId),
            static_cast<long long>(waitBegin.QuadPart),
            static_cast<long long>(waitEnd.QuadPart),
            static_cast<long long>(waitUs),
            static_cast<unsigned int>(hr),
            static_cast<unsigned int>(sampleTimeHrAtEntry),
            static_cast<long long>(sampleStartAtEntry),
            static_cast<long long>(sampleStopAtEntry),
            static_cast<unsigned int>(afterSampleTimeHr),
            static_cast<long long>(afterStart),
            static_cast<long long>(afterStop),
            waitStateAtEntry,
            static_cast<int>(m_State),
            waitFilterStateAtEntry,
            static_cast<int>(m_filterState),
            waitStreamingAtEntry,
            static_cast<int>(m_bStreaming),
            waitFlushingAtEntry,
            static_cast<int>(m_bFlushing),
            waitAbortAtEntry,
            static_cast<int>(m_bAbort),
            waitAdviseAtEntry,
            static_cast<unsigned long long>(m_dwAdvise),
            static_cast<long long>(waitRunStartAtEntry),
            static_cast<long long>(m_tStart.m_time),
            static_cast<long long>(waitRtStartAtEntry),
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



# Instrument the complete synchronous Receive call, but only log slow/failing exits.
receive_start_marker = "HRESULT CMpcVideoRenderer::Receive(IMediaSample* pSample)\n{"
receive_start = source.find(receive_start_marker)
receive_end = source.find("\nvoid CMpcVideoRenderer::UpdateDisplayInfo()", receive_start)
if receive_start < 0 or receive_end < 0:
    raise SystemExit("Could not locate CMpcVideoRenderer::Receive for timing.")
receive_block = source[receive_start:receive_end]
receive_setup = r"""
    LARGE_INTEGER receiveProbeBegin{};
    QueryPerformanceCounter(&receiveProbeBegin);
    void* receiveRendererInstance = static_cast<void*>(this);
    const DWORD receiveProcessId = GetCurrentProcessId();
    const DWORD receiveThreadId = GetCurrentThreadId();
    REFERENCE_TIME receiveSampleStartAtEntry = 0;
    REFERENCE_TIME receiveSampleStopAtEntry = 0;
    const HRESULT receiveSampleTimeHrAtEntry = pSample
        ? pSample->GetTime(&receiveSampleStartAtEntry, &receiveSampleStopAtEntry)
        : E_POINTER;
    const int receiveStateBefore = static_cast<int>(m_State);
    const int receiveFilterStateBefore = static_cast<int>(m_filterState);
    const int receiveStreamingBefore = static_cast<int>(m_bStreaming);
    const int receiveFlushingBefore = static_cast<int>(m_bFlushing);
    auto logReceiveProbe = [&](const wchar_t* phase, HRESULT result) {
        LARGE_INTEGER receiveProbeEnd{};
        QueryPerformanceCounter(&receiveProbeEnd);
        const LONGLONG receiveUs = MPCVRProbeElapsedUs(receiveProbeBegin.QuadPart, receiveProbeEnd.QuadPart);
        if (!MPCVRProbeShouldLogStage(receiveUs, result)) {
            return;
        }
        wchar_t schedulerContext[1024]{};
        MPCVRProbeConsumeSchedulerContext(schedulerContext, _countof(schedulerContext));
        REFERENCE_TIME sampleStart = 0;
        REFERENCE_TIME sampleStop = 0;
        const HRESULT sampleTimeHr = pSample ? pSample->GetTime(&sampleStart, &sampleStop) : E_POINTER;
        MPCVRProbeLog(L"RECEIVEPROBE renderer_instance=%p process_id=%lu thread_id=%lu qpc_begin=%lld qpc_end=%lld receive_us=%lld phase=%ls result_hr=0x%08X sample_begin_time_hr=0x%08X sample_begin_start_100ns=%lld sample_begin_stop_100ns=%lld sample_end_time_hr=0x%08X sample_end_start_100ns=%lld sample_end_stop_100ns=%lld state_before=%d state_after=%d filter_state_before=%d filter_state_after=%d streaming_before=%d streaming_after=%d flushing_before=%d flushing_after=%d scheduler_context=%ls",
            receiveRendererInstance,
            static_cast<unsigned long>(receiveProcessId),
            static_cast<unsigned long>(receiveThreadId),
            static_cast<long long>(receiveProbeBegin.QuadPart),
            static_cast<long long>(receiveProbeEnd.QuadPart),
            static_cast<long long>(receiveUs),
            phase,
            static_cast<unsigned int>(result),
            static_cast<unsigned int>(receiveSampleTimeHrAtEntry),
            static_cast<long long>(receiveSampleStartAtEntry),
            static_cast<long long>(receiveSampleStopAtEntry),
            static_cast<unsigned int>(sampleTimeHr),
            static_cast<long long>(sampleStart),
            static_cast<long long>(sampleStop),
            receiveStateBefore,
            static_cast<int>(m_State),
            receiveFilterStateBefore,
            static_cast<int>(m_filterState),
            receiveStreamingBefore,
            static_cast<int>(m_bStreaming),
            receiveFlushingBefore,
            static_cast<int>(m_bFlushing),
            schedulerContext);
    };
"""
receive_block = receive_block.replace(
    receive_start_marker,
    receive_start_marker + "\n" + receive_setup.rstrip("\n"),
    1)
receive_block = replace_once(
    receive_block,
    '\tif (m_bFlushing) {\n\t\tDLog(L"CMpcVideoRenderer::Receive() - flushing, skip sample");\n\t\treturn S_OK;\n\t}',
    '\tif (m_bFlushing) {\n\t\tDLog(L"CMpcVideoRenderer::Receive() - flushing, skip sample");\n\t\tlogReceiveProbe(L"flushing_skip", S_OK);\n\t\treturn S_OK;\n}',
    "probe flushing Receive exit")
receive_block = replace_once(
    receive_block,
    '\tif (FAILED(hr)) {\n\t\tif (hr == VFW_E_SAMPLE_REJECTED) {\n\t\t\treturn NOERROR;\n\t\t}\n\t\treturn hr;\n\t}',
    '\tif (FAILED(hr)) {\n\t\tif (hr == VFW_E_SAMPLE_REJECTED) {\n\t\t\tlogReceiveProbe(L"sample_rejected", hr);\n\t\t\treturn NOERROR;\n\t\t}\n\t\tlogReceiveProbe(L"prepare_receive_failed", hr);\n\t\treturn hr;\n\t}',
    "probe PrepareReceive exits")
receive_block = replace_once(
    receive_block,
    '\t\t\tif (m_State == State_Stopped)\n\t\t\t\treturn NOERROR;',
    '\t\t\tif (m_State == State_Stopped) {\n\t\t\t\tlogReceiveProbe(L"stopped_after_pause_lock", NOERROR);\n\t\t\t\treturn NOERROR;\n\t\t\t}',
    "probe stopped during pause")
receive_block = replace_once(
    receive_block,
    '\tif (FAILED(hr)) {\n\t\tm_bInReceive = FALSE;\n\t\treturn NOERROR;\n\t}',
    '\tif (FAILED(hr)) {\n\t\tm_bInReceive = FALSE;\n\t\tlogReceiveProbe(L"wait_for_render_time_failed", hr);\n\t\treturn NOERROR;\n\t}',
    "probe wait failure exit")
receive_block = replace_once(
    receive_block,
    '\tif (m_State == State_Stopped)\n\t\treturn NOERROR;',
    '\tif (m_State == State_Stopped) {\n\t\tlogReceiveProbe(L"stopped_after_wait", NOERROR);\n\t\treturn NOERROR;\n\t}',
    "probe stopped after wait")
receive_block = replace_once(
    receive_block,
    '\treturn NOERROR;\n}',
    '\tlogReceiveProbe(L"normal_exit", NOERROR);\n\treturn NOERROR;\n}',
    "probe normal Receive exit")
source = source[:receive_start] + receive_block + source[receive_end:]

# Time the whole video-processor call in DoRenderSample separately from WaitForRenderTime.
source = replace_once(
    source,
    '\tHRESULT hr = m_VideoProcessor->ProcessSample(pSample);',
    r"""	LARGE_INTEGER processProbeBegin{};
	QueryPerformanceCounter(&processProbeBegin);
	HRESULT hr = m_VideoProcessor->ProcessSample(pSample);
	LARGE_INTEGER processProbeEnd{};
	QueryPerformanceCounter(&processProbeEnd);
	const LONGLONG processProbeUs = MPCVRProbeElapsedUs(processProbeBegin.QuadPart, processProbeEnd.QuadPart);
	if (MPCVRProbeShouldLogStage(processProbeUs, hr)) {
		REFERENCE_TIME sampleStart = 0;
		REFERENCE_TIME sampleStop = 0;
		const HRESULT sampleTimeHr = pSample->GetTime(&sampleStart, &sampleStop);
		MPCVRProbeLog(L"PROCESSPROBE renderer_instance=%p process_id=%lu thread_id=%lu qpc_begin=%lld qpc_end=%lld process_us=%lld result_hr=0x%08X sample_time_hr=0x%08X sample_start_100ns=%lld sample_stop_100ns=%lld",
			static_cast<void*>(this),
			static_cast<unsigned long>(GetCurrentProcessId()),
			static_cast<unsigned long>(GetCurrentThreadId()),
			static_cast<long long>(processProbeBegin.QuadPart),
			static_cast<long long>(processProbeEnd.QuadPart),
			static_cast<long long>(processProbeUs),
			static_cast<unsigned int>(hr),
			static_cast<unsigned int>(sampleTimeHr),
			static_cast<long long>(sampleStart),
			static_cast<long long>(sampleStop));
	}""",
    "probe total ProcessSample duration")

# Add total/copy/render timings to the DX11 processor without changing its rendering decisions.
dx11_process_start_marker = "HRESULT CDX11VideoProcessor::ProcessSample(IMediaSample* pSample)\n{"
dx11_process_start = dx11_source.find(dx11_process_start_marker)
dx11_process_end = dx11_source.find("\nHRESULT CDX11VideoProcessor::CopySample(IMediaSample* pSample)", dx11_process_start)
if dx11_process_start < 0 or dx11_process_end < 0:
    raise SystemExit("Could not locate DX11 ProcessSample for timing.")
dx11_process = dx11_source[dx11_process_start:dx11_process_end]
dx11_process = replace_once(
    dx11_process,
    dx11_process_start_marker + "\n\tREFERENCE_TIME rtStart, rtEnd;",
    dx11_process_start_marker + """
	LARGE_INTEGER processProbeBegin{};
	QueryPerformanceCounter(&processProbeBegin);
	LONGLONG copyProbeUs = 0;
	LONGLONG render1ProbeUs = 0;
	LONGLONG render2ProbeUs = 0;
	REFERENCE_TIME rtStart, rtEnd;""",
    "start DX11 stage timing")
dx11_process = replace_once(
    dx11_process,
    '\tm_rtStart = rtStart;\n\tCRefTime rtClock(rtStart);\n\n\tHRESULT hr = CopySample(pSample);',
    r"""	m_rtStart = rtStart;
	CRefTime rtClock(rtStart);
	const REFERENCE_TIME probeFrameStart = rtStart;

	const auto logDx11Stages = [&](HRESULT result) {
		LARGE_INTEGER processProbeEnd{};
		QueryPerformanceCounter(&processProbeEnd);
		const LONGLONG totalUs = MPCVRProbeElapsedUs(processProbeBegin.QuadPart, processProbeEnd.QuadPart);
		if (!MPCVRProbeShouldLogStage(totalUs, result)) {
			return;
		}
		MPCVRProbeLog(L"DX11STAGE processor_instance=%p process_id=%lu thread_id=%lu qpc_begin=%lld qpc_end=%lld process_us=%lld copy_us=%lld render1_us=%lld render2_us=%lld frame_start_100ns=%lld double_frames=%d result_hr=0x%08X",
			static_cast<void*>(this),
			static_cast<unsigned long>(GetCurrentProcessId()),
			static_cast<unsigned long>(GetCurrentThreadId()),
			static_cast<long long>(processProbeBegin.QuadPart),
			static_cast<long long>(processProbeEnd.QuadPart),
			static_cast<long long>(totalUs),
			static_cast<long long>(copyProbeUs),
			static_cast<long long>(render1ProbeUs),
			static_cast<long long>(render2ProbeUs),
			static_cast<long long>(probeFrameStart),
			static_cast<int>(m_bDoubleFrames),
			static_cast<unsigned int>(result));
	};

	LARGE_INTEGER copyProbeBegin{};
	LARGE_INTEGER copyProbeEnd{};
	QueryPerformanceCounter(&copyProbeBegin);
	HRESULT hr = CopySample(pSample);
	QueryPerformanceCounter(&copyProbeEnd);
	copyProbeUs = MPCVRProbeElapsedUs(copyProbeBegin.QuadPart, copyProbeEnd.QuadPart);""",
    "time DX11 CopySample")
dx11_process = replace_once(
    dx11_process,
    '\tif (FAILED(hr)) {\n\t\tm_RenderStats.failed++;\n\t\treturn hr;\n\t}',
    '\tif (FAILED(hr)) {\n\t\tlogDx11Stages(hr);\n\t\tm_RenderStats.failed++;\n\t\treturn hr;\n\t}',
    "log slow failed CopySample")
dx11_process = replace_once(
    dx11_process,
    '\t// always Render(1) a frame after CopySample()\n\thr = Render(1, rtStart);',
    '\t// always Render(1) a frame after CopySample()\n\tLARGE_INTEGER render1ProbeBegin{};\n\tLARGE_INTEGER render1ProbeEnd{};\n\tQueryPerformanceCounter(&render1ProbeBegin);\n\thr = Render(1, rtStart);\n\tQueryPerformanceCounter(&render1ProbeEnd);\n\trender1ProbeUs = MPCVRProbeElapsedUs(render1ProbeBegin.QuadPart, render1ProbeEnd.QuadPart);',
    "time first DX11 render")
dx11_process = replace_once(
    dx11_process,
    '\t\tif (rtEnd < rtClock) {\n\t\t\tm_RenderStats.dropped2++;\n\t\t\treturn S_FALSE; // skip frame\n\t\t}',
    '\t\tif (rtEnd < rtClock) {\n\t\t\tm_RenderStats.dropped2++;\n\t\t\tlogDx11Stages(S_FALSE);\n\t\t\treturn S_FALSE; // skip frame\n\t\t}',
    "log DX11 dropped frame")
dx11_process = replace_once(
    dx11_process,
    '\t\thr = Render(2, rtStart);',
    '\t\tLARGE_INTEGER render2ProbeBegin{};\n\t\tLARGE_INTEGER render2ProbeEnd{};\n\t\tQueryPerformanceCounter(&render2ProbeBegin);\n\t\thr = Render(2, rtStart);\n\t\tQueryPerformanceCounter(&render2ProbeEnd);\n\t\trender2ProbeUs = MPCVRProbeElapsedUs(render2ProbeBegin.QuadPart, render2ProbeEnd.QuadPart);',
    "time second DX11 render")
dx11_process = replace_once(
    dx11_process,
    '\treturn hr;\n}',
    '\tlogDx11Stages(hr);\n\treturn hr;\n}',
    "log slow DX11 ProcessSample")
dx11_source = dx11_source[:dx11_process_start] + dx11_process + dx11_source[dx11_process_end:]

# Time staging-texture Map calls individually; only slow/failing operations are written.
map_start_marker = "HRESULT CDX11VideoProcessor::MemCopyToTexSrcVideo(const BYTE* srcData, const int srcPitch)\n{"
map_start = dx11_source.find(map_start_marker)
map_end = dx11_source.find("\nHRESULT CDX11VideoProcessor::SetDevice", map_start)
if map_start < 0 or map_end < 0:
    raise SystemExit("Could not locate MemCopyToTexSrcVideo for Map timing.")
map_method = dx11_source[map_start:map_end]
map_setup = r"""
	auto probeMap = [&](ID3D11Texture2D* texture, const wchar_t* plane, D3D11_MAPPED_SUBRESOURCE* resource) -> HRESULT {
		LARGE_INTEGER mapProbeBegin{};
		LARGE_INTEGER mapProbeEnd{};
		QueryPerformanceCounter(&mapProbeBegin);
		HRESULT mapHr = m_pDeviceContext->Map(texture, 0, D3D11_MAP_WRITE_DISCARD, 0, resource);
		QueryPerformanceCounter(&mapProbeEnd);
		const LONGLONG mapUs = MPCVRProbeElapsedUs(mapProbeBegin.QuadPart, mapProbeEnd.QuadPart);
		if (MPCVRProbeShouldLogStage(mapUs, mapHr)) {
			MPCVRProbeLog(L"MAPPROBE processor_instance=%p process_id=%lu thread_id=%lu qpc_begin=%lld qpc_end=%lld map_us=%lld plane=%ls result_hr=0x%08X",
				static_cast<void*>(this),
				static_cast<unsigned long>(GetCurrentProcessId()),
				static_cast<unsigned long>(GetCurrentThreadId()),
				static_cast<long long>(mapProbeBegin.QuadPart),
				static_cast<long long>(mapProbeEnd.QuadPart),
				static_cast<long long>(mapUs),
				plane,
				static_cast<unsigned int>(mapHr));
		}
		return mapHr;
	};
"""
map_method = map_method.replace(map_start_marker, map_start_marker + map_setup, 1)
old_ptex = 'm_pDeviceContext->Map(m_TexSrcVideo.pTexture, 0, D3D11_MAP_WRITE_DISCARD, 0, &mappedResource)'
if map_method.count(old_ptex) != 2:
    raise SystemExit("Expected two primary-texture Map sites in MemCopyToTexSrcVideo.")
map_method = map_method.replace(old_ptex, 'probeMap(m_TexSrcVideo.pTexture, L"Y", &mappedResource)')
map_method = replace_once(
    map_method,
    'm_pDeviceContext->Map(m_TexSrcVideo.pTexture2, 0, D3D11_MAP_WRITE_DISCARD, 0, &mappedResource)',
    'probeMap(m_TexSrcVideo.pTexture2, L"U", &mappedResource)',
    "probe chroma U Map")
map_method = replace_once(
    map_method,
    'm_pDeviceContext->Map(m_TexSrcVideo.pTexture3, 0, D3D11_MAP_WRITE_DISCARD, 0, &mappedResource)',
    'probeMap(m_TexSrcVideo.pTexture3, L"V", &mappedResource)',
    "probe chroma V Map")
dx11_source = dx11_source[:map_start] + map_method + dx11_source[map_end:]

# Time Present only in the normal Render function, not the separate FillBlack path.
render_start_marker = "HRESULT CDX11VideoProcessor::Render(int field, const REFERENCE_TIME frameStartTime)\n{"
render_start = dx11_source.find(render_start_marker)
render_end = dx11_source.find("\nHRESULT CDX11VideoProcessor::FillBlack()", render_start)
if render_start < 0 or render_end < 0:
    raise SystemExit("Could not locate DX11 Render for Present timing.")
render_block = dx11_source[render_start:render_end]
render_block = replace_once(
    render_block,
    '\tg_bPresent = true;\n\thr = m_pDXGISwapChain1->Present(1, 0);\n\tg_bPresent = false;',
    r"""	g_bPresent = true;
	LARGE_INTEGER presentProbeBegin{};
	LARGE_INTEGER presentProbeEnd{};
	QueryPerformanceCounter(&presentProbeBegin);
	hr = m_pDXGISwapChain1->Present(1, 0);
	g_bPresent = false;
	QueryPerformanceCounter(&presentProbeEnd);
	const LONGLONG presentProbeUs = MPCVRProbeElapsedUs(presentProbeBegin.QuadPart, presentProbeEnd.QuadPart);
	if (MPCVRProbeShouldLogStage(presentProbeUs, hr)) {
		MPCVRProbeLog(L"PRESENTPROBE processor_instance=%p process_id=%lu thread_id=%lu qpc_begin=%lld qpc_end=%lld present_us=%lld frame_start_100ns=%lld field=%d result_hr=0x%08X",
			static_cast<void*>(this),
			static_cast<unsigned long>(GetCurrentProcessId()),
			static_cast<unsigned long>(GetCurrentThreadId()),
			static_cast<long long>(presentProbeBegin.QuadPart),
			static_cast<long long>(presentProbeEnd.QuadPart),
			static_cast<long long>(presentProbeUs),
			static_cast<long long>(frameStartTime),
			field,
			static_cast<unsigned int>(hr));
	}""",
    "probe DXGI Present")
dx11_source = dx11_source[:render_start] + render_block + dx11_source[render_end:]


# Capture the scheduler's decision before Receive converts a rejection to NOERROR.
# The context is appended to the existing bounded RECEIVEPROBE record (no scheduler file I/O).
renbase2_source = replace_once(
    renbase2_source,
    """#include "renbase2.h" """.rstrip(),
    """#include "renbase2.h"

void MPCVRProbeSetSchedulerContext(const wchar_t* context);""",
    "declare context API in renderer scheduler source",
)
renbase2_source = replace_once(
    renbase2_source,
    """    // Don't call us unless there's a clock interface to synchronise with
    ASSERT(m_pClock);""",
    """    // Don't call us unless there's a clock interface to synchronise with
    ASSERT(m_pClock);
    MPCVRProbeSetSchedulerContext(L"");""",
    "clear scheduler context at decision start",
)
renbase2_source = replace_once(
    renbase2_source,
    """    m_nNormal = -1;
    return E_FAIL;                         // drop it""",
    """    wchar_t schedulerContext[768]{};
    _snwprintf_s(schedulerContext, _countof(schedulerContext), _TRUNCATE,
        L"decision=late_drop;stream_now_100ns=%lld;sample_start_after_bias_100ns=%lld;sample_stop_after_bias_100ns=%lld;lateness_100ns=%d;duration_100ns=%d;frame_avg_100ns=%d;render_avg_100ns=%d;wait_avg_state_100ns=%d;supplier_quality=%d;earliness_100ns=%d;since_last_draw_100ns=%lld;normal_count=%d",
        static_cast<long long>(trRealStream),
        static_cast<long long>(*ptrStart),
        static_cast<long long>(*ptrEnd),
        trTrueLate,
        trDuration,
        m_trFrameAvg,
        m_trRenderAvg,
        m_trWaitAvg,
        static_cast<int>(m_bSupplierHandlingQuality),
        m_trEarliness,
        static_cast<long long>(trRealStream - m_trLastDraw),
        m_nNormal);
    MPCVRProbeSetSchedulerContext(schedulerContext);

    m_nNormal = -1;
    return E_FAIL;                         // drop it""",
    "record late-drop decision",
)
base_renderer_source = replace_once(
    base_renderer_source,
    """#include "measure.h"        // Used for time critical log functions""",
    """#include "measure.h"        // Used for time critical log functions

void MPCVRProbeAppendSchedulerContext(const wchar_t* context);""",
    "declare BaseClasses context API",
)
schedule_start = base_renderer_source.find("BOOL CBaseRenderer::ScheduleSample(IMediaSample *pMediaSample)")
schedule_end = base_renderer_source.find("\n\n// This is called when a sample comes due", schedule_start)
if schedule_start < 0 or schedule_end < 0:
    raise SystemExit("Could not locate BaseClasses ScheduleSample for scheduler diagnostics.")
schedule_block = base_renderer_source[schedule_start:schedule_end]
schedule_block = replace_once(
    schedule_block,
    """    REFERENCE_TIME StartSample, EndSample;""",
    """    REFERENCE_TIME StartSample = 0, EndSample = 0;""",
    "initialize scheduler timestamps",
)
schedule_block = replace_once(
    schedule_block,
    """    HRESULT hr = GetSampleTimes(pMediaSample, &StartSample, &EndSample);
    if (FAILED(hr)) {
        return FALSE;
    }""",
    """    HRESULT hr = GetSampleTimes(pMediaSample, &StartSample, &EndSample);
    if (FAILED(hr)) {
        if (hr == E_FAIL) {
            // E_FAIL is the late-drop signal used by ShouldDrawSampleNow; details are already in TLS.
            MPCVRProbeAppendSchedulerContext(L"schedule_stage=get_sample_times;hr=0x80004005");
        } else {
            wchar_t schedulerContext[256]{};
            _snwprintf_s(schedulerContext, _countof(schedulerContext), _TRUNCATE,
                L"schedule_stage=get_sample_times;hr=0x%08X;start_100ns=%lld;stop_100ns=%lld",
                static_cast<unsigned int>(hr),
                static_cast<long long>(StartSample),
                static_cast<long long>(EndSample));
            MPCVRProbeAppendSchedulerContext(schedulerContext);
        }
        return FALSE;
    }""",
    "record GetSampleTimes failure",
)
schedule_block = replace_once(
    schedule_block,
    """    ASSERT(m_dwAdvise == 0);
    return FALSE;
}""",
    """    ASSERT(m_dwAdvise == 0);
    wchar_t schedulerContext[256]{};
    _snwprintf_s(schedulerContext, _countof(schedulerContext), _TRUNCATE,
        L"schedule_stage=advise_time;hr=0x%08X;start_100ns=%lld;stop_100ns=%lld",
        static_cast<unsigned int>(hr),
        static_cast<long long>(StartSample),
        static_cast<long long>(EndSample));
    MPCVRProbeAppendSchedulerContext(schedulerContext);
    return FALSE;
}""",
    "record AdviseTime failure",
)
base_renderer_source = base_renderer_source[:schedule_start] + schedule_block + base_renderer_source[schedule_end:]

if "WaitForSingleObject(m_RenderEvent" in source or "WaitForSingleObject(m_RenderEvent" in dx11_source:
    raise SystemExit("Unsafe diagnostic: polling auto-reset m_RenderEvent.")
if not all(marker in source for marker in ("WAITPROBE_ENTER", "WAITPROBE_EXIT", "RECEIVEPROBE", "PROCESSPROBE")):
    raise SystemExit("Renderer timing markers missing.")
if not all(marker in dx11_source for marker in ("DX11STAGE", "MAPPROBE", "PRESENTPROBE")):
    raise SystemExit("DX11 timing markers missing.")
if "decision=late_drop" not in renbase2_source or "MPCVRProbeSetSchedulerContext" not in renbase2_source:
    raise SystemExit("Late-drop decision context markers missing.")
if "schedule_stage=get_sample_times" not in base_renderer_source or "schedule_stage=advise_time" not in base_renderer_source:
    raise SystemExit("BaseClasses scheduling failure context markers missing.")

if "WAITPROBE_STATE" in source or "const REFERENCE_TIME wpRunStartBefore" in source:
    raise SystemExit("Old invasive state probes remain in source; refusing to continue.")

CPP.write_text(source, encoding="utf-8", newline=chr(10))
HEADER.write_text(header, encoding="utf-8", newline=chr(10))
DX11_CPP.write_text(dx11_source, encoding="utf-8", newline=chr(10))
RENBASE2_CPP.write_text(renbase2_source, encoding="utf-8", newline=chr(10))
BASE_RENDERER_CPP.write_text(base_renderer_source, encoding="utf-8", newline=chr(10))
print(f"Patched {CPP}, {HEADER}, {DX11_CPP}, {RENBASE2_CPP}, and {BASE_RENDERER_CPP}: added bounded timings plus scheduler decision context.")
