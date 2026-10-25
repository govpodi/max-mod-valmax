#include "notification_internal.h"
#include <bcrypt.h>
#include <algorithm>
#include <cstring>
#include <vector>
namespace valmax::notifications {
void OwnerMap::remember(uint64_t native, int64_t owner) {
    if (owner <= 0) return;
    Entry *empty = nullptr;
    for (auto &entry : entries) {
        if (entry.used && entry.native == native) {
            if (entry.owner != owner) entry.ambiguous = true;
            return;
        }
        if (!entry.used && !empty) empty = &entry;
    }
    if (empty) *empty = {native, owner, true, false};
    else overflow = true;
}
int64_t OwnerMap::resolve(uint64_t native) const {
    for (const auto &entry : entries)
        if (entry.used && entry.native == native) return entry.ambiguous ? 0 : entry.owner;
    return 0;
}
bool revision(const ValmaxNotificationScope *scopes, size_t count, char (&output)[64]) {
    if (count > kScopes || (count && !scopes)) return false;
    std::string json = "{\"protected\":[";
    std::pair<std::string, std::string> previous;
    for (size_t i = 0; i < count; ++i) {
        if (scopes[i].owner <= 0) return false;
        std::pair key{std::to_string(scopes[i].owner), std::to_string(scopes[i].chat)};
        if (i && key <= previous) return false;
        if (i) json += ',';
        json += "{\"chatId\":\"" + key.second + "\",\"ownerId\":\"" + key.first + "\"}";
        previous = std::move(key);
    }
    json += "],\"ready\":true,\"schema\":1}";
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return false;
    std::array<uint8_t, 32> digest{};
    bool ok = BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0;
    if (ok) ok = BCryptHashData(hash, reinterpret_cast<PUCHAR>(json.data()), static_cast<ULONG>(json.size()), 0) >= 0
        && BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) >= 0;
    if (hash) BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    constexpr char hex[] = "0123456789abcdef";
    if (ok) for (size_t i = 0; i < digest.size(); ++i) { output[2*i] = hex[digest[i] >> 4]; output[2*i+1] = hex[digest[i] & 15]; }
    return ok;
}
uint32_t PolicyState::update(const ValmaxNotificationPolicy &input, const ValmaxNotificationScope *items, uint64_t now) {
    fresh = false; // Invalid updates revoke ACK, without forgetting known PINs.
    if (input.size != sizeof(input) || input.version != 1 || input.reserved || input.ready > 1
        || input.count > kScopes || (input.count && !items) || !input.generation || input.generation <= generation
        || !input.lease_ms || input.lease_ms > 5000 || now > UINT64_MAX - input.lease_ms) return VALMAX_NOTIFICATION_INVALID_POLICY;
    if (!input.ready) return VALMAX_NOTIFICATION_CATALOG_UNAVAILABLE;
    char digest[64];
    if (!revision(items, input.count, digest) || std::memcmp(digest, input.revision, sizeof(digest))) return VALMAX_NOTIFICATION_INVALID_POLICY;
    std::copy_n(items, input.count, scopes.begin());
    policy = input; generation = input.generation; known = true; fresh = true; deadline = now + input.lease_ms;
    return VALMAX_NOTIFICATION_OK;
}
Decision PolicyState::select(bool data_valid, uint64_t native, int64_t chat) const {
    if (!known || !data_valid) return Decision::Suppress;
    const auto owner = owners.resolve(native);
    if (!owner && policy.count) return Decision::Suppress;
    for (size_t i = 0; i < policy.count; ++i) {
        if (scopes[i].chat != chat) continue;
        if (owner && scopes[i].owner == owner) return Decision::Mask;
    }
    // No global fallback: expiry preserves exact PIN scopes. A collision or an
    // unknown identity with any protected scopes is withheld, never assigned
    // to the wrong owner. Verified ordinary chats keep their original content.
    return Decision::Pass;
}
}
