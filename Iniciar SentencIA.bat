@echo off
rem SentencIA - iniciador de 1 clique para Windows.
rem Instala o que faltar (sem administrador) e abre o sistema no navegador.
rem Registro: data\logs\bootstrap.log
setlocal
title SentencIA
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalador\bootstrap.ps1" %*
set "CODIGO=%ERRORLEVEL%"
rem Se o PowerShell nao chegou a rodar o instalador (GPO de ExecutionPolicy, AppLocker, bloqueio do arquivo), nada
rem teria sido exibido: mostra o codigo e segura a janela. O VBS oculto passa -SemPerguntas e nao pode travar.
if not "%CODIGO%"=="0" if /i not "%~1"=="-SemPerguntas" (echo.& echo Falha ao iniciar o instalador ^(codigo %CODIGO%^).& echo Se a politica do computador bloqueia scripts, clique com o botao direito no arquivo ZIP ^> Propriedades ^> Desbloquear, extraia de novo e tente outra vez.& echo Registro: data\logs\bootstrap.log& pause)
endlocal & exit /b %CODIGO%
