<#
Update or roll back a NON-editable DataForge install in a Windows venv (df-qryy).

  update-prod.ps1 -VenvPath <venv> -Wheel <dataforge-*.whl>
  update-prod.ps1 -VenvPath <venv> -Ref <git ref> [-Repo <git repo>]
  update-prod.ps1 -VenvPath <venv> -Rollback

  Add -Constraints <file> to pin the dependencies (scripts\prod\constraints-win.txt holds the
  versions prod runs) and -Requirements <file>[,<file>...] for packages the workspaces need
  that are not DataForge dependencies (e.g. workspaces\GetFinance\requirements.txt).

What it does:
  1. Refuses while DataForge is in use: a process running from the venv, or a job_runs row
     still 'running'/'pending' in <DataForgeHome>\dataforge.db.
  2. Records what is installed now (pip freeze, source of the dataforge distribution).
  3. Installs the wheel non-editable, then lets pip add whatever dependency is missing,
     together with the -Requirements files, in one resolve under the same -Constraints.
  4. Keeps every installed wheel under <StateDir>\releases, with current.json and
     previous.json naming the active release and the one -Rollback returns to.

Limits, by design of the engine today:
  - The engine has no lock or PID file. The two checks above are the best signal there is, and
    a job the scheduler starts DURING the install is not blocked. Run it away from the schedule.
  - A process killed mid-run leaves its job_runs row 'running' forever. When the row is known
    to be stale, pass -IgnoreRunRows; the process check still applies.
  - The wheel version is always 0.1.0, so a release is identified by its commit (with -Ref) or
    by the wheel's sha256 (with -Wheel), never by the version.
  - Dependencies are not pinned in pyproject.toml. pip keeps an installed dependency that still
    satisfies the range; pass -Constraints <file> to pin them. -Rollback restores the versions
    the release had (its pip freeze) but does not uninstall packages added later.

Windows PowerShell 5.1 compatible. Text is ASCII on purpose: 5.1 reads a BOM-less file as ANSI.
#>
[CmdletBinding(DefaultParameterSetName = "Wheel")]
param(
    [Parameter(Mandatory = $true)][string]$VenvPath,
    [Parameter(ParameterSetName = "Wheel", Mandatory = $true)][string]$Wheel,
    [Parameter(ParameterSetName = "Ref", Mandatory = $true)][string]$Ref,
    [Parameter(ParameterSetName = "Ref")][string]$Repo,
    [Parameter(ParameterSetName = "Rollback", Mandatory = $true)][switch]$Rollback,
    [string]$DataForgeHome = $env:DATAFORGE_HOME,
    [string]$StateDir,
    [string]$Constraints,
    [string[]]$Requirements = @(),
    [switch]$IgnoreRunRows
)
$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Invoke-Native([string]$Label, [scriptblock]$Cmd) {
    # Native stderr under ErrorActionPreference=Stop aborts the script in 5.1, hence Continue.
    Write-Host "==> $Label"
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Cmd 2>&1 | ForEach-Object { Write-Host "    $_" } } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE)" }
}

function Get-NativeLines([string]$Label, [scriptblock]$Cmd) {
    # stdout as lines; stderr is left on the console. PowerShell unwraps the result (one line
    # comes back as a string, none as $null), so callers wrap the call in @().
    $lines = @(& $Cmd)
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE)" }
    return $lines
}

function Read-Json([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return [System.IO.File]::ReadAllText($Path, $utf8) | ConvertFrom-Json
}

function Write-Json([string]$Path, $Value) {
    [System.IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 5), $utf8)
}

function Get-InstalledSource {
    # Where the installed dataforge distribution came from, read from its direct_url.json.
    $code = @'
import json, sys
from importlib import metadata
try:
    dist = metadata.distribution('dataforge')
except metadata.PackageNotFoundError:
    print(json.dumps({'kind': 'none'}))
    sys.exit(0)
raw = dist.read_text('direct_url.json')
info = json.loads(raw) if raw else {}
editable = bool(info.get('dir_info', {}).get('editable'))
print(json.dumps({
    'kind': 'editable' if editable else 'wheel',
    'version': dist.version,
    'url': info.get('url'),
    'sha256': info.get('archive_info', {}).get('hashes', {}).get('sha256'),
}))
'@
    $out = @(Get-NativeLines "read installed dataforge" { & $py -c $code })
    return ($out -join "`n") | ConvertFrom-Json
}

function Assert-NotRunning {
    # 1. Processes started from the venv: dataforge.exe, dataforge-gui.exe and python.exe in
    #    Scripts are launchers that stay alive for the whole run, task scripts included
    #    (the python task handler starts them with sys.executable).
    $prefix = $venv.TrimEnd("\") + "\"
    $procs = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ProcessId -ne $PID -and (
                ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) -or
                ($_.Name -like "python*" -and $_.CommandLine -and
                    $_.CommandLine.IndexOf($prefix, [System.StringComparison]::OrdinalIgnoreCase) -ge 0))
        })
    if ($procs.Count -gt 0) {
        $procs | ForEach-Object { Write-Host ("    pid {0}  {1}  {2}" -f $_.ProcessId, $_.Name, $_.CommandLine) }
        throw "DataForge is running from $venv ($($procs.Count) process(es)). Wait for the job, or stop the API/GUI, then retry."
    }
    Write-Host "No process is running from $venv"

    # 2. Run-level rows of the global database (JobRunORM, dataforge/db.py).
    if (-not $DataForgeHome) {
        Write-Warning "DATAFORGE_HOME is not set and -DataForgeHome was not given: job_runs not checked."
        return
    }
    $db = Join-Path $DataForgeHome "dataforge.db"
    if (-not (Test-Path -LiteralPath $db)) {
        Write-Host "No global database at ${db}: no run rows to check"
        return
    }
    $code = @'
import pathlib, sqlite3, sys
conn = sqlite3.connect(pathlib.Path(sys.argv[1]).resolve().as_uri() + '?mode=ro', uri=True)
tables = [r[0] for r in conn.execute('select name from sqlite_master where type = ?', ('table',))]
if 'job_runs' in tables:
    query = 'select id, workspace, job, status, started_at from job_runs where status in (?, ?)'
    for row in conn.execute(query, ('running', 'pending')):
        print(' | '.join(str(v) for v in row))
'@
    $rows = @(Get-NativeLines "read job_runs" { & $py -c $code $db })
    if ($rows.Count -eq 0) {
        Write-Host "No 'running'/'pending' row in $db"
        return
    }
    $rows | ForEach-Object { Write-Host "    $_" }
    if ($IgnoreRunRows) {
        Write-Warning "$($rows.Count) unfinished job_runs row(s) ignored (-IgnoreRunRows)."
        return
    }
    throw "$($rows.Count) job_runs row(s) are 'running'/'pending' in $db. If they are leftovers of a killed process, retry with -IgnoreRunRows."
}

function Save-Freeze([string]$Path) {
    $lines = @(Get-NativeLines "pip freeze" { & $py -m pip freeze --disable-pip-version-check })
    [System.IO.File]::WriteAllLines($Path, [string[]]$lines, $utf8)
}

function Build-WheelFromRef([string]$OutDir) {
    # A clean export of the ref, so nothing uncommitted in the checkout reaches the wheel.
    # Default: the repository this script lives in ($PSScriptRoot is empty in a 5.1 param default).
    if (-not $Repo) { $Repo = Join-Path $PSScriptRoot "..\.." }
    $repoPath = (Resolve-Path -LiteralPath $Repo).Path
    $commit = @(Get-NativeLines "git rev-parse $Ref" { & git -C $repoPath rev-parse --verify "$Ref^{commit}" })[0].Trim()
    $work = Join-Path $env:TEMP ("dataforge-build-" + $commit.Substring(0, 12) + "-" + $stamp)
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        $zip = Join-Path $work "src.zip"
        Invoke-Native "git archive $commit" { & git -C $repoPath archive --format=zip -o $zip $commit }
        Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $work "src")
        Invoke-Native "pip wheel" {
            & $py -m pip wheel (Join-Path $work "src") --no-deps --disable-pip-version-check -w (Join-Path $work "dist")
        }
        $built = @(Get-ChildItem (Join-Path $work "dist") -Filter "dataforge-*.whl")
        if ($built.Count -ne 1) { throw "expected one dataforge wheel, found $($built.Count)" }
        Copy-Item -LiteralPath $built[0].FullName -Destination $OutDir
        return @{ Path = (Join-Path $OutDir $built[0].Name); Commit = $commit }
    }
    finally { Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue }
}

function Install-Wheel([string]$WheelPath) {
    $extra = @()
    if ($Constraints) { $extra = @("-c", (Resolve-Path -LiteralPath $Constraints).Path) }
    foreach ($req in $Requirements) { $extra += @("-r", (Resolve-Path -LiteralPath $req).Path) }
    # The version never changes, so pip would keep the installed copy without --force-reinstall.
    Invoke-Native "pip install (dataforge only)" {
        & $py -m pip install --disable-pip-version-check --force-reinstall --no-deps $WheelPath
    }
    Invoke-Native "pip install (dependencies and -Requirements)" {
        & $py -m pip install --disable-pip-version-check @extra $WheelPath
    }
}

function Restore-Freeze([string]$FreezePath) {
    # The dataforge line (a file URL or "-e ...") is installed separately, never from the freeze.
    $deps = @([System.IO.File]::ReadAllLines($FreezePath, $utf8) |
            Where-Object { $_ -and $_ -notmatch "^(-e |dataforge[ =@])" })
    $tmp = Join-Path $env:TEMP ("dataforge-freeze-" + $stamp + ".txt")
    [System.IO.File]::WriteAllLines($tmp, [string[]]$deps, $utf8)
    try { Invoke-Native "pip install (dependencies of the release)" { & $py -m pip install --disable-pip-version-check -r $tmp } }
    finally { Remove-Item -Force $tmp -ErrorAction SilentlyContinue }
}

function Assert-Installed([bool]$ExpectEditable) {
    Invoke-Native "pip check" { & $py -m pip check --disable-pip-version-check }
    $src = Get-InstalledSource
    $want = if ($ExpectEditable) { "editable" } else { "wheel" }
    if ($src.kind -ne $want) { throw "dataforge is installed as '$($src.kind)', expected '$want'" }
    Invoke-Native "dataforge --help" { & (Join-Path $venv "Scripts\dataforge.exe") --help | Out-Null }
    return $src
}

# --- main -----------------------------------------------------------------------------------
$venv = (Resolve-Path -LiteralPath $VenvPath).Path
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) { throw "not a Windows venv: $py is missing" }
if (-not $StateDir) { $StateDir = Join-Path $venv "dataforge-releases" }
$releases = Join-Path $StateDir "releases"
New-Item -ItemType Directory -Force -Path $releases | Out-Null
$currentFile = Join-Path $StateDir "current.json"
$previousFile = Join-Path $StateDir "previous.json"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

Assert-NotRunning

if ($Rollback) {
    $target = Read-Json $previousFile
    if (-not $target) { throw "nothing to roll back to: $previousFile does not exist" }
    $current = Read-Json $currentFile
    Write-Host "Rolling back to $($target.id) ($($target.kind), installed $($target.installed_at))"
    if ($target.kind -eq "editable") {
        Invoke-Native "pip install -e" { & $py -m pip install --disable-pip-version-check --no-deps -e $target.source }
    } else {
        Invoke-Native "pip install (dataforge only)" {
            & $py -m pip install --disable-pip-version-check --force-reinstall --no-deps $target.wheel
        }
    }
    Restore-Freeze $target.freeze
    Assert-Installed ($target.kind -eq "editable") | Out-Null
    # Swap, so a second -Rollback returns to the release just left.
    Write-Json $currentFile $target
    if ($current) { Write-Json $previousFile $current } else { Remove-Item -Force $previousFile }
    Write-Host "ROLLBACK OK: $($target.id)"
    exit 0
}

$releaseDir = Join-Path $releases $stamp
New-Item -ItemType Directory -Path $releaseDir | Out-Null
$before = Get-InstalledSource
$freezeBefore = Join-Path $releaseDir "freeze-before.txt"
Save-Freeze $freezeBefore
Write-Host "Installed now: $($before.kind) $($before.version) $($before.url)"

$commit = $null
if ($Ref) {
    $builtWheel = Build-WheelFromRef $releaseDir
    $wheelPath = $builtWheel.Path
    $commit = $builtWheel.Commit
} else {
    $src = (Resolve-Path -LiteralPath $Wheel).Path
    Copy-Item -LiteralPath $src -Destination $releaseDir
    $wheelPath = Join-Path $releaseDir (Split-Path $src -Leaf)
}
$sha = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLower()

# What -Rollback returns to: the release recorded as current, or, on the first run, the
# install found in the venv (an editable one is restored with pip install -e).
$previous = Read-Json $currentFile
if (-not $previous -and $before.kind -eq "editable") {
    $source = ([System.Uri]$before.url).LocalPath
    $previous = [ordered]@{
        id = "editable"; kind = "editable"; source = $source; wheel = $null; commit = $null
        sha256 = $null; ref = $null; freeze = $freezeBefore; installed_at = $null
    }
}

Install-Wheel $wheelPath
$after = Assert-Installed $false
if ($after.sha256 -ne $sha) { throw "installed wheel sha256 $($after.sha256) differs from $sha" }
$freezeAfter = Join-Path $releaseDir "freeze.txt"
Save-Freeze $freezeAfter

$id = if ($commit) { $commit.Substring(0, 12) } else { "sha256-" + $sha.Substring(0, 12) }
$current = [ordered]@{
    id = $id; kind = "wheel"; source = $null; wheel = $wheelPath; commit = $commit
    sha256 = $sha; ref = $Ref; freeze = $freezeAfter; installed_at = (Get-Date).ToString("s")
    constraints = $(if ($Constraints) { (Resolve-Path -LiteralPath $Constraints).Path } else { $null })
    requirements = @($Requirements | ForEach-Object { (Resolve-Path -LiteralPath $_).Path })
}
Write-Json $currentFile $current
if ($previous) { Write-Json $previousFile $previous }
$from = if ($previous) { $previous.id } else { "none" }
Write-Host "UPDATE OK: $id (was: $from). Roll back with -Rollback."
