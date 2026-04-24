param(
    [string]$Ref = "main",
    [string]$Repo = "https://github.com/sunwbeck/homebase.git",
    [string]$Python = "",
    [string]$Venv = ""
)

$ErrorActionPreference = "Stop"

$subdirectory = "homebase-cli"
$userBin = Join-Path $HOME ".local\bin"
$managedVenv = if ($Venv) { $Venv } else { Join-Path $HOME ".local\share\homebase-cli\.venv" }
$workDir = $null

function Get-PythonCommand {
    param([string]$Requested)

    if ($Requested) {
        return @($Requested)
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @("py", "-3")
    }

    foreach ($candidate in @("python", "python3")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            return @($candidate)
        }
    }

    throw "Python 3.11 or newer is required."
}

function Invoke-Python {
    param(
        [string[]]$Command,
        [string[]]$Arguments
    )

    if ($Command.Length -gt 1) {
        & $Command[0] $Command[1..($Command.Length - 1)] @Arguments
    } else {
        & $Command[0] @Arguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $($Command -join ' ') $($Arguments -join ' ')"
    }
}

function Ensure-UserBinOnPath {
    param([string]$Target)

    $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @()
    if ($currentUserPath) {
        $entries = $currentUserPath.Split(";", [System.StringSplitOptions]::RemoveEmptyEntries)
    }
    if ($entries -notcontains $Target) {
        $newPath = if ($currentUserPath) { "$currentUserPath;$Target" } else { $Target }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        return $true
    }
    return $false
}

function New-CommandShim {
    param(
        [string]$Name,
        [string]$TargetExe
    )

    $shimPath = Join-Path $userBin "$Name.cmd"
    $content = @(
        "@echo off",
        "`"$TargetExe`" %*"
    )
    Set-Content -Path $shimPath -Value $content -Encoding ASCII
}

try {
    $pythonCommand = Get-PythonCommand -Requested $Python
    Invoke-Python -Command $pythonCommand -Arguments @(
        "-c",
        "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    )
    $repoRoot = if ($Repo.EndsWith(".git")) { $Repo.Substring(0, $Repo.Length - 4) } else { $Repo }
    $archiveUrl = "$repoRoot/archive/$Ref.zip"

    if ($env:VIRTUAL_ENV) {
        Write-Host "Using the current Python environment"
        $installPython = $pythonCommand
    } else {
        Write-Host "Preparing homebase runtime"
        Invoke-Python -Command $pythonCommand -Arguments @("-m", "venv", $managedVenv)
        $installPython = Join-Path $managedVenv "Scripts\python.exe"
        New-Item -ItemType Directory -Force -Path $userBin | Out-Null
    }

    Invoke-Python -Command $installPython -Arguments @("-m", "pip", "install", "--upgrade", "pip")

    Write-Host "Downloading homebase from $Repo@$Ref"
    $workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("homebase-install-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $workDir | Out-Null
    $archivePath = Join-Path $workDir "homebase.zip"
    Invoke-WebRequest -Uri $archiveUrl -OutFile $archivePath
    Expand-Archive -LiteralPath $archivePath -DestinationPath $workDir -Force

    $sourceDir = Get-ChildItem -Path $workDir -Directory | Where-Object { $_.Name -like "homebase-*" } | Select-Object -First 1
    if (-not $sourceDir) {
        throw "Failed to unpack the homebase source tree from $archiveUrl."
    }

    $installRoot = Join-Path $sourceDir.FullName $subdirectory
    if (-not (Test-Path -LiteralPath $installRoot)) {
        throw "Missing install subdirectory '$subdirectory' in $archiveUrl."
    }

    Write-Host "Installing homebase from $Repo@$Ref"
    Invoke-Python -Command $installPython -Arguments @(
        "-m", "pip", "install",
        "--upgrade",
        "--force-reinstall",
        "--no-cache-dir",
        $installRoot
    )

    if (-not $env:VIRTUAL_ENV) {
        $homebaseExe = Join-Path $managedVenv "Scripts\homebase.exe"
        $hbExe = Join-Path $managedVenv "Scripts\hb.exe"
        New-CommandShim -Name "homebase" -TargetExe $homebaseExe
        New-CommandShim -Name "hb" -TargetExe $hbExe
        $pathUpdated = Ensure-UserBinOnPath -Target $userBin
        if ($pathUpdated) {
            Write-Host ""
            Write-Host "Install finished. Added $userBin to the user PATH."
            Write-Host "Open a new PowerShell window before running homebase or hb."
        }
    }
}
finally {
    if ($workDir -and (Test-Path -LiteralPath $workDir)) {
        Remove-Item -LiteralPath $workDir -Recurse -Force
    }
}
