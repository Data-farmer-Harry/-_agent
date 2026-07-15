$ErrorActionPreference = "Stop"

$version = "4Jul2026"
$expectedSha256 = "da912e2c62beadce4c74cc9d4ebe313915905da031310bdd8370254ee84ba925"
$fileName = "LAMMPS-64bit-$version.exe"
$downloadUrl = "https://rpm.lammps.org/windows/$fileName"
$desktopRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$destination = Join-Path $desktopRoot "resources\lammps"
$installer = Join-Path $env:RUNNER_TEMP $fileName

Write-Host "Downloading official LAMMPS $version Windows package"
Invoke-WebRequest -Uri $downloadUrl -OutFile $installer
$actualSha256 = (Get-FileHash -Path $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw "LAMMPS checksum mismatch. Expected $expectedSha256, got $actualSha256"
}

Remove-Item -Path $destination -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$process = Start-Process -FilePath $installer -ArgumentList @("/S", "/D=$destination") -Wait -PassThru
if ($process.ExitCode -ne 0) {
    throw "LAMMPS installer exited with code $($process.ExitCode)"
}

$lammps = Get-ChildItem -Path $destination -Filter "lmp.exe" -Recurse | Select-Object -First 1
$potentials = Get-ChildItem -Path $destination -Directory -Filter "potentials" -Recurse | Select-Object -First 1
if (-not $lammps -or -not $potentials) {
    throw "The staged LAMMPS package is missing lmp.exe or potential files."
}

$manifest = @{
    version = $version
    source = $downloadUrl
    sha256 = $actualSha256
    executable = $lammps.FullName.Substring($destination.Length).TrimStart("\")
    potentials = $potentials.FullName.Substring($destination.Length).TrimStart("\")
} | ConvertTo-Json
Set-Content -Path (Join-Path $destination "matterlab-lammps-manifest.json") -Value $manifest -Encoding UTF8
Write-Host "Staged LAMMPS at $destination"
