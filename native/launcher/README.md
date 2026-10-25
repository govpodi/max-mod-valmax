# Ordinary native launcher

Before KnownFolder, driver creation or profile access, the production entrypoint
uses the shared `native/common/startup_context` guard. Only confirmed unpackaged,
non-AppContainer primary-process metadata is accepted. Unknown observations
fail with a finite error; package names and SIDs are not queried. The standalone
transfer helper performs the same check before Qt initialization. Both
KnownFolder calls use `KF_FLAG_NO_PACKAGE_REDIRECTION | KF_FLAG_DONT_VERIFY`.
This is a necessary context check, not a desktop reentry implementation or proof
against inherited VFS. Explorer/logon/session parity remains unverified.

`ValMAX.exe` is a Windows x64 C++20 GUI executable with a static MSVC runtime.
It derives its package directory from its own executable and LocalAppData from
`SHGetKnownFolderPath`; command-line and inherited-environment path overrides
are not accepted. The only commands are the ordinary empty/`launcher` command,
`mdata`/`import`, and `recover`.

The package layout is `ValMAX.exe` beside `runtime/mod`, `runtime/webview2`, and
`runtime/transfer`. `launcher_generate_pins.py` generates a compiled inventory
of every regular payload file. `REQUIRED` lists minimum components; empty,
untracked, hardlinked, reparse, development and private entries are rejected.
The only mutable runtime entry is optional `mod/optimization-active.json`
(regular file, one link, at most 4096 bytes). Profile files belong under MXMOD
in LocalAppData, outside the payload.

The stock service remains in `runtime/mod/max-service/max-service.exe`, beside
its own `config.dll`. It is not flattened into the client directory. An owned
service still running without the main client prevents a duplicate startup.

Ordinary startup holds a transfer mutex and verified payload handles and scans
processes **before** preparing MXMOD. A main client from this exact package gets
only a read-only recovery/configuration check and window activation. A remaining
own service, unknown process state, or another potentially MXMOD-using client
blocks launch before profile/runtime preparation. The existing native transfer
classifier excludes only the pinned original EXE plus its sibling `config.dll`;
directory names and registration are not used to infer official-client identity.
The same rule covers portable/developer copies at arbitrary paths. An unfamiliar
official version also stays a potential client until its pair is reviewed.

With sampled process absence, startup performs a read-only guard, prepares the
profile/runtime and a bounded, sanitized child environment, rechecks read-only
recovery/configuration and process state, and calls ordinary `CreateProcessW`
with the exact application and working-directory paths. Creation/idle wait does
not acknowledge privacy, ghost, notification or retention readiness. No HTTP
server, Python runtime, injected bootstrap or observer is started by this code.

The read-only guard opens the technical recovery marker for attributes with
`OPEN_REPARSE_POINT`; every existing object blocks launch. Only a missing file
allows the existing Rust check-only API to inspect configuration. It does not
create a missing config while activating an existing window. Process scans have
a cooperative 15-second budget; Windows I/O is not preempted and sampled absence
does not exclude an unrelated direct launch between observations.

Explicit MData commands start the verified standalone helper. Recovery passes
only `--recover`; passwords and paths are selected in that helper. These routes
do not prepare MXMOD in the launcher or own the helper's transfer transaction.

Build in a process with `native/tooling/msvc_env.ps1` loaded, using build-only
Python:

```powershell
python native/tooling/build_launcher.py --build D:/ValMAXBuild/build/launcher-release --runtime-root D:/ValMAXBuild/build/package-input/runtime --report D:/ValMAX/analysis/native-migration-v1/launcher-release-build.json
```

At build time `launcher_icon.py` reads the selected stock icon group from the
exact pinned `runtime/mod/max.exe`, writes an ICO/RC in the new build directory,
and verifies the compiled launcher resource group and every frame byte for byte.
It preserves resource identifiers and language; it neither decodes/resizes pixels
nor introduces an image processing dependency into the installed executable.

The receipt contains `compiled`, `artifacts`, `sourceHashes`, `pinsReceipt`,
`iconReceipt` and `iconVerification`.
It proves compilation and payload bytes; dependency closure, full product
integration and distribution approval are separate checks. No end-user Python
dependency is introduced by the build tool.

`native/tests/launcher/run_production_launcher.py` compiles the real GUI entrypoint
without executing its production KnownFolder/profile path, then tests the same
driver against a separate fake core and own hidden window. Tests cover first
spawn, nested service process recognition, repeated activation, environment/configuration, recovery refusal,
explicit helper routing and byte tampering. Only owned synthetic fixture
directories are used, with no real profile or WinCred operation.

The 21 October lifecycle regression uses an explicit own-PID snapshot seam
compiled only into the test executable. Production always uses Toolhelp and
cannot select that seam through environment or command-line arguments.
`launcher-lifecycle-real-core-20261021.json` records 70 policy, 191 fake-core and
231 real frozen-core checks; `launcher-lifecycle-input-boundaries-20261021.json`
records 21 receipt/source-input boundary checks. The production fixture compiles
the actual GUI entrypoint and verifies the four stock icon frames, but never
executes its KnownFolder entrypoint or MAX. These are component/owned-lifecycle
checks, not installed full-client evidence.
