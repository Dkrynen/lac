function NormalizePathEntry(Value: String): String;
begin
  Result := Trim(Value);
  if (Length(Result) >= 2) and
     (Result[1] = '"') and (Result[Length(Result)] = '"') then
  begin
    Result := Copy(Result, 2, Length(Result) - 2);
  end;

  while (Length(Result) > 3) and
        ((Result[Length(Result)] = '\') or
         (Result[Length(Result)] = '/')) do
  begin
    Delete(Result, Length(Result), 1);
  end;

  Result := LowerCase(Result);
end;

function NextPathEntry(var Remaining: String; var Finished: Boolean): String;
var
  SeparatorAt: Integer;
begin
  SeparatorAt := Pos(';', Remaining);
  if SeparatorAt = 0 then
  begin
    Result := Remaining;
    Remaining := '';
    Finished := True;
  end
  else
  begin
    Result := Copy(Remaining, 1, SeparatorAt - 1);
    Delete(Remaining, 1, SeparatorAt);
    Finished := False;
  end;
end;

function PathContainsEntry(const PathValue, WantedEntry: String): Boolean;
var
  Remaining, Entry: String;
  Finished: Boolean;
begin
  Result := False;
  Remaining := PathValue;
  Finished := False;
  repeat
    Entry := NextPathEntry(Remaining, Finished);
    if NormalizePathEntry(Entry) = NormalizePathEntry(WantedEntry) then
    begin
      Result := True;
      Exit;
    end;
  until Finished;
end;

function AppendPathEntry(
  const PathValue, WantedEntry: String): String;
begin
  Result := PathValue;
  if PathContainsEntry(Result, WantedEntry) then
  begin
    Exit;
  end;

  if (Result <> '') and (Result[Length(Result)] <> ';') then
  begin
    Result := Result + ';';
  end;
  Result := Result + WantedEntry;
end;

function RemoveFirstPathEntry(
  const PathValue, WantedEntry: String; var Removed: Boolean): String;
var
  Remaining, Entry: String;
  Finished, HasOutputEntry: Boolean;
begin
  Remaining := PathValue;
  Result := '';
  Finished := False;
  HasOutputEntry := False;
  Removed := False;
  repeat
    Entry := NextPathEntry(Remaining, Finished);
    if (not Removed) and
       (NormalizePathEntry(Entry) = NormalizePathEntry(WantedEntry)) then
    begin
      Removed := True;
    end
    else
    begin
      if HasOutputEntry then
      begin
        Result := Result + ';';
      end;
      Result := Result + Entry;
      HasOutputEntry := True;
    end;
  until Finished;
end;
