; Inno Setup script — builds dist\WindowPhotoViewer-v<ver>-setup.exe from the PyInstaller output.
;
;   1) python -m PyInstaller build/viewer.spec --noconfirm --distpath dist --workpath build/work
;   2) ISCC.exe build\installer.iss            (Inno Setup 6: winget install JRSoftware.InnoSetup)
;
; Per-user install by default (no admin needed; lands in %LOCALAPPDATA%\Programs), with the
; option to install for all users. The installer is unsigned: Windows SmartScreen will show
; a "unknown publisher" warning on first run — expected for a hobby build.

; MyAppName is the ASCII identifier (install folder, exe, setup filename);
; MyAppDisplayName is what the wizard, Start menu and Apps & features show.
#define MyAppName "WindowPhotoViewer"
#define MyAppDisplayName "골라보기"
#define MyAppVersion "0.1.2"
#define MyAppPublisher "cys"
#define MyAppURL "https://github.com/choiyongsik/window-photo-viewer"
#define MyAppExeName "WindowPhotoViewer.exe"
#define SourceDir "..\dist\WindowPhotoViewer"

[Setup]
AppId={{8C0E6D6A-4B1F-4E4B-9C2A-1F1B6C3E2D71}
AppName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppDisplayName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppDisplayName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename={#MyAppName}-v{#MyAppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
; Ratings live in the photos themselves; the app keeps only caches/settings locally.
; Uninstall removes the program files; caches under %LOCALAPPDATA%\WindowPhotoViewer are
; removed too (see [UninstallDelete]) — they are regenerated on demand.

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppDisplayName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\WindowPhotoViewer"

[Registry]
; QSettings("WindowPhotoViewer", "WindowPhotoViewer") lives here; cleaned up on uninstall.
Root: HKCU; Subkey: "Software\WindowPhotoViewer"; Flags: uninsdeletekey dontcreatekey
