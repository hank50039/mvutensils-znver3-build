from pathlib import Path

ROOT = Path("MPCVideoRenderer")
CPP = ROOT / "Source/VideoRenderer.cpp"
raw_source = CPP.read_bytes()
has_utf8_bom = raw_source.startswith(b"\xef\xbb\xbf")
source_newline = "\r\n" if b"\r\n" in raw_source else "\n"
source = raw_source.decode("utf-8-sig").replace("\r\n", "\n")

helper = r'''
#include <chrono>
#include <cstdio>
#include <mutex>

namespace {
std::mutex g_mpcvrReceiveDiagMutex;
const auto g_mpcvrReceiveDiagStart = std::chrono::steady_clock::now();

void LogMpcvrReceiveDiagnostic(
    long long totalUs,
    long long prepareReceiveUs,
    long long waitForRenderUs,
    long long pausedProcessUs,
    long long renderUs,
    REFERENCE_TIME sampleStart,
    REFERENCE_TIME sampleStop,
    HRESULT waitHr)
{
    // Only write outlier records, to keep normal playback overhead low.
    if (totalUs < 20000) {
        return;
    }

    WCHAR localAppData[MAX_PATH]{};
    const DWORD pathLength = GetEnvironmentVariableW(L"LOCALAPPDATA", localAppData, ARRAYSIZE(localAppData));
    if (pathLength == 0 || pathLength >= ARRAYSIZE(localAppData)) {
        return;
    }

    WCHAR logPath[MAX_PATH]{};
    if (swprintf_s(logPath, ARRAYSIZE(logPath), L"%ls\\MPCVideoRendererDiag.log", localAppData) < 0) {
        return;
    }

    const long long sinceStartUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - g_mpcvrReceiveDiagStart).count();
    const long long otherUs = totalUs - prepareReceiveUs - waitForRenderUs - pausedProcessUs - renderUs;

    const std::lock_guard<std::mutex> lock(g_mpcvrReceiveDiagMutex);
    FILE* file = nullptr;
    if (_wfopen_s(&file, logPath, L"a") != 0 || file == nullptr) {
        return;
    }

    fwprintf_s(
        file,
        L"MRDIAG since_start_us %lld total_receive_us %lld prepare_receive_us %lld wait_for_render_us %lld paused_process_us %lld render_us %lld other_us %lld sample_start_100ns %lld sample_stop_100ns %lld wait_hr 0x%08X\r\n",
        sinceStartUs, totalUs, prepareReceiveUs, waitForRenderUs, pausedProcessUs, renderUs, otherUs,
        static_cast<long long>(sampleStart), static_cast<long long>(sampleStop),
        static_cast<unsigned int>(waitHr));
    fclose(file);
}
} // namespace

'''

include_anchor = '#include "SubPic/XySubPicQueueImpl.h"\n'
if source.count(include_anchor) != 1:
    raise SystemExit("Could not locate the source include anchor.")
source = source.replace(include_anchor, include_anchor + helper, 1)

receive_start = "HRESULT CMpcVideoRenderer::Receive(IMediaSample* pSample)\n{\n"
variables = '''HRESULT CMpcVideoRenderer::Receive(IMediaSample* pSample)
{
'''
# Add the measurement variables immediately after the assertion. This avoids timing flush-only returns.
assert_anchor = "\tASSERT(pSample);\n\n\t// It may return VFW_E_SAMPLE_REJECTED code to say don't bother\n"
assert_replace = '''\tASSERT(pSample);

    const auto diagReceiveStart = std::chrono::steady_clock::now();
    REFERENCE_TIME diagSampleStart = -1;
    REFERENCE_TIME diagSampleStop = -1;
    static_cast<void>(pSample->GetTime(&diagSampleStart, &diagSampleStop));
    long long diagPrepareReceiveUs = 0;
    long long diagWaitForRenderUs = 0;
    long long diagPausedProcessUs = 0;
    long long diagRenderUs = 0;
    HRESULT diagWaitHr = E_PENDING;

\t// It may return VFW_E_SAMPLE_REJECTED code to say don't bother
'''
if source.count(assert_anchor) != 1:
    raise SystemExit("Could not locate the Receive() assertion anchor.")
source = source.replace(assert_anchor, assert_replace, 1)

prepare_old = "\tHRESULT hr = PrepareReceive(pSample);\n"
prepare_new = '''    const auto diagPrepareStart = std::chrono::steady_clock::now();
\tHRESULT hr = PrepareReceive(pSample);
    diagPrepareReceiveUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - diagPrepareStart).count();
'''
if source.count(prepare_old) != 1:
    raise SystemExit("Could not uniquely locate PrepareReceive call.")
source = source.replace(prepare_old, prepare_new, 1)

failed_old = '''\tif (FAILED(hr)) {
\t\tif (hr == VFW_E_SAMPLE_REJECTED) {
\t\t\treturn NOERROR;
\t\t}
\t\treturn hr;
\t}
'''
failed_new = '''\tif (FAILED(hr)) {
        const long long diagTotalUs = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - diagReceiveStart).count();
        LogMpcvrReceiveDiagnostic(diagTotalUs, diagPrepareReceiveUs, 0, 0, 0,
            diagSampleStart, diagSampleStop, diagWaitHr);
\t\tif (hr == VFW_E_SAMPLE_REJECTED) {
\t\t\treturn NOERROR;
\t\t}
\t\treturn hr;
\t}
'''
if source.count(failed_old) != 1:
    raise SystemExit("Could not uniquely locate PrepareReceive failure block.")
source = source.replace(failed_old, failed_new, 1)

wait_old = "\thr = WaitForRenderTime();\n"
wait_new = '''    const auto diagWaitStart = std::chrono::steady_clock::now();
\thr = WaitForRenderTime();
    diagWaitHr = hr;
    diagWaitForRenderUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - diagWaitStart).count();
'''
if source.count(wait_old) != 1:
    raise SystemExit("Could not uniquely locate WaitForRenderTime call.")
source = source.replace(wait_old, wait_new, 1)

wait_failed_old = '''\tif (FAILED(hr)) {
\t\tm_bInReceive = FALSE;
\t\treturn NOERROR;
\t}

\t//  Set this here and poll it until we work out the locking correctly
'''
wait_failed_new = '''\tif (FAILED(hr)) {
        const long long diagTotalUs = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - diagReceiveStart).count();
        LogMpcvrReceiveDiagnostic(diagTotalUs, diagPrepareReceiveUs, diagWaitForRenderUs, 0, 0,
            diagSampleStart, diagSampleStop, diagWaitHr);
\t\tm_bInReceive = FALSE;
\t\treturn NOERROR;
\t}

\t//  Set this here and poll it until we work out the locking correctly
'''
if source.count(wait_failed_old) != 1:
    raise SystemExit("Could not uniquely locate WaitForRenderTime failure block.")
source = source.replace(wait_failed_old, wait_failed_new, 1)

paused_old = '''\t\tCAutoLock cRendererLock(&m_RendererLock);
\t\tDoRenderSample(m_pMediaSample);
'''
paused_new = '''\t\tCAutoLock cRendererLock(&m_RendererLock);
        const auto diagPausedStart = std::chrono::steady_clock::now();
\t\tDoRenderSample(m_pMediaSample);
        diagPausedProcessUs += std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - diagPausedStart).count();
'''
if source.count(paused_old) != 1:
    raise SystemExit("Could not uniquely locate paused DoRenderSample call.")
source = source.replace(paused_old, paused_new, 1)

render_old = '''\tif (m_State == State_Running) {
\t\tRender(m_pMediaSample);
\t}

\tClearPendingSample();
\tSendEndOfStream();
\tCancelNotification();
\treturn NOERROR;
}
'''
render_new = '''\tif (m_State == State_Running) {
        const auto diagRenderStart = std::chrono::steady_clock::now();
\t\tRender(m_pMediaSample);
        diagRenderUs = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - diagRenderStart).count();
\t}

\tClearPendingSample();
\tSendEndOfStream();
\tCancelNotification();

    const long long diagTotalUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - diagReceiveStart).count();
    LogMpcvrReceiveDiagnostic(diagTotalUs, diagPrepareReceiveUs, diagWaitForRenderUs,
        diagPausedProcessUs, diagRenderUs, diagSampleStart, diagSampleStop, diagWaitHr);

\treturn NOERROR;
}
'''
if source.count(render_old) != 1:
    raise SystemExit("Could not uniquely locate final Render()/return block.")
source = source.replace(render_old, render_new, 1)

encoded_source = source.replace("\n", source_newline).encode("utf-8")
if has_utf8_bom:
    encoded_source = b"\xef\xbb\xbf" + encoded_source
CPP.write_bytes(encoded_source)
print("Instrumented CMpcVideoRenderer::Receive; only Receive calls >=20 ms are written to %LOCALAPPDATA%\\MPCVideoRendererDiag.log.")
