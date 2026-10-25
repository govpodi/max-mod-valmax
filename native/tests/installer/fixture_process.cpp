#include <windows.h>
// Owned installer fixture only. Never loads MAX, a profile, Qt or the network.
int main() { Sleep(60000); return 0; }
