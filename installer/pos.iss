; ============================================================================
;  XT POS - Windows installer (Inno Setup 6.1+)
;
;  On install this will:
;    1. Ask for the MariaDB admin (root) username, password and port.
;    2. Download the MariaDB MSI and install it silently as a Windows service
;       (skipped automatically if MariaDB is already installed).
;    3. Create the 'pos_db' database, the app's DB user, and all tables.
;    4. Install POS.exe with Start-Menu and Desktop shortcuts.
;
;  Build it with:  installer\build-installer.bat   (needs Inno Setup's ISCC.exe)
;  Requires the compiled single-file app at  ..\dist\POS.exe  (run build.bat first).
; ============================================================================

#define AppName "XT POS"
; Version is read from the repo's single source of truth (..\VERSION), so a
; rebuild after `bump_version.py` automatically stamps the right number here.
#define AppVersion Trim(FileRead(FileOpen("..\VERSION")))
#define AppPublisher "Xonal Tech"
#define AppExe "POS.exe"

; ---- MariaDB to download. Bump the version + URL together when updating. ----
#define MariaDBVersion "11.4.4"
#define MariaDBUrl "https://archive.mariadb.org/mariadb-11.4.4/winx64-packages/mariadb-11.4.4-winx64.msi"

[Setup]
AppId={{B6F1B6A2-3C8E-4E2A-9D4F-POS0000000001}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\XTPOS
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=XTPOS-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
; The compiled single-file app + its version stamp (run build.bat first).
Source: "..\dist\POS.exe";     DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\version.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}";            Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}";  Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";      Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
; Launch the POS after a successful install (optional, user can untick).
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\.env"

; ============================================================================
[Code]
var
  CredPage: TInputQueryWizardPage;
  StoreName: string;

procedure InitializeWizard();
begin
  { Custom page that collects the DB admin credentials + port + store name. }
  CredPage := CreateInputQueryPage(wpSelectDir,
    'Database setup',
    'Enter the database administrator account to create.',
    'MariaDB will be installed locally. Choose the admin (root) account password' + #13#10 +
    'the POS will use. Keep these safe - you will need them for maintenance.');

  CredPage.Add('Admin username:', False);          { index 0 }
  CredPage.Add('Admin password:', True);           { index 1 - masked }
  CredPage.Add('Confirm password:', True);         { index 2 - masked }
  CredPage.Add('Database port:', False);           { index 3 }
  CredPage.Add('Store / shop name:', False);       { index 4 }

  CredPage.Values[0] := 'root';
  CredPage.Values[3] := '3306';
  CredPage.Values[4] := 'My Shop';
end;

function MariaDBInstalled(): Boolean;
begin
  { MariaDB registers a service; detect any existing install to skip download. }
  Result := RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\MariaDB') or
            RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\MySQL');
end;

function DownloadMariaDB(): Boolean; forward;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = CredPage.ID then
  begin
    if Trim(CredPage.Values[0]) = '' then
    begin
      MsgBox('Please enter an admin username.', mbError, MB_OK);
      Result := False; Exit;
    end;
    if CredPage.Values[1] = '' then
    begin
      MsgBox('Please enter a password.', mbError, MB_OK);
      Result := False; Exit;
    end;
    if CredPage.Values[1] <> CredPage.Values[2] then
    begin
      MsgBox('The passwords do not match.', mbError, MB_OK);
      Result := False; Exit;
    end;
    if Trim(CredPage.Values[3]) = '' then
      CredPage.Values[3] := '3306';
  end
  else if CurPageID = wpReady then
  begin
    { Download MariaDB now, before files are copied. Staying on the Ready
      page if the download fails lets the user retry. }
    Result := DownloadMariaDB();
  end;
end;

function DownloadMariaDB(): Boolean;
var
  DownloadPage: TDownloadWizardPage;
begin
  Result := True;
  if MariaDBInstalled() then
    Exit;  { already present - nothing to download }

  DownloadPage := CreateDownloadPage('Downloading MariaDB',
    'Fetching the database engine ({#MariaDBVersion}). This may take a few minutes.', nil);
  DownloadPage.Clear;
  DownloadPage.Add('{#MariaDBUrl}', 'mariadb.msi', '');
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
      Result := True;
    except
      MsgBox('Could not download MariaDB:' + #13#10 + GetExceptionMessage + #13#10 +
             'Check your internet connection and try again.', mbError, MB_OK);
      Result := False;
    end;
  finally
    DownloadPage.Hide;
  end;
end;

function InstallMariaDB(): Boolean;
var
  ResultCode: Integer;
  MsiPath, Params, Pwd, Port: string;
begin
  Result := True;
  if MariaDBInstalled() then
    Exit;

  Pwd := CredPage.Values[1];
  Port := CredPage.Values[3];
  MsiPath := ExpandConstant('{tmp}\mariadb.msi');

  { Silent MSI install: set root password, port, UTF-8, install as a service. }
  Params := '/i "' + MsiPath + '" /qn /norestart' +
            ' SERVICENAME=MariaDB' +
            ' PORT=' + Port +
            ' PASSWORD="' + Pwd + '"' +
            ' UTF8=1';

  WizardForm.StatusLabel.Caption := 'Installing MariaDB (this can take a few minutes)...';
  if not Exec('msiexec.exe', Params, '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('Failed to start the MariaDB installer.', mbError, MB_OK);
    Result := False; Exit;
  end;
  if ResultCode <> 0 then
  begin
    MsgBox('MariaDB installation failed (code ' + IntToStr(ResultCode) + ').', mbError, MB_OK);
    Result := False; Exit;
  end;

  { Give the freshly-installed service a moment to come up. }
  Exec('cmd.exe', '/c net start MariaDB', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(3000);
end;

procedure WriteEnvFile();
var
  Lines: TArrayOfString;
begin
  { Persistent config the compiled app reads at runtime (next to POS.exe).
    Note: the app user's password is stored here in plain text - this is a
    single-machine local POS. Root's password is NOT persisted. }
  SetArrayLength(Lines, 8);
  Lines[0] := 'DB_HOST=127.0.0.1';
  Lines[1] := 'DB_PORT=' + CredPage.Values[3];
  Lines[2] := 'DB_NAME=pos_db';
  Lines[3] := 'DB_USER=' + CredPage.Values[0];
  Lines[4] := 'DB_PASSWORD=' + CredPage.Values[1];
  Lines[5] := 'STORE_NAME=' + CredPage.Values[4];
  Lines[6] := 'CURRENCY=KES';
  Lines[7] := 'TAX_RATE=0';
  SaveStringsToFile(ExpandConstant('{app}\.env'), Lines, False);
end;

function InitializeDatabase(): Boolean;
var
  ResultCode: Integer;
  CfgPath: string;
  Json: TArrayOfString;
begin
  { Hand the credentials to POS.exe --init-db via a temp JSON file so we never
    pass the password on the command line. The exe creates the DB + tables. }
  CfgPath := ExpandConstant('{tmp}\pos-init.json');
  SetArrayLength(Json, 7);
  Json[0] := '{';
  Json[1] := '  "root_password": "' + CredPage.Values[1] + '",';
  Json[2] := '  "db_host": "127.0.0.1",';
  Json[3] := '  "db_port": "' + CredPage.Values[3] + '",';
  Json[4] := '  "db_name": "pos_db",';
  Json[5] := '  "app_user": "' + CredPage.Values[0] + '",';
  Json[6] := '  "app_password": "' + CredPage.Values[1] + '" }';
  SaveStringsToFile(CfgPath, Json, False);

  WizardForm.StatusLabel.Caption := 'Creating the POS database...';
  Result := Exec(ExpandConstant('{app}\{#AppExe}'),
                 '--init-db --config "' + CfgPath + '"',
                 '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  DeleteFile(CfgPath);  { remove the file containing the root password }

  if (not Result) or (ResultCode <> 0) then
  begin
    MsgBox('The database could not be initialized (code ' + IntToStr(ResultCode) + ').' + #13#10 +
           'You can finish setup and run POS.exe --init-db manually later.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { Files are copied and MariaDB is downloaded by now. Install the engine
      (if needed), write config, and create the database. }
    WriteEnvFile();
    if InstallMariaDB() then
      InitializeDatabase();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  { Offer to drop the POS database on uninstall. Runs while the app files (and
    .env credentials) still exist. POS.exe is a GUI exe launched hidden
    (SW_HIDE), so no console/PowerShell window ever appears. }
  if CurUninstallStep = usUninstall then
  begin
    if MsgBox('Also delete all POS data (sales, products and the pos_db database)?' + #13#10 +
              'Choose No to keep your data for a future reinstall.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      if FileExists(ExpandConstant('{app}\{#AppExe}')) then
        Exec(ExpandConstant('{app}\{#AppExe}'), '--drop-db', '',
             SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;
