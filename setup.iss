; Inno Setup Script für J.A.R.V.I.S
; Erzeugt einen professionellen Windows-Installer mit integrierter Konfiguration
; 
; VOR DEM BUILD:
; 1. Installiere Inno Setup: https://jrsoftware.org/isinfo.php
; 2. Erstelle die EXE: pyinstaller jarvis.spec
; 3. Öffne diese .iss Datei in Inno Setup und kompiliere (F9)

#define MyAppName "JARVIS"
#define MyAppVersion "3.0"
#define MyAppPublisher "Skater1808"
#define MyAppURL "https://github.com/Skater1808/gemini-live-jarvis"
#define MyAppExeName "Jarvis.exe"

[Setup]
AppId={{JARVIS-3-0-GEMINI-LIVE-EMIL-2025}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=LICENSE
PrivilegesRequired=admin
OutputDir=installer_output
OutputBaseFilename=Jarvis-Setup-v{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Files]
; Haupt-EXE
Source: "dist\Jarvis.exe"; DestDir: "{app}"; Flags: ignoreversion

; Frontend-Dateien
Source: "frontend\*"; DestDir: "{app}\frontend"; Flags: ignoreversion recursesubdirs createallsubdirs

; Python-Module (falls als .py Dateien vorhanden)
Source: "browser_tools.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "screen_capture.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "memory.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "quick_notes.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "mcp_client.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "wiki_tools.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion

; README und Lizenz
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion; Check: LicenseFileExists

[Dirs]
; Erstelle Datenverzeichnis für Datenbanken und Logs
Name: "{localappdata}\{#MyAppName}"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Zeige Info nach Installation
Filename: "{app}\README.md"; Description: "README anzeigen"; Flags: postinstall shellexec skipifsilent

; Optional: Starte Jarvis sofort
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent unchecked

[UninstallRun]
; Cleanup bei Deinstallation
Filename: "{cmd}"; Parameters: "/c rmdir /s /q ""{localappdata}\{#MyAppName}"""; RunOnceId: CleanupAppData

[Code]
// ==================== CUSTOM CONFIGURATION PAGE ====================

const
  SW_MAXIMIZE = 3;

// Windows API Funktionen
function ShowWindow(hWnd: HWND; nCmdShow: Integer): BOOL; external 'ShowWindow@user32.dll stdcall';

var
  ConfigPage: TWizardPage;
  UserNameEdit: TEdit;
  AddressEdit: TEdit;
  CityEdit: TEdit;
  ApiKeyEdit: TEdit;
  VoiceCombo: TComboBox;
  BrowserUrlEdit: TEdit;
  SpotifyTrackEdit: TEdit;
  AppsEdit: TEdit;
  QuickNotesEdit: TEdit;

// Hilfsfunktion zum Prüfen ob Lizenzdatei existiert
function LicenseFileExists(): Boolean;
begin
  Result := FileExists(ExpandConstant('{src}\LICENSE'));
end;

// Escape Backslashes für JSON (C:\Program Files -> C:\\Program Files)
function EscapeBackslashes(const S: String): String;
var
  I: Integer;
  ResultStr: String;
begin
  ResultStr := '';
  for I := 1 to Length(S) do
  begin
    if S[I] = '\' then
      ResultStr := ResultStr + '\\'
    else
      ResultStr := ResultStr + S[I];
  end;
  Result := ResultStr;
end;

// Erstelle die Konfigurationsseite
procedure CreateConfigPage;
var
  Y: Integer;
  LabelCtrl: TNewStaticText;
begin
  ConfigPage := CreateCustomPage(wpSelectDir, 'Jarvis Konfiguration', 'Bitte gib die folgenden Informationen ein, um Jarvis einzurichten.');
  
  Y := 0;
  
  // Name
  LabelCtrl := TNewStaticText.Create(ConfigPage);
  LabelCtrl.Parent := ConfigPage.Surface;
  LabelCtrl.Left := 0;
  LabelCtrl.Top := Y;
  LabelCtrl.Width := ConfigPage.Surface.Width;
  LabelCtrl.Caption := 'Dein Name:';
  
  UserNameEdit := TEdit.Create(ConfigPage);
  UserNameEdit.Parent := ConfigPage.Surface;
  UserNameEdit.Left := 0;
  UserNameEdit.Top := Y + 16;
  UserNameEdit.Width := ConfigPage.Surface.Width;
  UserNameEdit.Text := 'Max Mustermann ';
  
  Y := Y + 48;
  
  // Anrede
  LabelCtrl := TNewStaticText.Create(ConfigPage);
  LabelCtrl.Parent := ConfigPage.Surface;
  LabelCtrl.Left := 0;
  LabelCtrl.Top := Y;
  LabelCtrl.Width := ConfigPage.Surface.Width;
  LabelCtrl.Caption := 'Wie soll Jarvis dich ansprechen? (z.B. Sir, Chef, Herr):';
  
  AddressEdit := TEdit.Create(ConfigPage);
  AddressEdit.Parent := ConfigPage.Surface;
  AddressEdit.Left := 0;
  AddressEdit.Top := Y + 16;
  AddressEdit.Width := ConfigPage.Surface.Width;
  AddressEdit.Text := 'Sir';
  
  Y := Y + 48;
  
  // Stadt
  LabelCtrl := TNewStaticText.Create(ConfigPage);
  LabelCtrl.Parent := ConfigPage.Surface;
  LabelCtrl.Left := 0;
  LabelCtrl.Top := Y;
  LabelCtrl.Width := ConfigPage.Surface.Width;
  LabelCtrl.Caption := 'Stadt für Wettervorhersage:';
  
  CityEdit := TEdit.Create(ConfigPage);
  CityEdit.Parent := ConfigPage.Surface;
  CityEdit.Left := 0;
  CityEdit.Top := Y + 16;
  CityEdit.Width := ConfigPage.Surface.Width;
  CityEdit.Text := 'Bremen';
  
  Y := Y + 48;
  
  // API Key
  LabelCtrl := TNewStaticText.Create(ConfigPage);
  LabelCtrl.Parent := ConfigPage.Surface;
  LabelCtrl.Left := 0;
  LabelCtrl.Top := Y;
  LabelCtrl.Width := ConfigPage.Surface.Width;
  LabelCtrl.Caption := 'Gemini API Key (kostenlos bei aistudio.google.com/app/apikey):';
  
  ApiKeyEdit := TEdit.Create(ConfigPage);
  ApiKeyEdit.Parent := ConfigPage.Surface;
  ApiKeyEdit.Left := 0;
  ApiKeyEdit.Top := Y + 16;
  ApiKeyEdit.Width := ConfigPage.Surface.Width;
  ApiKeyEdit.Text := '';
  
  Y := Y + 48;
  
  // Stimme
  LabelCtrl := TNewStaticText.Create(ConfigPage);
  LabelCtrl.Parent := ConfigPage.Surface;
  LabelCtrl.Left := 0;
  LabelCtrl.Top := Y;
  LabelCtrl.Width := ConfigPage.Surface.Width;
  LabelCtrl.Caption := 'Jarvis Stimme:';
  
  VoiceCombo := TComboBox.Create(ConfigPage);
  VoiceCombo.Parent := ConfigPage.Surface;
  VoiceCombo.Left := 0;
  VoiceCombo.Top := Y + 16;
  VoiceCombo.Width := ConfigPage.Surface.Width;
  VoiceCombo.Style := csDropDownList;
  VoiceCombo.Items.Add('Charon - Tief, dunkel (klassischer Butler)');
  VoiceCombo.Items.Add('Puck - Jung, frisch (lebhaft)');
  VoiceCombo.Items.Add('Fenrir - Rau, stark (markant)');
  VoiceCombo.Items.Add('Kore - Klar, weiblich (präzise)');
  VoiceCombo.Items.Add('Aoede - Sanft, weiblich (melodisch)');
  VoiceCombo.ItemIndex := 0;
  
  Y := Y + 48;
  
  // Browser Startseite
  LabelCtrl := TNewStaticText.Create(ConfigPage);
  LabelCtrl.Parent := ConfigPage.Surface;
  LabelCtrl.Left := 0;
  LabelCtrl.Top := Y;
  LabelCtrl.Width := ConfigPage.Surface.Width;
  LabelCtrl.Caption := 'Browser Startseite (URL):';
  
  BrowserUrlEdit := TEdit.Create(ConfigPage);
  BrowserUrlEdit.Parent := ConfigPage.Surface;
  BrowserUrlEdit.Left := 0;
  BrowserUrlEdit.Top := Y + 16;
  BrowserUrlEdit.Width := ConfigPage.Surface.Width;
  BrowserUrlEdit.Text := 'https://www.google.com';
  
  Y := Y + 48;
  
  // Apps (Autostart bei Doppelklatsch)
  LabelCtrl := TNewStaticText.Create(ConfigPage);
  LabelCtrl.Parent := ConfigPage.Surface;
  LabelCtrl.Left := 0;
  LabelCtrl.Top := Y;
  LabelCtrl.Width := ConfigPage.Surface.Width;
  LabelCtrl.Caption := 'Autostart-Apps bei Doppelklatsch (kommagetrennt, z.B. code, obsidian):';
  
  AppsEdit := TEdit.Create(ConfigPage);
  AppsEdit.Parent := ConfigPage.Surface;
  AppsEdit.Left := 0;
  AppsEdit.Top := Y + 16;
  AppsEdit.Width := ConfigPage.Surface.Width;
  AppsEdit.Text := 'code';
  
  Y := Y + 48;
  
  // Spotify Track URI
  LabelCtrl := TNewStaticText.Create(ConfigPage);
  LabelCtrl.Parent := ConfigPage.Surface;
  LabelCtrl.Left := 0;
  LabelCtrl.Top := Y;
  LabelCtrl.Width := ConfigPage.Surface.Width;
  LabelCtrl.Caption := 'Spotify Track URI (optional, z.B. spotify:track:xxxxx):';
  
  SpotifyTrackEdit := TEdit.Create(ConfigPage);
  SpotifyTrackEdit.Parent := ConfigPage.Surface;
  SpotifyTrackEdit.Left := 0;
  SpotifyTrackEdit.Top := Y + 16;
  SpotifyTrackEdit.Width := ConfigPage.Surface.Width;
  SpotifyTrackEdit.Text := '';
  
  Y := Y + 48;
  
  // Quick Notes Pfad
  LabelCtrl := TNewStaticText.Create(ConfigPage);
  LabelCtrl.Parent := ConfigPage.Surface;
  LabelCtrl.Left := 0;
  LabelCtrl.Top := Y;
  LabelCtrl.Width := ConfigPage.Surface.Width;
  LabelCtrl.Caption := 'Quick Notes Dateipfad (optional, leer = deaktiviert):';
  
  QuickNotesEdit := TEdit.Create(ConfigPage);
  QuickNotesEdit.Parent := ConfigPage.Surface;
  QuickNotesEdit.Left := 0;
  QuickNotesEdit.Top := Y + 16;
  QuickNotesEdit.Width := ConfigPage.Surface.Width;
  QuickNotesEdit.Text := '';
end;

// Konvertiere ComboBox-Index zu Voice-Name
function GetSelectedVoice(): String;
begin
  case VoiceCombo.ItemIndex of
    0: Result := 'Charon';
    1: Result := 'Puck';
    2: Result := 'Fenrir';
    3: Result := 'Kore';
    4: Result := 'Aoede';
  else
    Result := 'Charon';
  end;
end;

// Konvertiere kommagetrennte Apps zu JSON-Array
function BuildAppsJson(const AppsText: String): String;
var
  AppList: TStringList;
  I: Integer;
  ResultStr: String;
  AppName: String;
begin
  if AppsText = '' then
  begin
    Result := '[]';
    Exit;
  end;
  
  AppList := TStringList.Create;
  try
    AppList.CommaText := AppsText;
    ResultStr := '[';
    for I := 0 to AppList.Count - 1 do
    begin
      AppName := Trim(AppList[I]);
      if AppName <> '' then
      begin
        if I > 0 then ResultStr := ResultStr + ', ';
        ResultStr := ResultStr + '"' + AppName + '"';
      end;
    end;
    ResultStr := ResultStr + ']';
    Result := ResultStr;
  finally
    AppList.Free;
  end;
end;

// Speichere Config nach Installation
procedure SaveConfigFile;
var
  ConfigPath: String;
  ConfigContent: String;
  Voice: String;
  EscapedPath: String;
  AppsJson: String;
  EscapedNotesPath: String;
begin
  ConfigPath := ExpandConstant('{app}\config.json');
  Voice := GetSelectedVoice();
  EscapedPath := EscapeBackslashes(ExpandConstant('{app}'));
  AppsJson := BuildAppsJson(AppsEdit.Text);
  EscapedNotesPath := EscapeBackslashes(QuickNotesEdit.Text);
  
  ConfigContent := '{' + #13#10 +
    '  "gemini_api_key": "' + ApiKeyEdit.Text + '",' + #13#10 +
    '  "user_name": "' + UserNameEdit.Text + '",' + #13#10 +
    '  "user_address": "' + AddressEdit.Text + '",' + #13#10 +
    '  "city": "' + CityEdit.Text + '",' + #13#10 +
    '  "jarvis_voice": "' + Voice + '",' + #13#10 +
    '  "workspace_path": "' + EscapedPath + '",' + #13#10 +
    '  "browser_url": "' + BrowserUrlEdit.Text + '",' + #13#10 +
    '  "spotify_track": "' + SpotifyTrackEdit.Text + '",' + #13#10 +
    '  "apps": ' + AppsJson + ',' + #13#10 +
    '  "obsidian_inbox_path": "",' + #13#10 +
    '  "quick_notes_path": "' + EscapedNotesPath + '",' + #13#10 +
    '  "wiki_sources": {}' + #13#10 +
    '}';
  
  SaveStringToFile(ConfigPath, ConfigContent, False);
end;

// Initialisierung - darf WizardForm NICHT verwenden
function InitializeSetup(): Boolean;
begin
  Result := true;
end;

// Wizard wird erstellt - HIER dürfen wir Custom Pages erstellen
procedure InitializeWizard();
var
  hWnd: Integer;
begin
  // Maximiere das Setup-Fenster für Vollbildmodus
  hWnd := WizardForm.Handle;
  ShowWindow(hWnd, SW_MAXIMIZE);
  
  CreateConfigPage;
end;

// Nach Installation
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    // Speichere die vom Benutzer eingegebene Config
    SaveConfigFile;
  end;
end;
