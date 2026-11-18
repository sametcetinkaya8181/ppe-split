# FAZ 02-A — Veri Güvenliği, Yedekleme ve Sürüm Hazırlığı Raporu

| | |
|---|---|
| Tarih | 2026-11-18 |
| Çalışma alanı | `C:\Users\PC\Desktop\ppe-split-integration`, branch `integration/faz-01` @ `c8204fa` + bu fazın commit'lenmemiş değişiklikleri |
| Üretim ortamı | `ppe-split` compose projesi, `C:\Users\PC\Desktop\ppe-split-main` (**dokunulmadı**, durdurulmuş halde) |
| Kapsam dışı (bilerek yapılmadı) | Ana PostgreSQL'i başlatma, üretim yedeği alma, üretim migration'ı, `ppe-dashboard:latest`'i değiştirme, mevcut yedekleri silme veya taşıma, anahtar üretme, push/PR, FAZ 02-B soak |

## 0. Özet ve karar

**Production deployment blocker (P0):** ana veritabanının güncel bir yedeği **yok**.

- Doğrulanmış en yeni yedek `ppe-20261102T030016Z` (0006). Olayları yalnızca **2026-11-01 08:42 UTC**'ye kadar kapsıyor.
- Ana DB **2026-11-12 10:19 UTC**'de kapandı. Arada kalan yaklaşık 11 günün olayları ve **334 snapshot** hiçbir yedekte yok.
- Bu yedek alınmadan 0007 migration'ı ve yeni sürüm dağıtımı yapılmamalı.
- Yedek, ana DB'nin başlatılmasını gerektiriyor; bu da **kullanıcı onayına** bağlı (§3, §12).

Bu fazda tamamlananlar:

| Alan | Durum |
|---|---|
| Ana veri ve yedek durumu (salt okunur) | Belgelendi (§2) |
| Üretim yedeği için bakım prosedürü | Hazır; araçları izole ortamda sınandı. Gerçek koşu onay bekliyor (§3) |
| Yönetim ve test araçlarında container kimliği | 8 açık kapatıldı. 16 gerçek Docker negatif testi PASS, yem kümelere **0 bağlantı** (§4) |
| `backups/env-*` ve yedek şifrelemesi | Durum belgelendi, seçenekler karşılaştırıldı. Anahtar kararı **BLOCKED (kullanıcı)** (§5) |
| Release güvenliği | Release bağlamı artık commit'in kendisi (`git archive`). Deterministik release adayı oluşturuldu ve doğrulandı (§6) |
| Migration 0007 | Tekrarlanabilir prova aracı yazıldı. Gerçek 0006 yedeğiyle **11/11 PASS** (§7) |
| FAZ 02-B soak hazırlığı | Altyapı incelendi, 2 küçük düzeltme yapıldı. Plan: `PHASE_02B_SOAK_TEST_PLAN.md` |
| Regresyon | Dashboard 427/427, detector 391/391, scripts 47/47, SteelBench 13 + 1 skip, secret audit PASS (§9) |

Kararlar:
- **FAZ 02-B'ye geçiş:** §11'deki kriterlere bağlı. Soak, üretim verisinden bağımsız izole yığında koşacağı için üretim yedeği soak'ı engellemez. Ancak disk doluluğu (%94) ve güç ayarları kriterleri karşılanmalı.
- **Üretim dağıtımı:** NO-GO. Blocker: güncel yedek, şifreleme kararı, release commit'i.

## 1. Çalışma kuralları ve doğrulama

- **Git başlangıcı:**
  - `integration/faz-01` = `c8204fa` ve temiz.
  - `github-main` = `2cfdf06`; 8 WIP dosyası ve izlenmeyen `docs/productization/` yerinde.
  - Tüm branch ve remote ref'leri FAZ 01 dış yedeğindeki `refs.txt` ile birebir aynı.
- **Branch ve commit:**
  - Bu fazda branch veya commit **oluşturulmadı**; değişiklikler integration worktree'sinde commit'lenmemiş duruyor (§13).
  - Release adayı için `c8204fa`'nın geçici, ayrık (detached) bir checkout'u kullanıldı ve iş bitince `git worktree remove` ile kaldırıldı.
- **Üretim:** `ppe-split-*` container'ları başlatılmadı. `ppe-split-db-1` meta verisi (durum `exited`, `FinishedAt` 2026-11-12T10:19:57Z) faz boyunca değişmedi; negatif testte önce/sonra karşılaştırıldı.
- **Başka projeler:** `career-agent-*` ve `llminsight-*` container'larına hiçbir komut gönderilmedi. Yalnızca `docker ps` listesi önce ve sonra karşılaştırıldı: 20 container, değişiklik yok.

## 2. Ana PostgreSQL ve veri durumu (salt okunur tespit)

| Konu | Bulgu | Kanıt |
|---|---|---|
| Compose proje kimliği | `ppe-split` (compose dosyasında `name: ppe-split` sabit). Config dosyası `C:\Users\PC\Desktop\ppe-split-main\docker-compose.yml` | Container etiketleri |
| DB container | `ppe-split-db-1`, ID `8389a9943f83…`, `postgres:17@sha256:b8faeff7…`, restart `unless-stopped`, host'a port yok | `docker inspect` |
| Durum | **Exited (0)**, 2026-11-12 10:19:57 UTC. Log: `fast shutdown` → `checkpoint complete` → `database system is shut down`, yani **temiz kapanış** | `docker logs` (son satırlar) |
| Kendiliğinden başlama riski | `unless-stopped` ve elle durdurulmuş, bu yüzden Docker Desktop yeniden başlasa bile başlamaz | Docker davranışı |
| Volume | `ppe-split_pgdata` (2026-10-28'de oluşturulmuş), `/var/lib/postgresql/data`'ya bağlı | `docker volume inspect` |
| Migration seviyesi | **0006 (çıkarım, doğrudan okunmadı).** Çalışan imaj `ppe-dashboard:latest` (`6f4ac355`) yalnızca 0001–0006'yı içeriyor; migrate container'ının tüm logunda `Running upgrade` satırı yok (0 adet). Kesin değer DB açılınca `alembic_version`'dan okunacak (§3 adım 3) | İmaj içeriği, log |
| Dashboard imajı | `ppe-dashboard:latest` = `6f4ac355` (2026-11-09). FAZ 01 öncesi WIP build'i: `BUILD_INFO`/provenance yok | `app.BUILD_INFO` kontrolü |
| Doğrulanmış son yedek | `backups/ppe-20261102T030016Z`: 0006, quiesce yok ama `db_changed_during_backup=false`. Olaylar 7, 74, 47, 104, 255; users 1, sessions 3, audit 110; 51 snapshot. **Bu fazda yeniden doğrulandı: PASS** | `restore_check` (izole) |
| Yedeklerin kapsadığı olaylar | İlk olay 2026-10-28 13:26 UTC, son olay **2026-11-01 08:42:53 UTC** (0005 ve 0006 yedeklerinde aynı) | `restore_check` "kapsanan olay aralığı" |
| Yedeklenmemiş pencere | 2026-11-01 08:42 → 2026-11-12 10:19 UTC. Dedektör 11-09…11-12 arasında çalıştı: 4 kamera, son başlatmadan beri **323 olay** gönderilmiş. `data/snapshots` 385 dosya; yedekte 51; **11-04/07/10/12 tarihli 334 dosya hiçbir yedekte yok** | Outbox status, dosya tarihleri |
| Snapshot konumu | `C:\Users\PC\Desktop\ppe-split-main\data\snapshots` (dashboard'a `/data` olarak bind; 7,1 MB) | `docker inspect` |
| Outbox konumu | `C:\Users\PC\Desktop\ppe-split-main\detector\outbox\cam1…cam4.sqlite3` + `*.status.json`. **Bekleyen 0**, ölü 0, karantina 0; 554 teslim edilmiş satır saklama penceresinde | status.json, SQLite (salt okunur) |
| Erişim | `backups/`, `.env` ve dump'lar için ACL devralınmış: Administrators, SYSTEM, PC (Users/Everyone yok). Masaüstü OneDrive'a yönlendirilmemiş (`User Shell Folders\Desktop` = `C:\Users\PC\Desktop`) | `icacls`, kayıt defteri |
| Şifreleme | Yedekler **şifresiz**. İşletim sistemi **Windows 11 Home**: klasik BitLocker yönetimi yok, "Cihaz şifrelemesi" durumu yönetici yetkisi olmadan okunamadı (**BLOCKED: yönetici**) | `manage-bde`: erişim reddedildi |
| Disk | C: 452 GB, **%94 dolu, 30 GB boş**. Docker: imajların 15,7 GB'ı ve build cache'in 7,7 GB'ı geri kazanılabilir (silme kararı kullanıcının) | `df`, `docker system df` |

**Not (şeffaflık):** outbox SQLite dosyalarını `mode=ro` ile okurken SQLite, WAL kipindeki veritabanlarının yanına boş `-wal` (0 bayt) ve `-shm` yan dosyaları oluşturdu.
- Ana `.sqlite3` dosyalarının değişmediği doğrulandı: boyut ve mtime 11-12 12:07 olarak kaldı.
- Açık süreç yoktu.
- Yalnızca bu 8 yan dosya silindi; klasör eski haline döndü.

Sonraki salt okunur incelemelerde `immutable=1` URI parametresi ya da kopya kullanılmalı.

## 3. Üretim yedeği için bakım prosedürü (onay bekliyor; bu fazda ÇALIŞTIRILMADI)

**Amaç:** 0007'den önce tutarlı, doğrulanmış ve şifrelenmiş bir üretim yedeği almak.

**Süre tahmini:** 30–45 dakika. DB küçük (0006 dökümü 52 KB; 11-12 itibarıyla birkaç yüz olay).

**Kimin onayı:**
- Proje sahibi: DB'yi başlatma ve dashboard'u durdurma.
- Veri sorumlusu: şifreleme anahtarı.

**Araçlar:**
- `scripts/backup.py`, `scripts/restore_check.py`, `scripts/migration_rehearsal.py` (bu fazda güncellendi ve sınandı).
- Bu araçlar üretim worktree'sinde henüz yok. İntegration worktree'sinden çalıştırılabilirler, çünkü hedefi etiketlerden, snapshot klasörünü de dashboard bağlamasından çözüyorlar.

**Durdurma kuralı:** aşağıdaki herhangi bir adım FAIL verirse bir sonraki adıma **geçilmez**. Sistem başlangıç durumuna döndürülür (§3.9) ve denetim kaydına yazılır.

### 3.1 Başlangıç ön kontrolleri (DB kapalıyken, salt okunur)

```powershell
cd C:\Users\PC\Desktop\ppe-split-integration
git -C C:\Users\PC\Desktop\ppe-split-main status --short          # WIP 8 dosya, beklenen
docker ps -a --filter label=com.docker.compose.project=ppe-split --format "{{.ID}} {{.Names}} {{.Status}}"
docker volume inspect ppe-split_pgdata --format "{{.Name}} {{.CreatedAt}}"
Get-PSDrive C                                                      # >= 10 GB boş olmalı
```

Beklenen:
- Tam 4 container: db, db-roles, migrate, dashboard (etiketle; ada bakılmaz).
- `ppe-split_pgdata` mevcut.
- Dedektör süreçleri kapalı (`Get-Process python` içinde `detector.py` yok).
- `detector\outbox\*.status.json` içinde `pending_count = 0`.

### 3.2 Yalnız PPE container'larının seçilmesi

- Bütün komutlar container **ID**'siyle verilir.
- ID'ler 3.1'deki etiket filtresinden alınır ve denetim kaydına yazılır.
- `docker compose` komutları yalnızca üretim worktree'sinde ve `-p ppe-split` ile çalıştırılır.
- Araçlar (`ppe_ops`) aynı doğrulamayı kendileri de yapar: etiketler, `ppe-split_pgdata` volume'u ve çalışıyor olma. Doğrulanmayan hedefe komut göndermezler.

### 3.3 Yalnız DB'yi başlatma ve tutarlılık kontrolü

```powershell
cd C:\Users\PC\Desktop\ppe-split-main
docker compose -p ppe-split start db          # YALNIZ db; dashboard ve dedektör KAPALI kalır (yeni yazma olmaz)
docker compose -p ppe-split ps db             # healthy beklenir
docker logs --tail 20 ppe-split-db-1          # "database system is ready", kurtarma (recovery) satırı OLMAMALI
```

Dashboard kapalı olduğu için yedek kendiliğinden tutarlı olur; `--quiesce` gerekmez. Dashboard'u durdurma ve başlatma adımı da böylece hiç olmaz.

### 3.4 PostgreSQL yedeği

```powershell
cd C:\Users\PC\Desktop\ppe-split-integration
python scripts\backup.py --out C:\Users\PC\ppe-local-backups\prod --operator "<ad>"
```

- Hedef ve snapshot klasörü otomatik çözülür. Manifest'te `source.project = ppe-split`, `source.volume = ppe-split_pgdata` ve `source.snapshots_dir = ...\ppe-split-main\data\snapshots` görülmelidir.
- Snapshot sayısı ~385, `missing_referenced = []` ve `db_changed_during_backup = false` beklenir.
- `alembic_revision` burada kesinleşir (0006 beklenir). Farklıysa **dur** ve §7'yi yeniden değerlendir.

### 3.5 Yedek doğrulaması ve izole restore

```powershell
python scripts\restore_check.py C:\Users\PC\ppe-local-backups\prod\ppe-<UTC> --json C:\Users\PC\ppe-local-backups\prod\ppe-<UTC>.restore.json
```

- SHA256SUMS, `pg_restore`, revision, sayımlar ve snapshot referanslarının hepsi PASS olmalı.
- "Kapsanan olay aralığı" **2026-11-12**'ye ulaşmalı. 11-01'de kalıyorsa **dur**: yanlış küme ya da volume demektir.
- Restore otomatik olarak ayrı, geçici bir kümede yapılır; ana kümede veritabanı oluşturulmaz.

### 3.6 Migration ve kayıt sayısı doğrulaması (prova)

```powershell
python scripts\migration_rehearsal.py C:\Users\PC\ppe-local-backups\prod\ppe-<UTC> --candidate-image ppe-dashboard:<release> --json ...\ppe-<UTC>.rehearsal.json
```

- 11/11 PASS gerekli (§7).
- Bu adım **yeni yedekle** tekrarlanır, çünkü 11-02 provası yeni verileri ve yeni kullanıcı/oturum satırlarını kapsamıyor.

### 3.7 Şifreleme (anahtar kararı verildiyse)

- §5 önerisine göre `age -r <alıcı>` ile `ppe-<UTC>.tar.age` oluşturulur ve özeti yazılır.
- Deneme açması güvenli bilgisayarda yapılır.
- Düz klasör, yalnızca onaydan sonra kaldırılır.
- Karar yoksa düz yedek ACL'li proje dışı klasörde kalır. Bu durum risk kaydında **açık** olarak görünür (§10).

### 3.8 Sistemi bırakma

```powershell
cd C:\Users\PC\Desktop\ppe-split-main
docker compose -p ppe-split stop db           # başlangıç durumuna dön (0007 ayrı bir karar/pencere)
```

Migration aynı pencerede yapılacaksa §7.3'e geçilir (ayrı onay).

### 3.9 Başarısızlıkta güvenli durdurma

| Durum | Eylem |
|---|---|
| 3.3'te DB açılmıyor, logda kurtarma hatası var | `docker compose -p ppe-split stop db`. Volume'a **dokunma**. Yeni yedek alınamadığı için 11-02 yedeği son kurtarma noktası olarak kalır. Ayrı bir olay incelemesi açılır |
| 3.4 başarısız | Yarım klasör silinmez, kanıt olarak kalır. DB durdurulur. Mesaj denetim kaydına yazılır |
| 3.5 veya 3.6 FAIL | Yedek "doğrulanmamış" olarak işaretlenir. Migration yapılmaz. DB durdurulur |
| Herhangi bir anda yanlış proje veya container şüphesi | Araçlar zaten reddeder. Elle komut **verilmez** |

### 3.10 Operatör onayı ve denetim kaydı

Kayıt dosyası `C:\Users\PC\ppe-local-backups\prod\MAINTENANCE_LOG.md`. Dosya proje dışında tutulur ve **gizli değer içermez**.

| Alan | Değer |
|---|---|
| Tarih/saat (UTC), operatör, onaylayan | |
| Başlangıç: container ID'leri (db/dashboard), volume, `latest` imaj ID | |
| 3.3 DB açılış logu özeti (kurtarma var/yok) | |
| 3.4 yedek klasörü, revision, sayımlar, snapshot sayısı, dump SHA-256 | |
| 3.5 restore_check sonucu ve kapsanan olay aralığı | |
| 3.6 prova sonucu (11 kontrol) | |
| 3.7 şifreli dosya adı ve SHA-256, deneme açması (kim, nerede) | |
| 3.8 bitiş durumu (DB durduruldu mu) | |
| Sapmalar ve kararlar | |

## 4. Docker ve proje izolasyonu

### 4.1 Bulunan açıklar ve düzeltmeler

| # | Açık | Risk | Düzeltme |
|---|---|---|---|
| F1 | `ppe_ops.set_target()` hedefin kimliğini, **hedefin veritabanına sorgu göndererek** öğreniyordu (`SELECT current_setting('cluster_name')`) | FAZ 01'deki `career-agent-postgres` olayının kök nedeni: kimlik doğrulanmadan önce başka bir projeye sorgu gidiyordu | Önce yalnızca `docker inspect` meta verisi doğrulanıyor: `ppe.ephemeral-db=1` etiketi, compose projesine ait olmama, yalnızca tmpfs veri, komut satırında işaret, yayınlanmış port olmaması ve çalışıyor olma. `cluster_name` sorgusu yalnızca ikinci kontrol olarak yapılıyor |
| F2 | Ana DB yolu `docker compose exec db` idi: kimlik kontrolü yoktu ve çözüm, çalıştırılan worktree'nin compose dosyasına bağlıydı | Yanlış ya da taklit container'a komut gidebilirdi | Ana DB `ppe-split`/`db` etiketli **tek** container olmalı ve `ppe-split_pgdata` volume'unu bağlamış olmalı. Komutlar doğrulanmış **tam ID** ile `docker exec` üzerinden gidiyor (`scripts/docker_identity.py`) |
| F3 | `restore_check` varsayılan olarak `ppe_restore_verify` veritabanını **ana kümede** oluşturuyordu | Üretim kümesine yazma | Restore her zaman ayrı, geçici bir kümede yapılıyor (`ephemeral_cluster()`: kendi oluşturduğu, ID ile silinen container ve internal ağ). Ana küme yolu kaldırıldı |
| F4 | `backup.py` snapshot'ları **betiğin worktree'sinden** (`ROOT/data/snapshots`) alıyordu | Integration worktree'sinden çalıştırılırsa yedek **sessizce 0 snapshot** ile "başarılı" olurdu | Kaynak artık ana dashboard container'ının `/data` bağlaması; klasör yoksa yedek reddediliyor. Manifeste `source` ve `operator` ekleniyor. Quiesce, doğrulanmış dashboard ID'si üzerinden yapılıyor |
| F5 | Reliability araçlarında `container()` = `f"{PROJECT}-{service}-1"` (yalnızca ad); `REL_PROJECT=ppe-split` engellenmiyordu | `failures.py` ana DB'yi `docker stop` edebilir, `reset_data()` TRUNCATE çalıştırabilirdi | `REL_PROJECT=ppe-split` modül yüklenirken reddediliyor. `container()` compose etiketleriyle çözüp tam ID döndürüyor. `steelbench` ve `baseline_eval` ana DB parmak izini adla değil, etiket ve volume ile alıyor |
| F6 | `db_guard` URL'de yalnızca `db` host'unu reddediyordu | `localhost`/`127.0.0.1`/`host.docker.internal` ya da başka bir servis adı verilirse **bağlantı denenirdi** | İzinli host listesi: `test-db`, `ppe-review-test-db`. Diğer adreslere bağlantı denenmeden reddediliyor (motor bile oluşturulmuyor; testli) |
| F7 | Compose `tests` profili proje adı olmadan çalıştırılınca test container'ları **üretim projesinin** (`ppe-split`) içinde oluşuyordu | `docker compose down` gibi proje çapındaki bir komut üretimi de etkilerdi | Belge ve yorumlar `-p ppe-split-test` olarak güncellendi. `deploy/lan/lan_smoke.py` artık `-p ppe-split-test` kullanıyor |
| F8 | Compose'da üç servis `image: ppe-dashboard` (= `latest`) + `build:` kullanıyor | Herhangi bir worktree'den `up --build` çalıştırılırsa üretim imajı yeniden yazılır | `image: ${PPE_DASHBOARD_IMAGE:-ppe-dashboard}`: varsayılan davranış aynı; üretimde sürüm etiketiyle sabitlenip `up --no-build` kullanılması öneriliyor. Doğrulama: `compose config` (ayrı proje adıyla, kaynak oluşmadı) |

**Kalan sınır:** `dashboard/tests/db_guard.py` sunucu kimliğini yine bağlantıyla doğruluyor (`cluster_name`). Bu kabul edilebilir, çünkü bağlantıdan önce F6 host listesi uygulanıyor ve bu adlar yalnızca internal test ağlarında çözülüyor.

### 4.2 Negatif testler

**Birim testleri:** `scripts/tests/test_docker_identity.py`, 31 test, docker çağırmaz. Kapsanan durumlar:
- Ana küme kuralları: başka proje, başka servis, sahte etiket ama farklı volume, tmpfs, geçici etiket.
- Geçici küme kuralları: etiketsiz, compose'a ait, kalıcı volume, bind, tmpfs yok, işaret yok, port yayını var.
- Yabancı ve benzer adlı container (`career-agent-postgres`, `ppe-split-db-1`, `ppe-eval-db-1`) için `set_target` **hiç komut üretmeden** reddediyor.
- Ana DB çözümü 0 veya 2 container, taklit volume ya da durmuş container durumunda exec göndermeden reddediyor.
- Doğrulanmış hedefe komut ad yerine tam ID ile gidiyor.

**Gerçek Docker testleri:** yalnızca kendi oluşturduğum yem container'lar kullanıldı. Hepsi `--network none` ve `log_connections=on` ile çalıştı.

| Test | Sonuç |
|---|---|
| Ad benzeri, etiketsiz, işaretli küme (`faz02a-decoy-lookalike-ppe-split-db`) | PASS: reddedildi |
| Geçici etiketli ama kalıcı volume'lu küme | PASS: reddedildi |
| Başka bir compose projesine ait (`faz02a-decoy-foreign`), geçici etiket taklidi | PASS: reddedildi |
| Etiketli ve tmpfs'li ama işaretsiz küme | PASS: reddedildi |
| `restore_check --target-container` (2 yem) | PASS: rc=1 |
| `backup.py --target-container` (2 yem) | PASS: rc=1, klasör oluşmadı |
| `backup.py --target-container` ama `--snapshots-dir` yok | PASS: reddedildi |
| `backup.py` ana mod (üretim DB'si durmuş) | PASS: "container 8389a9943f83 çalışmıyor", komut gönderilmedi, klasör oluşmadı |
| Reliability `REL_PROJECT=ppe-split` | PASS: import reddedildi |
| Reliability `container()` servis etiketi uyuşmayan projede | PASS: reddedildi |
| Yem kümelere yapılan bağlantı sayısı | **PASS: 4 kümede de 0.** Pozitif kontrol: bilerek yapılan tek `psql` bağlantısı sayacı 0'dan 1'e çıkardı, yani ölçüm duyarlı |
| Üretim DB meta verisi önce/sonra | PASS: aynı |
| Diğer 20 container'ın listesi ve durumu | PASS: aynı |
| Temizlik | PASS: yem container ve volume kalmadı |

## 5. Şifrelenmemiş yedekler ve şifreleme tasarımı

### 5.1 `backups/env-20261028T172010Z` (değerler okunmadan, yalnızca anahtar adlarıyla karşılaştırıldı)

| Dosya | Boyut | Anahtar sayısı (yedek / canlı) | Hassas anahtarlar |
|---|---|---|---|
| `root.env` | 963 B | 5 / 8 | `POSTGRES_PASSWORD` **canlı değerle AYNI**; `INGEST_TOKEN` farklı (döndürülmüş); `PPE_APP_PASSWORD` ve `PPE_MIGRATOR_PASSWORD` yedekte yok |
| `detector.env` | 1003 B | 7 / 11 | `INGEST_TOKEN` yedekte yok |

- **Git:** izlenmiyor (`.gitignore:57 backups/`) ve geçmişte hiç commit'lenmemiş (`git log --all -- backups/*` boş). Tüm geçmiş taramasında da (8 MB) eşleşme yok.
- **İzinler:** ACL devralınmış (Administrators, SYSTEM, PC). Yönetici yetkisi olan her yerel hesap ve bu klasörü kopyalayan her yedekleme aracı okuyabilir.
- **Gereklilik:**
  - Bu kopyalar **güncel yapılandırmanın geri yüklenmesi için yeterli değil**: rol parolaları ve güncel token yok.
  - DB geri yüklemesi için de gerekli değil: `db.dump` rol parolası içermiyor; roller `.env` ile `db-roles` servisinden yeniden kuruluyor.
  - Tek değerleri **canlı süper kullanıcı parolasının düz kopyası** olmaları. Risk taşıyorlar, kurtarma değerleri yok.
- **Ek bulgu:** `tools/reliability/output/**/detector.env` (60'tan fazla dosya) ve `rel.env` test yığınlarına ait. Secret audit bunlarda üretim değeri bulmadı (556 metin dosyası, 0 eşleşme).

**Öneri (onay gerekli):**
1. Canlı `.env` (kök ve dedektör) §5.3'teki yöntemle şifrelenerek proje dışında yedeklenir.
2. Eski `backups/env-*` kopyaları, şifreli yeni kopya doğrulandıktan **sonra** kullanıcı onayıyla güvenli biçimde silinir.
3. `POSTGRES_PASSWORD` 28 Ekim'den beri düz kopyada durduğu için döndürülmesi değerlendirilir (bakım penceresinde; `docs/SECURITY.md`).

### 5.2 Seçeneklerin karşılaştırılması (Windows saha bilgisayarı)

| Yöntem | Güvenlik | Taşınabilirlik | İşletim | Bu makinede | Değerlendirme |
|---|---|---|---|---|---|
| **age (X25519, açık anahtar)** | Modern AEAD. Makine yalnızca **alıcı anahtarını** bilir: şifreler ama çözemez. Ele geçirilen makine eski yedekleri açamaz | Tek binary (Windows/Linux/macOS). Dosya biçimi açık spesifikasyon | Anahtar parolası makinede durmadığı için otomasyona uygun | **Kurulu değil** (winget `FiloSottile.age`). Lisans BSD-3 | **Önerilen** |
| GnuPG (açık anahtar) | Güçlü, ama yapılandırma ayrıntılı (keyring, güven modeli) | Yaygın | Daha karmaşık; hata riski yüksek | Git Bash ile gelen `gpg 2.4.5` var | Yedek seçenek: hemen kullanılabilir |
| Windows DPAPI | Kullanıcı ya da makine profiline bağlı | **Taşınamaz.** Profil veya disk kaybolursa veri de kaybolur | Şeffaf | `pywin32` (`win32crypt`) var | Felaket kurtarma yedeği için **uygun değil**. Yalnızca yerel sırların (ör. alıcı anahtar dosyasının) korunmasına yardımcı |
| 7-Zip AES-256 (parola) | Parola gücüne bağlı | İyi | Otomatik yedek için parolanın makinede durması gerekir | Kurulu değil | Önerilmez |
| `openssl enc` | CLI'da AEAD yok, KDF ve parametre hatası riski | İyi | — | `OpenSSL 3.2.3` var | Önerilmez |
| Disk şifrelemesi (BitLocker/Cihaz şifrelemesi) | Canlı veriyi (volume, snapshot, outbox) **çalınan diske** karşı korur; açık oturumdaki saldırgana karşı korumaz | — | Şeffaf | Win 11 Home; durum **doğrulanamadı** | Yedek şifrelemesinin yerine geçmez, **ikisi birlikte** gerekli |

### 5.3 Önerilen tasarım ve anahtar ayrımı

Ayrıntılı akış `docs/BACKUP_RESTORE.md` "Yedek şifreleme" bölümünde.

- **Anahtarlar:**
  - Saha bilgisayarında yalnızca **açık anahtar** (`age1…`) durur. Bu dosya ACL ile korunur ve özeti kurulum kaydına yazılır, çünkü değiştirilmesi yeni yedeklerin saldırganın anahtarına gitmesi anlamına gelir.
  - **Özel anahtar saha bilgisayarında hiç bulunmaz.** Git'e, `.env`'e, loga veya rapora yazılmaz.
- **Kopyalar:** iki emanet tutulur.
  - (a) Veri sorumlusu: parola yöneticisi ya da şifreli USB.
  - (b) BT sorumlusu.
- **Kurtarma:** şifreli dosya ve özeti güvenli bilgisayara taşınır → özet doğrulanır → `age -d` → `SHA256SUMS` → `restore_check` (izole) → gerekirse felaket prosedürü uygulanır. Açılan düz kopya iş bitince silinir.
- **Tatbikat:** her anahtar rotasyonunda ve en az 3 ayda bir izole geri yükleme yapılır. Anahtarın erişilebilir olduğunun tek kanıtı budur.
- **Anahtar kaybı senaryoları:**

| Senaryo | Sonuç | Önlem |
|---|---|---|
| Tek emanet kayboldu | Diğer emanetle kurtarılır | Hemen yeni çift üretilir; eski özel anahtar, saklama süresi boyunca kalan emanette tutulur |
| İki emanet de kayboldu | O anahtarla şifrelenmiş **tüm yedekler kalıcı olarak açılamaz** | İki ayrı kişi ve ortam zorunlu; düzenli tatbikat |
| Özel anahtar sızdı | Eski yedekler okunabilir | Rotasyon; yeni yedekler yeni anahtarla; sızıntı KVKK olay prosedürüne girer |
| Saha makinesi ele geçirildi | Eski şifreli yedekler okunamaz; canlı veri disk şifrelemesine bağlı | Disk şifrelemesi doğrulanır; alıcı anahtar dosyasının özeti kontrol edilir |

### 5.4 BLOCKED: kullanıcı kararı gerekli

- Şifreleme aracı onayı (`age` kurulumu, lisans BSD-3) ya da `gpg` ile devam kararı.
- Anahtar çiftini kimin, hangi güvenli bilgisayarda üreteceği ve iki emanetin kimde duracağı.
- Disk şifrelemesi durumunun yönetici yetkisiyle doğrulanması: `manage-bde -status C:` ya da Ayarlar → Gizlilik ve güvenlik → Cihaz şifrelemesi.
- `backups/env-*` kopyalarının şifreli yeni kopya doğrulandıktan sonra silinmesi ve `POSTGRES_PASSWORD` rotasyonu.

## 6. Build ve release hazırlığı

### 6.1 FAZ 01 provenance mekanizmasının incelenmesi

İmaja şu bilgiler OCI etiketleri ve `/app/build-info.json` olarak yazılıyor:
- `revision` (commit SHA), `ref.name` (branch), `created` (build zamanı), `version`.
- `com.ppe.build.kind` ve `com.ppe.build.tree-state`.

Uygulama açılışta `verified` değerini hesaplıyor ve `/api/ops/metrics` altında yalnızca admin'e gösteriyor. `verified` = release + temiz ağaç + 40 karakterlik hex SHA. Bu fazda kontrol edilen kurallar:

| Kural | Sonuç |
|---|---|
| Kirli ağaçtan release | **Reddedildi** (rc=2; 16 kirli yol listelendi) |
| Geçersiz SemVer (`v1`) | **Reddedildi** |
| Var olan release etiketiyle yeniden build | **Reddedildi** (etiketler değişmez) |
| `latest` yazılması | Yapılmıyor; `PROTECTED_TAGS` ile korunuyor. `latest` bu faz boyunca `6f4ac355` olarak kaldı |
| Geliştirme ve release karışması | Ayrı depo adları var (`ppe-dashboard-dev:<sha12>[-dirty]` / `ppe-dashboard:<semver>`). Geliştirme imajında `verified=false` (`ppe-dashboard-dev:6ddabd371c34-dirty` ile doğrulandı) |

**Bulunan açık ve düzeltmesi:**
- **Açık:** release build bağlamı **çalışma ağacıydı** ve `dashboard/.dockerignore` yok. Git'in yok saydığı yerel dosyalar (`__pycache__`, yanlışlıkla bırakılmış bir `.env`, test çıktıları) Dockerfile'ın kopyaladığı klasörlerden "temiz" bir release imajına girebilirdi. "Temiz ağaç" kontrolü yok sayılan dosyaları görmüyor.
- **Düzeltme:** release artık `git archive --mtime=@<commit zamanı> <sha>:dashboard` arşivinden build ediliyor; bağlam commit'in kendisi.
  - Arşiv deterministik. İlk denemede tree arşivinin o anki zamanı kullandığı ve özetin her koşuda değiştiği görüldü; commit zamanı verilerek düzeltildi.
  - Bağlam SHA-256'sı build manifestine yazılıyor. Geliştirme build'leri çalışma ağacından yapılmaya devam ediyor.

### 6.2 Release adayı (izole)

| | |
|---|---|
| Kaynak | `c8204fa`'nın geçici, temiz ve ayrık checkout'u (sonra kaldırıldı) |
| Etiketler | `ppe-dashboard:0.10.7-rc.faz02a`, `ppe-dashboard:sha-c8204fa91d9b` → imaj `sha256:1bc80082…` |
| Bağlam | `git-archive:c8204fa…:dashboard`, SHA-256 `10257cc9…`. Bağımsız hesaplamayla **aynı** |
| Etiketler doğrulandı | kind=release, tree-state=clean, revision=c8204fa…, version=0.10.7-rc.faz02a; uygulama `verified=true` |
| İçerik | `/app` içinde `__pycache__` 0, `tests` 0, `.env` 0 |
| Notlar | İlk RC (bağlamı deterministik olmayan) bu oturumda oluşturulan iki etiketi kaldırılarak yeniden oluşturuldu. Branch etiketi `detached`, çünkü ayrık checkout kullanıldı. Gerçek release, FAZ 02-A değişikliklerinin commit'inden sonra o commit'ten ve yeni bir sürüm numarasıyla alınmalı |

Ana dashboard yeniden oluşturulmadı, ana DB'ye bağlanılmadı, üretim dağıtımı yapılmadı.

### 6.3 Kurulabilir release için gerekenler

| Bileşen | Nasıl sağlanır | Durum |
|---|---|---|
| Dashboard imajı | `python scripts/build_image.py --release <semver>` (temiz commit). Hedef makineye `docker save` / `docker load` ile ya da özel registry'den aktarılır; özet manifestle karşılaştırılır | Araç hazır; registry kararı yok |
| Compose | `PPE_DASHBOARD_IMAGE=ppe-dashboard:<semver>` (`.env`) + `docker compose -p ppe-split up -d --no-build`. Üç servis (db-roles, migrate, dashboard) aynı imajı kullanır | Değişken eklendi |
| PostgreSQL | `postgres:17@sha256:b8faeff7…` (digest sabit) | Hazır |
| Dedektör | Host'ta Python 3.12 venv: `detector/requirements.txt` (ultralytics 8.4.183, opencv-python 4.10.0.84, requests, lap, yt-dlp) + CUDA'lı torch (2.14.1+cu126; GPU sürücüsü 566.07). Kurulum `docs/DEPLOYMENT.md`'de | Sürüm kilidi (hash'li lock dosyası) **yok** (P2) |
| Model | `detector/models/best.pt`, 5 479 954 bayt, SHA-256 `6886ad2f…fe67ba43` (bu fazda değişmedi). `*.pt` `.gitignore`'da; Git'e **eklenmez**. Ayrı bir kanaldan (şifreli arşiv ya da erişim kontrollü depolama) dağıtılır, kurulumda SHA-256 doğrulanır | Dedektör açılışta özet doğrulamıyor; öneri: `MODEL_SHA256` kontrolü (ayrı iş, karar mantığına dokunmaz) |
| Lisans | Model checkpoint'i AGPL-3.0 (Ultralytics `construction-ppe` ile eğitildi). Ticari dağıtım için Ultralytics lisans kararı gerekli. Stok videolar lisansı doğrulanmadığı için dağıtılmaz | **BLOCKED (kullanıcı/hukuk)** |

## 7. Migration 0007 dağıtım hazırlığı

### 7.1 İzole prova (`scripts/migration_rehearsal.py`, gerçek `ppe-20261102T030016Z` yedeği)

**Kurulum:**
- Aday imaj `ppe-dashboard:0.10.7-rc.faz02a` (release).
- Temel imaj `ppe-dashboard:latest` (yalnızca çalıştırıldı, değiştirilmedi).
- Küme: internal ağ, tmpfs, rastgele parolalar.
- Akış üretim compose'uyla aynı: `pg_restore` → `db.roles setup` → migrator rolüyle `alembic upgrade head && db.roles grants`.

| # | Kontrol | Sonuç |
|---|---|---|
| 1 | Yedek bütünlüğü, geri yükleme, başlangıç revision'ı (0006) | PASS |
| 2 | `upgrade head`: 0006 → 0007 | PASS |
| 3 | Mevcut kayıtlar: 8 tabloda (5 çekirdek + users, sessions, audit) sayımlar **ve** satır md5 özetleri birebir aynı | PASS |
| 4 | İnceleme tabloları boş. Uygulamanın kendi okuma yolu (`db.history.list_incidents`, `ppe_app` rolüyle) **47/47 incident'ı UNREVIEWED** döndürüyor; geri doldurma yok | PASS |
| 5 | Yetkiler: `ppe_app` → `incident_reviews` S/I/U; `incident_review_history` ve `audit_log` yalnızca S/I (UPDATE ve DELETE yok); `alembic_version` yalnızca S; hiçbir tabloda DELETE/TRUNCATE yok; süper kullanıcı değil; inceleme tablolarının sahibi `ppe_migrator` | PASS |
| 6 | Aday imaj hazır (`/health/ready` 200, revision 0007) | PASS |
| 7 | **Eski imaj 0007 şemasında:** `/healthz` 200 ama `/health/ready` **503** (`schema: outdated`, expected 0006) | PASS (kısıt doğrulandı) |
| 8 | İnceleme kaydı varken `downgrade 0006` **reddedildi**; revision ve kayıt yerinde | PASS |
| 9 | 0007 dökümü ayrı bir DB'ye aktarıldı (döküm container içinde akıyor, host'a yazılmıyor): inceleme kaydı ve tüm özetler aynı | PASS |
| 10 | **Başarısız migration:** çakışan nesne bilerek eklendi → upgrade rc=1, işlem **atomik geri alındı** (revision 0006, geçmiş tablosu yok, özetler aynı). Aday imaj bu şemada **hazır değil** (503 outdated). Engel kaldırılınca ikinci deneme başarılı | PASS |
| 11 | İnceleme verisi yokken `downgrade 0006` başarılı, özetler aynı; ardından yeniden upgrade başarılı | PASS |

FAZ 01'deki elle prova (0005→0006→0007→0006→0007, md5 aynı) bu araçla tekrarlanabilir hale geldi.

### 7.2 Sürüm uyumluluk kısıtları

| | 0006 şeması | 0007 şeması |
|---|---|---|
| Eski imaj (0006 head, `latest`) | Hazır | Canlı ama **hazır değil** (`/health/ready` 503). Compose healthcheck `/healthz` kullandığı için container "healthy" görünür; izleme readiness'e bakmalı |
| Yeni imaj (0007 head) | Canlı ama **hazır değil** | Hazır |

Sonuç: imaj ve şema **birlikte** değişmeli. Migration başarısız olursa eski imajla devam edilmeli; şema zaten 0006'da kalıyor (adım 10).

### 7.3 Dağıtım planı (ayrı onay; §3'ten sonra, aynı pencerede yapılabilir)

1. Ön koşullar:
   - §3 tamamlanmış olmalı: yeni yedek, 3.5 PASS, 3.6 yeni yedekle 11/11 PASS.
   - Release imajı FAZ 02-A commit'inden alınmış olmalı.
   - Dedektör kapalı ve outbox'ta bekleyen olay yok.
2. `.env` içinde `PPE_DASHBOARD_IMAGE=ppe-dashboard:<semver>`.
3. `docker compose -p ppe-split up -d --no-build`. db-roles → migrate (0007 + grants) → dashboard sırasıyla çalışır.
4. Smoke kontrolleri:
   - `/health/ready` 200.
   - Admin `/api/ops/metrics` → `build.verified=true` ve doğru revision.
   - Geçmiş ekranı: tüm incident'lar "İncelenmedi".
   - `db.roles check` yetki özeti §7.1-5 ile aynı.
5. Dedektör başlatılır; outbox `delivered` sayısı artmalı, `pending` 0'a dönmeli.
6. Denetim kaydına sürüm, imaj ID'si ve smoke sonuçları yazılır.

### 7.4 Geri alma prosedürü ve sınırları

| Durum | Geri alma | Veri sonucu |
|---|---|---|
| A. Migration başarısız (adım 3) | Hiçbir şey yapılmaz. Şema atomik olarak 0006'da kalır; `PPE_DASHBOARD_IMAGE` eski değere döndürülür ve `up -d --no-build` | Kayıp yok |
| B. Migration başarılı, smoke FAIL, **henüz inceleme kararı yok** | `docker compose -p ppe-split run --rm migrate alembic downgrade 0006` (yeni imajla) → eski imaj | Kayıp yok (adım 11'de doğrulandı) |
| C. **İnceleme kararları girildikten sonra** geri dönme | Downgrade **reddedilir** (adım 8). Seçenekler: (1) **Önerilen:** ileri düzeltme (yeni sürüm). (2) `alembic -x allow_data_loss=1 downgrade 0006`: **inceleme kararları silinir**; önce `incident_reviews` ve `incident_review_history` dışa aktarılıp şifreli saklanmalı ve açık yazılı onay alınmalı. (3) Migration öncesi yedekten geri yükleme: inceleme kararlarına **ek olarak** yedekten sonraki tüm yeni olaylar da kaybolur | (2) ve (3) veri kaybıdır; yalnızca yazılı kararla |
| D. Veritabanı bozuldu | `docs/BACKUP_RESTORE.md` felaket prosedürü + §3 yedeği | Yedek noktasına döner |

## 8. Reliability hazırlığı (özet; ayrıntılar `PHASE_02B_SOAK_TEST_PLAN.md`)

Mevcut altyapı (`tools/reliability/soak.py`, `sampler.py`, `memtest.py`, `memboot.py`) 24 saatlik koşuya büyük ölçüde hazır:
- 4 saatlik ön koşu (2026-11-01) **4656/4656 olay kayıpsız ve tekrarsız** teslim etti.
- Dashboard bellek düzeltmesi (10-A.1) kodda mevcut.

Bu fazda yapılan küçük düzeltmeler (sampler ve soak):
- Disk boşluğu metrikleri (`disk_free_gb`, `disk_used_pct`).
- `soak.py --min-disk-gb` disk koruması (varsayılan 5 GB). Bellek korumasıyla aynı desende çalışır: eşiğin altında 50 saniye kalırsa güvenli durdurur.
- Örnek dosyasına her 10 örnekte bir `fsync`. Ani kapanmada en fazla yaklaşık 10 örnek kaybolur.
- Reliability araçlarına üretim projesi koruması (F5).

Eksikler: dedektörde Python heap ile native bellek ayrımı yok; gerçek RTSP kamera yok; Windows Update ve disk riski. Bunlar planda ele alındı. **Soak başlatılmadı.**

## 9. Test sonuçları (bu fazda koşuldu; eski sonuçlar yeni PASS sayılmadı)

| Test | Sonuç | Not |
|---|---|---|
| Dashboard regresyon (`isolated_dashboard_tests.py`, tam paket) | **PASS 427/427** | 418 + 9 yeni `db_guard` testi; işaretli geçici küme |
| Detector (`detector/`, tam paket) | **PASS 391/391** | `.env` izolasyonu FAZ 01'deki gibi |
| Scripts birim testleri | **PASS 47/47** | 14 build + 2 yeni bağlam testi + 31 kimlik testi |
| SteelBench birim testi (`steelbench.py` değişti) | **PASS 13, 1 skip** | Disk koruması eklendikten sonra yeniden koşuldu |
| `soak.py --help` ve sampler disk metrikleri (duman testi) | **PASS** | CSV ve özette `disk_free_gb`; 24 saatlik koşu yapılmadı |
| Yedek doğrulama, 7 yedek, yeni izole varsayılan | **PASS 7/7** | 0002, 0003, 0003, 0004, 0005, 0005, 0006 |
| Yedek turu: izole `backup.py --target-container` → `restore_check` | **PASS** | Manifest `source` ve `operator` alanları doğru; scratch kopyası silindi |
| Migration provası 0006 → 0007 (11 kontrol) | **PASS 11/11** | §7.1 |
| İzolasyon negatif testleri (gerçek Docker) | **PASS 16/16** | §4.2; ayrıca pozitif kontrol PASS |
| Release: kirli ağaç, SemVer, etiket değişmezliği | **PASS** (3 red) | |
| Release adayı build ve doğrulama | **PASS** | Deterministik bağlam, etiketler, `verified=true`, temiz `/app` |
| Compose config (`PPE_DASHBOARD_IMAGE`) | **PASS** | Ayrı proje adıyla; kaynak oluşmadı |
| Secret audit (üretim değerleri ↔ kanıtlar, test container'ları, tüm Git geçmişi) | **PASS** | 556 kanıt dosyası, 179 izlenen dosya, 8,0 MB geçmiş, 0 eşleşme |
| Secret taraması (bu fazın yeni/değişen dosyaları, build manifestleri, çıktılar) | **PASS** | 44 dosya, 0 eşleşme |
| Tarayıcı UI testleri (`ui_check.mjs`, `ui_check_10a7.py`) | **NOT_TESTED** | Uygulama ve statik kod `c8204fa`'dan beri değişmedi (yalnızca test, ops ve betik dosyaları değişti). Son PASS FAZ 01'de; yeni sonuç olarak sayılmadı |
| `lan_smoke.py` | **NOT_TESTED** | Yalnızca proje adı parametresi değişti (`py_compile` geçti); uçtan uca koşu Caddy/TLS kurulumu gerektiriyor |
| Üretim yedeği / restore / migration | **BLOCKED** | Kullanıcı onayı (§3, §12) |
| Disk şifrelemesi durumu | **BLOCKED** | Yönetici yetkisi |

## 10. Kalan P0/P1 riskler

| ID | Risk | Seviye | Durum / sahibi |
|---|---|---|---|
| R1 | 11-01 08:42 – 11-12 10:19 arası olaylar ve 334 snapshot **yedeksiz**. Docker volume ya da disk kaybında geri gelmez | **P0** | §3 bakım penceresi: **kullanıcı onayı** |
| R2 | Yedekler ve `backups/env-*` şifresiz; düz kopyada canlı `POSTGRES_PASSWORD` var; disk şifrelemesi doğrulanmadı | **P0** | §5 kararları: kullanıcı ve veri sorumlusu |
| R3 | C: %94 dolu (30 GB). 24 saatlik soak, yedekler ve build cache için marj dar; dolarsa PostgreSQL ve outbox yazamaz | **P1** | Temizlik kararı (build cache 7,7 GB ve kullanılmayan imajlar 15,7 GB geri kazanılabilir) ya da harici disk |
| R4 | Çalışan `latest` imajı provenance taşımıyor (WIP build) | P1 | Release commit'i + §7.3 |
| R5 | Bu fazın düzeltmeleri commit'lenmedi; üretim worktree'sinde eski araçlar var (ör. eski `restore_check` ana kümeye yazar) | P1 | §13: toplu commit önerisi. Bakımda araçlar integration worktree'sinden çalıştırılmalı |
| R6 | Windows Update etkin saatleri 08:00–02:00, yani 02:00–08:00 arası otomatik yeniden başlatma olabilir. Pilde 1 saat sonra hazırda bekletme | P1 (soak/üretim) | 02-B ön koşulu |
| R7 | Model ve video lisansı (AGPL-3.0 checkpoint) | P1 (ticari dağıtım) | Kullanıcı/hukuk |
| R8 | Dedektör bağımlılıklarında hash kilidi yok; açılışta model özeti doğrulanmıyor | P2 | Sonraki faz |

## 11. FAZ 02-B'ye (24 saatlik soak) geçiş kriterleri

1. Bu fazın değişiklikleri gözden geçirilmiş, tek toplu commit'le `integration/faz-01`'e alınmış ve regresyon tekrar PASS olmuş olmalı.
2. Soak **izole `ppe-rel` yığınında** koşar; üretim verisi ve servisleri kullanılmaz. Üretim yedeği (R1) bu yüzden soak'ı **engellemez**, ancak soak sırasında disk dolarsa üretim volume'u da etkilenir. R1'in soak'tan önce kapatılması önerilir.
3. Diskte en az 25 GB boş alan olmalı (plan §6'daki hesap) ya da kanıt klasörü harici diske yönlendirilmeli.
4. Güç ve güncelleme: AC'ye bağlı, uyku ve hazırda bekletme kapalı (AC'de zaten "asla"), kapak davranışı "hiçbir şey yapma", Windows Update en az 2 gün duraklatılmış olmalı.
5. Kabul eşikleri (plan §7) operatör ve İSG tarafından onaylanmış olmalı.
6. Aynı makinede çalışan diğer iş yükleri (ör. `career-agent` container'ları) için karar verilmeli: durdurmak kullanıcıya ait bir karar; durdurulmazlarsa ölçüm bağlamına yazılmalı.

## 12. Kullanıcı kararı ve onayı gereken işlemler

| # | Karar | Seçenekler | Öneri |
|---|---|---|---|
| K1 | **Bakım penceresi: ana DB'yi başlatıp üretim yedeği almak** (§3) | Şimdi / planlı tarih / ertele (R1 açık kalır) | En kısa sürede. Dashboard ve dedektör kapalıyken yalnızca `db` başlatılır; yaklaşık 30–45 dakika |
| K2 | 0007 migration'ını aynı pencerede yapmak (§7.3) | Aynı pencere / ayrı pencere | Ayrı pencere: önce release commit'i ve yeni yedekle prova |
| K3 | Yedek şifreleme aracı ve anahtar emanetçileri (§5.4) | age / gpg; iki emanetçi | age + iki emanet |
| K4 | `backups/env-*` kopyalarını şifreli yedek doğrulandıktan sonra silmek; `POSTGRES_PASSWORD` rotasyonu | Evet / hayır | Evet (sırayla) |
| K5 | Disk temizliği (Docker build cache ve kullanılmayan imajlar) | Hangi imajların kalacağı | Önce `latest`, `rollback-*` ve RC imajlarının korunacağı netleşmeli |
| K6 | FAZ 02-A değişikliklerinin toplu commit'i (§13) | Evet / düzenleme | Evet |
| K7 | Release sürüm numarası ve imaj dağıtım kanalı | `docker save` / registry | — |

## 13. Git durumu, commit önerisi ve geri dönüş

- **Branch:** `integration/faz-01` @ `c8204fa`. HEAD değişmedi; commit yok.
- **Değişen 15 dosya:**
  - Belgeler: `docs/BACKUP_RESTORE.md`, `docker-compose.yml`.
  - Betikler: `scripts/ppe_ops.py`, `scripts/backup.py`, `scripts/restore_check.py`, `scripts/build_image.py`, `scripts/tests/test_build_image.py`.
  - Testler: `dashboard/tests/db_guard.py`, `dashboard/tests/test_db_guard.py`.
  - Dağıtım ve reliability: `deploy/lan/lan_smoke.py`, `tools/reliability/common.py`, `tools/reliability/sampler.py`, `tools/reliability/soak.py`, `tools/reliability/steelbench.py`, `tools/reliability/baseline_eval.py`.
- **Yeni 5 dosya:** `scripts/docker_identity.py`, `scripts/migration_rehearsal.py`, `scripts/tests/test_docker_identity.py` ve bu iki rapor.
- **Uygulama, model ve karar mantığı:** `dashboard/app.py`, `db/`, `auth/`, `static/`, `detector/`. **Değişmedi.**
- **Commit önerisi:** **tek** toplu commit, örneğin "FAZ 02-A: veri güvenliği araçları, container kimlik doğrulaması, deterministik release, migration provası".
  - Gerekçe: değişiklikler testli ve birbirine bağlı (ortak `docker_identity`), üretim bakım prosedürü bunlara dayanıyor ve gerçek release bu commit'ten alınmalı.
  - Kullanıcı onayıyla yapılacak; push yapılmayacak.
- **Geri dönüş:**
  - Değişiklikler yalnızca çalışma ağacında; `c8204fa` korunuyor.
  - Repo dışı yedek: `C:\Users\PC\ppe-local-backups\faz02a-20261117T235058Z\`. İçinde izlenen değişikliklerin patch'i, yeni dosyaların tar arşivi ve `SHA256SUMS` var. ACL yalnızca sahibine açık, gizli değer yok. Patch'in çalışma ağacıyla birebir eşleştiği `git apply --check -R` ile doğrulandı.
  - `git diff > faz02a.patch` ile saklanıp, dosya bazında `git restore <yol>` ile geri alınabilir.
  - Yeni dosyalar silinerek kaldırılabilir.
- **Docker'da bu fazda oluşan kalıcı nesneler:**
  - `ppe-dashboard:0.10.7-rc.faz02a` ve `ppe-dashboard:sha-c8204fa91d9b` etiketleri (aynı imaj `1bc80082…`).
  - Build cache.
  - Silmek için `docker rmi ppe-dashboard:0.10.7-rc.faz02a ppe-dashboard:sha-c8204fa91d9b` (kullanıcı kararı).
  - Geçici kümeler, ağlar, yem ve uygulama container'ları temizlendi; kalan 0.
