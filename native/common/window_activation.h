#pragma once
#include <cstdint>

namespace valmax::activation {
// Both the launcher and the in-process Qt receiver register this same
// session-scoped Windows message. It carries no profile or message data.
inline constexpr wchar_t kMessageName[] = L"ValMAX.Native.MainWindow.Activate.v1.{BC5079B2-069A-41C2-B084-B9C2A66592D8}";
inline constexpr uintptr_t kActivate = 1;
inline constexpr uintptr_t kQueryVisible = 2;
inline constexpr uintptr_t kAck = 0x564D4158;
}
