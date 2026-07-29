[Setup]
AppName=LAC Path Contract
AppVersion=1
DefaultDirName={tmp}\lac-path-contract
PrivilegesRequired=lowest
Uninstallable=no
CreateAppDir=no
OutputBaseFilename=LAC-Path-Contract
Compression=zip

[Code]
#include "..\..\installer\path_helpers.iss"

procedure AssertEqual(
  const CaseName, ExpectedValue, ActualValue: String);
begin
  if ExpectedValue <> ActualValue then
  begin
    RaiseException(
      CaseName + ': expected [' + ExpectedValue + '] but got [' +
      ActualValue + ']');
  end;
end;

procedure AssertTrue(const CaseName: String; Value: Boolean);
begin
  if not Value then
  begin
    RaiseException(CaseName + ': expected True');
  end;
end;

procedure AssertFalse(const CaseName: String; Value: Boolean);
begin
  if Value then
  begin
    RaiseException(CaseName + ': expected False');
  end;
end;

function InitializeSetup: Boolean;
var
  Removed: Boolean;
begin
  AssertEqual(
    'case-insensitive duplicate',
    'C:\Tools;C:\LAC',
    AppendPathEntry('C:\Tools;C:\LAC', 'c:\lac\'));
  AssertEqual(
    'substring is not a duplicate',
    'C:\LAC-tools;C:\LAC',
    AppendPathEntry('C:\LAC-tools', 'C:\LAC'));
  AssertEqual(
    'empty path',
    'C:\LAC',
    AppendPathEntry('', 'C:\LAC'));

  AssertEqual(
    'preserve empty and trailing segments',
    'A;;B;',
    RemoveFirstPathEntry('A;;C:\LAC;B;', 'C:\LAC', Removed));
  AssertTrue('owned segment removed', Removed);

  AssertEqual(
    'quoted path comparison',
    'A;B',
    RemoveFirstPathEntry('A;"C:\LAC\";B', 'c:\lac', Removed));
  AssertTrue('quoted segment removed', Removed);

  AssertEqual(
    'remove one duplicate only',
    'B;c:\lac',
    RemoveFirstPathEntry('C:\LAC;B;c:\lac', 'C:\LAC', Removed));
  AssertTrue('first duplicate removed', Removed);

  AssertEqual(
    'missing segment leaves bytes unchanged',
    'A;;B;',
    RemoveFirstPathEntry('A;;B;', 'C:\LAC', Removed));
  AssertFalse('missing segment not removed', Removed);

  Result := True;
end;
