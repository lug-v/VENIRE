<#
Sincroniza o vault BRAIN do Obsidian com o repositorio de notas do Venire (lug-v/venire-notas).

  Vault -> GitHub : copia "10 MBA\Conceitos" para "_fontes\MBA" (as notas originais nunca sao alteradas)
  GitHub -> Vault : copia as notas geradas pelo Venire para "40 VENIRE"

Uso:
  powershell -ExecutionPolicy Bypass -File sincronizar_obsidian.ps1 -Instalar   # configura tudo uma vez
  powershell -ExecutionPolicy Bypass -File sincronizar_obsidian.ps1             # uma sincronizacao (a tarefa faz isso)

O arquivo e todo em ASCII de proposito: o Windows PowerShell 5.1 le acentos errado em scripts sem BOM.
#>
param([switch]$Instalar)

$ErrorActionPreference = "Stop"

$Pasta     = Join-Path $env:USERPROFILE "Venire"
$Repo      = Join-Path $Pasta "venire-notas"
$Log       = Join-Path $Pasta "sincronizar.log"
$Vault     = Join-Path ([Environment]::GetFolderPath("Desktop")) "BRAIN\BRAIN"
$Conceitos = Join-Path $Vault "10 MBA\Conceitos"
$Destino   = Join-Path $Vault "40 VENIRE"
$Git       = "C:\Program Files\Git\cmd\git.exe"
$Remoto    = "https://github.com/lug-v/venire-notas.git"
$Tarefa    = "Venire - sincronizar Obsidian"

function Registrar([string]$texto) {
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 512KB) { Remove-Item $Log }
    Add-Content -Path $Log -Value ("{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $texto) -Encoding UTF8
}

function Git([string[]]$argumentos) {
    # O git escreve avisos no stderr; no PowerShell 5.1 isso viraria erro, entao so o codigo de saida conta.
    $ErrorActionPreference = "Continue"
    $saida = & $Git -C $Repo @argumentos 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "git $($argumentos -join ' ') falhou: $saida" }
    return $saida.Trim()
}

function Copiar([string]$de, [string]$para, [string[]]$extras) {
    & robocopy $de $para "*.md" @extras /R:2 /W:5 /NJH /NJS /NP /NFL /NDL | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy de '$de' para '$para' falhou (codigo $LASTEXITCODE)" }
}

if ($Instalar) {
    New-Item -ItemType Directory -Force -Path $Pasta | Out-Null
    if (-not (Test-Path $Conceitos)) { throw "Nao achei a pasta de conceitos: $Conceitos" }
    # O git passa a usar o login do GitHub CLI (gh) para acessar o repositorio privado.
    & gh auth setup-git --hostname github.com
    if (-not (Test-Path (Join-Path $Repo ".git"))) {
        & $Git clone --quiet $Remoto $Repo
        if ($LASTEXITCODE -ne 0) { throw "Nao consegui clonar $Remoto" }
    }
    Git @("config", "user.name", "Venire (PC)") | Out-Null
    Git @("config", "user.email", "lucasvgregio2002@gmail.com") | Out-Null
    New-Item -ItemType Directory -Force -Path $Destino | Out-Null

    # A tarefa roda o script copiado para a pasta Venire (fica no lugar mesmo que o repositorio mude).
    $script = Join-Path $Pasta "sincronizar_obsidian.ps1"
    if ($PSCommandPath -ne $script) { Copy-Item $PSCommandPath $script -Force }
    $acao = New-ScheduledTaskAction -Execute "conhost.exe" `
        -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$script`""
    # A cada 15 min e, para o PC que ficou desligado, 2 min depois de entrar no Windows.
    # StartWhenAvailable recupera a execucao perdida; RunOnlyIfNetworkAvailable espera o Wi-Fi conectar.
    $repetir = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
    $aoEntrar = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $aoEntrar.Delay = "PT2M"
    $opcoes = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $Tarefa -Action $acao -Trigger @($repetir, $aoEntrar) -Settings $opcoes `
        -Description "Copia os conceitos do MBA para o Venire e traz as notas do Venire para o vault BRAIN." -Force | Out-Null
    Write-Output "Tarefa '$Tarefa' criada: roda a cada 15 minutos. Log em $Log"
}

try {
    # 1. Traz o que o Venire gravou.
    Git @("pull", "--rebase", "--autostash", "--quiet") | Out-Null

    # 2. Vault -> GitHub: copia dos conceitos (espelho: conceito apagado no vault sai da copia).
    Copiar $Conceitos (Join-Path $Repo "_fontes\MBA") @("/MIR")
    Git @("add", "-A", "_fontes") | Out-Null
    if (Git @("status", "--porcelain", "_fontes")) {
        Git @("commit", "--quiet", "-m", "Vault: conceitos do MBA atualizados") | Out-Null
        try { Git @("push", "--quiet") | Out-Null }
        catch {  # o Venire gravou uma nota no meio do caminho: junta e tenta de novo
            Git @("pull", "--rebase", "--autostash", "--quiet") | Out-Null
            Git @("push", "--quiet") | Out-Null
        }
        Registrar "Conceitos enviados ao GitHub."
    }

    # 3. GitHub -> Vault: notas do Venire (sem apagar nada no vault).
    Copiar $Repo $Destino @("/E", "/XD", ".git", "_fontes", "/XF", "README.md")
    Registrar "Ok."
}
catch {
    Registrar "ERRO: $($_.Exception.Message)"
    exit 1
}
