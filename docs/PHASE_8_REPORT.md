# FAZ 8 raporu — Model doğrulama, PPE doğruluğu ve hata analizi

Tarih: 2026-11-01. Başlangıç: `cd78c28` (FAZ 7). Plan: `docs/PPE_Saha_Hazirlik_Claude_Opus_Plani.md`, FAZ 8.
Protokol: `docs/MODEL_EVALUATION_PROTOCOL.md`.

**Sonuç (iki ayrı statü):**

| Statü | Karar |
|---|---|
| Değerlendirme altyapısı (araçlar, şema, metrikler, testler, gerçek modelle uçtan uca çalıştırma) | **PASS** |
| Saha doğruluğunun nicel kanıtı (etiketli şantiye videosu üzerinde PPE ihlal / incident metrikleri) | **BLOCKED — etiketli saha verisi yok** |

Bu fazda model eğitilmedi; `best.pt`, `ppe_core.py`, ByteTrack ayarları, üretim eşikleri, PostgreSQL ana verisi,
snapshot'lar ve outbox dosyaları değiştirilmedi. Aşağıdaki nesne düzeyi sayılar **modelin kendi eğitim veri
kümesinin** test bölümünden gelir; saha performansı yerine geçmez.

## 0. Ön denetim

- Dal `main`, HEAD `cd78c28af1dd…` (FAZ 7), çalışma ağacı temiz; izlenmeyen tek dosya kullanıcının plan belgesi
  (korundu, commit'e eklenmedi). FAZ 8'e ait önceki kısmi çalışma yoktu.
- Okunanlar: plan belgesi (tamamı), `PHASE_7_REPORT.md`, `detector.py`, `ppe_core.py`, `protocol.py`
  (IncidentTracker), Ultralytics 8.4.183 video yükleyici ve tracker kancaları.
- Başlangıç regresyonu FAZ 7'deki gibi; bu fazdaki sonuçlar §9'da.

## 1. Değişen dosyalar

Yeni (üretim kodundan bağımsız, yalnız okur):

| Dosya | İçerik |
|---|---|
| `detector/evaluation/metrics.py` | P/R/F1/FNR, Wilson aralığı, kutu eşleme, kişi-kare karışıklığı (UNKNOWN kuralları), incident eşleme (gecikme, duplicate, yanlış alarm, kapanmamış), kamera-saat başına yanlış alarm |
| `detector/evaluation/dataset.py` | YOLO etiket doğrulama, split denetimi, sınıf eşleme kontrolü, sızıntı (SHA-256, dHash, kaynak grubu), donmuş manifest + doğrulama |
| `detector/evaluation/annotations.py` | Video GT şeması `ppe-eval-video/1` + doğrulayıcı + sentetik örnek |
| `detector/evaluation/pipeline.py` | Üretim zincirinin ölçümlü çevrimdışı kopyası (YOLO+ByteTrack → match_items → PPEMonitor → IncidentTracker), aşama zamanlaması, GPU/CPU/RAM, GT karşılaştırması, koşul/kamera kırılımı |
| `detector/evaluation/detect_eval.py` | `model.val`, üretim çalışma noktası, eşik taraması (yalnız val), güven dağılımı, FP/FN örnekleri, boy kırılımı, tek kare kişi-düzeyi vekil |
| `detector/evaluation/temporal.py` | Sentetik zamansal senaryolar (karar mantığı karakterizasyonu) |
| `detector/evaluation/cli.py`, `__main__.py`, `__init__.py` | `python -m evaluation env|audit|detect|video|temporal|validate-gt|example-gt` |
| `detector/evaluation/manifests/construction-ppe.manifest.json` | Donmuş manifest (yalnız dosya adı + SHA-256; görüntü yok) |
| `detector/tests/test_evaluation_metrics.py` (16), `test_evaluation_dataset.py` (22), `test_evaluation_pipeline.py` (11) | 49 yeni test |
| `docs/MODEL_EVALUATION_PROTOCOL.md`, `docs/PHASE_8_REPORT.md` | Protokol ve bu rapor |

Değişen: `.gitignore` (+`detector/evaluation/datasets/`, `detector/evaluation/output/`).
Git dışı (yerel): indirilen veri kümesi `detector/evaluation/datasets/` (zip dahil ~360 MB) ve tüm çıktılar
`detector/evaluation/output/`. İstenirse silinebilir; kod onlara bağımlı değildir.

## 2. Model checkpoint ve sürümler

| Alan | Değer |
|---|---|
| Dosya | `detector/models/best.pt`, 5 479 954 bayt |
| SHA-256 | `6886ad2fc301cf992152d4b047307071cac940082ab071175c0ef498fe67ba43` (FAZ 8 başı ve sonu aynı) |
| Mimari | YOLO26n (`yolo26n.yaml`, scale n, end2end/NMS'siz kafa), 2.39 M parametre, 5.4 GFLOPs |
| Sınıflar (runtime `names`) | 0 helmet, 1 gloves, **2 vest**, 3 boots, 4 goggles, 5 none, **6 Person**, **7 no_helmet**, 8 no_goggle, 9 no_gloves, 10 no_boots — üretim eşlemesi (0/2/6) **uyumlu** |
| Eğitim (checkpoint meta) | 2026-10-20, Ultralytics 8.4.183, `data=construction-ppe.yaml`, 100 epoch, imgsz 640, AdamW, seed 0, deterministic |
| Eğitim sonu val metriği (meta) | P 0.569, R 0.609, mAP50 0.605, mAP50-95 0.291 (11 sınıf) |
| Lisans (meta) | AGPL-3.0 — lisans NO-GO kararı sürüyor |
| Çalışma ortamı | Python 3.12.6, torch 2.14.1+cu126, CUDA 12.6, Ultralytics 8.4.183, OpenCV 4.10.0, RTX 3050 Laptop 4 GB (sürücü 566.07) |
| Üretim ayarları (`detector/.env` + `detector.py`) | DEVICE 0, IMGSZ 640, CONF 0.25, VID_STRIDE 2, NMS IoU varsayılan (0.7), TRACKER `bytetrack.yaml` |
| ByteTrack | high 0.25, low 0.1, new 0.25, track_buffer 30 kare, match 0.8, fuse_score true |
| PPEMonitor | WINDOW 4 s, MIN_SPAN 2 s, MIN_OBS 8, ENTER 0.20, EXIT 0.40, PERSON_CONF_MIN 0.40, MIN_PERSON_H 60 px, EDGE 6 px, REALERT 3 s, STALE 10 s |

Not: `detector/models/best/` dizini açılmış bir torch arşiv kopyasıdır (`data.pkl`); üretim `models/best.pt`
kullanır. Dokunulmadı.

## 3. Veri kümesi envanteri

| Kaynak | Süre / boyut | FPS | Çözünürlük | Kamera / koşul (görsel inceleme) | Etiket | Lisans / izin | Eğitimle örtüşme |
|---|---|---|---|---|---|---|---|
| V1 `16533594_1920_1080_25fps.mp4` | 14.9 s, 373 kare | 25 | 1920×1080 | Dış mekân, gün ışığı, sabit görünümlü, cadde seviyesi; 2 işçi orta mesafe, önden geçen araçlar | **yok** | Dosya adı stok video (Pexels benzeri) kalıbı; lisans **doğrulanmadı** | dHash en yakın 16 (yakın kopya yok) |
| V2 `5434223-hd_1920_1080_24fps.mp4` | 24.1 s, 577 kare | 23.98 | 1920×1080 | İç mekân (kaba inşaat), ortam ışığı, el kamerası ile arkadan yakın takip; omuzda taşınan fırça kafayı örtüyor | **yok** | aynı | en yakın 15 |
| V3 `8965526-uhd_2160_3840_25fps.mp4` | 12.6 s, 315 kare | 25 | 2160×3840 (dikey) | Açık toprak saha, parlak gün ışığı; 2 işçi kameraya yürüyor, arka planda kırmızı iş makinesi | **yok** | aynı | en yakın 15 |
| Construction-PPE (Ultralytics) | 1416 görüntü: train 1132 / val 143 / test 141 | — | çeşitli (çoğu yakın plan) | İnternet/stok ve sahnelenmiş fotoğraflar; gece, yağmur, sabit CCTV açısı neredeyse yok | Ultralytics'in insan etiketleri (bağımsız doğrulanmadı) | AGPL-3.0 (zip içi LICENSE); tanınabilir kişiler içerir → yalnız yerel, Git dışı | **Modelin eğitim kümesinin kendisi** (bkz. §4) |

- Videolarda görünen herkes baret + yelekli görünüyor (tek gözden geçirenin görsel izlenimi, GT değildir). Etiketlenseler
  bile pozitif ihlal örneği yok denecek kadar azdır → ihlal recall'ü bu videolarla ölçülemez.
- Zip: `github.com/ultralytics/assets/releases/.../construction-ppe.zip`, 178 415 813 bayt,
  SHA-256 `bef8dcb599aa4e9d…2c2632ccc`. Yalnız indirildi; hiçbir görüntü dışarı gönderilmedi.
- Gerçek çalışan / şantiye kamerası verisi **toplanmadı** (izin yok).

## 4. Etiket kalitesi ve veri sızıntısı

| Kontrol | Sonuç |
|---|---|
| Geçersiz etiket satırı (sınıf aralığı, koordinat, boyut) | 0 (tüm split'ler) |
| Etiketsiz görüntü / boş etiket | 0 / 0 |
| Sahipsiz etiket dosyası | **train'de 10** (`image940(1).txt` … `image950(1).txt`) — görüntüsü yok; kalite bulgusu |
| Sınıf eşleme (model ↔ veri ↔ üretim) | uyumlu |
| Bayt-özdeş görüntü (val/test ↔ train) | 0 |
| Algısal yakın kopya, dHash Hamming ≤ 4 | test↔train **14**, val↔train **10**, test↔val **3** |
| dHash ≤ 8 | test görüntülerinin **34/141'i (%24)** |
| Görsel örneklem | ≤4 grubundan 4 çift: 2 neredeyse aynı kare, 1 aynı sahne/kişi farklı poz, 1 yanlış eşleşme (siyah çerçeve). 5–8 grubundan 8 çift: 5 aynı sahne/kişi, 3 farklı |
| Kaynak meta verisi (şantiye/kamera/video/gün) | **yok** → kaynak düzeyinde ayrım doğrulanamaz |

Değerlendirme: test kümesinin tahminen **%15–25'inin** train'de aynı sahneden karşılığı var; val kümesi ayrıca
checkpoint seçiminde kullanıldı. Bu nedenle §5'teki sayılar **iyimser, dağılım içi** ölçümlerdir ve saha CCTV'sine
genellenemez. Test kümesi manifest ile donduruldu: `content_sha256 fe003f103cba…cfcb0e`, 1416 dosya
(`detector/evaluation/manifests/`); test değerlendirmesi manifest doğrulamasıyla çalıştırıldı, testte eşik taraması
yapılmadı (CLI reddediyor).

## 5. Nesne düzeyi detection metrikleri (Construction-PPE; saha verisi DEĞİL)

Ultralytics `model.val` (conf 0.001, NMS IoU 0.7, imgsz 640):

| Split | Görüntü | Örnek | P | R | mAP50 | mAP50-95 |
|---|---|---|---|---|---|---|
| val (seçimde kullanıldı) | 143 | 1172 | 0.585 | 0.611 | 0.613 | 0.287 |
| **test** | 141 | 1251 | 0.659 | 0.515 | **0.547** | **0.272** |

Sınıf AP50 / AP50-95 (test): helmet **0.927 / 0.497**, vest **0.890 / 0.574**, Person **0.813 / 0.482**,
no_helmet **0.192 / 0.077**; diğerleri: gloves 0.791, boots 0.743, goggles 0.806, none 0.419, no_goggle 0.209,
no_gloves 0.207, no_boots 0.020. (Val: helmet 0.845, vest 0.849, Person 0.921, no_helmet 0.252.)

Üretim çalışma noktası (conf ≥ 0.25, IoU ≥ 0.5, Wilson %95):

| Sınıf | Split | GT | TP | FP | FN | Precision [CI] | Recall [CI] | F1 | FNR |
|---|---|---|---|---|---|---|---|---|---|
| helmet | test | 192 | 174 | 18 | 18 | 0.906 [0.857–0.940] | 0.906 [0.857–0.940] | 0.906 | 0.094 |
| vest | test | 178 | 158 | 34 | 20 | 0.823 [0.763–0.870] | 0.888 [0.833–0.926] | 0.854 | 0.112 |
| Person | test | 236 | 192 | 46 | 44 | 0.807 [0.752–0.852] | 0.814 [0.759–0.858] | 0.810 | 0.186 |
| no_helmet | test | 40 | 9 | 10 | 31 | 0.474 [0.273–0.683] | **0.225** [0.123–0.375] | 0.305 | 0.775 |
| helmet | val | 201 | 166 | 33 | 35 | 0.834 | 0.826 [0.767–0.872] | 0.830 | 0.174 |
| vest | val | 171 | 137 | 28 | 34 | 0.830 | 0.801 [0.735–0.854] | 0.815 | 0.199 |
| Person | val | 239 | 215 | 28 | 24 | 0.885 | 0.900 [0.855–0.932] | 0.892 | 0.100 |
| no_helmet | val | 45 | 14 | 21 | 31 | 0.400 | 0.311 [0.195–0.457] | 0.350 | 0.689 |

Karışıklık matrisi (test, satır = tahmin, sütun = gerçek; Ultralytics): helmet→helmet 166, vest→vest 149,
Person→Person 192, no_helmet→no_helmet 22; sınıflar arası karışma küçük (vest↔Person 8/5, helmet→vest 7,
no_helmet→helmet 3); kaçırılan (background satırı): helmet 4, vest 6, **Person 30**, **no_helmet 10**. Tam matris,
PR eğrileri ve F1 eğrileri `output/*_detect_test/val_test/` altında (Git dışı).

Boy kırılımında recall (test / val): helmet 16–32 px **0.79 / 0.68**, 32–64 px 0.88 / 0.76, ≥64 px 0.96 / 0.94.
Veri kümesinde 60 px altı kişi yalnız 2 adet → uzak çalışan performansı **ölçülemez**.

no_helmet: recall 0.23 (test) — üretime bağımsız sinyal olarak eklemeye **uygun değil** (eklenmedi).

## 6. PPE ihlal metrikleri (kişi-kare)

**Saha videosu üzerinde: BLOCKED** (etiketli video yok). Araç hazır: `video --gt`.

Tek kare **vekil** ölçüm (Construction-PPE test; GT kişi durumu GT kutularından geometrik türetildi — insan ilişki
etiketi değildir; tek karede PPEMonitor penceresi çalışmaz, yalnız `match_items` + güvenilirlik kapısı ölçülür):

| Ekipman | GT present / missing / unknown | TP | FP | FN | TN | Recall [CI] | Not |
|---|---|---|---|---|---|---|---|
| Baret eksikliği (test) | 120 / 27 / 89 | 15 | 13 | 12 | 74 | 0.556 [0.373–0.724] | FN'nin 11/12'si güvenilirlik kapısı (kare kenarına değen yakın plan kişiler), 1 kişi tespit edilmedi; FP'nin 11/13'ü GT'de kişi olmayan tespit |
| Baret eksikliği (val) | 126 / 37 / 76 | 23 | 4 | 14 | 78 | 0.622 [0.461–0.759] | FN'nin 13/14'ü güvenilirlik kapısı |
| Yelek eksikliği | — | — | — | — | — | **NOT EVALUATED** | Veri kümesinde `no_vest` sınıfı yok → eksik yelek GT'si kurulamaz. Yelekli kişilerde yanlış ihlal: 3 / 83 (test) |

Bu vekil sayılar sahayı temsil etmez; güvenilirlik kapısının yakın plan fotoğraflarda kişilerin büyük kısmını
kararsız bıraktığını gösterir (CCTV'de beklenen durum değil, sahada ölçülmeli).

## 7. Incident düzeyi doğruluk, yanlış alarm ve gecikme

| Ölçüt | Gerçek veri | Durum |
|---|---|---|
| Gerçek / algılanan / kaçırılan / yanlış incident, duplicate | yok | **BLOCKED** |
| Yanlış alarm / kamera-saat | yok | **BLOCKED** |
| Baret ve yelek eksikliği recall / FNR | yok | **BLOCKED** |

Etiketsiz videolarda (toplam 51.5 s) sistem 2 alarm üretti; ikisi de görsel incelemede **aday yanlış alarm**dır
(§10). 51.5 saniyelik etiketsiz örnekten kamera-saat oranı **hesaplanmadı** (anlamsız ve doğrulanmamış).

**Karar gecikmesi** (sentetik, ideal tespit, 12.5 Hz = 25 fps / stride 2 — model gecikmesi değil, alt sınır):

| Senaryo | Sonuç |
|---|---|
| Uyumlu kişi bareti çıkarır | `start` **3.2 s** sonra |
| Bareti geri takar | `end` **1.6 s** sonra |
| Yelek çıkar/giyer | 3.2 s / 1.6 s |
| Kişi kadraja baretsiz girer | `start` **2.1 s** (MIN_SPAN 2 s) |

**Uçtan uca uyarı gecikmesi** = karar gecikmesi (≥2.1–3.2 s ideal) + işlem (alarm karesi 34–45 ms, §12) +
teslim (outbox → dashboard; ölçülmedi, FAZ 9) + operatör ekranı. Canlı RTSP gecikmesi **ölçülmedi (SKIP)**.

## 8. Zorlu saha koşulları

| Koşul | Gerçek etiketli örnek | Durum |
|---|---|---|
| Gün ışığı | 3 etiketsiz video; Construction-PPE çoğunlukla gün ışığı | Saha: **NOT EVALUATED** |
| Düşük ışık / gece, ters ışık, yağmur/sis/toz | 0 | **NOT EVALUATED** |
| Uzak çalışan (<60 px kişi) | veri kümesinde 2 kişi | **NOT EVALUATED** (sentetik: kapı nedeniyle hiç alarm yok, §11) |
| Küçük baret (16–32 px) | test 14, val 19 kutu | nesne recall 0.79 / 0.68 (dağılım içi) |
| Kalabalık kadraj | birkaç görüntü (ör. Person FN'ler tek kalabalık karede yoğun) | ayrı ölçüm yok — **NOT EVALUATED** |
| Kısmi örtülme / kafa görünmemesi | V2'de 1 gerçek örnek (etiketsiz) | sentetik eşik: ≥3.5 s → yanlış alarm (§11) |
| Benzer renk / makine | V3'te 1 gerçek örnek (etiketsiz) | aday yanlış alarm (§10) |
| Hareket bulanıklığı, titreşim, kamera açısı | etiket yok | **NOT EVALUATED** |
| Video çözünürlüğü (1080p vs UHD) | etiketsiz | yalnız performans ölçüldü (§12) |
| Kare atlama (VID_STRIDE 2 vs 1) | etiketsiz | V2'de stride 1 ile 2 alarm (stride 2: 1); doğruluk etkisi **ölçülemez** |

En yüksek yanlış negatif riski taşıyan senaryolar (kanıt düzeyi belirtilmiştir):
1. **Uzak / küçük / düşük güvenli / kare kenarındaki kişi** — güvenilirlik kapısı nedeniyle tasarım gereği hiç
   değerlendirilmez (sentetik kesin; V2'de bir kişi 286/286 karede `ANALIZ` kaldı — gerçek gözlem).
2. **Benzer görünen sahte baret** (şapka, saç, turuncu nesne) — kare başına sahte baret olasılığı ≥0.4 ise ihlal
   hiç algılanmaz; 0.3'te 1/20 kaçırma ve ortalama 15.6 s gecikme (sentetik).
3. **Küçük baret kutuları** — 16–32 px'te baret recall 0.68–0.79 (dağılım içi). Kaçırılan baret uyumlu kişide
   yanlış alarm üretir; aynı boyda baş bölgesindeki sahte baret tespiti ise gerçek ihlali gizler (madde 2).

## 9. Testler

| Paket / kalem | Komut | Sonuç |
|---|---|---|
| Dedektör (153 mevcut + 49 yeni) | `cd detector; .venv\Scripts\python -m pytest` | **202/202 PASS** |
| — metrik formülleri, sıfır payda, UNKNOWN, incident/duplicate/temporal eşleme, kamera-saat | `test_evaluation_metrics.py` | 16/16 PASS |
| — leakage, eksik/geçersiz etiket, sınıf eşleme uyumsuzluğu, manifest tutarlılığı/kurcalama, GT şeması | `test_evaluation_dataset.py` | 22/22 PASS |
| — üretim `run_once` ile karar eşdeğerliği, no_helmet etkisizliği, GT karşılaştırma, ID switch, senaryolar, tekrar üretilebilirlik, CLI | `test_evaluation_pipeline.py` | 11/11 PASS |
| SQLite outbox (FAZ 7) | dedektör paketi içinde (34) | PASS |
| Dashboard (gerçek PostgreSQL 17, test DB) — WebSocket, ingest idempotency, RBAC/oturum/CSRF, migration drift | `docker compose --profile test run --rm tests` | **311/311 PASS** |
| Tarayıcı (Edge headless, izole fixture 127.0.0.1) | `node dashboard/tests/ui/ui_check.mjs` | **28/28 PASS** (fixture durduruldu) |
| LAN TLS izole smoke | `python deploy/lan/lan_smoke.py` | SKIP (deploy değişmedi; yeniden koşulmadı) |
| Gerçek model: `best.pt` inference (val/test görüntü, 3 video, RTX 3050) | `python -m evaluation detect/video` | PASS |
| Kare okuma yolu ↔ üretim `model.track(source, vid_stride)` eşdeğerliği | `video --check-equivalence 60` | **60/60 kare aynı × 3 video — PASS** |
| Nicel saha doğruluğu (PPE ihlal, incident, kamera-saat) | — | **BLOCKED** (etiketli saha verisi yok) |
| Kontrollü takip testleri (kadrajdan çıkıp dönme, örtüşme, baret takıp çıkarma — gerçek kişilerle) | — | **BLOCKED** (izinli çekim yok; yalnız sentetik) |
| İki annotator uyumu (κ) | — | **BLOCKED** |
| Canlı RTSP gecikmesi, çoklu kamera | — | SKIP (FAZ 9) |

## 10. Yanlış pozitif / negatif analizi

Saha videolarından (etiketsiz; tek gözden geçiren; insan onayı gerekir; görüntü dışa aktarılmadı):

| Kimlik | Video, zaman | Sistem | Görsel inceleme | Hata türü | Güven | Olası kök neden |
|---|---|---|---|---|---|---|
| E-01 | V2, t=4.7 s (pid 3), `end` t=14.8 s | Baretsiz ihlal, baret oranı %0, yelek %100 | Koyu renkli baret var, omuzdaki fırça tarafından kısmen örtülüyor | Aday FP | orta | Model detection failure (koyu baret + örtülme) + visibility ambiguity; örtülme >3.5 s olduğu için temporal smoothing yetmedi |
| E-02 | V3, t=9.4 s (pid 160), run sonunda açık (`run_end`) | Baretsiz ihlal, baret %0, yelek %25 | Arka plandaki kırmızı iş makinesi kolu "Person" algılandı | Aday FP | yüksek | Model detection failure (makineye kişi FP'si) → kişi kutusu oluşunca baret yokluğu ihlal sayıldı |
| E-03 | V2, tüm video (pid 1) | 286/286 karede `ANALIZ` | Yakın plandaki, kareden taşan uyumlu işçi | Kapsam açığı (ihlal olsaydı FN) | yüksek | Güvenilirlik kapısı (EDGE_MARGIN_PX) |
| E-04 | V1 | ~2–3 kişi için 6 iz ID'si; kişi-karelerin %45'i `ANALIZ` | Önden geçen araçlar işçileri örtüyor | Aday fragmentasyon | düşük (GT yok) | Tracking error (örtülme sonrası yeni ID → MIN_SPAN yeniden başlar) |

Construction-PPE test (dağılım içi; ayrıntılı listeler `output/*_detect_test/detect.json`):
- Baret FN 18: **14'ü < 64 px** (3 adet 16–32, 11 adet 32–64), 2'si düşük güvenle bulunmuş → baskın neden küçük nesne.
- Yelek FN 20: çoğu ≥64 px; 5'i başka sınıf (ör. `none`) olarak karıştırılmış. Yelek FP 34 (10 sahte, 5 konum).
- Person FN 44 (recall 0.81): kalabalık karelerde yoğunlaşıyor; Person FP 46.
- no_helmet FN 31/40; 32–64 px'te 7/7 kaçırıldı.

Sınıflandırma özeti: model detection failure (E-01, E-02, küçük baret, makine), visibility ambiguity (E-01),
tracking error (E-04), karar kapsamı/güvenilirlik kapısı (E-03), dataset/annotation issue (10 sahipsiz etiket,
sahne düzeyi sızıntı). Person/PPE association hatası etiketli veride ölçülemedi (sentetik ID swap: §11).

**İyileştirme önerileri (bu fazda uygulanmadı; her biri ayrı onay ve validation ölçümü gerektirir):**
- *Veri*: protokole göre izinli saha CCTV videosu (sabit yüksek açı, uzak çalışan, gece/IR, yağmur/toz,
  kalabalık), gerçek ihlal örnekleri dahil; zor negatifler (iş makinesi, kırmızı/turuncu ekipman, koyu baret);
  kaynak düzeyinde bölme ve yakın kopya temizliği; train'deki 10 sahipsiz etiket.
- *Model* (ayrı faz): küçük baret ve kişi recall'ü için saha verisiyle ince ayar; uzak kameralarda daha yüksek
  `IMGSZ` yalnız çevrimdışı ölçülerek değerlendirilebilir.
- *Karar mantığı* (değiştirilmedi; aday çalışma konuları): baş bölgesi örtülüyken gözlemi "bilinmiyor" sayma;
  ID değişiminde incident sürekliliği; STALE kapanışı için açık "izlenemiyor" durumu; kenar kapısının kapsam
  etkisinin raporlanması (ANALIZ'de kalan kişi oranı metriği).

## 11. Takip ve zamansal analiz (sentetik — üretim karar zinciri, ideal tespit)

| Senaryo | GT | Tahmin | Bulgu |
|---|---|---|---|
| Kadrajdan çıkıp 12 s sonra yeni ID ile dönme | 1 | 2 | **Duplicate alarm**; ilk incident `end` olmadan `stale` ile kapanır (t=19.9) |
| 3 s kaybolup yeni ID ile dönme (track_buffer 30 kare ≈ 2.4 s) | 1 | 2 | Duplicate; dönüşte eski incident 5 s daha açık kalır → aynı kişi için **eşzamanlı 2 açık incident** |
| İki kişi kesişir, ID'ler yer değiştirir | 1 | 2 | Gerçek ihlal sürerken **yanlış `end`** (t=16.6), ardından yeni `start` (t=18.2) |
| İhlalli kişi kadrajdan çıkar, dönmez | 1 | 1 | `end` üretilmez; incident 10 s sonra `stale` (beklenen, FAZ 2 tasarımı) |
| Uzak (50 px), düşük güven (0.35), kare kenarı | 1 | 0 | **Hiç alarm yok** (tasarım gereği FN) |
| Uyumlu kişide baret kesintisiz görünmez (eğilme/arkasını dönme/örtülme) | 0 | 1 eğer ≥ **3.5 s** | 1–3 s: alarm yok; ≥3.5 s: yanlış alarm |

Tespit kararsızlığına duyarlılık (uyumlu kişi, 30 dk benzetim/değer):

| Kare başına baret tespit olasılığı | Bağımsız kaçırma: yanlış/kişi-saat (yanlış ihlal süresi) | ~1 s'lik ilişkili kaçırma: yanlış/kişi-saat (süre) |
|---|---|---|
| 0.95 | 0 (%0) | **14** (%0.9) |
| 0.90 | 0 (%0) | 20 (%1.3) |
| 0.80 | 0 (%0) | 40 (%3.2) |
| 0.60 | 0 (%0) | 120 (%11) |
| 0.40 | 4 (%0.7) | 192 (%34) |
| 0.30 | 70 (%51) | 188 (%50) |

Yorum: pencere/histerezis bağımsız kaçırmaları iyi bastırır; **gerçek hatalar zamana bağlı ve ilişkili olduğundan**
saha yanlış alarm yükü yalnız etiketli videoyla ölçülebilir. Bu tablo bir hedef değil, duyarlılık göstergesidir.

Etiketsiz gerçek videolar (ByteTrack): V1 6 ID (≈2–3 kişi; fragmentasyon adayı), V2 3 ID (1 kısa), V3 4 ID
(1 kısa, 1 makine FP). ID switch ve fragmentasyon **GT olmadan ölçülemez** — araç hazır.

## 12. GPU/CPU performansı (RTX 3050 Laptop, tek akış, çevrimdışı hızlı işleme, ısınma hariç)

| Video | Stride | İşlenen kare | Video süresi | İşlem süresi | Etkin FPS | Gerekli FPS | Gerçek zaman katsayısı |
|---|---|---|---|---|---|---|---|
| V1 1080p | 2 | 186 | 14.9 s | 5.0 s | 37.5 | 12.5 | 3.0× |
| V2 1080p | 2 | 288 | 24.0 s | 7.3 s | 39.4 | 12.0 | 3.3× |
| V3 UHD dikey | 2 | 157 | 12.6 s | 7.3 s | 21.5 | 12.5 | 1.7× |
| V1 / V2 / V3 | 1 | 373 / 577 / 315 | — | — | 37.1 / 31.1 / 19.4 | 25 / 24 / 25 | 1.5× / 1.3× / **0.78×** |

Aşama süreleri p50 (p95), ms — 1080p | UHD:

| Decode | YOLO pre | YOLO inference | YOLO post | ByteTrack | match_items | PPEMonitor | Kare toplam | Alarm üreten kare (snapshot dahil) | İlk kare |
|---|---|---|---|---|---|---|---|---|---|
| 3.5–3.6 \| 16.5 | 1.5–1.6 \| 3.1 | 13.3–13.8 (22.5–25.6) \| 16.4 (26.0) | 1.6 \| 1.8 | 0.9 \| 1.3 | 0.03 | 0.02–0.05 | 23.4–23.7 (33.9–37.9) \| 43.2 (55.6) | 34.5 \| 44.8 | 190–290 |

Kaynak: torch tepe GPU belleği 64–95 MB (rezerve 104–134 MB), cihaz toplam kullanım 322–352 MB (CUDA bağlamı
dahil); süreç CPU %143–230 (tek çekirdek = %100), RSS 1.45–1.83 GB.
Sonuç: tek 1080p akış üretim stride'ıyla gerçek zamanın ~3 katı hızda işleniyor; UHD + stride 1 gerçek zamanı
yakalayamıyor. Bu **çevrimdışı** ölçümdür; canlı RTSP gecikmesi, çoklu kamera ve uzun süreli yük FAZ 9'dadır.

## 13. Eşik analizi (yalnız val; üretim eşikleri değiştirilmedi)

| conf | helmet P / R | vest P / R | Person P / R | no_helmet P / R |
|---|---|---|---|---|
| 0.10 | 0.73 / 0.84 | 0.72 / 0.82 | 0.79 / 0.92 | 0.20 / 0.36 |
| 0.15 | 0.80 / 0.84 | 0.76 / 0.82 | 0.84 / 0.91 | 0.25 / 0.33 |
| **0.25 (üretim)** | 0.83 / 0.83 | 0.83 / 0.80 | 0.88 / 0.90 | 0.40 / 0.31 |
| 0.40 (= PERSON_CONF_MIN) | 0.85 / 0.82 | 0.85 / 0.79 | 0.93 / 0.88 | 0.50 / 0.18 |
| 0.50 | 0.89 / 0.81 | 0.89 / 0.77 | 0.94 / 0.87 | 0.50 / 0.13 |

Gözlem: 0.25 altında baret/yelek recall'ü neredeyse artmıyor, precision düşüyor → CONF düşürmenin bu veride
faydası görünmüyor. Baret FP'si ihlali gizler (FN), baret FN'si yanlış alarm üretir — tercih İSG'nin FN/yanlış alarm
toleransına bağlıdır. PPEMonitor eşikleri (ENTER/EXIT/WINDOW) gerçek veride **ölçülmedi**; yalnız sentetik
duyarlılık (§11). Val kümesi checkpoint seçiminde kullanıldığından bu tarama da iyimserdir. Hiçbir eşik saha
güvenlik garantisi olarak sunulmaz.

## 14. Eksik veya güvenilmez veriler

1. Etiketli şantiye videosu yok → PPE ihlal, incident, kamera-saat, gecikme (gerçek), ID switch: **BLOCKED**.
2. Construction-PPE = eğitim kümesi; val seçimde kullanıldı; test'te sahne düzeyi sızıntı (%15–25 tahmini); kaynak
   meta verisi yok; görüntüler çoğunlukla yakın plan stok/sahnelenmiş → saha CCTV'sini temsil etmez.
3. Kişi-ekipman ilişki GT'si yok (vekil, geometrik türetme); `no_vest` sınıfı yok → yelek eksikliği ölçülemez.
4. Test videoları 51.5 s, gündüz, ihlal içermiyor, lisansı doğrulanmamış.
5. Kişi-kare güven aralıkları kümelenmeyi yok sayar; küme bootstrap'i eklenmedi.
6. Koşul matrisinin (gece, yağmur, ters ışık, kalabalık, uzak, bulanıklık, açı) hiçbiri gerçek veriyle ölçülmedi.

Ek bulgu — **Ultralytics analitiği**: kullanıcı ayarlarında `sync: true`. Üretim dedektörü çevrim içiyken
Ultralytics'e anonim kullanım olayları (görüntü değil; sürüm, cihaz, model adı) gönderebilir. Değerlendirme
süreçleri `YOLO_OFFLINE=1` ile kapatıldı; **üretim değiştirilmedi** — karar önerisi: `yolo settings sync=False` veya
`detector/.env` içine `YOLO_OFFLINE=1` (KVKK/veri akışı envanterine eklenmeli). Ayrıca `model.val` grafikleri için
`Arial.ttf` yazı tipi ultralytics.com'dan **indirildi** (yalnız indirme).

## 15. Risk bazlı GO / NO-GO

| Karar | Durum | Gerekçe |
|---|---|---|
| Değerlendirme altyapısı ile etiketli veri toplamaya ve ölçmeye başlamak | **GO** | Araçlar, şema, testler ve gerçek modelle uçtan uca çalıştırma PASS |
| Sistemi saha güvenliği için güvenilir kabul etmek | **NO-GO** | Saha doğruluğu kanıtlanmadı (BLOCKED); kabul eşikleri yok |
| Gölge mod pilot (gözlem + insan doğrulama) | **NO-GO (şimdilik)** | KVKK ve lisans NO-GO sürüyor; etiketli saha ölçümü ve İSG onaylı eşikler yok |
| Otomatik yaptırım / tek başına güvenlik kararı | **NO-GO** | Kapsam dışı (tüm fazlar) |
| `no_helmet` sınıfını karara eklemek | **NO-GO** | Recall 0.23 (dağılım içi bile) |
| Üretim eşiklerini değiştirmek | **Karar yok** | Saha validation verisi olmadan önerilmez |

Yüksek riskler: uzak/kenardaki kişilerin hiç değerlendirilmemesi (sessiz FN); örtülme ≥3.5 s ve makine/benzer
nesnelerde yanlış alarm; ID değişiminde duplicate ve yanlış `end`; eğitim verisinin saha alanından uzak olması.

**Karar gerektiren konu:** saha kullanım kabul eşikleri **önceden belirlenmemiştir**. Protokol §8'deki tablo İSG
uzmanı ve saha sorumlusu tarafından yazılı olarak doldurulmadan hiçbir sonuç PASS/FAIL olarak yorumlanamaz.

## 16. Komutlar

```
cd detector
.venv\Scripts\python -m evaluation env
.venv\Scripts\python -m evaluation audit --root evaluation/datasets/construction-ppe --freeze evaluation/datasets/construction-ppe.manifest.json
.venv\Scripts\python -m evaluation detect --root evaluation/datasets/construction-ppe --split val --manifest evaluation/manifests/construction-ppe.manifest.json
.venv\Scripts\python -m evaluation detect --root evaluation/datasets/construction-ppe --split test --manifest evaluation/manifests/construction-ppe.manifest.json
.venv\Scripts\python -m evaluation video videos/16533594_1920_1080_25fps.mp4 videos/5434223-hd_1920_1080_24fps.mp4 videos/8965526-uhd_2160_3840_25fps.mp4 --check-equivalence 60
.venv\Scripts\python -m evaluation video <aynı videolar> --stride 1
.venv\Scripts\python -m evaluation temporal --minutes 30
```
Veri kümesi indirme: `construction-ppe.zip` → `detector/evaluation/datasets/construction-ppe/` (Git dışı).

## 17. Git commit

Bu raporla oluşturulan commit: `git log -1` (FAZ 8). Plan belgesi ve yerel veri/çıktılar commit'e eklenmedi.

## 18. FAZ 9 ön koşulları

1. İzinli saha verisi: veri sorumlusu onayı + çalışan aydınlatması; protokole göre ≥ 2 annotator ile etiketlenmiş,
   kaynak düzeyinde bölünmüş ve dondurulmuş video GT (gerçek ihlal örnekleri ve §5 koşul matrisi dahil).
2. İSG uzmanı + saha sorumlusunun yazılı kabul eşikleri (protokol §8).
3. Etiketli video üzerinde `python -m evaluation video --gt` ile incident recall, kamera-saat yanlış alarm,
   gecikme ve ID switch ölçümü — FAZ 9 yük testleriyle aynı konfigürasyonda.
4. Ultralytics analitiği kararı (`sync`/`YOLO_OFFLINE`) ve veri akışı envanteri güncellemesi.
5. FAZ 7'den devreden: öksüz run uzlaştırma onayı, FastAPI/Starlette yükseltmesi, klasör ACL ve disk şifrelemesi.
6. FAZ 9'da teslim gecikmesi (outbox → DB) ve canlı RTSP uçtan uca uyarı gecikmesi ölçülmeli; çoklu kamera GPU
   kapasitesi (tek akış 1080p ≈ 37–39 FPS çevrimdışı) doğrulanmalı.
