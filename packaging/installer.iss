#ifndef AppVersion
  #error AppVersion must be supplied from core.version.VERSION
#endif

[Setup]
AppId={{BC21AEB9-9DD6-4C43-95A9-E5732B8EE481}
AppName=B2B Contact Finder
AppVersion={#AppVersion}
AppPublisher=B2B Contact Finder
AppPublisherURL=https://github.com/foden303/find_contract
DefaultDirName={localappdata}\Programs\B2BContactFinder
DefaultGroupName=B2B Contact Finder
DisableProgramGroupPage=yes
DisableDirPage=yes
UsePreviousAppDir=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
AppMutex=Local\B2BContactFinder
SetupMutex=Local\B2BContactFinderSetup
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\B2BContactFinder.exe
SetupIconFile=..\build\assets\finder.ico
OutputDir=..\dist\release
OutputBaseFilename=B2BContactFinder-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes

[Tasks]
Name: desktopicon; Description: "Create a &Desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
; Only obsolete bundled runtime files, never the separate user-data directory.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\B2BContactFinder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\B2B Contact Finder"; Filename: "{app}\B2BContactFinder.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\B2B Contact Finder"; Filename: "{app}\B2BContactFinder.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\B2BContactFinder.exe"; Description: "Launch B2B Contact Finder"; Flags: nowait postinstall skipifsilent

[Code]
var
  PurgeData: Boolean;

function NativeFileAttributes(Name: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';

procedure DeleteUserDataWithoutFollowingLinks(Directory: String);
var
  Entry: TFindRec;
  Path: String;
  Attributes: Cardinal;
begin
  Attributes := NativeFileAttributes(Directory);
  if (Attributes = $FFFFFFFF) or ((Attributes and $400) <> 0) then
    exit;
  if FindFirst(Directory + '\*', Entry) then begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
          Path := Directory + '\' + Entry.Name;
          { Junctions, symlinks and other reparse points are never traversed. }
          if (Entry.Attributes and $400) = 0 then begin
            if (Entry.Attributes and $10) <> 0 then
              DeleteUserDataWithoutFollowingLinks(Path)
            else
              DeleteFile(Path);
          end;
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
  RemoveDir(Directory);
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  PurgeData := False;
  if not UninstallSilent then
    PurgeData := MsgBox(
      'Also permanently delete this Windows user''s saved settings, API key, history, uploads and logs in:' + #13#10 +
      ExpandConstant('{localappdata}\B2BContactFinder') + #13#10#13#10 +
      'Choose No to keep your data (recommended). Custom data locations and original source files are always kept.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and PurgeData then begin
    DeleteUserDataWithoutFollowingLinks(ExpandConstant('{localappdata}\B2BContactFinder'));
    if DirExists(ExpandConstant('{localappdata}\B2BContactFinder')) then
      MsgBox('Some user data could not be removed (for example linked or locked files). Remaining data was retained.', mbInformation, MB_OK);
  end;
end;
