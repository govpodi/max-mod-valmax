#include "retention_row_codec.h"

#include <array>
#include <cstdlib>
#include <cstring>
#include <string_view>
#include <type_traits>

#define CHECK(condition) do { if (!(condition)) std::abort(); } while (false)

using namespace valmax::retention::rows;
namespace {
struct Memory final {
    std::array<uint8_t,kMessageBytes> row{};
    std::array<uint8_t,64> heap{};
    bool permit_heap = true;
};
template <typename T>
void put(Memory &memory, size_t offset, T value) {
    static_assert(std::is_trivially_copyable_v<T>);
    CHECK(offset + sizeof(value) <= memory.row.size());
    std::memcpy(memory.row.data()+offset,&value,sizeof(value));
}
bool inside(uintptr_t address, size_t size, uintptr_t start, size_t capacity) noexcept {
    return address >= start && size <= capacity && address-start <= capacity-size;
}
bool read(uintptr_t address, void *output, size_t size, void *context) noexcept {
    auto &memory = *static_cast<Memory *>(context);
    const auto row = reinterpret_cast<uintptr_t>(memory.row.data());
    const auto heap = reinterpret_cast<uintptr_t>(memory.heap.data());
    if (inside(address,size,row,memory.row.size())) {
        std::memcpy(output,memory.row.data()+(address-row),size); return true;
    }
    if (memory.permit_heap && inside(address,size,heap,memory.heap.size())) {
        std::memcpy(output,memory.heap.data()+(address-heap),size); return true;
    }
    return false;
}
void route_checks() {
    const std::array<OwnerScope,2> owners{{{11,0x1234,0x5678},{12,0x9876,0x5432}}};
    Route route{};
    CHECK(unique_route(owners,0x1234,0x5678,-22,route));
    CHECK(route.owner == 11 && route.chat == -22);
    CHECK(unique_route(owners,0x9876,0x5432,-22,route));
    CHECK(route.owner == 12 && route.chat == -22);
    CHECK(!unique_route(owners,0x1234,0x5432,-22,route));
    CHECK(route.owner == 0 && route.chat == 0);
    CHECK(!unique_route(owners,0x1234,0x5678,0,route));
    auto ambiguous = owners;
    ambiguous[1] = {12,0x1234,0x5678};
    CHECK(!unique_route(ambiguous,0x1234,0x5678,-22,route));
    CHECK(route.owner == 0 && route.chat == 0);
    std::array<OwnerScope,129> too_many{};
    CHECK(!unique_route(too_many,0x1234,0x5678,-22,route));
}
void inline_text_and_removed() {
    Memory memory;
    put<int64_t>(memory,0x00,31); put<int64_t>(memory,0x80,11);
    put<int64_t>(memory,0x18,1700000000123);
    put<uint32_t>(memory,0x128,0);
    constexpr std::string_view text = "synthetic";
    std::memcpy(memory.row.data()+0x30,text.data(),text.size());
    put<uint64_t>(memory,0x40,text.size()); put<uint64_t>(memory,0x48,15);
    Snapshot snapshot;
    const auto address = reinterpret_cast<uintptr_t>(memory.row.data());
    CHECK(decode(address,&read,&memory,snapshot));
    CHECK(snapshot.kind == Kind::Text && snapshot.id == 31 && snapshot.sender == 11
        && snapshot.order_key == 1700000000123);
    CHECK(snapshot.text.bytes().size() == text.size());
    CHECK(std::memcmp(snapshot.text.bytes().data(),text.data(),text.size()) == 0);
    put<uint32_t>(memory,0x128,2);
    CHECK(decode(address,&read,&memory,snapshot));
    CHECK(snapshot.kind == Kind::Removed && snapshot.id == 31 && snapshot.order_key == 0);
    CHECK(snapshot.text.bytes().empty());
    put<uint32_t>(memory,0x128,9);
    CHECK(decode(address,&read,&memory,snapshot));
    CHECK(snapshot.kind == Kind::Ignore && snapshot.id == 0 && snapshot.order_key == 0);
}
void heap_edit_and_fail_closed() {
    Memory memory;
    put<int64_t>(memory,0x00,32); put<int64_t>(memory,0x80,12);
    put<int64_t>(memory,0x18,1700000000456);
    put<uint32_t>(memory,0x128,1);
    constexpr std::string_view text = "synthetic edited content";
    std::memcpy(memory.heap.data(),text.data(),text.size());
    put<uintptr_t>(memory,0x30,reinterpret_cast<uintptr_t>(memory.heap.data()));
    put<uint64_t>(memory,0x40,text.size()); put<uint64_t>(memory,0x48,31);
    Snapshot snapshot;
    const auto address = reinterpret_cast<uintptr_t>(memory.row.data());
    CHECK(decode(address,&read,&memory,snapshot));
    CHECK(snapshot.kind == Kind::Text && snapshot.status == 1
        && snapshot.order_key == 1700000000456);
    CHECK(snapshot.text.bytes().size() == text.size());
    memory.permit_heap = false;
    CHECK(!decode(address,&read,&memory,snapshot));
    CHECK(snapshot.kind == Kind::Ignore && snapshot.text.bytes().empty());
    memory.permit_heap = true;
    put<int64_t>(memory,0x18,0);
    CHECK(decode(address,&read,&memory,snapshot));
    CHECK(snapshot.kind == Kind::Text && snapshot.order_key == 0);
    put<int64_t>(memory,0x18,-1);
    CHECK(decode(address,&read,&memory,snapshot));
    CHECK(snapshot.kind == Kind::Text && snapshot.order_key == 0);
    put<int64_t>(memory,0x18,1700000000456);
    put<uint64_t>(memory,0x40,kMaxTextBytes+1);
    CHECK(!decode(address,&read,&memory,snapshot));
    put<uint64_t>(memory,0x40,text.size()); put<uint64_t>(memory,0x48,text.size()-1);
    CHECK(!decode(address,&read,&memory,snapshot));
    put<uint64_t>(memory,0x48,31); put<int64_t>(memory,0x00,0);
    CHECK(!decode(address,&read,&memory,snapshot));
}
}
int main() {
    route_checks(); inline_text_and_removed(); heap_edit_and_fail_closed();
}
