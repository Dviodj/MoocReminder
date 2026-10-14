Option Explicit
Dim shell, fso, folder, python, candidate, entry, failures, probeOnly, code
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
failures = ""
probeOnly = False
If WScript.Arguments.Count > 0 Then probeOnly = (WScript.Arguments(0) = "--preflight-only")
python = ""
candidate = fso.BuildPath(folder, ".venv\Scripts\pythonw.exe")
If fso.FileExists(candidate) Then
    If CanLoadTk(candidate) Then python = candidate
End If
If python = "" Then
    For Each entry In Split(shell.ExpandEnvironmentStrings("%PATH%"), ";")
        entry = Replace(entry, Chr(34), "")
        If entry <> "" Then
            candidate = fso.BuildPath(entry, "pythonw.exe")
            If fso.FileExists(candidate) Then
                If CanLoadTk(candidate) Then python = candidate
            End If
            If python = "" Then
                candidate = fso.BuildPath(entry, "pyw.exe")
                If fso.FileExists(candidate) Then
                    If CanLoadTk(candidate) Then python = candidate
                End If
            End If
            If python <> "" Then Exit For
        End If
    Next
End If
If python = "" Then
    If probeOnly Then
        WScript.Echo "No usable Python/Tk interpreter." & vbCrLf & failures
    Else
        MsgBox "Unable to open the startup window. Install or repair Python 3.9 or later with Tcl/Tk, then retry." & vbCrLf & "You can also run 启动MoocReminder.bat --check-runtime for diagnostics." & vbCrLf & "Startup log: " & folder & "\.launcher-state\startup.log" & vbCrLf & failures, 16, "MoocReminder"
    End If
    WScript.Quit 1
End If
If probeOnly Then
    WScript.Echo python
    WScript.Quit 0
End If
On Error Resume Next
code = shell.Run(Quote(python) & " " & Quote(fso.BuildPath(folder, "launcher_ui.py")), 0, False)
If Err.Number <> 0 Then
    MsgBox "Unable to open MoocReminder startup window: " & Err.Description, 16, "MoocReminder"
    WScript.Quit 1
End If
On Error GoTo 0
Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function
Function CanLoadTk(executable)
    Dim process, began, command, launchError
    CanLoadTk = False
    command = Quote(executable) & " -c " & Quote("import sys; assert sys.version_info >= (3,9); import tkinter as tk; root=tk.Tk(); root.withdraw(); root.update_idletasks(); root.destroy()")
    On Error Resume Next
    Set process = shell.Exec(command)
    launchError = Err.Number
    Err.Clear
    On Error GoTo 0
    If launchError <> 0 Then
        failures = failures & executable & ": cannot start" & vbCrLf
        Exit Function
    End If
    began = Now
    Do While process.Status = 0
        If DateDiff("s", began, Now) >= 8 Then
            On Error Resume Next
            process.Terminate
            On Error GoTo 0
            failures = failures & executable & ": Tk check timed out" & vbCrLf
            Exit Function
        End If
        WScript.Sleep 40
    Loop
    If process.ExitCode = 0 Then
        CanLoadTk = True
    Else
        failures = failures & executable & ": Python/Tk unavailable" & vbCrLf
    End If
End Function
