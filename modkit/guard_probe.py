"""Harmless probe for the smoke-test guard, using only a synthetic file/target."""
import ctypes
import json
import os
from pathlib import Path
import socket
import sys
import time

result = {}
try:
    Path(sys.argv[1]).read_text(encoding="utf-8")
    result["host_sentinel_denied"] = False
except PermissionError:
    result["host_sentinel_denied"] = True
credential = ctypes.c_void_p()
advapi = ctypes.WinDLL("advapi32", use_last_error=True)
ok = advapi.CredReadW("CodexMaxModGuardProbeNonexistent", 1, 0, ctypes.byref(credential))
result["credential_read_denied"] = not ok and not credential.value
result["credential_error"] = ctypes.get_last_error()
try:
    socket.getaddrinfo("invalid.invalid", 443)
    result["dns_denied"] = False
except socket.gaierror as error:
    result["dns_denied"] = error.errno == 11001
try:
    with socket.socket() as connection:
        connection.settimeout(0.1)
        connection.connect(("127.0.0.1", 1))
    result["socket_denied"] = False
except OSError as error:
    result["socket_denied"] = error.winerror == 10013
probe = Path(os.environ["TEMP"]) / "probe.json"
probe.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result), flush=True)
time.sleep(8)
