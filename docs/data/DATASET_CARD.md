# Veri kümesi kartları ve envanter (FAZ 10-A.3)

**Okuyucu:** ML geliştirici, veri mühendisi, ürün/İSG. **Tarih:** 2026-11-02. Envanter repo ve yerel dosyalar
incelenerek çıkarıldı (macOS kopyası); özetler bu oturumda yeniden hesaplandı. Hiçbir veri indirilmedi.

## 1. Envanter

| Alan | Construction-PPE (Ultralytics) | V1 `16533594_1920_1080_25fps.mp4` | V2 `5434223-hd_1920_1080_24fps.mp4` | V3 `8965526-uhd_2160_3840_25fps.mp4` | Sentetik fikstür `ppe-fixture` |
|---|---|---|---|---|---|
| Kaynak | `github.com/ultralytics/assets` release zip (FAZ 8'de indirildi) | Stok video (dosya adı kalıbı Pexels benzeri; kaynak kaydı yok) | aynı | aynı | `detector/evaluation/fixtures.py` |
| Sürüm / özet | zip SHA-256 `bef8dcb5…2c2632ccc` (178 415 813 B); donmuş manifest `fe003f10…cfcb0e` (1416 dosya, `detector/evaluation/manifests/`) | SHA-256 `6dd2ad33…66fd64` | `df2f645c…2fa4` | `b9183548…67ef5` | kod sürümü (Git) |
| Lisans / izin | **AGPL-3.0** (zip içi LICENSE); içindeki kişilerin rızası belirsiz → kullanım için **LICENSE_UNVERIFIED** (ticari kullanım kararı yok) | **LICENSE_UNVERIFIED** | **LICENSE_UNVERIFIED** | **LICENSE_UNVERIFIED** | SYNTHETIC |
| Etiket | Ultralytics'in YOLO kutuları (11 sınıf); bağımsız doğrulanmadı; train'de 10 sahipsiz etiket | **yok** | **yok** | **yok** | sentetik insan-biçimli etiket (`ppe-annotation/1`) |
| Çözünürlük | çeşitli, çoğu yakın plan | 1920×1080, 25 fps | 1920×1080, 23.98 fps | 2160×3840 (dikey), 25 fps | 1280×720 (kurgusal) |
| Kare / video | 1416 görüntü (train 1132 / val 143 / test 141) | 373 kare, 14.9 sn | 577 kare, 24.1 sn | 315 kare, 12.6 sn | 4 medya, 11 kare etiketi |
| Saha temsil gücü | **Düşük**: stok/sahnelenmiş, yakın plan; gece, yağmur, sabit CCTV açısı neredeyse yok; <60 px kişi yalnız 2 | Düşük: cadde seviyesi, 2 işçi, gündüz | Düşük: el kamerası, yakın takip, iç mekân | Düşük: dikey, yürüyüş, gündüz | **Yok** (yalnız hat testi) |
| Eğitimle örtüşme | **Modelin eğitim kümesinin kendisi**; test↔train dHash ≤ 4: 14, val↔train 10; test'in tahminen %15–25'i aynı sahneden | dHash en yakın 16 (yakın kopya yok) | 15 | 15 | — |
| Kullanım amacı (izinli olan) | Dağılım içi nesne düzeyi gösterge (FAZ 8); **saha doğruluğu değildir** | Performans/yük ve kapsam gözlemi | aynı | aynı | Veri hattı testleri |
| Hassas veri sınıfı | **Kişisel veri içerir** (tanınabilir kişiler) → Git dışı, yalnız yerel | Kişisel veri olabilir (tanınabilir işçiler) → Git dışı | aynı | aynı | Kişisel veri yok |
| Konum | `detector/evaluation/datasets/` (Git dışı) | `detector/videos/` (Git dışı, `*.mp4`) | aynı | aynı | repo (kod) |

## 2. `best.pt` eğitim kökeni

| Alan | Değer (FAZ 8 checkpoint meta; SHA bu oturumda doğrulandı) |
|---|---|
| Dosya | `detector/models/best.pt`, SHA-256 `6886ad2fc301cf992152d4b047307071cac940082ab071175c0ef498fe67ba43` |
| Mimari | YOLO26n, 11 sınıf (üretim: helmet 0, vest 2, Person 6) |
| Eğitim verisi | `construction-ppe.yaml` (yukarıdaki Construction-PPE), 100 epoch, imgsz 640, seed 0 |
| Model seçimi | val kümesiyle (val bağımsız değildir) |
| Lisans | checkpoint meta AGPL-3.0 → lisans kararı **NO-GO** |
| Sonuç | Construction-PPE üzerindeki her sonuç **eğitim dağılımı içi ve iyimserdir**; bağımsız saha başarısı sayılmaz |

## 3. Değerlendirme

- Bugün **etiketli, izinli, bağımsız saha veri kümesi yok**. Saha recall'ü, yanlış alarm/kamera-saat, duplicate ve
  gerçek uyarı gecikmesi ölçülemez → **BLOCKED**.
- Mevcut üç video, olası kullanımda bile `LICENSE_UNVERIFIED` olduğundan yeni manifestte BLOCKED kalır; ayrıca
  ihlal içermedikleri görülüyor (FAZ 8) → recall ölçümü için uygun değil.
- Construction-PPE FAZ 8 manifestine (`ppe-eval-manifest/1`, YOLO görüntü kümesi biçimi) dokunulmadı; yeni
  `ppe-dataset-manifest/1` video/olay düzeyi saha verisi içindir.

## 4. Gelecek saha veri kümesi kartı (şablon — doldurulmadı)

| Alan | Değer |
|---|---|
| `dataset_id` / `dataset_version` / manifest özeti / test dondurma özeti | |
| Toplama amacı ve onay referansları (G1–G11) | |
| Sahalar (anonim), kamera sayısı, oturum sayısı, tarih aralığı | |
| Kamera türleri, çözünürlük, yükseklik/açı | |
| Koşul dağılımı (`scenario_distribution`) ve boş hücreler | |
| `group_by` seçimi ve gerekçesi | |
| Kişi / ihlal / UNKNOWN / NOT_EVALUABLE sayıları (split başına) | |
| Etiketleyici sayısı, IAA sonuçları (kutu + durum) | |
| Bilinen sınırlamalar ve temsil edilmeyen koşullar | |
| Lisans ve saklama süresi | |
| Doğrulama raporu (durum, özet) | |
