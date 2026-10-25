"""Pin the Microsoft-signed Evergreen online bootstrapper before packaging.

The small EXE is kept outside Git and outside the installed ValMAX payload.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess


PIN = Path(__file__).resolve().parents[1] / "installer/webview2-evergreen-bootstrapper.json"


def load_pin() -> dict:
    pin = json.loads(PIN.read_text(encoding="utf8"))
    if (set(pin) != {"schema", "name", "source", "size", "sha256",
                     "signerSubject", "signerThumbprint"}
            or pin["schema"] != 1
            or pin["name"] != "MicrosoftEdgeWebview2Setup.exe"
            or pin["source"] != "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
            or type(pin["size"]) is not int or not 0 < pin["size"] < 4 << 20
            or not re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])
            or not pin["signerSubject"].startswith("CN=Microsoft Corporation,")
            or not re.fullmatch(r"[0-9A-F]{40}", pin["signerThumbprint"])):
        raise ValueError("invalid pinned Microsoft WebView2 bootstrapper metadata")
    return pin


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ordinary_file(path: Path) -> os.stat_result:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode)
            or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or info.st_nlink != 1):
        raise ValueError("WebView2 bootstrapper must be an ordinary, single-linked file")
    return info


def authenticode(path: Path) -> dict:
    script = ("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
              "$OutputEncoding = [Console]::OutputEncoding; "
              "$ErrorActionPreference = 'Stop'; "
              "$sig = Get-AuthenticodeSignature -LiteralPath $env:VALMAX_WEBVIEW2_BOOTSTRAPPER; "
              "if ($null -eq $sig.SignerCertificate) { throw 'No signer certificate' }; "
              "[pscustomobject]@{ status = $sig.Status.ToString(); "
              "subject = $sig.SignerCertificate.Subject; "
              "thumbprint = $sig.SignerCertificate.Thumbprint } | ConvertTo-Json -Compress")
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    env = os.environ.copy()
    # Do not inherit third-party/user module paths from build shells.
    env["PSModulePath"] = str(system_root / "System32/WindowsPowerShell/v1.0/Modules")
    env["VALMAX_WEBVIEW2_BOOTSTRAPPER"] = str(path)
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                            env=env, capture_output=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError("WebView2 bootstrapper Authenticode validation failed "
                         "(PowerShell exit " + str(result.returncode) + ")")
    try:
        signature = json.loads(result.stdout.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("WebView2 bootstrapper signature result is invalid") from error
    return signature


def verify(path: Path) -> dict:
    """Return the exact release pin only after bytes and signer are verified."""
    pin = load_pin()
    path = path.absolute()
    if path.name != pin["name"]:
        raise ValueError("wrong Microsoft WebView2 bootstrapper filename")
    info = ordinary_file(path)
    if info.st_size != pin["size"] or digest(path) != pin["sha256"]:
        raise ValueError("Microsoft WebView2 bootstrapper differs from release pin")
    signature = authenticode(path)
    if (signature.get("status") != "Valid"
            or signature.get("subject") != pin["signerSubject"]
            or signature.get("thumbprint") != pin["signerThumbprint"]):
        raise ValueError("Microsoft WebView2 bootstrapper signature differs from release pin")
    return pin
