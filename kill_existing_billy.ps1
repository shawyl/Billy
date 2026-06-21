$AppTitle = "Billy - Telegram Bill Split Bot"

# This script is meant to sit in the Billy project root beside launch_billy.bat.
# Using $PSScriptRoot avoids fragile path passing from the batch file.
$ProjectPath = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd("\")

# Do not kill the current launcher PowerShell process or its parent cmd window.
$CurrentPowerShellPid = $PID
$CurrentPowerShellProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $CurrentPowerShellPid" -ErrorAction SilentlyContinue
$CurrentLauncherPid = $null
if ($CurrentPowerShellProcess) {
    $CurrentLauncherPid = $CurrentPowerShellProcess.ParentProcessId
}

Write-Host "Checking for existing Billy windows/processes..."
Write-Host ("Project path: {0}" -f $ProjectPath)

# 1. Close old Billy console windows by title.
# The new launcher window is titled "Billy Launcher", so it should not close itself.
$allVisibleProcesses = Get-Process -ErrorAction SilentlyContinue

foreach ($proc in $allVisibleProcesses) {
    if ($proc.Id -eq $CurrentPowerShellPid -or $proc.Id -eq $CurrentLauncherPid) {
        continue
    }

    if ($proc.MainWindowTitle -like "$AppTitle*") {
        Write-Host ("Closing Billy window PID {0}: {1}" -f $proc.Id, $proc.MainWindowTitle)
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 1

# 2. Kill Python processes that appear to be running Billy from this project.
# Track their parent console process IDs too, so the old command prompt window closes as well.
$parentConsolePids = New-Object System.Collections.Generic.List[int]
$allWmiProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue

foreach ($proc in $allWmiProcesses) {
    $name = [string]$proc.Name
    $cmd = [string]$proc.CommandLine

    $isPython = $name -eq "python.exe" -or $name -eq "pythonw.exe"
    if (-not $isPython) {
        continue
    }

    $isFromProject = $false
    $isBillyModule = $false

    if ($cmd) {
        $isFromProject = $cmd -like "*$ProjectPath*"
        $isBillyModule = $cmd -match "src\.bot" -or $cmd -match "bot\.py"
    }

    if ($isFromProject -or $isBillyModule) {
        if ($proc.ParentProcessId -and $proc.ParentProcessId -ne $CurrentLauncherPid -and $proc.ParentProcessId -ne $CurrentPowerShellPid) {
            $parentConsolePids.Add([int]$proc.ParentProcessId)
        }

        Write-Host ("Stopping Billy Python process PID {0}" -f $proc.ProcessId)
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 1

# 3. Close old parent command prompt / PowerShell windows that launched the killed Billy Python process.
# This is what removes the old lingering command prompt window.
$uniqueParentPids = $parentConsolePids | Select-Object -Unique

foreach ($parentPid in $uniqueParentPids) {
    if ($parentPid -eq $CurrentLauncherPid -or $parentPid -eq $CurrentPowerShellPid) {
        continue
    }

    $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $parentPid" -ErrorAction SilentlyContinue
    if (-not $parent) {
        continue
    }

    $parentName = [string]$parent.Name
    $parentCmd = [string]$parent.CommandLine

    $isConsoleHost = $parentName -in @("cmd.exe", "powershell.exe", "pwsh.exe", "WindowsTerminal.exe", "wt.exe")
    $looksLikeBilly = $false

    if ($parentCmd) {
        $looksLikeBilly = $parentCmd -like "*$ProjectPath*" -or $parentCmd -match "launch_billy\.bat" -or $parentCmd -match "Billy"
    }

    if ($isConsoleHost -and ($looksLikeBilly -or $parentName -eq "cmd.exe")) {
        Write-Host ("Closing old Billy command window PID {0}" -f $parentPid)
        Stop-Process -Id $parentPid -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 1

Write-Host "Existing Billy cleanup completed."
