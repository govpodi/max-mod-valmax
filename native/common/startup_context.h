#pragma once
#include <cstdint>

namespace valmax::startup {
enum class PackagePresence:uint32_t {Absent,Present,Unknown};
enum class ContainerPresence:uint32_t {Absent,Present,Unknown};
// Desktop means only unpackaged + non-AppContainer primary-process metadata.
// It does not establish Explorer identity, logon/session parity or no VFS.
enum class Context:uint32_t {Desktop,Packaged,AppContainer,Unknown};
struct Observation {
    PackagePresence family=PackagePresence::Unknown,full_name=PackagePresence::Unknown;
    ContainerPresence container=ContainerPresence::Unknown;
};
// Pure observation seams: no names, SIDs, filesystem, credentials or spawning.
PackagePresence package_presence(int32_t status,uint32_t required_characters) noexcept;
ContainerPresence container_presence(bool token_opened,bool information_read,
    uint32_t returned_bytes,uint32_t is_container) noexcept;
Context classify(const Observation&) noexcept;
// Queries only our own package-presence metadata and TOKEN_QUERY metadata.
Observation observe_current() noexcept;
const wchar_t* refusal_message() noexcept;
// Neither this guard nor KnownFolder flags prove that an unpackaged descendant
// cannot inherit a filesystem VFS. Full ordinary-desktop reentry remains open.
}
