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
    '''    {
        // Register newly available output frames, even when CAP28 is full.
        const std::unique_lock uniqueOutputLock(_outputMutex);
        _maxRequestOutputFrameNb = std::max(_maxRequestOutputFrameNb, maxRequestOutputFrameNb);
    }
    RequestPendingOutputFrames();''',
    '''    while (_nextOutputFrameNb <= maxRequestOutputFrameNb) {
        // Limit simultaneously outstanding output frames to the validated CAP28.
        // Preserve async request and frame delivery ordering.
        // Save the highest frame number now eligible to be requested.
        // The worker replenishes the bounded queue as completed frames leave.
        {
            const std::unique_lock uniqueOutputLock(_outputMutex);
            _maxRequestOutputFrameNb = std::max(_maxRequestOutputFrameNb, maxRequestOutputFrameNb);
        }
        RequestPendingOutputFrames();
        break;
    }''',
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
assert "_outputRequestTimes" not in modified["frame"]
assert "_wfsopen" not in modified["environment"]
for tag in ("STARTPOINTDIAG", "RECVTRACE", "FORMAT_DIFF", "DYNAMIC_MT_FASTPATH", "DYNAMIC_MT_PRECHECK_FASTPATH", "CAP28DIAG"):
    if any(tag in x for x in modified.values()):
        raise SystemExit("Unexpected diagnostic marker remains: " + tag)

print("Release patch complete: dual BT709 Fast Paths + CAP28 auto-refill after output delivery; no runtime logs.")
