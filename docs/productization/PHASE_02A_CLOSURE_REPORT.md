# FAZ 02-A.1 / 02-A.2 — Güvenli Kapanış, Yedek Ön Kontrolü ve Sürüm Kontrol Noktası

| | |
|---|---|
| Tarih | 2026-11-18 |
| Çalışma alanı | `C:\Users\PC\Desktop\ppe-split-integration`, branch `integration/faz-01` |
| Commit | Araçlar **`65fe6ba2ff8976a367a097a4d0ce7c481bae52de`** olarak commit'lenip doğrulandı (önceki: `c8204fa`); bu rapordaki testler, RC ve bakım kaydı bu SHA'ya atıf yapar. 2026-11-18'de kullanıcı isteğiyle `65fe6ba` ve rapor commit'i, push'tan önce **tek commit'te birleştirildi**. Birleşik commit = `65fe6ba` + yalnızca bu rapor; kod ağacı aynı (`dashboard` tree `59498c7e…`). `65fe6ba` yerel tag `rc/0.10.7-rc.faz02a1` ile erişilebilir tutuldu |
| Release adayı | `ppe-dashboard:0.10.7-rc.faz02a1` = `ppe-dashboard:sha-65fe6ba2ff89` → `sha256:ea722e05b99f…` |
| Üretim | Kapanış çalışması sırasında `ppe-split` yığını **kapalı kaldı**. 0007 uygulanmadı, dashboard deploy edilmedi, disk temizliği yapılmadı, FAZ 02-B başlatılmadı |
| Sonradan, onayla (2026-11-18) | K2(a) `C:\PPE-Backups` + sahibine özel ACL; K3 kısmen: age 1.3.2 kuruldu (anahtar yok); **K1 G1 ile uygulandı:** üretim yedeği `ppe-20261118T112231Z` alındı ve doğrulandı, DB yeniden kapatıldı (§10, `C:\PPE-Backups\MAINTENANCE_LOG.md`) |
| Önceki raporlar | `PHASE_01_CONSOLIDATION_REPORT.md`, `PHASE_02A_DATA_SAFETY_REPORT.md`, `PHASE_02B_SOAK_TEST_PLAN.md` |

## 0. Özet

| Alan | Sonuç |
|---|---|
| FAZ 02-A değişikliklerinin doğrulanması | 15 değişen + 5 yeni dosya bulundu. İzlenen değişikliklerin patch'i repo dışı yedekle **bayt düzeyinde aynı**. Yeni dosyalardan 4'ü aynı; FAZ 02-A raporunda yalnızca yedeğin kendisini anlatan 1 satır sonradan eklenmiş |
| Tek toplu commit | **Yapıldı:** `65fe6ba`, 23 dosya, +2293/−157. İçerik: FAZ 02-A + bu fazın 3 küçük güvenlik eki (§2.2) |
| Gizli bilgi denetimi | **PASS.** Üretim değerleri referans alındı: 5 değer, 217 izlenen + 7 aday dosya, 8,04 MB Git geçmişi, 556 kanıt dosyası, repo dışı yedekler, 10 test container'ı → 0 eşleşme |
| Testler | Dashboard 427/427, scripts 69/69, SteelBench 13 + 1 skip, gerçek Docker negatif testleri ve izole yedek turu 25/25, migration provası (yeni RC) 11/11 |
| Yeni release adayı | Commit'ten oluşturuldu. Bağlam tekrarlanabilir. `verified=true`. `/app`, eski RC ile `build-info.json` dışında bayt düzeyinde aynı. `latest` değişmedi |
| Yedek için disk | **Yeterli.** Yedek < 0,3 GiB (üst sınır) gerektiriyor; C:'de 29–32 GiB boş. Kapasite koruması artık FAIL-CLOSED |
| **Yeni bulgu (P1)** | `C:\Users\PC` başka bir projenin **Git deposu** (GitHub remote'u var, index bozuk). `C:\Users\PC\ppe-local-backups\` bu deponun içinde kalıyor. Üretim yedeği bu klasöre **alınmamalı** (§4.3) |
| **Yeni bulgu (P1, 02-B)** | Reliability yığını `ppe-dashboard:faz9-rel` imajına sabitlenmiş. Soak bu haliyle yeni kodu değil, FAZ 9 imajını ölçer (§9) |
| Üretim dağıtımı | **NO-GO.** Güncel yedek artık var (R1 kapandı, 2026-11-18), ancak şifresiz ve makine dışı kopyası yok (G1); 0007 penceresi ve deploy ayrı onay bekliyor |
| FAZ 02-B | Teknik kod hazır. Başlatılmadı. Operatör ön koşulları ve imaj sabitlemesi bekleniyor (§9) |

## 1. Git ve kaynak kod kontrolü (A)

### 1.1 Başlangıç durumu

- `integration/faz-01` @ `c8204fa`, worktree `ppe-split-integration`.
- Diğer worktree: `ppe-split-main` @ `2cfdf06` (`github-main`). Kullanıcının 8 WIP dosyası ve izlenmeyen `docs/productization/` orada duruyor; **dokunulmadı**.
- Integration worktree'sinde ilgisiz kullanıcı dosyası **yok**. Bulunan 20 yol, FAZ 02-A raporu §13'te listelenen yollarla birebir aynı.

### 1.2 FAZ 02-A içeriğinin repo dışı yedekle karşılaştırılması

Yedek: `C:\Users\PC\ppe-local-backups\faz02a-20261117T235058Z\`

| Kontrol | Sonuç |
|---|---|
| `SHA256SUMS` (4 dosya) | OK |
| `git diff` ↔ `faz02a-tracked.patch` | **Bayt düzeyinde aynı** (`cmp`); `git apply --check -R` OK |
| `faz02a-untracked.tar` ↔ çalışma ağacı | 4/5 aynı. `PHASE_02A_DATA_SAFETY_REPORT.md`'de yedekten sonra 1 satır eklenmiş (§13'teki yedek yolu satırı) |
| ACL | Yalnızca `<makine>\PC:(OI)(CI)(F)`. Devralma kapalı; Users/Everyone yok |
| Konum riski | Klasör, `C:\Users\PC` Git deposunun içinde (§4.3). İçerik yalnızca kod/patch; gizli değer yok (taramada 0) |

### 1.3 Testlerin dosyalarla ilişkisi

- FAZ 02-A testleri bu içerik üzerinde koşulmuştu; patch yedeği o testlerden sonra alındı ve hâlâ aynı.
- Bu fazda ek değişiklik yapıldığı için **tüm ilgili testler son içerikle yeniden koşuldu** (§6).
- Testlerden sonra yalnızca `docs/BACKUP_RESTORE.md` (belge) değişti. Ardından scripts testleri, `py_compile` ve `git diff --check` tekrarlandı.
- Commit'lenen içerik, test edilen içerikle aynı.

### 1.4 Araçların tutarlılık incelemesi (diff satır satır okundu)

| Konu | Sonuç |
|---|---|
| Ortak kimlik modülü | `backup.py`, `restore_check.py`, `migration_rehearsal.py`, `ppe_ops.py` ve reliability `common.py`/`steelbench.py`/`baseline_eval.py` hepsi `scripts/docker_identity.py`'yi kullanıyor. Ad kalıbıyla seçim kalmadı |
| Ana küme | Etiketler + `ppe-split_pgdata` + çalışıyor olma. Doğrulanmazsa klasör oluşmadan reddediliyor. Gerçek Docker testinde kanıtlandı (§6.2) |
| Geçici küme | `ephemeral_cluster()`: internal ağ, tmpfs, rastgele parola, etiket, işaret. `restore_check` ve `migration_rehearsal` yalnızca bunu kullanıyor |
| Release | `build_image.py` release bağlamı `git archive <sha>:dashboard`; temiz ağaç zorunlu, `latest` korumalı |
| **Tutarsızlık 1 (düzeltildi)** | `backup.py` belgesi bakım penceresi için `--quiesce` öneriyordu. Dashboard kapalıyken `--quiesce` FAIL-CLOSED reddediliyor. Belge ve prosedür düzeltildi: bakımda `--quiesce` **kullanılmaz** |
| **Tutarsızlık 2 (düzeltildi)** | `secret_audit.py` üretim `.env`'ini kendi worktree'sinden okuyor. Integration worktree'sinde `.env` olmadığı için **referans değer olmadan** "0 eşleşme = PASS" verebiliyordu. Önceki koşular `ppe-split-main`'den yapılmış (179 izlenen dosya), dolayısıyla geçerli. Ek: `--prod-root` ve referans yoksa FAIL |
| Tutarsızlık 3 (açık, P3) | `steelbench.isolation_fingerprint()` üretim outbox'ını ve snapshot klasörünü betiğin worktree'sine göre izliyor (`c.ROOT/data/snapshots`). Integration'dan çalıştırılırsa yanlış klasörü izler. Önceden de vardı; yedek prosedürünü etkilemez. Sonraki reliability işinde etiketten (`working_dir`) çözülmeli |

## 2. Commit (B)

### 2.1 Ön kontroller

| Kontrol | Sonuç |
|---|---|
| `git diff --check` (yeni dosyalar dahil, `--cached` ile tekrar) | Temiz |
| Stage | **23 yol tek tek** belirtildi (`git add -- <yol>…`); `-A`/`.` kullanılmadı. `__pycache__` ve `build-manifests/` yok sayılıyor |
| Secret audit + desen taraması | PASS (§6.3) |
| İlgili testler | PASS (§6) |

### 2.2 Commit içeriği

`65fe6ba chore(reliability): harden backup isolation and release preparation`

- **FAZ 02-A (değişmeden):** container kimliği, izole restore, snapshot kaynağı, `db_guard` host listesi, `REL_PROJECT` koruması, deterministik release bağlamı, `PPE_DASHBOARD_IMAGE`, migration provası, soak disk koruması, iki rapor.
- **FAZ 02-A.1 ekleri (bu faz, testli):**
  1. `backup.py --min-free-gb` (varsayılan 2 GiB): yazmadan, dashboard'u durdurmadan ve klasör oluşturmadan önce `(DB boyutu + snapshot) × 2` ayrılır; yer kalmıyorsa **FAIL-CLOSED**. Yardımcılar `ppe_ops.capacity_problems/existing_parent/dir_bytes`.
  2. `scripts/backup_preflight.py` (yeni, salt okunur bakım ön kontrolü) ve `scripts/tests/test_backup_preflight.py` (22 test).
  3. `secret_audit.py`: `--prod-root`, referans yoksa FAIL, commit'lenmeye aday (izlenmeyen) dosyaları da tarama.
  4. `docs/BACKUP_RESTORE.md`: ön kontrol, kapasite koruması ve `--quiesce` düzeltmesi. Örnek yol yer tutucuya çevrildi.

Gerekçe: tek, anlamlı ve geri alınabilir değişiklik grubu. Bakım prosedürü ve release adayı bu commit'e dayanıyor. Geri alma `git revert 65fe6ba` ile yapılır; geçmiş yeniden yazılmaz.

**Bu rapor commit'te yok:** commit SHA'sını içerdiği için commit'ten sonra yazıldı ve izlenmeyen dosya olarak duruyor. Bir sonraki anlamlı commit'e eklenmesi ya da onayla tek başına commit'lenmesi önerilir.

## 3. Üretim veri envanteri (C) — yalnız meta veri, DB kapalı

| Konu | Değer | Kaynak |
|---|---|---|
| Compose projesi | `ppe-split`. Config `C:\Users\PC\Desktop\ppe-split-main\docker-compose.yml`, working_dir `…\ppe-split-main` | Container etiketleri |
| Container'lar (etiketle) | db `8389a9943f83`, dashboard `559aa4f5f627`, migrate `0b6ffb5abca7`, db-roles `e4ba0ad6e908`. Hepsi exited | `docker ps -a --filter label=…` |
| DB container | `8389a9943f833c1a…`, `postgres:17@sha256:b8faeff7…`. Exited (0), StartedAt 2026-11-12T08:03:31Z, FinishedAt **2026-11-12T10:19:57Z**. Faz boyunca değişmedi (3 ayrı karşılaştırma) | `docker inspect` |
| Volume | `ppe-split_pgdata` → `/var/lib/postgresql/data`, oluşturulma 2026-10-28T12:38:45Z, etiketler `ppe-split`/`pgdata`. Boyut **134,7 MB** (WAL ve katalog dahil; döküm bundan çok küçük) | `docker volume inspect`, `system df -v` |
| Snapshot kaynağı | Dashboard bind `C:\Users\PC\Desktop\ppe-split-main\data` → `/data`. `data\snapshots`: **385 dosya, 6,1 MiB**; en yeni 2026-11-12T09:07Z | `docker inspect`, dosya sistemi |
| Outbox | `…\ppe-split-main\detector\outbox`: 4 kamera, pending 0, dead 0, quarantine 0. Son yazma 2026-11-12T09:07Z | `*.status.json` (SQLite açılmadı) |
| Son doğrulanmış yedek | `backups\ppe-20261102T030016Z`: 0006; dump 52 165 B; 51 snapshot (1,9 MB). Olay aralığı 2026-10-28 13:26 → **2026-11-01 08:42:53 UTC**. Bu fazda izole olarak **yeniden doğrulandı: PASS** | `restore_check` |
| Korunması gereken kapsam | Bütün DB: 0006; cameras, detector_runs, incidents, violation_events, person_run_reports, app_users, user_sessions, audit_log. Son yedekten sonraki pencere: **2026-11-01 08:42 → 2026-11-12 10:19 UTC** (yaklaşık 11 gün; FAZ 02-A'ya göre 323+ olay ve 334 yedeksiz snapshot). Ayrıca 385 snapshot dosyasının tamamı ve outbox meta verisi | — |
| Tahmini yedek boyutu | Düz: dump < 1 MB (0006'da 52 KB) + snapshot 6,1 MiB ≈ **7–10 MiB**. Şifreli kopya da yaklaşık aynı. Ön kontrolün üst sınırı (volume boyutu ile): 134,6 MiB × 2 = **0,26 GiB** | §4 |
| Restore için geçici alan | Geri yükleme ve prova geçici kümede **tmpfs**'te (Docker VM RAM'i) yapılıyor: < 200 MB RAM, host diski yaklaşık 0. Gerekli imajlar (`postgres:17`, RC, `latest`) zaten mevcut | `ppe_ops.ephemeral_cluster` |

## 4. Disk kapasitesi ve güvenli temizlik planı (D)

### 4.1 Ölçümler (salt okunur)

| Kaynak | Değer | Not |
|---|---|---|
| C: | 452 GiB; boş **28,97–32,42 GiB** (faz içinde dalgalandı; %93–94 dolu) | Başka harici veya ikinci veri diski yok (yalnızca kurtarma bölümleri) |
| Docker VHDX `docker_data.vhdx` | Dosya **28,47 GiB**; içeride 27,1 GB kullanılmış; sanal üst sınır 1 TB; **sparse değil** | Docker'ın gördüğü "928 GB boş" yanıltıcı; gerçek sınır C:'nin boş alanı |
| İmajlar | 24,36 GB (21 adet); "geri kazanılabilir" 15,69 GB | Bunun ~13,8 GB'ı **başka projelerin** imajları: flowise ×2 9,7 GB, llminsight-frontend-check 1,45 GB, n8n, node. Silinmez, öneri dışı |
| PPE imajları | 12 etiket; her biri yaklaşık 73 MB benzersiz (ortak taban 240 MB) | Kullanılmayan PPE imajlarının hepsi ≈ 0,6 GB; silmek anlamlı yer açmaz |
| Build cache | 7,70 GB (295 kayıt, 0 aktif) | Bütün projelerin ortak önbelleği; temizlik projeler arası bir karar |
| Volume'lar | 1,98 GB. `ppe-split_pgdata` 134,7 MB, `ppe-rel_*` 192 MB, `ppe-eval_*` 49 MB | Üretim volume'u hiçbir koşulda temizlik kapsamında değil |
| Container'lar | 20 (2 çalışıyor: `career-agent-*`), 50 MB | Faz başı ve sonu aynı |
| `%TEMP%` (`C:\Users\PC\AppData\Local\Temp`) | **13,06 GiB** | En büyük güvenli aday (§4.2) |
| PPE verisi (`ppe-split-main`) | snapshots 6,1 MiB, outbox 0,7 MB, `backups\` 10,7 MB | — |

### 4.2 Kapasite değerlendirmesi

| İş | Tahmini tüketim | Değerlendirme |
|---|---|---|
| DB'yi başlatma (WAL, checkpoint) | < 100 MB (VHDX içinde) | Sorun yok |
| Üretim yedeği (düz + şifreli + `.ops`) | ≈ 20 MiB (üst sınır 0,26 GiB) | **Yeterli.** `backup.py` 2 GiB tabanla FAIL-CLOSED |
| Restore ve prova | Host diskinde ≈ 0 (tmpfs) | Sorun yok |
| Yeni RC imajı | Ölçülen artış ≈ 0 (katmanlar paylaşılıyor) | Oluşturuldu |
| FAZ 02-B 24 saat soak | PG ~1,6 MB/saat, outbox ~1,2 MB/saat (24 saatte ≈ 70 MB), samples ve loglar birkaç yüz MB. **≤ 2 GB bütçe** | Plandaki 25 GB eşiği (§4.3) şu an karşılanıyor ama marj dar. `--min-disk-gb 5` koruması devrede |

**Sonuç:** yedek için temizlik **gerekmiyor**. Asıl risk:
- Windows güncellemesi ya da başka bir uygulama C:'yi doldurursa hem PostgreSQL (VHDX) hem outbox yazamaz.
- Aynı diskteki yedek, disk kaybına karşı **koruma sağlamaz**. Felaket kurtarma için şifreli kopyanın makine dışına (harici disk ya da ikinci bilgisayar) taşınması gerekir.

**Güvenli temizlik seçenekleri (hiçbiri uygulanmadı; hepsi kullanıcı kararı):**

| # | Seçenek | Kazanç | Güvenlik notu |
|---|---|---|---|
| T1 | `%TEMP%` temizliği: Ayarlar → Sistem → Depolama → Geçici dosyalar (ya da Disk Temizleme) | ≤ 13 GiB | Önce açık uygulamalar kapatılır. Kullanımdaki dosyalar atlanır. PPE verisi içermez |
| T2 | Build cache (`docker builder prune`, isteğe bağlı `--filter until=168h`) | ≤ 7,7 GB **VHDX içinde** | Bütün projelerin önbelleğini etkiler. Imaj ve volume silmez. **C:'ye geri kazandırmaz** (VHDX sparse değil, bkz. T4) |
| T3 | Kullanılmayan PPE imajları: `ppe-dashboard-dev:6ddabd371c34-dirty`, `ppe-dashboard:faz10a-rel-base`, (FAZ 02-B'den sonra) `ppe-dashboard:0.10.7-rc.faz02a` | ≈ 0,2 GB | `latest`, `faz9-rel` (8 container kullanıyor), `review-test`, `ppe-dashboard-test:dev` ve `rollback-*` **korunur**. Değer düşük |
| T4 | VHDX sıkıştırma: Docker Desktop kapalıyken yönetici `diskpart` → `compact vdisk`, ya da Docker Desktop sürümünün sunduğu sparse/reclaim seçeneği | T2 ve T3'te boşalan alanı C:'ye geri verir | Yönetici yetkisi gerekir. Önce Docker Desktop tamamen durdurulur. Sürüme göre doğrulanmalı |
| T5 | Başka projelerin imajları (flowise vb.) | ~13 GB | **PPE kapsamı dışında**; ilgili proje sahibinin kararı |

Yasak olanlar: `docker system prune`, `docker volume prune`, mevcut yedeklerin, outbox'ın ve snapshot'ların silinmesi.

### 4.3 Yedek hedef konumu (yeni bulgu)

- `backup_preflight.py`, önerilen hedef `C:\Users\PC\ppe-local-backups\prod` için **FAIL** verdi: `C:\Users\PC` bir Git çalışma ağacı.
  - Bu depo başka bir projeye ait (GitHub remote'u var); `.git/index` bozuk.
- **Risk:** o depoda yapılacak bir `git add -A` + push, şifresiz üretim yedeğini (işçi görüntüleri = kişisel veri) GitHub'a taşıyabilir. Bu depoya **dokunulmadı**.
- **Seçenekler:**
  - (a) **Önerilen:** Git dışı, yalnızca sahibine açık yeni bir kök, örneğin `C:\PPE-Backups`. `C:\` altında yeni klasörler "Authenticated Users: Modify" devralır; bu yüzden ACL'nin açıkça daraltılması gerekir:
    ```powershell
    New-Item -ItemType Directory C:\PPE-Backups | Out-Null
    icacls C:\PPE-Backups /inheritance:r /grant:r "${env:USERDOMAIN}\${env:USERNAME}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F"
    icacls C:\PPE-Backups          # yalnız kullanıcı + SYSTEM görünmeli
    ```
  - (b) Şifreli kopyanın ayrıca harici diske yazılması (felaket kurtarma için zorunlu, §5).
  - (c) `C:\Users\PC\.git` için ayrı karar: depoyu ait olduğu klasöre taşımak ya da `.git/info/exclude`. PPE dışı bir değişiklik olduğu için kullanıcı kararı.
- Mevcut `ppe-local-backups\faz01-*` ve `faz02a-*` yalnızca kod ve patch içeriyor (gizli değer yok); taşınmaları da (a)'daki karara bağlı.

## 5. Yedek şifreleme (E)

### 5.1 Durum (bu makine)

| Araç | Durum |
|---|---|
| `age` / `age-keygen` | **Kurulu değil.** `winget show FiloSottile.age` → 1.3.2, BSD-3-Clause. Kurulum **yapılmadı** (onay gerekiyor) |
| GnuPG | `C:\Program Files\Git\usr\bin\gpg.exe` 2.4.5 (PATH'te değil) |
| `tar` | Windows yerleşik `tar.exe` (bsdtar) |
| Disk şifrelemesi (Cihaz şifrelemesi) | Windows 11 Home; durum yönetici yetkisi olmadan okunamıyor (**BLOCKED: yönetici**) |

### 5.2 Önerilen tasarım: age, iki bağımsız alıcı

FAZ 02-A §5.3'ün güncellenmiş hali:
- **Her emanetçinin kendi anahtar çifti olur.** Yedek iki alıcıya birden şifrelenir (`age -R` dosyasında iki satır). Böylece özel anahtar kopyalanmaz: tek emanetçi kaybolursa diğeri açar, birinin anahtarı sızarsa yalnızca o rotasyona girer.
- Anahtarlar saha makinesinde değil, **güvenli bir bilgisayarda** üretilir. Özel anahtar dosyası parola ile korunur (age identity'nin `age -p` ile şifrelenmesi).
- Saha makinesinde yalnızca **alıcı dosyası** (açık anahtarlar) bulunur: ACL'li ve SHA-256'sı kurulum kaydında.
  - `backup_preflight.py --age-recipients <dosya>`, dosyada `AGE-SECRET-KEY` görürse FAIL verir (testli).
- Düz yedek klasörü, şifreli dosya **güvenli bilgisayarda açılıp `restore_check` PASS olduktan sonra** ve onayla kaldırılır.

**Kurulum ve anahtar üretimi (ONAY SONRASI; bu fazda çalıştırılmadı):**

```powershell
# Saha makinesi: araç
winget install --id FiloSottile.age -e
age --version

# GÜVENLİ bilgisayar, her emanetçi kendi oturumunda (A ve B):
age-keygen -o ppe-backup-A.key            # ekrana "Public key: age1..." yazar
age -p -o ppe-backup-A.key.age ppe-backup-A.key ; Remove-Item ppe-backup-A.key   # parola korumalı kimlik
# ppe-backup-A.key.age -> parola yöneticisi / şifreli USB (emanetçi A). B için aynısı.

# Saha makinesi: yalnız açık anahtarlar
Set-Content -Encoding ascii C:\PPE-Backups\recipients.txt "age1<A>`r`nage1<B>"
icacls C:\PPE-Backups\recipients.txt
(Get-FileHash C:\PPE-Backups\recipients.txt -Algorithm SHA256).Hash   # kurulum kaydına
```

**Şifreleme (bakım adımı 10):**

```powershell
$N = Split-Path $B -Leaf
tar -C $BK -cf "$BK\$N.tar" $N "$N.ops"
age -R "$BK\recipients.txt" -o "$BK\$N.tar.age" "$BK\$N.tar"
Remove-Item "$BK\$N.tar"                                   # ara arşiv (içerik zaten düz klasörde)
(Get-FileHash "$BK\$N.tar.age" -Algorithm SHA256).Hash.ToLower() | Set-Content -Encoding ascii "$BK\$N.tar.age.sha256"
```

**Şifreli yedekten izole doğrulama** (güvenli bilgisayarda, ya da emanetçi gözetiminde anahtar USB'den kullanılıp hemen kaldırılarak):

```powershell
Get-FileHash .\ppe-<UTC>.tar.age -Algorithm SHA256        # .sha256 ile aynı olmalı
age -d -i .\ppe-backup-A.key.age -o .\ppe-<UTC>.tar .\ppe-<UTC>.tar.age   # parola sorar
tar -xf .\ppe-<UTC>.tar
python <INT>\scripts\restore_check.py .\ppe-<UTC> --json .\ppe-<UTC>.restore-from-encrypted.json   # PASS
# sonra açılmış düz kopya ve .tar silinir
```

### 5.3 age hazır değilse: geçici seçenekler ve kalan riskler

| Seçenek | Uygulanabilirlik | Kalan risk | Değerlendirme |
|---|---|---|---|
| G1. **Yedeği şimdi düz al**, Git dışı ve ACL'li kökte tut; age hazır olunca şifrele ve düz kopyayı kaldır | Hemen | Kişisel veri dizüstünde düz duruyor. Disk şifrelemesi doğrulanmadıysa çalınmaya karşı korumasız. Makine dışında kopya yok | **Önerilen ara adım.** R1 (11 günlük yedeksiz veri) daha büyük bir risk. Düz kopya makine dışına **çıkarılmaz** |
| G2. `gpg --symmetric --cipher-algo AES256` (Git'in gpg'si) | Hemen | Parola operatör tarafından saha makinesine girilir. Parola gücü ve paylaşımı (iki emanetçi) risk taşır | Kabul edilebilir geçici çözüm. Dosyayı makine dışına taşımak için age'den önce kullanılabilir |
| G3. gpg açık anahtar (anahtar güvenli bilgisayarda, sahada yalnızca açık anahtar) | Hemen, ama yapılandırma ayrıntılı | Keyring ve güven modelinde hata riski | age'e eşdeğer güvenlik; daha karmaşık |
| G4. Yedeği şifreleme hazır olana kadar ertelemek | — | R1 açık kalır. Disk ya da volume kaybında 11 günlük veri geri gelmez | **Önerilmez** |
| DPAPI / 7-Zip / openssl | — | FAZ 02-A §5.2 | Felaket kurtarma yedeği için önerilmez |

## 6. Testler (H) — bu fazda gerçekten koşulanlar

Python: `ppe-split-main\detector\.venv` (3.12.6, pytest 9.2.0). Bütün Docker işleri, kendi oluşturduğumuz ve ID'leriyle sildiğimiz kaynaklarla yapıldı.

### 6.1 Birim ve regresyon

| Test | Sonuç |
|---|---|
| `scripts/tests` (build 16, kimlik 31, **yeni ön kontrol/kapasite 22**) | **69/69 PASS** (son içerikle tekrar koşuldu) |
| Dashboard tam paketi (`isolated_dashboard_tests.py`, işaretli geçici küme) | **427/427 PASS** (71 s) |
| `tools/reliability/test_steelbench.py` | **13 PASS, 1 skip** |
| `py_compile` (değişen tüm betikler + `lan_smoke.py`) | OK |
| `build_image.py --release … --dry-run` kirli ağaçta | **Reddedildi** (23 yol listelendi) |
| `REL_PROJECT=ppe-split` ile `common` import | **Reddedildi** |

### 6.2 Gerçek Docker: izolasyon, negatif bağlantı ve yedek/restore yardımcıları (25/25)

Yem kümeler `--network none` ve `log_connections=on` ile çalıştırıldı.

| # | Test | Sonuç |
|---|---|---|
| 1–8 | D1 ad benzeri ve etiketsiz (`faz02a1-decoy-ppe-split-db`); D2 geçici etiket + kalıcı volume; D3 başka compose projesi + etiket taklidi; D4 etiketli ve tmpfs ama işaretsiz. Her biri için `backup.py --target-container` ve `restore_check --target-container` | Hepsi **reddedildi** (rc=1); yedek klasörü oluşmadı |
| 9–12 | Yem kümelere yapılan bağlantı sayısı | **4 kümede de 0** |
| 13 | Pozitif kontrol: bilerek açılan 1 `psql` bağlantısı | Sayaç +1 (ölçüm duyarlı) |
| 14–15 | `backup.py` ana mod ve ana mod `--quiesce`, üretim DB kapalı | Reddedildi: "container 8389a9943f83 çalışmıyor". Komut gönderilmedi, klasör oluşmadı |
| 16 | Tur: `ppe-20261102T030016Z` dökümü geçici kümeye | PASS |
| 17 | **Kapasite koruması:** `--min-free-gb 100000` | **FAIL-CLOSED**: "boş alan yetersiz…", klasör oluşturulmadı |
| 18 | Tur: `backup.py` (geçici küme, operatör alanı dolu) | PASS: 0006, 51 snapshot, dump 48 763 B |
| 19 | Tur: sayımlar kaynakla aynı (kaynak manifestin tüm anahtarları) | PASS. Yeni araç ek olarak users 1, sessions 3, audit 110 sayıyor |
| 20 | Tur: manifest `source.kind=ephemeral`, operatör ve `missing_referenced=[]` | PASS |
| 21 | Tur: `restore_check` (izole) | PASS |
| 22 | Son üretim yedeğinin yeniden doğrulanması | PASS. Olay aralığı 2026-10-28 13:26 → 2026-11-01 08:42:53 |
| 23 | Temizlik: `faz02a1-*` container, volume ve ağ kalmadı | PASS |
| 24 | Üretim DB meta verisi önce ve sonra aynı | PASS |
| 25 | Diğer 20 container'ın listesi ve durumu aynı | PASS |

**Şeffaflık:**
- İlk koşuda 2 kontrol FAIL verdi. İkisi de **test betiğinin** hatasıydı:
  - (19) Eski biçimdeki manifestle birebir sözlük eşitliği arandı. Aracın kendi `compare_counts` kuralına göre düzeltildi.
  - (25) Paralel koşan dashboard test container'ı "önce" listesine girdi.
- Betik düzeltilip tekrar koşuldu: 25/25.
- Turda kullanılan gerçek snapshot ve döküm kopyaları (scratch klasöründe) iş bitince **silindi**.

### 6.3 Gizli bilgi denetimi

| Denetim | Sonuç |
|---|---|
| `secret_audit.py --prod-root …\ppe-split-main` | **PASS**: 5 referans değer (2 × INGEST_TOKEN, POSTGRES/APP/MIGRATOR parolaları); 217 izlenen + 7 aday dosya; 8,04 MB geçmiş; 10 test container'ı; 0 eşleşme |
| Aynı araç, referanssız (integration kökü) | **FAIL (beklenen)**: "aranabilir üretim değeri yüklenemedi" (fail-closed) |
| Ek tarama (aynı referanslarla): `ppe-split-main\tools\reliability\output` (556), `ppe-local-backups` (17 + tar içindeki 5), `build-manifests` (2) | 0 eşleşme |
| 23 aday dosyada desen taraması (özel anahtar, AGE-SECRET-KEY, AWS, GitHub token, JWT, e-posta, parola ataması, URL kimlik bilgisi, LAN IP) | Gerçek bulgu yok. Eşleşmelerin hepsi `c8204fa`'dan beri commit'li test fixture'ları (`u:p`, `pw-test-only`), compose yer tutucuları, Docker test alt ağı (`172.31.251.0/24`) ya da sürüm numarası (`opencv 4.10.0.84`) |
| Değer yazdırma | Hiçbir gizli değer ekrana veya dosyaya yazılmadı (yalnızca etiketler ve sayımlar) |

### 6.4 Release

Ayrıntılar §8. Kısaca:
- Bağlam tekrarlanabilir: iki bağımsız `git archive` ile manifest aynı.
- Etiketler doğru; `verified=true`.
- `/app`'te `__pycache__`, `tests` ve `.env` yok.
- Prova 11/11.

### 6.5 Koşulmayanlar

| Test | Durum | Gerekçe |
|---|---|---|
| Tarayıcı UI testleri | NOT_TESTED | RC `/app` içeriği eski RC ile `build-info.json` dışında bayt düzeyinde aynı. Uygulama ve statik kod `c8204fa`'dan beri değişmedi. Son PASS FAZ 01'de |
| `lan_smoke.py` uçtan uca | NOT_TESTED | Caddy ve TLS kurulumu gerekiyor; yalnızca `py_compile` |
| Üretim yedeği, restore ve 0007 | **BLOCKED** | Kullanıcı onayı (§10) |
| Disk şifrelemesi durumu | BLOCKED | Yönetici yetkisi |

## 7. Bakım prosedürü (F)

> **Uygulama kaydı:** 2026-11-18 11:22–11:26 UTC'de onayla (K1, G1) çalıştırıldı. 14 adımın hepsi PASS; V1–V8, V10, V11 PASS, V9 uygulanamaz (şifresiz). Tek sapma: adım 2'de araç ağacında yalnız bu raporun izlenmeyen olması. Ayrıntı `C:\PPE-Backups\MAINTENANCE_LOG.md`.

**Süre:** 30–45 dakika, prova dahil.

**Durdurma kuralı:** herhangi bir adım FAIL verirse sonraki adıma geçilmez; adım 14'e (DB'yi durdurma) gidilir. Yedek başarısızsa bu pencerede **migration, dashboard deploy ve veri temizliği yapılmaz.** Ana DB, volume ve snapshot klasörü hiçbir adımda değiştirilmez; DB yalnızca okunur.

```powershell
# Değişkenler (operatör doldurur)
$INT = "C:\Users\PC\Desktop\ppe-split-integration"           # araçlar @ 65fe6ba
$MAIN = "C:\Users\PC\Desktop\ppe-split-main"                 # üretim compose worktree'si (değiştirilmez)
$BK  = "C:\PPE-Backups"                                       # §4.3 (a): Git dışı, ACL'li (önceden oluşturulmuş)
$PY  = "$MAIN\detector\.venv\Scripts\python.exe"
$OP  = "<operatör>"
$env:PYTHONIOENCODING = "utf-8"
```

| # | Adım | Komut / ölçüt |
|---|---|---|
| 1 | **Operatörün açık onayı** | `$BK\MAINTENANCE_LOG.md`'ye tarih (UTC), operatör, onaylayan ve seçilen kapanış durumu (adım 14) yazılır. Dashboard ve dedektör kapalı kalacak |
| 2 | Araç sürümü ve **disk kapasitesi** | `git -C $INT rev-parse HEAD` → `65fe6ba…`, `git -C $INT status --short` → boş. `& $PY -B $INT\scripts\backup_preflight.py --out $BK --json $BK\preflight-prestart.json` → **ÖN KONTROL: PASS** (disk, VHDX, Git dışı, ACL, dedektör, outbox, servisler). Şifreleme kararı verildiyse `--age-recipients $BK\recipients.txt --require-encryption` eklenir |
| 3 | **Docker proje ve volume kimliği** | `$DB = docker ps -a -q --no-trunc --filter label=com.docker.compose.project=ppe-split --filter label=com.docker.compose.service=db` → **tam 1 ID** (`8389a9943f83…` beklenir). `docker inspect $DB --format '{{range .Mounts}}{{.Name}} {{.Destination}}{{end}}'` → `ppe-split_pgdata /var/lib/postgresql/data`. ID kayda yazılır. Başka container'a komut gönderilmez |
| 4 | **Yalnızca DB'nin açılması** | `$T0 = (Get-Date).ToUniversalTime().ToString("s"); docker start $DB`. Healthy beklenir (en fazla 120 s): `docker inspect $DB --format '{{.State.Health.Status}}'`. dashboard, migrate ve db-roles **başlatılmaz** (`docker start <ID>` bağımlılık başlatmaz) |
| 5 | **Tutarlılık** | `docker logs --since $T0 $DB`: "database system was shut down at 2026-11-12 10:19:57" ve "ready to accept connections" **olmalı**; "was interrupted", "redo starts", "automatic recovery", "PANIC", "invalid page" **olmamalı**. Ardından `& $PY -B $INT\scripts\backup_preflight.py --out $BK --stage db-up` → PASS (yalnızca db çalışıyor ve healthy) |
| 6 | **Migration seviyesi** (salt okuma) | `"SELECT version_num FROM alembic_version;" \| docker exec -i $DB sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -AtX'` → **0006**. Farklıysa DUR (§7'yi yeniden değerlendir) |
| 7 | **PostgreSQL yedeği** | `& $PY -B $INT\scripts\backup.py --out $BK --operator $OP`. `--quiesce` **kullanılmaz** (dashboard zaten kapalı). Çıktıda revision 0006, sayımlar ve "snapshot ~385 dosya" görülmeli; `db_changed_during_backup` ve `missing_referenced` uyarısı olmamalı. Kapasite yetersizse araç kendisi reddeder |
| 8 | **Snapshot ve operasyon meta verisi** | Snapshot'lar adım 7'de dump'tan sonra, SHA-256'larıyla kopyalanır. Operasyon meta verisi ayrı bir klasöre alınır (yedeğin `SHA256SUMS`'ı bozulmaz): `$B=(Get-ChildItem $BK -Directory -Filter "ppe-2*" \| Sort-Object Name \| Select-Object -Last 1).FullName; $O="$B.ops"; New-Item -ItemType Directory "$O\outbox" \| Out-Null; Copy-Item "$MAIN\detector\outbox\*" "$O\outbox\"`. Dedektör kapalı, dosyalar kapalı; SQLite **açılmaz**. Envanter yalnızca seçili alanlarla alınır: `docker inspect $DB --format '{{.Id}} {{.Image}} {{.Config.Image}} {{.State.FinishedAt}}' > "$O\inventory.txt"`. **Uyarı:** `docker inspect`'in tam çıktısı `Config.Env` içinde parola içerir; tam çıktı **alınmaz**. `.env` dosyaları bu yedeğe **konmaz** (ayrı, şifreli yapılandırma yedeği: K9) |
| 9 | **SHA-256 manifesti** | Yedek klasörü: araç `SHA256SUMS` yazar. Meta veri: `Get-ChildItem $O -Recurse -File \| Get-FileHash -Algorithm SHA256 \| ForEach-Object { "{0}  {1}" -f $_.Hash.ToLower(), $_.Path.Substring($O.Length+1).Replace('\','/') } \| Set-Content -Encoding ascii "$O\SHA256SUMS"`. Dump özeti ve snapshot sayısı kayda yazılır |
| 10 | **Şifreleme** | K3 verildiyse §5.2 komutları → `.tar.age` ve `.sha256`. Verilmediyse G1: düz yedek `$BK`'de kalır ve risk kaydında **açık** olarak görünür. Makine dışına yalnızca şifreli dosya çıkar |
| 11 | **Bağımsız/geçici PostgreSQL'de restore** | `& $PY -B $INT\scripts\restore_check.py $B --json "$B.restore.json"` → PASS (geçici küme, tmpfs). Önerilen: `& $PY -B $INT\scripts\migration_rehearsal.py $B --candidate-image ppe-dashboard:0.10.7-rc.faz02a1 --json "$B.rehearsal.json"` → 11/11 (0007 kararı için kanıt; üretime bir şey uygulamaz). Şifreli dosyadan doğrulama §5.2'de |
| 12 | **Karşılaştırmalar** | §7.1 ölçütleri |
| 13 | **Doğrulama sonucu ve kurtarma raporu** | `MAINTENANCE_LOG.md`: container ID'leri, volume, `latest` imaj ID'si (`6f4ac355…`), adım 5 log özeti, revision, sayımlar, snapshot sayısı, dump SHA-256, restore ve prova sonuçları, olay aralığı, şifreli dosya adı ve SHA-256, sapmalar. Gizli değer yazılmaz |
| 14 | **Kapanış durumu** (operatörün adım 1'deki seçimi) | Varsayılan: `docker stop $DB`, ardından `docker inspect $DB --format '{{.State.Status}}'` → exited (başlangıç durumu). Seçenekler: (b) 0007 penceresi hemen başlayacaksa DB açık kalır (**ayrı onay**, FAZ 02-A §7.3). (c) Servisin eski imajla yeniden açılması (`docker compose -p ppe-split start` + dedektör): ayrı onay, R1 kapandıktan sonra |

### 7.1 Restore doğrulama ölçütleri (adım 11–12)

| # | Ölçüt | Beklenen |
|---|---|---|
| V1 | `SHA256SUMS` | 0 bozuk ya da eksik |
| V2 | `pg_restore` | rc = 0 (`--exit-on-error`) |
| V3 | Revision | Manifest ile aynı; **0006** |
| V4 | Sayımlar | Manifestteki her tablo geri yüklenen kopyayla aynı (8 tablo: cameras, detector_runs, incidents, violation_events, person_run_reports, app_users, user_sessions, audit_log) |
| V5 | Monotonluk (yanlış volume tespiti) | Her sayım 11-02 yedeğine eşit ya da büyük: cameras ≥ 7, runs ≥ 74, incidents ≥ 47, events ≥ 104, reports ≥ 255, audit ≥ 110. Küçükse **DUR** |
| V6 | Olay aralığı | İlk olay 2026-10-28 13:26. **Son olay > 2026-11-01 08:42:53** ve 2026-11-09…11-12 aralığında. 11-01'de kalıyorsa **DUR**: yanlış küme ya da volume |
| V7 | Snapshot'lar | Yedekte ~385 dosya (adla eşleşenler). Referanslardan eksik 0, özeti bozuk 0; `missing_referenced = []` (değilse liste kayda yazılır ve incelenir) |
| V8 | Run ve kamera | `detector_runs` ve `cameras` sayımı V4'te. Son run başlangıcı (`last_run_started`) 2026-11-09…11-12 |
| V9 | Şifreli kopya | `.sha256` eşleşiyor; açılan arşivde `SHA256SUMS` OK; açılmış kopyada `restore_check` PASS |
| V10 | Prova (önerilen) | Yeni yedek + `0.10.7-rc.faz02a1` → 11/11 |
| V11 | Ana küme ve dosyalar | Adım 14'ten sonra DB exited (varsayılan). `data\snapshots` dosya sayısı ve en yeni dosya zamanı değişmemiş |

## 8. Release imajı (G)

| | |
|---|---|
| Kaynak | Commit `65fe6ba2ff8976a367a097a4d0ce7c481bae52de`, temiz ağaç (`git status` boş) |
| Komut | `python scripts/build_image.py --release 0.10.7-rc.faz02a1` |
| Etiketler | `ppe-dashboard:0.10.7-rc.faz02a1`, `ppe-dashboard:sha-65fe6ba2ff89` → `sha256:ea722e05b99fdbcd…` |
| Bağlam | `git-archive:65fe6ba…:dashboard`, SHA-256 `bfe0a01cb35d1d02…`. İki bağımsız `git archive --mtime=@<commit>` koşusu ve build manifesti **aynı** |
| OCI etiketleri | kind=release, tree-state=clean, revision=65fe6ba…, ref.name=integration/faz-01, version=0.10.7-rc.faz02a1 |
| Uygulama | `/app/build-info.json` → `load_build_info()` → **`verified: True`**. Ağsız, salt okunur, `cap-drop ALL` container'da denendi |
| İçerik | `/app`: 37 dosya; `__pycache__` 0, `tests` 0, `.env` 0. Eski RC'ye göre **yalnızca `build-info.json` farklı** (dosya bazında SHA-256 karşılaştırması). `c8204fa → 65fe6ba` arasında `dashboard/` altında yalnızca 2 test dosyası değişti ve ikisi de imaja girmiyor |
| Prova | `migration_rehearsal.py` (`ppe-20261102T030016Z`, aday `0.10.7-rc.faz02a1`, temel `latest`) → **11/11 PASS** |
| Eski aday | `ppe-dashboard:0.10.7-rc.faz02a` / `sha-c8204fa91d9b` (`1bc80082…`) kendi commit'i olan `c8204fa`'yı gösteriyor. **Yeni commit'in sürümü olarak sunulmuyor**, silinmedi |
| `ppe-dashboard:latest` | `6f4ac355be7b…`. **Değişmedi** (önce/sonra) |
| Yapılmayanlar | Production dashboard başlatılmadı; 0007 uygulanmadı; `PPE_DASHBOARD_IMAGE` üretim `.env`'ine yazılmadı |

Son sürüm numarası (`0.10.7`) ve dağıtım kanalı (`docker save`/`load` ya da registry) kullanıcı kararıdır (K8).

## 9. FAZ 02-B hazır olma durumu

| Kriter (FAZ 02-A §11) | Durum |
|---|---|
| 1. Toplu commit + regresyon | ✅ `65fe6ba`; §6 |
| 2. İzole `ppe-rel` yığını | ⚠️ **Açık (P1):** `tools/reliability/docker-compose.reliability.yml` dört serviste `image: ppe-dashboard:faz9-rel` sabit. Soak bu haliyle **FAZ 9 imajını** (10-A.1 bellek düzeltmesi ve bu commit **olmadan**) ölçer. Önerilen düzeltme: `${REL_DASHBOARD_IMAGE:-ppe-dashboard:faz9-rel}` + soak sonucuna imaj ID'sinin yazılması, ve soak'ın `0.10.7-rc.faz02a1` ile koşulması. Küçük ve ayrı bir değişiklik; FAZ 02-B başlatılmadığı için yapılmadı |
| 3. Disk ≥ 25 GB | ✅ ama dar (29–32 GiB). `--min-disk-gb 5` koruması var |
| 4. Güç ve güncelleme | ❓ Operatör: AC, kapak eylemi, Windows Update duraklatma, bekleyen yeniden başlatma |
| 5. Kabul eşikleri (A5–A11) | ❓ Operatör ve İSG onayı |
| 6. Diğer iş yükleri (`career-agent-*` çalışıyor) | ❓ Kullanıcı kararı |
| Öneri: R1 soak'tan önce kapatılsın | ✅ Üretim yedeği 2026-11-18'de alındı ve doğrulandı (şifresiz, G1) |

**Sonuç:** FAZ 02-B **başlatılmadı**. Teknik araçlar hazır. Başlamadan önce madde 2'deki imaj sabitlemesi yapılmalı, 4–6 karara bağlanmalı ve tercihen R1 kapatılmalı.

## 10. Kullanıcı onayı bekleyen işlemler

| # | İşlem | Seçenekler / öneri | Engellediği |
|---|---|---|---|
| **K1** | **Bakım penceresi: yalnızca ana DB'yi açıp üretim yedeği almak** (§7) | **Uygulandı (2026-11-18 11:22–11:26Z, G1 şifresiz):** `C:\PPE-Backups\ppe-20261118T112231Z`; 0006; events 629, son olay 2026-11-12 09:07Z; dump SHA-256 `66298175…`; 385 snapshot, eksik 0; restore_check PASS; prova 11/11; V1–V8, V10, V11 PASS (V9 n/a); DB yeniden exited. Ayrıntı: `C:\PPE-Backups\MAINTENANCE_LOG.md`. R1 kapandı; G1 riski (şifresiz, makine dışı kopya yok) açık | R1 (P0), 0007, deploy |
| **K2** | **Yedek kök klasörü** | (a) **Uygulandı (2026-11-18):** `C:\PPE-Backups` oluşturuldu; kalıtım kesildi; yalnız `<makine>\PC` + SYSTEM `(OI)(CI)F`; alt dosyalar aynı ACL'yi devralıyor; `backup_preflight.py --out C:\PPE-Backups --stage pre-start` → PASS (age/şifreleme/araç ağacı WARN). Açık kalan: (b) ek olarak harici disk (şifreli kopya için); (c) `C:\Users\PC\.git` hakkında ayrı karar | K1 |
| K3 | Şifreleme | **age 1.3.2 kuruldu (2026-11-18, winget, kullanıcı kapsamı, installer hash doğrulandı; `%LOCALAPPDATA%\Microsoft\WinGet\Links\age.exe`). Atılabilir anahtarlarla duman testi: iki alıcıya şifreleme → her iki anahtar ayrı ayrı çözdü (SHA-256 aynı), yanlış anahtar ve 1 bit kurcalama reddedildi; test anahtarları silindi. Preflight: şifreleme aracı PASS.** Açık kalan: iki emanetçi, iki ayrı anahtar çifti (güvenli bilgisayarda). Geçici: G1 ya da G2 | Makine dışına kopya |
| K4 | Disk şifrelemesi durumunun doğrulanması | Yönetici: `manage-bde -status C:` ya da Ayarlar → Gizlilik ve güvenlik → Cihaz şifrelemesi | R2 |
| K5 | Disk temizliği | T1 (`%TEMP%` 13 GiB) önerilir. T2–T4 isteğe bağlı. T5 PPE dışı | Soak marjı |
| K6 | 0007 migration penceresi | Ayrı pencere (öneri). Yeni yedek + V10 provası sonrası; FAZ 02-A §7.3 | Deploy |
| K7 | Bu raporun commit'i | Bir sonraki anlamlı commit'e ekle (öneri) ya da onayla tek başına commit'le | — |
| K8 | Release numarası ve dağıtım kanalı | `0.10.7` final etiketi; `docker save`/`load` ya da registry | Deploy |
| K9 | `.env` yedeği (şifreli), `backups/env-*` kopyalarının silinmesi, `POSTGRES_PASSWORD` rotasyonu | FAZ 02-A §5.1 sırasıyla | R2 |
| K10 | FAZ 02-B ön koşulları | §9 madde 2 (imaj), 4, 5, 6 | Soak |
| K11 | GitHub'a push | Yapılmadı; ayrı onay | — |

## 11. Risk kaydı (güncel)

| ID | Risk | Seviye | Değişim |
|---|---|---|---|
| R1 | 11-01 08:42 – 11-12 10:19 arası olaylar ve snapshot'lar yedeksiz | ~~P0~~ **Kapandı** | 2026-11-18: `ppe-20261118T112231Z` (events 629, son olay 11-12 09:07Z), restore PASS. Kalan: G1 riski (aşağıda R12) |
| R12 | Güncel üretim yedeği şifresiz ve tek kopya, aynı diskte (G1) | P1 | Emanetçi anahtarları (K3) gelince şifrele, harici diske kopyala (K2b), düz kopyayı kaldır |
| R2 | Yedekler şifresiz; disk şifrelemesi doğrulanmadı; `backups/env-*`'de canlı parola | **P0** | Tasarım netleşti (iki alıcı); araç kurulu değil (K3, K4, K9) |
| R9 | **Yeni:** yedek klasörü (`ppe-local-backups`) başka projenin Git deposunun içinde | **P1** | Ön kontrol bunu artık FAIL olarak yakalıyor. Konum kararı K2 |
| R3 | C: %93–94 dolu; VHDX sparse değil | P1 | Yedek için yeterli. Soak marjı dar. Temizlik K5 |
| R10 | **Yeni:** reliability yığını `faz9-rel` imajına sabit; soak yeni kodu ölçmez | P1 (02-B) | §9 madde 2 |
| R4 | Çalışan `latest` provenance taşımıyor | P1 | Doğrulanmış RC hazır (`0.10.7-rc.faz02a1`); deploy onay bekliyor |
| R5 | Düzeltmeler commit'lenmemişti; üretim worktree'sinde eski araçlar var | P1 → **kapandı / azaldı** | Commit `65fe6ba`. Bakım araçları integration worktree'sinden çalıştırılır. `ppe-split-main`'deki eski `restore_check` kullanılmamalı |
| R11 | `steelbench` izolasyon parmak izi worktree'ye göre klasör izliyor | P3 | §1.4; sonraki reliability işi |
| R6–R8 | Windows Update/güç, model ve video lisansı, bağımlılık kilidi | P1/P1/P2 | Değişmedi (FAZ 02-A §10) |

## 12. Geri dönüş

- Kod: `git revert 65fe6ba` (geçmiş yeniden yazılmaz); önceki durum `c8204fa`. Repo dışı patch yedeği yerinde.
- Docker: bu fazda kalıcı olarak yalnızca `ppe-dashboard:0.10.7-rc.faz02a1` / `sha-65fe6ba2ff89` etiketleri (`ea722e05…`) ve küçük bir build cache artışı oluştu. Kaldırmak için: `docker rmi ppe-dashboard:0.10.7-rc.faz02a1 ppe-dashboard:sha-65fe6ba2ff89` (kullanıcı kararı).
- Geçici kümeler, ağlar, yem container'ları ve volume'ları kaldırıldı; kalan 0.
- Repo dışı: `build-manifests/ppe-dashboard_0.10.7-rc.faz02a1.json` (Git dışı, gizli değer yok).
