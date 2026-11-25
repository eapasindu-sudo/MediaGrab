; Inno Setup script for MediaGrab. Built automatically by .github/workflows/release.yml
#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif

[Setup]
AppId={{6C1B1E4A-6F43-4C8B-9E0A-4D3B8A7F2C11}
AppName=MediaGrab
AppVersion={#MyAppVersion}
AppPublisher=MediaGrab
DefaultDirName={localappdata}\Programs\MediaGrab
DefaultGroupName=MediaGrab
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=MediaGrab-Setup
SetupIconFile=mediagrab.ico
UninstallDisplayIcon={app}\MediaGrab.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\MediaGrab.exe"; DestDir: "{app}"; Flags: ignoreversion
; yt-dlp updates itself later, so never overwrite a newer copy the user already has
Source: "bin\yt-dlp.exe"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "bin\ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "bin\ffprobe.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "extension\*"; DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MediaGrab"; Filename: "{app}\MediaGrab.exe"
Name: "{group}\Uninstall MediaGrab"; Filename: "{uninstallexe}"
Name: "{userdesktop}\MediaGrab"; Filename: "{app}\MediaGrab.exe"; Tasks: desktopicon

[Run]
; normal install: offer to start; silent self-update: always start again
Filename: "{app}\MediaGrab.exe"; Description: "Start MediaGrab"; Flags: nowait postinstall skipifsilent
Filename: "{app}\MediaGrab.exe"; Flags: nowait; Check: WizardSilent
