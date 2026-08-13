#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef CreatorDir
  #define CreatorDir "..\..\artifacts\creator\keywave_creator.dist"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\artifacts\installer"
#endif
#ifndef IconFile
  #define IconFile "..\branding\KeyWave.ico"
#endif
#ifndef CompressionMethod
  #define CompressionMethod "lzma2/max"
#endif
#ifndef SolidCompressionValue
  #define SolidCompressionValue "yes"
#endif

[Setup]
AppId={{8F6C69B4-9D5F-4E28-9D8B-A3733E3714BC}
AppName=KeyWave Creator
AppVersion={#AppVersion}
AppPublisher=KeyWave
DefaultDirName={localappdata}\Programs\KeyWave Creator
DefaultGroupName=KeyWave Creator
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=KeyWave-Creator-{#AppVersion}-windows-x64-setup
SetupIconFile={#IconFile}
Compression={#CompressionMethod}
SolidCompression={#SolidCompressionValue}
WizardStyle=modern dynamic
UninstallDisplayIcon={app}\KeyWave.ico
SetupLogging=yes
MinVersion=10.0.0

[Tasks]
Name: "desktopcreator"; Description: "Create a desktop shortcut for KeyWave Creator"; GroupDescription: "Desktop shortcuts:"

[Files]
Source: "{#IconFile}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#CreatorDir}\*"; DestDir: "{app}\Creator"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
Type: filesandordirs; Name: "{app}\Creator"

[Icons]
Name: "{group}\KeyWave Creator"; Filename: "{app}\Creator\KeyWaveCreator.exe"; IconFilename: "{app}\KeyWave.ico"
Name: "{group}\Uninstall KeyWave Creator"; Filename: "{uninstallexe}"; IconFilename: "{app}\KeyWave.ico"
Name: "{autodesktop}\KeyWave Creator"; Filename: "{app}\Creator\KeyWaveCreator.exe"; IconFilename: "{app}\KeyWave.ico"; Tasks: desktopcreator

[Run]
Filename: "{app}\Creator\KeyWaveCreator.exe"; Description: "Launch KeyWave Creator"; Flags: nowait postinstall skipifsilent unchecked
