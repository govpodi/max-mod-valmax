#pragma once
#include <string_view>

namespace valmax::launcher {
enum class EvergreenProbeResult { Available, LoaderUnavailable, RuntimeUnavailable };
// Load only the already verified application-local WebView2Loader.dll. The
// browser itself remains the shared, system-installed Evergreen Runtime.
EvergreenProbeResult probe_evergreen_runtime(std::wstring_view loader_path) noexcept;
}
