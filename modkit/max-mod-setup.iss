#ifndef Payload
  #error Payload is required
#endif
#ifndef Output
  #error Output is required
#endif
#ifndef NativeTransfer
  #define NativeTransfer "0"
#endif
#if (NativeTransfer != "0") && (NativeTransfer != "1")
  #error NativeTransfer must be 0 or 1
#endif
#if NativeTransfer == "1"
  #if !FileExists(Payload + "\runtime\transfer\valmax-transfer.exe")
    #error Verified native transfer payload is missing
  #endif
#endif

[Setup]
AppId={{419706B9-6FC7-46F8-B9FB-D7212FC91BAC}
AppName=ValMAX
AppVersion=26.35.1.80662-valmax8.8.4
AppPublisher=ValMAX
DefaultDirName={localappdata}\Programs\ValMAX
DefaultGroupName=ValMAX
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#Output}
OutputBaseFilename=ValMAX-26.35.1-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ValMAX.exe
CloseApplications=no
RestartApplications=no
SetupLogging=yes
DisableWelcomePage=no
DisableProgramGroupPage=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Messages]
WelcomeLabel1=ValMAX%nВаше общение. Ваши правила.
WelcomeLabel2=Неофициальный клиент MAX для Windows — для жизни и бизнеса.%n%nВАЛМАКС — Возможности. Автоматизация. Личный контроль.%n%nБлиже к клиентам. Свободнее в работе.%n%nМастер установит ValMAX на этот компьютер.
FinishedHeadingLabel=ValMAX установлена
FinishedLabel=Ваше общение. Ваши правила.%n%nБлиже к клиентам. Свободнее в работе.%n%nValMAX готова к запуску. Нажмите «Завершить», чтобы закрыть мастер установки.
FinishedLabelNoIcons=Ваше общение. Ваши правила.%n%nБлиже к клиентам. Свободнее в работе.%n%nValMAX готова к запуску. Нажмите «Завершить», чтобы закрыть мастер установки.

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: unchecked

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ValMAX"; Filename: "{app}\ValMAX.exe"; WorkingDir: "{app}"
#if NativeTransfer == "1"
Name: "{group}\Войти с MData"; Filename: "{app}\runtime\transfer\valmax-transfer.exe"; WorkingDir: "{app}\runtime\transfer"
#else
Name: "{group}\Войти с MData"; Filename: "{app}\ValMAX.exe"; Parameters: "mdata"; WorkingDir: "{app}"
#endif
Name: "{group}\Восстановление MData"; Filename: "{app}\ValMAX.exe"; Parameters: "recover"; WorkingDir: "{app}"
Name: "{group}\Инструкция"; Filename: "{app}\ValMAX-инструкция.txt"
Name: "{autodesktop}\ValMAX"; Filename: "{app}\ValMAX.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\ValMAX.exe"; Description: "Запустить ValMAX"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent unchecked

[Code]
const
  OwnedProcessRunning = 'Закрой ValMAX и его помощник, затем повтори установку или удаление.';
  OwnedProcessScanFailed = 'Не удалось проверить закрытие ValMAX. Установка или удаление остановлены; повтори попытку позже.';

function NormalizedExecutablePath(const Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '/', '\', True);
  if CompareText(Copy(Result, 1, 8), '\\?\UNC\') = 0 then
    Result := '\\' + Copy(Result, 9, Length(Result))
  else if Copy(Result, 1, 4) = '\\?\' then
    Delete(Result, 1, 4);
  if (Pos(#0, Result) > 0) or
     ((Copy(Result, 1, 2) <> '\\') and
      ((Length(Result) < 3) or (Copy(Result, 2, 2) <> ':\'))) then
    RaiseException(OwnedProcessScanFailed);
  Result := ExpandFileName(Result);
end;

function OwnedProcessPreflight: String;
var
  Locator, Services, Processes, Process, ImageValue: Variant;
  Prefix, Image: String;
  Count, Index: Integer;
begin
  { WMI reads only executable paths for the exact process names below.
    No command line, memory, profile or credential is read; no process is killed. }
  Result := OwnedProcessScanFailed;
  try
    Prefix := AddBackslash(NormalizedExecutablePath(ExpandConstant('{app}')));
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Services := Locator.ConnectServer('.', 'root\CIMV2');
    Processes := Services.ExecQuery(
      'SELECT ExecutablePath FROM Win32_Process WHERE Name = ''max.exe'' ' +
      'OR Name = ''max-service.exe'' OR Name = ''ValMAX.exe'' OR Name = ''MAX Mod.exe'' ' +
      'OR Name = ''valmax-transfer.exe''', 'WQL', 0);
    Count := Processes.Count;
    if (Count < 0) or (Count > 4096) then
      Exit;
    for Index := 0 to Count - 1 do
    begin
      Process := Processes.ItemIndex(Index);
      ImageValue := Process.ExecutablePath;
      { An inaccessible or already-exited candidate cannot prove safety. }
      if VarIsNull(ImageValue) or VarIsEmpty(ImageValue) then
        Exit;
      Image := ImageValue;
      if Image = '' then
        Exit;
      Image := NormalizedExecutablePath(Image);
      { The trailing slash enforces a component boundary: ValMAX 2 is outside.
        Known official MAX paths outside this installation are ignored. }
      if CompareText(Copy(Image, 1, Length(Prefix)), Prefix) = 0 then
      begin
        Result := OwnedProcessRunning;
        Exit;
      end;
    end;
    Result := '';
  except
    { Never print COM exceptions, executable paths or other process metadata. }
    Result := OwnedProcessScanFailed;
  end;
end;

#if NativeTransfer == "1"
function TransferGetFileAttributes(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function NativeTransferPreflight: String;
var
  Path, Parent: String;
  Attributes, ErrorCode: LongWord;
begin
  { No deletion/merging: replacing a previously installed helper requires a
    separate managed-inventory updater. This opt-in only installs a fresh tree. }
  Result := 'Каталог нативного помощника MData уже существует или недоступен. Выбери новую папку установки ValMAX; существующие файлы не будут удалены.';
  Path := ExpandConstant('{app}\runtime\transfer');
  Attributes := TransferGetFileAttributes(Path);
  if Attributes <> $FFFFFFFF then
    Exit;
  ErrorCode := DLLGetLastError;
  if (ErrorCode <> 2) and (ErrorCode <> 3) then
    Exit;
  Path := ExtractFileDir(Path);
  while Path <> '' do
  begin
    Attributes := TransferGetFileAttributes(Path);
    if Attributes = $FFFFFFFF then
    begin
      ErrorCode := DLLGetLastError;
      if (ErrorCode <> 2) and (ErrorCode <> 3) then
        Exit;
    end
    else if ((Attributes and $400) <> 0) or ((Attributes and $10) = 0) then
      Exit;
    Parent := ExtractFileDir(Path);
    if Parent = Path then
      Break;
    Path := Parent;
  end;
  Result := '';
end;
#endif

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;
  Result := OwnedProcessPreflight;
#if NativeTransfer == "1"
  if Result = '' then
    Result := NativeTransferPreflight;
#endif
end;

function InitializeUninstall: Boolean;
var
  Message: String;
begin
  Message := OwnedProcessPreflight;
  Result := Message = '';
  if not Result then
    SuppressibleMsgBox(Message, mbError, MB_OK, IDOK);
end;
