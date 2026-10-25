#include "webview2_probe.h"
#include <windows.h>
#include <cstdlib>
#include <iostream>
#include <string>

int wmain(int argc, wchar_t **argv) {
    using namespace valmax::launcher;
    if (argc != 3 && argc != 4) return 2;
    const auto good = probe_evergreen_runtime(argv[1]);
    const auto missing = probe_evergreen_runtime(argv[2]);
    const auto absent_loader = probe_evergreen_runtime(L"C:\\Synthetic\\Missing\\WebView2Loader.dll");
    if (good != EvergreenProbeResult::Available || missing != EvergreenProbeResult::RuntimeUnavailable
        || absent_loader != EvergreenProbeResult::LoaderUnavailable) return 3;
    if (argc == 4 && probe_evergreen_runtime(argv[3]) != EvergreenProbeResult::Available) return 4;
    std::cout << "{\"syntheticOnly\":" << (argc == 3 ? "true" : "false")
              << ",\"evergreenAvailable\":true,\"missingRuntimeRejected\":true,\"missingLoaderRejected\":true}\n";
    return 0;
}
