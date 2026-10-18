from pathlib import Path
import sys

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "upstream")


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit("{}: expected exactly one match, found {}".format(label, count))
    return source.replace(old, new, 1)


def replace_between(source: str, start: str, end: str, replacement: str, label: str) -> str:
    count = source.count(start)
    if count != 1:
        raise SystemExit("{}: expected one start marker, found {}".format(label, count))
    start_index = source.find(start)
    end_index = source.find(end, start_index + len(start))
    if end_index < 0:
        raise SystemExit("{}: end marker not found after start marker".format(label))
    return source[:start_index] + replacement + source[end_index:]


header = ROOT / "vapoursynth_filter/src/frame_handler.h"
h = header.read_text(encoding="utf-8")
h = replace_once(
    h,
    "    std::map<int, AutoReleaseVSFrame> _outputFrames;\n",
    "    std::map<int, AutoReleaseVSFrame> _outputFrames;\n"
    "    // Diagnostic timestamps; accessed while holding _outputMutex.\n"
    "    std::map<int, std::chrono::steady_clock::time_point> _outputRequestTimes;\n"
    "    std::map<int, std::chrono::steady_clock::time_point> _outputReadyTimes;\n",
    "header timestamp maps",
)
header.write_text(h, encoding="utf-8")

cpp = ROOT / "vapoursynth_filter/src/frame_handler.cpp"
s = cpp.read_text(encoding="utf-8")

s = replace_once(
    s,
    "            _outputFrames.emplace(_nextOutputFrameNb, nullptr);\n",
    "            _outputFrames.emplace(_nextOutputFrameNb, nullptr);\n"
    "            _outputRequestTimes[_nextOutputFrameNb] = std::chrono::steady_clock::now();\n",
    "record output request time",
)

s = replace_once(
    s,
    "            frameHandler->_outputFrames.erase(n);\n",
    "            frameHandler->_outputFrames.erase(n);\n"
    "            frameHandler->_outputRequestTimes.erase(n);\n"
    "            frameHandler->_outputReadyTimes.erase(n);\n",
    "clear timestamps during flush callback",
)

s = replace_once(
    s,
    "            frameHandler->_outputFrames[n] = const_cast<VSFrame *>(f);\n",
    "            frameHandler->_outputFrames[n] = const_cast<VSFrame *>(f);\n"
    "            frameHandler->_outputReadyTimes[n] = std::chrono::steady_clock::now();\n",
    "record output-ready time",
)

s = replace_once(
    s,
    "    _outputFrames.clear();\n",
    "    _outputFrames.clear();\n"
    "    _outputRequestTimes.clear();\n"
    "    _outputReadyTimes.clear();\n",
    "clear timestamp maps at flush",
)

s = replace_once(
    s,
    "auto FrameHandler::WorkerProc() -> void {\n    const auto ResetOutput = [this]() -> void {",
    "auto FrameHandler::WorkerProc() -> void {\n"
    "    std::optional<std::chrono::steady_clock::time_point> lastDeliveryStartTime;\n"
    "    const auto ResetOutput = [this, &lastDeliveryStartTime]() -> void {",
    "add delivery interval tracker",
)

s = replace_once(
    s,
    "        _frameRateCheckpointDeliveryFrameNb = 0;\n        _currentDeliveryFrameRate = 0;\n    };",
    "        _frameRateCheckpointDeliveryFrameNb = 0;\n"
    "        _currentDeliveryFrameRate = 0;\n"
    "        lastDeliveryStartTime.reset();\n"
    "    };",
    "reset delivery interval tracker",
)

wait_start = "        decltype(_outputFrames)::iterator iter;"
wait_end = "\n\n        if (_isFlushing) {\n            continue;\n        }"
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
s = replace_between(s, wait_start, wait_end, wait_block, "collect worker wake timing")

delivery_start = "        if (ATL::CComPtr<IMediaSample> outSample; PrepareOutputSample("
delivery_end = "        GarbageCollect(sourceFrameNb - 1);"
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

            _filter.m_pOutput->Deliver(outSample);
            const auto deliveryEndTime = std::chrono::steady_clock::now();
            const long long downstreamDeliverUs = std::chrono::duration_cast<std::chrono::microseconds>(
                deliveryEndTime - deliveryStartTime).count();

            RefreshDeliveryFrameRates(iter->first);

            // Reuse the existing per-frame delivery log slot to avoid extra log writes.
            Environment::GetInstance().Log(
                L"VPDIAG frame %6d req_to_worker_us %lld req_to_ready_us %lld ready_to_worker_us %lld pending %zu ready_pending %zu prepare_us %lld downstream_deliver_us %lld deliver_call_gap_us %lld",
                iter->first, requestToWorkerUs, requestToReadyUs, readyToWorkerUs,
                queueDepthAtWake, readyBacklogAtWake, prepareUs, downstreamDeliverUs, deliveryCallGapUs);
        } else {
            const long long prepareUs = std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now() - prepareStartTime).count();
            Environment::GetInstance().Log(
                L"VPDIAG PREPARE_FAILED frame %6d req_to_worker_us %lld req_to_ready_us %lld ready_to_worker_us %lld pending %zu ready_pending %zu prepare_us %lld",
                iter->first, requestToWorkerUs, requestToReadyUs, readyToWorkerUs,
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
s = replace_between(s, delivery_start, delivery_end, delivery_block, "record prepare and downstream delivery timings")

cpp.write_text(s, encoding="utf-8")
print("Applied frame-pacing instrumentation only; no queue limit was added.")
