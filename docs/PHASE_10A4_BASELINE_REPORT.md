# FAZ 10-A.4 raporu — Tekrarlanabilir video taban çizgisi (Windows CUDA)

Tarih: 2026-11-02 (UTC). Başlangıç: `e1f1e48` (FAZ 10-A.1). Bu fazda `best.pt`, sınıf haritası, `ppe_core.py`,
alarm/karar eşikleri, dashboard kodu, ana PostgreSQL, snapshot'lar ve üretim outbox'ı **değiştirilmedi**. FAZ 10-A.1'in
açık işleri (ana dashboard güncellemesi, 24 saatlik soak, ~2,9 MB/sa anon kalıntısı) bu fazda başlatılmadı.

**İki ayrı durum:**

| Konu | Durum |
|---|---|
| Değerlendirme altyapısı (araç, manifest, iz, teslim karşılaştırması, rapor, karşılaştırma, testler) | **TAMAMLANDI** |
| Mac referans testinin (Apple MPS, `20261102T124324Z_capture_accuracy`) yeniden üretimi / doğrulanması | **BLOCKED** — ham kanıt klasörü bu makinede yok; o testin video ve model SHA-256'ları kayıtlı değil |
| Windows CUDA yeni taban çizgisi (V1–V3) | **YAPILDI** — kendi manifesti ve kanıt klasörüyle; doğruluk metrikleri INSUFFICIENT_GROUND_TRUTH |

## 1. Windows ortamı (doğrulandı)

| Kalem | Değer |
|---|---|
| Proje dizini | `C:\Users\PC\Desktop\ppe-split-main` (dal `main`; `e1f1e48` mevcut, HEAD) |
| Python / PyTorch / CUDA / cuDNN | 3.12.6 / 2.14.1+cu126 / 12.6 / 9.10.02 |
| GPU / sürücü | NVIDIA GeForce RTX 3050 Laptop GPU (4 GB) / 566.07 |
| Ultralytics / OpenCV / NumPy / Pillow | 8.4.183 / 4.10.0 / 2.5.4 / 13.0.0 |
| `detector/models/best.pt` SHA-256 | `6886ad2fc301cf992152d4b047307071cac940082ab071175c0ef498fe67ba43` (FAZ 8–10 ile aynı) |
| Docker | Ana yığın `ppe-split` (dashboard + db) çalışıyor; `ppe-rel` durdurulmuş; başka projeler: Flowise, llminsight (dokunulmadı) |
| Devam eden test süreci | yok (makine 17:13:59Z'de yeniden başlamıştı; FAZ 10-A.1 `fixall90` 66. dk'da kesilmişti) |

## 2. Eksik kanıt araştırması

- `tools/reliability/output/20261102T124324Z_capture_accuracy/`: **yok**. Depoda "SteelBench" ya da FAZ 10-A.2/10-A.3
  commit'i / dosyası yok (`git log --all`, dosya araması).
- Stok videolar **mevcut** (`detector/videos/`). SHA-256 (bu makinede ilk kez kaydedildi):

| Anahtar | Dosya | SHA-256 | Kare / fps / çözünürlük |
|---|---|---|---|
| V1 | `16533594_1920_1080_25fps.mp4` | `6dd2ad33bf1fde977ad1c13ad2900739e81e5d213f1d1e2f89b9f9854066fd64` | 373 / 25 / 1920×1080 |
| V2 | `5434223-hd_1920_1080_24fps.mp4` | `df2f645caad8d90eee93ad1c5b012de4394981e5eeabd92e37bfc16101ea2fa4` | 577 / 23.98 / 1920×1080 |
| V3 | `8965526-uhd_2160_3840_25fps.mp4` | `b9183548a9ec8bfddd2298bea144476aa14c29dc7e67c7ce009a2821d0567ef5` | 315 / 25 / 2160×3840 |

- **Hash eşleşmesi doğrulanamadı:** önceki hiçbir rapor/manifest bu videoların SHA-256'sını içermiyor; Mac testinin
  hash'leri de yok. Yalnız dosya adı + kare sayısı + çözünürlük FAZ 8 kaydıyla (`PHASE_8_REPORT.md` §V1–V3) uyumlu.
- **Video lisansı:** dosya adları Pexels kalıbında; kaynak/lisans FAZ 8'den beri **doğrulanmadı**. Önceki fazlarla
  aynı kapsamda kullanıldı: yalnız yerel teknik ölçüm; Git'e eklenmez, dağıtılmaz, eğitimde kullanılmaz.

## 3. SteelBench

Verilen kaynak `https://huggingface.co/datasets/ThinkingHub/PP` incelendi (veri indirilmedi): sayfada "SteelBench"
adı geçmiyor (aynı veri seti olduğu doğrulanamadı); lisans etiketi **cc-by-nc-4.0** (ticari olmayan kullanım);
ek kullanım, eğitim, yeniden dağıtım, mahremiyet/rıza veya erişim koşulu metni sayfada yok. İçerik: 16 endüstriyel
tesis / 52 kameradan ~3.3 bin gözetim klibi + kişi sayısı/kutu istatistikleri, görsel etiket ve `severity_level`;
**PPE sınıfı (baret/yelek) ya da ground-truth şeması yok**. Sonuç: ticari ürün geliştirme ve model eğitimi için izin
doğrulanmadı; PPE doğruluk ölçümüne doğrudan uygun değil; görüntülerde kişiler var (KVKK). **Kapsama alınmadı.**
Kullanılacaksa önce veri sahibinden yazılı izin + hukuk incelemesi + PPE etiketlemesi gerekir.

## 4. Değerlendirme aracı

Tek komut (proje kökünde, PowerShell):

```powershell
detector\.venv\Scripts\python.exe tools\reliability\baseline_eval.py run                 # V1-V3, VID_STRIDE=2, çevrimdışı iz + uçtan uca
detector\.venv\Scripts\python.exe tools\reliability\baseline_eval.py run --no-e2e        # yalnız iz (Docker gerekmez)
detector\.venv\Scripts\python.exe tools\reliability\baseline_eval.py run --review review.json --previous <önceki>\result.json --frame-every 24
detector\.venv\Scripts\python.exe tools\reliability\baseline_eval.py compare <A>\result.json <B>\result.json
```

| Bileşen | Yeniden kullanılan | Yeni |
|---|---|---|
| Karar zinciri izi (YOLO → ByteTrack → `match_items` → PPEMonitor → `enrich_event`/IncidentTracker) | `detector/evaluation/pipeline.run_video` (FAZ 8 eşdeğerlik testli) | kare izi JSONL; kapı (ANALIZ/değerlendirilemiyor) nedenleri `telemetry.gate_reasons` ile |
| Manifest | `evaluation/cli.environment` (model SHA-256, sınıf haritası, sürümler, tracker YAML, karar parametreleri) | video SHA-256 + kapsayıcı meta, `known_errors.json` özeti, VID_STRIDE |
| Uçtan uca | `common` (izole yığın, gerçek `detector.py`, psql) | ayrı compose projesi `ppe-eval` (127.0.0.1:8200, ayrı volume ve parolalar), `LOOP=0`, outbox defteri ↔ DB |
| Analiz | `metrics.incidents_from_events` | `evaluation/baseline.py`: takip özeti, kapsam, incident yaşam döngüsü, bilinen hata sinyalleri, insan inceleme etiketleri, teslim karşılaştırması, çalıştırma karşılaştırması, Markdown |

Çıktı `tools/reliability/output/<UTC>_baseline_<etiket>/` (Git dışı): `result.json`, `REPORT.md`,
`trace_V*.jsonl` (kare başına kişi kutusu, güven, takip kimliği, eşleşen baret/yelek kutusu, durum, kapı nedeni),
`events_V*.jsonl`, `review/` (alarm başlangıç kareleri + isteğe bağlı her N. kare, çizimli), `e2e/` (dedektör log'u +
outbox). İnsan inceleme kaydı (`review.json`) alarmları `false_alarm | true_violation | unclear` olarak etiketler;
**etiketsiz alarm UNREVIEWED kalır, asla doğru tespit sayılmaz.**

**Ground-truth kapısı:** `ppe-eval-video/1` GT yoksa precision, recall, yanlış alarm oranı
`INSUFFICIENT_GROUND_TRUTH`, gerçek kapsam `NOT_MEASURABLE`; gözlenen sayılar (alarm, kapıdan geçen kişi-kare oranı)
"doğruluk değildir" notuyla verilir. GT eklendiğinde mevcut `evaluate_against_gt` bağlanmalıdır (bkz. §9).

**Bilinen hatalar** (`tools/reliability/baseline/known_errors.json`; kaynak: kullanıcı bildirimi, doğrulanmamış;
numaralandırma bu dosyada verildi): E-01 V2 örtülen koyu baret → baretsiz alarm; E-02 V3 kırmızı iş makinesi kolu →
kişi; E-03 V2 kenardaki çalışan değerlendirilemiyor; E-04 V1 2 çalışan / 6 kimlik. Çözüm durumu yalnız
**sinyal yok + bağımsız insan onayı + regresyon testi** birlikteyse `RESOLVED_CONFIRMED`.

**İzolasyon doğrulaması** (her koşuda, önce/sonra): ana DB sayımları (olay, incident, run, kişi raporu, son denetim
kaydı), ana snapshot dosya listesi, üretim outbox'ı, `ppe-split`/`ppe-rel` ve diğer container durumları, ayrı proje
adı ve port.

## 5. Windows CUDA taban çizgisi sonuçları

Resmi koşu: `20261102T174149Z_baseline_cuda` (git `e1f1e48` + bu fazın değişiklikleri; dashboard imajı
`4c972e0a` = FAZ 10-A.1 kodu). Ayarlar üretimle aynı: device 0, IMGSZ 640, CONF 0.25, `bytetrack.yaml`,
**VID_STRIDE 2**; karar parametreleri değişmedi.

| Video | İşlenen kare | Olay start/ongoing/end | Alarm | Takip kimliği / en çok eşzamanlı | Kapıdan geçen kişi-kare | Değerlendirilemeyen |
|---|---|---|---|---|---|---|
| V1 | 186 | 0/0/0 | 0 | 6 / 3 | 224/293 (%76.4) | kenar 67, düşük güven 2 |
| V2 | 288 | 1/2/1 | 1 | 3 / 2 | 48/554 (%8.7) | kenar 504, düşük güven 6 |
| V3 | 157 | 1/0/0 | 1 | 4 / 3 | 328/349 (%94.0) | düşük güven 21 |

| Alarm | Başlangıç → bitiş | Eksik | Başlangıçta | Bilinen hata | İnceleme |
|---|---|---|---|---|---|
| V2 track 3 | 4.7 → 14.8 sn (`end`) | baret | güven 0.77, baret kutusu eşleşmedi, yelek eşleşti | E-01 | **UNREVIEWED** |
| V3 track 160 | 9.4 sn → video sonu (`run_end`) | baret | güven 0.42 (kapı eşiği 0.40), kısa iz (4.1 sn), `low_conf` ağırlıklı | E-02 | **UNREVIEWED** |

- Bilinen hata sinyalleri: **E-01, E-02, E-03, E-04 hepsi PRESENT** (çözüm durumu OPEN). E-03: V2 track 1 23.9 sn
  boyunca `edge` nedeniyle hiç karar almadı; E-04: V1 6 kimlik / en çok 3 eşzamanlı kişi (oran 2.0; gerçek kişi
  sayısı GT'siz bilinmez).
- **Doğruluk metrikleri: INSUFFICIENT_GROUND_TRUTH.** Alarm 2 — incelenmemiş 2, doğru ihlal 0, yanlış alarm 0
  (insan incelemesi yapılmadı). Not: inceleme görüntüleri (`review/`) aracın doğru çizdiğini kontrol etmek için
  açıldı; V3 alarm kutusunun arka plandaki kırmızı makine kolunda, V2 alarmındaki kişinin koyu bir bareti fırçayla
  kısmen örtülü göründüğü bir yapay zekâ gözlemidir — **insan incelemesi yerine geçmez**, etiketlere işlenmedi.
- **Uçtan uca teslim PASS:** dedektör 5 olay (2 start / 2 ongoing / 1 end) = izole DB 5 satır, 5 tekil; eksik 0,
  fazla 0, mükerrer 0; outbox 8 mesaj `delivered` (5 olay + 3 rapor); 3 run `finished/eof`, kişi satırı 6/2/3;
  incident 2 (V2, V3); snapshot 2 satır, eksik dosya 0; dedektör log hatası 0; dashboard traceback 0.
- **Çevrimdışı iz ↔ gerçek zamanlı uçtan uca:** olay sayıları birebir (V1 0/0, V2 4/4, V3 1/1).
- **Tekrarlanabilirlik:** iki ardışık koşu (`173807Z`, `174149Z`) — `compare`: girdiler karşılaştırılabilir, ortam ve
  ayar farkı yok, video ve bilinen hata farkı yok.
- **İzolasyon PASS** (yedi kontrolün hepsi).

## 6. Mac (Apple MPS) geçmiş referansıyla karşılaştırma — yalnız tarihsel

`tools/reliability/baseline/reference_mac_20261102.json` (`HISTORICAL_UNVERIFIED`). Araç bunu
`comparable_inputs: False` olarak işaretler (video/model hash'i yok, cihaz/yazılım sürümleri bilinmiyor).

| Bildirilen (Mac) | Windows CUDA (bu faz) | Yorum |
|---|---|---|
| 5 olay, 5'i DB'de; kayıp/çift/fazla 0 | 5 olay, 5'i izole DB'de; 0/0/0 | sayı aynı; olay kimlikleri/zamanları karşılaştırılamaz |
| 2 ihlal alarmı, ikisi insan incelemesinde yanlış | 2 alarm (V2, V3), **incelenmemiş** | Windows alarmları için insan incelemesi bekleniyor |
| V2 örtülen koyu baret, V3 makine kolu, V2 kenar, V1 6 kimlik | dördünün sinyali de PRESENT | örüntü tutarlı; aynı videolar olduğu hash ile doğrulanamadı |

MPS ↔ CUDA farkları kaydedilemedi: Mac ortamının sürüm, cihaz ve ham iz bilgisi yok. Aynı videolarla iki platformda
koşu yapılırsa `compare` kare/olay düzeyinde farkları gösterir (önerilen: Mac'te aynı aracı `--no-e2e` ile çalıştırıp
`result.json`'u buraya getirmek).

## 7. Testler

| Kalem | Sonuç |
|---|---|
| Yeni birim testleri `detector/tests/test_evaluation_baseline.py` (GT kapısı, inceleme etiketleri, bilinen hata + çözüm kapısı, takip parçalanması, kapı nedenleri, yuvarlanmış olay zamanı eşleşmesi, teslim eksik/fazla/mükerrer, karşılaştırma, Markdown, manifestte kullanıcı yolu yok) | **12/12 PASS** |
| Dedektör tam paket | **257/257 PASS** (245 + 12) |
| Dashboard (gerçek PostgreSQL 17; bu fazda dashboard kodu değişmedi) | **338/338 PASS** |
| Uçtan uca + izolasyon (gerçek GPU) | PASS (iki koşu) |

**Bu fazda bulunup düzeltilen araç hataları** (kanıt klasörleri silinmedi):
1. `20261102T173539Z_baseline_cuda` — uçtan uca **FAIL**: `production_settings()` dedektör modülünü içe aktarırken
   `detector/.env`'deki üretim `INGEST_TOKEN`'ı süreç ortamına giriyor; Docker Compose'da kabuk ortamı `--env-file`'ı
   ezdiğinden izole `ppe-eval` dashboard'u üretim token'ıyla kuruldu → dedektör 401, 5 olay outbox'ta korundu (kayıp
   yok). Düzeltme `common.compose`: yığın env dosyasındaki anahtarlar alt süreç ortamından çıkarılır (ppe-rel için de
   geçerli). Etkilenen container, düzeltilmiş koşuda yeniden oluşturuldu (token özeti eşleşmesi doğrulandı); yerel ve
   izoleydi, dışarı açık değildi.
2. Olay zamanı yuvarlandığından (4.7 sn ↔ kare 4.667 sn) incident başlangıç karesi ve inceleme görüntüsü
   bulunamıyordu → en yakın kare eşleşmesi + test.
3. `--previous` göreli yolu dizin değişiminden sonra çözülüyordu → mutlak yol.

## 8. Değişen dosyalar

`detector/evaluation/baseline.py` (yeni), `detector/tests/test_evaluation_baseline.py` (yeni),
`tools/reliability/baseline_eval.py` (yeni), `tools/reliability/baseline/known_errors.json` (yeni),
`tools/reliability/baseline/reference_mac_20261102.json` (yeni), `tools/reliability/common.py` (`REL_PROJECT`,
`REL_PORT`; compose ortam sızıntısı düzeltmesi), `docs/RELIABILITY_TEST_PLAN.md` §10, bu rapor. Videolar, ham iz,
görüntüler, yığın parolaları (`output/eval_stack/rel.env`) Git dışı.

## 9. Eksik kanıtlar ve tamamlanamayan doğrulamalar

1. Mac ham kanıt klasörü `20261102T124324Z_capture_accuracy/` — **yok** (BLOCKED).
2. Mac testinin video/model SHA-256, sürüm ve cihaz bilgisi — **yok**; aynı girdi olduğu doğrulanamaz.
3. Windows alarmlarının **bağımsız insan incelemesi** — yapılmadı (`review.json` şablonu §4).
4. Ground-truth (`ppe-eval-video/1`) — yok; precision/recall/yanlış alarm oranı/gerçek kapsam ölçülemez.
5. Stok video lisansı ve SteelBench kullanım izni — doğrulanmadı.
6. MPS ↔ CUDA kare düzeyi farkı — Mac'te aynı araçla koşu yapılmadan ölçülemez.

## 10. Sonraki model iyileştirme önerileri (bu fazda uygulanmadı; önce ölçüm + insan onayı)

1. **E-02 (nesne → kişi):** yanlış kişi kutusu 0.42 güvenle kapıdan (0.40) geçiyor; alarm için kişi güveninin
   kalıcılığı (örn. iz boyunca medyan güven) veya kısa/düşük güvenli izlerde alarm bastırma — yanlış negatif riskiyle
   birlikte GT üzerinde değerlendirilmeli.
2. **E-01 (örtülme):** baret kutusu yokluğunun "baretsiz" sayılması örtülmede yanlış alarm üretiyor; "baş görünmüyor
   / belirsiz" durumu (FAZ 8 `unknown/occluded` etiketi) ve `no_helmet` sınıfının (şu an yalnız sayılıyor) kanıt olarak
   kullanımı A/B ile ölçülmeli.
3. **E-03 (kadraj kenarı):** 6 px kenar kapısı yakın çekimde kişilerin %91'ini değerlendirme dışı bırakıyor; kenar
   kuralının yalnız kesilen tarafa (ör. alt kenar) uygulanması veya kamera yerleşimi rehberi.
4. **E-04 (kimlik parçalanması):** ByteTrack `track_buffer`/`match_thresh` ayarı veya ReID'li izleyici; incident
   sayısını şişirme riski FAZ 8'den biliniyor.
5. Her değişiklik bu araçla `--previous` karşılaştırmalı koşulmalı ve izinli, etiketli saha verisinde GT ile
   doğrulanmalı; İSG kabul eşikleri onaylanmadan üretime alınmamalı.
