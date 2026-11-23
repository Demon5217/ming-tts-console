' Silently keep WSL alive (no console window)
Set sh = CreateObject("WScript.Shell")
sh.Run "C:\Windows\System32\wsl.exe -d Ubuntu-22.04 -e true", 0, False
