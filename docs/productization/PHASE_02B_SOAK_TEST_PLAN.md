# FAZ 02-B — 24 Saatlik Soak Testi Planı

| | |
|---|---|
| Hazırlanma | 2026-11-18 (FAZ 02-A). **Bu plan henüz uygulanmadı; soak başlatılmadı.** |
| Ortam | İzole `ppe-rel` yığını (`tools/reliability/docker-compose.reliability.yml`, ayrı proje adı, port 8100, rastgele test kimlik bilgileri). Üretim (`ppe-split`) verisi ve servisleri **kullanılmaz** |
| Araç | `tools/reliability/soak.py` + `sampler.py` (+ `--memprobe` ile `memtest.py` / `memboot.py`) |
| Eşikler | Bu belgedeki eşikler **öneridir**. Kritik kabul sınırları operatör ve İSG tarafından onaylanmadan "kabul ölçütü" olarak kullanılmaz (§7) |

## 1. Amaç

24 saatlik kesintisiz çalışmada şu soruları yanıtlamak:
1. Dedektör ve dashboard belleği **plato** yapıyor mu, yoksa sınırsız mı büyüyor?
2. Kamera başına işleme hızı ve gecikme zamanla bozuluyor mu?
3. Her olay **kayıpsız ve tekrarsız** PostgreSQL'e ulaşıyor mu, outbox geri boşalıyor mu?
4. Kamera kopma ve donmalarından sonra sistem kendiliğinden toparlanıyor mu, bu durum raporlanıyor mu?
5. Disk, GPU sıcaklığı ve host kaynakları 24 saat boyunca güvenli aralıkta kalıyor mu?

## 2. Mevcut altyapının değerlendirmesi (FAZ 02-A incelemesi)

| Soru | Durum | Kanıt / not |
|---|---|---|
| Bellek düzeltmesi mevcut mu? | **Evet (dashboard).** 10-A.1: çok satırlı yazımlar `executemany`, session fabrikası motor başına, hazırlık sorgusu derlenmiş önbellekte | `dashboard/db/repository.py`, `session.py`, `health.py`; `test_statement_cache.py`. Ölçüm: önce/sonra 90 dakikalık koşular (`20261102T023118Z_mem_base90`, `..._mem_fixall90`) |
| CPU ve RSS toplanıyor mu? | **Evet.** Dedektör süreç ağacının RSS, CPU, handle ve thread sayıları (psutil); kamera başına süreç RSS ve handle; dashboard ve DB için docker stats + cgroup anon/file/kernel; sistem CPU ve belleği; GPU kullanımı, belleği, sıcaklığı, gücü ve saat hızı (nvidia-smi) | `sampler.py` |
| Python heap ve native bellek ayrılabiliyor mu? | **Dashboard: evet** (`--memprobe`: RSS, glibc arena/in-use/free/mmap, Python blok sayısı, nesne ve sınıf sayıları). **Dedektör: hayır.** Yalnızca süreç RSS'i ve `torch_reserved_mb` var; Python heap, native ve CUDA ayrımı yok | Boşluk B1 (§9) |
| Kamera başına FPS ve gecikme ölçülüyor mu? | **Evet.** `proc_fps`, `source_fps`, `lag_sec` (anlık ve p95), kare, inference, ByteTrack ve monitor süreleri (p50/p95/p99), kapsam (değerlendirilmeyen track-frame) | `*.health.json` → sampler |
| Olay teslim sayıları karşılaştırılabiliyor mu? | **Evet.** Dedektör gönderici sayaçları ↔ PostgreSQL `count(*)` ve `count(DISTINCT event_id)` (kayıp ve tekrar); bekleyen, ölü ve karantina sayıları; run durumları | `soak.py` sonu |
| Disk ve outbox büyümesi izleniyor mu? | **Kısmen → düzeltildi.** Outbox klasör boyutu, outbox WAL/DB boyutu ve PG DB boyutu vardı. **Eklendi:** `disk_free_gb` ve `disk_used_pct` (sampler) ile **disk koruması** (`soak.py --min-disk-gb`, varsayılan 5 GB: 50 saniye eşik altında kalırsa güvenli durdurma) | FAZ 02-A değişikliği |
| Kamera kopması ve yeniden bağlanma raporlanıyor mu? | **Evet** (TCP/MPEG-TS ağ kamerası benzeticisi). `reconnect_attempts`, `camera_disconnected`/`no_signal`/`lagging` bayrakları, run `end_reason`. **Gerçek RTSP kamera yok** | Boşluk B2 |
| Makine kapansa bile sonuçlar korunuyor mu? | **Kısmen → iyileştirildi.** `samples.jsonl` her örnekte flush ediliyordu; **eklendi:** her 10 örnekte `fsync`. `result.json` (teslim karşılaştırması, özet) **yalnızca koşu sonunda** yazılıyor. Ani kapanmada samples, dedektör logu, outbox ve `ppe-rel` DB'si kalır ve teslim karşılaştırması bunlardan elle yeniden hesaplanabilir (§8.3) | Boşluk B3 (otomatik kısmi sonuç yok) |
| Windows güç ve uyku ayarları etkiler mi? | **Evet, risk var.** Güç planı "Performance". AC'de uyku ve hazırda bekletme **kapalı**. **Pilde 3600 s sonra hazırda bekletme.** Modern Standby (S0, ağ bağlantısı kesik): kapak kapanırsa ağ kopar. Kapak eylemi okunamadı. **Windows Update etkin saatleri 08:00–02:00**, yani 02:00–08:00 arası otomatik yeniden başlatma olabilir. Makine bir laptop (pil var) | `powercfg`, kayıt defteri. Ön koşul §4 |

## 3. Donanım ve kamera profilleri

**Donanım:**
- Bu makine: AMD Ryzen 7 6800HS (8 çekirdek/16 iş parçacığı), **15,3 GB RAM**, sayfa dosyası 32 GB.
- GPU: NVIDIA RTX 3050 Laptop, 4 GB, sürücü 566.07.
- Disk: C: 452 GB, **%94 dolu (~30 GB boş)**.
- Docker Desktop 28.5.1.

Mevcut kanıt (aynı makine):

| Profil | Kaynak | Süre | Kamera başına işleme FPS p50 | Gecikme p95 | GPU kullanımı p95 | Sistem belleği p95 | Dedektör RSS p50 |
|---|---|---|---|---|---|---|---|
| 4 kamera, stride 2 (3 dosya + 1 ağ benzeticisi, arızalı) | `20261101T214427Z_soak4h` | **4 saat** | 12,0–12,4 | ≤ 0 s (ağ kamerasında 19,5 s maks, kopmalar) | 63 % | 14,95 GB | 1 792 MB |
| 4 kamera, stride 2 | `stress_final` | 115 s | 11,9–12,6 | ≤ 0 s | 65 % | 15,12 GB | 4 834 MB* |
| 5 kamera, stride 2 | `stress_final` | 115 s | 11,8–12,6 | ≤ 0 s | 88 % | 15,35 GB | 5 008 MB* |
| 6 kamera, stride 2 | `stress_final` | 115 s | 11,6–12,4 | **0,08–0,90 s** | 92 % | 15,17 GB | 5 957 MB* |

\* Kısa koşu: ısınma (model yükleme) dahil, platoyu göstermez. 4 saatlik koşuda RSS ilk değeri 4 315 MB'tan sonra 1 716–1 866 MB bandına indi.

4 saatlik koşunun başında: kullanılabilir RAM 3 448 MB, **swap kullanımı 3 982 MB**. Bellek, bu makinenin en dar kaynağı.

| Profil | Tanım | Zorunlu mu? | Gerekçe |
|---|---|---|---|
| **P-A (ana)** | 4 kamera, stride 2: 3 yerel video döngüsü + 1 TCP/MPEG-TS ağ kamerası benzeticisi (40 dakikada bir 60 s kopma, yarı periyotta 45 s donma), `--memprobe` | **Evet, 24 saat** | 4 saatlik kanıtla doğrudan karşılaştırılabilir; arıza ve toparlanma dahil |
| P-B (gerçek kamera) | Sahadaki gerçek RTSP kamera(lar), sayı sahaya göre (en az 1), stride 2 | Önerilir, ayrı ve en az 8 saat | Benzetici gerçek RTSP davranışını (kimlik doğrulama, akış yeniden anlaşması, NAT) temsil etmez. Kamera erişimi kullanıcıda |
| P-C (sınır) | 5–6 kamera, stride 2 | İsteğe bağlı, 1–2 saat | Kapasite payını görmek için; 6 kamerada gecikme başlıyor ve GPU %92. 24 saatlik koşu için **önerilmez** |

Video kaynakları: `detector/videos` altındaki üç stok video. Lisansları doğrulanmadığı için yalnızca yerel teknik ölçüm yapılır, dağıtılmaz ve rapora görüntü konmaz.

## 4. Ön koşullar ve hazırlık listesi

**Giriş kriterleri FAZ 02-A raporu §11'de.** Özetle:

1. **Kod:** FAZ 02-A değişiklikleri toplu commit'le `integration/faz-01`'e alınmış ve regresyon PASS olmuş olmalı. Soak, commit SHA'sı kaydedilerek başlar.
2. **İmaj:** izole yığın için dashboard imajı commit'ten `scripts/build_image.py` ile oluşturulur. Release önerilir; geliştirme imajı da olabilir, ama kaydedilir. `ppe-dashboard:latest` kullanılmaz ve değiştirilmez.
3. **Disk:** C:'de en az **25 GB** boş alan olmalı ya da `REL_OUTPUT` harici diske yönlendirilmeli.
   - Hesap: 4 saatlik koşuda PG DB yaklaşık 1,6 MB/saat ve outbox yaklaşık 1,2 MB/saat büyüdü; samples birkaç MB.
   - Asıl tüketici Docker katmanları ve sistem; yeni imaj yaklaşık 0,5 GB.
   - `--min-disk-gb 5` koruması devrede.
   - Docker build cache (7,7 GB) ve kullanılmayan imajların temizliği **kullanıcı kararı**.
4. **Güç ve güncelleme (operatör, yönetici gerekebilir):**
   - AC adaptörüne bağlı.
   - `powercfg /change standby-timeout-ac 0` ve `hibernate-timeout-ac 0` (zaten 0); kapak kapatma eylemi "Hiçbir şey yapma" (AC).
   - Ekran kapanabilir.
   - Windows Update: Ayarlar → Windows Update → **güncellemeleri 1 hafta duraklat**; bekleyen yeniden başlatma olmamalı.
   - Test süresince başka bir yük (oyun, büyük derleme) çalıştırılmamalı.
5. **Diğer iş yükleri:** `career-agent-*` container'ları çalışıyor. Durdurmak **kullanıcı kararı**; durdurulmazlarsa CPU ve bellek bağlamı olarak kayda geçer.
6. **Saat:** Windows zaman eşitlemesi açık (zaman damgaları UTC).
7. **Kimlik:** `tools/reliability/output/rel.env` test yığınına ait (secret audit PASS). Üretim `.env`/token kullanılmaz; `common.compose()` zaten `assert_env_isolated` kontrolünü yapıyor. `REL_PROJECT=ppe-split` FAZ 02-A ile reddediliyor.

## 5. Uygulama

```powershell
cd C:\Users\PC\Desktop\ppe-split-integration
# 0) ön kontrol: disk, güç, Windows Update, izole yığın imajı
# İmaj SABİT: doğrulanmış release adayı (derlenmez; latest reddedilir). Verilmezse eski faz9-rel ölçülür.
$env:REL_DASHBOARD_IMAGE = "ppe-dashboard:0.10.7-rc.faz02a1"
# ppe-rel volume'u bu dosyanın parolalarıyla kurulu (integration ağacında rel.env yok; araç yenisini üretmeyi reddeder)
$env:REL_ENV_FILE = "C:\Users\PC\Desktop\ppe-split-main\tools\reliability\output\rel.env"
$env:REL_DETECTOR_PY = "C:\Users\PC\Desktop\ppe-split-main\detector\.venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
& $env:REL_DETECTOR_PY -B tools\reliability\soak.py --hours 24 --memprobe --interval 30 --min-disk-gb 5
```

- **İmaj kaydı:** soak, çalışan dashboard'un imaj ID'sini, build etiketlerini (revision, kind, tree-state, version) ve Git durumunu `provenance.json` ile `result.json`'a yazar. İmaj sabitlenmişse ve başka bir imaj çalışıyorsa soak **başlamaz**.

- **Örnekleme:** 30 saniyede bir, yani yaklaşık 2 880 örnek. Docker istatistikleri ve PG sayaçları her 3 örnekte bir alınır.
- **Arıza planı:** ağ kamerasında 40 dakikada bir 60 s kopma ve 45 s donma; 24 saatte yaklaşık 36 kopma ve 36 donma.
- **Elle ek senaryo (P-B varsa):** 6. saatte ve 18. saatte gerçek kameranın ağ kablosu ya da PoE'si 2 dakika çıkarılır. Zaman damgaları operatör kaydına yazılır.
- **İzleme:** operatör 6 saatte bir `samples.jsonl` son satırını ve `detector.log` sonunu kontrol eder. Koşu yalnızca §8'deki koşullarda durdurulur.

### 5.1 Duman koşusu (2026-11-19, 15 dakika) — PASS

`soak.py --hours 0.25 --fault-every 6 --memprobe`, `REL_DASHBOARD_IMAGE=ppe-dashboard:0.10.7-rc.faz02a1`, `REL_ENV_FILE=<ppe-split-main>\tools\reliability\output\rel.env`. Kanıt: `tools/reliability/output/20261119T144122Z_soak0.25h/` (Git dışı).

| Kontrol | Sonuç |
|---|---|
| İmaj | Çalışan = istenen `ea722e05…` (release, clean, revision `65fe6ba`); `provenance.json` + `result.json`. Araç ağacı commit'lenmemişti (`dirty=true`) |
| Teslim (A1–A3) | 266 gönderildi = 266 DB = 266 farklı; bekleyen/ölü/karantina 0; dedektör zarif durdu (2 s, rc 0) |
| Toparlanma (A9) | 2 kopma + 2 donma; arıza bitişinden ilk işleyen örneğe ≤ 60 s (30 s örnek çözünürlüğü); 14 yeniden bağlanma |
| Dosya kameraları | `proc_fps` p50 12,0–12,4; `lag_sec` p95 ≤ 0 |
| Dashboard | `/api/state` p95 33,9 ms; RSS ~100 MB; anon 74→77 MB (15 dakika, eğim için çok kısa) |
| Host | GPU maks 71 °C; disk 98 GB boş; bellek/disk koruması tetiklenmedi |
| **Dikkat: bellek** | Dedektör süreç ağacı RSS p50 **6,4 GB** (kamera başına 1,2–1,45 GB). 4 saatlik koşudaki 1,8 GB büyük olasılıkla bellek baskısında Windows'un working set kırpmasıydı (o koşuda swap 3,9 GB). Sistem belleği p95 14,2 / 15,3 GB. RSS = working set; özel bellek (private bytes) ölçülmüyor (B1'e ek). 24 saatte bellek koruması tetiklenebilir |

İlk deneme (`20261119T143956Z_soak0.25h`) yığın kurulurken durdu: integration ağacında yeni üretilmiş `rel.env`, `ppe-split-main`'in `rel.env`'iyle kurulmuş `ppe-rel_rel_pgdata` volume'una uymadı (`password authentication failed`). Düzeltme: `common.env()` volume varken yeni parola üretmeyi reddeder; koşular `REL_ENV_FILE` ile eşleşen dosyayı kullanır.

## 6. Ölçümler ve analiz

| Grup | Metrikler | Analiz |
|---|---|---|
| Dedektör belleği | `det_rss_mb` (toplam), kamera başına `proc_rss_mb`, `torch_reserved_mb`, `det_handles`, `det_threads` | İlk 60 dakika ısınma olarak dışlanır. Kalan süre dört 6 saatlik pencereye bölünür ve her pencerenin medyanı ile eğimi (MB/saat) hesaplanır. **Plato:** son iki pencere medyanı arasındaki fark ve son 12 saatin eğimi küçük ve işaret değiştiriyor. **Büyüme:** dört pencere medyanı monoton artıyor ve son 12 saatin eğimi pozitif |
| Dashboard belleği | `dash_mem_mb`, cgroup anon/file/kernel; memprobe: RSS anon, glibc in-use/free/arena, `py_blocks`, nesne sayıları | Aynı pencere yöntemi. Anon ve Python blokları büyürse uygulama sızıntısı; yalnızca file büyürse sayfa önbelleği (geri kazanılabilir) |
| DB ve outbox | `pg_db_bytes`, `pg_conns`, `ob_pending`, `ob_oldest_sec`, `ob_dead`, `ob_quar`, `ob_wal_bytes`, `outbox_dir_bytes` | DB boyutu olay sayısıyla doğrusal olmalı. Outbox bekleyen sayısı her kopmadan sonra 0'a dönmeli |
| Kamera performansı | `proc_fps`, `lag_sec`, frame/inference p95 ve p99, `reconnects`, bayraklar | 6 saatlik pencere medyanlarının karşılaştırılması; kopmadan sonra toparlanma süresi (bayrağın kalkması, FPS'in geri gelmesi) |
| Teslim | gönderici sayacı ↔ DB `count(*)` ve `count(DISTINCT event_id)`; bekleyen, ölü; run `end_reason` | Kayıp = gönderilen − DB; tekrar = DB − farklı |
| Host | `sys_cpu`, `sys_mem_used_mb`, swap (başlangıç), GPU sıcaklığı ve gücü, `disk_free_gb` | Swap ve bellek baskısı; termal kısma (saat hızı düşüşü ile sıcaklık) |
| Kesinti tespiti | Ardışık `samples.jsonl` zaman damgaları | Aralık > 3 × 30 s ise uyku, askıya alma ya da donma şüphesi; kesinti olarak raporlanır |

Kanıt klasörü `tools/reliability/output/<UTC>_soak24h/cams4_stride2/` altındadır: `samples.jsonl`, `samples.csv`, `detector.log`, `result.json` ve memprobe dosyaları. Klasör Git dışıdır (`.gitignore`).

## 7. Kabul ölçütleri (ÖNERİ; operatör ve İSG onayı gerekir)

Mevcut kanıta dayanan öneriler. "Kesin" olarak işaretlenenler veri bütünlüğüyle ilgilidir ve gevşetilmesi önerilmez. Diğerleri onay bekler.

| # | Ölçüt | Öneri | Dayanak |
|---|---|---|---|
| A1 | Olay kaybı | **0 (kesin)** | 4 saatte 4656/4656 |
| A2 | Tekrar eden olay | **0 (kesin)** | Aynı |
| A3 | Koşu sonunda bekleyen, ölü ve karantinadaki olay | **0 (kesin)** | Aynı |
| A4 | Dedektörün beklenmedik çıkışı, bellek ya da disk korumasının devreye girmesi | 0 | — |
| A5 | Dedektör RSS: ısınma sonrası 6 saatlik pencere medyanlarında monoton artış yok; son 12 saatin eğimi ≤ **+10 MB/saat** (öneri) | Onay gerekli | 4 saatlik koşu: eğim −55 MB/saat (ısınma etkisi), p50 1 792, p95 1 866 MB |
| A6 | Dashboard RSS/anon: son 12 saatin eğimi ≤ **+2 MB/saat** (öneri) | Onay gerekli | Düzeltme öncesi 90 dakikada anon +4,6 MB/saat; 4 saatlik koşuda `dash_mem` +3,9 MB/saat (düzeltme öncesi imaj) |
| A7 | Dosya kameralarında işleme FPS'i: her 6 saatlik pencerenin medyanı ≥ ilk pencerenin %90'ı | Onay gerekli | 4 saat: 12,0–12,4 (stride 2, kaynak 24–25 fps) |
| A8 | Gecikme: dosya kameralarında `lag_sec` p95 ≤ 1 s; ağ kamerasında kopma dışındaki p95 ≤ 1 s | Onay gerekli | 4 saat: ≤ 0 s; ağ kamerası maks 19,5 s (kopmalar) |
| A9 | Toparlanma: her planlı kopmadan sonra kamera ≤ **120 s** içinde yeniden işliyor; run ve bayrak durumları doğru | Onay gerekli (İSG: kabul edilebilir kör süre) | 40 yeniden bağlanma, hepsi toparlandı (4 saat) |
| A10 | Dashboard `/api/state` p95 ≤ 100 ms; HTTP 5xx = 0 | Onay gerekli | 4 saat: p95 40 ms, 5xx 0 |
| A11 | GPU sıcaklığı ≤ 85 °C (5 dakikadan uzun aşım yok) | Onay gerekli | 4 saat: maks 74 °C |
| A12 | Kesinti (örnek aralığı > 90 s) = 0 | Kesin (geçerlilik koşulu) | — |

A12 sağlanmazsa koşu "geçersiz" sayılır, FAIL sayılmaz ve tekrarlanır.

## 8. İptal, güvenli durdurma ve kurtarma

### 8.1 Otomatik durdurma (araçta var)

- **Bellek koruması:** kullanılabilir RAM `MEM_GUARD_BYTES` altında ya da swap > %90, 10 ardışık kontrol (yaklaşık 50 s) boyunca.
- **Disk koruması (FAZ 02-A):** boş alan `--min-disk-gb` altında, 10 ardışık kontrol boyunca.
- **Dedektör süreci çıkarsa** ölçüm biter.

Her durumda dedektör zarif biçimde durdurulur (`stop_detector`), outbox sonucu ve teslim karşılaştırması yazılır.

### 8.2 Elle durdurma koşulları (operatör)

- GPU sıcaklığı 5 dakikadan uzun süre 90 °C'nin üzerinde.
- Bekleyen olay sayısı 30 dakika boyunca artıyor (`ob_pending` ya da `ob_oldest_sec`).
- `dead_letter` > 0.
- Makinede acil başka bir iş.
- Windows'un yeniden başlatma bildirimi.

**Durdurma:** `soak.py` penceresinde **bir kez Ctrl+C**. Ölçüm döngüsü kesmeyi yakalar (`stop_reason=operator_interrupt`) ve sonraki Ctrl+C'leri yok sayar. Ardından sampler durdurulur, dedektör zarifçe kapatılır, teslim karşılaştırması yapılır ve `result.json` yazılır. (FAZ 02-B'de düzeltildi: önceden kesme `finally`'den sonra yeniden yükseliyor ve `result.json` yazılmıyordu; bkz. `PHASE_02B_SOAK_REPORT.md` S2.) Container'lar `docker compose -p ppe-rel -f tools/reliability/docker-compose.reliability.yml stop` ile durdurulur. Üretim projesine komut verilmez.

### 8.3 Ani kapanma sonrası kurtarma

1. `samples.jsonl` (her 10 örnekte bir fsync), `detector.log`, `outbox/*.sqlite3` ve `ppe-rel` DB volume'u diskte kalır.
2. Makine açılınca `ppe-rel` yığını başlatılır (`docker compose -p ppe-rel ... start`). Teslim karşılaştırması elle yapılır:
   - DB'de `soak.py`'deki SQL (kamera kimlikleri `relc1-3`, `relnet`).
   - Dedektör logunda `gönderici kapandı` satırları zarif kapanmada vardır; ani kapanmada yoktur. Bu durumda gönderilen sayısı outbox `delivered` ve `pending` satırlarından alınır.
3. Koşu A12'ye göre "geçersiz" sayılır; ölçülen süre ve kesinti nedeni raporlanır.

## 9. Bilinen boşluklar ve öneriler (soak'tan önce ya da paralel)

| ID | Boşluk | Etki | Öneri | Bu fazda |
|---|---|---|---|---|
| B1 | Dedektörde Python heap, native ve CUDA ayrımı yok | Dedektör RSS'i büyürse kök neden ayrılamaz | `detector.py`'ye dokunmadan, `tools/reliability` altında isteğe bağlı bir prob (tracemalloc anlık görüntüsü + `torch.cuda.memory_allocated`, süreç içi thread) **ya da** büyüme görülürse ikinci, kısa bir hedefli koşu | Yapılmadı: üretim karar koduna dokunma kuralı. Ayrı karar |
| B2 | Gerçek RTSP kamera yok | Benzetici, gerçek akış hatalarını temsil etmez | P-B profili (sahadan kamera erişimi) | Kullanıcıya bağlı |
| B3 | `result.json` yalnızca koşu sonunda yazılıyor | Ani kapanmada otomatik özet yok | §8.3 elle kurtarma; ileride `soak.py --resume-analysis` | Belgelendi |
| B4 | Windows Update ve güç ayarları otomatik doğrulanmıyor | Gece yeniden başlatması koşuyu geçersiz kılar | §4 ön kontrol listesi (operatör) | Belgelendi |
| B5 | Bellek dar: 15,3 GB, 4 saatlik koşunun başında swap 3,9 GB | Swap'e taşma gecikme ve FPS ölçümünü bozar | Diğer iş yükleri kapatılır; başlangıçta kullanılabilir bellek ≥ 4 GB olmalı (öneri) | Belgelendi |
| B6 | Disk %94 dolu | Koşu ya da üretim yazamaz | `--min-disk-gb` koruması + temizlik kararı | **Koruma eklendi** |

## 10. Raporlama

Koşu sonunda `docs/productization/PHASE_02B_SOAK_REPORT.md` yazılır. İçeriği:
- Commit SHA, imaj ID'si ve kind.
- Başlangıç koşulları: bellek, swap, disk, güç ve güncelleme durumu, çalışan diğer yükler.
- Profil ve arıza planı.
- §7'deki her ölçütün PASS, FAIL ya da ONAY BEKLİYOR sonucu ve dayandığı sayı.
- 6 saatlik pencere tabloları.
- Kesintiler.
- Sapmalar.
- Eski ve yeni bulguların karşılaştırması (4 saatlik koşuyla).

Ham veriler Git'e eklenmez; özet tablolar rapora alınır.
