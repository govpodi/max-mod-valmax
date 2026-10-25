#ifndef Payload
  #error Payload is required
#endif
#ifndef Output
  #error Output is required
#endif
#ifndef ProductVersion
  #error ProductVersion is required
#endif
#ifndef ValMAXInstallerFixture
  #define ValMAXInstallerFixture "0"
#endif
#if (ValMAXInstallerFixture != "0") && (ValMAXInstallerFixture != "1")
  #error ValMAXInstallerFixture must be 0 or 1
#endif
#if ValMAXInstallerFixture == "1"
  #ifndef FixtureRoot
    #error FixtureRoot is required for installer tests
  #endif
  #if (Pos("D:\ValMAXBuild\fixtures\installer-", FixtureRoot) != 1) && (Pos("E:\ValMAXBuild\fixtures\installer-", FixtureRoot) != 1)
    #error Installer fixtures must stay in an owned D or E build root
  #endif
  #define NativeAppId "{80AA932D-1E5C-4509-BE97-20D43D271ADE}"
  #define NativeCanonicalAppName "ValMAX Installer Fixture"
  #define NativePreviewAppName "ValMAX Installer Fixture native preview"
  #define NativeBetaAppName "ValMAX Installer Fixture BETA"
  #ifdef FixtureBetaName
    #define NativeAppName NativeBetaAppName
  #else
    #ifdef FixturePreviewName
      #define NativeAppName NativePreviewAppName
    #else
      #define NativeAppName NativeCanonicalAppName
    #endif
  #endif
#else
  #if !FileExists(Payload + "\native-release-state.iss")
    #error Native release eligibility include is required
  #endif
  #include Payload + "\native-release-state.iss"
  #if NativeDistributionMode == "local-preview"
    #ifndef NativePreviewVersion
      #error A local preview version is required
    #endif
    #ifndef NativePreviewInfoSha256
      #error Local preview information must be pinned
    #endif
    #if (NativeReleaseEligible != 0) || (NativeReleaseBlockerCount < 6) || (NativeReleaseBlockerCount > 7)
      #error A local preview must preserve its unresolved product blockers
    #endif
    #if (NativePreviewVersion != ProductVersion) || (Pos("-native.preview.", ProductVersion) == 0)
      #error The preview product version must be visibly marked
    #endif
    #if !FileExists(Payload + "\native-preview.txt")
      #error Local preview information is missing
    #endif
    #if GetSHA256OfFile(Payload + "\native-preview.txt") != NativePreviewInfoSha256
      #error Local preview information belongs to another package
    #endif
  #else
    #if NativeDistributionMode == "public-beta"
      #ifndef NativeBetaVersion
        #error A public BETA version is required
      #endif
      #ifndef NativeBetaInfoSha256
        #error Public BETA information must be pinned
      #endif
      #ifndef NativeBetaCoreTested
        #error Public BETA installed core smoke must be pinned
      #endif
      #ifndef NativeBetaMaterialsInfoSha256
        #error Public BETA materials summary must be pinned
      #endif
      #ifndef NativeBetaSource713Sha256
        #error FFmpeg 7.1.3 source must be pinned
      #endif
      #ifndef NativeBetaSource715Sha256
        #error FFmpeg 7.1.5 source must be pinned
      #endif
      #ifndef NativeBetaSource713SignatureSha256
        #error FFmpeg 7.1.3 signature must be pinned
      #endif
      #ifndef NativeBetaSource715SignatureSha256
        #error FFmpeg 7.1.5 signature must be pinned
      #endif
      #ifndef NativeBetaBuildConfigSha256
        #error FFmpeg build configurations must be pinned
      #endif
      #if (NativeReleaseEligible != 1) || (NativeReleaseBlockerCount != 0) || (NativeBetaCoreTested != 1)
        #error A blocked or untested public BETA cannot become a user installer
      #endif
      #if (NativeBetaVersion != ProductVersion) || (Pos("-native.preview.", ProductVersion) == 0)
        #error The public BETA product version must be visibly marked
      #endif
      #if !FileExists(Payload + "\native-beta.txt")
        #error Public BETA information is missing
      #endif
      #if GetSHA256OfFile(Payload + "\native-beta.txt") != NativeBetaInfoSha256
        #error Public BETA information belongs to another package
      #endif
      #if !FileExists(Payload + "\native-beta-materials.json")
        #error Public BETA materials summary is missing
      #endif
      #if GetSHA256OfFile(Payload + "\native-beta-materials.json") != NativeBetaMaterialsInfoSha256
        #error Public BETA materials summary belongs to another package
      #endif
      #if !FileExists(Payload + "\materials\FFmpeg\ffmpeg-7.1.3.tar.xz") || !FileExists(Payload + "\materials\FFmpeg\ffmpeg-7.1.5.tar.xz")
        #error Both versioned FFmpeg source archives are required
      #endif
      #if GetSHA256OfFile(Payload + "\materials\FFmpeg\ffmpeg-7.1.3.tar.xz") != NativeBetaSource713Sha256
        #error FFmpeg 7.1.3 source belongs to another package
      #endif
      #if GetSHA256OfFile(Payload + "\materials\FFmpeg\ffmpeg-7.1.5.tar.xz") != NativeBetaSource715Sha256
        #error FFmpeg 7.1.5 source belongs to another package
      #endif
      #if !FileExists(Payload + "\materials\FFmpeg\ffmpeg-7.1.3.tar.xz.asc") || !FileExists(Payload + "\materials\FFmpeg\ffmpeg-7.1.5.tar.xz.asc")
        #error Both versioned FFmpeg source signatures are required
      #endif
      #if GetSHA256OfFile(Payload + "\materials\FFmpeg\ffmpeg-7.1.3.tar.xz.asc") != NativeBetaSource713SignatureSha256
        #error FFmpeg 7.1.3 signature belongs to another package
      #endif
      #if GetSHA256OfFile(Payload + "\materials\FFmpeg\ffmpeg-7.1.5.tar.xz.asc") != NativeBetaSource715SignatureSha256
        #error FFmpeg 7.1.5 signature belongs to another package
      #endif
      #if !FileExists(Payload + "\materials\FFmpeg\build-configurations.json")
        #error FFmpeg build configurations are missing
      #endif
      #if GetSHA256OfFile(Payload + "\materials\FFmpeg\build-configurations.json") != NativeBetaBuildConfigSha256
        #error FFmpeg build configurations belong to another package
      #endif
    #else
      #if (NativeDistributionMode != "user-release") || (NativeReleaseEligible != 1) || (NativeReleaseBlockerCount != 0)
        #error A staging or blocked native package cannot become a user installer
      #endif
    #endif
  #endif
  #if GetSHA256OfFile(Payload + "\native-package.json") != NativePackageManifestSha256
    #error Native eligibility include belongs to another package manifest
  #endif
  #ifndef NativeWebView2Mode
    #error Native WebView2 packaging mode must be pinned
  #endif
  #if (NativeWebView2Mode != "fixed") && (NativeWebView2Mode != "evergreen")
    #error Unknown native WebView2 packaging mode
  #endif
  #ifndef NativeQtSourceMode
    #error Native Qt source packaging mode must be pinned
  #endif
  #if (NativeQtSourceMode != "inline") && (NativeQtSourceMode != "separate-asset")
    #error Unknown native Qt source packaging mode
  #endif
  #if NativeQtSourceMode == "separate-asset"
    #if NativeWebView2Mode != "evergreen"
      #error Separate Qt source asset requires Evergreen WebView2 mode
    #endif
    #ifndef NativeQtSourceAssetSha256
      #error Separate Qt source asset digest is missing
    #endif
    #ifndef NativeQtSourceInfoSha256
      #error Separate Qt source notice digest is missing
    #endif
    #ifndef QtSourceAsset
      #error Separate Qt source ZIP path must be supplied to the compiler
    #endif
    #if !FileExists(QtSourceAsset)
      #error Separate Qt source ZIP is missing
    #endif
    #if GetSHA256OfFile(QtSourceAsset) != NativeQtSourceAssetSha256
      #error Separate Qt source ZIP differs from the package receipt
    #endif
    #if !FileExists(Payload + "\QT-SOURCE-AVAILABILITY.txt")
      #error Separate Qt source availability notice is missing
    #endif
    #if GetSHA256OfFile(Payload + "\QT-SOURCE-AVAILABILITY.txt") != NativeQtSourceInfoSha256
      #error Separate Qt source availability notice differs from the package receipt
    #endif
  #endif
  #define NativeAppId "{419706B9-6FC7-46F8-B9FB-D7212FC91BAC}"
  #define NativeCanonicalAppName "ValMAX"
  #define NativePreviewAppName "ValMAX native preview"
  #define NativeBetaAppName "ValMAX BETA"
  #if NativeDistributionMode == "local-preview"
    #define NativeAppName NativePreviewAppName
  #else
    #if NativeDistributionMode == "public-beta"
      #define NativeAppName NativeBetaAppName
    #else
      #define NativeAppName NativeCanonicalAppName
    #endif
  #endif
#endif
#if !FileExists(Payload + "\ValMAX.exe")
  #error Native launcher is missing
#endif
#if !FileExists(Payload + "\native-package.json")
  #error Native package inventory is missing
#endif
#if !FileExists(Payload + "\runtime\transfer\valmax-transfer.exe")
  #error Native MData helper is missing
#endif

[Setup]
AppId={{#NativeAppId}
AppName={#NativeAppName}
AppVerName={#NativeAppName}
AppVersion={#ProductVersion}
AppPublisher=ValMAX
AppPublisherURL=https://github.com/govpodi/max-mod-valmax
AppSupportURL=https://t.me/VALMAX_dev
AppUpdatesURL=https://github.com/govpodi/max-mod-valmax/releases
#if ValMAXInstallerFixture == "1"
DefaultDirName={#FixtureRoot}\app
#else
DefaultDirName={localappdata}\Programs\ValMAX
#endif
DefaultGroupName=ValMAX
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#Output}
OutputBaseFilename=ValMAX-{#ProductVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ValMAX.exe
CloseApplications=no
RestartApplications=no
DisableWelcomePage=no
#if ValMAXInstallerFixture != "1"
  #if NativeDistributionMode == "local-preview"
InfoBeforeFile={#Payload}\native-preview.txt
  #else
    #if NativeDistributionMode == "public-beta"
InfoBeforeFile={#Payload}\native-beta.txt
    #endif
  #endif
#endif
DisableProgramGroupPage=yes
UninstallLogMode=new

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
russian.WelcomeLabel1=ValMAX%nВаше общение. Ваши правила.
russian.WelcomeLabel2=Неофициальный клиент MAX для Windows — для жизни и бизнеса.%n%nВАЛ — Возможности. Автоматизация. Личный контроль.%n%nБлиже к клиентам. Свободнее в работе.%n%nМастер установит ValMAX и необходимые библиотеки на этот компьютер.
russian.FinishedLabel=Ваше общение. Ваши правила.%n%nValMAX установлена. Нажмите «Завершить», чтобы закрыть мастер.
english.WelcomeLabel1=ValMAX%nYour conversations. Your rules.
english.WelcomeLabel2=An unofficial MAX client for Windows, for everyday life and business.%n%nCloser to customers. More freedom at work.%n%nSetup will install ValMAX and its required libraries on this computer.
english.FinishedLabel=Your conversations. Your rules.%n%nValMAX is installed. Click Finish to close Setup.

[CustomMessages]
russian.DesktopIcon=Создать ярлык на рабочем столе
english.DesktopIcon=Create a desktop shortcut
russian.ImportMData=Войти с MData
english.ImportMData=Sign in with MData
russian.RecoverMData=Восстановление MData
english.RecoverMData=MData recovery
russian.StartClient=Запустить ValMAX
english.StartClient=Launch ValMAX
russian.CloseClient=Полностью закройте ValMAX через значок в трее → «Выход», затем повторите установку или удаление.
english.CloseClient=Exit ValMAX from its system tray menu, then retry installing or uninstalling.
russian.ScanFailed=Не удалось проверить, закрыта ли ValMAX. Повторите попытку после её полного закрытия.
english.ScanFailed=Setup could not verify that ValMAX has exited. Close ValMAX completely and retry.
russian.UpgradeUnsafe=Не удалось безопасно обновить эту папку. Нужна пустая папка или зарегистрированная установка ValMAX без ссылок на другие каталоги. Аккаунты не изменены.
english.UpgradeUnsafe=This folder cannot be upgraded safely. Select an empty folder or a registered ValMAX installation without directory links. Account data has not been changed.
russian.UpgradeRecovery=Предыдущее обновление не завершилось. Старая установка и незавершённые файлы сохранены рядом. Повторите установку для проверки и восстановления; при повторной ошибке сохраните эти папки.
english.UpgradeRecovery=The previous update did not finish. The old installation and incomplete files are preserved beside the install folder. Retry Setup to check and restore them; keep these folders if recovery fails.
russian.EvergreenMissing=Для этой компактной сборки нужен Microsoft Edge WebView2 Evergreen Runtime. Установите его с официальной страницы Microsoft https://developer.microsoft.com/en-us/microsoft-edge/webview2/ и повторите установку. Без сети можно заранее скачать официальный автономный установщик Runtime или использовать опубликованную полную p16. ValMAX и аккаунты не изменены.
english.EvergreenMissing=This compact build requires Microsoft Edge WebView2 Evergreen Runtime. Install it from the official Microsoft page https://developer.microsoft.com/en-us/microsoft-edge/webview2/ and retry Setup. For an offline PC, prepare Microsoft's standalone Runtime installer in advance or use the published full p16. ValMAX and account data were not changed.

#if ValMAXInstallerFixture != "1"
[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked
#endif

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[UninstallDelete]
Type: files; Name: "{app}\.valmax-installation.ini"

#if ValMAXInstallerFixture != "1"
[Icons]
Name: "{group}\ValMAX"; Filename: "{app}\ValMAX.exe"; WorkingDir: "{app}"
Name: "{group}\{cm:ImportMData}"; Filename: "{app}\ValMAX.exe"; Parameters: "mdata"; WorkingDir: "{app}"
Name: "{group}\{cm:RecoverMData}"; Filename: "{app}\ValMAX.exe"; Parameters: "recover"; WorkingDir: "{app}"
Name: "{group}\Инструкция"; Filename: "{app}\ValMAX-инструкция.txt"
Name: "{autodesktop}\ValMAX"; Filename: "{app}\ValMAX.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\ValMAX.exe"; Description: "{cm:StartClient}"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent unchecked; Check: NativeInstallCommitted
#endif

[Code]
function WebView2VersionPresent(const Version: String): Boolean;
var
  Dot: Integer;
begin
  Dot := Pos('.', Trim(Version));
  Result := (Dot > 1) and (StrToIntDef(Copy(Trim(Version), 1, Dot - 1), 0) > 0);
end;

function EvergreenWebView2Present: Boolean;
var
  ClientKey, Version: String;
begin
  ClientKey := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  { Microsoft documents pv in the 32-bit machine view or per-user key. }
  Result := (RegQueryStringValue(HKLM32, ClientKey, 'pv', Version) and
             WebView2VersionPresent(Version)) or
            (RegQueryStringValue(HKCU, ClientKey, 'pv', Version) and
             WebView2VersionPresent(Version));
end;

function NormalizedPath(const Value: String): String;
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
    RaiseException(CustomMessage('ScanFailed'));
  Result := ExpandFileName(Result);
end;

function ProcessOpen(Access: LongWord; Inherit: Integer; ProcessId: LongWord): LongWord;
  external 'OpenProcess@kernel32.dll stdcall';
function ProcessImageName(Handle, Flags: LongWord; Buffer: String; var Size: LongWord): Integer;
  external 'QueryFullProcessImageNameW@kernel32.dll stdcall';
function ProcessIdentity(Handle: LongWord): LongWord;
  external 'GetProcessId@kernel32.dll stdcall';
function ProcessClose(Handle: LongWord): Integer;
  external 'CloseHandle@kernel32.dll stdcall';

function LimitedProcessImage(ProcessId: LongWord): String;
var Handle, Size: LongWord; Buffer: String;
begin
  Result := '';
  if ProcessId = 0 then Exit;
  { WMI can omit a same-user sandboxed child's image path. Query only this
    metadata with minimum rights; no memory/command line or other data access. }
  Handle := ProcessOpen($1000, 0, ProcessId);
  if Handle = 0 then Exit;
  try
    if ProcessIdentity(Handle) <> ProcessId then Exit;
    Size := 32768;
    Buffer := StringOfChar(#0, Size);
    if ProcessImageName(Handle, 0, Buffer, Size) = 0 then Exit;
    if (Size = 0) or (Size >= 32768) then Exit;
    Result := Copy(Buffer, 1, Size);
  finally ProcessClose(Handle); end;
end;

function OwnedDirectoryProcessPreflight(const Directory: String): String;
var
  Locator, Services, Processes, Process, ImageValue: Variant;
  Prefix, Image: String;
  Count, Index: Integer;
  ProcessId: LongWord;
begin
  Result := CustomMessage('ScanFailed');
  try
    Prefix := AddBackslash(NormalizedPath(Directory));
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Services := Locator.ConnectServer('.', 'root\CIMV2');
    { Read executable paths only; never stop another program or inspect profiles. }
    Processes := Services.ExecQuery(
      'SELECT ProcessId, ExecutablePath FROM Win32_Process WHERE Name = ''max.exe'' ' +
      'OR Name = ''max-service.exe'' OR Name = ''ValMAX.exe'' ' +
      'OR Name = ''MAX Mod.exe'' OR Name = ''valmax-transfer.exe'' ' +
      'OR Name = ''msedgewebview2.exe'' OR Name = ''QtWebEngineProcess.exe''', 'WQL', 0);
    Count := Processes.Count;
    if (Count < 0) or (Count > 4096) then Exit;
    for Index := 0 to Count - 1 do
    begin
      Process := Processes.ItemIndex(Index);
      ImageValue := Process.ExecutablePath;
#if ValMAXInstallerFixture == "1"
  #ifdef FixtureForcePathFallback
      if True then
  #else
      if VarIsNull(ImageValue) or VarIsEmpty(ImageValue) then
  #endif
#else
      if VarIsNull(ImageValue) or VarIsEmpty(ImageValue) then
#endif
      begin
        ProcessId := Process.ProcessId;
        Image := LimitedProcessImage(ProcessId);
      end
      else Image := ImageValue;
      if Image = '' then Exit;
      Image := NormalizedPath(Image);
      if CompareText(Copy(Image, 1, Length(Prefix)), Prefix) = 0 then
      begin
        Result := CustomMessage('CloseClient');
        Exit;
      end;
    end;
    Result := '';
  except
    Result := CustomMessage('ScanFailed');
  end;
end;

function OwnedProcessPreflight: String;
begin
  Result := OwnedDirectoryProcessPreflight(ExpandConstant('{app}'));
end;

#include "managed-upgrade.iss"

function InitializeUninstall: Boolean;
var
  Message: String;
  Entries: Integer;
begin
  Message := CustomMessage('UpgradeUnsafe');
  if LockTransfer then Message := OwnedProcessPreflight;
  Entries := 0;
  if (Message = '') and not PlainTree(ExpandConstant('{app}'), 0, Entries) then
    Message := CustomMessage('UpgradeUnsafe');
  Result := Message = '';
  if not Result then
  begin
    UnlockTransfer;
    SuppressibleMsgBox(Message, mbError, MB_OK, IDOK);
  end;
end;

procedure DeinitializeUninstall;
begin
  UnlockTransfer;
end;
