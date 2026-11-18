<#
PPE proje klasörü Windows ACL envanteri / yedeği / daraltması / geri alınması (FAZ 10-A.1).

Sorun: C:\Users\PC\Desktop üzerinde açık (kalıtsal olmayan) "BUILTIN\Users:(OI)(CI)(M)" girdisi var; proje klasörü
ve altındaki her şey (.env, detector\.env, backups\, data\snapshots\, tools\reliability\output\) bunu miras alır.
Hedef: proje kökünde mirası kesip yalnız geçerli kullanıcı + SYSTEM + Administrators bırakmak. Docker Desktop
(kullanıcı hesabıyla çalışır), dedektör, yedekleme betikleri aynı kullanıcıyla çalıştığından erişimleri sürer.

Kullanım (PowerShell, yönetici GEREKMEZ; klasör sahibi kullanıcı yeterli):
  .\scripts\windows_acl.ps1                         # yalnız envanter (salt okunur; varsayılan)
  .\scripts\windows_acl.ps1 -Backup                 # ACL yedeği (icacls /save), değişiklik yok
  .\scripts\windows_acl.ps1 -Apply -IConfirm DARALT # yedek alır, sonra daraltır ve doğrular
  .\scripts\windows_acl.ps1 -Rollback -BackupFile <dosya>   # yedekten birebir geri yükler

Yedekler proje DIŞINDA, profil kökünde tutulur (C:\Users\<kullanıcı>\ppe-acl-backup; yalnız kullanıcı/SYSTEM/
Administrators erişebilir). Gizli bilgi içermez (yalnız güvenlik tanımlayıcıları).
#>
param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [switch]$Backup,
    [switch]$Apply,
    [string]$IConfirm = "",
    [switch]$Rollback,
    [string]$BackupFile = "",
    [string]$BackupDir = (Join-Path $env:USERPROFILE "ppe-acl-backup")
)
$ErrorActionPreference = "Stop"

$SID_USERS = "S-1-5-32-545"; $SID_AUTH = "S-1-5-11"; $SID_EVERYONE = "S-1-1-0"
$SID_SYSTEM = "S-1-5-18"; $SID_ADMINS = "S-1-5-32-544"
$Me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Sensitive = @(".env", "detector\.env", "backups", "data", "data\snapshots", "tools\reliability\output", "detector\models")

function Get-Broad([string]$Path) {
    # Geniş gruplara (Users / Authenticated Users / Everyone) verilmiş izin girdileri
    (Get-Acl -LiteralPath $Path).Access | Where-Object {
        $sid = $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
        $_.AccessControlType -eq "Allow" -and $sid -in @($SID_USERS, $SID_AUTH, $SID_EVERYONE)
    } | ForEach-Object { "{0} {1} inherited={2}" -f $_.IdentityReference, $_.FileSystemRights, $_.IsInherited }
}

function Show-Inventory {
    "Kök: $Root"
    "Kullanıcı SID: $Me"
    foreach ($rel in @("") + $Sensitive) {
        $p = if ($rel) { Join-Path $Root $rel } else { $Root }
        if (-not (Test-Path -LiteralPath $p)) { "  [$rel] yok"; continue }
        $b = @(Get-Broad $p)
        $label = if ($rel) { $rel } else { "(kök)" }
        if ($b.Count) { "  [$label] GENİŞ ERİŞİM: " + ($b -join "; ") } else { "  [$label] yalnız sahip/SYSTEM/Administrators" }
    }
    $desk = Split-Path $Root -Parent
    $db = @(Get-Broad $desk)
    if ($db.Count) { "  [üst klasör $desk] " + ($db -join "; ") + "  (kaynak; bu betik üst klasörü DEĞİŞTİRMEZ)" }
}

function Save-Acl {
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $file = Join-Path $BackupDir "acl-$((Split-Path $Root -Leaf))-$stamp.txt"
    & icacls $Root /save $file /t /c /q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls /save başarısız (çıkış $LASTEXITCODE)" }
    $n = (Get-Content -LiteralPath $file -Encoding Unicode | Measure-Object -Line).Lines / 2
    Write-Host "ACL yedeği: $file ($n öğe). Geri yükleme: .\scripts\windows_acl.ps1 -Rollback -BackupFile `"$file`""
    return $file
}

if ($Rollback) {
    if (-not $BackupFile -or -not (Test-Path -LiteralPath $BackupFile)) { throw "-BackupFile ile var olan yedek dosyası verin" }
    # Yedek, kökün ÜST klasörüne göre göreli yollar içerir; önce mirası geri açıp kayıtlı DACL'leri birebir uygular.
    & icacls $Root /inheritance:e /q | Out-Null
    & icacls (Split-Path $Root -Parent) /restore $BackupFile /c /q
    if ($LASTEXITCODE -ne 0) { throw "icacls /restore başarısız (çıkış $LASTEXITCODE)" }
    "Geri yüklendi."; Show-Inventory; return
}

if ($Apply) {
    if ($IConfirm -ne "DARALT") { throw "Değişiklik için -IConfirm DARALT gerekli (yanlışlıkla çalıştırmaya karşı)" }
    $file = Save-Acl
    # Kökte mirası kes ve yalnız üç hesabı (OI)(CI) tam yetkiyle bırak; alt öğeler kökten miras alır.
    & icacls $Root /inheritance:r /grant:r "*${Me}:(OI)(CI)F" "*${SID_SYSTEM}:(OI)(CI)F" "*${SID_ADMINS}:(OI)(CI)F" /q
    if ($LASTEXITCODE -ne 0) { throw "icacls daraltma başarısız (çıkış $LASTEXITCODE); geri alma: -Rollback -BackupFile `"$file`"" }
    # Alt öğelerde ayrıca verilmiş (açık) geniş girdiler varsa onları da kaldır
    & icacls $Root /remove:g "*$SID_USERS" "*$SID_AUTH" "*$SID_EVERYONE" /t /c /q | Out-Null
    "Uygulandı. Doğrulama:"; Show-Inventory; return
}

Show-Inventory
if ($Backup) { Save-Acl | Out-Null }
