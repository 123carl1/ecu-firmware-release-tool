#ifndef MyAppVersion
  #error MyAppVersion must be supplied by the build
#endif
#define MyAppName "ECU Firmware Release Tool"
#define MyAppPublisher "Internal Engineering"

[Setup]
AppId={{63A6F055-819E-45D3-B84A-47C57B140234}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}.0
AppPublisher={#MyAppPublisher}
UninstallDisplayName={#MyAppName}
DefaultDirName={autopf}\EcuReleaseTool
DefaultGroupName=EcuReleaseTool
OutputDir=..\dist\installer
OutputBaseFilename=EcuReleaseTool_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
CloseApplications=no
RestartApplications=no

[Files]
Source: "..\dist\windows\EcuReleaseTool.exe"; DestDir: "{app}"
Source: "..\dist\windows\EcuReleaseCLI.exe"; DestDir: "{app}"
Source: "check_running_processes.ps1"; Flags: dontcopy
Source: "check_running_processes.ps1"; DestDir: "{app}"

[Icons]
Name: "{group}\EcuReleaseTool"; Filename: "{app}\EcuReleaseTool.exe"
Name: "{autodesktop}\EcuReleaseTool"; Filename: "{app}\EcuReleaseTool.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\EcuReleaseTool.exe"; Description: "Launch EcuReleaseTool"; Flags: nowait postinstall skipifsilent; Check: not IsAutoUpdate
Filename: "{app}\EcuReleaseTool.exe"; Flags: nowait skipifnotsilent; Check: IsAutoUpdate

[Code]
const
  PROCESS_SYNCHRONIZE = $00100000;
  WAIT_OBJECT_0 = 0;
  WAIT_TIMEOUT = 258;
  WAIT_FAILED = $FFFFFFFF;
  ParentWaitTimeoutMs = 60000;
  UninstallRegistryKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{63A6F055-819E-45D3-B84A-47C57B140234}_is1';

var
  AutoUpdateMode: Boolean;
  ParentPid: Integer;
  CommandLineError: String;

function OpenProcess(dwDesiredAccess: LongWord; bInheritHandle: Boolean;
  dwProcessId: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(hHandle: THandle; dwMilliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function IsAutoUpdate: Boolean;
begin
  Result := AutoUpdateMode;
end;

function ParseUpdateCommandLine: Boolean;
var
  I: Integer;
  Arg: String;
  UpperArg: String;
  ParentValue: String;
  AutoUpdateSeen: Boolean;
  ParentPidSeen: Boolean;
begin
  Result := False;
  AutoUpdateMode := False;
  ParentPid := 0;
  AutoUpdateSeen := False;
  ParentPidSeen := False;
  CommandLineError := '';

  for I := 1 to ParamCount do
  begin
    Arg := ParamStr(I);
    UpperArg := UpperCase(Arg);
    if UpperArg = '/AUTO_UPDATE' then
    begin
      if AutoUpdateSeen then
      begin
        CommandLineError := '自动更新参数重复。';
        Exit;
      end;
      AutoUpdateSeen := True;
      AutoUpdateMode := True;
    end
    else if Pos('/PARENT_PID=', UpperArg) = 1 then
    begin
      if ParentPidSeen then
      begin
        CommandLineError := '父进程 PID 参数重复。';
        Exit;
      end;
      ParentPidSeen := True;
      ParentValue := Copy(Arg, Length('/PARENT_PID=') + 1, MaxInt);
      ParentPid := StrToIntDef(ParentValue, 0);
      if ParentPid <= 0 then
      begin
        CommandLineError := 'PARENT_PID 必须为正整数。';
        Exit;
      end;
    end
    else if (Pos('/AUTO_UPDATE', UpperArg) = 1) or
            (Pos('/PARENT_PID', UpperArg) = 1) then
    begin
      CommandLineError := '自动更新参数格式无效。';
      Exit;
    end;
  end;

  if AutoUpdateMode and ((not ParentPidSeen) or (ParentPid <= 0)) then
  begin
    CommandLineError := '自动更新缺少有效的父进程 PID。';
    Exit;
  end;

  if ParentPidSeen and (not AutoUpdateMode) then
  begin
    CommandLineError := 'PARENT_PID 只能与 AUTO_UPDATE 同时使用。';
    Exit;
  end;

  Result := True;
end;

function InitializeSetup: Boolean;
begin
  Result := ParseUpdateCommandLine;
  if not Result then
    SuppressibleMsgBox(CommandLineError, mbCriticalError, MB_OK, IDOK);
end;

function WaitForParentProcess: String;
var
  ProcessHandle: THandle;
  WaitResult: LongWord;
begin
  Result := '';
  if not AutoUpdateMode then
    Exit;

  ProcessHandle := OpenProcess(PROCESS_SYNCHRONIZE, False, ParentPid);
  if ProcessHandle = 0 then
    Exit;

  try
    WaitResult := WaitForSingleObject(ProcessHandle, ParentWaitTimeoutMs);
    if WaitResult = WAIT_TIMEOUT then
      Result := '等待旧版 ECU 发布工具退出超时（60 秒），已取消自动更新。'
    else if WaitResult <> WAIT_OBJECT_0 then
      Result := '等待旧版 ECU 发布工具退出失败，已取消自动更新。';
  finally
    CloseHandle(ProcessHandle);
  end;
end;

function RunProcessGuard(const InstallDir: String; ExcludedPid: Integer;
  UseInstalledScript: Boolean): Integer;
var
  ScriptPath: String;
  InstalledScriptPath: String;
  Params: String;
  ResultCode: Integer;
begin
  Result := 11;
  InstalledScriptPath := ExpandConstant('{app}\check_running_processes.ps1');
  if UseInstalledScript then
    ScriptPath := InstalledScriptPath
  else
    ScriptPath := ExpandConstant('{tmp}\check_running_processes.ps1');
  if (not UseInstalledScript) and (not FileExists(ScriptPath)) then
    ExtractTemporaryFile('check_running_processes.ps1');

  if not FileExists(ScriptPath) then
    Exit;

  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
    AddQuotes(ScriptPath) + ' -InstallDir ' + AddQuotes(InstallDir);
  if ExcludedPid > 0 then
    Params := Params + ' -ExcludedPid ' + IntToStr(ExcludedPid);

  if Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := ResultCode;
end;

function RunVersionPolicy(const CandidateVersion, InstalledVersion,
  InstalledVersionSource: String; IsAutomatic: Boolean): Integer;
var
  ScriptPath: String;
  Params: String;
  ResultCode: Integer;
begin
  Result := 12;
  ScriptPath := ExpandConstant('{tmp}\check_running_processes.ps1');
  if not FileExists(ScriptPath) then
    ExtractTemporaryFile('check_running_processes.ps1');
  if not FileExists(ScriptPath) then
    Exit;

  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
    AddQuotes(ScriptPath) + ' -CandidateVersion ' + AddQuotes(CandidateVersion) +
    ' -InstalledVersion ' + AddQuotes(InstalledVersion) +
    ' -InstalledVersionSource ' + AddQuotes(InstalledVersionSource);
  if IsAutomatic then
    Params := Params + ' -AutoUpdate';

  if Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := ResultCode;
end;

function ReadInstalledVersion(var InstalledVersion,
  InstalledVersionSource: String): Boolean;
begin
  Result := RegQueryStringValue(HKCU, UninstallRegistryKey,
    'DisplayVersion', InstalledVersion);
  if not Result then
  begin
    Result := GetVersionNumbersString(ExpandConstant('{app}\EcuReleaseTool.exe'),
      InstalledVersion);
    if Result then
      InstalledVersionSource := 'PeFile';
  end
  else
    InstalledVersionSource := 'Registry';
end;

function CheckInstalledVersion: String;
var
  InstalledVersion: String;
  InstalledVersionSource: String;
  PolicyResult: Integer;
begin
  Result := '';
  if not ReadInstalledVersion(InstalledVersion, InstalledVersionSource) then
    Exit;

  PolicyResult := RunVersionPolicy('{#MyAppVersion}', InstalledVersion,
    InstalledVersionSource, AutoUpdateMode);
  if PolicyResult = 13 then
    Result := '已安装版本高于当前安装包，不允许降级安装。'
  else if PolicyResult = 14 then
    Result := '自动更新不允许重复安装相同版本。'
  else if PolicyResult <> 0 then
    Result := '无法解析安装包版本或已安装版本，已取消覆盖安装。';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  GuardResult: Integer;
begin
  Result := WaitForParentProcess;
  if Result <> '' then
    Exit;

  if CheckForMutexes('Local\EcuFirmwareReleaseTool.Run') then
  begin
    Result := 'ECU 发布工具仍在运行，请先关闭后再安装。';
    Exit;
  end;

  GuardResult := RunProcessGuard(ExpandConstant('{app}'), 0, False);
  if GuardResult = 10 then
  begin
    Result := '安装目录内仍有 ECU 发布工具进程，请先关闭后再安装。';
    Exit;
  end;
  if GuardResult <> 0 then
  begin
    Result := '无法确认 ECU 发布工具进程状态，已取消安装。';
    Exit;
  end;

  Result := CheckInstalledVersion;
end;

function InitializeUninstall: Boolean;
var
  GuardResult: Integer;
begin
  Result := False;
  if CheckForMutexes('Local\EcuFirmwareReleaseTool.Run') then
  begin
    MsgBox('ECU 发布工具仍在运行，请先关闭后再卸载。', mbCriticalError, MB_OK);
    Exit;
  end;

  GuardResult := RunProcessGuard(ExpandConstant('{app}'), 0, True);
  if GuardResult = 10 then
    MsgBox('安装目录内仍有 ECU 发布工具进程，请先关闭后再卸载。',
      mbCriticalError, MB_OK)
  else if GuardResult <> 0 then
    MsgBox('无法确认 ECU 发布工具进程状态，已取消卸载。',
      mbCriticalError, MB_OK)
  else
    Result := True;
end;
