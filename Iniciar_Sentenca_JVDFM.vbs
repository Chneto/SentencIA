' Atalho-ponte: o programa agora se chama SentencIA. Este arquivo existe so para que atalhos criados
' por versoes anteriores (apontando para Iniciar_Sentenca_JVDFM.vbs) continuem abrindo o programa.
Option Explicit
Dim sh, fso, raiz, novo
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
raiz = fso.GetParentFolderName(WScript.ScriptFullName)
novo = raiz & "\Iniciar_SentencIA.vbs"
If Not fso.FileExists(novo) Then
    MsgBox "Arquivo nao encontrado: " & novo, vbCritical, "SentencIA"
    WScript.Quit 1
End If
sh.CurrentDirectory = raiz
sh.Run "wscript.exe """ & novo & """", 0, False
