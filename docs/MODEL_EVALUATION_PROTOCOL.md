# Model ve PPE Sistem Değerlendirme Protokolü (FAZ 8)

Sürüm 1 — 2026-11-01. Araçlar: `detector/evaluation/` (`python -m evaluation ...`). Rapor: `docs/PHASE_8_REPORT.md`.

Bu protokol, mevcut `best.pt` + ByteTrack + `PPEMonitor` + `IncidentTracker` zincirinin **saha doğruluğunun** nasıl
ölçüleceğini tanımlar. Model eğitimi, eşik değişikliği veya otomatik yaptırım kapsam dışıdır. Saha doğruluğunun
kanıtı, bu protokole göre toplanmış **izinli, insan etiketli, donmuş** bir test kümesi ve İSG uzmanının yazılı olarak
onayladığı kabul eşikleri olmadan verilemez.

---

## 1. İlkeler

1. **Ground truth yalnız insandan gelir.** Model tahmini, otomatik ön-etiket veya PPEMonitor çıktısı GT kabul edilmez.
   Ön-etiket kullanılırsa her kutu ve durum insan tarafından tek tek onaylanmalı ve `review` alanı doldurulmalıdır.
2. **Görünmeme ihlal değildir.** Kafa görünmüyorsa `helmet = unknown`; gövde görünmüyorsa `vest = unknown`. `unknown`
   kişi-kare metriklerinde değerlendirme dışıdır; sistemin bu karelerde verdiği alarmlar ayrıca sayılır.
3. **Üç düzey asla birleştirilmez:** nesne (kutu), kişi-kare ve incident. Kişi-kare örnekleri bağımsız değildir
   (aynı kişinin ardışık kareleri); etkin örneklem kişi ve incident sayısıdır.
4. **Test kümesi bir kez kullanılır.** Eşik/konfigürasyon seçimi yalnız validation kümesinde yapılır. Test kümesi
   değerlendirme öncesinde manifest ile dondurulur; CLI test split'inde eşik taramasını reddeder.
5. **Veri yoksa metrik yok.** Bir koşul için veri yoksa sonuç `NOT EVALUATED`; etiketli veri yoksa nicel doğrulama
   `BLOCKED`. Sentetik senaryolar model doğruluğu yerine geçmez.
6. **Kişisel veri yerel kalır.** Görüntü, video, etiket ve değerlendirme çıktıları Git'e girmez
   (`detector/evaluation/datasets/`, `detector/evaluation/output/` .gitignore'da), dış servise yüklenmez.
   Değerlendirme süreci Ultralytics analitiğini `YOLO_OFFLINE=1` ile kapatır.

## 2. Yetki ve veri koruma ön koşulları (veri toplamadan önce)

| Kontrol | Sorumlu | Kanıt |
|---|---|---|
| İşveren / veri sorumlusu yazılı onayı; amaç = model doğrulama | Veri sorumlusu | onay referansı → `video.consent_ref` |
| Çalışanlara kamera ve değerlendirme amaçlı kullanım aydınlatması (KVKK) | İK / hukuk | aydınlatma metni sürümü |
| Erişim listesi (annotator'lar dahil), saklama süresi, imha tarihi | Veri sorumlusu | erişim listesi |
| Yüz tanıma / kimliklendirme yok; `track_id` anonim ve videoya özgü | Teknik ekip | şema (`p1`, `p2` …) |
| Kayıtlar şifreli diskte (BitLocker vb.) | BT | ekran görüntüsü/rapor |
| Lisans: kamera görüntüsü hakları; model AGPL-3.0 (bkz. `LICENSE_REVIEW.md`) | Hukuk | karar kaydı |

KVKK ve lisans **NO-GO** kararları (FAZ 6) bu protokolle değişmez.

## 3. Ground-truth (annotation) şeması — `ppe-eval-video/1`

Bir video = bir JSON dosyası. Doğrulama: `python -m evaluation validate-gt dosya.json`. Örnek:
`python -m evaluation example-gt`.

| Alan | Zorunlu | Açıklama |
|---|---|---|
| `video.video_id`, `camera_id`, `site_id`, `recorded_day` | evet (site/gün uyarı) | Sızıntı denetimi bu alanlarla yapılır |
| `video.fps`, `frame_count`, `width`, `height` | evet | Kaynak videonun gerçek değerleri |
| `video.split` | evet | `train` / `val` / `test` (kaynak düzeyinde atanır, §4) |
| `video.conditions` | önerilir | §5 koşul etiketleri (video veya bölüm düzeyi) |
| `video.license`, `consent_ref` | önerilir | İzin izlenebilirliği |
| `annotators` | evet | Anonim annotator kimlikleri |
| `frames[].frame` | evet | **0 tabanlı orijinal kare indeksi**; `t = frame / fps` |
| `persons[].track_id` | evet | Video içinde aynı gerçek kişi = aynı kimlik (kadrajdan çıkıp dönse bile) |
| `persons[].box` | evet | `[x1, y1, x2, y2]` piksel, tüm görünür beden |
| `persons[].visibility` | evet | `full`, `partial`, `heavy_occlusion`, `head_hidden`, `torso_hidden`, `head_torso_hidden` |
| `persons[].helmet`, `vest` | evet | `present` / `missing` / `unknown` |
| `persons[].helmet_box`, `vest_box` | `present` ise önerilir | Kişi-ekipman eşleme doğruluğu için |
| `persons[].review` | hayır | `ok` / `disputed` / `adjudicated` |
| `incidents[]` | evet (boş olabilir) | `incident_id`, `track_id`, `item` (`helmet`/`vest`), `start`, `end` (sn; `null` = video sonuna kadar), `certainty` |

**Durum kuralları**

- `missing`: ilgili bölge açıkça görünüyor ve ekipman yok. Elde taşınan baret, boyuna asılı yelek = `missing`.
- `present`: ekipman takılı/giyili. Başka renkli/alışılmadık baret de `present`'tir.
- `unknown`: bölge görünmüyor, çok küçük (<~12 px baş), aşırı bulanık veya annotator emin değil.
  `head_hidden` ile `helmet=missing` birlikte **geçersizdir** (doğrulayıcı reddeder).
- Kare örnekleme: üretim `VID_STRIDE=2` ile 0 tabanlı **tek** indeksli kareleri işler (`idx·stride − 1`). En az her
  0.4–1 sn'de bir kare etiketleyin ve indeksleri `frame ≡ stride−1 (mod stride)` seçin; aksi halde kare işlenmez ve
  `gt_frames_not_processed` olarak raporlanır.

**Incident tanımı (zamansal GT)**

- Başlangıç: kişinin ekipmanının eksik olduğunun **ilk açıkça görüldüğü** an (kişi kadraja eksik girerse ilk görünür an).
- Bitiş: ekipmanın yeniden takılıp **açıkça görüldüğü** an. Kişi eksik halde kadrajdan çıkar ve dönmezse bitiş =
  çıkış anı. Eksik halde çıkıp aynı eksiklikle geri dönerse **aynı GT incident sürer**; sistemin dönüşte verdiği
  ikinci alarm `duplicate` sayılır (yanlış alarm değil).
- Baret ve yelek eksikliği ayrı incident'lardır (aynı aralıkta olabilir).
- <2 sn'lik anlık durumlar (`certainty=uncertain`) raporda ayrı gösterilir.

**İnceleme ve ikinci göz**

1. Her video iki bağımsız annotator tarafından etiketlenir (en az test kümesi için zorunlu).
2. Uyum: kişi-kare durumlarında Cohen κ (helmet ve vest ayrı); incident başlangıçlarında |Δt| ≤ 1 sn eşleşme oranı.
   κ < 0.8 ise kural seti gözden geçirilir.
3. Uyuşmazlıklar üçüncü kişi (tercihen İSG uzmanı) tarafından karara bağlanır → `review=adjudicated`.
4. Doğrulayıcının uyarıları (ör. incident içinde `present` kare) kapatılmadan dosya dondurulmaz.

## 4. Bölme (split), sızıntı ve dondurma

- Bölme **kaynak düzeyinde**: şantiye > kamera > kaynak video > kayıt günü. Aynı kamera veya aynı günün kayıtları
  farklı split'lerde bulunmaz. Aynı videonun komşu kareleri asla ayrılmaz.
- Test kümesi mümkünse modelin hiç görmediği şantiye/kameralardan seçilir.
- Kontroller: `check_group_leakage` (alan eksikse durum `UNKNOWN`; kanıtlanmış "OK" sayılmaz), bayt-özdeş SHA-256,
  algısal yakın kopya (dHash Hamming ≤4 kesin şüphe, 5–8 insan incelemesi). Siyah çerçeveli/letterbox görüntüler
  dHash'te yanlış eşleşme üretebilir — örnekler gözle incelenir.
- Dondurma: `python -m evaluation audit --root <veri> --freeze <manifest.json>`; değerlendirme
  `detect --manifest <manifest.json>` ile çalıştırılır. Manifest dosya içeriği (SHA-256) değişirse değerlendirme
  çalışmaz. Video GT dosyaları için de aynı SHA-256 listesi tutulur.
- Modelin eğitim verisi bilinmiyorsa veya eğitim kaynakları saha kaynaklarıyla örtüşüyorsa sızıntı riski açıkça
  raporlanır.

## 5. Koşul matrisi

Her koşul için **gerçek örnek sayısı** (video, kişi, incident) raporlanır; eşikler İSG ile belirlenir.

| Koşul etiketi | Tanım |
|---|---|
| `daylight` / `low_light` / `night_ir` | Aydınlatma |
| `backlight` | Ters ışık / güneşe karşı |
| `rain`, `fog`, `dust` | Hava / partikül |
| `far_workers` | Kişi yüksekliği < 120 px (kişi kutusu boy kırılımı otomatik: <60, 60–120, 120–240, ≥240 px) |
| `small_helmet` | Baret kutusu < 32 px |
| `crowded` | ≥ 5 kişi veya kutular arası örtüşme |
| `partial_occlusion`, `head_hidden`, `torso_hidden` | Kişi düzeyi `visibility` ile de kırılır |
| `similar_colors` | Yelek/baret rengine benzer kıyafet, şapka, saç, arka plan |
| `motion_blur`, `camera_shake` | Hareket |
| `camera_angle_high`, `camera_angle_low` | Yukarıdan / aşağıdan bakış |
| `low_resolution` | Kaynak < 720p |
| `non_worker` | Ziyaretçi / işçi olmayan kişi |
| `stride_1` vs `stride_2` | Kare atlama karşılaştırması (yalnız çevrimdışı; `video --stride 1`) |

Minimum: bir koşul hücresi < 20 GT incident veya < 30 kişi içeriyorsa sonuç "yetersiz örneklem" olarak işaretlenir.

## 6. Metrik tanımları

### 6.1 Nesne düzeyi (YOLO, görüntü)
- Ultralytics `model.val`: P, R, mAP50, mAP50-95, sınıf AP, karışıklık matrisi, PR eğrileri (conf=0.001, NMS IoU 0.7).
- Üretim çalışma noktası (conf = üretim `CONF`, IoU ≥ 0.5, açgözlü eşleme): sınıf başına TP/FP/FN, P/R/F1/FNR,
  Wilson %95 aralığı; kutu boyu kırılımında recall; güven dağılımı; FP/FN örnek listesi.
- Sınıflar: `helmet=0`, `vest=2`, `Person=6` (üretim kararı) ve `no_helmet=7` (yalnız bağımsız ölçüm; **üretim
  kararına eklenmez**).

### 6.2 Kişi-kare düzeyi (sistem, etiketli kare)
Pozitif = ekipman eksik (ihlal). GT kişi ↔ tahmin kişi eşlemesi kişi kutusu IoU ≥ 0.5.

| GT \ Sistem | IHLAL | UYGUN | ANALIZ (karar yok) | Kişi tespit edilmedi |
|---|---|---|---|---|
| missing | TP | FN | FN (`fn_undecided`) | FN (`fn_not_detected`) |
| present | FP | TN | abstain (TN sayılmaz) | abstain |
| unknown | değerlendirme dışı (`violation_on_unknown` sayılır) | dışı | dışı | dışı |
| GT kişisi yok | FP (`fp_unmatched`) | — | — | — |

Baret ve yelek ayrı hesaplanır. Ek: kişi-ekipman eşleme doğruluğu (`present` ve GT kutusu varken sistemin eşlediği
ekipman kutusu GT kutusuyla IoU ≥ 0.3 → doğru; başka kutu → `wrong_item`; hiç → `no_item_matched`).

### 6.3 Incident düzeyi (sistem, zamansal)
- Tahmin incident = `start` olayından `end`'e (yoksa `stale`/`run_end` kapanışına) kadar; ekipmanlar start/ongoing
  `missing_*` bayraklarının birleşimi; orphan olaylar incident açmaz.
- Eşleşme: aynı video + ortak ekipman + zaman örtüşmesi (GT aralığı 1 sn önce / 2 sn sonra genişletilir) + kişi
  kimliği (tahmin `pid`'inin karelerde en çok eşleştiği GT `track_id`).
- Sayaçlar: GT incident, algılanan, kaçırılan, yanlış incident, duplicate (aynı GT için 2.+ alarm), kapanmamış
  (end yok: `stale` / `run_end`).
- Recall = algılanan / GT; precision = (tahmin − yanlış) / tahmin; FNR = 1 − recall.
- **Yanlış alarm / kamera-saat** = yanlış incident / izlenen video saati.
- **Başlangıç gecikmesi** = ilk eşleşen alarm − GT başlangıcı (p50/p95). **Bitiş hatası** = son eşleşen alarmın
  `end` zamanı − GT bitişi (yalnız gerçek `end` olayı varsa).

### 6.4 Takip
GT izi başına farklı tahmin ID sayısı (fragmentasyon), ardışık eşleşmelerde ID değişimi (ID switch), birden çok GT
kişiyi kapsayan tahmin ID (karışma), hiç tespit edilmeyen GT izleri.

### 6.5 Gecikme ve performans (ayrı ayrı; birbirinin yerine kullanılmaz)
1. **Karar gecikmesi** (video zamanı): GT ihlal başlangıcı → `start` olayı. PPEMonitor penceresinden gelir.
2. **İşlem gecikmesi** (duvar saati, kare başına): decode, YOLO (pre/infer/post), ByteTrack, match_items,
   PPEMonitor, alarm üretimi (enrich + snapshot) — p50/p95, ilk 5 ısınma karesi hariç.
3. **Teslim gecikmesi**: outbox → dashboard → DB (FAZ 9).
4. Etkin işleme FPS'i vs gereken FPS (kaynak fps / stride). Çevrimdışı dosya işleme, canlı RTSP gecikmesi değildir.

### 6.6 Belirsizlik
Oranlar için Wilson %95 aralığı. Kişi-kare aralıkları kümelenmeyi yok saydığından **yalnız göstergedir**; karar için
video/kamera düzeyinde küme bootstrap'i (FAZ 9 öncesi eklenecek) veya incident düzeyi kullanılır.
Rehber (gözlenen recall'a göre Wilson alt sınırı):

| GT incident sayısı | gözlenen 0.90 → alt sınır | gözlenen 0.95 → alt sınır | gözlenen 1.00 → alt sınır |
|---|---|---|---|
| 30 | 0.74 | 0.79 | 0.89 |
| 50 | 0.79 | 0.87 | 0.93 |
| 100 | 0.83 | 0.89 | 0.96 |
| 200 | 0.85 | 0.91 | 0.98 |

## 7. Eşik analizi kuralları
- Üretim eşikleri (`CONF`, `PERSON_CONF_MIN`, `ENTER_RATE`, `EXIT_RATE`, `WINDOW_SEC`, `MIN_OBS`, `MIN_SPAN_SEC`,
  `STALE_SEC`, ByteTrack) **bu fazda değiştirilmez**; tüm taramalar çevrimdışıdır.
- Tarama yalnız validation kümesinde; aday konfigürasyon testte **bir kez** baseline ile A/B karşılaştırılır.
- Her öneri: FN etkisi (kaçırılan ihlal), yanlış alarm yükü (kamera-saat), kamera bazlı değişkenlik birlikte sunulur.
- Ürün koduna taşımadan önce İSG + teknik sorumlu onayı ve ayrı faz gerekir.

## 8. Kabul eşikleri — KARAR GEREKİYOR
Aşağıdaki değerler **geliştirici tarafından belirlenmez**. İSG uzmanı ve saha sorumlusu yazılı olarak doldurur.

| Ölçüt | Hedef | Alt sınır mı nokta mı | Onaylayan / tarih |
|---|---|---|---|
| Baret eksikliği incident recall | ? | | |
| Yelek eksikliği incident recall | ? | | |
| Yanlış alarm / kamera-saat | ? | | |
| Duplicate alarm oranı | ? | | |
| Karar gecikmesi p95 (sn) | ? | | |
| Uçtan uca uyarı gecikmesi p95 (sn) | ? | | |
| Koşul başına en kötü recall (gece, uzak, örtülme) | ? | | |
| Kapsam dışı kişi oranı (ANALIZ'de kalan) | ? | | |

Teknik ekip görüşü (onay değildir): gözlem/insan doğrulamalı pilot için incident recall alt sınırı, kabul edilecek
yanlış alarm yükü ile birlikte belirlenmelidir; tek başına mAP veya kişi-kare F1 kabul ölçütü olarak kullanılmamalıdır.

## 9. Araçların kullanımı
`detector/` dizininde, `.venv` ile:

```
python -m evaluation env                                   # sürümler, best.pt SHA256, sınıf eşlemesi, üretim ayarları
python -m evaluation audit --root <veri> --freeze <manifest.json>
python -m evaluation detect --root <veri> --split val      # eşik taraması dahil
python -m evaluation detect --root <veri> --split test --manifest <manifest.json>
python -m evaluation validate-gt gt/*.json
python -m evaluation video <video...> --gt <gt.json...> --check-equivalence 60
python -m evaluation video <video...> --stride 1           # yalnız çevrimdışı karşılaştırma
python -m evaluation temporal                              # sentetik karar-mantığı senaryoları
```
Çıktılar `detector/evaluation/output/<UTC>_<komut>/` (JSON/CSV; Git dışı). `video --gt` için GT dosyasındaki
`video.source_name` (veya `video_id`) video dosya adıyla eşleşmelidir. `--check-equivalence N` değerlendirme kare
okuma yolunun üretimdeki `model.track(source=..., vid_stride=...)` ile ilk N karede birebir aynı sonuç verdiğini
doğrular. Snapshot kırpıntıları çıktıya yazılmaz.

## 10. Tekrar üretilebilirlik kaydı
Her rapor şunları içerir: Git HEAD, `best.pt` SHA-256, Ultralytics/torch/CUDA/OpenCV sürümleri, GPU, üretim
ayarları (`detector/.env` + `detector.py`), PPEMonitor parametreleri, ByteTrack YAML içeriği, manifest
`content_sha256`, komut satırı ve çıktı klasörü. Sentetik senaryolar sabit tohumla deterministiktir (testli).
