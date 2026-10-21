from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_vsfilter_diag.py <avisynth_filter-source-root>")

root = Path(sys.argv[1]).resolve()
filter_cpp = root / "filter_common/src/filter.cpp"
frame_cpp = root / "vapoursynth_filter/src/frame_handler.cpp"

def replace_once(path: Path, old: str, new: str, label: str) -> None:
    source = path.read_text(encoding="utf-8-sig")
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match in {path}, got {count}; refusing to patch")
    path.write_text(source.replace(old, new, 1), encoding="utf-8", newline="")

# ---------- Shared filter: media-type byte/field diff + sparse Receive timings ----------
s = filter_cpp.read_text(encoding="utf-8-sig")
include_anchor = '#include "filter.h"\n\n#include "constants.h"'
if s.count(include_anchor) != 1:
    raise SystemExit("Could not uniquely locate filter.cpp include anchor; refusing to patch")
s = s.replace(
    include_anchor,
    '#include "filter.h"\n\n#include <chrono>\n#include <cstring>\n#include <dvdmedia.h>\n\n#include "constants.h"',
    1
)

namespace_anchor = "namespace SynthFilter {\n\n"
if s.count(namespace_anchor) != 1:
    raise SystemExit("Could not uniquely locate SynthFilter namespace in filter.cpp")

helper = r'''
namespace {

auto LogVideoInfo2FormatDiff(const CMediaType &oldMediaType, const AM_MEDIA_TYPE &newMediaType) -> void {
    if (oldMediaType.pbFormat == nullptr || newMediaType.pbFormat == nullptr) {
        Environment::GetInstance().Log(L"FORMAT_DIFF null format block: old_cbFormat %lu new_cbFormat %lu",
                                       oldMediaType.cbFormat, newMediaType.cbFormat);
        return;
    }

    Environment::GetInstance().Log(L"FORMAT_DIFF begin: old_cbFormat %lu new_cbFormat %lu old_fixed %d new_fixed %d old_temporal %d new_temporal %d old_sampleSize %lu new_sampleSize %lu",
                                   oldMediaType.cbFormat, newMediaType.cbFormat,
                                   oldMediaType.bFixedSizeSamples, newMediaType.bFixedSizeSamples,
                                   oldMediaType.bTemporalCompression, newMediaType.bTemporalCompression,
                                   oldMediaType.lSampleSize, newMediaType.lSampleSize);

    if (IsEqualGUID(oldMediaType.formattype, FORMAT_VideoInfo2) &&
        IsEqualGUID(newMediaType.formattype, FORMAT_VideoInfo2) &&
        oldMediaType.cbFormat >= sizeof(VIDEOINFOHEADER2) &&
        newMediaType.cbFormat >= sizeof(VIDEOINFOHEADER2)) {
        const auto &oldHeader = *reinterpret_cast<const VIDEOINFOHEADER2 *>(oldMediaType.pbFormat);
        const auto &newHeader = *reinterpret_cast<const VIDEOINFOHEADER2 *>(newMediaType.pbFormat);

        Environment::GetInstance().Log(
            L"FORMAT_DIFF_VIH2 rects old source=(%ld,%ld,%ld,%ld) target=(%ld,%ld,%ld,%ld) new source=(%ld,%ld,%ld,%ld) target=(%ld,%ld,%ld,%ld)",
            oldHeader.rcSource.left, oldHeader.rcSource.top, oldHeader.rcSource.right, oldHeader.rcSource.bottom,
            oldHeader.rcTarget.left, oldHeader.rcTarget.top, oldHeader.rcTarget.right, oldHeader.rcTarget.bottom,
            newHeader.rcSource.left, newHeader.rcSource.top, newHeader.rcSource.right, newHeader.rcSource.bottom,
            newHeader.rcTarget.left, newHeader.rcTarget.top, newHeader.rcTarget.right, newHeader.rcTarget.bottom);

        Environment::GetInstance().Log(
            L"FORMAT_DIFF_VIH2 timing old AvgTimePerFrame=%lld BitRate=%lu BitErrorRate=%lu new AvgTimePerFrame=%lld BitRate=%lu BitErrorRate=%lu",
            static_cast<long long>(oldHeader.AvgTimePerFrame), oldHeader.dwBitRate, oldHeader.dwBitErrorRate,
            static_cast<long long>(newHeader.AvgTimePerFrame), newHeader.dwBitRate, newHeader.dwBitErrorRate);

        Environment::GetInstance().Log(
            L"FORMAT_DIFF_VIH2 flags old Interlace=%08lX CopyProtect=%08lX Aspect=%lu:%lu Control=%08lX Reserved2=%08lX new Interlace=%08lX CopyProtect=%08lX Aspect=%lu:%lu Control=%08lX Reserved2=%08lX",
            oldHeader.dwInterlaceFlags, oldHeader.dwCopyProtectFlags,
            oldHeader.dwPictAspectRatioX, oldHeader.dwPictAspectRatioY, oldHeader.dwControlFlags, oldHeader.dwReserved2,
            newHeader.dwInterlaceFlags, newHeader.dwCopyProtectFlags,
            newHeader.dwPictAspectRatioX, newHeader.dwPictAspectRatioY, newHeader.dwControlFlags, newHeader.dwReserved2);

        const auto &oldBitmap = oldHeader.bmiHeader;
        const auto &newBitmap = newHeader.bmiHeader;
        Environment::GetInstance().Log(
            L"FORMAT_DIFF_BMI old size=%lu width=%ld height=%ld planes=%u bitcount=%u compression=%08lX sizeimage=%lu xppm=%ld yppm=%ld clrused=%lu clrimportant=%lu new size=%lu width=%ld height=%ld planes=%u bitcount=%u compression=%08lX sizeimage=%lu xppm=%ld yppm=%ld clrused=%lu clrimportant=%lu",
            oldBitmap.biSize, oldBitmap.biWidth, oldBitmap.biHeight,
            static_cast<unsigned int>(oldBitmap.biPlanes), static_cast<unsigned int>(oldBitmap.biBitCount),
            oldBitmap.biCompression, oldBitmap.biSizeImage, oldBitmap.biXPelsPerMeter, oldBitmap.biYPelsPerMeter,
            oldBitmap.biClrUsed, oldBitmap.biClrImportant,
            newBitmap.biSize, newBitmap.biWidth, newBitmap.biHeight,
            static_cast<unsigned int>(newBitmap.biPlanes), static_cast<unsigned int>(newBitmap.biBitCount),
            newBitmap.biCompression, newBitmap.biSizeImage, newBitmap.biXPelsPerMeter, newBitmap.biYPelsPerMeter,
            newBitmap.biClrUsed, newBitmap.biClrImportant);
    } else {
        Environment::GetInstance().Log(
            L"FORMAT_DIFF not VIDEOINFOHEADER2 or undersized block: old_is_VIH2 %d new_is_VIH2 %d old_cbFormat %lu new_cbFormat %lu",
            IsEqualGUID(oldMediaType.formattype, FORMAT_VideoInfo2),
            IsEqualGUID(newMediaType.formattype, FORMAT_VideoInfo2),
            oldMediaType.cbFormat, newMediaType.cbFormat);
    }

    const ULONG commonSize = oldMediaType.cbFormat < newMediaType.cbFormat
        ? oldMediaType.cbFormat : newMediaType.cbFormat;
    ULONG differentBytes = 0;
    for (ULONG offset = 0; offset < commonSize; ++offset) {
        const BYTE oldValue = oldMediaType.pbFormat[offset];
        const BYTE newValue = newMediaType.pbFormat[offset];
        if (oldValue != newValue) {
            Environment::GetInstance().Log(L"FORMAT_DIFF_BYTE offset=%lu old=%02X new=%02X",
                                           offset,
                                           static_cast<unsigned int>(oldValue),
                                           static_cast<unsigned int>(newValue));
            ++differentBytes;
        }
    }

    Environment::GetInstance().Log(L"FORMAT_DIFF end: differing_bytes_in_common_block %lu size_changed %d",
                                   differentBytes, oldMediaType.cbFormat != newMediaType.cbFormat);
}

auto IsOnlyVideoInfo2ControlFlagsChanged(const CMediaType &oldMediaType, const AM_MEDIA_TYPE &newMediaType) -> bool {
    if (!IsEqualGUID(oldMediaType.majortype, newMediaType.majortype) ||
        !IsEqualGUID(oldMediaType.subtype, newMediaType.subtype) ||
        oldMediaType.bFixedSizeSamples != newMediaType.bFixedSizeSamples ||
        oldMediaType.bTemporalCompression != newMediaType.bTemporalCompression ||
        oldMediaType.lSampleSize != newMediaType.lSampleSize ||
        !IsEqualGUID(oldMediaType.formattype, FORMAT_VideoInfo2) ||
        !IsEqualGUID(newMediaType.formattype, FORMAT_VideoInfo2) ||
        oldMediaType.cbFormat != newMediaType.cbFormat ||
        oldMediaType.cbFormat < sizeof(VIDEOINFOHEADER2) ||
        oldMediaType.pbFormat == nullptr || newMediaType.pbFormat == nullptr) {
        return false;
    }

    constexpr size_t controlFlagsOffset = offsetof(VIDEOINFOHEADER2, dwControlFlags);
    constexpr size_t afterControlFlags = controlFlagsOffset + sizeof(DWORD);
    if (std::memcmp(oldMediaType.pbFormat, newMediaType.pbFormat, controlFlagsOffset) != 0 ||
        std::memcmp(oldMediaType.pbFormat + afterControlFlags, newMediaType.pbFormat + afterControlFlags,
                    oldMediaType.cbFormat - afterControlFlags) != 0) {
        return false;
    }

    const auto &oldHeader = *reinterpret_cast<const VIDEOINFOHEADER2 *>(oldMediaType.pbFormat);
    const auto &newHeader = *reinterpret_cast<const VIDEOINFOHEADER2 *>(newMediaType.pbFormat);

    // Exact, observed SDR metadata transition: previously unspecified to
    // 4:2:0 / limited-range / BT.709 matrix, primaries and transfer function.
    // The exact-value guard intentionally prevents swallowing other color-space
    // changes (for example BT.2020/PQ or HLG).
    return oldHeader.dwControlFlags == 0 &&
           newHeader.dwControlFlags == 0x2880A581;
}

auto LogDynamicMediaTypeHeader(const CMediaType &oldMediaType, const AM_MEDIA_TYPE &newMediaType,
                               HRESULT timeHr, REFERENCE_TIME sampleStart, REFERENCE_TIME sampleStop,
                               HRESULT discontinuityHr) -> void {
    const bool sameHeader =
        IsEqualGUID(oldMediaType.majortype, newMediaType.majortype) &&
        IsEqualGUID(oldMediaType.subtype, newMediaType.subtype) &&
        oldMediaType.bFixedSizeSamples == newMediaType.bFixedSizeSamples &&
        oldMediaType.bTemporalCompression == newMediaType.bTemporalCompression &&
        oldMediaType.lSampleSize == newMediaType.lSampleSize &&
        IsEqualGUID(oldMediaType.formattype, newMediaType.formattype) &&
        oldMediaType.cbFormat == newMediaType.cbFormat;
    const bool sameFormatBlock =
        oldMediaType.cbFormat == newMediaType.cbFormat &&
        (oldMediaType.cbFormat == 0 ||
         (oldMediaType.pbFormat != nullptr && newMediaType.pbFormat != nullptr &&
          std::memcmp(oldMediaType.pbFormat, newMediaType.pbFormat, oldMediaType.cbFormat) == 0));

    Environment::GetInstance().Log(
        L"DYNAMIC_MT time_hr 0x%08lX start %lld stop %lld discontinuity_hr 0x%08lX same_header %d same_format_block %d old major %08lX subtype %08lX formattype %08lX cbFormat %lu sampleSize %lu new major %08lX subtype %08lX formattype %08lX cbFormat %lu sampleSize %lu",
        static_cast<unsigned long>(timeHr), static_cast<long long>(sampleStart), static_cast<long long>(sampleStop),
        static_cast<unsigned long>(discontinuityHr), sameHeader, sameFormatBlock,
        static_cast<unsigned long>(oldMediaType.majortype.Data1),
        static_cast<unsigned long>(oldMediaType.subtype.Data1),
        static_cast<unsigned long>(oldMediaType.formattype.Data1),
        oldMediaType.cbFormat, oldMediaType.lSampleSize,
        static_cast<unsigned long>(newMediaType.majortype.Data1),
        static_cast<unsigned long>(newMediaType.subtype.Data1),
        static_cast<unsigned long>(newMediaType.formattype.Data1),
        newMediaType.cbFormat, newMediaType.lSampleSize);
}

}
'''
s = s.replace(namespace_anchor, namespace_anchor + helper + "\n", 1)

receive_anchor = '''    AM_MEDIA_TYPE *pmt;
    pSample->GetMediaType(&pmt);
    if (pmt != nullptr && pmt->pbFormat != nullptr) {
        m_pInput->CurrentMediaType() = *pmt;'''
if s.count(receive_anchor) != 1:
    raise SystemExit("Could not uniquely locate Receive() media-type block; refusing to patch")
receive_replacement = '''    // Sparse timing trace separates upstream inter-arrival gaps from time spent in Filter processing.
    static thread_local const CSynthFilter *lastTraceFilter = nullptr;
    static thread_local bool hasLastTraceReceive = false;
    static thread_local std::chrono::steady_clock::time_point lastTraceReceiveTime{};
    const auto receiveTraceEntry = std::chrono::steady_clock::now();
    if (lastTraceFilter != this) {
        lastTraceFilter = this;
        hasLastTraceReceive = false;
    }
    const long long receiveGapUs = hasLastTraceReceive
        ? static_cast<long long>(std::chrono::duration_cast<std::chrono::microseconds>(receiveTraceEntry - lastTraceReceiveTime).count())
        : -1;
    lastTraceReceiveTime = receiveTraceEntry;
    hasLastTraceReceive = true;

    AM_MEDIA_TYPE *pmt;
    pSample->GetMediaType(&pmt);
    const bool traceHasDynamicMediaType = pmt != nullptr && pmt->pbFormat != nullptr;
    bool traceMetadataOnlyCandidate = false;
    if (traceHasDynamicMediaType) {
        const CMediaType &oldMediaType = m_pInput->CurrentMediaType();
        const bool alreadyHadPendingReconfigure = _isInputMediaTypeChanged || _needReloadScript;
        traceMetadataOnlyCandidate = !alreadyHadPendingReconfigure &&
            IsOnlyVideoInfo2ControlFlagsChanged(oldMediaType, *pmt);
        Environment::GetInstance().Log(
            L"DYNAMIC_MT_CLASSIFY metadata_only_bt709_candidate %d pending_reconfigure %d",
            traceMetadataOnlyCandidate, alreadyHadPendingReconfigure);
        REFERENCE_TIME traceSampleStart = 0;
        REFERENCE_TIME traceSampleStop = 0;
        const HRESULT traceTimeHr = pSample->GetTime(&traceSampleStart, &traceSampleStop);
        const HRESULT traceDiscontinuityHr = pSample->IsDiscontinuity();
        LogDynamicMediaTypeHeader(oldMediaType, *pmt, traceTimeHr, traceSampleStart, traceSampleStop, traceDiscontinuityHr);
        LogVideoInfo2FormatDiff(oldMediaType, *pmt);
    }
    if (traceHasDynamicMediaType || receiveGapUs >= 50000) {
        REFERENCE_TIME traceSampleStart = 0;
        REFERENCE_TIME traceSampleStop = 0;
        const HRESULT traceTimeHr = pSample->GetTime(&traceSampleStart, &traceSampleStop);
        const HRESULT traceDiscontinuityHr = pSample->IsDiscontinuity();
        Environment::GetInstance().Log(
            L"RECVTRACE enter gap_us %lld start %lld stop %lld time_hr 0x%08lX discontinuity_hr 0x%08lX has_media_type %d",
            receiveGapUs, static_cast<long long>(traceSampleStart), static_cast<long long>(traceSampleStop),
            static_cast<unsigned long>(traceTimeHr), static_cast<unsigned long>(traceDiscontinuityHr), traceHasDynamicMediaType);
    }
    if (pmt != nullptr && pmt->pbFormat != nullptr) {
        m_pInput->CurrentMediaType() = *pmt;'''
s = s.replace(receive_anchor, receive_replacement, 1)

# Keep upstream format-change behavior. Metadata-only classification is diagnostic
# evidence only; every dynamic media-type change still uses normal reconfiguration.
flag_anchor = '''        _inputVideoFormat = Format::GetVideoFormat(*pmt, &MainFrameServer::GetInstance());
        DeleteMediaType(pmt);
        _isInputMediaTypeChanged = true;'''
flag_replacement = '''        _inputVideoFormat = Format::GetVideoFormat(*pmt, &MainFrameServer::GetInstance());
        DeleteMediaType(pmt);
        _isInputMediaTypeChanged = !traceMetadataOnlyCandidate;
        if (traceMetadataOnlyCandidate) {
            Environment::GetInstance().Log(
                L"DYNAMIC_MT_FASTPATH applied metadata_only_control_flags old_control 00000000 new_control 2880A581 downstream_type_kept_existing 1");
        }'''
if s.count(flag_anchor) != 1:
    raise SystemExit("Could not uniquely locate input media-type reconfigure flag; refusing to patch")
s = s.replace(flag_anchor, flag_replacement, 1)


skip_anchor = '''    if (ShouldSkipFrame(pSample)) {
        m_bSampleSkipped = TRUE;
        return S_OK;
    }'''
skip_replacement = '''    if (ShouldSkipFrame(pSample)) {
        m_bSampleSkipped = TRUE;
        if (traceHasDynamicMediaType || receiveGapUs >= 50000) {
            Environment::GetInstance().Log(L"RECVTRACE skipped_before_AddInputSample gap_us %lld has_media_type %d",
                                           receiveGapUs, traceHasDynamicMediaType);
        }
        return S_OK;
    }'''
if s.count(skip_anchor) != 1:
    raise SystemExit("Could not uniquely locate ShouldSkipFrame block; refusing to patch")
s = s.replace(skip_anchor, skip_replacement, 1)

add_anchor = '''    m_tDecodeStart = timeGetTime();

    hr = frameHandler->AddInputSample(pSample);

    m_tDecodeStart = timeGetTime() - m_tDecodeStart;'''
add_replacement = '''    m_tDecodeStart = timeGetTime();

    const auto addInputSampleStart = std::chrono::steady_clock::now();
    const long long preAddInputUs = static_cast<long long>(std::chrono::duration_cast<std::chrono::microseconds>(addInputSampleStart - receiveTraceEntry).count());
    hr = frameHandler->AddInputSample(pSample);
    const auto addInputSampleEnd = std::chrono::steady_clock::now();
    const long long addInputSampleUs = static_cast<long long>(std::chrono::duration_cast<std::chrono::microseconds>(addInputSampleEnd - addInputSampleStart).count());
    if (traceHasDynamicMediaType || receiveGapUs >= 50000 || preAddInputUs >= 50000 || addInputSampleUs >= 50000) {
        REFERENCE_TIME traceSampleStart = 0;
        REFERENCE_TIME traceSampleStop = 0;
        const HRESULT traceTimeHr = pSample->GetTime(&traceSampleStart, &traceSampleStop);
        const HRESULT traceDiscontinuityHr = pSample->IsDiscontinuity();
        Environment::GetInstance().Log(
            L"RECVTRACE AddInputSample_done gap_us %lld pre_add_us %lld addinput_us %lld start %lld stop %lld time_hr 0x%08lX discontinuity_hr 0x%08lX has_media_type %d add_hr 0x%08lX",
            receiveGapUs, preAddInputUs, addInputSampleUs,
            static_cast<long long>(traceSampleStart), static_cast<long long>(traceSampleStop),
            static_cast<unsigned long>(traceTimeHr), static_cast<unsigned long>(traceDiscontinuityHr),
            traceHasDynamicMediaType, static_cast<unsigned long>(hr));
    }

    m_tDecodeStart = timeGetTime() - m_tDecodeStart;'''
if s.count(add_anchor) != 1:
    raise SystemExit("Could not uniquely locate AddInputSample call; refusing to patch")
s = s.replace(add_anchor, add_replacement, 1)
filter_cpp.write_text(s, encoding="utf-8", newline="")

# ---------- VapourSynth Filter: retain CAP28 and trace long AddInputSample waits ----------
s = frame_cpp.read_text(encoding="utf-8-sig")

# Split long Receive/AddInputSample calls into condition-variable wait time versus
# post-wait work. This is sparse, thresholded diagnostics only; it does not change
# the wait predicate, frame ordering, interpolation, or the proven CAP28 limit.
wait_anchor = '''    HRESULT hr;

    _addInputSampleCv.wait(_filter.m_csReceive, [this]() -> bool {'''
wait_replacement = '''    HRESULT hr;
    const auto addInputSampleWaitBegin = std::chrono::steady_clock::now();

    _addInputSampleCv.wait(_filter.m_csReceive, [this]() -> bool {'''
if s.count(wait_anchor) != 1:
    raise SystemExit("Could not uniquely locate AddInputSample wait start; refusing to patch")
s = s.replace(wait_anchor, wait_replacement, 1)

wait_end_anchor = '''    });

    if (_isFlushing || _isStopping) {'''
wait_end_replacement = '''    });
    const long long addInputSampleWaitUs = static_cast<long long>(
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - addInputSampleWaitBegin).count());
    if (addInputSampleWaitUs >= 20000 || _filter._isInputMediaTypeChanged || _filter._needReloadScript) {
        REFERENCE_TIME diagInputStart = 0;
        REFERENCE_TIME diagInputStop = 0;
        const HRESULT diagInputTimeHr = inputSample->GetTime(&diagInputStart, &diagInputStop);
        Environment::GetInstance().Log(
            L"STARTPOINTDIAG AddInputSample_wait wait_us %lld input_time_hr 0x%08lX input_start %lld input_stop %lld next_source %d last_used_source %d extra_src_buffer %d input_mt_changed %d need_reload %d flushing %d stopping %d",
            addInputSampleWaitUs, static_cast<unsigned long>(diagInputTimeHr),
            static_cast<long long>(diagInputStart), static_cast<long long>(diagInputStop),
            _nextSourceFrameNb, static_cast<int>(_lastUsedSourceFrameNb.load()), _extraSrcBuffer,
            _filter._isInputMediaTypeChanged, _filter._needReloadScript,
            static_cast<bool>(_isFlushing.load()), static_cast<bool>(_isStopping.load()));
    }

    if (_isFlushing || _isStopping) {'''
if (s.count(wait_end_anchor) != 1):
    raise SystemExit("Could not uniquely locate AddInputSample wait end; refusing to patch")
s = s.replace(wait_end_anchor, wait_end_replacement, 1)

ns = "namespace SynthFilter {\n\n"
if s.count(ns) != 1:
    raise SystemExit("Could not uniquely locate SynthFilter namespace in frame_handler.cpp")
s = s.replace(ns, ns + "namespace {\nconstexpr size_t MAX_PENDING_OUTPUT_FRAMES = 28;\n}\n\n", 1)

request_anchor = '''    while (_nextOutputFrameNb <= maxRequestOutputFrameNb) {
        // before every async request to a frame, we need to keep track of the request so that when flushing we can wait for
        // any pending request to finish before destroying the script

        {
            const std::unique_lock uniqueOutputLock(_outputMutex);

            _outputFrames.emplace(_nextOutputFrameNb, nullptr);
        }
        AVSF_VPS_API->getFrameAsync(_nextOutputFrameNb, MainFrameServer::GetInstance().GetScriptClip(), VpsGetFrameCallback, this);'''
request_replacement = '''    while (_nextOutputFrameNb <= maxRequestOutputFrameNb) {
        // Preserve the tested CAP28 bound while leaving request ordering unchanged.
        {
            const std::unique_lock uniqueOutputLock(_outputMutex);
            if (_outputFrames.size() >= MAX_PENDING_OUTPUT_FRAMES) {
                break;
            }
            _outputFrames.emplace(_nextOutputFrameNb, nullptr);
            _outputRequestTimes.emplace(_nextOutputFrameNb, std::chrono::steady_clock::now());
        }
        const int requestedOutputFrameNb = _nextOutputFrameNb;
        {
            const std::unique_lock uniqueOutputLock(_outputMutex);
            if (_outputFrames.size() >= MAX_PENDING_OUTPUT_FRAMES) {
                break;
            }
            _outputFrames.emplace(requestedOutputFrameNb, nullptr);
            _outputRequestTimes.emplace(requestedOutputFrameNb, std::chrono::steady_clock::now());
        }
        const auto getFrameAsyncStart = std::chrono::steady_clock::now();
        AVSF_VPS_API->getFrameAsync(requestedOutputFrameNb, MainFrameServer::GetInstance().GetScriptClip(), VpsGetFrameCallback, this);
        const long long getFrameAsyncUs = static_cast<long long>(
            std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now() - getFrameAsyncStart).count());
        if (getFrameAsyncUs >= 5000) {
            Environment::GetInstance().Log(
                L"STARTPOINTDIAG OutputFrame_submit frame %d getframeasync_us %lld",
                requestedOutputFrameNb, getFrameAsyncUs);
        }'''
if s.count(request_anchor) != 1:
    raise SystemExit("Could not uniquely locate async output request loop; refusing to patch")
s = s.replace(request_anchor, request_replacement, 1)

# Add request-to-callback timing for slow individual output frames. This records only
# the async request timestamp and callback latency; it does not change scheduling,
# queue capacity, delivery order, or the VapourSynth frame-generation request.
frame_header = root / "vapoursynth_filter/src/frame_handler.h"
header_anchor = "    std::map<int, AutoReleaseVSFrame> _outputFrames;\n"
header_replacement = header_anchor + "    std::map<int, std::chrono::steady_clock::time_point> _outputRequestTimes;\n"
h = frame_header.read_text(encoding="utf-8-sig")
if h.count(header_anchor) != 1:
    raise SystemExit("Could not uniquely locate output frame map in frame_handler.h")
frame_header.write_text(h.replace(header_anchor, header_replacement, 1), encoding="utf-8", newline="")

callback_anchor = '''    FrameHandler *frameHandler = static_cast<FrameHandler *>(userData);
    Environment::GetInstance().Log(L"Output frame %6d is ready, output queue size %2zd", n, frameHandler->_outputFrames.size());'''
callback_replacement = '''    FrameHandler *frameHandler = static_cast<FrameHandler *>(userData);
    const auto callbackEntry = std::chrono::steady_clock::now();
    long long requestToCallbackUs = -1;
    const auto callbackLockBegin = std::chrono::steady_clock::now();
    {
        const std::unique_lock uniqueOutputLock(frameHandler->_outputMutex);
        if (const auto requestIter = frameHandler->_outputRequestTimes.find(n);
            requestIter != frameHandler->_outputRequestTimes.end()) {
            requestToCallbackUs = static_cast<long long>(
                std::chrono::duration_cast<std::chrono::microseconds>(callbackEntry - requestIter->second).count());
            frameHandler->_outputRequestTimes.erase(requestIter);
        }
    }
    const long long callbackLockLookupUs = static_cast<long long>(
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - callbackLockBegin).count());
    if (callbackLockLookupUs >= 5000) {
        Environment::GetInstance().Log(
            L"STARTPOINTDIAG OutputFrame_callback_lock frame %d lock_lookup_us %lld",
            n, callbackLockLookupUs);
    }
    if (requestToCallbackUs >= 20000 || f == nullptr) {
        Environment::GetInstance().Log(
            L"STARTPOINTDIAG OutputFrame_ready frame %d request_to_callback_us %lld failed %d",
            n, requestToCallbackUs, f == nullptr);
    }
    Environment::GetInstance().Log(L"Output frame %6d is ready, output queue size %2zd", n, frameHandler->_outputFrames.size());'''
if s.count(callback_anchor) != 1:
    raise SystemExit("Could not uniquely locate VapourSynth output callback timing anchor")
s = s.replace(callback_anchor, callback_replacement, 1)
frame_cpp.write_text(s, encoding="utf-8", newline="")
print("Patched CAP28 and sparse start-point/output-frame latency diagnostics; frame order and delivery path unchanged.")
