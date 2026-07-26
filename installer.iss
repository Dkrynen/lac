; InnoSetup script for LAC — local AI, sorted.
; Build the .exe first with: pyinstaller build.spec
; Then compile: iscc installer.iss

#define MyAppName "LAC"
#define MyAppVersion "2.7.0"
#define MyAppPublisher "Duan Krynen"
#define MyAppURL "https://github.com/Dkrynen/lac"
#define MyAppExeName "lac.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=dist
OutputBaseFilename=LAC-Setup-{#MyAppVersion}
SetupIconFile=assets\app-icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce
Name: "autostart"; Description: "Start {#MyAppName} when &Windows starts"; GroupDescription: "Startup options:"; Flags: unchecked
Name: "addtopath"; Description: "Add {#MyAppName} to &PATH (recommended for the lac command)"; GroupDescription: "Command line:"

[Files]
; One-dir PyInstaller build: dist\lac\ is a folder (lac.exe + its deps), not
; a single exe — ship the whole folder so lac.exe finds its deps next to it.
Source: "dist\lac\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\GETTING_STARTED.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[InstallDelete]
; Vite hashes web assets, so remove stale bundles before copying the fresh UI.
Type: files; Name: "{app}\_internal\web\dist\assets\*"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Getting Started"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\docs\GETTING_STARTED.md"""
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/f /im {#MyAppExeName}"; Flags: runhidden

[Code]
const
  MachineEnvironmentKey =
    'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';
  LacRegistryKey = 'Software\LAC';
  LacPathMarkerName = 'LacOwnedPath';
  ErrorSuccess = 0;
  ErrorFileNotFound = 2;
  RegSz = 1;
  RegExpandSz = 2;
  RrfRtRegSz = $00000002;
  RrfRtRegExpandSz = $00000004;
  RrfNoExpand = $10000000;

function RegGetValue(
  RootKey: Integer;
  const SubKeyName, ValueName: String;
  Flags: Cardinal;
  var ValueType: Cardinal;
  Data: LongWord;
  var DataSize: Cardinal): Longint;
external 'RegGetValueW@advapi32.dll stdcall';

#include "installer\path_helpers.iss"

function ReadMachinePath(
  var PathValue: String;
  var ValueType: Cardinal;
  var Exists: Boolean): Boolean;
var
  DataSize: Cardinal;
  Status: Longint;
begin
  Result := False;
  PathValue := '';
  ValueType := RegExpandSz;
  Exists := False;
  DataSize := 0;

  Status := RegGetValue(
    HKEY_LOCAL_MACHINE,
    MachineEnvironmentKey,
    'Path',
    RrfRtRegSz or RrfRtRegExpandSz or RrfNoExpand,
    ValueType,
    0,
    DataSize);

  if Status = ErrorFileNotFound then
  begin
    if RegValueExists(
      HKEY_LOCAL_MACHINE, MachineEnvironmentKey, 'Path') then
    begin
      Log('Machine PATH exists but is not a supported string value.');
      Exit;
    end;
    Result := True;
    Exit;
  end;

  if Status <> ErrorSuccess then
  begin
    Log(
      'Could not inspect the machine PATH type (Windows error ' +
      IntToStr(Status) + ').');
    Exit;
  end;

  Exists := True;
  if (ValueType <> RegSz) and (ValueType <> RegExpandSz) then
  begin
    Log('Machine PATH has an unsupported registry type.');
    Exit;
  end;

  if not RegQueryStringValue(
    HKEY_LOCAL_MACHINE, MachineEnvironmentKey, 'Path', PathValue) then
  begin
    Log('Machine PATH exists but could not be read.');
    Exit;
  end;

  Result := True;
end;

function WriteMachinePath(
  const PathValue: String;
  ValueType: Cardinal): Boolean;
begin
  if ValueType = RegSz then
  begin
    Result := RegWriteStringValue(
      HKEY_LOCAL_MACHINE, MachineEnvironmentKey, 'Path', PathValue);
  end
  else if ValueType = RegExpandSz then
  begin
    Result := RegWriteExpandStringValue(
      HKEY_LOCAL_MACHINE, MachineEnvironmentKey, 'Path', PathValue);
  end
  else
  begin
    Result := False;
  end;
end;

function TryWriteMachinePathIfUnchanged(
  const ExpectedPath: String;
  ExpectedType: Cardinal;
  const NewPath: String;
  var ConcurrentChange: Boolean): Boolean;
var
  LatestPath: String;
  LatestType: Cardinal;
  LatestExists: Boolean;
begin
  Result := False;
  ConcurrentChange := False;
  if not ReadMachinePath(LatestPath, LatestType, LatestExists) then
  begin
    Exit;
  end;

  if (not LatestExists) or
     (LatestType <> ExpectedType) or
     (LatestPath <> ExpectedPath) then
  begin
    ConcurrentChange := True;
    Exit;
  end;

  Result := WriteMachinePath(NewPath, ExpectedType);
end;

function ReadOwnedPath(var OwnedPath: String): Boolean;
begin
  Result := RegQueryStringValue(
    HKEY_LOCAL_MACHINE_32,
    LacRegistryKey,
    LacPathMarkerName,
    OwnedPath);
end;

function WriteOwnedPath(const OwnedPath: String): Boolean;
begin
  Result := RegWriteStringValue(
    HKEY_LOCAL_MACHINE_32,
    LacRegistryKey,
    LacPathMarkerName,
    OwnedPath);
end;

function ClearOwnedPath: Boolean;
begin
  Result := RegDeleteValue(
    HKEY_LOCAL_MACHINE_32, LacRegistryKey, LacPathMarkerName);
  RegDeleteKeyIfEmpty(HKEY_LOCAL_MACHINE_32, LacRegistryKey);
end;

procedure ReconcileLacPath;
var
  CurrentPath, OriginalPath, UpdatedPath, NewPath, PreviousOwnedPath: String;
  ValueType: Cardinal;
  Exists, HadOwnedPath, Removed, AddedNewPath, PathChanged: Boolean;
  ConcurrentChange: Boolean;
  Attempt: Integer;
begin
  for Attempt := 1 to 3 do
  begin
    if not ReadMachinePath(CurrentPath, ValueType, Exists) then
    begin
      RaiseException(
        'LAC did not change PATH because the existing value could not be read safely.');
    end;
    if not Exists then
    begin
      RaiseException(
        'LAC did not change PATH because the machine PATH value is absent.');
    end;

    OriginalPath := CurrentPath;
    UpdatedPath := CurrentPath;
    NewPath := ExpandConstant('{app}');
    HadOwnedPath := ReadOwnedPath(PreviousOwnedPath);

    if HadOwnedPath and
       (NormalizePathEntry(PreviousOwnedPath) =
        NormalizePathEntry(NewPath)) and
       PathContainsEntry(UpdatedPath, NewPath) then
    begin
      Log('The installer-owned LAC PATH entry is already current.');
      Exit;
    end;

    if HadOwnedPath then
    begin
      UpdatedPath := RemoveFirstPathEntry(
        UpdatedPath, PreviousOwnedPath, Removed);
    end;

    AddedNewPath := not PathContainsEntry(UpdatedPath, NewPath);
    if AddedNewPath then
    begin
      UpdatedPath := AppendPathEntry(UpdatedPath, NewPath);
    end;
    PathChanged := UpdatedPath <> OriginalPath;

    if PathChanged and not TryWriteMachinePathIfUnchanged(
      OriginalPath, ValueType, UpdatedPath, ConcurrentChange) then
    begin
      if ConcurrentChange then
      begin
        Log('PATH changed concurrently; retrying LAC reconciliation.');
        Continue;
      end;
      RaiseException('LAC could not update PATH safely.');
    end;

    if AddedNewPath then
    begin
      if not WriteOwnedPath(NewPath) then
      begin
        if PathChanged and not TryWriteMachinePathIfUnchanged(
          UpdatedPath, ValueType, OriginalPath, ConcurrentChange) then
        begin
          RaiseException(
            'LAC could not record PATH ownership or safely restore PATH.');
        end;
        RaiseException('LAC could not record ownership of its PATH entry.');
      end;
      Log('Added and recorded the LAC installation directory in PATH.');
    end
    else
    begin
      if HadOwnedPath and not ClearOwnedPath then
      begin
        if PathChanged and not TryWriteMachinePathIfUnchanged(
          UpdatedPath, ValueType, OriginalPath, ConcurrentChange) then
        begin
          RaiseException(
            'LAC could not clear PATH ownership or safely restore PATH.');
        end;
        RaiseException('LAC could not clear its previous PATH ownership record.');
      end;
      Log('LAC already exists in PATH; no new entry was claimed.');
    end;
    Exit;
  end;

  RaiseException(
    'LAC did not change PATH because it was modified repeatedly by another process.');
end;

procedure RemoveLacFromPath(FailClosed: Boolean);
var
  OwnedPath, CurrentPath, UpdatedPath: String;
  ValueType: Cardinal;
  Exists, Removed, ConcurrentChange: Boolean;
begin
  if not ReadOwnedPath(OwnedPath) then
  begin
    Exit;
  end;

  if not ReadMachinePath(CurrentPath, ValueType, Exists) then
  begin
    if FailClosed then
    begin
      RaiseException(
        'LAC kept its PATH entry because PATH could not be read safely.');
    end;
    Log('Could not read PATH safely; retaining the LAC ownership record.');
    Exit;
  end;

  if not Exists then
  begin
    if ClearOwnedPath then
    begin
      Log('Machine PATH is absent; cleared the stale LAC ownership record.');
    end
    else
    begin
      if FailClosed then
      begin
        RaiseException(
          'LAC could not clear its stale PATH ownership record.');
      end;
      Log('Machine PATH is absent but the stale LAC ownership record remains.');
    end;
    Exit;
  end;

  UpdatedPath := RemoveFirstPathEntry(CurrentPath, OwnedPath, Removed);
  if not Removed then
  begin
    if ClearOwnedPath then
    begin
      Log('The installer-owned LAC PATH entry was already absent.');
    end
    else
    begin
      if FailClosed then
      begin
        RaiseException(
          'LAC could not clear its stale PATH ownership record.');
      end;
      Log('The LAC PATH entry is absent but its ownership record remains.');
    end;
    Exit;
  end;

  if not TryWriteMachinePathIfUnchanged(
    CurrentPath, ValueType, UpdatedPath, ConcurrentChange) then
  begin
    if FailClosed then
    begin
      if ConcurrentChange then
      begin
        RaiseException(
          'LAC kept its PATH entry because PATH changed concurrently.');
      end;
      RaiseException('LAC could not remove its PATH entry safely.');
    end;
    Log('Could not remove LAC from PATH; retaining the ownership record.');
    Exit;
  end;

  if not ClearOwnedPath then
  begin
    if FailClosed then
    begin
      if not TryWriteMachinePathIfUnchanged(
        UpdatedPath, ValueType, CurrentPath, ConcurrentChange) then
      begin
        RaiseException(
          'LAC could not clear PATH ownership or safely restore PATH.');
      end;
      RaiseException(
        'LAC restored PATH because its ownership record could not be cleared.');
    end;
    Log('Removed LAC from PATH but could not clear the ownership record.');
    Exit;
  end;
  Log('Removed the installer-owned LAC PATH entry.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('addtopath') then
    begin
      ReconcileLacPath;
    end
    else
    begin
      RemoveLacFromPath(True);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveLacFromPath(False);
  end;
end;
