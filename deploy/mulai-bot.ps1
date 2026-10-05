# Menyalakan bot Klipa di Windows: 9Router (model) lalu gateway Hermes (Telegram).
#   powershell -ExecutionPolicy Bypass -File deploy\mulai-bot.ps1
#
# Aman dijalankan berulang: yang sudah hidup TIDAK disentuh dan TIDAK di-restart (restart gateway
# membunuh render yang sedang berjalan -- lihat CLAUDE.md aturan 3).
#
# Kenapa skrip, bukan dua perintah manual:
# - 9Router bawaan mendengarkan di 0.0.0.0 (terbuka ke WiFi/LAN). Di sini ia selalu diikat ke 127.0.0.1.
# - `hermes` tanpa argumen adalah mode obrolan terminal, BUKAN bot Telegram (4 Okt: pesan Telegram tidak
#   dijawab 2,5 jam karena yang dinyalakan mode terminal). Bot = `hermes gateway run`.
# - Gateway dijalankan di KONSOL SENDIRI (cmd start /min), bukan menumpang konsol jendela ini. Terukur
#   4 Okt 16:54: dengan Win32_Process.Create saja, gateway ikut mati saat user menekan Ctrl+C di
#   PowerShell-nya -- log gateway berakhir dengan "^C" dan bot membisu di Telegram tanpa pesan apa pun.
param(
    [string]$FolderLog = "D:\wsl",
    [int]$Port = 20128
)
$ErrorActionPreference = "Stop"

function Mulai-Lepas([string]$Perintah) {
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $Perintah }
    if ($r.ReturnValue -ne 0) { throw "gagal menjalankan (kode $($r.ReturnValue)): $Perintah" }
    return $r.ProcessId
}

if (-not (Test-Path $FolderLog)) { New-Item -ItemType Directory -Force $FolderLog | Out-Null }
$adaMasalah = $false

# --- Docker: tanpa ini bot tetap menjawab, tapi tidak bisa merender ---
$docker = (docker info --format "{{.ServerVersion}}" 2>$null) -join ""
if ($docker) { "Docker      : hidup ($docker)" }
else { "Docker      : MATI -- nyalakan Docker Desktop, kalau tidak bot tidak bisa merender."; $adaMasalah = $true }

# --- 9Router ---
$dengar = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($dengar.Count -eq 0) {
    $skrip = (Get-Command 9router.ps1 -ErrorAction SilentlyContinue).Source
    if (-not $skrip) { $skrip = "C:\Program Files\nodejs\9router.ps1" }
    if (-not (Test-Path $skrip)) { throw "9router.ps1 tidak ditemukan. Pasang 9Router dulu (npm i -g 9router)." }
    Mulai-Lepas ("powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$skrip`" " +
                 "-n -H 127.0.0.1 --skip-update") | Out-Null
    for ($i = 0; $i -lt 30 -and $dengar.Count -eq 0; $i++) {
        Start-Sleep -Seconds 2
        $dengar = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    }
    if ($dengar.Count -eq 0) { throw "9Router tidak mau menyala dalam 60 detik." }
    "9Router     : dinyalakan"
} else { "9Router     : sudah hidup" }
$alamat = @($dengar | Select-Object -ExpandProperty LocalAddress -Unique)
$terbuka = @($alamat | Where-Object { $_ -notin @("127.0.0.1", "::1") })
if ($terbuka.Count -gt 0) {
    "              PERINGATAN: mendengarkan di $($alamat -join ', ') -- terbuka ke jaringan."
    "              Tutup 9Router (ikon tray -> Quit), lalu jalankan skrip ini lagi."
    $adaMasalah = $true
} else { "              hanya 127.0.0.1 (tidak terbuka ke jaringan)" }

# --- Gateway Hermes ---
$hermes = Join-Path $env:LOCALAPPDATA "hermes\bin\hermes.cmd"
if (-not (Test-Path $hermes)) { throw "Hermes tidak ditemukan di $hermes." }
$status = (& $hermes gateway status 2>$null) -join "`n"
if ($status -match "Gateway is running") {
    "Gateway     : sudah hidup (tidak di-restart)"
} else {
    $err = Join-Path $FolderLog "gateway.err.txt"
    $out = Join-Path $FolderLog "gateway.out.txt"
    if (Test-Path $err) { Move-Item $err (Join-Path $FolderLog "gateway.err.prev.txt") -Force }
    # Perintahnya ditulis ke .cmd dulu supaya tidak ada kutip bersarang yang salah tafsir di `start`.
    $launcher = Join-Path $FolderLog "jalankan-gateway.cmd"
    Set-Content -Path $launcher -Encoding ASCII -Value @(
        "@echo off",
        "title Hermes Gateway (bot Telegram) - JANGAN tekan Ctrl+C di sini",
        "`"$hermes`" gateway run > `"$out`" 2> `"$err`""
    )
    # `start` memberi konsol baru = grup Ctrl+C terpisah dari jendela yang menjalankan skrip ini.
    Mulai-Lepas "cmd.exe /c start `"Hermes Gateway`" /min `"$launcher`"" | Out-Null
    $tersambung = $false
    for ($i = 0; $i -lt 45 -and -not $tersambung; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Path $err) { $tersambung = [bool](Select-String -Path $err -Pattern "Connected to Telegram" -Quiet) }
    }
    if ($tersambung) { "Gateway     : dinyalakan, tersambung ke Telegram" }
    else { "Gateway     : dinyalakan, tapi BELUM tersambung ke Telegram setelah 90 detik. Lihat $err"; $adaMasalah = $true }
}

"C: bebas    : {0:N0} MB" -f ((Get-PSDrive C).Free / 1MB)
if ((Get-PSDrive C).Free -lt 2GB) { "              PERINGATAN: di bawah 2 GB. Gateway pernah mati karena C: penuh."; $adaMasalah = $true }
if ($adaMasalah) { exit 1 }
