#pragma once

#include <cstddef>
#include <cstdint>
#include <span>

namespace valmax::retention::wire {

// The api2 v11 envelope is independently observed. The body is opaque here:
// no opcode or frame is evidence of a received or deleted message by itself.
inline constexpr std::size_t header_bytes = 10;
inline constexpr std::size_t maximum_body_bytes = 2 * 1024 * 1024;

enum class ParseResult : std::uint8_t { incomplete, complete, invalid };

struct FrameView {
    std::uint8_t version = 0;
    std::uint8_t command = 0;
    std::uint16_t sequence = 0;
    std::uint16_t opcode = 0;
    std::uint8_t compression_factor = 0;
    std::span<const std::uint8_t> body{};
    std::size_t consumed = 0;
};

// Parses one frame from the beginning of a caller-owned buffer. A complete
// view borrows body bytes only until that buffer changes. An incomplete or
// invalid frame yields an empty view; no content is copied, decoded or logged.
[[nodiscard]] inline ParseResult parse_frame(
    std::span<const std::uint8_t> input, FrameView &view) noexcept {
    view = {};
    if (input.size() < header_bytes) return ParseResult::incomplete;
    if (input[0] != 11 || (input[1] != 0 && input[1] != 1 && input[1] != 3)) {
        return ParseResult::invalid;
    }
    const std::size_t length =
        (static_cast<std::size_t>(input[7]) << 16) |
        (static_cast<std::size_t>(input[8]) << 8) |
        static_cast<std::size_t>(input[9]);
    if (length > maximum_body_bytes) return ParseResult::invalid;
    const std::size_t total = header_bytes + length;
    if (input.size() < total) return ParseResult::incomplete;

    view.version = input[0];
    view.command = input[1];
    view.sequence = static_cast<std::uint16_t>((input[2] << 8) | input[3]);
    view.opcode = static_cast<std::uint16_t>((input[4] << 8) | input[5]);
    view.compression_factor = input[6];
    view.body = input.subspan(header_bytes, length);
    view.consumed = total;
    return ParseResult::complete;
}

} // namespace valmax::retention::wire
