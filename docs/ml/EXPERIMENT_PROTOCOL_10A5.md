# FAZ 10-A.5 deney protokolü — E-01 – E-04 kontrollü iyileştirme (hazırlık)

**Okuyucu:** ML geliştirici, veri mühendisi, İSG uzmanı (kabul eşikleri), ürün sahibi.
**Durum (2026-11-03):** Protokol hazır; **hiçbir deney yapılmadı, model eğitilmedi, üretim ayarı değişmedi.** Bağımsız
etiketli saha verisi ve İSG kabul eşikleri olmadan hiçbir aday kabul edilemez.

İlgili belgeler (tekrar edilmez, bağlanır): metrik tanımları `docs/MODEL_EVALUATION_PROTOCOL.md` §6; KPI sözlüğü ve
onay formu `docs/product/ACCEPTANCE_METRICS.md`; riskler `docs/product/RISK_REGISTER.md` (R-01, R-04, R-07, R-08);
etiket profilleri `docs/data/LABELING_GUIDE.md` §8; veri sözleşmesi `docs/data/DATASET_SPEC.md`; ayrım ve dondurma
`docs/data/SPLIT_PROTOCOL.md`; referans değerlendirme `docs/STEELBENCH.md`, `docs/PHASE_10A4_BASELINE_REPORT.md`.

## 1. Değişmez kurallar

1. **Baseline sabittir:** `best.pt` (SHA-256 `6886ad2f…`), sınıf haritası (0/2/6), `ppe_core.py` eşikleri, ByteTrack
   ayarı. Adaylar ayrı dosya/ayar/dalda; üretime aktarım ayrı onaylı faz (feature flag + rollback).
2. **Kapılar sırası:** (a) birim/regresyon testleri → (b) **SteelBench** (`--model` veya aday ayarla; yeni alarm =
   insan incelemesi; bütünlük/izolasyon PASS) → (c) **validation** kümesinde ayar ve seçim → (d) **dondurulmuş test**
   kümesinde aday başına **bir kez** baseline ile A/B → (e) bağımsız insan doğrulaması → (f) İSG kabulü.
3. **Eşik ayarı yalnız validation'da.** Test sonucu görüldükten sonra yapılan her ayar test kümesini kirletir (yeni
   test kümesi gerekir).
4. **Çapraz regresyon = başarısızlık:** aday bir hatayı düzeltirken başka bir KPI'yı onaylı tolerans dışında kötüleştirirse
   (ör. E-02 için kişi precision artarken kişi recall düşerse) aday **FAIL**'dir; "net iyileşme" hesabı yapılmaz.
5. **Koşul bazında en kötü durum** raporlanır; ortalama onu gizlemez.
6. **Bilinen yanlış alarmlar asla doğru tespit sayılmaz**; SteelBench'te bir hatanın `NOT_REPRODUCED` olması yalnız
   *fix candidate*'tir.
7. **SteelBench videoları ve Construction-PPE** E-01–E-04 test kümesine girmez (DATA_GOVERNANCE §11).

## 2. Metrik haritası ve bugünkü ölçülebilirlik

| Hata | Hedef metrik | Mevcut araç çıktısı | Gerekli GT düzeyi | Bugün |
|---|---|---|---|---|
| E-01 | Örtülü-baret bölümlerinde yanlış ihlal: incident düzeyi yanlış alarm; kişi-kare `fp` ve `violation_on_unknown` | `evaluate_against_gt` → `incidents.false_incidents`, `person_frame.helmet.{fp, violation_on_unknown}`, `breakdown["visibility=…"]` | Yoğun kişi-kare + bölüm + gerçek baretsiz pozitifler | **NOT_MEASURABLE** |
| E-01 | Baret eksikliği **recall**'ü (iyileştirme kaçırmayı artırmamalı) | `person_frame.helmet.recall`, `incidents.recall` | Gerçek baretsiz incident'lar | **NOT_MEASURABLE** |
| E-02 | Kişi precision (nesne) ve GT'de kişi olmayan karede kişi tahmini | `detect_eval` kişi P/R; `person_frame.*.fp_unmatched`; `incidents.false_incidents` | Kapsamlı kişi etiketi + `hard_negatives` | **NOT_MEASURABLE** |
| E-02 | Kişi **recall** (makine yakınındaki gerçek kişiler) | `detect_eval` kişi R; `fn_not_detected` | `near_machinery` | **NOT_MEASURABLE** |
| E-03 | GT-değerlendirilebilir kişide sistem karar oranı (kapsam) | `person_frame.*.{fn_undecided, abstain_present}` / GT EVALUABLE | `edge_contact`, `head_in_frame` | **NOT_MEASURABLE** (yalnız sistem iç kapsamı ölçülüyor: SteelBench V2 0.380) |
| E-03 | Kenarda yeni yanlış alarm | `person_frame.helmet.fp` (kenar kırılımı) | aynı | **NOT_MEASURABLE** |
| E-04 | ID switch, parçalanma, duplicate incident | `tracking.{id_switches, fragmentation_extra_ids}`, `incidents.duplicates` | Yoğun `track_id` + `reentry` | **NOT_MEASURABLE** (SteelBench V1: 6 sistem kimliği / 2 incelenen kişi — gösterge) |
| Tümü | Yanlış alarm / kamera-saat | `incidents.false_alarms_per_camera_hour` | Tam etiketli kamera-saat | **INSUFFICIENT_GROUND_TRUTH** |
| Tümü | İnsan inceleme uyumu | `data-agreement` (kutu + durum); profil öznitelikleri için araç yok | ≥ 2 bağımsız etiketleyici | Araç kısmen hazır |

Belirsizlik: Wilson %95 (MODEL_EVALUATION_PROTOCOL §6.6). Kişi-kare aralıkları kümelenmeyi yok saydığından karar
**incident / bölüm / kişi** düzeyinde verilir.

## 3. Deney kartları

### E-01 — Kısmen örtülü koyu baret → yanlış "Baretsiz"

| # | İçerik |
|---|---|
| 1. Baseline kanıtı | SteelBench V2 t=4.8 sn, iz 3 (`KNOWN_FALSE_ALARM`); inceleme görselinde bu kişide **baret kutusu yok**; FAZ 8 E-01; sentetik: baş ≥ 3.5 sn görünmezse yanlış alarm |
| 2. Kök neden hipotezleri | H1 koyu baret + örtülmede **tespit** kaybı (ana); H2 baş görünmüyorken PPEMonitor gözlemi "baret yok" sayıyor; H3 baş bölgesi eşleştirme geometrisi omuzdaki nesneyle karışıyor |
| 3. Gerekli veri | §8.2 profili: örtülü-baret bölümleri (renk, örtücü, görünürlük) **ve** gerçek baretsiz kişiler (koyu saç, bere, kapüşon); farklı kamera/saha |
| 4. Aday değişiklikler | A1 karar kuralı: baş bölgesi örtülü/yetersizken gözlemi "bilinmiyor" say (ihlal penceresine katma); A2 koyu baret + örtülme verisiyle ince ayar; A3 baş bölgesi eşleştirme toleransı (yalnız ölçümle) |
| 5. Beklenen iyileşme | Örtülü-baret bölümlerinde yanlış incident ↓; `violation_on_unknown` ↓ |
| 6. Olası regresyon | A1 gerçek baretsizliği "bilinmiyor"a çevirir → **recall ↓, kapsam ↓** (en tehlikeli); A2 açık renkli kask/yelek tespitinde değişim |
| 7. Kabul (sayılar İSG onayına bağlı) | Yanlış incident (örtülü bölüm) ↓ en az onaylı etki kadar **ve** baret recall'ü ile kapsam onaylı non-inferiority marjının içinde **ve** SteelBench'te yeni incelenmemiş alarm yok |
| 8. Bağımsız doğrulama | Etiketleyiciden bağımsız iki kişi; tüm değişen alarmlar insan incelemesinden geçer |

### E-02 — İş makinesi "kişi" algılanıyor

| # | İçerik |
|---|---|
| 1. Baseline kanıtı | SteelBench V3 t=9.0 sn, iz 159 (`KNOWN_FALSE_ALARM`): kırmızı makine kolu; FAZ 8 E-02 |
| 2. Hipotezler | H1 eğitim verisinde zor negatif eksikliği; H2 kişi güven eşiği (0.40) bu nesnede aşılıyor; H3 iz sürekliliği kısa FP'yi uzatıyor (iz 159: 36 kare) |
| 3. Gerekli veri | §8.3: `hard_negatives` kategorili makineli sahneler + makine yakınında **gerçek kişiler** (`near_machinery`) |
| 4. Adaylar | B1 zor negatiflerle ince ayar; B2 kamera bazlı hariç tutma bölgesi (yapılandırma, kamera başına onaylı); B3 kişi kutusu en-boy/hareket tutarlılık filtresi (yalnız ölçümle) |
| 5. Beklenen | Makineli sahnede kişi precision ↑, yanlış incident ↓ |
| 6. Regresyon | Kişi **recall ↓** (özellikle makineye yakın / kısmen görünen gerçek kişiler); B2 hariç tutma bölgesine giren gerçek kişiyi **kör nokta** yapar |
| 7. Kabul | Makineli sahnede yanlış incident ↓ **ve** kişi recall'ü (`near_machinery≠none` dahil) marj içinde **ve** B2 için kör nokta haritası İSG onaylı |
| 8. Doğrulama | Bağımsız inceleme; B2 için saha sorumlusu imzası |

### E-03 — Kadraj kenarında kapsam kaybı

| # | İçerik |
|---|---|
| 1. Baseline kanıtı | SteelBench V2 iz 1: 23.9 sn görünür, değerlendirilen oran 0.0, nedeni `edge` (282 kare); baret kutusu bulunup eşleşmiş; FAZ 8 E-03 |
| 2. Hipotezler | H1 `EDGE_MARGIN_PX` kapısı tüm kişiyi dışlıyor (baş içerideyken de); H2 yakın plan kişide boyut/kesiklik birlikte |
| 3. Gerekli veri | §8.4: kenara değen kişiler, `edge_contact`, `head_in_frame`, insan EVALUABLE kararı |
| 4. Adaylar | C1 yalnız baret için: kişi kenara değse de baş bölgesi kadraj içindeyse baret kararına izin ver; C2 yeleği ayrı kapı ile değerlendir |
| 5. Beklenen | GT-EVALUABLE kenar kişilerinde sistem karar oranı ↑ |
| 6. Regresyon | Kesik gövde/başta **yeni yanlış alarm** (E-01 benzeri); yelek kararında hata |
| 7. Kabul | Kenar kapsamı ↑ **ve** kenar kırılımında yanlış ihlal marj içinde **ve** iç bölge metriklerinde değişim yok |
| 8. Doğrulama | Bağımsız inceleme; kapsam eşiği (K-04) İSG onaylı |

### E-04 — İz kimliği parçalanması

| # | İçerik |
|---|---|
| 1. Baseline kanıtı | SteelBench V1: 2 incelenen kişi → 6 sistem kimliği (5'i ≥ 10 kare); "yetersiz geçmiş" 90 kişi-kare; FAZ 8 sentetik: kadrajdan çıkıp dönmede duplicate, kesişmede yanlış `end` |
| 2. Hipotezler | H1 araç örtmesi > `track_buffer` (30 kare ≈ 2.4 sn @12.5 Hz); H2 eşleşme eşiği; H3 incident'ın iz kimliğine bağlı olması |
| 3. Gerekli veri | §8.5: yoğun `track_id` + `reentry` + örtücü türü; kesişen kişiler |
| 4. Adaylar | D1 `track_buffer` / `match_thresh` taraması (validation); D2 görünüm tabanlı yeniden tanıma (ReID) değerlendirmesi; D3 incident sürekliliği (aynı yerde kısa sürede yeni iz → aynı incident adayı) |
| 5. Beklenen | ID switch, parçalanma, duplicate incident ↓; "yetersiz geçmiş" kaynaklı kapsam kaybı ↓ |
| 6. Regresyon | Farklı kişileri **birleştirme** (`pred_ids_covering_multiple_gt` ↑) → bir kişinin ihlali diğerine yazılır; D3 gerçek iki ayrı ihlali tek sayar (kaçırma) |
| 7. Kabul | Parçalanma ve duplicate ↓ **ve** birleştirme hatası marj içinde **ve** incident recall'ü düşmemiş |
| 8. Doğrulama | Bağımsız takip etiketi incelemesi |

## 4. Ground-truth asgari düzeyi (öneri — İSG ve veri sorumlusu onayı gerekir)

Wilson alt sınırı tablosuna göre (30 incident'ta gözlenen 0.90 → alt sınır ≈ 0.74) öneri: her hata için **≥ 30
bağımsız bölüm/incident** ve ayrıca regresyon metriği için **≥ 30 karşı örnek** (E-01: gerçek baretsiz; E-02: makine
yakınında gerçek kişi; E-03: iç bölge kişileri; E-04: kesişen farklı kişiler), en az **2 saha veya 3 kamera** (ayrım
`group_by` ≥ `camera_id`), iki bağımsız etiketleyici + hakem. Bu sayılar **onaylanmadan** "yeterli veri" denmez.

## 5. Aday karar kuralı

Aday **PASS** yalnız: hedef metrikte onaylı minimum etki **ve** bütün diğer KPI'larda onaylı non-inferiority marjı
**ve** SteelBench bütünlük/izolasyon PASS ve yeni alarm yok (veya bağımsız incelenmiş) **ve** test kümesi bir kez
kullanıldı **ve** bağımsız insan doğrulaması tamam. Biri eksikse **FAIL / INCONCLUSIVE**; baseline kalır.

## 6. Başlamadan önce gereken araç işleri (kod; bu belgeyle uygulanmadı)

1. Doğrulayıcıya profil eklerinin değer ve tutarlılık denetimi (DATASET_SPEC §1.8-1).
2. `to_eval_video` + `evaluate_against_gt` kırılımlarının `attributes` alanlarına genişletilmesi; `hard_negatives`
   kategorisine göre yanlış kişi sayımı; bölüm süresi ve kapsam kaybı süresi.
3. Profil öznitelikleri ve iz sürekliliği için etiketleyici uyumu.
4. Gizlilik taramasının alt dize/kalıp denetimine genişletilmesi.
5. SteelBench'e aday ayar dosyası girişi (bugün `--model` var; eşik/tracker adayları için ayar girişi yok).

Hepsi `detector/evaluation/` ve `tools/reliability/` içinde, sentetik fikstür ve testlerle; üretim kodu değişmez.

## 7. Deney kaydı şablonu

| Alan | Değer |
|---|---|
| Deney kimliği / tarih / sorumlu rol | |
| Hata (E-xx) ve hipotez (Hn) | |
| Aday tanımı (model SHA-256 / ayar farkı) | |
| Veri: manifest `content_sha256`, `test_items_sha256`, sürüm | |
| SteelBench sonucu (çalıştırma kimliği, karar, yeni/kaybolan alarm) | |
| Validation sonuçları (hedef + regresyon metrikleri, Wilson aralıkları) | |
| Test kümesi kullanımı (tarih; ilk ve tek mi) | |
| Bağımsız insan doğrulaması (kim/rol, sonuç) | |
| Karar: PASS / FAIL / INCONCLUSIVE ve gerekçe | |

## 8. Hazırlık durumu

| İş | Durum |
|---|---|
| Etiketleme profilleri E-01–E-04, veri kısıtları, deney kartları, karar kuralı | **Hazır (bu belge + LABELING_GUIDE §8)** |
| Metrik haritası (mevcut araçlarla) | Hazır; E'ye özgü kırılımlar araç eksiği (§6) |
| §6 araç işleri (sentetik veriyle) | Başlanabilir (kod işi; ayrı onay ve commit) |
| İzinli saha verisi + etiketleme + dondurulmuş test | **Engelli** — KVKK (G1–G11), lisans |
| Ground-truth asgari sayıları, minimum etki ve non-inferiority marjları | **Engelli** — İSG/işletme onayı |
| Aday eğitimi/deneyleri | **Engelli** — veri ve onaylar |
| Aday kabulü / üretime aktarım | **Engelli** — yukarıdakiler + 10-A.9 kurulu |
