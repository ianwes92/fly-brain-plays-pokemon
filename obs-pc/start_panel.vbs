Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
sh.CurrentDirectory = fs.GetParentFolderName(WScript.ScriptFullName)
sh.Run "pythonw.exe flypanel.py", 0, False
