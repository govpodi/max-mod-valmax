#include <windows.h>
#include <shellapi.h>
#include <fstream>
#include <string>
int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    int argc = 0; auto** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    const bool valid = argv && argc == 2 && std::wstring(argv[1]) == L"mdata";
    if (argv) LocalFree(argv);
    if (!valid) return 17;
    std::ofstream marker("called.txt", std::ios::binary); marker << "mdata"; marker.close();
    std::ifstream input("mode.txt"); std::string mode; input >> mode;
    if (mode == "timeout") Sleep(250);
    return mode == "failure" ? 13 : 0;
}
