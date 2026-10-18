from pathlib import Path

ROOT = Path("MPCVideoRenderer")
CPP = ROOT / "Source/DX11VideoProcessor.cpp"
raw_source = CPP.read_bytes()
has_utf8_bom = raw_source.startswith(b"\xef\xbb\xbf")
source_newline = "\r\n" if b"\r\n" in raw_source else "\n"
source = raw_source.decode("utf-8-sig").replace("\r\n", "\n")


def replace_once(old: str, new: str, label: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"Expected exactly one anchor for {label}; found {count}.")
    source = source.replace(old, new, 1)


helper = r'''
#include <chrono>
#include <cstdio>
#include <mutex>

namespace {
std::mutex g_mpcvrDx11DiagMutex;
const auto g_mpcvrDx11DiagStart = std::chrono::steady_clock::now();

void LogMpcvrDx11Diagnostic(const wchar_t* record)
{
    WCHAR localAppData[MAX_PATH]{};
    const DWORD pathLength = GetEnvironmentVariableW(L"LOCALAPPDATA", localAppData, ARRAYSIZE(localAppData));
    if (pathLength == 0 || pathLength >= ARRAYSIZE(localAppData)) {
        return;
    }

    WCHAR logPath[MAX_PATH]{};
    if (swprintf_s(logPath, ARRAYSIZE(logPath), L"%ls\\MPCVideoRendererDX11Diag.log", localAppData) < 0) {
        return;
    }

    const long long sinceStartUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - g_mpcvrDx11DiagStart).count();

    const std::lock_guard<std::mutex> lock(g_mpcvrDx11DiagMutex);
    FILE* file = nullptr;
    if (_wfopen_s(&file, logPath, L"a") != 0 || file == nullptr) {
        return;
    }

    fwprintf_s(file, L"DX11DIAG since_start_us %lld %ls\r\n", sinceStartUs, record);
    fclose(file);
}
} // namespace

'''

replace_once(
    '#include "Utils/CPUInfo.h"\n',
    '#include "Utils/CPUInfo.h"\n' + helper,
    "DX11 diagnostic helper insertion"
)

process_start_old = '''HRESULT CDX11VideoProcessor::ProcessSample(IMediaSample* pSample)
{
\tREFERENCE_TIME rtStart, rtEnd;
'''
process_start_new = '''HRESULT CDX11VideoProcessor::ProcessSample(IMediaSample* pSample)
{
    const auto diagProcessSampleStart = std::chrono::steady_clock::now();
    long long diagCopySampleUs = 0;
    long long diagDrawRender1Us = 0;
    long long diagDrawRender2Us = 0;
\tREFERENCE_TIME rtStart, rtEnd;
'''
replace_once(process_start_old, process_start_new, "ProcessSample entry")

copy_old = '''\tHRESULT hr = CopySample(pSample);
\tif (FAILED(hr)) {
\t\tm_RenderStats.failed++;
\t\treturn hr;
\t}
'''
copy_new = '''    const auto diagCopySampleStart = std::chrono::steady_clock::now();
\tHRESULT hr = CopySample(pSample);
    diagCopySampleUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - diagCopySampleStart).count();
\tif (FAILED(hr)) {
\t\tm_RenderStats.failed++;
        const long long diagTotalUs = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - diagProcessSampleStart).count();
        if (diagTotalUs >= 20000 || diagCopySampleUs >= 20000) {
            wchar_t record[768]{};
            swprintf_s(record, ARRAYSIZE(record),
                L"stage ProcessSample sample_start_100ns %lld total_us %lld copy_sample_us %lld draw_render1_us 0 draw_render2_us 0 other_us %lld d3d11_vp_ready %d src_from_gpu %d src_width %u src_height %u hr 0x%08X",
                static_cast<long long>(rtStart), diagTotalUs, diagCopySampleUs,
                (std::max)(0LL, diagTotalUs - diagCopySampleUs),
                m_D3D11VP.IsReady() ? 1 : 0, m_iSrcFromGPU, m_srcWidth, m_srcHeight,
                static_cast<unsigned int>(hr));
            LogMpcvrDx11Diagnostic(record);
        }
\t\treturn hr;
\t}
'''
replace_once(copy_old, copy_new, "CopySample timing")

render1_old = '''\thr = Render(1, rtStart);
\tm_pFilter->m_DrawStats.Add(GetPreciseTick());
'''
render1_new = '''    const auto diagDrawRender1Start = std::chrono::steady_clock::now();
\thr = Render(1, rtStart);
    diagDrawRender1Us = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - diagDrawRender1Start).count();
\tm_pFilter->m_DrawStats.Add(GetPreciseTick());
'''
replace_once(render1_old, render1_new, "first internal render timing")

drop_old = '''\t\tif (rtEnd < rtClock) {
\t\t\tm_RenderStats.dropped2++;
\t\t\treturn S_FALSE; // skip frame
\t\t}
'''
drop_new = '''\t\tif (rtEnd < rtClock) {
\t\t\tm_RenderStats.dropped2++;
            const long long diagTotalUs = std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now() - diagProcessSampleStart).count();
            if (diagTotalUs >= 20000 || diagCopySampleUs >= 20000 || diagDrawRender1Us >= 20000) {
                wchar_t record[768]{};
                swprintf_s(record, ARRAYSIZE(record),
                    L"stage ProcessSample sample_start_100ns %lld total_us %lld copy_sample_us %lld draw_render1_us %lld draw_render2_us 0 other_us %lld d3d11_vp_ready %d src_from_gpu %d src_width %u src_height %u hr 0x%08X",
                    static_cast<long long>(rtStart), diagTotalUs, diagCopySampleUs, diagDrawRender1Us,
                    (std::max)(0LL, diagTotalUs - diagCopySampleUs - diagDrawRender1Us),
                    m_D3D11VP.IsReady() ? 1 : 0, m_iSrcFromGPU, m_srcWidth, m_srcHeight,
                    static_cast<unsigned int>(S_FALSE));
                LogMpcvrDx11Diagnostic(record);
            }
\t\t\treturn S_FALSE; // skip frame
\t\t}
'''
replace_once(drop_old, drop_new, "dropped second-field diagnostic")

render2_old = '''\t\thr = Render(2, rtStart);
\t\tm_pFilter->m_DrawStats.Add(GetPreciseTick());
'''
render2_new = '''        const auto diagDrawRender2Start = std::chrono::steady_clock::now();
\t\thr = Render(2, rtStart);
        diagDrawRender2Us = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - diagDrawRender2Start).count();
\t\tm_pFilter->m_DrawStats.Add(GetPreciseTick());
'''
replace_once(render2_old, render2_new, "second internal render timing")

process_end_old = '''\t\tm_Syncs.Add(so);
\t}

\treturn hr;
}

HRESULT CDX11VideoProcessor::CopySample(IMediaSample* pSample)
'''
process_end_new = '''\t\tm_Syncs.Add(so);
\t}

    const long long diagTotalUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - diagProcessSampleStart).count();
    if (diagTotalUs >= 20000 || diagCopySampleUs >= 20000 || diagDrawRender1Us >= 20000 || diagDrawRender2Us >= 20000) {
        wchar_t record[768]{};
        swprintf_s(record, ARRAYSIZE(record),
            L"stage ProcessSample sample_start_100ns %lld total_us %lld copy_sample_us %lld draw_render1_us %lld draw_render2_us %lld other_us %lld d3d11_vp_ready %d src_from_gpu %d src_width %u src_height %u hr 0x%08X",
            static_cast<long long>(rtStart), diagTotalUs, diagCopySampleUs, diagDrawRender1Us, diagDrawRender2Us,
            (std::max)(0LL, diagTotalUs - diagCopySampleUs - diagDrawRender1Us - diagDrawRender2Us),
            m_D3D11VP.IsReady() ? 1 : 0, m_iSrcFromGPU, m_srcWidth, m_srcHeight,
            static_cast<unsigned int>(hr));
        LogMpcvrDx11Diagnostic(record);
    }

\treturn hr;
}

HRESULT CDX11VideoProcessor::CopySample(IMediaSample* pSample)
'''
replace_once(process_end_old, process_end_new, "ProcessSample summary")

render_entry_old = '''HRESULT CDX11VideoProcessor::Render(int field, const REFERENCE_TIME frameStartTime)
{
\tCheckPointer(m_TexSrcVideo.pTexture, E_FAIL);
\tCheckPointer(m_pDXGISwapChain1, E_FAIL);

\tif (field) {
'''
render_entry_new = '''HRESULT CDX11VideoProcessor::Render(int field, const REFERENCE_TIME frameStartTime)
{
\tCheckPointer(m_TexSrcVideo.pTexture, E_FAIL);
\tCheckPointer(m_pDXGISwapChain1, E_FAIL);

    const auto diagDrawStart = std::chrono::steady_clock::now();
    long long diagProcessPipelineUs = 0;
    long long diagWaitVBlankUs = 0;
    long long diagSyncFrameUs = 0;
    long long diagPresentUs = 0;

\tif (field) {
'''
replace_once(render_entry_old, render_entry_new, "Render entry")

pipeline_old = '''\tif (!m_renderRect.IsRectEmpty()) {
\t\thr = Process(pBackBuffer, m_srcRect, m_videoRect, m_FieldDrawn == 2);
\t}
'''
pipeline_new = '''\tif (!m_renderRect.IsRectEmpty()) {
        const auto diagProcessPipelineStart = std::chrono::steady_clock::now();
\t\thr = Process(pBackBuffer, m_srcRect, m_videoRect, m_FieldDrawn == 2);
        diagProcessPipelineUs = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - diagProcessPipelineStart).count();
\t}
'''
replace_once(pipeline_old, pipeline_new, "render pipeline timing")

vblank_old = '''\tif (m_bVBlankBeforePresent && m_pDXGIOutput) {
\t\thr = m_pDXGIOutput->WaitForVBlank();
\t\tDLogIf(FAILED(hr), L"WaitForVBlank failed with error {}", HR2Str(hr));
\t}
'''
vblank_new = '''\tif (m_bVBlankBeforePresent && m_pDXGIOutput) {
        const auto diagWaitVBlankStart = std::chrono::steady_clock::now();
\t\thr = m_pDXGIOutput->WaitForVBlank();
        diagWaitVBlankUs = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - diagWaitVBlankStart).count();
\t\tDLogIf(FAILED(hr), L"WaitForVBlank failed with error {}", HR2Str(hr));
\t}
'''
replace_once(vblank_old, vblank_new, "WaitForVBlank timing")

sync_old = '''\tif (m_bAdjustPresentTime) {
\t\tSyncFrameToStreamTime(frameStartTime);
\t}

\tg_bPresent = true;
\thr = m_pDXGISwapChain1->Present(1, 0);
\tg_bPresent = false;
'''
sync_new = '''\tif (m_bAdjustPresentTime) {
        const auto diagSyncFrameStart = std::chrono::steady_clock::now();
\t\tSyncFrameToStreamTime(frameStartTime);
        diagSyncFrameUs = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - diagSyncFrameStart).count();
\t}

    const auto diagPresentStart = std::chrono::steady_clock::now();
\tg_bPresent = true;
\thr = m_pDXGISwapChain1->Present(1, 0);
\tg_bPresent = false;
    diagPresentUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - diagPresentStart).count();
'''
replace_once(sync_old, sync_new, "sync and Present timing")

render_end_old = '''\tif (hr == DXGI_ERROR_INVALID_CALL && m_pFilter->m_bIsD3DFullscreen) {
\t\tInitSwapChain(false);
\t}

\treturn hr;
}

HRESULT CDX11VideoProcessor::FillBlack()
'''
render_end_new = '''\tif (hr == DXGI_ERROR_INVALID_CALL && m_pFilter->m_bIsD3DFullscreen) {
\t\tInitSwapChain(false);
\t}

    const long long diagTotalUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - diagDrawStart).count();
    if (diagTotalUs >= 20000 || diagProcessPipelineUs >= 20000 || diagWaitVBlankUs >= 20000
            || diagSyncFrameUs >= 20000 || diagPresentUs >= 20000) {
        const long long diagOtherUs = (std::max)(0LL,
            diagTotalUs - diagProcessPipelineUs - diagWaitVBlankUs - diagSyncFrameUs - diagPresentUs);
        wchar_t record[768]{};
        swprintf_s(record, ARRAYSIZE(record),
            L"stage Render frame_start_100ns %lld field %d total_us %lld process_pipeline_us %lld wait_vblank_us %lld sync_frame_us %lld present_us %lld other_us %lld hr 0x%08X",
            static_cast<long long>(frameStartTime), field, diagTotalUs, diagProcessPipelineUs,
            diagWaitVBlankUs, diagSyncFrameUs, diagPresentUs, diagOtherUs,
            static_cast<unsigned int>(hr));
        LogMpcvrDx11Diagnostic(record);
    }

\treturn hr;
}

HRESULT CDX11VideoProcessor::FillBlack()
'''
replace_once(render_end_old, render_end_new, "Render summary")

encoded_source = source.replace("\n", source_newline).encode("utf-8")
if has_utf8_bom:
    encoded_source = b"\xef\xbb\xbf" + encoded_source
CPP.write_bytes(encoded_source)
print("Instrumented CDX11VideoProcessor::ProcessSample and Render; only slow stages are written to %LOCALAPPDATA%\\MPCVideoRendererDX11Diag.log.")
