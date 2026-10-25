#include "retention_wire_frame.h"

#include <array>
#include <cstdlib>
#include <iostream>
#include <vector>

namespace {

int checks = 0;
void require(bool value, const char *label) {
    if (!value) { std::cerr << "FAIL " << label << '\n'; std::exit(1); }
    ++checks;
}

std::vector<std::uint8_t> frame(std::uint8_t cmd, std::uint16_t seq,
    std::uint16_t opcode, std::uint8_t cof, std::span<const std::uint8_t> body) {
    std::vector<std::uint8_t> bytes{
        11, cmd, static_cast<std::uint8_t>(seq >> 8), static_cast<std::uint8_t>(seq),
        static_cast<std::uint8_t>(opcode >> 8), static_cast<std::uint8_t>(opcode), cof,
        static_cast<std::uint8_t>(body.size() >> 16),
        static_cast<std::uint8_t>(body.size() >> 8),
        static_cast<std::uint8_t>(body.size())};
    bytes.insert(bytes.end(), body.begin(), body.end());
    return bytes;
}

} // namespace

int main() {
    using namespace valmax::retention::wire;
    FrameView view{};
    const std::array<std::uint8_t, 3> opaque{0xff, 0x00, 0x81};
    const auto response = frame(1, 0x1234, 49, 2, opaque);
    require(parse_frame(std::span(response).first(9), view) == ParseResult::incomplete
        && view.consumed == 0 && view.body.empty(), "partial header");
    require(parse_frame(std::span(response).first(11), view) == ParseResult::incomplete
        && view.consumed == 0 && view.body.empty(), "partial body");
    require(parse_frame(response, view) == ParseResult::complete
        && view.version == 11 && view.command == 1 && view.sequence == 0x1234
        && view.opcode == 49 && view.compression_factor == 2,
        "api2 response envelope");
    require(view.body.size() == opaque.size() && view.body[0] == 0xff
        && view.consumed == response.size(), "opaque borrowed body");

    const auto notification = frame(0, 5, 128, 0, opaque);
    auto combined = response;
    combined.insert(combined.end(), notification.begin(), notification.end());
    require(parse_frame(combined, view) == ParseResult::complete
        && view.opcode == 49 && view.consumed == response.size(), "first coalesced frame");
    require(parse_frame(std::span(combined).subspan(view.consumed), view)
        == ParseResult::complete && view.command == 0 && view.opcode == 128,
        "second coalesced frame");

    const auto deletion_candidate = frame(0, 6, 142, 0, opaque);
    require(parse_frame(deletion_candidate, view) == ParseResult::complete
        && view.opcode == 142 && view.body.size() == 3,
        "uninterpreted deletion candidate");
    const auto empty_ping = frame(1, 7, 1, 0, {});
    require(parse_frame(empty_ping, view) == ParseResult::complete
        && view.body.empty() && view.consumed == header_bytes, "empty response");

    auto wrong_version = response;
    wrong_version[0] = 10;
    require(parse_frame(wrong_version, view) == ParseResult::invalid
        && view.consumed == 0 && view.body.empty(), "version gate");
    auto wrong_command = response;
    wrong_command[1] = 2;
    require(parse_frame(wrong_command, view) == ParseResult::invalid
        && view.consumed == 0, "command gate");
    auto oversized = response;
    oversized[7] = 0x20; oversized[8] = 0x00; oversized[9] = 0x01;
    require(parse_frame(oversized, view) == ParseResult::invalid
        && view.consumed == 0, "body bound before buffering");

    std::cout << "{\"schema\":1,\"checks\":" << checks
        << ",\"passed\":true,\"synthetic\":true,"
        << "\"payloadDecoded\":false,\"retentionEnabled\":false}\n";
}
