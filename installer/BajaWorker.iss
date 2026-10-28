; Baja Worker installer (Inno Setup 6). Design §9; infra/tailnet/worker_join.md.
;
; Compiled by `installer/build.py --compile-installer`, which passes:
;   /DAppVersion=0.1.0+...        version shown to people (build.py stamp_version)
;   /DAppNumericVersion=0.1.0.0   four-number Windows file version
;   /DDistDir=...\dist\BajaWorker the PyInstaller onedir output, with ffmpeg\ and nssm.exe
;   /DTailscaleMsi=...\*.msi      the pinned, hash-checked Tailscale MSI (vendor.lock)
;
; What it does, in order (see [Code] CurStepChanged):
;   1. copy the program to C:\Program Files\BajaWorker (one UAC prompt: PrivilegesRequired=admin);
;   2. install Tailscale silently if tailscale.exe is not there yet;
;   3. restrict C:\BajaWorker to SYSTEM + Administrators (it holds the worker's bearer token);
;   4. write the installer's keys into C:\BajaWorker\worker.json (BajaWorker.exe --init-config);
;   5. register and start the NSSM service "BajaWorker";
;   6. open TCP 8710 and 8720 inbound for the tailnet range;
;   7. after Finish: BajaWorker.exe --first-run (setup page, tailscale up, service restart).
; The uninstaller undoes 5, 6 and the Baja tailnet login, and keeps C:\BajaWorker.

#ifndef AppVersion
  #error Pass /DAppVersion=... (installer/build.py does this)
#endif
#ifndef AppNumericVersion
  #error Pass /DAppNumericVersion=...
#endif
#ifndef DistDir
  #error Pass /DDistDir=... (the PyInstaller output folder)
#endif
#ifndef TailscaleMsi
  #error Pass /DTailscaleMsi=... (from installer/vendor)
#endif

#define ServiceName "BajaWorker"
; Initial data folder. Never contains spaces, so it needs no quoting below.
#define DataDir "{sd}\BajaWorker"
#define SetupPageUrl "http://localhost:8700"
#define FileServerRule "Baja Worker file server (TCP 8710)"
#define ReceiverRule "Baja Worker studio receiver (TCP 8720)"
#define TailnetRange "100.64.0.0/10"

[Setup]
; Keep AppId forever: it is how Windows recognises an upgrade of the same app.
AppId={{6F0C2A8E-3B1D-4E57-9A64-2C8D7B1E5F30}
AppName=Baja Worker
AppVersion={#AppVersion}
AppVerName=Baja Worker {#AppVersion}
AppPublisher=Baja video pipeline
VersionInfoVersion={#AppNumericVersion}
DefaultDirName={commonpf64}\BajaWorker
DisableDirPage=yes
DefaultGroupName=Baja Worker
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=BajaWorker-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Baja Worker
UninstallDisplayIcon={app}\BajaWorker.exe
; The service holds files open; we stop it ourselves in PrepareToInstall.
CloseApplications=no
SetupLogging=yes

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#TailscaleMsi}"; DestDir: "{tmp}"; DestName: "tailscale-setup-amd64.msi"; Flags: deleteafterinstall; Check: TailscaleIsMissing

[Dirs]
; Raws live here: never removed by the uninstaller.
Name: "{#DataDir}"; Flags: uninsneveruninstall
Name: "{#DataDir}\logs"; Flags: uninsneveruninstall

[INI]
; A plain internet shortcut, so the Start-menu entry opens the default browser.
Filename: "{app}\Baja Worker.url"; Section: "InternetShortcut"; Key: "URL"; String: "{#SetupPageUrl}"

[Icons]
Name: "{group}\Baja Worker"; Filename: "{app}\Baja Worker.url"; Comment: "Página de configuração e status ({#SetupPageUrl})"
Name: "{group}\Baja Worker - concluir conexão"; Filename: "{app}\BajaWorker.exe"; Parameters: "--first-run --config {#DataDir}\worker.json"; Comment: "Conecta este PC à rede Baja depois de colar o token"
Name: "{group}\Desinstalar Baja Worker"; Filename: "{uninstallexe}"

[Run]
; Runs elevated (as Setup does) after "Concluir"; skipped in silent installs (CI).
Filename: "{app}\BajaWorker.exe"; Parameters: "--first-run --config {#DataDir}\worker.json"; Description: "Abrir a configuração e conectar à rede Baja"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\Baja Worker.url"

[Code]
var
  { Steps that failed; reported at the end and turned into exit code 1. }
  FailedSteps: String;

function DataDir: String;
begin
  Result := ExpandConstant('{#DataDir}');
end;

function ConfigPath: String;
begin
  Result := DataDir + '\worker.json';
end;

function NssmPath: String;
begin
  Result := ExpandConstant('{app}\nssm.exe');
end;

function TailscaleIsMissing: Boolean;
begin
  Result := not FileExists(ExpandConstant('{commonpf64}\Tailscale\tailscale.exe'));
end;

{ Run a program without a window and wait; returns its exit code (-1 if it did not start). }
function RunHidden(const FileName, Params: String): Integer;
var
  ResultCode: Integer;
begin
  Log('Running: ' + FileName + ' ' + Params);
  if not Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    ResultCode := -1;
  Log('Exit code: ' + IntToStr(ResultCode));
  Result := ResultCode;
end;

{ Run a step that must succeed; remember it if it does not. }
procedure RunStep(const What, FileName, Params: String);
begin
  if RunHidden(FileName, Params) <> 0 then
    FailedSteps := FailedSteps + #13#10 + '- ' + What;
end;

procedure Nssm(const Params: String);
begin
  RunStep('nssm ' + Params, NssmPath, Params);
end;

function ServiceExists: Boolean;
begin
  Result := RunHidden(ExpandConstant('{sys}\sc.exe'), 'query {#ServiceName}') = 0;
end;

procedure StopAndRemoveService;
begin
  if not ServiceExists then
    exit;
  if FileExists(NssmPath) then begin
    RunHidden(NssmPath, 'stop {#ServiceName}');
    RunHidden(NssmPath, 'remove {#ServiceName} confirm');
  end else begin
    RunHidden(ExpandConstant('{sys}\sc.exe'), 'stop {#ServiceName}');
    RunHidden(ExpandConstant('{sys}\sc.exe'), 'delete {#ServiceName}');
  end;
end;

procedure InstallTailscale;
var
  Msi: String;
  Code: Integer;
begin
  Msi := ExpandConstant('{tmp}\tailscale-setup-amd64.msi');
  if not FileExists(Msi) then begin
    Log('Tailscale already installed; MSI skipped');
    exit;
  end;
  WizardForm.StatusLabel.Caption := 'Instalando o Tailscale…';
  { Properties: infra/tailnet/worker_join.md §1. }
  Code := RunHidden(ExpandConstant('{sys}\msiexec.exe'),
    '/i "' + Msi + '" /quiet /norestart' +
    ' TS_UNATTENDEDMODE=always TS_NOLAUNCH=1 TS_ALLOWINCOMINGCONNECTIONS=always' +
    ' TS_ONBOARDING_FLOW=hide /l*v "' + DataDir + '\logs\tailscale-msi.log"');
  { 3010 / 1641: installed, a reboot is suggested. }
  if (Code <> 0) and (Code <> 3010) and (Code <> 1641) then
    FailedSteps := FailedSteps + #13#10 + '- Tailscale (msiexec ' + IntToStr(Code) + ')';
end;

procedure RestrictDataFolder;
begin
  { SIDs, not names: "Administrators" is "Administradores" on a pt-BR Windows.
    S-1-5-18 = SYSTEM, S-1-5-32-544 = Administrators. (OI)(CI)F = full
    control, inherited by every file and folder created inside. }
  RunStep('icacls ' + DataDir, ExpandConstant('{sys}\icacls.exe'),
    '"' + DataDir + '" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F');
end;

procedure RegisterService;
var
  LogsDir: String;
begin
  WizardForm.StatusLabel.Caption := 'Registrando o serviço BajaWorker…';
  LogsDir := DataDir + '\logs';
  StopAndRemoveService;
  Nssm('install {#ServiceName} "' + ExpandConstant('{app}\BajaWorker.exe') + '" --service --config ' + ConfigPath);
  Nssm('set {#ServiceName} DisplayName "Baja Worker"');
  Nssm('set {#ServiceName} Description "Baja video pipeline worker (status: {#SetupPageUrl})"');
  Nssm('set {#ServiceName} Start SERVICE_AUTO_START');
  Nssm('set {#ServiceName} AppDirectory ' + DataDir);
  Nssm('set {#ServiceName} AppStdout ' + LogsDir + '\service-stdout.log');
  Nssm('set {#ServiceName} AppStderr ' + LogsDir + '\service-stderr.log');
  Nssm('set {#ServiceName} AppRotateFiles 1');
  Nssm('set {#ServiceName} AppRotateOnline 1');
  Nssm('set {#ServiceName} AppRotateBytes 10485760');
  { Restart the worker whenever it exits, 10 s later. }
  Nssm('set {#ServiceName} AppExit Default Restart');
  Nssm('set {#ServiceName} AppRestartDelay 10000');
  { Stop = CTRL-C first; the worker winds its tasks down (worker/README.md). }
  Nssm('set {#ServiceName} AppStopMethodConsole 30000');
  { If nssm itself dies, Windows restarts the service (after 60 s, always). }
  RunStep('sc failure', ExpandConstant('{sys}\sc.exe'),
    'failure {#ServiceName} reset= 86400 actions= restart/60000/restart/60000/restart/60000');
  Nssm('start {#ServiceName}');
end;

procedure DeleteFirewallRules;
begin
  RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="{#FileServerRule}"');
  RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="{#ReceiverRule}"');
end;

procedure AddFirewallRules;
begin
  DeleteFirewallRules;  { an upgrade must not leave duplicates }
  RunStep('firewall 8710', ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall add rule name="{#FileServerRule}" dir=in action=allow protocol=TCP localport=8710 remoteip={#TailnetRange} profile=any');
  RunStep('firewall 8720', ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall add rule name="{#ReceiverRule}" dir=in action=allow protocol=TCP localport=8720 remoteip={#TailnetRange} profile=any');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { Upgrade: the running service keeps BajaWorker.exe open; stop it first. }
  StopAndRemoveService;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    exit;
  InstallTailscale;
  RestrictDataFolder;
  RunStep('worker.json', ExpandConstant('{app}\BajaWorker.exe'), '--init-config ' + ConfigPath);
  RegisterService;
  AddFirewallRules;
  if FailedSteps <> '' then
    SuppressibleMsgBox('Algumas etapas da instalação falharam:' + FailedSteps + #13#10#13#10 +
      'Detalhes no log da instalação (%TEMP%\Setup Log*.txt).', mbError, MB_OK, IDOK);
end;

function GetCustomSetupExitCode: Integer;
begin
  { Lets CI (and anyone scripting the install) see a partly failed install. }
  if FailedSteps <> '' then
    Result := 1
  else
    Result := 0;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    StopAndRemoveService;
    DeleteFirewallRules;
    { tailscale logout, only if this PC is on the Baja tailnet (leave_tailnet.py). }
    RunHidden(ExpandConstant('{app}\BajaWorker.exe'), '--leave-tailnet');
  end;
  if CurUninstallStep = usPostUninstall then
    SuppressibleMsgBox('O Baja Worker foi removido.' + #13#10#13#10 +
      'A pasta ' + DataDir + ' foi mantida: ela guarda os vídeos brutos, os logs e a ' +
      'configuração. Se você escolheu outra pasta de trabalho na configuração, ela também ' +
      'foi mantida. Apague-as à mão quando a equipe Baja disser que os vídeos já foram copiados.' + #13#10#13#10 +
      'O Tailscale continua instalado.', mbInformation, MB_OK, IDOK);
end;
