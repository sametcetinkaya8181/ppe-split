# Train / validation / test ayrım protokolü ve sızıntı denetimi (FAZ 10-A.3)

**Okuyucu:** veri mühendisi, ML geliştirici. **Kod:** `detector/evaluation/datamanifest.py`
(`assign_splits`, `freeze_test`, `validate_dataset`). **Durum:** altyapı sentetik verilerle test edildi; gerçek
veri kümesi **yok**.

## 1. Neden grup düzeyinde ayrım

Aynı kameranın, aynı kayıt oturumunun veya aynı videonun komşu kareleri birbirine çok benzer. Bunlar hem eğitimde
hem testte bulunursa test sonucu **iyimser** çıkar (FAZ 8'de Construction-PPE test kümesinin tahminen %15–25'inin
train'de aynı sahneden karşılığı vardı). Bu yüzden ayrım **kare** düzeyinde değil **grup** düzeyinde yapılır.

## 2. Grup düzeyleri

`site_id` → `camera_id` → `session_id` → `source_video_id` (kabadan inceye). `split_policy.group_by` seçilen
düzeydir; o düzey ve **tüm daha ince düzeyler** split'ler arasında paylaşılamaz (`E_LEAKAGE`). Daha kaba düzeyin
paylaşılması uyarıdır (`W_GROUP_SHARED`) — ör. `group_by=camera_id` ile aynı saha train ve testte olabilir.

| Durum | Önerilen `group_by` |
|---|---|
| Birden çok saha var | `site_id` (sahaya genelleme ölçülür — tercih edilen) |
| Tek saha, birden çok kamera | `camera_id` (yeni kameraya genelleme) |
| Tek kamera | `session_id` (en zayıf; rapora "tek kamera" sınırlaması yazılır) |

Seçim ve gerekçe veri kümesi kartına (DATASET_CARD) yazılır.

## 3. Deterministik atama

`assign_splits(entries, group_by, seed, ratios)`:
1. Gruplar `sha256(seed | grup)` sırasına dizilir (girdi sırası sonucu etkilemez).
2. Her grup, hedef orana (`ratio × toplam kayıt`) en uzak split'e bütün olarak atanır.
3. Grup değeri eksik kayıt varsa **ayrım yapılmaz** (hata) — bilinmeyen grup sızıntısız sayılmaz.

Aynı `seed`, aynı kayıtlar → her makinede (Mac/Windows) aynı sonuç. `seed` ve oranlar manifestte saklanır.
Grup sayısı azsa oranlar tutmayabilir; gerçek oranlar raporda `media_per_split` ile görülür.

## 4. Kopya ve yakın kopya denetimi

| Kontrol | Yöntem | Sonuç |
|---|---|---|
| Bayt-özdeş medya | SHA-256 | farklı split'lerde → `E_LEAKAGE_DUPLICATE`; aynı split'te → `E_DUPLICATE_MEDIA` |
| Yakın kopya (yeniden boyutlandırma, yeniden kodlama, komşu kare) | dHash 64 bit (görüntü: 1; video: eşit aralıklı 5 kare), Hamming ≤ 4 | farklı split'lerde → `E_NEAR_DUPLICATE` (insan incelemesi); aynı split'te uyarı |
| Algısal hash yok | — | gerçek veri kümesinde `B_NO_PHASH` (BLOCKED) |

Değerlendirme: SHA-256 yalnız bayt aynılığını yakalar. dHash ölçek/sıkıştırma değişikliğine dayanıklıdır, ama
kırpma/döndürmeye zayıftır ve benzer sahnelerde yanlış pozitif verebilir (FAZ 8: siyah çerçeveli farklı görüntü).
Bu yüzden yakın kopya **hata olarak işaretlenir ve insan karar verir**; eşik değişikliği manifest sürümüyle kaydedilir.
Grup düzeyi ayrım asıl korumadır; hash denetimi ikinci savunmadır (aynı olayın farklı kameradan çekimi gibi
durumlar hash ile yakalanmaz → `session_id`/`recorded_day` ile değerlendirilir).

## 5. Test kümesinin dondurulması ve tek kullanım

1. Test kümesi ayrım, IAA ve hakem çözümleri tamamlandıktan sonra `freeze_test` ile dondurulur
   (`test_items_sha256`, `frozen_at`, onaylayan rol). Dondurulmamış küme `B_TEST_NOT_FROZEN`.
2. Dondurulmuş kümede herhangi bir değişiklik `E_TEST_CHANGED` üretir; düzeltme gerekiyorsa **yeni MAJOR sürüm**
   ve yeni dondurma (eski sonuçlar yeni sürümle karşılaştırılmaz).
3. Eşik/model seçimi yalnız **validation** kümesinde yapılır. Test kümesi aday başına **bir kez** (baseline ile A/B)
   çalıştırılır; her test çalıştırması manifest özetiyle birlikte deney kaydına yazılır (FAZ 10-A.5
   `EXPERIMENT_REGISTRY`). FAZ 8 CLI testte eşik taramasını zaten reddeder.
4. Test kümesi sonuçları incelenip bu sonuca göre etiket/eşik değiştirilirse test kümesi **kirlenmiş** sayılır;
   yeni test kümesi gerekir.

## 6. Raporlama

`python -m evaluation data-validate manifest.json --out <dizin>` → `report.json` (makine) + `report.md` (insan).
Rapor zaman damgası içermez; aynı manifest aynı raporu üretir. Raporda split başına medya sayısı, hata/engel/uyarı
kodları, `manifest_content_sha256`. Ayrım sonucu ayrıca `label_counts` ve `scenario_distribution` ile
manifestte saklanır (koşul matrisi boşlukları: `MODEL_EVALUATION_PROTOCOL.md` §5).
