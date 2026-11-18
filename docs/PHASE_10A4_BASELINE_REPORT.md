# FAZ 10-A.4 raporu — SteelBench referans değerlendirmesi (baseline)

**Okuyucu:** ML geliştirici, veri mühendisi, ürün ekibi; özet ve kararlar İSG/işletme için.
**Kapsam ayrımı:** Bu rapor **SteelBench referans çalışmasıdır** (3 yerel stok video, bilinen davranışın tekrar
üretilmesi ve kayıt bütünlüğü). Yol haritasındaki **asıl FAZ 10-A.4 — etiketli, izinli saha verisiyle bağımsız
doğruluk benchmark'ı — BLOCKED**'tır (veri yok; FAZ 10-A.3 DATA_COLLECTION_BLOCKED). Bu fazda model, `ppe_core.py`,
eşikler, sınıf haritası, dashboard ve migration'lar değiştirilmedi.

| Karar | Sonuç |
|---|---|
| SteelBench referans altyapısı (araç, manifest, bütünlük, sınıflama, karşılaştırma, izolasyon) | **PASS** — 3 gerçek çalıştırma (2–3 Kasım) BASELINE_REPRODUCED; kapanış çalıştırması referans özetine göre NO_DECISION_CHANGE; regresyonlar PASS (§11) |
| Etiketli saha benchmark'ı (asıl 10-A.4) | **BLOCKED** |
| Saha doğruluğu (precision, recall, yanlış alarm oranı) | **NOT_MEASURABLE / INSUFFICIENT_GROUND_TRUTH** |
| Gerçek şantiye pilotu | **NO-GO** |

## 1. Amaç ve kapsam

Gerçek dedektörle yapılan 3 videoluk testi (`tools/reliability/output/20261102T124324Z_capture_accuracy/`), model ve
PPE karar mekanizması değişikliklerini karşılaştırmak için **tek komutla tekrar çalıştırılabilir** bir araca
dönüştürmek: girdi manifesti, ham dedektör kaydı, kalıcı kayıtlarla karşılaştırma, bilinen hataların ayrı izlenmesi,
yanıltıcı başarı metriği üretmeme, izole çalışma, önceki çalıştırmayla fark.

## 2. Ortam (referans çalıştırması)

| Alan | Değer |
|---|---|
| Makine | macOS (iMac M1, 8 GB), `macOS-27.0-arm64` |
| Cihaz | Apple **MPS** |
| Yazılım | Python 3.12.16, torch 2.15.0, Ultralytics 8.4.183, OpenCV 4.10.0, NumPy 2.5.4 |
| Dashboard imajı (izole yığın) | `sha256:a7fd21e613d9…` |
| Kod | Git `358b24b` + bu fazın commit edilmemiş SteelBench dosyaları (manifestte `dirty: true`) |

## 3. Model, video ve test manifestleri

| Girdi | Değer |
|---|---|
| Model | `detector/models/best.pt`, SHA-256 `6886ad2fc301cf992152d4b047307071cac940082ab071175c0ef498fe67ba43`; sınıflar helmet=0, vest=2, Person=6 |
| Dedektör ayarı | CONF 0.25, IMGSZ 640, VID_STRIDE 2, TRACKER `bytetrack.yaml` |
| ByteTrack | high 0.25, low 0.1, new 0.25, track_buffer 30, match 0.8, fuse_score true |
| PPEMonitor | WINDOW 4 s, MIN_SPAN 2 s, MIN_OBS 8, ENTER 0.20, EXIT 0.40, PERSON_CONF_MIN 0.40, MIN_PERSON_H 60 px, EDGE 6 px, REALERT 3 s, STALE 10 s |
| Karar ayarı özeti (`config_sha256`, cihaz hariç) | `c829bb3293a1…` (iki çalıştırmada aynı) |
| `detector/.env` karar anahtarları | `IMGSZ, MODEL_PATH, VID_STRIDE` — üçünü de SteelBench açıkça verir; değerler aynı |
| V1 `16533594_1920_1080_25fps.mp4` | SHA-256 `6dd2ad33…66fd64`, 1920×1080, 373 kare, 25 fps |
| V2 `5434223-hd_1920_1080_24fps.mp4` | `df2f645c…2fa4`, 1920×1080, 577 kare, 23.98 fps |
| V3 `8965526-uhd_2160_3840_25fps.mp4` | `b9183548…67ef5`, 2160×3840, 315 kare, 25 fps |

Beklenen girdiler ve bilinen hatalar: `tools/reliability/steelbench_expected.json`. Her çalıştırmanın tam manifesti
`result.json` → `manifest` (sürümler, gerçekleşen ayarlar, Git commit'i, imaj).

## 4. Referans sonuçları (çalıştırma `20261102T131913Z_steelbench`; `…131753Z…` ve kapanış çalıştırması `20261103T064618Z_steelbench` ile birebir aynı)

| Video | İşlenen kare | Kişi-kare | Değerlendirilen (sistem kapsamı) | ANALIZ nedenleri | İz kimliği (≥10 kare) | Olaylar | Incident |
|---|---|---|---|---|---|---|---|
| V1 | 186 | 294 | 162 (0.551) | edge 42, yetersiz geçmiş 90 | 6 (5) | — | — |
| V2 | 288 | 553 | 210 (0.380) | edge 330, yetersiz geçmiş 12, düşük güven 2 | 3 (2) | start 1, ongoing 2, end 1 | 4.8 → 14.7 sn, `end` |
| V3 | 157 | 364 | 285 (0.783) | yetersiz geçmiş 60, düşük güven 19 | 4 (4) | start 1 | 9.0 → 12.6 sn, `run_end` |

Dedektör: 3 kamera eşzamanlı, 30.0 sn, çıkış kodu 0; tüm run'lar `finished / eof`.

## 5. Olay teslim bütünlüğü

| Kontrol | Sonuç |
|---|---|
| Olay: defter → DB | 5 → 5; **eksik 0, fazla 0, mükerrer 0** (defter ve DB ayrı sayılır) |
| Alan eşleşmesi (tür, iz, incident, eksik ekipman, oranlar, kutu, video zamanı, olay zamanı, kutu kare içinde) | tüm olaylarda eşleşti |
| Snapshot SHA-256 (defter = disk = API) | 2/2 |
| Incident yaşam döngüsü | 2/2 tutarlı (V3'teki açık incident `run_end` ile kapandı) |
| Run durumu + kişi raporu | 3/3; 12 kişi satırı rapordakiyle aynı |
| Dashboard geçmiş API'si | 2 incident (DB ile aynı) |
| Outbox sonu (sbv1–3) | bekleyen 0, ölü 0, karantina 0 |

## 6. İki yanlış alarmın analizi

| Alarm | Sistem | Görüntüde (ön inceleme) | Sınıflama | Kök neden |
|---|---|---|---|---|
| V2, t=4.8 sn, iz 3 | Baretsiz (baret %0, yelek %100) | Çalışan **koyu renkli baret takıyor**; omuzdaki fırça kafayı kısmen örtüyor | **KNOWN_FALSE_ALARM (E-01)** | İnceleme görselinde bu kişide **hiç baret kutusu tespit edilmemiş** — eşleştirme değil tespit hatası; örtülme > 3.5 sn olduğundan zamansal yumuşatma yetmiyor |
| V3, t=9.0 sn, iz 159 | Baretsiz (baret %0, yelek %50) | Kişi değil: arka planda **kırmızı iş makinesi kolu** | **KNOWN_FALSE_ALARM (E-02)** | Model kişi yanlış pozitifi; kişi kutusu oluşunca baret yokluğu ihlal sayıldı |

Bu iki alarm **başarılı tespit değildir**; sınıflama yalnız `steelbench_expected.json`'daki bağımsız doğrulanmış gerçek
ihlallere (`verified_true_incidents`, bugün boş) eşleşirse `VERIFIED_TRUE_VIOLATION` olabilir.

## 7. Bilinen hata kayıtları (E-01 – E-04)

| Kimlik | Tür | Video | Ölçüt | Referans durumu | İnceleme durumu |
|---|---|---|---|---|---|
| E-01 | yanlış alarm | V2 | t 4.8 ± 1.5 sn, kutu IoU ≥ 0.3, baret | REPRODUCED | PENDING_INDEPENDENT_HUMAN_REVIEW |
| E-02 | yanlış alarm | V3 | t 9.0 ± 1.5 sn, kutu IoU ≥ 0.3, baret | REPRODUCED | PENDING_INDEPENDENT_HUMAN_REVIEW |
| E-03 | kapsam açığı | V2 | ≥ 10 sn görünen iz, değerlendirilen oran ≤ 0.2 | REPRODUCED (iz 1: 23.9 sn, oran 0.0; edge 282 kare) | PENDING_INDEPENDENT_HUMAN_REVIEW |
| E-04 | iz parçalanması | V1 | iz kimliği > 2 (incelenen 2 kişi) | REPRODUCED (6 kimlik, 5'i ≥ 10 kare) | PENDING_INDEPENDENT_HUMAN_REVIEW |

İlk inceleme geliştirici asistanı tarafından (snapshot + tam kare + videolardan 6'şar örnek kare) yapıldı; bağımsız
insan doğrulaması **yapılmadı**. Bir hata ileride `NOT_REPRODUCED` olursa yalnız **FIX_CANDIDATE** sayılır;
"giderildi" için bağımsız inceleme + regresyon testi + expected kaydının güncellenmesi gerekir (`docs/STEELBENCH.md`).

## 8. İz parçalanması ve değerlendirme kapsamı

- **V1:** 2 çalışan → 6 iz kimliği; önden geçen araçlar örtüyor. İz 69 tek kare. Kareler boyunca toplam 32
  baret kutusu-karesi hiçbir kişiye eşlenmedi (aynı kutu her karede ayrı sayılır). Etki: her yeni kimlikte PPEMonitor penceresi baştan başlar ("yetersiz geçmiş" 90 kişi-kare).
- **V2:** uyumlu çalışan (iz 1) 24 sn boyunca **hiç değerlendirilmedi**. Baret kutusu bulunup eşlendiği hâlde kişi
  kutusu kadrajın üst kenarına değdiği için güvenilirlik kapısı (`edge`) kararı engelledi. İhlal olsaydı sessizce
  kaçırılırdı (E-03).
- **V3:** iki gerçek çalışan iyi kapsanıyor (0.84); düşük güvenli iz 93 ve makine izi 159 kapsamı düşürüyor.
- Sistem kapsamı yalnız **tespit edilen** kişileri kapsar; tespit edilmeyen kişiler bu sayıya girmez.

## 9. Ölçülebilir ve ölçülemeyen metrikler

| Metrik | Durum | Neden |
|---|---|---|
| Precision | **INSUFFICIENT_GROUND_TRUTH** | 2 alarm, 0 doğrulanmış gerçek, 2 bilinen yanlış; bağımsız incelenmiş alarm sayısı 0 (gerekli ≥ 30) |
| Recall | **NOT_MEASURABLE** | Doğrulanmış gerçek ihlal yok; tam video ground-truth yok; örnek kare incelemesi genellenemez |
| Yanlış alarm / kamera-saat | **INSUFFICIENT_GROUND_TRUTH** | 51.5 sn gözlem; tam etiket yok (gerekli ≥ 1 kamera-saat) |
| Sistem kapsamı | **MEASURED_SYSTEM_ONLY** | V1 0.551, V2 0.380, V3 0.783 — ground-truth değil |
| Ground-truth kapsamı | **NOT_MEASURABLE** | Kişi düzeyinde etiket yok |

Hiçbir doğruluk metriği PASS veya sayısal başarı olarak raporlanmadı.

## 10. Veri izolasyonu kanıtları

Ayrı compose projesi `ppe-bench` (kendi PostgreSQL ve snapshot volume'u, `127.0.0.1:8200`, kendi parola dosyası
`tools/reliability/output/bench.env`, Git dışı). Araç `ppe-rel`/`ppe-split` adıyla veya 8000/8100 portuyla çalışmayı
reddeder (birim test). İki gerçek çalıştırmada da bütün izolasyon kontrolleri **PASS**:

| Kontrol | Sonuç |
|---|---|
| Ana DB'de (`ppe-split`) bench kamera kimliği (`sbv1–3`) yok (salt okunur SELECT) | PASS |
| `ppe-rel` DB'sinde bench kaydı yok | PASS |
| `data/snapshots`'ta bench snapshot dosyası yok | PASS |
| Üretim `detector/outbox`'ında bench dosyası yok; bench kendi outbox'ını kullandı | PASS / PASS |
| `ppe-rel` ve ana yığın container kimlik/başlama zamanı değişmedi | PASS / PASS |
| Ayrı proje ve port; dedektör hedefi `http://127.0.0.1:8200` | PASS / PASS |
| Önce/sonra parmak izi (bilgi): ana DB sayımları, ppe-rel, üretim outbox, `data/snapshots` | değişmedi |

`common.py` değişikliği geriye uyumlu: `REL_PROJECT`/`REL_ENV_FILE`/`REL_DEFAULT_PORT` verilmezse `ppe-rel`,
`rel.env`, 8100 (doğrulandı).

## 11. Test ve regresyon sonuçları

**2 Kasım 2026 (geliştirme oturumu, macOS):**

| Test | Sonuç |
|---|---|
| SteelBench birim testleri | PASS 14/14 |
| Dedektör tam paketi | PASS 307, SKIP 1 (Windows ACL) |
| Dashboard tam paketi (gerçek PostgreSQL 17, `ppe_test`) | PASS 338/338 |
| Gerçek SteelBench çalıştırması #1 (`131753Z`) | BASELINE_REPRODUCED, çıkış 0, bütünlük ve izolasyon PASS |
| Gerçek SteelBench çalıştırması #2 (`131913Z`, #1 ile karşılaştırma) | BASELINE_REPRODUCED, **NO_DECISION_CHANGE**, çıkış 0 |

**3 Kasım 2026 (kapanış doğrulaması):**

| Test | Sonuç |
|---|---|
| SteelBench birim testleri | **PASS 14/14** |
| Dedektör tam paketi | **PASS 307, SKIP 1** |
| `git diff --check`, kapsam ve sır taraması | temiz |
| `common.py` varsayılanı (`ppe-rel`, `rel.env`, 8100) | doğrulandı |
| `steelbench.py compare` (#1 → #2 ve referans özeti → #2) | NO_DECISION_CHANGE |
| Mevcut `result.json` dosyaları geçerli JSON; Markdown yeniden üretimi birebir aynı; video/model SHA-256 expected ile aynı | doğrulandı |
| Dashboard tam paketi (06:45:16–06:45:55Z; `ppe_test` DB, ana DB tablolarına dokunmaz) | **PASS 338/338** |
| `ppe-bench` altyapı izolasyonu (container etiketleri, volume, ağ, port, DB hedefi) | doğrulandı: proje `ppe-bench`, ağ `ppe-bench_default`, volume `ppe-bench_rel_pgdata` / `ppe-bench_rel_data` (host `data/` bağlaması yok), yalnız `127.0.0.1:8200`, DB host `db` yalnız bench ağında; `ppe-rel` ve `ppe-split` ayrı volume'larda |
| Gerçek SteelBench çalıştırması #3 (`20261103T064618Z_steelbench`, 06:46:18–06:47:01Z, MPS, dedektör 29.8 sn) | **BASELINE_REPRODUCED, çıkış 0**; referans özetine göre **NO_DECISION_CHANGE** (ortam farkı yok); girdiler PASS; bütünlük PASS (5/5 olay, eksik/fazla/mükerrer 0; snapshot 2/2; incident 2/2; run 3/3); alarmlar E-01, E-02 `KNOWN_FALSE_ALARM`; E-01–E-04 REPRODUCED; izolasyon 9/9 PASS |
| Bağımsız önce/sonra parmak izi (test + benchmark çevresinde; salt okunur) | **değişmedi**: ana DB sayımları ve revision (0006), `ppe-rel` container'ları (kapalı), `data/snapshots`, üretim `detector/outbox` (dosya adı/boyut/zaman özeti), ana container'lar |

Not: Docker Desktop açıldıktan sonra `ppe-bench` container'ları 06:44Z'de çalışır durumdaydı (2 Kasım'da oluşturulan
aynı container'lar; başlatan süreç belirlenemedi, o anda çalışan SteelBench/dedektör süreci yoktu). Yalnız izole
yığındır; çalıştırma sonunda araç tarafından durduruldu.

## 12. Tekrar çalıştırma

```bash
# macOS / Linux (proje kökü; Docker Desktop açık)
REL_DETECTOR_PY=$PWD/.venv/bin/python python3 tools/reliability/steelbench.py run --images
# Windows (PowerShell)
$env:REL_DETECTOR_PY="$PWD\detector\.venv\Scripts\python.exe"; python tools\reliability\steelbench.py run --images
# birim testleri
.venv/bin/python -m pytest -q tools/reliability/test_steelbench.py
```
Beklenen: `BASELINE_REPRODUCED`, karşılaştırma `NO_DECISION_CHANGE`, çıkış 0. Çıkış 2 = insan incelemesi gerekli;
1 = girdi/bütünlük/izolasyon hatası. Aday model: `--model models/<aday>.pt`. Ayrıntı: `docs/STEELBENCH.md`.

## 13. Lisans ve veri kullanım kısıtları

- Üç video yerel stok videodur; lisans/kullanım koşulu **doğrulanmadı** (`LICENSE_UNVERIFIED`). Yalnız yerel iç test;
  Git'e eklenmez, paylaşılmaz; kapsam doğrulanmamış videolarla genişletilmez.
- `best.pt` ve Ultralytics AGPL-3.0 — lisans kararı **NO-GO**.
- Git'e yalnız kod, beklenen değerler ve sayısal referans özeti girer. Ham defter, inceleme görselleri (kişi
  görüntüsü içerir), snapshot, `bench.env`, token ve parola Git dışıdır (`tools/reliability/output/`).

## 14. Sonraki ML iyileştirme önerileri (uygulanmadı; her biri FAZ 10-A.5'te etiketli veri + SteelBench A/B gerektirir)

| Hata | Öneri | Ölçüm |
|---|---|---|
| E-01 koyu/örtülü baret | Koyu baret ve kısmi örtülme örnekleriyle (izinli veri) ince ayar; kafa bölgesi örtülüyken gözlemi "bilinmiyor" sayan karar kuralı (ihlal yerine) | E-01 NOT_REPRODUCED + yeni alarm yok + bağımsız inceleme |
| E-02 makine = kişi | İş makinesi/kırmızı ekipman zor negatifleriyle eğitim; kişi kutusu en-boy/güven filtresi; kamera bazlı hariç tutma bölgesi (ROI) | E-02 NOT_REPRODUCED; gerçek kişi kapsamı düşmemeli |
| E-03 kenar kapısı | Kişi kutusu kenara değse de kafa bölgesi kadraj içindeyse baret kararına izin veren kapı varyantı (yalnız baret için) | V2 iz 1 kapsamı > 0; yeni yanlış alarm yok |
| E-04 parçalanma | `track_buffer`/eşleşme eşiği taraması; görünüm tabanlı yeniden tanıma (ReID) değerlendirmesi; incident sürekliliği | V1 iz sayısı ↓, "yetersiz geçmiş" ↓, duplicate yok |

Kısıt: aynı 3 videoya göre ayar yapmak aşırı uyum riskidir. Kabul kararı yalnız bağımsız etiketli saha verisiyle
verilir.

## 15. Açık riskler ve saha pilotu kısıtları

1. Saha doğruluğu bilinmiyor; mevcut 2 alarmın ikisi de yanlış; gerçek ihlal içeren doğrulanmış örnek yok.
2. E-03: kadraja yakın çalışanlar sessizce değerlendirme dışında kalabilir (güvenlik riski R-01).
3. İnceleme tek kişilik ön incelemedir; bağımsız insan doğrulaması bekliyor.
4. Referans macOS/MPS'te üretildi; Windows/CUDA'da küçük farklar beklenir (cihaza özgü karşılaştırma gerekir).
5. Depoda uzak (remote) yok; commit'ler yalnız bu makinede.
6. KVKK, lisans, İSG kabul eşikleri, 24 saatlik soak ve gerçek RTSP doğrulaması eksik → **saha pilotu NO-GO**.
