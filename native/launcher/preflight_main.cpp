#include "preflight.h"
#include "preflight_pins.generated.h"
#include <cstdio>
#include <string_view>
int wmain(int argc,wchar_t **argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    if(argc!=4 || std::wstring_view(argv[1])!=L"--check-only" || std::wstring_view(argv[2])!=L"--runtime") {
        std::puts("{\"schema\":1,\"verified\":false,\"code\":\"invalid_command\",\"files\":0,\"launchAllowed\":false}");return 2;
    }
    valmax::launcher::VerifiedPayload payload;
    const auto error=valmax::launcher::verify_payload(argv[3],valmax::launcher::pins::kFiles,payload);
    std::printf("{\"schema\":1,\"verified\":%s,\"code\":\"%s\",\"files\":%u,\"manifestSha256\":\"%s\",\"launchAllowed\":false}\n",
        payload.verified()?"true":"false",valmax::launcher::preflight_code(error),payload.file_count(),valmax::launcher::pins::kManifestSha256);
    return error==valmax::launcher::PreflightError::Ok?0:1;
}
