from pathlib import Path

CPP = Path("MPCVideoRenderer/Source/renbase2.cpp")
raw = CPP.read_bytes()
has_bom = raw.startswith(b"\xef\xbb\xbf")
newline = "\r\n" if b"\r\n" in raw else "\n"
source = raw.decode("utf-8-sig").replace("\r\n", "\n")


def replace_once(old: str, new: str, label: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise SystemExit("{}: expected exactly one anchor, found {}".format(label, count))
    source = source.replace(old, new, 1)


helper = r'''
#include <cstdio>
#include <mutex>

namespace {
std::mutex g_mpcvrScheduleDiagMutex;

void LogMpcvrScheduleDiagnostic(const wchar_t* record)
{
    WCHAR localAppData[MAX_PATH]{};
    const DWORD pathLength = GetEnvironmentVariableW(L"LOCALAPPDATA", localAppData, ARRAYSIZE(localAppData));
    if (pathLength == 0 || pathLength >= ARRAYSIZE(localAppData)) {
        return;
    }

    WCHAR logPath[MAX_PATH]{};
    if (swprintf_s(logPath, ARRAYSIZE(logPath), L"%ls\\MPCVideoRendererScheduleDiag.log", localAppData) < 0) {
        return;
    }

    const std::lock_guard<std::mutex> lock(g_mpcvrScheduleDiagMutex);
    FILE* file = nullptr;
    if (_wfopen_s(&file, logPath, L"a") != 0 || file == nullptr) {
        return;
    }
    fwprintf_s(file, L"SCHEDDIAG %ls\r\n", record);
    fclose(file);
}
} // namespace

'''

replace_once(
'''#include "stdafx.h"
#include "renbase2.h"
''',
'''#include "stdafx.h"
#include "renbase2.h"
''' + helper,
    "insert schedule diagnostic helper"
)

replace_once(
'''{
    const REFERENCE_TIME diagInputStart = *ptrStart;
    const REFERENCE_TIME diagInputStop = *ptrEnd;
    const int diagEarlinessBefore = m_trEarliness;
    const int diagWaitAverageBefore = m_trWaitAvg;
    const int diagRenderAverageBefore = m_trRenderAvg;
    const int diagFrameAverageBefore = m_trFrameAvg;

    // Don't call us unless there's a clock interface to synchronise with
    ASSERT(m_pClock);
''',
'''{
    const REFERENCE_TIME diagInputStart = *ptrStart;
    const REFERENCE_TIME diagInputStop = *ptrEnd;
    const int diagEarlinessBefore = m_trEarliness;
    const int diagWaitAverageBefore = m_trWaitAvg;
    const int diagRenderAverageBefore = m_trRenderAvg;
    const int diagFrameAverageBefore = m_trFrameAvg;
 
    // Don't call us unless there's a clock interface to synchronise with
    ASSERT(m_pClock);
''',
    "capture incoming timestamp and scheduler state"
)

replace_once(
'''    if (*ptrStart>=80000) {
        *ptrStart -= 80000;
        *ptrEnd -= 80000;       // bias stop to to retain valid frame duration
    }
    const REFERENCE_TIME diagStartAfterMonitorBias = *ptrStart;

    // Cache the time stamp now.  We will want to compare what we did with what
''',
'''    if (*ptrStart>=80000) {
        *ptrStart -= 80000;
        *ptrEnd -= 80000;       // bias stop to to retain valid frame duration
    }
    const REFERENCE_TIME diagStartAfterMonitorBias = *ptrStart;
 
    // Cache the time stamp now.  We will want to compare what we did with what
''',
    "capture monitor-biased start timestamp"
)

replace_once(
'''    REFERENCE_TIME trRealStream;     // the real time now expressed as stream time.
    m_pClock->GetTime(&trRealStream);
    const REFERENCE_TIME diagReferenceClockNow = trRealStream;
    const REFERENCE_TIME diagRunStartTime = m_tStart;

    trRealStream -= m_tStart;     // convert to stream time (this is a reftime)
    const REFERENCE_TIME diagStreamClockNow = trRealStream;
''',
'''    REFERENCE_TIME trRealStream;     // the real time now expressed as stream time.
    m_pClock->GetTime(&trRealStream);
    const REFERENCE_TIME diagReferenceClockNow = trRealStream;
    const REFERENCE_TIME diagRunStartTime = m_tStart;
 
    trRealStream -= m_tStart;     // convert to stream time (this is a reftime)
    const REFERENCE_TIME diagStreamClockNow = trRealStream;
''',
    "capture reference clock and run start time"
)

# The scheduler return branch exposes both the requested sample time and the
# decision (immediate vs advise-time). Log only unusual timing decisions.
replace_once(
'''        return Result;
    }

    // We are going to drop this frame!
''',
'''        const REFERENCE_TIME diagScheduledAhead = *ptrStart - diagStreamClockNow;
        const bool diagLargeEarlyDecision = trTrueLate <= -2000000; // >=200 ms early
        const bool diagLargeLateDecision = trTrueLate >= 2000000;   // >=200 ms late
        const bool diagLongScheduledWait = (Result == S_FALSE && diagScheduledAhead >= 200000);
        if (diagLongScheduledWait || diagLargeEarlyDecision || diagLargeLateDecision) {
            wchar_t record[1400]{};
            swprintf_s(record, ARRAYSIZE(record),
                L"input_start_100ns %lld input_stop_100ns %lld biased_start_100ns %lld scheduled_start_100ns %lld clock_now_100ns %lld run_start_100ns %lld stream_clock_now_100ns %lld scheduled_ahead_100ns %lld tr_true_late_100ns %d duration_100ns %d earliness_before_100ns %d earliness_after_100ns %d wait_avg_before_100ns %d wait_avg_after_100ns %d render_avg_before_100ns %d render_avg_after_100ns %d frame_avg_before_100ns %d frame_avg_after_100ns %d target_100ns %d n_normal %d just_dropped %d play_asap %d supplier_handles_quality %d result %ld",
                static_cast<long long>(diagInputStart), static_cast<long long>(diagInputStop),
                static_cast<long long>(diagStartAfterMonitorBias), static_cast<long long>(*ptrStart),
                static_cast<long long>(diagReferenceClockNow), static_cast<long long>(diagRunStartTime),
                static_cast<long long>(diagStreamClockNow), static_cast<long long>(diagScheduledAhead),
                trTrueLate, trDuration, diagEarlinessBefore, m_trEarliness,
                diagWaitAverageBefore, m_trWaitAvg, diagRenderAverageBefore, m_trRenderAvg,
                diagFrameAverageBefore, m_trFrameAvg, m_trTarget, m_nNormal,
                bJustDroppedFrame ? 1 : 0, bPlayASAP ? 1 : 0,
                m_bSupplierHandlingQuality ? 1 : 0, static_cast<long>(Result));
            LogMpcvrScheduleDiagnostic(record);
        }
        return Result;
    }

    // We are going to drop this frame!
''',
    "record scheduling decision before returning"
)

# Also log a frame that the renderer explicitly chooses to drop when its
# lateness is large, so preceding and following scheduling decisions can be compared.
replace_once(
'''    m_nNormal = -1;
    return E_FAIL;                         // drop it
''',
'''    m_nNormal = -1;
    if (trTrueLate >= 2000000 || trTrueLate <= -2000000) {
        wchar_t record[1400]{};
        swprintf_s(record, ARRAYSIZE(record),
            L"input_start_100ns %lld input_stop_100ns %lld biased_start_100ns %lld scheduled_start_100ns %lld clock_now_100ns %lld run_start_100ns %lld stream_clock_now_100ns %lld scheduled_ahead_100ns %lld tr_true_late_100ns %d duration_100ns %d earliness_before_100ns %d earliness_after_100ns %d wait_avg_before_100ns %d wait_avg_after_100ns %d render_avg_before_100ns %d render_avg_after_100ns %d frame_avg_before_100ns %d frame_avg_after_100ns %d target_100ns %d n_normal %d just_dropped 1 play_asap -1 supplier_handles_quality %d result -1",
            static_cast<long long>(diagInputStart), static_cast<long long>(diagInputStop),
            static_cast<long long>(diagStartAfterMonitorBias), static_cast<long long>(*ptrStart),
            static_cast<long long>(diagReferenceClockNow), static_cast<long long>(diagRunStartTime),
            static_cast<long long>(diagStreamClockNow),
            static_cast<long long>(*ptrStart - diagStreamClockNow),
            trTrueLate, trDuration, diagEarlinessBefore, m_trEarliness,
            diagWaitAverageBefore, m_trWaitAvg, diagRenderAverageBefore, m_trRenderAvg,
            diagFrameAverageBefore, m_trFrameAvg, m_trTarget, m_nNormal,
            m_bSupplierHandlingQuality ? 1 : 0);
        LogMpcvrScheduleDiagnostic(record);
    }
    return E_FAIL;                         // drop it
''',
    "record outlier frame drops"
)

encoded = source.replace("\n", newline).encode("utf-8")
if has_bom:
    encoded = b"\xef\xbb\xbf" + encoded
CPP.write_bytes(encoded)
print("Added SCHEDDIAG records for long scheduled waits and large early/late/drop decisions.")
