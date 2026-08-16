Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = base & "\.venv\Scripts\pythonw.exe"
app = base & "\app.py"

If Not fso.FileExists(pythonw) Then
  WScript.Echo "Сначала один раз запустите build.bat или run.bat для установки зависимостей."
  WScript.Quit 1
End If

shell.Run """" & pythonw & """ """ & app & """", 0, False
