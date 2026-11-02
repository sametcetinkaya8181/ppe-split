# FAZ 10-A.5B raporu — Kontrollü ML ve karar deneyleri (Windows CUDA)

Tarih: 2026-11-02 (UTC). Başlangıç: `b8c571d` (FAZ 10-A.5A). Ortam: Windows 11, PowerShell, NVIDIA RTX 3050 Laptop
(4 GB), Python 3.12.6, PyTorch 2.14.1+cu126, Ultralytics 8.4.183. Model `best.pt` SHA-256 `6886ad2f…ba43` (faz
sonunda yeniden doğrulandı).

**Bu faz üretime hiçbir değişiklik ALMAZ.** `best.pt`, `ppe_core.py` (PPEMonitor), `protocol.py` (IncidentTracker),
`detector.py`, eşikler, tracker yapılandırması, dashboard, ana PostgreSQL/snapshot/outbox değişmedi. Tüm adaylar ayrı,
isimli, çevrimdışı varyantlardır. 24 saatlik soak **başlatılmadı**. İki alarm **UNREVIEWED**. Ground-truth yok:
**accuracy, precision, recall, yanlış alarm oranı hesaplanmadı.** V1–V3 hata analizi/regresyon verisidir; üzerinde
yapılan hiçbir gözlem bağımsız doğrulama değildir. Sentetik senaryolar yalnız yazılım mantığını sınar.

## Özet — hata başına en iyi aday

| Hata | En iyi aday | V1–V3 etkisi | Bilinen yan etki / FN riski | Durum | Üretim |
|---|---|---|---|---|---|
| E-01 örtülü koyu baret | **H5 kanıt derecelendirme** (alarm bastırmaz; `missing_detection / conflicting / verified_absence` etiketi) | alarm sayısı aynı; V2 alarmı `missing_detection` | FN riski yok (taban ile aynı olaylar); yanlış alarmı **azaltmaz** | yalnız triyaj aracı | **NO-GO** (karar değişikliği olarak) — gösterim etiketi olarak ayrı değerlendirilebilir |
| E-01 | (azaltan adaylar: H1, H2, H6) | hiçbiri E-01 başlangıcını güvenle kaldırmadı (§3) | H1 sentetik baretsiz kaçırma; H2 neredeyse tüm gerçek ihlalleri kaçırır; H6 fps'e bağlı yapısal kaçırma | **REJECTED / NOT VALIDATED** | **NO-GO** |
| E-02 makine → Person | **P1 iz düzeyi kayan medyan güven** (τ yalnız duyarlılık: 0.45/0.50/0.55) | V3 alarmı kalkar (üç τ'da) | Uzak çalışan vekilinde (V1 ×0.33) kapıdan geçen kişi-karenin %35–54'ü kaybolur; gerçek uzak çalışan medyanları (0.30–0.53) makineyle (0.36–0.42) **örtüşüyor** | **PROMISING / NOT VALIDATED** | **NO-GO** |
| E-02 | P5 ikinci aşama (aynı model, kırpıntı) | **etkisiz**: makine kırpıntıda 0.71–0.76 Person | — | **REJECTED** | NO-GO |
| E-03 kenar kapsamı | **A3 eşyaya özgü kenar kapısı** (baret: yalnız alt kenar muaf; yelek: üretim kapısı) | V2 baret-değerlendirilebilir kişi-kare 48 → 485; V1 224 → 291 | E-01 çözülmeden V2 alarmı 4.7 → 2.1 sn'ye çekilir, +1 incident (22.4 sn), kontrol aralığında +40 IHLAL karesi | **PROMISING / NOT VALIDATED** (E-01'e bağımlı) | **NO-GO** |
| E-04 kimlik kararlılığı | **T6 yalnız-Person takip + `match_thresh` 0.9** | kişi kimliği V1 6→3, V2 3→2, V3 4→3; başka sınıfa geçen kişi kimliği 2→0; V2 incident tek kalır | Kimlik **birleşmesi** riski ölçülemedi (GT yok; sentetik kesişmeler ayırt etmedi); eşya kaynağı ham tespit olur | **PROMISING / NOT VALIDATED** | **NO-GO** |
| E-04 mükerrer incident | A4 kimlik devri (karar katmanı) | V1–V3 incident değişmez; T3 ile V2'de 2→1 | Sentetik: aynı yerde beliren **farklı kişiyi birleştirir** (S13) | **PROMISING / NOT VALIDATED** | **NO-GO** |

**Yalnız deneysel olarak başarılı olanlar:** E-02 P1 (V3 alarmını kaldırır), E-03 A3 (kapsamı artırır), E-04 T6
(kimlik sayısını ve sınıf kaçırmayı azaltır), E-04 A4 (S11 mükerrer incident'ı tekler), E-01 H5 (etiketler).
**Hiçbiri üretim için yeterli kanıta sahip değil:** bağımsız, izinli, gerçek ihlal içeren GT'li veri olmadan hiçbir
adayın yanlış negatif riskinin artmadığı gösterilemez. Bu nedenle **genel karar: NO-GO** (§11).

## 1. Ön güvenlik ve ortam (A)

| Kontrol | Sonuç |
|---|---|
| Git | dal `main`, HEAD `b8c571d`; `99217bf` (10-A.4), `e1f1e48` (10-A.1) geçmişte. İzlenmeyen `docs/PPE_Saha_Hazirlik_Claude_Opus_Plani.md` bu faza ait değil — **commit'e eklenmedi** |
| 10-A.4 baseline manifesti korunması | `20261102T174149Z_baseline_cuda/` + `20261102T181114Z_failure_analysis/` 124 dosyanın SHA-256 listesi faz başında ve sonunda alındı: **birebir aynı** |
| Mevcut testler (başlangıç) | 275/275 PASS |
| Gizli bilgi denetimi (başlangıç / son) | `secret_audit.py` **PASS / PASS** (son: 556 kanıt metin dosyası, 10 test container'ı, 155 izlenen dosya + tüm Git geçmişi; üretim değeri yok) |
| Ana sistem (son) | ana DB sayımları `104\|47\|74\|255\|110` (10-A.5A ile aynı), ana snapshot 51, üretim outbox'ı yok, `ppe-split`/`ppe-rel` container durumları aynı; `llminsight`, `docker-flowise` **dokunulmadı**. Bu fazda hiçbir container başlatılmadı/durdurulmadı |

### 1.1 Token izolasyonu (10-A.5A kalan riskinin kapatılması)

Doğrulanan risk (yalnız boole yazdırıldı): `production_settings()` sonrası değerlendirme sürecinde
`INGEST_TOKEN` ortamda **vardı** ve üretim değerine **eşitti**; `detector.INGEST_TOKEN` modül kopyası da aynıydı
(`import detector` → `_load_env`).

Kapsamı sınırlı düzeltme (**üretim `detector.py` değişmedi**), yeni `detector/evaluation/isolation.py`:

1. `scrub_process_env()` — `cli.production_settings()` içinde `import detector`'dan hemen sonra
   `INGEST_TOKEN, INGEST_TOKEN_NEXT, *_PASSWORD, ADMIN_PASSWORD, DASHBOARD_URL` süreç ortamından silinir,
   `detector.INGEST_TOKEN` boşaltılır. Değerlendirme süreci token'a hiçbir zaman ihtiyaç duymaz. Model/cihaz/IMGSZ
   gibi gizli olmayan üretim ayarları korunur.
2. `assert_env_isolated()` — test alt süreçleri başlatılmadan önce ortamdaki **her** değer üretim gizli değerleriyle
   (kök `.env` + `detector/.env`) karşılaştırılır (gömülü değer dahil); eşleşmede süreç **başlatılmaz**, hata
   iletisi yalnız anahtar adı içerir. Bağlandığı yerler: `common.compose()`, `common.detector_env()`,
   `memtest.start_cams()`.
3. `experiments_5b.py cache` ayrıca gizli anahtar kalırsa `BLOCKED` ile durur; önbellek manifesti
   `eval_env_secret_keys_present: []` kaydeder.

| Doğrulama | Sonuç |
|---|---|
| Gerçek makinede `production_settings()` sonrası | ortamda INGEST_TOKEN **yok**, modül kopyası üretim değerine **eşit değil**, DASHBOARD_URL **yok** |
| Koruma, üretim değeri başka bir anahtarla ortama konulduğunda | `IsolationError`, ileti değeri **içermiyor** |
| Temiz test ortamı / `compose config` (ppe-eval, salt okunur) | geçti / rc 0 |
| Birim testleri `test_evaluation_isolation.py` | 5/5 (gerçek `.env` okunmaz; geçici dosyalar) |

Kalan sınır: dedektör alt süreci (`detector_launcher.py`) `import detector` ile `detector/.env`'i kendisi okur;
`setdefault` kullandığından açıkça verilen test `INGEST_TOKEN`/`DASHBOARD_URL` kazanır, ancak `detector/.env`'e ileride
eklenecek **başka** bir gizli anahtar alt süreçte yüklenir. Kalıcı çözüm üretim kodunda (`DETECTOR_ENV_FILE`'ın
`_load_env` tarafından da kullanılması) — bu fazda **uygulanmadı**.

**Rotasyon:** gerekli görülmedi — token geçmişte yalnız 127.0.0.1'e bağlı yerel test container'ının ortamında
bulundu (10-A.5A), Git/kanıt/log taraması temiz. İhtiyaten döndürülecekse güvenli plan (`docs/SECURITY.md`
rotasyon prosedürü): (1) kök `.env`'ye `INGEST_TOKEN_NEXT=<yeni>` ekle ve yalnız dashboard'u yeniden oluştur
(`docker compose up -d dashboard` — ana dashboard yapılandırması değişikliğidir, bakım penceresi gerekir),
(2) `detector/.env`'de token'ı yenisiyle değiştir (dedektör dosyayı gönderimde yeniden okur), (3) reddedilen ingest
sayacı sıfırken yeni token'ı `INGEST_TOKEN`'a taşı, NEXT'i kaldır, dashboard'u yeniden oluştur, (4) `secret_audit.py`
ve `ppe-eval`/`ppe-rel` env dosyalarının hâlâ farklı olduğunu doğrula. Karar ve uygulama kullanıcıya aittir; bu fazda
**yapılmadı**.

## 2. Yöntem

**Tespit önbelleği (GPU, bir kez, ≈3 dk):** `experiments_5b.py cache` → `output/20261102T185341Z_experiments_5b_cache/`.
Kare okuma `pipeline.run_video` ile aynı (grab×2 + retrieve, `t = idx·stride/fps`); `model.predict(conf=0.05)`
tüm sınıflar, tam hassasiyet, NMS sırası korunur. Person ≥ 0.25 kutuları için **ikinci aşama**: kutu %25 bağlamla
kırpılır, ayrı model örneği (aynı `best.pt`) yeniden tahmin eder. **Sentetik bozulmalar** (her video): ×0.50 ve
×0.33 küçültme (gri tuval; uzak çalışan vekili), ×0.35 karartma (düşük ışık vekili).

**Takip eşdeğerliği:** `track_offline()` Ultralytics `BYTETracker`'ı önbellekten yeniden oynatır (üretim girdisi:
conf ≥ 0.25, tüm sınıflar; Ultralytics'in `Results.update` kırpması ve "tracker boşsa kimliksiz ham tespit" davranışı
dahil). 10-A.5A tanı iziyle: **V1 186/186, V2 288/288, V3 157/157 kare IDENTICAL** (kimlik, sınıf, kutu ±0.15 px,
güven ±1e-3). Doğrulama sırasında bulunan ve düzeltilen iki hata (her ikisi de regresyon testli): karşılaştırıcıda
çift yuvarlama (0.785515 → 0.7855 → 0.785/0.786) ve eksik görüntü kırpması (y2 1083.6 → 1080).

**Karar eşdeğerliği:** `ExperimentalMonitor` varsayılan yapılandırmayla `PPEMonitor` ile birebir — V1–V3 üretim
olayları ve 12 rastgele çok kişili dizide olay/kapanış/durum sayıları aynı. Eşya başına ayrı gözlem kuyruğu, tek
değişkenli politika anahtarları; `ppe_core` sabitleri değiştirilmez (testli).

**Ölçümler (GT'siz):** incident listesi (kişi, başlangıç, bitiş, kapanış nedeni: `end/stale/run_end`), durum
kişi-kareleri (IHLAL / BELIRSIZ / UYGUN / ANALIZ), baret ve yelek için değerlendirilebilir kişi-kare, **kontrol
aralıkları** (taban alarm aralığı ±1 sn dışındaki tüm kişi-kareler: V1 293, V2 286, V3 218), takip göstergeleri
(kişi kimliği, başka sınıfla sürmüş kişi kimliği, parçalanma adayı, eşzamanlı çift), bozulmalarda kişi-kare kapsamı
korunumu, 15 sentetik karar senaryosu ve 7 sentetik takip senaryosu.

```powershell
detector\.venv\Scripts\python.exe tools\reliability\secret_audit.py
detector\.venv\Scripts\python.exe tools\reliability\experiments_5b.py cache
detector\.venv\Scripts\python.exe tools\reliability\experiments_5b.py run tools\reliability\output\<UTC>_experiments_5b_cache
cd detector; .venv\Scripts\python.exe -m pytest tests\test_evaluation_experiments.py tests\test_evaluation_isolation.py -q
```

Sonuç kökü: `tools/reliability/output/20261102T190020Z_experiments_5b/` (`results.json` tüm varyant
yapılandırmaları + çıktılar, `RESULTS.md`; Git dışı). Önbellek/sonuç dosyalarında görüntü yoktur.

## 3. E-01 — Eksik baret tespiti ve ihlal kararı

**Kişi-kare baret kanıtı** (`helmet_evidence`): `present` (eşleşti) / `weak` (baş bölgesinde eşleşmemiş ya da eşik
altı ≥ 0.10 baret adayı; `no_helmet` ile çelişki de) / `absent` (`no_helmet` ≥ 0.25, baret adayı yok) / `none` (hiç
kanıt yok). Değerlendirilemez baş: kapı nedenleri + (H1b) baş bölgesinde eldiven (el/alet) veya önde başka kişi.
Ön gözlem (10-A.5A izi): V2 track 3'ün 4.7 sn alarmından önceki kapıdan geçen karelerin neredeyse tamamı `none`
(yalnız 1 zayıf aday) — ayırt edici bir model sinyali yok; üç videoda `no_helmet` hiç yok; baş bölgesinde başka
sınıf yalnız eldiven (45 karenin 10'u).

| Varyant (tek değişken) | V2 incident (taban 3@4.7–14.8 end) | V3 (taban 160@9.4) | V2 IHLAL/BELIRSIZ kişi-kare | Sentetik kaçırılan gerçek ihlal | Not |
|---|---|---|---|---|---|
| H1 çelişkiye duyarlı (zayıf kanıt yokluk sayılmaz; BELIRSIZ durumu) | **2 incident**: 4.7–5.1, 10.3–14.0 | aynı | 48 / 43 (taban 100 / 0) | **S02** (baretsiz + zayıf aday titreşimi) | başlangıcı kaldırmaz, incident'ı böler |
| H1, taban 0.05 | aynı | 10.5'e kayar | 48 / 43 | S02 | eşik duyarlılığı |
| H1b baş örtülme kapısı | **4.7–24.0 run_end** (uzar) | aynı | 212 / 0; kontrolde +99 IHLAL | — | baret görünen kareler de dışlandı → çıkış gecikti; **zararlı** |
| H2 doğrulanmış yokluk (yalnız `no_helmet` kanıtıyla) | yok | yok | 0 / 91 | **8/9 must_alarm** (S01, S02, S05, S06, S08, S10, S14, S15; yalnız `no_helmet`'li S03 alarm verir) | model `no_helmet` üretmediği için fiilen "her şeyi uygun say" → **REJECTED** |
| H5 kanıt derecelendirme | aynı (etiket `missing_detection`) | aynı (`missing_detection`) | 100 / 0 | yok (taban ile aynı) | yanlış alarmı azaltmaz; inceleme önceliği için |
| H6 baret için ≥16 gözlem | **5.4–24.0 run_end** (uzar) | yok | 203 / 0; kontrolde +99 IHLAL | yok | V2'yi kötüleştirir |
| H6 ≥24 gözlem | yok | yok | 0 / 0 | yok (sentetik 12 fps) | **sayı tabanlı → fps'e bağlı**: 4 sn pencerede ≥24 gözlem 12.5 fps işlemede ≥2 sn sürekli değerlendirme ister; ≤6 fps işlemede **hiç alarm veremez** → yapısal FN; REJECTED |

**Birleşikler:** C1 (H1+H1b) V2'de 2 incident (4.7–5.1, 12.1–14.0); S02 kaçırılır.

**Bozulma gözlemi (genelleme):** küçültülmüş/karartılmış V1–V3'te tabanın kendisi yeni "Baretsiz" alarmlar üretti
(V2 ×0.50: 3, V2 ×0.33: 2, V2 karanlık: 3, V3 ×0.50 ve ×0.33: 2'şer, V1 ×0.33: 1) — AI gözlemine göre bu çalışanlar baretli; küçük/karanlık
başta baret kanıtı kayboluyor (E-01 mekanizmasının genellemesi). H1 bunların bir kısmını kaldırdı (V2 ×0.33 2→0,
V2 ×0.50 3→1, V3 ×0.50 2→0) — ancak bu alarmların yanlış olduğu **insan onaylı değil** ve H1 sentetik S02'yi kaçırıyor.

**Sonuç:** Karar katmanında, mevcut model çıktılarıyla E-01 başlangıcını güvenle kaldıran bir aday **yok**. Kanıt
eksikliği ile ekipman yokluğunu ayıracak bilgi modelde yok (`no_helmet` üç videoda 0; baş görünürlüğü sinyali yok).
Gerçek çözüm model tarafında (C1: koyu/örtülü/arkadan baret + `no_helmet` sınıf kalitesi, izinli veriyle). H5 alarmı
gizlemeden "kanıt türü" etiketi ekler; gösterim/triyaj olarak düşünülebilir, ancak etiketin kendisi de GT ile
kalibre edilmemiştir.

## 4. E-02 — İş makinesinin Person algılanması

**İz düzeyi güven kararlılığı (orijinal videolar):** gerçek çalışan izlerinin kayan medyanı (son 25 gözlem, ≥8
gözlemden sonra) **0.712–0.802**, kapıdan geçen güven oranı 0.96–1.0; makine izi 160 **0.357–0.418**, oran 0.467;
track 111 medyan 0.336. **Ancak** uzak çalışan vekilinde (V1 ×0.33) gerçek çalışan izleri: kayan medyan en düşük
**0.30–0.53**, kapı oranı **0.22–0.98** → makine aralığıyla **örtüşüyor**. Sabit bir τ üç stok videodan genellenemez.

| Varyant | V3 alarmı | Orijinalde maliyet (baret-değerlendirilebilir kişi-kare) | V1 ×0.33 kapsam korunumu | Sentetik kaçırma | Durum |
|---|---|---|---|---|---|
| P1 kayan medyan ≥ 0.45 | **kalkar** | V1 224→193, V2 48→44, V3 328→300 (her iz için 8 gözlemlik ısınma) | **%65** | S05 (uzak, 0.42–0.48) | PROMISING / NOT VALIDATED |
| P1 ≥ 0.50 | kalkar | aynı | %62.7 | S05 | duyarlılık |
| P1 ≥ 0.55 | kalkar | aynı | **%45.9** | S05 | duyarlılık |
| P2 kapı oranı ≥ 0.6 | kalkar | aynı | %70.5 | **S06** (örtülü, dalgalı güven) | NOT VALIDATED |
| P3 MIN_SPAN 4 sn | kalkar | tüm kararlar 2 sn gecikir (V1 UYGUN 160→102) | — | yok | kısa maruziyetleri kaçırır; NOT VALIDATED |
| P4 en/boy ≤ 0.55 | kalkar | **V2 0 değerlendirme** (yakın çekim geniş kutular) | %73 | **S14** (çömelmiş) | **REJECTED** |
| P5 kırpıntı doğrulama 0.5 / 0.6 | **kalmaz** (160 kırpıntıda 0.71–0.76) | yok | %100 | — | **REJECTED** (aynı ağırlıklar bağımsız doğrulayıcı değil) |
| (ref) PERSON_CONF_MIN 0.50 | kalkar | V1 224→218 | %71.8 | S05 | 10-A.5A'daki kırılganlık sürüyor |

Ek yan etki: V1 karanlık bozulmasında P1/P2 **yeni** bir alarm üretti (iz 5, 13.8 sn; taban yok) — kapı yalnız
bastırmaz, pencere içeriğini değiştirerek karar üretebilir. Kontrol aralıklarında P1 ile yeni IHLAL yok.

**Sonuç:** P1 V3 sahte alarmını kaldıran en tutarlı aday; ancak uzak/örtülü gerçek çalışanları değerlendirme dışı
bırakma riski bozulma deneyinde **gözlendi** ve sentetik S05'te alarm kaçırıldı. Kamera bazlı **hariç tutma bölgesi**
(saha yapılandırması) ve model sert negatifleri (C1) alternatif olarak kalır; ikisi de bu fazda denenmedi.

## 5. E-03 — Kadraj kenarı değerlendirmesi

Taban (konservatif kural) korundu. Yalnız alt kenara değen (baş görünür) kişi-kare: V1 **67**, V2 **437**, V3 0
— ayrı sınıflandırıldı (`bottom_edge_only`). A3: baret yalnız `bottom` kenarına muaf (üst/sol/sağ kenar, düşük
güven, küçük boy hâlâ reddedilir); yelek üretim kapısında kalır (kesik kutuda gövde bölgesi güvenilmez).

| | V1 | V2 | V3 |
|---|---|---|---|
| Baret-değerlendirilebilir kişi-kare taban → A3 | 224 → **291** | 48 → **485** | 328 → 328 |
| Yelek-değerlendirilebilir | 224 → 224 | 48 → 48 | aynı |
| ANALIZ (karar yok) kişi-kare | 133 → 108 | 342 → **50** | 71 → 71 |
| Incident | yok → yok | 1 → **2** (4.7 → **2.1** başlangıç; **+22.4 sn run_end**) | aynı |
| Kontrol aralığında IHLAL kişi-kare | 0 → 0 | 0 → **40** | 0 → 0 |

Birleşik: C2 (A3+H1+H1b) V2'de **3** incident; C3 (A3+H5) 2 incident, etiketler `missing_detection` ve `conflicting`;
C4 (A3+H6≥16) V2 2 incident. Sentetik: A3, S08 (alt kenarda baretsiz çalışan; taban **hiç değerlendirmez**)
kaçırmasını giderir; S09 (alt kenarda baretli) alarmsız.

**Sonuç:** A3 kapsamı belirgin artırıyor ve tabanın yapısal bir yanlış negatifini (S08) gideriyor; fakat E-01
çözülmeden V2'de yanlış alarm olasılığı yüksek alarmları **artırıyor**. Talimat gereği **üretime önerilmez**.

## 6. E-04 — ByteTrack kimlik kararlılığı

**Kalman kontrolü (10-A.5A açık maddesi kapatıldı):** yeni iz doğduğunda kayıp izin Kalman tahmini kutusuyla
IoU × güven: 3→63 **0.047**, 6→66 0.090, 6→67 **0.114**, 63→81 **0.159**, V3 111→160 0.139 — hepsi ilk aşama sınırı
**0.2'nin altında**. Parçalanmanın nedeni tampon değil (T4 tampon 60: değişiklik yok), `match_thresh 0.8` +
`fuse_score` altında hızlı kutu değişimi. 63→81 ve 6→67 için 10-A.5A'daki "ORTA" kesinlik **YÜKSEK**'e çıkar
(Kalman tahmini ölçüldü).

| Takip varyantı | V1 kişi kimliği / tüm sınıf kimliği | başka sınıfla sürmüş kişi kimliği | parçalanma adayı | V2 incident | V3 incident |
|---|---|---|---|---|---|
| T0 üretim | 6 / 21 | V1 [66], V2 [74] | V1 4, V3 1 | 3@4.7–14.8 | 160@9.4 |
| T0b eşya ham tespitten (kontrol) | 6 / 21 | aynı | aynı | 3@4.7–14.1 | aynı |
| T1 yalnız Person | 7 / 7 | **yok** | V1 5, V3 1 | **2 incident** (4.7–17.7 stale + 10.3) | 9.4 |
| T2 `conf=0.10` (düşük skor aşaması), karar ≥ 0.25 | **8** / 22 | V1 [62] | V1 6, V3 2 | **2 incident** | 10.5 |
| T3 T1+T2 | 8 / 8 | yok | V1 6, V3 2 | 2 incident | 10.5 |
| T4 tampon 60 | 6 / 21 | aynı | aynı | aynı | aynı |
| T5 `match_thresh` 0.9 | 4 / 16 | **V1 [3, 5, 6]** (artar) | V1 1 | 1 | 9.4 |
| **T6 yalnız Person + match 0.9** | **3 / 3** | **yok** | **0** | **1** (4.7–14.1) | 9.4 (111+160 tek kimlik) |
| T7 T6 + düşük skor | 3 / 3 | yok | 0 | 1 | 9.4 |

- **`CONF=0.25` ön filtresi izole edildi (T2):** düşük skor aşamasını açmak bu verilerde kimlik sayısını **artırdı**
  (V1 6→8) ve V2'de mükerrer incident üretti; tek başına önerilmez.
- **Sınıfa duyarlı takip (T1)** sınıf kaçırmayı kaldırıyor ama tek başına parçalanmayı artırıyor: sınıftan bağımsız
  eşleştirme bazı karelerde kişi izini yelek kutusuyla "köprülüyordu".
- **Eşzamanlı çift tespit** T6'da sürüyor (V1 `2-12`: 1 kare, V2 `1-2`: 1 kare) — model kaynaklı.
- **A4 kimlik devri** (karar katmanı; 2.5 sn / 1×boy): T0'da V1–V3 incident'ları değişmez (V1 3→63, 6→66, 63→81
  devri; V2'de yeleğe geçen kimlik 74 ile 4 kez ileri-geri devir — yan etki). T3 + A4 V2'yi tek incident'a indirir;
  V3'te alarmı 10.5 → 9.7'ye çeker (makine izlerini birleştirir).
- **Sentetik takip:** yelek kutusunun kişi kimliğini alması T0/T2'de var, T1/T3/T6'da yok. Doğrusal hareketli kesişme
  ve 1.5 sn örtülme senaryolarında tüm varyantlar 0 kimlik değişimi, 0 paylaşılan kimlik (sentetik GT) — **Kalman için
  kolay; gerçek birleşme riskini ölçmez.**
- **Mükerrer incident regresyonu:** `test_id_fragmentation_duplicate_incident_and_handoff` (taban 2 incident, A4 1),
  `test_handoff_keeps_far_apart_people_separate_but_merges_same_spot` (A4'ün S13 birleşme riski belgelendi),
  10-A.5A `test_id_fragmentation_creates_second_incident_for_same_person` sürüyor.

**Sonuç:** T6 kimlik göstergelerinde açık iyileşme; **IDF1/kimlik değişimi iyileşmesi iddia edilmez** (fiziksel kimlik
GT'si yok). Daha gevşek eşleşme (IoU×skor ≥ 0.1) iki farklı kişiyi birleştirebilir; bu risk ölçülemedi.

## 7. Sentetik senaryo matrisi (yalnız yazılım mantığı)

15 senaryo (`experiments.synthetic_scenarios`; 12 fps). Taban profili (testle sabitlendi): kaçırılan
`must_alarm` **S08**; gereksiz alarm **S04** (örtülü baret), **S07** (makine); yanlış incident sayısı **S11**.

| Varyant | Kaçırılan gerçek ihlal (must_alarm) | Gereksiz alarm | Incident sayısı yanlış |
|---|---|---|---|
| Taban | S08 | S04, S07 | S11 |
| H1 | **S02**, S08 | S07 | S11 |
| H2 | **S01, S02, S05, S06, S08, S10, S14, S15** | — | S11, S12, S13 |
| H5 / H6≥16 / H6≥24 / H1b | S08 | S04, S07 | S11 |
| P1 (0.45–0.55) | **S05**, S08 | S04 | S11 |
| P2 | **S06**, S08 | S04 | S11 |
| P4 | S08, **S14** | S04 | S11 |
| P5 kırpıntı | S08 | S04 | S11 (kırpıntı skorları varsayımdır; gerçek veride etkisiz) |
| A3 | — | S04, S07 | S11 |
| A4 | S08 | S04, S07 | **S13** |
| C2 A3+H1+H1b | **S02** | S07 | S11 |
| C3 A3+H5 / C4 A3+H6≥16 | — | S04, S07 | S11 |

Hiçbir aday sentetik pakette taban kaçırmalarını artırmadan tüm sorunları gidermiyor. Değerler el ile seçilmiştir;
saha doğruluğu değildir.

## 8. Testler

| Kalem | Sonuç |
|---|---|
| Yeni `detector/tests/test_evaluation_experiments.py` (37): varsayılan monitör = PPEMonitor (12 rastgele dizi), sabitlerin korunması, kanıt kategorileri, her adayın amaçlanan etkisi **ve belgelenen FN/birleşme riski** (H1-S02, H2-S01, P1-S05, P2-S06, P4-S14, A4-S13), kırpıntı doğrulama, A3 üst kenar reddi, mükerrer incident + devir, taban sentetik profili, offline ByteTrack (yelek kaçırma, düşük skor aşaması, karar ≥ 0.25 sınırı, Kalman tanısı, görüntü kırpması, eşdeğerlik toleransı) | **37/37 PASS** |
| Yeni `detector/tests/test_evaluation_isolation.py` (5) | **5/5 PASS** |
| Dedektör tam paket | **317/317 PASS** (275 + 42) |
| Offline takip ↔ 10-A.5A izi (gerçek GPU önbelleği) | **IDENTICAL** 631/631 kare |
| Taban karar ↔ üretim olayları; deney monitörü varsayılanı ↔ üretim | **birebir** (V1/V2/V3) |
| Gizli bilgi denetimi | **PASS** |
| Dashboard testleri / uçtan uca | **çalıştırılmadı** — dashboard ve teslim zinciri değişmedi (yalnız `common.py`'ye koruma eklendi; `compose config` ile doğrulandı) |

İlk koşularda bulunan ve düzeltilen hatalar: H5 derecelendirmesi yalnız zayıf adayları sayıyordu (eşleşen baret de
çelişki sayılmalı — kural düzeltildi, test değiştirilmedi); takip eşdeğerlik karşılaştırıcısı çift yuvarlama ve eksik
kırpma nedeniyle yanlış `DIFFERENT` veriyordu (§2).

## 9. Bağımsız insan incelemesi

`20261102T181114Z_failure_analysis/review_pack/`: `review_sheet.csv` **0/31** satır dolu, `review.json` **yok**,
şablondaki iki alarm (V2 pid 3 @4.7, V3 pid 160 @9.4) `label: null`. **Her iki alarm UNREVIEWED.** Formlar
otomatik doldurulmadı; bu fazdaki hiçbir sonuç alarmların doğru/yanlış olduğunu varsaymaz ("AI gözlemi" ifadeleri
insan onayı değildir).

## 10. Veri, etiket ve lisans sınırları

- V1–V3 yalnız hata analizi/regresyon; eşik/varyant seçimi bunlar üzerinde **yapılmadı** (τ'lar duyarlılık olarak
  raporlandı). Dondurulmuş bağımsız test seti yok ve kullanılmadı.
- Sentetik senaryolar ve bozulmalar yalnız yazılım/yöntem duyarlılığıdır.
- Stok video lisansı ve SteelBench kullanım izni doğrulanmadı — hiçbir eğitim yapılmadı, hiçbir görüntü eğitime
  eklenmedi; aday ağırlık (C1) üretilmedi.
- Gerçek ihlal (baretsiz çalışan) içeren izinli, bağımsız etiketli veri yok → **hiçbir adayın yanlış negatif riskinin
  artmadığı kanıtlanmış değildir.**

## 11. GO / NO-GO

**Genel: NO-GO.** FAZ 10-A.5B'nin tamamlanması hiçbir değişikliğin üretime alınması anlamına gelmez.

| Aday | Karar | Üretime alma için gereken ek kanıt |
|---|---|---|
| E-01 H5 (etiket) | NO-GO (karar mantığı olarak); gösterim etiketi ayrı tasarım konusu | Etiketlerin GT ile kalibrasyonu; dashboard/İSG iş akışı onayı |
| E-01 H1/H2/H6 | **REJECTED** | — (model tarafı çözüm: C1) |
| E-02 P1 | NO-GO | İzinli saha verisinde uzak/örtülü/karanlık gerçek çalışanların iz güven dağılımı; τ'nun yalnız `dev`'de seçilmesi, dondurulmuş `test`te çalışan incident recall'ı için önceden yazılı non-inferiority marjı |
| E-02 P5 | **REJECTED** | (farklı mimari/bağımsız doğrulayıcı ayrı deney olur) |
| E-03 A3 | NO-GO (E-01'e bağımlı) | E-01 çözümü + "baş görünür" görünürlük etiketli GT'de baret yanlış alarmı ve kaçırma oranı |
| E-04 T6 | NO-GO | Fiziksel kimlik GT'li kesişme/örtülme klipleri: kimlik değişimi ↓ **ve birleşme artmıyor**; uçtan uca gecikme; eşyanın ham tespitten eşleşmesinin titreşim etkisi |
| E-04 A4 | NO-GO | Aynı yerde farklı kişi senaryolarında birleşme oranı (GT); İSG ile birleşme/mükerrer tercih kararı |

Ortak ön koşullar: (1) iki alarmın bağımsız insan incelemesi; (2) `docs/PHASE_10A5A_FAILURE_ANALYSIS_REPORT.md` §5
veri yönetimi (izinli saha verisi, video/kamera düzeyinde `train/dev/test`, `ppe-eval-video/1` GT, iki etiketleyici);
(3) adaylar `dev`'de seçilip dondurulmuş `test`te yalnız bir kez raporlanır.

## 12. Değişen dosyalar

Yeni: `detector/evaluation/experiments.py`, `detector/evaluation/isolation.py`,
`detector/tests/test_evaluation_experiments.py`, `detector/tests/test_evaluation_isolation.py`,
`tools/reliability/experiments_5b.py`, bu rapor. Güncellenen: `detector/evaluation/cli.py`
(`production_settings` sonrası gizli anahtar temizliği), `tools/reliability/common.py` ve `memtest.py`
(test alt süreci koruması), `docs/RELIABILITY_TEST_PLAN.md` §12. **Değişmeyen:** `best.pt`, `ppe_core.py`,
`protocol.py`, `detector.py`, tracker yapılandırması, dashboard, ana veriler. Önbellek ve sonuç dosyaları Git dışı
(`tools/reliability/output/`).
