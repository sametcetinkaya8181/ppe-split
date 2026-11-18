# FAZ 10-A.1 raporu — Bellek kararlılığı, bağımlılık güvenliği, dosya izinleri

Tarih: 2026-11-02 (UTC). Başlangıç: `d67854d` (FAZ 10-A). Saha pilotu başlatılmadı; ana DB kayıtları, snapshot'lar,
`best.pt`, `ppe_core.py`, eşikler ve migration'lar değiştirilmedi.

| Karar | Sonuç |
|---|---|
| Teknik pilot hazırlığı | **INCOMPLETE** — 24 saatlik soak koşulmadı; kalan küçük anon bellek eğilimi kanıtla kapatılamadı; gerçek RTSP hâlâ BLOCKED |
| Gerçek saha pilotu | **NO-GO** (FAZ 10-A gerekçeleri değişmedi) |

## 1. Başlangıç durumu (salt okunur)

HEAD `d67854d`, ağaç temiz (izlenmeyen tek dosya kullanıcının plan belgesi; commit'e eklenmedi). Ana yığın sağlıklı:
Alembic `0006`; 74 run (finished 64 / interrupted 6 / running 4), 47 incident; dashboard 11 saatlik boşta çalışmadan
sonra 72 MiB, bunun ~70 MB'ı anon (sayfa önbelleği ~0). Her iki dashboard imajındaki kod depoyla birebir aynı.

## 2. Dashboard bellek artışı — ölçüm ve kök neden

Yöntem: ürün koduna dokunmadan izole `ppe-rel` yığınında dashboard `memboot.py` probuyla (aynı uvicorn ayarları);
yük `memtest.py`: 4 sentetik kamera × 12 fps (gerçek outbox/DurableSender, gerçek video kareleri 640 px), olaylar +
snapshot + kişi raporları, operatör taklidi (`/ws`, `/api/state` 0.7 sn, `/api/ops/metrics` 30 sn).

**Katmanlar (taban çizgisi, eski kod, 90 dk, 02:31:30Z–04:01:30Z, 12 590 olay, kayıp 0 / çift 0, traceback 0):**

| Katman | Değişim | Yorum |
|---|---|---|
| `docker stats` | 83.6 → 92.7 MB (+7.8 MB/sa) | = cgroup − `inactive_file`; aşağıdakilerin toplamı |
| sayfa önbelleği (`inactive_file`) | +8.3 MB/sa | snapshot JPEG'leri; `docker stats` saymaz, geri kazanılabilir |
| kernel slab | +3.8 MB/sa | yeni snapshot dosyalarının dentry/inode önbelleği; geri kazanılabilir, `docker stats`'a dahil |
| anon (süreç) | 30.–66. dk +4.8 MB/sa | Python blokları +22.8 bin/sa; artan tipler `quoted_name`, `_ColumnMetrics`, `DDLEventsDispatch` |

**Kök neden (kanıtlı):** `db/health.check_ready()` her çağrıda `MigrationContext.configure()` ile yeni bir
`alembic_version` Table/MetaData kuruyordu; SQLAlchemy derlenmiş ifade önbelleğinin anahtarı bu nesneye bağlı
olduğundan **her hazırlık kontrolü yeni bir önbellek girdisi** ekleyip nesneleri canlı tutuyordu (ölçüm: kontrol başına
~16 KB; LRU 500 girdide durur → ≳8 MB). `/health/ready` ve `/api/ops/metrics` (5 sn önbellekli) bu yolu çağırır;
FAZ 10-A soak örnekleyicisi 30 sn'de bir çağırdığından 4 saatte ~480 girdi oluştu — "4 saatte plato yok" gözlemiyle
uyumlu. İkincil: `get_session()` her istekte yeni `sessionmaker` (yeni Session alt sınıfı) üretiyordu (yalnız gen2
GC'de toplanır; sızıntı değil, gereksiz çöp).

**Yanlışlanan hipotez (kayıt için):** Çok satırlı `insert().values(rows)` ifadelerinin her satır sayısı için önbellek
girdisi ürettiği varsayıldı; ölçüm bu PostgreSQL upsert'lerinin **hiç önbelleğe alınmadığını** gösterdi. Bunun yerine
her çağrıda satır başına ~5 KB'lık ifade yeniden derleniyordu (geçici tepe; aşağıda).

**Düzeltmeler:**

| Dosya | Değişiklik | Kanıt |
|---|---|---|
| `dashboard/db/health.py` | Sürüm, sabit metinli iki sorguyla okunur (`to_regclass` + `SELECT version_num`); anlam aynı (tablo yoksa revision yok) | 51 kontrolde önbellek: eski +51, yeni 0; kontrol başına tutulan bellek 16 KB → 0 |
| `dashboard/db/repository.py` | Olay/incident/kişi raporu yazımları executemany (insertmanyvalues); sıra, ON CONFLICT, RETURNING aynı | 5000 kişilik rapor: tepe 51.2 → 14.7 MB, süre 5.9 → 0.84 sn; 200 olay 519 → 198 ms |
| `dashboard/db/session.py` | `sessionmaker` motor başına bir kez | Session alt sınıfı sayısı 13–25 → 1 |
| `dashboard/tests/test_statement_cache.py` (yeni, 4 test) | hazırlık kontrolü önbelleği büyütmez; 5000 satır rapor < 30 MB; 2500 satır (sayfa sınırı üstü) + birebir tekrar + 409; yeni/mevcut olay karışımı | eski kodla 2 test FAIL (önbellek 61−10, tepe 48.6 MB), yeni kodla PASS |

**A/B sonucu (aynı yük, düzeltmeli imaj `4c972e0a`, 04:15Z başladı):** test 66. dakikada makinenin kapanmasıyla
kesildi (05:21:20Z; 9 458 olay; ölçüm verisi kurtarıldı). 30.–66. dk:

| | anon eğimi | glibc kullanımda | Python blok | derlenmiş önbellek |
|---|---|---|---|---|
| Taban | 4.78 MB/sa | +0.56 MB/sa | +22 765/sa | 33 → 143 |
| Düzeltme | **2.89 MB/sa** | **+0.09 MB/sa** | **+1 067/sa** | **12 (sabit)** |

Python düzeyindeki büyüme giderildi. **Açık:** anon'da ~2.9 MB/sa kalıntı var; Python nesnelerinden ve glibc
kullanımdaki bellekten gelmiyor (adaylar: ayırıcı/pymalloc parçalanması). 66 dakikalık veriyle platoya ulaşıp
ulaşmadığı belirlenemez → **24 saatlik soak gerekli**. Kernel slab artışı snapshot sayısıyla orantılıdır ve
geri kazanılabilir (ürün değişikliği gerekmez); izleme artık anon/file/kernel'i ayrı kaydeder.

**24 saatlik soak: INCOMPLETE — koşulmadı.** Kullanıcı onayı alındı ancak test başlatılmadan önce makine yeniden
başladı (17:13:59Z). Prosedür ve PASS ölçütleri: `RELIABILITY_TEST_PLAN.md` §9; komut
`python tools/reliability/soak.py --hours 24 --memprobe`.

## 3. Dedektör bağımlılık güvenliği

`pip-audit` (2026-11-02, canlı OSV/PyPI) FAZ 10-A bulgularını meta veride doğruladı: Pillow 12.3.0 (41 kayıt; 13.0.0),
filelock 3.32.3 (12; 4.2.1), setuptools 78.1.0 (3; 84.1.0). **Ancak çalışan kod farklıydı:** venv kurulumunda
(2026-10-27 18:31–18:33) 7 paket yükseltilmiş, eski `dist-info` dizinleri silinmemişti. RECORD SHA-256
karşılaştırması: diskteki dosyalar **%100 yeni sürüm** — Pillow 13.0.0 (120/120), filelock 4.2.4 (28/28), setuptools
84.2.1 (348/348), numpy 2.5.4, fsspec 2026.9.0, MarkupSafe 3.0.4, networkx 3.7. Gerçek kalan risk: setuptools 78.1.0'dan
kalan **331 öksüz dosya** (`pkg_resources/`, CVE-2025-47273'lü `setuptools/package_index.py`, `sandbox.py`); çalışma
zamanında içe aktarılmıyordu (erişilebilirlik yok).

Onarım (`scripts/venv_dedupe.py`, ağ yok, silme yok — karantinaya taşıma): önce venv'in tam kopyasında (scratchpad):
uygulama → geri alma → yeniden uygulama doğrulandı; `pip check` temiz, pip-audit temiz, 245/245, GPU eşdeğerliği
birebir. Kullanıcı onayıyla üretim venv'ine uygulandı (04:07Z; 380 öğe →
`C:\Users\PC\ppe-venv-quarantine-20261102T040700Z`; geri alma `--rollback <dizin>`; önceki freeze
`C:\Users\PC\ppe-venv-freeze-before-20261102T040700Z.txt`).

| Üretim venv'i, onarım sonrası | Sonuç |
|---|---|
| `pip check` / çift dist-info / `pkg_resources`, `package_index`, `sandbox` | temiz / 0 / yok |
| pip-audit (torch/torchvision +cu126 PyPI dışı, denetlenemez) | **No known vulnerabilities found** |
| Dedektör testleri | **245/245 PASS** |
| GPU eşdeğerliği (`gpu_parity.py`, RTX 3050, best.pt `6886ad2f…`, ByteTrack, PPEMonitor, 186 kare) | onarım öncesiyle **186/186 birebir** (sınıf, track ID, IoU 1.0, güven farkı 0) |
| Gerçek dedektör uçtan uca (2 kamera, 120 sn, düzeltmeli dashboard) | 22/22 olay, eksik 0, fazla 0; 13 `finished/eof` + 2 `interrupted`; 66 kişi satırı; traceback 0 |
| Ultralytics çevrimdışı | varsayılanda dış bağlantı denemesi 0 |

## 4. Windows dosya izinleri ve disk şifrelemesi

Kök neden: `C:\Users\PC\Desktop` üzerinde **açık** `BUILTIN\Users:(OI)(CI)(M)` girdisi; proje bunu miras alıyordu.
Tek etkin yerel hesap `PC` (UAC bölünmüş yönetici, docker-users). Kullanıcı onayıyla `scripts/windows_acl.ps1 -Apply`:
ACL yedeği `C:\Users\PC\ppe-acl-backup\acl-ppe-split-main-20261102T025804Z.txt` (38 836 öğe), proje kökünde miras
kesildi, yalnız PC + SYSTEM + Administrators. Masaüstünün kendisine dokunulmadı. Geri alma:
`.\scripts\windows_acl.ps1 -Rollback -BackupFile <dosya>`.

Doğrulama: 38 836 öğede geniş grup girdisi 0, hata 0; ana dashboard `/healthz` + `/health/ready` 200; container
`/data/snapshots` yazımı OK; `backup.py` + `restore_check.py` PASS (yeni yedek kısıtlı ACL'yi miras aldı); outbox/ACL
testleri 40/40. Not: `backups/env-20261028T172010Z` bir `.env` yedeği içerir (artık kısıtlı; silinmedi).

**BitLocker:** yönetici gerektirmeyen salt okunur göstergeler — Windows 11 **Home**, `PreventDeviceEncryption=1`,
Shell `BitLockerProtection=0` → disk büyük olasılıkla **şifresiz**. Kesin teyit: yönetici `manage-bde -status C:`.
Yedekler ve outbox hâlâ şifresiz (kişisel veri).

## 5. Testler

| Kalem | Sonuç |
|---|---|
| Dashboard (gerçek PostgreSQL 17) | **338/338 PASS** (334 + 4) |
| Dedektör | **245/245 PASS** |
| Tarayıcı (Edge + CDP) | **30/30 PASS** |
| LAN TLS izole | **21/21 PASS** |
| Bellek A/B | taban 90 dk tam; düzeltme 66 dk (kesildi) — Python düzeyi düzeldi, anon kalıntısı açık |
| 24 saatlik soak | **INCOMPLETE** (koşulmadı) |
| Gerçek RTSP | BLOCKED |

## 6. Değişen dosyalar

`dashboard/db/{health,repository,session}.py`, `dashboard/tests/test_statement_cache.py` (yeni);
`scripts/venv_dedupe.py`, `scripts/windows_acl.ps1` (yeni); `tools/reliability/memboot.py`, `memtest.py`,
`gpu_parity.py` (yeni); `eventgen.py` (`--frame-video`, dedektörle aynı rapor satırları), `sampler.py` (cgroup
anon/file/kernel), `soak.py` (`--memprobe`), `common.py` / `telemetry_check.py` (`REL_DETECTOR_PY`);
`docs/RELIABILITY_TEST_PLAN.md` §9; bu rapor. Ana dashboard container'ı bu fazda **güncellenmedi** (düzeltmeler
yalnız izole imajda); güncelleme FAZ 10-A §3 prosedürüyle (yedek + geri dönüş imajı) ayrıca yapılmalı.

## 7. Kalan riskler / sonraki adımlar

1. 24 saatlik soak (`--memprobe`) — anon kalıntısının platoya ulaştığını göstermeli; aksi hâlde `MALLOC_ARENA_MAX=2`
   ile A/B (`memtest.py --env`).
2. Ana dashboard'un düzeltmeli imaja kontrollü güncellenmesi.
3. BitLocker teyidi (yönetici) ve yedek şifreleme kararı; masaüstündeki geniş ACL diğer projeleri etkilemeye devam eder.
4. FAZ 10-A'dan devreden: gerçek RTSP, KVKK, lisans, etiketli saha verisi, İSG eşikleri.
