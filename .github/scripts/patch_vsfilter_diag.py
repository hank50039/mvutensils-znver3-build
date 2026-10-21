from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_vsfilter_diag.py <avisynth_filter-source-root>")

root = Path(sys.argv[1]).resolve()
filter_cpp = root / "filter_common/src/filter.cpp"
frame_cpp = root / "vapoursynth_filter/src/frame_handler.cpp"
environment_cpp = root / "filter_common/src/environment.cpp"

def replace_once(path: Path, old: str, new: str, label: str) -> None:
    source = path.read_text(encoding="utf-8-sig")
    hits = source.count(old)
    if hits != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {hits}; refusing to patch")
    path.write_text(source.replace(old, new, 1), encoding="utf-8", newline="")

# Production patch: preserve BOTH previously tested BT.709 fast paths.
# Use byte-exact guard; neither real colorspace transitions nor dimensions
# nor image pixel layout changes are bypassed.
s = filter_cpp.read_text(encoding="utf-8-sig")
include_anchor = '#include "filter.h"\n\n#include "constants.h"'
if s.count(include_anchor) != 1:
    raise SystemExit("filter.cpp includes have changed; refusing to patch")
s = s.replace(include_anchor, '#include "filter.h"\n\n#include <cstddef>\n#include <cstring>\n#include <dvdmedia.h>\n\n#include "constants.h"', 1)
namespace_anchor = "namespace SynthFilter {\n\n"
if s.count(namespace_anchor) != 1:
    raise SystemExit("filter.cpp namespace anchor changed; refusing to patch")
helper = r'''auto IsOnlyVideoInfo2ControlFlagsChanged(const CMediaType &oldMediaType, const AM_MEDIA_TYPE &newMediaType) -> bool {
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
}'''
s = s.replace(namespace_anchor, namespace_anchor + "namespace {\n" + helper + "\n}\n\n", 1)
filter_cpp.write_text(s, encoding="utf-8", newline="")

# Precheck Fast Path: avoid redundant auxiliary VapourSynth script reloads
# for the exact metadata-only input media type transition.
replace_once(
    filter_cpp,
    '''auto CSynthFilter::CheckInputType(const CMediaType *mtIn) -> HRESULT {
    bool result = false;''',
    '''auto CSynthFilter::CheckInputType(const CMediaType *mtIn) -> HRESULT {
    if (mtIn != nullptr && IsActive() && m_pInput->IsConnected() &&
        !_isInputMediaTypeChanged && !_needReloadScript &&
        IsOnlyVideoInfo2ControlFlagsChanged(m_pInput->CurrentMediaType(), *mtIn)) {
        return S_OK;
    }
    bool result = false;''',
    "Precheck BT.709 metadata-only fast path"
)

# Existing Receive Fast Path: keep refreshing the input color metadata,
# but skip unnecessary main script/renderer format renegotiation.
replace_once(
    filter_cpp,
    '''    AM_MEDIA_TYPE *pmt;
    pSample->GetMediaType(&pmt);
    if (pmt != nullptr && pmt->pbFormat != nullptr) {
        m_pInput->CurrentMediaType() = *pmt;''',
    '''    AM_MEDIA_TYPE *pmt;
    pSample->GetMediaType(&pmt);
    if (pmt != nullptr && pmt->pbFormat != nullptr) {
        const bool metadataOnlyBt709 =
            !_isInputMediaTypeChanged && !_needReloadScript &&
            IsOnlyVideoInfo2ControlFlagsChanged(m_pInput->CurrentMediaType(), *pmt);
        m_pInput->CurrentMediaType() = *pmt;''',
    "Receive exact metadata-only guard"
)
replace_once(
    filter_cpp,
    '''        _inputVideoFormat = Format::GetVideoFormat(*pmt, &MainFrameServer::GetInstance());
        DeleteMediaType(pmt);
        _isInputMediaTypeChanged = true;''',
    '''        _inputVideoFormat = Format::GetVideoFormat(*pmt, &MainFrameServer::GetInstance());
        DeleteMediaType(pmt);
        _isInputMediaTypeChanged = !metadataOnlyBt709;''',
    "Receive skip-only-reconfigure path"
)

# CAP28: keep the proved upper bound and original request sequence, but
# remove every diagnostic timestamp, map and callback instrumentation.
s = frame_cpp.read_text(encoding="utf-8-sig")
if s.count("namespace SynthFilter {\n\n") != 1:
    raise SystemExit("frame_handler.cpp namespace anchor changed; refusing to patch")
s = s.replace("namespace SynthFilter {\n\n",
              "namespace SynthFilter {\n\nnamespace {\nconstexpr size_t MAX_PENDING_OUTPUT_FRAMES = 28;\n}\n\n", 1)
frame_cpp.write_text(s, encoding="utf-8", newline="")

replace_once(
    frame_cpp,
    '''    while (_nextOutputFrameNb <= maxRequestOutputFrameNb) {
        // before every async request to a frame, we need to keep track of the request so that when flushing we can wait for
        // any pending request to finish before destroying the script

        {
            const std::unique_lock uniqueOutputLock(_outputMutex);

            _outputFrames.emplace(_nextOutputFrameNb, nullptr);
        }
        AVSF_VPS_API->getFrameAsync(_nextOutputFrameNb, MainFrameServer::GetInstance().GetScriptClip(), VpsGetFrameCallback, this);

        _nextOutputFrameNb += 1;
    }''',
    '''    {
        // Register the highest requestable output frame for this source sample.
        // Completion of an output frame can now refill CAP28 without new input.
        const std::unique_lock uniqueOutputLock(_outputMutex);
        _maxRequestOutputFrameNb = std::max(_maxRequestOutputFrameNb, maxRequestOutputFrameNb);
    }
    RequestPendingOutputFrames();''',
    "CAP28 outstanding-output request bound"
)

# CAP28 drain fix: without an input sample after decoder EOS, a queue that
# became full would never submit remaining output requests. Pump after each
# output is delivered, using the same pending map limit and original ordering.
replace_once(
    root / "vapoursynth_filter/src/frame_handler.h",
    '''    auto WorkerProc() -> void;
    auto GarbageCollect(int srcFrameNb) -> void;''',
    '''    auto WorkerProc() -> void;
    auto RequestPendingOutputFrames() -> void;
    auto GarbageCollect(int srcFrameNb) -> void;''',
    "Declaration for output request replenishment"
)
replace_once(
    root / "vapoursynth_filter/src/frame_handler.h",
    '''    int _nextOutputFrameNb;
    REFERENCE_TIME _nextOutputFrameStartTime;''',
    '''    int _nextOutputFrameNb;
    int _maxRequestOutputFrameNb = -1;
    REFERENCE_TIME _nextOutputFrameStartTime;''',
    "Saved highest eligible output frame"
)
replace_once(
    frame_cpp,
    '''auto FrameHandler::GetSourceFrame(int frameNb) -> const VSFrame * {''',
    '''auto FrameHandler::RequestPendingOutputFrames() -> void {
    // Both producer and worker can trigger this method. Reserve each request
    // under the output mutex, then call the VS API WITHOUT holding it:
    // callbacks need to acquire the same mutex to store completed frames.
    while (!_isFlushing && !_isStopping) {
        int n;
        {
            const std::unique_lock uniqueOutputLock(_outputMutex);
            if (_isFlushing || _isStopping ||
                _outputFrames.size() >= MAX_PENDING_OUTPUT_FRAMES ||
                _nextOutputFrameNb > _maxRequestOutputFrameNb) {
                return;
            }
            n = _nextOutputFrameNb++;
            _outputFrames.emplace(n, nullptr);
        }
        AVSF_VPS_API->getFrameAsync(n, MainFrameServer::GetInstance().GetScriptClip(),
                                  VpsGetFrameCallback, this);
    }
}

auto FrameHandler::GetSourceFrame(int frameNb) -> const VSFrame * {''',
    "CAP28 refill worker method"
)
replace_once(
    frame_cpp,
    '''    _nextOutputFrameNb = 0;
    _lastUsedSourceFrameNb = 0;''',
    '''    _nextOutputFrameNb = 0;
    _maxRequestOutputFrameNb = -1;
    _lastUsedSourceFrameNb = 0;''',
    "Reinitialize queued request watermark on seeking/flush"
)
replace_once(
    frame_cpp,
    '''        GarbageCollect(sourceFrameNb - 1);
        _nextDeliveryFrameNb += 1;''',
    '''        GarbageCollect(sourceFrameNb - 1);
        _nextDeliveryFrameNb += 1;

        // Critical at end-of-stream: continue issuing eligible output requests
        // even after the decoder has stopped delivering new input samples.
        RequestPendingOutputFrames();''',
    "Refill CAP28 from output delivery thread after each completed frame"
)


# DirectShow's default transform-filter EndOfStream sends EOS immediately
# because it assumes there is NO output worker / queued samples. This Filter
# has both, so defer EOS until CAP28 is fully drained and all eligible
# output frames have been delivered to the renderer.
replace_once(
    root / "filter_common/src/filter.h",
    '''    auto Receive(IMediaSample *pSample) -> HRESULT override;
    auto BeginFlush() -> HRESULT override;''',
    '''    auto Receive(IMediaSample *pSample) -> HRESULT override;
    auto EndOfStream() -> HRESULT override;
    auto BeginFlush() -> HRESULT override;''',
    "Declare correct asynchronous end-of-stream notification"
)
replace_once(
    filter_cpp,
    '''    return hr;
}

auto CSynthFilter::BeginFlush() -> HRESULT {''',
    '''    return hr;
}

auto CSynthFilter::EndOfStream() -> HRESULT {
#ifdef AVSF_VAPOURSYNTH
    // VapourSynth produces asynchronously on a separate delivery worker.
    // Do not forward decoder EOS until the pending output frames are sent.
    frameHandler->SignalInputEndOfStream();
    return S_OK;
#else
    // Leave the AviSynth variant's existing EOS behavior unchanged.
    return __super::EndOfStream();
#endif
}

auto CSynthFilter::BeginFlush() -> HRESULT {''',
    "Override premature downstream end-of-stream notification"
)
replace_once(
    root / "vapoursynth_filter/src/frame_handler.h",
    '''    auto GetSourceFrame(int frameNb) -> const VSFrame *;
    auto BeginFlush() -> void;''',
    '''    auto GetSourceFrame(int frameNb) -> const VSFrame *;
    auto SignalInputEndOfStream() -> void;
    auto BeginFlush() -> void;''',
    "Expose EOS notification to owning filter"
)
replace_once(
    root / "vapoursynth_filter/src/frame_handler.h",
    '''    auto RequestPendingOutputFrames() -> void;
    auto GarbageCollect(int srcFrameNb) -> void;''',
    '''    auto RequestPendingOutputFrames() -> void;
    auto DeliverEndOfStreamIfDrained() -> void;
    auto GarbageCollect(int srcFrameNb) -> void;''',
    "Queue-drained end-of-stream declarations"
)
replace_once(
    root / "vapoursynth_filter/src/frame_handler.h",
    '''    int _maxRequestOutputFrameNb = -1;
    REFERENCE_TIME _nextOutputFrameStartTime;''',
    '''    int _maxRequestOutputFrameNb = -1;
    std::atomic<bool> _inputEosSeen = false;  // read by source frame waiters
    bool _eosForwarded = false;  // guarded by _outputMutex
    REFERENCE_TIME _nextOutputFrameStartTime;''',
    "End-of-stream state tracking"
)
replace_once(
    frame_cpp,
    '''auto FrameHandler::RequestPendingOutputFrames() -> void {''',
    '''auto FrameHandler::SignalInputEndOfStream() -> void {
    // Normal startup triggers the main VapourSynth script only after the
    // configured InitialSrcBuffer (100 here). When seeking into the final few
    // seconds there may be fewer than 100 decoded source samples remaining.
    // Without this short-tail startup the renderer gets EOS with zero frames.
    if (_nextSourceFrameNb >= NUM_SRC_FRAMES_PER_PROCESSING &&
        _nextSourceFrameNb < Environment::GetInstance().GetInitialSrcBuffer()) {
        MainFrameServer::GetInstance().ReloadScript(_filter.m_pInput->CurrentMediaType(), true);
    }

    // The final source frame never gets duration properties from a following
    // source frame. Derive a final duration from the previous source interval
    // (or the source's nominal duration), so any waiting FlowFPS requests
    // referring to the last frame can complete normally.
    {
        const std::unique_lock sourceLock(_sourceMutex);
        if (!_sourceFrames.empty()) {
            const auto last = std::prev(_sourceFrames.end());
            VSMap *props = AVSF_VPS_API->getFramePropertiesRW(last->second.autoFrame.frame);
            // CRITICAL BUGFIX: VSAPI mapNumElements returns -1 when a
            // property key is ABSENT (not 0). Prior ==0 checks never ran
            // for the terminal frame, leaving GetSourceFrame stuck at EOS.
            const int durationNumElementsBefore = AVSF_VPS_API->mapNumElements(props, FRAME_PROP_NAME_DURATION_NUM);
            const int durationDenElementsBefore = AVSF_VPS_API->mapNumElements(props, FRAME_PROP_NAME_DURATION_DEN);
            EofWatch(false, L"EOF_DURATION_BEFORE frame=%d num_elements=%d den_elements=%d",
                     last->first, durationNumElementsBefore, durationDenElementsBefore);
            if (durationNumElementsBefore <= 0 || durationDenElementsBefore <= 0) {
                REFERENCE_TIME duration = MainFrameServer::GetInstance().GetSourceAvgFrameDuration();
                if (last != _sourceFrames.begin()) {
                    const auto previous = std::prev(last);
                    if (last->second.startTime > previous->second.startTime) {
                        duration = last->second.startTime - previous->second.startTime;
                    }
                }
                if (duration > 0) {
                    REFERENCE_TIME numerator = duration;
                    REFERENCE_TIME denominator = UNITS;
                    CoprimeIntegers(numerator, denominator);
                    const int setNumResult = AVSF_VPS_API->mapSetInt(props, FRAME_PROP_NAME_DURATION_NUM, numerator, maReplace);
                    const int setDenResult = AVSF_VPS_API->mapSetInt(props, FRAME_PROP_NAME_DURATION_DEN, denominator, maReplace);
                    EofWatch(false, L"EOF_DURATION_AFTER frame=%d num_elements=%d den_elements=%d set_num_result=%d set_den_result=%d duration_num=%lld duration_den=%lld",
                             last->first,
                             AVSF_VPS_API->mapNumElements(props, FRAME_PROP_NAME_DURATION_NUM),
                             AVSF_VPS_API->mapNumElements(props, FRAME_PROP_NAME_DURATION_DEN),
                             setNumResult, setDenResult,
                             static_cast<long long>(numerator),
                             static_cast<long long>(denominator));
                }
            }
        }
    }
    {
        const std::unique_lock outputLock(_outputMutex);
        // Schedule outputs through the last source frame's nominal
        // presentation interval, rather than stopping one source frame early.
        // Uses exactly the same time-ratio convention as AddInputSample().
        if (_nextSourceFrameNb >= NUM_SRC_FRAMES_PER_PROCESSING) {
            const REFERENCE_TIME srcDuration = MainFrameServer::GetInstance().GetSourceAvgFrameDuration();
            const REFERENCE_TIME outDuration = MainFrameServer::GetInstance().GetScriptAvgFrameDuration();
            if (srcDuration > 0 && outDuration > 0) {
                const auto count = llMulDiv(static_cast<LONGLONG>(_nextSourceFrameNb),
                                            srcDuration, outDuration, 0);
                const int lastOutput = static_cast<int>(count - 1);
                _maxRequestOutputFrameNb = std::max(_maxRequestOutputFrameNb, lastOutput);
            }
        }
        _inputEosSeen.store(true, std::memory_order_release);
    }
    // Wake any source callbacks blocked waiting for lookahead frames that
    // the upstream decoder will never deliver.
    _newSourceFrameCv.notify_all();

    // Continue draining the final eligible frames even though the decoder
    // will never call AddInputSample again.
    RequestPendingOutputFrames();
    DeliverEndOfStreamIfDrained();
}

auto FrameHandler::DeliverEndOfStreamIfDrained() -> void {
    bool deliverEos = false;
    {
        const std::unique_lock outputLock(_outputMutex);
        if (_inputEosSeen && !_eosForwarded && !_isFlushing && !_isStopping &&
            _nextOutputFrameNb > _maxRequestOutputFrameNb && _outputFrames.empty()) {
            _eosForwarded = true;
            deliverEos = true;
        }
    }
    if (deliverEos && !_isFlushing && !_isStopping) {
        _filter.m_pOutput->DeliverEndOfStream();
    }
}

auto FrameHandler::RequestPendingOutputFrames() -> void {''',
    "Finalize last source frame and delay EOS until all outputs are delivered"
)
replace_once(
    frame_cpp,
    '''    _maxRequestOutputFrameNb = -1;
    _lastUsedSourceFrameNb = 0;''',
    '''    _maxRequestOutputFrameNb = -1;
    {
        const std::unique_lock outputLock(_outputMutex);
        _inputEosSeen = false;
        _eosForwarded = false;
    }
    _lastUsedSourceFrameNb = 0;''',
    "Clear stale EOS state on graph start/reload/seek"
)
replace_once(
    frame_cpp,
    '''        RequestPendingOutputFrames();
    }

    Environment::GetInstance().Log(L"Stop worker thread");''',
    '''        RequestPendingOutputFrames();
        DeliverEndOfStreamIfDrained();
    }

    Environment::GetInstance().Log(L"Stop worker thread");''',
    "Send downstream EOS only after worker has delivered the last output"
)


# A VapourSynth plugin may request a look-ahead source frame beyond the last
# decoder frame. It is impossible for an unknown-length streaming source to
# infer EOF until upstream EndOfStream(), so GetSourceFrame() MUST wake at EOS.
# The look-ahead frame is then satisfied by repeating the final real frame;
# no fabricated future pixel data or interleaved output outside the file.
replace_once(
    frame_cpp,
    '''        iter = _sourceFrames.lower_bound(frameNb);
        if (iter == _sourceFrames.end()) {
            return false;
        }

        const VSMap *frameProps =''',
    '''        iter = _sourceFrames.lower_bound(frameNb);
        if (iter == _sourceFrames.end()) {
            if (!_inputEosSeen.load(std::memory_order_acquire)) {
                return false;
            }
            // Decoder has ended: forward references cannot ever arrive.
            // Hold the actual last source image for motion-vector lookahead.
            // Return dummy only for an empty/fully drained source.
            if (_sourceFrames.empty()) {
                return true;
            }
            iter = std::prev(_sourceFrames.end());
        }

        const VSMap *frameProps =''',
    "Wake EOF lookahead source frame requests"
)
replace_once(
    frame_cpp,
    '''    if (_isFlushing) {
        Environment::GetInstance().Log(L"Drain for frame %6d", frameNb);
        return FrameServerCommon::GetInstance().CreateSourceDummyFrame(MainFrameServer::GetInstance().GetVsCore());
    }

    Environment::GetInstance().Log(L"Return source frame %6d", frameNb);''',
    '''    if (_isFlushing || _sourceFrames.empty()) {
        Environment::GetInstance().Log(L"Drain for frame %6d", frameNb);
        return FrameServerCommon::GetInstance().CreateSourceDummyFrame(MainFrameServer::GetInstance().GetVsCore());
    }

    Environment::GetInstance().Log(L"Return source frame %6d", frameNb);''',
    "Guard empty end-of-stream source while flushing"
)


# EOF-only diagnostic build. No hot-path telemetry before decoder EOS; save
# failure events unconditionally. Readable trace in %TEMP%\VapourSynth_EOFWatch_<PID>.txt.
# Does not alter queue, interpolation, output ordering or EOS behavior.
replace_once(
    frame_cpp,
    '''#include "frame_handler.h"

#include "constants.h"''',
    '''#include "frame_handler.h"

#include <cstdarg>
#include <cstdio>
#include <cwchar>

#include "constants.h"''',
    "Include EOF-only watchdog CRT helpers"
)
replace_once(
    frame_cpp,
    '''namespace {
constexpr size_t MAX_PENDING_OUTPUT_FRAMES = 28;
}''',
    r'''namespace {
constexpr size_t MAX_PENDING_OUTPUT_FRAMES = 28;

// Disabled during normal playback. Only near EOS or on actual output error.
// This diagnostic output is independent from Environment::Log(), which stays off.
std::atomic<bool> g_eofWatchEnabled{false};
std::mutex g_eofWatchMutex;
FILE *g_eofWatchFile = nullptr;

auto EofWatch(bool always, const wchar_t *fmt, ...) noexcept -> void {
    if (!always && !g_eofWatchEnabled.load(std::memory_order_acquire)) {
        return;
    }
    const std::scoped_lock lock(g_eofWatchMutex);
    if (g_eofWatchFile == nullptr) {
        wchar_t filename[MAX_PATH]{};
        // User-requested persistent diagnostic location; create if absent.
        if (!CreateDirectoryW(L"D:\\SynthFilters", nullptr) &&
            GetLastError() != ERROR_ALREADY_EXISTS) {
            return;
        }
        swprintf_s(filename, L"D:\\SynthFilters\\VapourSynth_EOFWatch_%lu.txt",
                   GetCurrentProcessId());
        if (_wfopen_s(&g_eofWatchFile, filename, L"w") != 0 || g_eofWatchFile == nullptr) {
            return;
        }
        fwprintf_s(g_eofWatchFile, L"EOFWatch build: source+output state; all ticks QueryPerformanceCounter. File=%ls\n", filename);
    }
    LARGE_INTEGER tick{};
    QueryPerformanceCounter(&tick);
    fwprintf_s(g_eofWatchFile, L"qpc=%lld tid=%lu ", static_cast<long long>(tick.QuadPart), GetCurrentThreadId());
    va_list ap;
    va_start(ap, fmt);
    vfwprintf_s(g_eofWatchFile, fmt, ap);
    va_end(ap);
    fwprintf_s(g_eofWatchFile, L"\n");
    fflush(g_eofWatchFile);
}
}''',
    "Insert EOF-only watchdog sink"
)
replace_once(
    frame_cpp,
    '''auto FrameHandler::SignalInputEndOfStream() -> void {
    // Normal startup''',
    '''auto FrameHandler::SignalInputEndOfStream() -> void {
    g_eofWatchEnabled.store(true, std::memory_order_release);
    EofWatch(true, L"INPUT_EOS_ENTER source_received=%d next_requested=%d last_delivered=%d",
             _nextSourceFrameNb, _nextOutputFrameNb, _nextDeliveryFrameNb);
    // Normal startup''',
    "Record precise decoder EOS entry"
)
replace_once(
    frame_cpp,
    '''    // Continue draining the final eligible frames even though the decoder
    // will never call AddInputSample again.
    RequestPendingOutputFrames();''',
    '''    // Continue draining the final eligible frames even though the decoder
    // will never call AddInputSample again.
    {
        const std::shared_lock lock(_outputMutex);
        int emptyCount = 0;
        for (const auto &[id, frame] : _outputFrames) {
            if (frame.frame == nullptr) ++emptyCount;
        }
        EofWatch(false, L"INPUT_EOS_STATE received=%d output_next=%d watermark=%d delivery_next=%d queued=%zu incomplete=%d",
                 _nextSourceFrameNb, _nextOutputFrameNb, _maxRequestOutputFrameNb,
                 _nextDeliveryFrameNb, _outputFrames.size(), emptyCount);
    }
    RequestPendingOutputFrames();''',
    "Record queued work at EOS"
)
replace_once(
    frame_cpp,
    '''    if (deliverEos && !_isFlushing && !_isStopping) {
        _filter.m_pOutput->DeliverEndOfStream();
    }''',
    '''    if (deliverEos && !_isFlushing && !_isStopping) {
        EofWatch(false, L"EOS_SEND next_request=%d watermark=%d next_delivery=%d",
                 _nextOutputFrameNb, _maxRequestOutputFrameNb, _nextDeliveryFrameNb);
        const HRESULT eosHr = _filter.m_pOutput->DeliverEndOfStream();
        EofWatch(false, L"EOS_SEND_RESULT hr=0x%08lX", static_cast<unsigned long>(eosHr));
    }''',
    "Confirm eventual downstream EOS"
)
replace_once(
    frame_cpp,
    '''    if (f == nullptr) {
        Environment::GetInstance().Log(L"Fail to generate output frame %6d with message: %hs", n, errorMsg);
    }''',
    '''    if (f == nullptr) {
        Environment::GetInstance().Log(L"Fail to generate output frame %6d with message: %hs", n, errorMsg);
        EofWatch(true, L"CALLBACK_FAILURE frame=%d error=%hs", n,
                 errorMsg != nullptr ? errorMsg : "(null)");
    }''',
    "Catch null output frame even before EOS"
)
replace_once(
    frame_cpp,
    '''                if (prevFrameIter == frameHandler->_outputFrames.end()) {
                    return;
                } else {
                    f = AVSF_VPS_API->addFrameRef(prevFrameIter->second.frame);''',
    '''                if (prevFrameIter == frameHandler->_outputFrames.end()) {
                    EofWatch(true, L"CALLBACK_LOST_FAILED_FRAME frame=%d previous=%d already_delivered_or_missing=1",
                             n, n - 1);
                    return;
                } else {
                    if (prevFrameIter->second.frame == nullptr) {
                        EofWatch(true, L"CALLBACK_PREVIOUS_UNREADY frame=%d previous=%d", n, n - 1);
                    }
                    f = AVSF_VPS_API->addFrameRef(prevFrameIter->second.frame);''',
    "Capture unfillable callback placeholders"
)
replace_once(
    frame_cpp,
    '''        frameHandler->_deliverSampleCv.notify_all();
    }

    frameHandler->_flushOutputSampleCv.notify_all();''',
    '''        frameHandler->_deliverSampleCv.notify_all();
    }
    EofWatch(false, L"OUTPUT_CALLBACK frame=%d success=%d", n, f != nullptr);

    frameHandler->_flushOutputSampleCv.notify_all();''',
    "Trace output callbacks only after source EOS"
)
replace_once(
    frame_cpp,
    '''    while (!_isFlushing && !_isStopping) {
        int n;''',
    '''    while (!_isFlushing && !_isStopping) {
        int n;''',
    "Retain request-pump invariants"
)
replace_once(
    frame_cpp,
    '''        AVSF_VPS_API->getFrameAsync(n, MainFrameServer::GetInstance().GetScriptClip(),
                                  VpsGetFrameCallback, this);
    }
}''',
    '''        EofWatch(false, L"OUTPUT_REQUEST frame=%d", n);
        AVSF_VPS_API->getFrameAsync(n, MainFrameServer::GetInstance().GetScriptClip(),
                                  VpsGetFrameCallback, this);
    }
}''',
    "Trace new requests only after EOS"
)
replace_once(
    frame_cpp,
    '''        if (_isFlushing) {
            continue;
        }

        const VSMap *frameProps =''',
    '''        if (_isFlushing) {
            continue;
        }
        EofWatch(false, L"OUTPUT_DELIVER_BEGIN frame=%d", iter->first);

        const VSMap *frameProps =''',
    "Track delivery progress at EOF"
)
replace_once(
    frame_cpp,
    '''        GarbageCollect(sourceFrameNb - 1);
        _nextDeliveryFrameNb += 1;

        // Critical at end-of-stream:''',
    '''        GarbageCollect(sourceFrameNb - 1);
        _nextDeliveryFrameNb += 1;
        EofWatch(false, L"OUTPUT_DELIVER_DONE frame=%d source=%d next_delivery=%d",
                 _nextDeliveryFrameNb - 1, sourceFrameNb, _nextDeliveryFrameNb);

        // Critical at end-of-stream:''',
    "Track completed delivery at EOF"
)
replace_once(
    frame_cpp,
    '''            _deliverSampleCv.wait(sharedOutputLock, [this, &iter]() -> bool {
                if (_isFlushing) {
                    return true;
                }

                iter = _outputFrames.find(_nextDeliveryFrameNb);
                if (iter == _outputFrames.end()) {
                    return false;
                }

                return iter->second.frame != nullptr;
            });''',
    '''            const bool isReady = _deliverSampleCv.wait_for(
                sharedOutputLock, std::chrono::milliseconds(750), [this, &iter]() -> bool {
                    if (_isFlushing) {
                        return true;
                    }
                    iter = _outputFrames.find(_nextDeliveryFrameNb);
                    return iter != _outputFrames.end() && iter->second.frame != nullptr;
                });
            if (!isReady) {
                if (_inputEosSeen.load(std::memory_order_acquire)) {
                    int firstNull = -1, firstReady = -1;
                    for (const auto &[n, item] : _outputFrames) {
                        if (item.frame == nullptr && firstNull < 0) firstNull = n;
                        if (item.frame != nullptr && firstReady < 0) firstReady = n;
                    }
                    EofWatch(false,
                        L"EOS_WAIT_TIMEOUT next_delivery=%d next_request=%d max_request=%d map=%zu first_null=%d first_ready=%d",
                        _nextDeliveryFrameNb, _nextOutputFrameNb, _maxRequestOutputFrameNb,
                        _outputFrames.size(), firstNull, firstReady);
                }
                continue;
            }''',
    "Watchdog for stuck delivery head of line without changing output request order"
)


replace_once(
    frame_cpp,
    '''            const auto last = std::prev(_sourceFrames.end());
            VSMap *props =''',
    '''            const auto last = std::prev(_sourceFrames.end());
            EofWatch(false, L"SOURCE_TAIL oldest=%d last=%d last_pts_100ns=%lld source_total=%d",
                     _sourceFrames.begin()->first, last->first,
                     static_cast<long long>(last->second.startTime), _nextSourceFrameNb);
            VSMap *props =''',
    "Log final real source PTS when EOS arrives"
)
replace_once(
    frame_cpp,
    '''            iter = std::prev(_sourceFrames.end());
        }

        const VSMap *frameProps =''',
    '''            iter = std::prev(_sourceFrames.end());
            EofWatch(false, L"SOURCE_LOOKAHEAD requested=%d clamped_to=%d",
                     frameNb, iter->first);
        }

        const VSMap *frameProps =''',
    "Observe final-frame lookahead clamping"
)
replace_once(
    frame_cpp,
    '''    _nextOutputFrameStartTime = frameStopTime;

    Environment::GetInstance().Log''',
    '''    _nextOutputFrameStartTime = frameStopTime;
    EofWatch(false, L"OUTPUT_PTS frame=%d start_100ns=%lld stop_100ns=%lld",
             outputFrameNb, static_cast<long long>(frameStartTime),
             static_cast<long long>(frameStopTime));

    Environment::GetInstance().Log''',
    "Measure generated output PTS near EOS"
)


# Targeted follow-up to EOFWatch: distinguish a missing/blocked source frame
# from an expensive downstream FlowFPS callback. This is a DIAGNOSTIC only,
# and preserves all original wait predicates, frame ordering and interpolation.
replace_once(
    frame_cpp,
    '''auto FrameHandler::BeginFlush() -> void {
    Environment::GetInstance().Log(L"FrameHandler start BeginFlush()");''',
    '''auto FrameHandler::BeginFlush() -> void {
    EofWatch(true, L"FLUSH_BEGIN next_source=%d next_request=%d next_delivery=%d",
             _nextSourceFrameNb, _nextOutputFrameNb, _nextDeliveryFrameNb);
    Environment::GetInstance().Log(L"FrameHandler start BeginFlush()");''',
    "Identify whether last callbacks were released only during shutdown"
)
replace_once(
    frame_cpp,
    '''auto FrameHandler::GetSourceFrame(int frameNb) -> const VSFrame * {
    Environment::GetInstance().Log(L"Wait for source frame: frameNb %6d input queue size %2zd", frameNb, _sourceFrames.size());

    std::shared_lock sharedSourceLock(_sourceMutex);''',
    '''auto FrameHandler::GetSourceFrame(int frameNb) -> const VSFrame * {
    Environment::GetInstance().Log(L"Wait for source frame: frameNb %6d input queue size %2zd", frameNb, _sourceFrames.size());

    const auto sourceQueryStart = std::chrono::steady_clock::now();
    std::shared_lock sharedSourceLock(_sourceMutex);
    if (_inputEosSeen.load(std::memory_order_acquire) &&
        (frameNb >= _nextSourceFrameNb - 6 || _sourceFrames.empty())) {
        EofWatch(false, L"SOURCE_REQ frame=%d oldest=%d latest=%d eos=1 flush=%d",
                 frameNb,
                 _sourceFrames.empty() ? -1 : _sourceFrames.begin()->first,
                 _sourceFrames.empty() ? -1 : _sourceFrames.rbegin()->first,
                 static_cast<int>(_isFlushing.load()));
    }''',
    "Trace actual late source lookahead queries"
)
replace_once(
    frame_cpp,
    '''    _newSourceFrameCv.wait(sharedSourceLock, [this, &iter, frameNb]() -> bool {''',
    '''    while (!_newSourceFrameCv.wait_for(sharedSourceLock, std::chrono::milliseconds(750),
                                          [this, &iter, frameNb]() -> bool {''',
    "Log long waits for source frames required by the final output"
)
replace_once(
    frame_cpp,
    '''        return AVSF_VPS_API->mapNumElements(frameProps, FRAME_PROP_NAME_DURATION_NUM) > 0 && AVSF_VPS_API->mapNumElements(frameProps, FRAME_PROP_NAME_DURATION_DEN) > 0;
    });

    if (_isFlushing || _sourceFrames.empty()) {''',
    '''        return AVSF_VPS_API->mapNumElements(frameProps, FRAME_PROP_NAME_DURATION_NUM) > 0 && AVSF_VPS_API->mapNumElements(frameProps, FRAME_PROP_NAME_DURATION_DEN) > 0;
    })) {
        if (_inputEosSeen.load(std::memory_order_acquire)) {
            EofWatch(false, L"SOURCE_WAIT_TIMEOUT requested=%d oldest=%d latest=%d size=%zu eos=1 flushing=%d",
                frameNb, _sourceFrames.empty() ? -1 : _sourceFrames.begin()->first,
                _sourceFrames.empty() ? -1 : _sourceFrames.rbegin()->first,
                _sourceFrames.size(), static_cast<int>(_isFlushing.load()));
        }
    }
    const auto sourceWaitUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - sourceQueryStart).count();
    if (_inputEosSeen.load(std::memory_order_acquire) &&
        (sourceWaitUs > 100000 || frameNb >= _nextSourceFrameNb - 6)) {
        EofWatch(false, L"SOURCE_RESULT requested=%d resolved=%d waited_us=%lld oldest=%d latest=%d",
                 frameNb, (_isFlushing || _sourceFrames.empty()) ? -1 : iter->first,
                 static_cast<long long>(sourceWaitUs),
                 _sourceFrames.empty() ? -1 : _sourceFrames.begin()->first,
                 _sourceFrames.empty() ? -1 : _sourceFrames.rbegin()->first);
    }

    if (_isFlushing || _sourceFrames.empty()) {''',
    "Source-frame dependencies after EOF, including timeout and resolution"
)
replace_once(
    frame_cpp,
    '''    _isFlushing = true;

    _addInputSampleCv.notify_all();''',
    '''    _isFlushing = true;
    EofWatch(true, L"FLUSH_SIGNAL source=%d request=%d delivery=%d",
             _nextSourceFrameNb, _nextOutputFrameNb, _nextDeliveryFrameNb);

    _addInputSampleCv.notify_all();''',
    "Correlate flush signal and release of pending callbacks"
)


# Produce a no-diagnostics release from the EXACT EOF-fix patch that passed
# runtime testing. Only disable the EOFWatch output sink and enable flag.
# All verified EOS, CAP28, BT.709, interpolation and wait behavior is unchanged.
release_cpp = frame_cpp.read_text(encoding="utf-8")
sink_anchor = "// Disabled during normal playback. Only near EOS or on actual output error."
sink_tail = "    fflush(g_eofWatchFile);\n}"
if release_cpp.count(sink_anchor) != 1 or release_cpp.count(sink_tail) != 1:
    raise SystemExit("EOFWatch implementation changed; refusing unsafe release build")
sink_start = release_cpp.index(sink_anchor)
sink_end = release_cpp.index(sink_tail, sink_start) + len(sink_tail)
release_cpp = (
    release_cpp[:sink_start]
    + "// Clean production release: no diagnostic file output.\n"
    + "inline auto EofWatch(bool, const wchar_t*, ...) noexcept -> void {}\n"
    + release_cpp[sink_end:]
)
enable_marker = "    g_eofWatchEnabled.store(true, std::memory_order_release);\n"
if release_cpp.count(enable_marker) != 1:
    raise SystemExit("EOFWatch activation changed; refusing unsafe release build")
release_cpp = release_cpp.replace(enable_marker, "", 1)
frame_cpp.write_text(release_cpp, encoding="utf-8", newline="")
if any(token in release_cpp for token in (
    "CreateDirectoryW", "_wfopen_s", "fwprintf_s", "vfwprintf_s",
    "QueryPerformanceCounter", "g_eofWatchFile", "g_eofWatchEnabled",
    "VapourSynth_EOFWatch_"
)):
    raise SystemExit("EOFWatch file sink was not completely disabled")


# Start/seek clock trace: RAM-only bounded capture during the initial ~4.5 s
# of each play/seek epoch. A text log is written only at Flush/EndFlush, NOT
# on the timing-sensitive output path. The proven EOS/BT709/CAP28 logic stays.
def probe_replace(source: str, old: str, new: str, label: str) -> str:
    matches = source.count(old)
    if matches != 1:
        raise SystemExit(f"StartSeek {label}: expected one anchor, found {matches}")
    return source.replace(old, new, 1)

trace_cpp = frame_cpp.read_text(encoding="utf-8")
trace_cpp = probe_replace(trace_cpp,
    '''#include "frame_handler.h"

#include <cstdarg>
#include <cstdio>
#include <cwchar>''',
    '''#include "frame_handler.h"

#include <array>
#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cwchar>''',
    "Add bounded recorder headers")

trace_cpp = probe_replace(trace_cpp,
    '''// Clean production release: no diagnostic file output.
inline auto EofWatch(bool, const wchar_t*, ...) noexcept -> void {}''',
    r'''// Clean EOF foundation: legacy EOFWatch stays disabled.
inline auto EofWatch(bool, const wchar_t*, ...) noexcept -> void {}

// START/SEEK PROBE - fixed-size RAM recording with no per-frame file I/O.
// Each slot has its own release/acquire ready flag so callback threads never
// race the later flush read. Flush is called only after worker latch.
constexpr unsigned STARTSEEK_MAX_RECORDS = 6000;
struct StartSeekEvent {
    std::atomic<bool> ready{false};
    const char *tag{};
    long long tick{};
    long long pts{};
    long long clock{};
    long long aux{};
    unsigned tid{};
    int frame{};
};
std::array<StartSeekEvent, STARTSEEK_MAX_RECORDS> g_startSeekEvents{};
std::atomic<unsigned> g_startSeekPos{0};
unsigned g_startSeekEpoch = 0;

auto StartSeekProbe(const char *tag, int frame, long long pts = -1,
                    long long clock = -1, long long aux = -1) noexcept -> void {
    const unsigned at = g_startSeekPos.fetch_add(1, std::memory_order_relaxed);
    if (at >= STARTSEEK_MAX_RECORDS) return;
    LARGE_INTEGER t{};
    QueryPerformanceCounter(&t);
    auto &item = g_startSeekEvents[at];
    item.tag = tag;
    item.frame = frame;
    item.pts = pts;
    item.clock = clock;
    item.aux = aux;
    item.tick = t.QuadPart;
    item.tid = GetCurrentThreadId();
    item.ready.store(true, std::memory_order_release);
}

auto StartSeekDump() noexcept -> void {
    const unsigned n = std::min(g_startSeekPos.load(std::memory_order_acquire),
                                STARTSEEK_MAX_RECORDS);
    if (!n) return;
    if (CreateDirectoryW(L"D:\\SynthFilters", nullptr) ||
        GetLastError() == ERROR_ALREADY_EXISTS) {
        wchar_t path[MAX_PATH]{};
        swprintf_s(path, L"D:\\SynthFilters\\VapourSynth_StartSeek_%lu.txt",
                   GetCurrentProcessId());
        FILE *fp = nullptr;
        if (_wfopen_s(&fp, path, L"a") == 0 && fp != nullptr) {
            LARGE_INTEGER freq{};
            QueryPerformanceFrequency(&freq);
            fwprintf_s(fp, L"STARTSEEK_EPOCH=%u count=%u qpc_freq=%lld pid=%lu (pts/clock 100ns; aux is event-specific)\n",
                       ++g_startSeekEpoch, n, static_cast<long long>(freq.QuadPart),
                       GetCurrentProcessId());
            for (unsigned i = 0; i < n; ++i) {
                const auto &e = g_startSeekEvents[i];
                if (e.ready.load(std::memory_order_acquire)) {
                    fwprintf_s(fp, L"qpc=%lld tid=%u event=%hs frame=%d pts=%lld clock=%lld aux=%lld\n",
                               e.tick, e.tid, e.tag, e.frame,
                               e.pts, e.clock, e.aux);
                }
            }
            fclose(fp);
        }
    }
    for (unsigned i = 0; i < n; ++i) {
        g_startSeekEvents[i].ready.store(false, std::memory_order_relaxed);
    }
    g_startSeekPos.store(0, std::memory_order_release);
}''',
    "Insert RAM recorder; leave EOF production behavior intact")

trace_cpp = probe_replace(trace_cpp,
    '''    _addInputSampleCv.wait(_filter.m_csReceive, [this]() -> bool {''',
    '''    LARGE_INTEGER startSeekWaitBegin{};
    QueryPerformanceCounter(&startSeekWaitBegin);
    _addInputSampleCv.wait(_filter.m_csReceive, [this]() -> bool {''',
    "Timestamp receive-buffer wait begin")
trace_cpp = probe_replace(trace_cpp,
    '''    if (_isFlushing || _isStopping) {
        Environment::GetInstance().Log(L"Reject input sample due to flush or stop");''',
    '''    if (_nextSourceFrameNb < 420) {
        LARGE_INTEGER startSeekWaitEnd{}, startSeekFreq{};
        QueryPerformanceCounter(&startSeekWaitEnd);
        QueryPerformanceFrequency(&startSeekFreq);
        const auto waitUs = static_cast<long long>(
            (startSeekWaitEnd.QuadPart - startSeekWaitBegin.QuadPart) * 1000000LL / startSeekFreq.QuadPart);
        if (waitUs > 8000) StartSeekProbe("INPUT_WAIT_US", _nextSourceFrameNb, waitUs);
    }
    if (_isFlushing || _isStopping) {
        Environment::GetInstance().Log(L"Reject input sample due to flush or stop");''',
    "Log significant receive wait")

trace_cpp = probe_replace(trace_cpp,
    '''    {
        const std::shared_lock sharedSourceLock(_sourceMutex);

        // since the key of _sourceFrames''',
    '''    if (_nextSourceFrameNb < 115 || (_nextSourceFrameNb < 420 && _nextSourceFrameNb % 15 == 0)) {
        CRefTime startSeekStreamClock;
        const HRESULT startSeekClockHr = _filter.StreamTime(startSeekStreamClock);
        StartSeekProbe("SOURCE_INPUT", _nextSourceFrameNb, inputSampleStartTime,
                       SUCCEEDED(startSeekClockHr) ? startSeekStreamClock.GetUnits() : -1,
                       inputSampleStopTime);
    }
    {
        const std::shared_lock sharedSourceLock(_sourceMutex);

        // since the key of _sourceFrames''',
    "Sample original source PTS vs graph clock")
trace_cpp = probe_replace(trace_cpp,
    '''    } else if (_nextSourceFrameNb == Environment::GetInstance().GetInitialSrcBuffer()) {
        MainFrameServer::GetInstance().ReloadScript(_filter.m_pInput->CurrentMediaType(), true);
    }''',
    '''    } else if (_nextSourceFrameNb == Environment::GetInstance().GetInitialSrcBuffer()) {
        StartSeekProbe("MAIN_SCRIPT_START", _nextSourceFrameNb);
        MainFrameServer::GetInstance().ReloadScript(_filter.m_pInput->CurrentMediaType(), true);
        StartSeekProbe("MAIN_SCRIPT_READY", _nextSourceFrameNb);
    }''',
    "Record startup script activation interval")
trace_cpp = probe_replace(trace_cpp,
    '''        EofWatch(false, L"OUTPUT_REQUEST frame=%d", n);
        AVSF_VPS_API->getFrameAsync(n,''',
    '''        if (n < 420) StartSeekProbe("OUTPUT_REQUEST", n);
        EofWatch(false, L"OUTPUT_REQUEST frame=%d", n);
        AVSF_VPS_API->getFrameAsync(n,''',
    "Record output request lead-in")

trace_cpp = probe_replace(trace_cpp,
    '''    EofWatch(false, L"OUTPUT_CALLBACK frame=%d success=%d", n, f != nullptr);

    frameHandler->_flushOutputSampleCv.notify_all();''',
    '''    if (n < 420) StartSeekProbe("OUTPUT_READY", n, -1, -1, f != nullptr);
    EofWatch(false, L"OUTPUT_CALLBACK frame=%d success=%d", n, f != nullptr);

    frameHandler->_flushOutputSampleCv.notify_all();''',
    "Record async completion")

trace_cpp = probe_replace(trace_cpp,
    '''    _nextOutputFrameStartTime = frameStopTime;
    EofWatch(false, L"OUTPUT_PTS''',
    '''    _nextOutputFrameStartTime = frameStopTime;
    if (outputFrameNb < 420) StartSeekProbe("OUTPUT_PTS", outputFrameNb,
                                            frameStartTime, frameStopTime, sourceFrameNb);
    EofWatch(false, L"OUTPUT_PTS''',
    "Measure generated output PTS")

trace_cpp = probe_replace(trace_cpp,
    '''            _filter.m_pOutput->Deliver(outSample);
            RefreshDeliveryFrameRates(iter->first);''',
    '''            if (iter->first < 420) {
                REFERENCE_TIME ptsStart = -1, ptsStop = -1;
                outSample->GetTime(&ptsStart, &ptsStop);
                CRefTime graphStreamTime;
                const HRESULT clockHr = _filter.StreamTime(graphStreamTime);
                StartSeekProbe("DELIVER_BEGIN", iter->first, ptsStart,
                               SUCCEEDED(clockHr) ? graphStreamTime.GetUnits() : -1,
                               sourceFrameNb);
            }
            const HRESULT deliverHr = _filter.m_pOutput->Deliver(outSample);
            if (iter->first < 420) {
                CRefTime graphStreamTime;
                const HRESULT clockHr = _filter.StreamTime(graphStreamTime);
                StartSeekProbe("DELIVER_END", iter->first, -1,
                               SUCCEEDED(clockHr) ? graphStreamTime.GetUnits() : -1,
                               static_cast<long long>(deliverHr));
            }
            RefreshDeliveryFrameRates(iter->first);''',
    "Track renderer handoff and stream-clock lateness")

trace_cpp = probe_replace(trace_cpp,
    '''    _outputFrames.clear();

    ResetInput();''',
    '''    _outputFrames.clear();

    StartSeekProbe("FLUSH_CAPTURE", _nextDeliveryFrameNb, _nextSourceFrameNb,
                   _nextOutputFrameNb);
    StartSeekDump();
    ResetInput();''',
    "Dump trace at safe latched flush boundary")
frame_cpp.write_text(trace_cpp, encoding="utf-8", newline="")


# PTS-gap follow-up. Two captures showed a 0.48-0.99 second leap in
# decoded-source PTS near source frame 104, while Filter output timestamps
# remain continuously spaced at 90 Hz. Trace each upstream Receive() sample
# BEFORE ShouldSkipFrame() to separate decoder-side loss from Filter skipping.
filter_trace_cpp = filter_cpp.read_text(encoding="utf-8")
filter_trace_hdr = root / "vapoursynth_filter/src/frame_handler.h"
header_trace = filter_trace_hdr.read_text(encoding="utf-8")
frame_trace_cpp = frame_cpp.read_text(encoding="utf-8")

header_trace = probe_replace(header_trace,
    '''    auto AddInputSample(IMediaSample *inputSample) -> HRESULT;
    auto GetSourceFrame(int frameNb) -> const VSFrame *;''',
    '''    auto AddInputSample(IMediaSample *inputSample) -> HRESULT;
    auto TraceUpstreamInput(const char *tag, IMediaSample *sample) -> void;
    auto GetSourceFrame(int frameNb) -> const VSFrame *;''',
    "Public helper for read-only raw input diagnostics")
filter_trace_hdr.write_text(header_trace, encoding="utf-8", newline="")

frame_trace_cpp = probe_replace(frame_trace_cpp,
    '''std::atomic<unsigned> g_startSeekPos{0};
unsigned g_startSeekEpoch = 0;''',
    '''std::atomic<unsigned> g_startSeekPos{0};
std::atomic<int> g_startSeekRawSeen{0};
unsigned g_startSeekEpoch = 0;''',
    "Raw input capture count")
frame_trace_cpp = probe_replace(frame_trace_cpp,
    '''    g_startSeekPos.store(0, std::memory_order_release);
}''',
    '''    g_startSeekPos.store(0, std::memory_order_release);
    g_startSeekRawSeen.store(0, std::memory_order_release);
}''',
    "Reset raw input capture counter on Flush")

frame_trace_cpp = probe_replace(frame_trace_cpp,
    '''}

auto FrameHandler::AddInputSample(IMediaSample *inputSample) -> HRESULT {''',
    '''}

auto FrameHandler::TraceUpstreamInput(const char *tag, IMediaSample *sample) -> void {
    int raw = g_startSeekRawSeen.load(std::memory_order_relaxed) - 1;
    if (tag != nullptr && std::strcmp(tag, "RAW_RECEIVE") == 0)
        raw = g_startSeekRawSeen.fetch_add(1, std::memory_order_relaxed);
    if (raw < 0 || raw >= 260 || sample == nullptr) return;
    REFERENCE_TIME rawStart = -1, rawStop = -1;
    const HRESULT timestampHr = sample->GetTime(&rawStart, &rawStop);
    CRefTime currentClock;
    const HRESULT clockHr = _filter.StreamTime(currentClock);
    StartSeekProbe(tag, raw, SUCCEEDED(timestampHr) ? rawStart : -1,
                   SUCCEEDED(clockHr) ? currentClock.GetUnits() : -1,
                   _nextSourceFrameNb);
}

auto FrameHandler::AddInputSample(IMediaSample *inputSample) -> HRESULT {''',
    "Trace unmodified decoder samples before filter skip logic")

filter_trace_cpp = probe_replace(filter_trace_cpp,
    '''    if (ShouldSkipFrame(pSample)) {
        m_bSampleSkipped = TRUE;''',
    '''    frameHandler->TraceUpstreamInput("RAW_RECEIVE", pSample);
    if (ShouldSkipFrame(pSample)) {
        frameHandler->TraceUpstreamInput("FILTER_SKIP", pSample);
        m_bSampleSkipped = TRUE;''',
    "Capture source decoder PTS before any Filter skip decision")
filter_trace_cpp = probe_replace(filter_trace_cpp,
    '''    hr = frameHandler->AddInputSample(pSample);

    m_tDecodeStart''',
    '''    hr = frameHandler->AddInputSample(pSample);
    if (hr == S_FALSE) frameHandler->TraceUpstreamInput("ADDINPUT_REJECT", pSample);

    m_tDecodeStart''',
    "Capture rejected input frames")
filter_trace_cpp = probe_replace(filter_trace_cpp,
    '''    if (m_nWaitForKey && hr == S_OK) {
        DbgLog''',
    '''    if (m_nWaitForKey && hr == S_OK) {
        frameHandler->TraceUpstreamInput("KEYFRAME_SKIP", pSample);
        DbgLog''',
    "Capture keyframe wait skips")

frame_trace_cpp = probe_replace(frame_trace_cpp,
    '''    if (outputFrameNb < 420) StartSeekProbe("OUTPUT_PTS", outputFrameNb,
                                            frameStartTime, frameStopTime, sourceFrameNb);''',
    '''    if (outputFrameNb < 420) {
        StartSeekProbe("OUTPUT_PTS", outputFrameNb,
                       frameStartTime, frameStopTime, sourceFrameNb);
        int absError = peSuccess;
        const double srcAbsolute = AVSF_VPS_API->mapGetFloat(
            frameProps, FRAME_PROP_NAME_ABS_TIME, 0, &absError);
        StartSeekProbe("OUTPUT_ABS", outputFrameNb, frameStartTime,
                       absError == peSuccess ? static_cast<long long>(srcAbsolute * UNITS) : -1,
                       sourceFrameNb);
    }''',
    "Check propagated source absolute PTS per interpolated frame")

# filter.cpp is shared with AviSynth variant. Only VapourSynth has this
# tracing helper; keep the AviSynth build entirely unchanged.
for call in (
    'frameHandler->TraceUpstreamInput("RAW_RECEIVE", pSample);',
    'frameHandler->TraceUpstreamInput("FILTER_SKIP", pSample);',
    'frameHandler->TraceUpstreamInput("ADDINPUT_REJECT", pSample);',
    'frameHandler->TraceUpstreamInput("KEYFRAME_SKIP", pSample);',
):
    if filter_trace_cpp.count(call) != 1:
        raise SystemExit("Trace call missing or repeated: " + call)
    filter_trace_cpp = filter_trace_cpp.replace(
        call, "#ifdef AVSF_VAPOURSYNTH\\n    " + call +
              "\\n#endif", 1)
# The replacement above writes escaped newlines as separators; convert only
# in the added preprocessor wrappers, never touch other source literals.
filter_trace_cpp = filter_trace_cpp.replace(
    "#ifdef AVSF_VAPOURSYNTH\\n", "#ifdef AVSF_VAPOURSYNTH\n").replace(
    "\\n#endif", "\n#endif")

filter_cpp.write_text(filter_trace_cpp, encoding="utf-8", newline="")
frame_cpp.write_text(frame_trace_cpp, encoding="utf-8", newline="")


# Regression fix after detailed EOF logs confirmed the final frame 203 was
# present, but SourceGetFrame(203/204/205) waited until shutdown. VSAPI's
# mapNumElements() returns -1 when a duration key does not exist. Earlier code
# incorrectly compared ==0; therefore the terminal frame was never stamped.
# Here we use <=0 and inspect the actual result in the D:\SynthFilters log.

# Disable the built-in logfile as well, even if a stale LogFile= entry
# remains in the user's existing INI/registry settings. No settings are
# changed and no runtime logfile is opened. Calls to Environment::Log
# already return immediately when _logFile is null.
s = environment_cpp.read_text(encoding="utf-8-sig")
start = s.find('    if (!_logPath.empty()) {')
end = s.find('    Log(L"Active CPU feature:', start)
if start < 0 or end < start or s.count('    if (!_logPath.empty()) {') != 1:
    raise SystemExit("Environment::Environment logfile setup has changed; refusing to patch")
s = s[:start] + '''    // Production variant: no logfile creation, even when LogFile is configured.
    // _logFile stays null; existing call sites become no-ops.

''' + s[end:]
environment_cpp.write_text(s, encoding="utf-8", newline="")

# Final source assertions: no diagnostic code or separate timing map.
modified = {
    "filter": filter_cpp.read_text(encoding="utf-8"),
    "frame": frame_cpp.read_text(encoding="utf-8"),
    "environment": environment_cpp.read_text(encoding="utf-8"),
}
assert modified["filter"].count("IsOnlyVideoInfo2ControlFlagsChanged(") == 3
assert "_isInputMediaTypeChanged = !metadataOnlyBt709" in modified["filter"]
assert "MAX_PENDING_OUTPUT_FRAMES = 28" in modified["frame"]
assert modified["frame"].count("getFrameAsync(") == 1
assert "RequestPendingOutputFrames();" in modified["frame"]
assert "_maxRequestOutputFrameNb" in modified["frame"]
assert "SignalInputEndOfStream()" in modified["frame"]
assert "DeliverEndOfStreamIfDrained()" in modified["frame"]
assert "_inputEosSeen.load(std::memory_order_acquire)" in modified["frame"]
assert "_inputEosSeen.store(true, std::memory_order_release)" in modified["frame"]
assert "_nextSourceFrameNb < Environment::GetInstance().GetInitialSrcBuffer()" in modified["frame"]
assert "_outputRequestTimes" not in modified["frame"]
assert "_wfsopen" not in modified["environment"]
assert "EOF_DURATION_BEFORE" in modified["frame"]
assert "EOF_DURATION_AFTER" in modified["frame"]
assert 'inline auto EofWatch(bool, const wchar_t*, ...) noexcept -> void {}' in modified["frame"]
assert "CreateDirectoryW" in modified["frame"]
assert "_wfopen_s" in modified["frame"]
assert "QueryPerformanceCounter" in modified["frame"]
assert "VapourSynth_EOFWatch_" not in modified["frame"]
assert "SOURCE_WAIT_TIMEOUT" in modified["frame"]
assert "FLUSH_SIGNAL" in modified["frame"]
assert "EOS_WAIT_TIMEOUT" in modified["frame"]
assert "CALLBACK_LOST_FAILED_FRAME" in modified["frame"]
for tag in ("STARTPOINTDIAG", "RECVTRACE", "FORMAT_DIFF", "DYNAMIC_MT_FASTPATH", "DYNAMIC_MT_PRECHECK_FASTPATH", "CAP28DIAG"):
    if any(tag in x for x in modified.values()):
        raise SystemExit("Unexpected diagnostic marker remains: " + tag)

assert "RAW_RECEIVE" in modified["frame"] and "RAW_RECEIVE" in modified["filter"]
assert "FILTER_SKIP" in modified["filter"]
assert "OUTPUT_ABS" in modified["frame"]
assert "VapourSynth_StartSeek_" in modified["frame"]
assert "StartSeekDump();" in modified["frame"]
assert "DELIVER_BEGIN" in modified["frame"]
assert "SOURCE_INPUT" in modified["frame"]
assert "OUTPUT_READY" in modified["frame"]
print("StartSeek raw decoder PTS + skipped input + output absolute time trace verified; EOF fix + dual Fast Paths + CAP28 intact.")
