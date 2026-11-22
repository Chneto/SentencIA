' SentencIA - iniciador silencioso (Windows), usado pelo atalho da Area de Trabalho.
' Ja instalado: roda o instalador/iniciador sem janela (modo -SemPerguntas).
' Primeira vez (ou sem data\.instalado): abre a janela normal do instalador.
Option Explicit
Dim sh, fso, raiz, bat, marca, codigo
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
raiz = fso.GetParentFolderName(WScript.ScriptFullName)
bat = raiz & "\Iniciar SentencIA.bat"
marca = raiz & "\data\.instalado"
sh.CurrentDirectory = raiz

If Not fso.FileExists(bat) Then
    MsgBox "Arquivo nao encontrado: " & bat, vbCritical, "SentencIA"
    WScript.Quit 1
End If

If fso.FileExists(marca) And fso.FileExists(raiz & "\.venv\Scripts\python.exe") Then
    codigo = sh.Run("""" & bat & """ -SemPerguntas", 0, True)
    If codigo <> 0 Then
        MsgBox "O SentencIA nao iniciou (codigo " & codigo & ")." & vbCrLf & vbCrLf & _
            "Abra 'Iniciar SentencIA.bat' para ver os detalhes, ou consulte data\logs\bootstrap.log." & vbCrLf & _
            "Se o problema persistir, abra o .bat por um prompt de comando com o argumento -Reinstalar.", _
            vbExclamation, "SentencIA"
    End If
Else
    sh.Run """" & bat & """", 1, False
End If
