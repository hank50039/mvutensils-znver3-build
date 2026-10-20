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
    bool traceMetadataOnlyFastPath = false;
    if (traceHasDynamicMediaType) {
        const CMediaType &oldMediaType = m_pInput->CurrentMediaType();
        const bool alreadyHadPendingReconfigure = _isInputMediaTypeChanged || _needReloadScript;
        traceMetadataOnlyFastPath = !alreadyHadPendingReconfigure &&
            IsOnlyVideoInfo2ControlFlagsChanged(oldMediaType, *pmt);
        Environment::GetInstance().Log(
            L"DYNAMIC_MT_CLASSIFY metadata_only_bt709_candidate %d pending_reconfigure %d",
            traceMetadataOnlyFastPath, alreadyHadPendingReconfigure);
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

# Set the existing format-change flag only for real layout changes. The narrow, exact
# BT.709 SDR metadata-only case updates per-frame color props without resetting
# the input prebuffer or reloading the VapourSynth script.
flag_anchor = '''        _inputVideoFormat = Format::GetVideoFormat(*pmt, &MainFrameServer::GetInstance());
        DeleteMediaType(pmt);
        _isInputMediaTypeChanged = true;'''
flag_replacement = '''        _inputVideoFormat = Format::GetVideoFormat(*pmt, &MainFrameServer::GetInstance());
        DeleteMediaType(pmt);
        _isInputMediaTypeChanged = !traceMetadataOnlyFastPath;
        if (traceMetadataOnlyFastPath) {
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

# ---------- VapourSynth Filter: retain the proven 28-frame request cap ----------
s = frame_cpp.read_text(encoding="utf-8-sig")
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
        // Keep asynchronous requests bounded; the proven PotPlayer 4K baseline uses 28 pending outputs.
        {
            const std::unique_lock uniqueOutputLock(_outputMutex);
            if (_outputFrames.size() >= MAX_PENDING_OUTPUT_FRAMES) {
                break;
            }
            _outputFrames.emplace(_nextOutputFrameNb, nullptr);
        }
        AVSF_VPS_API->getFrameAsync(_nextOutputFrameNb, MainFrameServer::GetInstance().GetScriptClip(), VpsGetFrameCallback, this);'''
if s.count(request_anchor) != 1:
    raise SystemExit("Could not uniquely locate async output request loop; refusing to patch")
s = s.replace(request_anchor, request_replacement, 1)

callback_anchor = '''    FrameHandler *frameHandler = static_cast<FrameHandler *>(userData);
    Environment::GetInstance().Log(L"Output frame %6d is ready, output queue size %2zd", n, frameHandler->_outputFrames.size());'''
callback_replacement = '''    FrameHandler *frameHandler = static_cast<FrameHandler *>(userData);
    size_t outputQueueSize = 0;
    {
        const std::shared_lock sharedOutputLock(frameHandler->_outputMutex);
        outputQueueSize = frameHandler->_outputFrames.size();
    }
    Environment::GetInstance().Log(L"Output frame %6d is ready, output queue size %2zd", n, outputQueueSize);'''
if s.count(callback_anchor) != 1:
    raise SystemExit("Could not uniquely locate async callback queue-size log; refusing to patch")
s = s.replace(callback_anchor, callback_replacement, 1)
frame_cpp.write_text(s, encoding="utf-8", newline="")

# ---------- CAP-28 request/readiness/delivery timing for start-position comparison ----------
# Keep the existing BT.709 metadata-only fast path and cap of 28 unchanged.
# The added clocks are diagnostic only and let paired logs be aligned by media timestamp.
header_path = root / "vapoursynth_filter/src/frame_handler.h"
replace_once(
    header_path,
    "#pragma once\n",
    "#pragma once\n\n#include <chrono>\n#include <map>\n",
    "start-point diagnostic header includes",
)
replace_once(
    header_path,
    "    std::map<int, AutoReleaseVSFrame> _outputFrames;\n",
    "    std::map<int, AutoReleaseVSFrame> _outputFrames;\n"
    "    // Diagnostic-only timestamps; protected by _outputMutex.\n"
    "    std::map<int, std::chrono::steady_clock::time_point> _outputRequestTimes;\n"
    "    std::map<int, std::chrono::steady_clock::time_point> _outputReadyTimes;\n"
    "    std::chrono::steady_clock::time_point _diagResetTime = std::chrono::steady_clock::now();\n"
    "    bool _outputRequestThrottleLogged = false;\n",
    "start-point diagnostic timing members",
)

s = frame_cpp.read_text(encoding="utf-8-sig")

def diag_replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}; refusing to patch")
    return source.replace(old, new, 1)

# Explicit standard headers for the new diagnostic-only timing maps and optional interval.
s = diag_replace_once(
    s,
    '#include "frame_handler.h"\n\n',
    '#include "frame_handler.h"\n\n#include <chrono>\n#include <optional>\n#include <ranges>\n\n',
    "start-point diagnostic includes",
)

s = diag_replace_once(
    s,
    '''            if (_outputFrames.size() >= MAX_PENDING_OUTPUT_FRAMES) {
                break;
            }''',
    '''            if (_outputFrames.size() >= MAX_PENDING_OUTPUT_FRAMES) {
                if (!_outputRequestThrottleLogged) {
                    const auto capEventNow = std::chrono::steady_clock::now();
                    const long long sinceResetUs = std::chrono::duration_cast<std::chrono::microseconds>(
                        capEventNow - _diagResetTime).count();
                    Environment::GetInstance().Log(
                        L"CAP28DIAG_THROTTLE next_output %d max_request %d process_source %d input_media_start_ticks %lld queued %zu since_reset_us %lld",
                        _nextOutputFrameNb, maxRequestOutputFrameNb, processSourceFrameIters[0]->first,
                        static_cast<long long>(inputSampleStartTime), _outputFrames.size(), sinceResetUs);
                    _outputRequestThrottleLogged = true;
                }
                break;
            }''',
    "record first cap-28 throttle per reset",
)

s = diag_replace_once(
    s,
    "            _outputFrames.emplace(_nextOutputFrameNb, nullptr);\n",
    "            _outputFrames.emplace(_nextOutputFrameNb, nullptr);\n"
    "            _outputRequestTimes[_nextOutputFrameNb] = std::chrono::steady_clock::now();\n",
    "record output request clock",
)

s = diag_replace_once(
    s,
    "            frameHandler->_outputFrames.erase(n);\n",
    "            frameHandler->_outputFrames.erase(n);\n"
    "            frameHandler->_outputRequestTimes.erase(n);\n"
    "            frameHandler->_outputReadyTimes.erase(n);\n",
    "clear request and ready clocks when callback arrives during flush",
)

s = diag_replace_once(
    s,
    "            frameHandler->_outputFrames[n] = const_cast<VSFrame *>(f);\n",
    "            frameHandler->_outputFrames[n] = const_cast<VSFrame *>(f);\n"
    "            frameHandler->_outputReadyTimes[n] = std::chrono::steady_clock::now();\n",
    "record output-ready clock",
)

s = diag_replace_once(
    s,
    "    _outputFrames.clear();\n",
    "    _outputFrames.clear();\n"
    "    _outputRequestTimes.clear();\n"
    "    _outputReadyTimes.clear();\n",
    "clear frame timing maps at EndFlush",
)

s = diag_replace_once(
    s,
    "    _lastUsedSourceFrameNb = 0;\n",
    "    _lastUsedSourceFrameNb = 0;\n"
    "    _outputRequestThrottleLogged = false;\n"
    "    _diagResetTime = std::chrono::steady_clock::now();\n"
    "    Environment::GetInstance().Log(L\"CAP28DIAG_RESET local_frame_indices_reset 1\");\n",
    "tag each new filter input epoch",
)

s = diag_replace_once(
    s,
    "auto FrameHandler::WorkerProc() -> void {\n    const auto ResetOutput = [this]() -> void {",
    "auto FrameHandler::WorkerProc() -> void {\n"
    "    std::optional<std::chrono::steady_clock::time_point> lastDeliveryStartTime;\n"
    "    const auto ResetOutput = [this, &lastDeliveryStartTime]() -> void {",
    "track inter-delivery intervals",
)

s = diag_replace_once(
    s,
    "        _frameRateCheckpointDeliveryFrameNb = 0;\n        _currentDeliveryFrameRate = 0;\n    };",
    "        _frameRateCheckpointDeliveryFrameNb = 0;\n"
    "        _currentDeliveryFrameRate = 0;\n"
    "        lastDeliveryStartTime.reset();\n"
    "    };",
    "reset inter-delivery interval tracker",
)

wait_start = "        decltype(_outputFrames)::iterator iter;"
wait_end = "\n\n        if (_isFlushing) {\n            continue;\n        }"
if s.count(wait_start) != 1:
    raise SystemExit("start-point timing: expected one worker output wait block")
start_index = s.find(wait_start)
end_index = s.find(wait_end, start_index + len(wait_start))
if end_index < 0:
    raise SystemExit("start-point timing: worker wait block end marker not found")
wait_block = '''        decltype(_outputFrames)::iterator iter;
        long long requestToWorkerUs = -1;
        long long readyToWorkerUs = -1;
        long long requestToReadyUs = -1;
        std::size_t queueDepthAtWake = 0;
        std::size_t readyBacklogAtWake = 0;
        {
            std::shared_lock sharedOutputLock(_outputMutex);

            _deliverSampleCv.wait(sharedOutputLock, [this, &iter]() -> bool {
                if (_isFlushing) {
                    return true;
                }

                iter = _outputFrames.find(_nextDeliveryFrameNb);
                if (iter == _outputFrames.end()) {
                    return false;
                }

                return iter->second.frame != nullptr;
            });

            if (!_isFlushing) {
                const auto workerWakeTime = std::chrono::steady_clock::now();
                queueDepthAtWake = _outputFrames.size();
                readyBacklogAtWake = static_cast<std::size_t>(std::ranges::count_if(
                    _outputFrames | std::views::values,
                    [](const AutoReleaseVSFrame &frame) { return frame.frame != nullptr; }));

                if (const auto requestIter = _outputRequestTimes.find(iter->first);
                    requestIter != _outputRequestTimes.end()) {
                    requestToWorkerUs = std::chrono::duration_cast<std::chrono::microseconds>(
                        workerWakeTime - requestIter->second).count();
                }

                if (const auto readyIter = _outputReadyTimes.find(iter->first);
                    readyIter != _outputReadyTimes.end()) {
                    readyToWorkerUs = std::chrono::duration_cast<std::chrono::microseconds>(
                        workerWakeTime - readyIter->second).count();
                }

                if (requestToWorkerUs >= 0 && readyToWorkerUs >= 0) {
                    requestToReadyUs = requestToWorkerUs - readyToWorkerUs;
                }
            }
        }'''
s = s[:start_index] + wait_block + s[end_index:]

delivery_start = "        if (ATL::CComPtr<IMediaSample> outSample; PrepareOutputSample("
delivery_end = "\n        GarbageCollect(sourceFrameNb - 1);"
if s.count(delivery_start) != 1:
    raise SystemExit("start-point timing: expected one output delivery block")
start_index = s.find(delivery_start)
end_index = s.find(delivery_end, start_index + len(delivery_start))
if end_index < 0:
    raise SystemExit("start-point timing: output delivery block end marker not found")
delivery_block = '''        const auto prepareStartTime = std::chrono::steady_clock::now();
        ATL::CComPtr<IMediaSample> outSample;
        if (PrepareOutputSample(outSample, iter->first, iter->second.frame, sourceFrameNb)) {
            const auto deliveryStartTime = std::chrono::steady_clock::now();
            const long long prepareUs = std::chrono::duration_cast<std::chrono::microseconds>(
                deliveryStartTime - prepareStartTime).count();

            long long deliveryCallGapUs = -1;
            if (lastDeliveryStartTime.has_value()) {
                deliveryCallGapUs = std::chrono::duration_cast<std::chrono::microseconds>(
                    deliveryStartTime - *lastDeliveryStartTime).count();
            }
            lastDeliveryStartTime = deliveryStartTime;

            const long long sinceResetUs = std::chrono::duration_cast<std::chrono::microseconds>(
                deliveryStartTime - _diagResetTime).count();

            _filter.m_pOutput->Deliver(outSample);
            const auto deliveryEndTime = std::chrono::steady_clock::now();
            const long long downstreamDeliverUs = std::chrono::duration_cast<std::chrono::microseconds>(
                deliveryEndTime - deliveryStartTime).count();

            RefreshDeliveryFrameRates(iter->first);

            Environment::GetInstance().Log(
                L"CAP28DIAG_DELIVER frame %d source_frame %d req_to_ready_us %lld ready_to_worker_us %lld req_to_worker_us %lld pending %zu ready_pending %zu prepare_us %lld downstream_deliver_us %lld deliver_call_gap_us %lld since_reset_us %lld",
                iter->first, sourceFrameNb, requestToReadyUs, readyToWorkerUs, requestToWorkerUs,
                queueDepthAtWake, readyBacklogAtWake, prepareUs, downstreamDeliverUs,
                deliveryCallGapUs, sinceResetUs);
            Environment::GetInstance().Log(L"Deliver output sample %6d from source frame %6d", iter->first, sourceFrameNb);
        } else {
            const long long prepareUs = std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now() - prepareStartTime).count();
            Environment::GetInstance().Log(
                L"CAP28DIAG_PREPARE_FAILED frame %d req_to_ready_us %lld ready_to_worker_us %lld req_to_worker_us %lld pending %zu ready_pending %zu prepare_us %lld",
                iter->first, requestToReadyUs, readyToWorkerUs, requestToWorkerUs,
                queueDepthAtWake, readyBacklogAtWake, prepareUs);
        }

        const int completedFrameNb = iter->first;
        {
            const std::unique_lock uniqueOutputLock(_outputMutex);

            _outputFrames.erase(iter);
            _outputRequestTimes.erase(completedFrameNb);
            _outputReadyTimes.erase(completedFrameNb);
        }
'''
s = s[:start_index] + delivery_block + s[end_index:]
frame_cpp.write_text(s, encoding="utf-8", newline="")

print("Added CAP28 per-frame request/readiness/worker/delivery timing without changing the queue cap or BT.709 fast path.")

print("Patched shared media-type diagnostics and sparse Receive timing.")
print("Patched VapourSynth output queue cap at 28, with synchronized queue-size logging.")
