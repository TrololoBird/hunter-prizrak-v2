#Requires -Version 5.1
<#
  hunter-v2 — настройка рабочего места Claude Code и боевого контура.

  Запускается из setup-windows.bat (от имени администратора).
  Каждый изменяющий шаг СПРАШИВАЕТ. Ничего не делается молча.
  Что сюда сознательно НЕ вошло и почему — в самом конце.
#>

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$PROJ     = $PSScriptRoot
$LOGDIR   = Join-Path $env:USERPROFILE 'hunter-logs'
$UV       = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
$SKILLS   = Join-Path $env:USERPROFILE '.claude\skills'
$MARKET   = 'claude-plugins-official'
$MP       = Join-Path $env:USERPROFILE ".claude\plugins\marketplaces\$MARKET\plugins"
$TASKNAME = 'hunter-live'

$IsAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

function Head($t) { Write-Host ''; Write-Host "=== $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "   $t" -ForegroundColor Green }
function Warn($t) { Write-Host "   $t" -ForegroundColor Yellow }
function Info($t) { Write-Host "   $t" }

function Ask($q) {
    # ⚠ Умолчание — НЕТ, и попыток ровно три. Первая редакция крутила while($true)
    #   до распознанного ответа: при пустом вводе или закончившемся stdin она
    #   зависала навсегда. Поймано прогоном 2026-08-28, до того как файл ушёл
    #   владельцу. Изменяющий шаг обязан НЕ делаться при неясном ответе.
    for ($i = 0; $i -lt 3; $i++) {
        $a = (Read-Host "   $q [д/н, по умолчанию н]").Trim()
        if ($a -match '^(д|y|да|yes)$') { return $true }
        if ($a -eq '' -or $a -match '^(н|n|нет|no)$') { return $false }
        Write-Host '   не понял ответа' -ForegroundColor Yellow
    }
    Write-Host '   три раза не понял — считаю за НЕТ' -ForegroundColor Yellow
    return $false
}

Write-Host ''
Write-Host '============================================================'
Write-Host '  hunter-v2 · настройка Windows и Claude Code'
Write-Host "  проект: $PROJ"
Write-Host '============================================================'
if ($IsAdmin) { Ok 'права: АДМИНИСТРАТОР' }
else { Warn 'права: обычные — шаг «питание» будет пропущен' }

# ------------------------------------------------------------------ 0. осмотр
Head '0. ОСМОТР — только чтение, ничего не меняется'
# ⚠ wmic здесь НЕ используется: в Windows 11 сборки 26200 он УДАЛЁН и молча
#    напечатал бы пустоту. Проверено на этой машине 2026-08-28.
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$dc = Get-PSDrive C
Info ("Windows: {0}, сборка {1}" -f $os.Caption, [Environment]::OSVersion.Version)
Info ("ОЗУ: {0} ГБ, свободно {1} ГБ" -f `
    [math]::Round($cs.TotalPhysicalMemory / 1GB, 1), [math]::Round($os.FreePhysicalMemory / 1MB, 1))
Info ("Диск C: свободно {0} из {1} ГБ" -f `
    [math]::Round($dc.Free / 1GB, 1), [math]::Round(($dc.Used + $dc.Free) / 1GB, 1))

foreach ($t in @(
        @{n = 'git'; c = { git --version } },
        @{n = 'claude CLI'; c = { claude --version } })) {
    $v = & { try { & $t.c 2>$null | Select-Object -First 1 } catch { $null } }
    if ($v) { Ok ("{0}: {1}" -f $t.n, $v) } else { Warn ("{0}: НЕ НАЙДЕН" -f $t.n) }
}
if (Test-Path $UV) { Ok 'uv: есть' } else { Warn "uv: не найден по $UV" }
if (Test-Path (Join-Path $PROJ '.venv\Scripts\python.exe')) { Ok '.venv: есть' }
else { Warn '.venv: НЕТ — выполните: uv sync' }
if ([Environment]::GetEnvironmentVariable('TELEGRAM_BOT_TOKEN', 'User')) {
    Ok 'TELEGRAM_BOT_TOKEN: задан для пользователя (задача планировщика его увидит)'
} else {
    Warn 'TELEGRAM_BOT_TOKEN: НЕ задан на уровне пользователя — бот не подключится из задачи'
}
$py = @(Get-Process python -ErrorAction SilentlyContinue)
if ($py.Count) { Ok "боевой контур: процессов python $($py.Count)" }
else { Warn 'боевой контур: НЕ ЗАПУЩЕН' }

# --------------------------------------------------------- 1. Claude Code CLI
Head '1. CLAUDE CODE CLI — НЕ СТАВИТСЯ, и это осознанно'
Info 'Документация Anthropic дословно: «The desktop app includes Claude Code.'
Info 'You don''t need to install Node.js or the CLI separately.»'
Info 'Приложение и CLI — один движок и ОДИН каталог настроек ~\.claude\,'
Info 'поэтому отдельная команда ничего не добавляет к работе из приложения.'
$dl = Join-Path $env:USERPROFILE '.claude\downloads'
if (Test-Path $dl) {
    $junk = @(Get-ChildItem $dl -File -ErrorAction SilentlyContinue)
    if ($junk.Count) {
        $mb = [math]::Round(($junk | Measure-Object Length -Sum).Sum / 1MB, 1)
        Warn "остатки скачивания CLI: $($junk.Count) файл(ов), $mb МБ"
        if (Ask 'Удалить их?') { $junk | Remove-Item -Force; Ok 'удалено' }
        else { Info 'оставлено' }
    } else { Ok 'остатков нет' }
} else { Ok 'CLI не скачивался' }

# ------------------------------------------------------------------ 2. плагины
Head '2. ПЛАГИНЫ — ставятся В ПРИЛОЖЕНИИ, а не отсюда'
Info 'Кнопка + рядом с полем ввода → Plugins → выбрать и установить.'
Info 'Так добавляются навыки, агенты и MCP-серверы разом.'
$decl = @()
try {
    $ps = Join-Path $PROJ '.claude\settings.json'
    if (Test-Path $ps) {
        $o = (Get-Content $ps -Raw | ConvertFrom-Json).enabledPlugins
        if ($o) { $decl = @($o.PSObject.Properties | Where-Object { $_.Value -eq $true } | ForEach-Object { $_.Name }) }
    }
} catch { }
$cache = Join-Path $env:USERPROFILE '.claude\plugins\cache'
if ($decl.Count) {
    Warn "в .claude\settings.json объявлено плагинов: $($decl.Count)"
    $decl | ForEach-Object { Info "   $_" }
    if (Test-Path $cache) { Ok 'каталог установки на месте' }
    else {
        Warn 'каталог установки ОТСУТСТВУЕТ — ни один из них не загружен'
        Info 'Отсюда и симптом: /revise-claude-md не находится.'
        Info 'Поставить их: + → Plugins в приложении. Либо убрать строки из'
        Info '.claude\settings.json — файл в git, правьте сами и смотрите git diff.'
    }
} else { Ok 'проектных плагинов не объявлено' }

# ------------------------------------------------------------------- 3. навыки
Head "3. НАВЫКИ — личные, в $SKILLS"
New-Item -ItemType Directory -Force -Path $SKILLS | Out-Null
$want = @{
    'claude-automation-recommender' = 'claude-code-setup'
    'claude-md-improver'            = 'claude-md-management'
    'session-report'                = 'session-report'
    'skill-creator'                 = 'skill-creator'
}
$missing = @()
foreach ($k in $want.Keys) {
    if (Test-Path (Join-Path $SKILLS "$k\SKILL.md")) { Ok "есть: $k" }
    else { Warn "нет:  $k"; $missing += $k }
}
if ($missing.Count -and (Test-Path $MP) -and (Ask 'Доустановить отсутствующие из скачанного магазина?')) {
    foreach ($k in $missing) {
        $src = Join-Path $MP "$($want[$k])\skills\$k"
        if (Test-Path $src) { Copy-Item $src (Join-Path $SKILLS $k) -Recurse -Force; Ok "скопирован $k" }
        else { Warn "в магазине нет: $k" }
    }
} elseif ($missing.Count -and -not (Test-Path $MP)) {
    Warn 'магазин не скачан — сначала шаг 2'
}

# --------------------------------------------------------------------- 4. MCP
Head '4. MCP-СЕРВЕРЫ — в проекте объявлен один, context7 (11 вызовов за месяц)'
Info 'context7 работает и оставлен как есть.'
Info 'Новые MCP-серверы добавляются В ПРИЛОЖЕНИИ: + → Plugins (плагин может'
Info 'принести MCP-сервер с собой). Отдельная команда для этого не нужна.'
Warn 'MCP леджера СНЯТ из этого скрипта 2026-08-28, и вот почему:'
Info '  проект держит правило «единственный писатель» — леджер пишет только служба.'
Info '  Ни у одного доступного sqlite-сервера MCP не удалось ПОДТВЕРДИТЬ, что режим'
Info '  только-чтение действительно принудителен, а не соглашение по названию.'
Info '  Ставить сюда непроверенное — тот самый костыль, который свод запрещает.'
Info 'Разовые запросы к леджеру делаются как и делались: python + mode=ro.'

# --------------------------------------------------------- 5. настройки Claude
Head '5. НАСТРОЙКИ CLAUDE — пользовательский settings.json'
Info 'permissions.defaultMode=auto: значение "auto" из файла ПРОЕКТА игнорируется'
Info 'по построению — действует только пользовательский файл.'
Info 'В приложении режим переключается и вручную — селектор рядом с кнопкой'
Info 'отправки: Auto / Manual / Accept edits / Plan. Здесь задаётся УМОЛЧАНИЕ.'
$sp = Join-Path $env:USERPROFILE '.claude\settings.json'
$cur = $null
if (Test-Path $sp) {
    try { $cur = (Get-Content $sp -Raw | ConvertFrom-Json).permissions.defaultMode } catch { }
}
if ($cur -eq 'auto') {
    Ok 'уже auto — ничего не нужно'
} elseif (Ask "сейчас '$cur'. Выставить auto?") {
    if (-not (Test-Path $sp)) { '{}' | Set-Content -LiteralPath $sp -Encoding UTF8 }
    Copy-Item $sp "$sp.bak-setup" -Force
    $j = Get-Content $sp -Raw | ConvertFrom-Json
    if (-not $j.permissions) {
        $j | Add-Member permissions ([pscustomobject]@{}) -Force
    }
    $j.permissions | Add-Member defaultMode 'auto' -Force
    $j | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $sp -Encoding UTF8
    Ok "выставлено, копия: $sp.bak-setup"
} else { Info 'пропущено' }

# ------------------------------------------------------------------ 6. питание
Head '6. ПИТАНИЕ WINDOWS — бот работает круглосуточно, спящая машина его убивает'
if (-not $IsAdmin) {
    Warn 'нужны права администратора — пропущено'
} else {
    Info 'не засыпать от сети; экран гасить через 15 мин; гибернацию выключить'
    Info '(гибернация освободит hiberfil.sys — примерно объём ОЗУ на диске)'
    if (Ask 'Применить?') {
        powercfg /change standby-timeout-ac 0
        powercfg /change hibernate-timeout-ac 0
        powercfg /change monitor-timeout-ac 15
        powercfg -h off
        Ok 'готово. Откат: powercfg /change standby-timeout-ac 30 ; powercfg -h on'
    } else { Info 'пропущено' }
}

# --------------------------------------------------------------- 7. супервизор
Head '7. СУПЕРВИЗОР — 28.08 контур умер, и никто не заметил 3.6 часа'
New-Item -ItemType Directory -Force -Path $LOGDIR | Out-Null
# ⚠⚠ ОБЁРТКА — POWERSHELL, А НЕ .CMD. Первая редакция писала .cmd и была
#   СЛОМАНА: имя пользователя кириллическое, а батник сохраняется в OEM —
#   в файле оказывалось "C:\Users\??????\Documents\hunter-v2", задача уходила
#   в несуществующий каталог и бот не стартовал бы НИКОГДА. Поймано прогоном
#   2026-08-28 до передачи владельцу.
$runner = Join-Path $LOGDIR 'run-hunter-live.ps1'
$runnerBody = @"
`$ErrorActionPreference = 'Continue'
`$log = '$LOGDIR\live.log'
if ((Test-Path `$log) -and ((Get-Item `$log).Length -gt 20MB)) {
    Move-Item `$log "`$log.1" -Force
}
Set-Location -LiteralPath '$PROJ'
"[{0}] --- start hunter live ---" -f (Get-Date -f 'yyyy-MM-dd HH:mm:ss') | Add-Content `$log
& '$UV' run python -m hunter live *>> `$log
"[{0}] --- exit {1} ---" -f (Get-Date -f 'yyyy-MM-dd HH:mm:ss'), `$LASTEXITCODE | Add-Content `$log
exit `$LASTEXITCODE
"@
# BOM обязателен: без него Windows PowerShell 5.1 читает файл как ANSI и
# кириллический путь снова рассыпается — та же ловушка, что и у самого setup.
[IO.File]::WriteAllText($runner, $runnerBody, (New-Object Text.UTF8Encoding $true))

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>hunter-v2 live loop, auto-restart</Description></RegistrationInfo>
  <!-- ДВА триггера, и второй обязателен. Правка 2026-08-28: с одним LogonTrigger
       задача НЕ ЯВЛЯЛАСЬ супервизором. Замер на этой машине через два часа после
       создания: Last Run Time 30.11.1999, Last Result 267011 (0x41303, "задача ещё
       не запускалась"), файла live.log не существует. Вход в систему с момента
       регистрации не случился, и не случится, пока владелец не перезайдёт, — а
       раздел называется "контур умер, и никто не заметил 3.6 часа".
       RestartOnFailure тут не спасает: он перезапускает УПАВШЕЕ действие, а не
       запускает то, что не стартовало.
       Повтор каждые 5 минут + MultipleInstancesPolicy=IgnoreNew выше: пока контур
       жив, новый экземпляр игнорируется; умер — поднимется в течение пяти минут.
       StartBoundary в прошлом вместе со StartWhenAvailable даёт первый запуск сразу
       после регистрации, без ожидания входа. -->
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author">
    <LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>
  </Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author"><Exec>
    <Command>powershell.exe</Command>
    <Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$runner"</Arguments>
    <WorkingDirectory>$PROJ</WorkingDirectory>
  </Exec></Actions>
</Task>
"@
$xmlPath = Join-Path $env:TEMP 'hunter-live-task.xml'
$xml | Set-Content -LiteralPath $xmlPath -Encoding Unicode

Info "задача: $TASKNAME · запускает: $runner"
Info "лог:    $LOGDIR\live.log (ротация при 20 МБ)"
if (Ask 'Создать/обновить задачу?') {
    schtasks /delete /tn $TASKNAME /f 2>$null | Out-Null
    schtasks /create /tn $TASKNAME /xml $xmlPath /f
    if ($LASTEXITCODE -eq 0) {
        Ok "готово. Запустить: schtasks /run /tn $TASKNAME"
        Info "остановить: schtasks /end /tn $TASKNAME ; удалить: schtasks /delete /tn $TASKNAME /f"
    } else { Warn 'создать задачу не удалось — см. сообщение выше' }
} else { Info 'пропущено' }

# ---------------------------------------------------------------- 8. логи
Head '8. ЛОГИ — ежедневная чистка старше 14 дней'
if (Ask 'Настроить?') {
    $cmd = "forfiles /P `"$LOGDIR`" /M *.log* /D -14 /C `"cmd /c del @path`""
    schtasks /create /tn 'hunter-logs-cleanup' /tr $cmd /sc DAILY /st 04:00 /f | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok 'готово, ежедневно в 04:00' } else { Warn 'не удалось' }
} else { Info 'пропущено' }

# ---------------------------------------------------------------- 9. данные
Head '9. ДАННЫЕ ПРОЕКТА — правил хранения нет ни одного (находка B-4)'
$frames = Join-Path $PROJ 'data\frames'
if (Test-Path $frames) {
    $size = [math]::Round(((Get-ChildItem $frames -Recurse -File -ErrorAction SilentlyContinue |
                Measure-Object Length -Sum).Sum) / 1GB, 2)
    $old = @(Get-ChildItem $frames -Directory |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) })
    Info "data\frames: $size ГБ, каталогов прогонов $((Get-ChildItem $frames -Directory).Count)"
    if ($old.Count) {
        Warn "старше 7 дней: $($old.Count)"
        $old | ForEach-Object { Info "   $($_.Name)  $($_.LastWriteTime.ToString('yyyy-MM-dd'))" }
        if (Ask 'Удалить перечисленные?') {
            $old | Remove-Item -Recurse -Force
            Ok 'удалено'
        } else { Info 'не тронуто' }
    } else { Ok 'старых каталогов нет' }
} else { Warn 'data\frames не найден' }

# ------------------------------------------------------------------ итог
Write-Host ''
Write-Host '============================================================'
Write-Host '  ИТОГ'
Write-Host '============================================================'
Write-Host '  Проверить:'
Write-Host "    schtasks /query /tn $TASKNAME"
Write-Host "    Get-Content '$LOGDIR\live.log' -Tail 40"
Write-Host '    claude plugin list ; claude mcp list'
Write-Host ''
Write-Host '  ОТКАТ:'
Write-Host "    schtasks /delete /tn $TASKNAME /f"
Write-Host '    schtasks /delete /tn hunter-logs-cleanup /f'
Write-Host '    powercfg /change standby-timeout-ac 30 ; powercfg -h on'
Write-Host "    copy `"$sp.bak-setup`" `"$sp`""
Write-Host ''
Write-Host '  ЧТО ДЕЛАЕТСЯ В САМОМ ПРИЛОЖЕНИИ, А НЕ ЗДЕСЬ:'
Write-Host '    плагины и MCP-серверы  — кнопка + рядом с полем ввода, пункт Plugins'
Write-Host '    навыки и команды       — символ / либо + → Slash commands'
Write-Host '    режим разрешений       — селектор рядом с кнопкой отправки'
Write-Host '    терминал внутри окна   — Ctrl+`'
Write-Host ''
Write-Host '  СОЗНАТЕЛЬНО НЕ ДЕЛАЕТСЯ:'
Write-Host '    - установка Claude Code CLI: приложение УЖЕ содержит Claude Code'
Write-Host '      и делит с CLI один каталог ~\.claude\ — отдельная команда не нужна;'
Write-Host '    - исключения Windows Defender: ускоряют, но ослабляют защиту машины;'
Write-Host '    - правка реестра (длинные пути): на них ничего не ломалось;'
Write-Host '    - правка .claude\settings.json ПРОЕКТА: он в git, смотрите git diff;'
Write-Host '    - расширение permissions.allow: за месяц не нашлось ни одной команды,'
Write-Host '      которую можно предодобрить безопасно (18 отказов из 20 — составные);'
Write-Host '    - файл подкачки: настоящее ограничение это 5.9 ГБ ОЗУ, своп его только'
Write-Host '      маскирует.'
Write-Host ''
# Паузу держит .bat — иначе скрипт нельзя прогнать неинтерактивно для проверки.
