Set WshShell = CreateObject("WScript.Shell")
' 0 hides the command prompt window completely
WshShell.Run "cmd /c venv\Scripts\python.exe launcher.py", 0, False
Set WshShell = Nothing