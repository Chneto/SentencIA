; SentencIA — Script Inno Setup 6 (instalacao por usuario, sem administrador, sem certificado).
; Compilar com: iscc /DMyAppVersion=2.1.0 instalador\windows\SentencIA.iss
;
; Observacoes:
; - PrivilegesRequired=lowest: instala em {localappdata}\Programs\SentencIA sem pedir UAC/admin.
; - Dados do gabinete ficam fora da pasta do programa: {localappdata}\SentencIA\data (JVDFM_DATA_DIR).
; - Na desinstalacao, os dados sao MANTIDOS por padrao (o usuario so apaga se confirmar explicitamente).

#ifndef MyAppVersion
  #define MyAppVersion "2.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\.."
#endif

[Setup]
AppId={{8D6B4F2C-4E12-4F5A-9C3E-7A2B1D5E9F01}
AppName=SentencIA
AppVersion={#MyAppVersion}
AppVerName=SentencIA {#MyAppVersion}
AppPublisher=FChNeto
DefaultDirName={localappdata}\Programs\SentencIA
DefaultGroupName=SentencIA
AllowNoIcons=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#SourceDir}\dist
OutputBaseFilename=SentencIA-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=SentencIA
UninstallDisplayIcon={app}\app\static\assets\app_icon.ico
SetupIconFile={#SourceDir}\app\static\assets\app_icon.ico

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Area de Trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: checkedonce
Name: "nuvem"; Description: "Instalar suporte opcional a nuvem (Gemini)"; GroupDescription: "Componentes opcionais:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\uv.lock"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\launcher.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\launcher.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\Iniciar SentencIA.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\Iniciar_SentencIA.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc"
Source: "{#SourceDir}\instalador\*"; DestDir: "{app}\instalador"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "windows\Output"
Source: "{#SourceDir}\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc"
Source: "{#SourceDir}\docs\GUIA_DO_USUARIO.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#SourceDir}\docs\INSTALACAO_MAC.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#SourceDir}\data\knowledge_base\acervo_completo.json"; DestDir: "{app}\data\knowledge_base"; Flags: ignoreversion

[Icons]
Name: "{group}\SentencIA"; Filename: "{app}\Iniciar SentencIA.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app\static\assets\app_icon.ico"
Name: "{group}\Desinstalar SentencIA"; Filename: "{uninstallexe}"
Name: "{autodesktop}\SentencIA"; Filename: "{app}\Iniciar SentencIA.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app\static\assets\app_icon.ico"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -NoProfile -Command ""$d='{localappdata}\SentencIA\data'; [IO.File]::WriteAllText('{app}\.pasta_dados', $d + [char]10, (New-Object Text.UTF8Encoding($false))); $env:JVDFM_DATA_DIR=$d; & '{app}\instalador\bootstrap.ps1' -SemIniciar -SemPerguntas"""; WorkingDir: "{app}"; StatusMsg: "Configurando Python 3.12 e bibliotecas locais do SentencIA..."; Flags: runhidden waituntilterminated; Tasks: not nuvem
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -NoProfile -Command ""$d='{localappdata}\SentencIA\data'; [IO.File]::WriteAllText('{app}\.pasta_dados', $d + [char]10, (New-Object Text.UTF8Encoding($false))); $env:JVDFM_DATA_DIR=$d; & '{app}\instalador\bootstrap.ps1' -SemIniciar -SemPerguntas -Nuvem"""; WorkingDir: "{app}"; StatusMsg: "Configurando Python 3.12, bibliotecas locais e suporte Gemini..."; Flags: runhidden waituntilterminated; Tasks: nuvem
Filename: "{app}\Iniciar SentencIA.bat"; Description: "Abrir o SentencIA agora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\.venv\Scripts\python.exe"; Parameters: """{app}\launcher.py"" --parar"; WorkingDir: "{app}"; Flags: runhidden skipifdoesntexist

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DadosDir: String;
  Resp: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DadosDir := ExpandConstant('{localappdata}\SentencIA\data');
    if DirExists(DadosDir) then
    begin
      { Padrao seguro: manter os dados (MB_DEFBUTTON1 = Sim, manter) }
      Resp := MsgBox(
        'Deseja MANTER os seus dados do gabinete (autos, configuracoes, precedentes e exportacoes) em:' + #13#10 +
        DadosDir + #13#10#13#10 +
        'Clique em SIM para manter seus dados (recomendado) ou NAO para apagar tudo.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON1
      );
      if Resp = IDNO then
      begin
        DelTree(DadosDir, True, True, True);
      end;
    end;
  end;
end;
