#include "retention_store_api.h"
#include "retention_queue.h"
#include <windows.h>
#include <algorithm>
#include <array>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <string>

namespace {
int checks = 0;
void require(bool value, const char *label) {
    if (!value) { std::cerr << "FAIL " << label << '\n'; std::exit(1); }
    ++checks;
}
}
int wmain(int argc, wchar_t **argv) {
    require(argc == 2, "test profile argument");
    const std::filesystem::path profile(argv[1]);
    require(std::filesystem::create_directories(profile), "new synthetic profile");
    const std::u16string path = profile.u16string();
    const std::string text = "synthetic interop message";
    require(valmax_core_retention_capture_text(
        reinterpret_cast<const uint16_t *>(path.data()), path.size(),
        11, -22, 31, 1234, reinterpret_cast<const uint8_t *>(text.data()), text.size()) == 0,
        "capture text into durable native store");
    std::array<int64_t, 2> ids{31, 32};
    ValmaxRetentionRequest request{};
    request.size = sizeof(request);
    request.version = 1;
    request.kind = VALMAX_RETENTION_RECEIVED;
    request.count = static_cast<uint32_t>(ids.size());
    request.nonce = request.epoch = request.generation = 1;
    request.owner = 11;
    request.chat = -22;
    require(valmax::retention::digest_ids(ids, request.ids_digest), "C++ queue digest");
    ValmaxRetentionAck ack{};
    require(valmax_core_retention_store_process(
        reinterpret_cast<const uint16_t *>(path.data()), path.size(),
        &request, ids.data(), &ack) == 0, "received C ABI call");
    require(ack.size == sizeof(ack) && ack.result == VALMAX_RETENTION_DURABLE
        && ack.retained[0] == 0, "received ACK has no deletion bits");
    request.kind = VALMAX_RETENTION_PREPARE_DELETE;
    request.nonce = 2;
    request.deadline_tick = GetTickCount64() + 1000;
    require(valmax_core_retention_store_process(
        reinterpret_cast<const uint16_t *>(path.data()), path.size(),
        &request, ids.data(), &ack) == 0, "delete C ABI call");
    require(ack.result == VALMAX_RETENTION_DURABLE && ack.retained[0] == 1
        && std::all_of(std::begin(ack.retained) + 1, std::end(ack.retained),
            [](uint8_t value) { return value == 0; }), "only archived message retained");
    std::cout << "{\"schema\":1,\"checks\":" << checks
        << ",\"passed\":true,\"synthetic\":true,\"actualHooksInstalled\":false}\n";
    return 0;
}
