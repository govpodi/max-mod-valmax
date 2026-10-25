{ Ordinary whole-tree installer update. No profile, account or credential API.
  Never delete an old/partial tree: rename it to a verified sibling instead. }
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#NativeAppId}_is1';
  TransactionKey = 'Software\ValMAX\InstallerTransactions\{#NativeAppId}';
  MarkerName = '.valmax-installation.ini';
type
  NativeFindData = record
    Attributes: LongWord;
    CreationLow, CreationHigh, AccessLow, AccessHigh, WriteLow, WriteHigh: LongWord;
    SizeHigh, SizeLow, Reserved0, Reserved1: LongWord;
    FileName: array[0..259] of Char;
    AlternateName: array[0..13] of Char;
  end;
var
  UpgradePrepared, UpgradeMoved, UpgradeCommitted, RegistrySaved, HadPrevious, CommitFailed: Boolean;
  UpgradeApp, UpgradeNonce, UpgradeBackup, UpgradePartial, UpgradeJournal: String;
  TransferMutex: LongWord;

function NativeGetFileAttributes(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function NativeRegOpen(Key: LongWord; SubKey: String; Options, Access: LongWord;
  var Opened: LongWord): LongWord;
  external 'RegOpenKeyExW@advapi32.dll stdcall';
function NativeRegCreate(Key: LongWord; SubKey: String; Reserved: LongWord;
  ClassName: LongWord; Options, Access, Security: LongWord;
  var Opened, Disposition: LongWord): LongWord;
  external 'RegCreateKeyExW@advapi32.dll stdcall';
function NativeRegCopy(Key: LongWord; SubKey: LongWord; Destination: LongWord): LongWord;
  external 'RegCopyTreeW@advapi32.dll stdcall';
function NativeRegClose(Key: LongWord): LongWord;
  external 'RegCloseKey@advapi32.dll stdcall';
function NativeFindFirst(Name: String; var Data: NativeFindData): LongWord;
  external 'FindFirstFileW@kernel32.dll stdcall';
function NativeFindNext(Handle: LongWord; var Data: NativeFindData): Integer;
  external 'FindNextFileW@kernel32.dll stdcall';
function NativeFindClose(Handle: LongWord): Integer;
  external 'FindClose@kernel32.dll stdcall';
function NativeCreateMutex(Security: LongWord; Owned: Integer; Name: String): LongWord;
  external 'CreateMutexW@kernel32.dll stdcall';
function NativeWait(Handle, Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function NativeReleaseMutex(Handle: LongWord): Integer;
  external 'ReleaseMutex@kernel32.dll stdcall';
function NativeCloseHandle(Handle: LongWord): Integer;
  external 'CloseHandle@kernel32.dll stdcall';
#if ValMAXInstallerFixture == "1"
  #ifdef FixtureCrashAfterMove
procedure FixtureExitProcess(Code: LongWord);
  external 'ExitProcess@kernel32.dll stdcall';
  #endif
#endif

function LockTransfer: Boolean;
var Waited: LongWord;
begin
  Result := False;
  if TransferMutex <> 0 then begin Result := True; Exit; end;
#if ValMAXInstallerFixture == "1"
  TransferMutex := NativeCreateMutex(0, 0, 'Local\ValMAXInstallerFixture-{#NativeAppId}');
#else
  TransferMutex := NativeCreateMutex(0, 0, 'Local\MAXModSessionTransferV1');
#endif
  if TransferMutex = 0 then Exit;
  Waited := NativeWait(TransferMutex, 0);
  if (Waited = 0) or (Waited = $80) then begin Result := True; Exit; end;
  NativeCloseHandle(TransferMutex); TransferMutex := 0;
end;

procedure UnlockTransfer;
begin
  if TransferMutex <> 0 then
  begin
    NativeReleaseMutex(TransferMutex); NativeCloseHandle(TransferMutex); TransferMutex := 0;
  end;
end;

function FindName(const Data: NativeFindData): String;
var Index: Integer;
begin
  Result := '';
  for Index := 0 to 259 do
  begin
    if Data.FileName[Index] = #0 then Exit;
    Result := Result + Data.FileName[Index];
  end;
  RaiseException(CustomMessage('UpgradeUnsafe'));
end;

function ReservedComponent(const Name: String): Boolean;
begin
  Result := (CompareText(Name, 'MXMOD') = 0) or (CompareText(Name, 'ONEME') = 0)
    or (CompareText(Name, '.mxmod-helper-temp') = 0);
end;

function PlainPath(const Value: String; AllowMissing: Boolean): Boolean;
var
  Path, Parent: String;
  Attributes, ErrorCode: LongWord;
begin
  Result := False;
  Path := NormalizedPath(Value);
  { Same-volume sibling rename on a local fixed path only. }
  if (Length(Path) < 4) or (Copy(Path, 2, 2) <> ':\') then Exit;
  while Path <> '' do
  begin
    if ReservedComponent(ExtractFileName(Path)) then Exit;
    Attributes := NativeGetFileAttributes(Path);
    if Attributes = $FFFFFFFF then
    begin
      ErrorCode := DLLGetLastError;
      if not AllowMissing or ((ErrorCode <> 2) and (ErrorCode <> 3)) then Exit;
    end
    else if (Attributes and $400) <> 0 then Exit;
    Parent := ExtractFileDir(Path);
    if Parent = Path then Break;
    Path := Parent;
  end;
  Result := True;
end;

function PlainFile(const Path: String): Boolean;
var Attributes: LongWord;
begin
  Attributes := NativeGetFileAttributes(Path);
  Result := (Attributes <> $FFFFFFFF) and ((Attributes and $410) = 0)
    and PlainPath(Path, False);
end;

function MissingPath(const Path: String): Boolean;
var Attributes, ErrorCode: LongWord;
begin
  Attributes := NativeGetFileAttributes(Path);
  ErrorCode := DLLGetLastError;
  Result := (Attributes = $FFFFFFFF) and ((ErrorCode = 2) or (ErrorCode = 3));
end;

function EmptyDirectory(const Path: String): Boolean;
var Entry: NativeFindData; ErrorCode, Handle: LongWord; Name: String;
begin
  Result := False;
  if MissingPath(Path) then begin Result := True; Exit; end;
  Handle := NativeFindFirst(AddBackslash(Path) + '*', Entry);
  if Handle = $FFFFFFFF then
  begin
    ErrorCode := DLLGetLastError;
    Result := (ErrorCode = 2) or (ErrorCode = 18);
    Exit;
  end;
  try
    repeat
      Name := FindName(Entry);
      if (Name <> '.') and (Name <> '..') then Exit;
    until NativeFindNext(Handle, Entry) = 0;
    Result := DLLGetLastError = 18;
  finally NativeFindClose(Handle); end;
end;

function OpaqueLegacyHelper(const TreeRoot, Parent, Name: String; Attributes: LongWord): Boolean;
begin
  { Only the two paths produced by supported old launchers. The directory
    itself must be plain; its contents remain opaque and move with the tree. }
  Result := (CompareText(Name, '.mxmod-helper-temp') = 0) and
    ((Attributes and $410) = $10) and
    ((CompareText(Parent, TreeRoot) = 0) or
     (CompareText(Parent, AddBackslash(TreeRoot) + 'runtime') = 0));
end;

function PlainTreeAtRoot(const TreeRoot, Path: String; Depth: Integer; var Entries: Integer): Boolean;
var Entry: NativeFindData; Child, Name: String; Handle: LongWord; Opaque: Boolean;
begin
  Result := False;
  if (Depth > 64) or (Entries > 100000) or not PlainPath(Path, False) then Exit;
  Handle := NativeFindFirst(AddBackslash(Path) + '*', Entry);
  if Handle = $FFFFFFFF then
  begin Result := DLLGetLastError = 2; Exit; end;
  try
    repeat
      Name := FindName(Entry);
      if (Name <> '.') and (Name <> '..') then
      begin
        Entries := Entries + 1;
        if (Entries > 100000) or ((Entry.Attributes and $400) <> 0) then Exit;
        Opaque := OpaqueLegacyHelper(TreeRoot, Path, Name, Entry.Attributes);
        if ReservedComponent(Name) and not Opaque then Exit;
        Child := AddBackslash(Path) + Name;
        if ((Entry.Attributes and $10) <> 0) and
           not Opaque and
           not PlainTreeAtRoot(TreeRoot, Child, Depth + 1, Entries) then Exit;
      end;
    until NativeFindNext(Handle, Entry) = 0;
    Result := DLLGetLastError = 18;
  finally NativeFindClose(Handle); end;
end;

function PlainTree(const Path: String; Depth: Integer; var Entries: Integer): Boolean;
var TreeRoot: String;
begin
  { Each app/backup/incomplete tree establishes its own root. In particular,
    rollback validation must not use the active UpgradeApp as a global root. }
  TreeRoot := RemoveBackslashUnlessRoot(NormalizedPath(Path));
  Result := PlainTreeAtRoot(TreeRoot, TreeRoot, Depth, Entries);
end;

function DisplayNameInFamily(const Name, Version: String): Boolean;
begin
  Result := (Version <> '') and
    ((Name = '{#NativeCanonicalAppName}') or
     (Name = '{#NativeCanonicalAppName} version ' + Version) or
     (Name = '{#NativeCanonicalAppName}, версия ' + Version) or
     (Name = '{#NativePreviewAppName}') or
     (Name = '{#NativePreviewAppName} version ' + Version) or
     (Name = '{#NativePreviewAppName}, версия ' + Version));
end;

function RegisteredInstallation(const Path: String): Boolean;
var Registered, Uninstaller, Version, Name, Marker: String; Entries: Integer;
begin
  Result := False;
  if not RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', Registered)
    or (CompareText(RemoveBackslashUnlessRoot(NormalizedPath(Registered)), Path) <> 0)
    or not RegQueryStringValue(HKCU64, UninstallKey, 'UninstallString', Uninstaller)
    or not RegQueryStringValue(HKCU64, UninstallKey, 'DisplayVersion', Version)
    or not RegQueryStringValue(HKCU64, UninstallKey, 'DisplayName', Name) then Exit;
#if ValMAXInstallerFixture == "1"
  Log('fixture_registered_values: ' + Name + ' | ' + Version + ' | ' + Uninstaller);
#endif
  { Inno's default DisplayName includes a localized version suffix. }
  if not DisplayNameInFamily(Name, Version) then Exit;
  { Inno's default uninstaller and data must belong to the registered root. }
  if (Uninstaller <> '"' + Path + '\unins000.exe"') or
     not PlainFile(Path + '\unins000.exe') or not PlainFile(Path + '\unins000.dat')
     or not PlainFile(Path + '\ValMAX.exe') then Exit;
#if ValMAXInstallerFixture == "1"
  Log('fixture_registered_files_ok');
#endif
  Marker := Path + '\' + MarkerName;
  if PlainFile(Marker) then
  begin
    if (GetIniString('installation', 'schema', '', Marker) <> '1') or
       (GetIniString('installation', 'appId', '', Marker) <> '{#NativeAppId}') or
       not PlainFile(Path + '\native-package.json') then Exit;
#if ValMAXInstallerFixture == "1"
    Log('fixture_registered_marker_ok');
#endif
  end
  else
  begin
    { The previous packager writes this complete contract, not a guessed name.
      These files identify legacy program files; their contents are never run. }
    if not PlainFile(Path + '\MAX Mod.exe') or
       not PlainFile(Path + '\package-files.json') or
       not PlainFile(Path + '\runtime\mod\mod-manifest.json') or
       not PlainFile(Path + '\ValMAX-инструкция.txt') then Exit;
  end;
  Entries := 0;
  Result := PlainTree(Path, 0, Entries);
#if ValMAXInstallerFixture == "1"
  Log('fixture_registered_tree_checked: ' + IntToStr(Entries));
#endif
end;

function CopyRegistryTree(const Source, Destination: String): Boolean;
var SourceHandle, DestinationHandle, Disposition: LongWord;
begin
  Result := False;
  if NativeRegOpen($80000001, Source, 0, $20019 or $100, SourceHandle) <> 0 then Exit;
  try
    if NativeRegCreate($80000001, Destination, 0, 0, 0,
      $F003F or $100, 0, DestinationHandle, Disposition) <> 0 then Exit;
    try Result := NativeRegCopy(SourceHandle, 0, DestinationHandle) = 0;
    finally NativeRegClose(DestinationHandle); end;
  finally NativeRegClose(SourceHandle); end;
end;

function NonceValid(const Value: String): Boolean;
var Index: Integer;
begin
  Result := False;
  if Length(Value) <> 32 then Exit;
  for Index := 1 to Length(Value) do
    if not (((Value[Index] >= '0') and (Value[Index] <= '9')) or
            ((Value[Index] >= 'a') and (Value[Index] <= 'f'))) then Exit;
  Result := True;
end;

procedure SetTransactionPaths;
begin
  UpgradeBackup := UpgradeApp + '.valmax-backup-' + UpgradeNonce;
  UpgradePartial := UpgradeApp + '.valmax-incomplete-' + UpgradeNonce;
  UpgradeJournal := UpgradeApp + '.valmax-update.ini';
end;

function WriteJournal: Boolean;
var Content, Temporary: String;
begin
  Temporary := UpgradeJournal + '.new';
  Result := False;
  if not MissingPath(Temporary) or not MissingPath(UpgradeJournal) then Exit;
  Content := '[transaction]' + #13#10 + 'schema=1' + #13#10 +
    'appId={#NativeAppId}' + #13#10 + 'app=' + UpgradeApp + #13#10 +
    'nonce=' + UpgradeNonce + #13#10;
  if HadPrevious then Content := Content + 'previous=1' + #13#10
  else Content := Content + 'previous=0' + #13#10;
  if not SaveStringToFile(Temporary, Content, False) then Exit;
  Result := RenameFile(Temporary, UpgradeJournal);
end;

function RestorePrevious: Boolean;
var Entries: Integer;
begin
  Result := False;
  if not UpgradePrepared then begin Result := True; Exit; end;
  if not PlainPath(UpgradeApp, True) or not PlainPath(UpgradeBackup, True) or
     not PlainPath(UpgradePartial, True) then Exit;
  if HadPrevious and not MissingPath(UpgradeBackup) then
  begin
    Entries := 0;
    if (OwnedDirectoryProcessPreflight(UpgradeBackup) <> '') or
       not PlainTree(UpgradeBackup, 0, Entries) then Exit;
    if not EmptyDirectory(UpgradeApp) then
    begin
      if not MissingPath(UpgradePartial) or not RenameFile(UpgradeApp, UpgradePartial) then Exit;
    end
    else if DirExists(UpgradeApp) and not RemoveDir(UpgradeApp) then Exit;
    if not RenameFile(UpgradeBackup, UpgradeApp) then Exit;
  end
  else if HadPrevious and UpgradeMoved then Exit;
  if not HadPrevious then
  begin
    if not EmptyDirectory(UpgradeApp) then
    begin
      if not MissingPath(UpgradePartial) or not RenameFile(UpgradeApp, UpgradePartial) then Exit;
    end
    else if DirExists(UpgradeApp) and not RemoveDir(UpgradeApp) then Exit;
    if RegKeyExists(HKCU64, UninstallKey) then
      if not RegDeleteKeyIncludingSubkeys(HKCU64, UninstallKey) then Exit;
  end;
  if RegistrySaved then
  begin
    if RegKeyExists(HKCU64, UninstallKey) and
       not RegDeleteKeyIncludingSubkeys(HKCU64, UninstallKey) then Exit;
    if not CopyRegistryTree(TransactionKey + '\' + UpgradeNonce, UninstallKey) then Exit;
  end;
  { Deleting only our exact journal/key; old and partial trees are retained. }
  if PlainFile(UpgradeJournal) and not DeleteFile(UpgradeJournal) then Exit;
  RegDeleteKeyIncludingSubkeys(HKCU64, TransactionKey + '\' + UpgradeNonce);
  UpgradePrepared := False;
  Result := True;
end;

function RecoverInterrupted: Boolean;
var Marker, Previous: String; ParsedPrevious, ParsedRegistry: Boolean;
begin
  Result := False;
  if MissingPath(UpgradeJournal) then begin Result := True; Exit; end;
  if not PlainFile(UpgradeJournal) or
     (GetIniString('transaction', 'schema', '', UpgradeJournal) <> '1') or
     (GetIniString('transaction', 'appId', '', UpgradeJournal) <> '{#NativeAppId}') or
     (GetIniString('transaction', 'app', '', UpgradeJournal) <> UpgradeApp) then Exit;
  UpgradeNonce := GetIniString('transaction', 'nonce', '', UpgradeJournal);
  if not NonceValid(UpgradeNonce) then Exit;
  SetTransactionPaths;
  { An already committed payload after a process interruption remains valid. }
  Marker := UpgradeApp + '\' + MarkerName;
  if PlainFile(Marker) and
     (GetIniString('installation', 'transaction', '', Marker) = UpgradeNonce) and
     (GetIniString('installation', 'schema', '', Marker) = '1') and
     (GetIniString('installation', 'appId', '', Marker) = '{#NativeAppId}') and
     RegisteredInstallation(UpgradeApp) then
  begin
    Result := DeleteFile(UpgradeJournal);
    Exit;
  end;
  Previous := GetIniString('transaction', 'previous', '', UpgradeJournal);
  if Previous = '1' then ParsedPrevious := True
  else if Previous = '0' then ParsedPrevious := False
  else Exit;
  ParsedRegistry := RegKeyExists(HKCU64, TransactionKey + '\' + UpgradeNonce);
  if ParsedPrevious <> ParsedRegistry then Exit;
  HadPrevious := ParsedPrevious;
  RegistrySaved := ParsedRegistry;
  UpgradeMoved := not MissingPath(UpgradeBackup);
  UpgradePrepared := True;
  Result := RestorePrevious;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Entries: Integer;
begin
  NeedsRestart := False;
  Result := CustomMessage('UpgradeUnsafe');
  if not LockTransfer then Exit;
  Result := OwnedProcessPreflight;
  if Result <> '' then Exit;
  Result := CustomMessage('UpgradeUnsafe');
  UpgradeApp := RemoveBackslashUnlessRoot(NormalizedPath(ExpandConstant('{app}')));
#if ValMAXInstallerFixture == "1"
  if CompareText(UpgradeApp, '{#FixtureRoot}\app') <> 0 then Exit;
#endif
  if not PlainPath(UpgradeApp, True) then Exit;
  UpgradeJournal := UpgradeApp + '.valmax-update.ini';
  if not RecoverInterrupted then begin Result := CustomMessage('UpgradeRecovery'); Exit; end;
  HadPrevious := not EmptyDirectory(UpgradeApp);
  if HadPrevious then
  begin
    if not RegisteredInstallation(UpgradeApp) then Exit;
  end
  else if RegKeyExists(HKCU64, UninstallKey) then Exit;
  { A collision-resistant directory label, not a security token. Inno provides
    a fresh private temporary directory; existing labels are always rejected. }
  UpgradeNonce := GetMD5OfString(ExpandConstant('{tmp}') +
    GetDateTimeString('yyyymmddhhnnsszzz', '-', ':'));
#if ValMAXInstallerFixture == "1"
  Log('fixture_transaction_label_created');
#endif
  if not NonceValid(UpgradeNonce) then Exit;
  SetTransactionPaths;
  if not MissingPath(UpgradeBackup) or not MissingPath(UpgradePartial) or
     RegKeyExists(HKCU64, TransactionKey + '\' + UpgradeNonce) then Exit;
  if HadPrevious then
  begin
    if not CopyRegistryTree(UninstallKey, TransactionKey + '\' + UpgradeNonce) then Exit;
    RegistrySaved := True;
  end;
#if ValMAXInstallerFixture == "1"
  Log('fixture_registry_snapshot_copied');
#endif
  if not WriteJournal then Exit;
  UpgradePrepared := True;
  { Recheck immediately before the same-volume whole-directory rename. }
  Entries := 0;
  if OwnedProcessPreflight <> '' then Exit;
  if HadPrevious then
  begin
    if not PlainTree(UpgradeApp, 0, Entries) or not RenameFile(UpgradeApp, UpgradeBackup) then Exit;
    UpgradeMoved := True;
  end;
#if ValMAXInstallerFixture == "1"
  #ifdef FixtureCrashAfterMove
  FixtureExitProcess(91);
  #endif
#endif
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Marker, Content: String;
begin
  if CurStep <> ssPostInstall then Exit;
  CommitFailed := True;
  try
#if ValMAXInstallerFixture == "1"
  #ifdef FixtureFailAfterCopy
  Log('synthetic_failure_after_copy');
  RaiseException('synthetic_failure_after_copy');
  #endif
#endif
    Marker := ExpandConstant('{app}\') + MarkerName;
    Content := '[installation]' + #13#10 + 'schema=1' + #13#10 +
      'appId={#NativeAppId}' + #13#10 + 'version={#ProductVersion}' + #13#10 +
      'transaction=' + UpgradeNonce + #13#10;
    if not PlainPath(ExpandConstant('{app}'), False) or
       not MissingPath(Marker) or not SaveStringToFile(Marker, Content, False) then
      RaiseException(CustomMessage('UpgradeRecovery'));
    if UpgradePrepared then
    begin
      { Keep the previous uninstall metadata beside its old files logically:
        the exact transaction key is named by this retained backup receipt. }
      if HadPrevious and not SaveStringToFile(UpgradeBackup + '.receipt.ini',
        '[backup]' + #13#10 + 'appId={#NativeAppId}' + #13#10 +
        'transaction=' + UpgradeNonce + #13#10, False) then
        RaiseException(CustomMessage('UpgradeRecovery'));
      if not DeleteFile(UpgradeJournal) then RaiseException(CustomMessage('UpgradeRecovery'));
    end;
    UpgradeCommitted := True;
    CommitFailed := False;
  except
    { Inno catches exceptions from CurStepChanged; returning a normal success
      code would be misleading. Restore now, suppress postinstall launch and
      override the exit/finished state explicitly. }
    if not RestorePrevious then Log('ValMAX commit failed; recovery remains pending.');
    SuppressibleMsgBox(CustomMessage('UpgradeRecovery'), mbError, MB_OK, IDOK);
  end;
end;

function NativeInstallCommitted: Boolean;
begin
  Result := UpgradeCommitted and not CommitFailed;
end;

function GetCustomSetupExitCode: Integer;
begin
  if CommitFailed or (UpgradePrepared and not UpgradeCommitted) then Result := 1
  else Result := 0;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and CommitFailed then
  begin
    WizardForm.FinishedHeadingLabel.Caption := CustomMessage('UpgradeRecovery');
    WizardForm.FinishedLabel.Caption := CustomMessage('UpgradeRecovery');
  end;
end;

procedure DeinitializeSetup;
begin
  if UpgradePrepared and not UpgradeCommitted then
    if not RestorePrevious then
      Log('ValMAX update recovery remains pending; trees and transaction are preserved.');
  UnlockTransfer;
end;
