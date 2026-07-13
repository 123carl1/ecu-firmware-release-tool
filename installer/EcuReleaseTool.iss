#define MyAppName "E68 LIN - AS5PR CAN Internal Release Tool"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Internal Engineering"

[Setup]
AppId={{63A6F055-819E-45D3-B84A-47C57B140234}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
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

[Files]
Source: "..\dist\windows\EcuReleaseTool.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\windows\EcuReleaseCLI.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\EcuReleaseTool"; Filename: "{app}\EcuReleaseTool.exe"
Name: "{autodesktop}\EcuReleaseTool"; Filename: "{app}\EcuReleaseTool.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\EcuReleaseTool.exe"; Description: "Launch EcuReleaseTool"; Flags: nowait postinstall skipifsilent
