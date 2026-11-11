<#
Aceite em Windows limpo (Finanças v0, criterio 1).
Copia so os arquivos da lista (git ls-files -co --exclude-standard, gerada no WSL)
para uma pasta temporaria nova, tira o Node do PATH, instala, roda o job, sobe
`dataforge serve` e confere o app no Edge headless. Aborta no primeiro erro.
#>
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$FileList,
    [int]$Port = 8766,
    [string]$Python = "python",
    [string]$Edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$utf8 = New-Object System.Text.UTF8Encoding($false)
$Expected = @("6.066,74", "6.500,00", "433,26", "2.713,73", "5.230,55", "63.943,70")
$server = $null
$Fora = $null
$ok = $false

function Read-Shared([string]$Path) {
    $fs = New-Object System.IO.FileStream($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try { return (New-Object System.IO.StreamReader($fs, $utf8)).ReadToEnd() } finally { $fs.Dispose() }
}

function Invoke-Native([string]$Label, [scriptblock]$Cmd) {
    Write-Host "==> $Label"
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Cmd 2>&1 | ForEach-Object { Write-Host "$_" } } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "$Label falhou (exit $LASTEXITCODE)" }
}

function Get-DomValues([string]$Url, [string]$Tag) {
    $out = Join-Path $Dest "dom-$Tag.html"
    $err = Join-Path $Dest "dom-$Tag.err"
    $prof = Join-Path $Dest "edge-profile-$Tag"
    $edgeArgs = @("--headless", "--disable-gpu", "--no-first-run", "--user-data-dir=$prof",
        "--virtual-time-budget=8000", "--dump-dom", $Url)
    $p = Start-Process -FilePath $Edge -ArgumentList $edgeArgs -NoNewWindow -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    if (-not $p.WaitForExit(90000)) { $p.Kill(); throw "Edge nao terminou em 90s" }
    Start-Sleep -Seconds 2
    Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" | Where-Object { $_.CommandLine -like "*$prof*" } |
        ForEach-Object { & taskkill.exe /PID $_.ProcessId /T /F 2>&1 | Out-Null }
    $dom = (Read-Shared $out).Replace([string][char]0x00A0, " ")
    $found = [ordered]@{}
    foreach ($v in $Expected) { $found[$v] = $dom.Contains($v) }
    Write-Host "DOM ($Tag): $($dom.Length) chars"
    foreach ($k in $found.Keys) { Write-Host ("  {0,-10} {1}" -f $k, $(if ($found[$k]) { "OK" } else { "AUSENTE" })) }
    if ($found.Values -contains $false) { throw "valores ausentes no DOM ($Tag); veja $out" }
}

function Get-Probe([string]$Url) {
    # Status e corpo sem excecao em 4xx/5xx. Sem resposta HTTP, status 0.
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 30 -MaximumRedirection 0
        return @{ Status = [int]$r.StatusCode; Body = [string]$r.Content }
    } catch [System.Net.WebException] {
        $resp = $_.Exception.Response
        if (-not $resp) { return @{ Status = 0; Body = "" } }
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
        try { return @{ Status = [int]$resp.StatusCode; Body = $reader.ReadToEnd() } } finally { $reader.Dispose() }
    }
}

try {
    # 1. copia
    $Dest = Join-Path $env:TEMP ("dataforge-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Path $Dest | Out-Null
    $Source = (Resolve-Path $Source).Path
    $files = [System.IO.File]::ReadAllLines($FileList, $utf8) | ForEach-Object { $_.TrimEnd("`r") } | Where-Object { $_ }
    foreach ($f in $files) {
        $src = Join-Path $Source $f
        if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { continue }
        $dst = Join-Path $Dest $f
        New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
        Copy-Item -LiteralPath $src -Destination $dst
    }
    Write-Host "Copiados para $Dest"
    $ws = Get-ChildItem (Join-Path $Dest "workspaces") -Directory | ForEach-Object { $_.Name }
    Write-Host "workspaces copiados: $($ws -join ', ')"
    if (@($ws).Count -ne 1 -or $ws -ne "GetFinance-Show") { throw "workspaces copiados deve ser exatamente GetFinance-Show, veio: $($ws -join ', ')" }
    $dist = Join-Path $Dest "workspaces\GetFinance-Show\apps\financas\dist\index.html"
    if (-not (Test-Path $dist)) { throw "dist\index.html ausente na copia" }
    Write-Host "dist\index.html presente: $dist"
    Set-Location $Dest

    # 2. sem Node no PATH
    $kept = @()
    foreach ($d in ($env:PATH -split ";")) {
        if ($d -and -not (Test-Path -LiteralPath (Join-Path $d "node.exe"))) { $kept += $d }
        elseif ($d) { Write-Host "PATH: removido $d" }
    }
    $env:PATH = $kept -join ";"
    if (Get-Command node -ErrorAction SilentlyContinue) { throw "node ainda no PATH" }
    Write-Host "Get-Command node -> nao encontrado (Node fora do PATH)"

    # 3. venv + install
    $py = if (Get-Command $Python -ErrorAction SilentlyContinue) { $Python } else { "C:\Program Files\Python313\python.exe" }
    Invoke-Native "python --version" { & $py --version }
    Invoke-Native "python -m venv .venv" { & $py -m venv .venv }
    Invoke-Native "pip install -e ." { & .venv\Scripts\pip install -e . }

    # 4-5. job
    $env:DATAFORGE_HOME = (Get-Location).Path
    Invoke-Native "dataforge run GetFinance-Show finance_daily" { & .venv\Scripts\dataforge run GetFinance-Show finance_daily }

    # 6. serve
    $so = Join-Path $Dest "serve.out"; $se = Join-Path $Dest "serve.err"
    $server = Start-Process -FilePath (Join-Path $Dest ".venv\Scripts\dataforge.exe") `
        -ArgumentList @("serve", "--port", "$Port") -NoNewWindow -PassThru `
        -RedirectStandardOutput $so -RedirectStandardError $se
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
        Start-Sleep -Milliseconds 500
        if ($server.HasExited) { break }
        $txt = ""
        foreach ($f in @($so, $se)) { if (Test-Path $f) { $txt += Read-Shared $f } }
        if ($txt -match "(http://127\.0\.0\.1:$Port/[^\s]+)") { $url = $Matches[1] }
    }
    if (-not $url) { throw "URL do app nao impressa. stdout/stderr em $so / $se" }
    Write-Host "URL do app impressa: $url"
    # aguarda o servidor aceitar conexoes
    $up = $false
    for ($i = 0; $i -lt 60 -and -not $up; $i++) {
        try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 | Out-Null; $up = $true } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $up) { throw "servidor nao respondeu em $url" }

    # 7. content-type do .js
    $html = (Invoke-WebRequest -Uri $url -UseBasicParsing).Content
    if ($html -notmatch 'src="([^"]+\.js)"') { throw "nenhum <script src=.js> no index" }
    $jsUrl = [Uri]::new([Uri]$url, $Matches[1]).AbsoluteUri
    $ct = (Invoke-WebRequest -Uri $jsUrl -UseBasicParsing).Headers["Content-Type"]
    Write-Host "$jsUrl -> Content-Type: $ct"
    if ($ct -notmatch "^text/javascript") { throw "Content-Type do .js e '$ct', esperado text/javascript" }

    # 7b. segmentos de URL no Windows: nada fora de apps/, nenhum acesso a host UNC
    $Fora = Join-Path $env:TEMP ("dataforge-smoke-fora-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Path (Join-Path $Fora "dist") | Out-Null
    $marcador = "MARCADOR-FORA-DA-COPIA"
    [System.IO.File]::WriteAllText((Join-Path $Fora "app.yaml"), "name: '$($Fora.Replace("'", "''"))'`n", $utf8)
    [System.IO.File]::WriteAllText((Join-Path $Fora "dist\index.html"), "<html>$marcador</html>", $utf8)
    $base = "http://127.0.0.1:$Port/apps/GetFinance-Show"
    $problemas = @()
    $r = Get-Probe "$base/$([Uri]::EscapeDataString($Fora))/"
    Write-Host "app em caminho absoluto -> $($r.Status)"
    if ($r.Status -eq 200) { $problemas += "caminho absoluto respondeu 200" }
    if ($r.Body.Contains($marcador)) { $problemas += "caminho absoluto serviu o marcador de fora da copia" }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $r = Get-Probe "$base/financas/%5C%5C192.0.2.1%5Cs%5Cx"
    $sw.Stop()
    Write-Host "caminho UNC -> $($r.Status) em $($sw.ElapsedMilliseconds) ms"
    if ($r.Status -ne 404) { $problemas += "caminho UNC respondeu $($r.Status), esperado 404" }
    if ($sw.ElapsedMilliseconds -ge 2000) { $problemas += "caminho UNC levou $($sw.ElapsedMilliseconds) ms, limite 2000" }
    if ($problemas) { throw ($problemas -join "; ") }

    # 8. DOM
    Get-DomValues $url "run1"

    # 9. segunda execucao
    Invoke-Native "dataforge run GetFinance-Show finance_daily (2a)" { & .venv\Scripts\dataforge run GetFinance-Show finance_daily }
    Get-DomValues $url "run2"

    $ok = $true
}
finally {
    if ($server -and -not $server.HasExited) {
        & taskkill.exe /PID $server.Id /T /F 2>&1 | Out-Null
        Write-Host "Servidor encerrado (pid $($server.Id))"
    }
    Set-Location $env:TEMP
    if ($Fora) { Remove-Item -Recurse -Force $Fora -ErrorAction SilentlyContinue }
    if ($ok) {
        Remove-Item -Recurse -Force $Dest -ErrorAction SilentlyContinue
        Write-Host "ACEITE OK"
    } else {
        Write-Host "FALHOU. Pasta mantida: $Dest"
    }
}
if (-not $ok) { exit 1 }
