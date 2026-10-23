// Generic conservative policy for DirectShow VIDEOINFOHEADER2::dwControlFlags.
// This header deliberately has no Windows/DirectShow dependencies so it can
// be independently tested. Only safe no-op flag changes are skipped. A real
// color-space, range, primaries, transfer, aspect-ratio or layout transition
// continues through normal graph/media type negotiation. License: MIT.
#pragma once
#include <cstdint>

namespace SynthFilter::SafeVideoInfo2Flags {
inline constexpr std::uint32_t kAmcontrolUsed = 0x00000001u;
inline constexpr std::uint32_t kObservedBt709 = 0x2880A581u;

[[nodiscard]] constexpr bool CanBypassReconfigure(
    std::uint32_t previous, std::uint32_t incoming) noexcept {
    if (previous == incoming) {
        return false; // No dynamic metadata update to optimize.
    }

    // Preserve the exact SDR BT.709 transition already verified during
    // PotPlayer/LAV testing. This is NOT a rule for arbitrary new colorimetry.
    if (previous == 0u && incoming == kObservedBt709) {
        return true;
    }

    // DirectShow requires ignoring every other dwControlFlags bit when
    // AMCONTROL_USED is clear in BOTH media types. In that case an update is
    // semantically inert, irrespective of the nominal 24-bit color payload.
    if ((previous & kAmcontrolUsed) == 0u &&
        (incoming & kAmcontrolUsed) == 0u) {
        return true;
    }

    // An empty control-flags field and AMCONTROL_USED alone have identical
    // meaning (no padding flags, no color info). Only this bit may change.
    if ((previous ^ incoming) == kAmcontrolUsed &&
        (previous & ~kAmcontrolUsed) == 0u &&
        (incoming & ~kAmcontrolUsed) == 0u) {
        return true;
    }

    // Genuine colorimetry/format transitions, or any unknown transition,
    // deliberately take the fully supported standard media-type path.
    return false;
}

// Compile-time regression matrix. These are checked by the real x64 build.
static_assert(CanBypassReconfigure(0u, kObservedBt709));
static_assert(CanBypassReconfigure(0u, 0x00000100u)); // AMCONTROL_USED clear
static_assert(CanBypassReconfigure(0x00000100u, 0x00000200u));
static_assert(CanBypassReconfigure(0u, 1u));
static_assert(CanBypassReconfigure(1u, 0u));
static_assert(!CanBypassReconfigure(0u, 0u));
static_assert(!CanBypassReconfigure(kObservedBt709, kObservedBt709));
static_assert(!CanBypassReconfigure(kObservedBt709, 0u));
static_assert(!CanBypassReconfigure(0u, 0x44801581u)); // alternative color info
static_assert(!CanBypassReconfigure(kObservedBt709, 0x44801581u));
static_assert(!CanBypassReconfigure(0x00000003u, 0x00000005u)); // padding
static_assert(!CanBypassReconfigure(kObservedBt709, kObservedBt709 & ~1u));
} // namespace SynthFilter::SafeVideoInfo2Flags
