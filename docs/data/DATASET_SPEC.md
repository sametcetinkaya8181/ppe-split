# Veri kümesi ve etiket sözleşmesi (FAZ 10-A.3)

**Okuyucu:** veri mühendisi, ML geliştirici, etiketleme ekibi; özet bölümü ürün/İSG için.
**Kod:** `detector/evaluation/datacontract.py` (etiket), `datamanifest.py` (manifest, ayrım, sızıntı, rapor),
`agreement.py` (uyum), `fixtures.py` (sentetik örnekler). JSON Schema: `detector/evaluation/schemas/`.
**Durum:** Altyapı hazır ve sentetik verilerle test edildi. **Gerçek saha verisi yok; toplanmadı.**

## 0. Özet (teknik olmayan okuyucu için)

- Her etiketli video için bir **etiket belgesi** (insan etiketleri + köken + izin) ve tüm veri kümesi için bir
  **manifest** (dosya özetleri, ayrım, sayımlar, sürüm) tutulur.
- Bir kişinin bareti/yeleği için dört durum vardır: **PRESENT** (var), **MISSING** (yok), **UNKNOWN** (kişi
  değerlendirilebilir ama ilgili bölge bu karede görünmüyor), **NOT_EVALUABLE** (kişinin kendisi çok küçük/bulanık/
  karanlık/kadraj dışında; değerlendirme dışı). UNKNOWN ve NOT_EVALUABLE **ne ihlal ne uygun** sayılır.
- Model tahmini insan etiketi yerine **geçemez**; YOLO'nun bir şeyi bulamaması "MISSING" demek değildir.
- Doğrulayıcı her veri kümesine **PASS / FAIL / BLOCKED** verir. İzin, lisans veya ikinci insan incelemesi eksikse
  sonuç **BLOCKED**'dır; gerçek saha veri kümesi yalnız PASS + izinli + lisanslı ise **READY** olur.

## 1. Etiket belgesi `ppe-annotation/1`

Bir belge = bir kaynak medya (video veya kare dizisi). JSON, UTF-8.

### 1.1 Üst düzey

| Alan | Tür | Kural |
|---|---|---|
| `schema_version` | `"ppe-annotation/1"` | sabit |
| `annotation_version` | tamsayı ≥ 1 | etiket her değiştiğinde **artırılır**; onay bu sürüme bağlıdır |
| `dataset_id` | kimlik | |
| `origin` | `"human"` | başka değer → `E_MODEL_AS_GT` |
| `annotators` | `["ann_X", …]` | anonim rol kimliği (`ann_/rev_/adj_` + kısa kod); ad/e-posta yasak |
| `media` | nesne | §1.2 |
| `frames` | liste | §1.3; en az bir kare |
| `incidents` | liste | §1.5 |
| `reviews` | liste | §1.6 belge düzeyi onay |

### 1.2 Medya metadata (`media`)

| Alan | Kural |
|---|---|
| `media_id`, `source_video_id`, `camera_id`, `session_id` | `[A-Za-z0-9_-]{1,40}` |
| `site_id` | **anonim** `site_<kod>` (saha adı/adresi yazılmaz; eşleme tablosu veri sorumlusunda, veri kümesi dışında) |
| `recorded_day` | `YYYY-MM-DD` (saat yazılmaz; gerekmedikçe kişiye bağlanabilir zaman bilgisi azaltılır) |
| `fps`, `frame_count`, `width`, `height` | pozitif |
| `pts_mode` | `cfr` (sabit kare hızı; `pts_sec ≈ frame_index / fps`) veya `vfr` (yalnız artan olmalı) |
| `scenario_tags` | koşul etiketleri (ör. `daylight`, `low_light`, `far_workers`, `occlusion`, `edge_of_frame`, `multi_person`, `crowd`, `motion_blur`, `rain_dust`) |
| `provenance.source_ref` | köken referansı (kayıt/aktarım kaydı numarası; yol veya kişi adı değil) |
| `authorization` | `{status: APPROVED/PENDING/REJECTED/WITHDRAWN/SYNTHETIC, reference}` — APPROVED için onay referansı zorunlu |
| `license` | `{id, status: VERIFIED/LICENSE_UNVERIFIED/SYNTHETIC}` |

### 1.3 Kare ve kişi

Kare: `{frame_index (0 tabanlı özgün kare), pts_sec, persons: [...]}`. Kişi yoksa `persons: []` yazılır (boş kare
de bilgidir). `frame_index` ve `pts_sec` kesin artan.

| Kişi alanı | Kural |
|---|---|
| `track_id` | **video-yerel** iz kimliği (`t1`, `t2`…). Gerçek kişi kimliği, çalışan no, ad, yüz özelliği **değildir**; videolar arası eşleştirme yapılmaz |
| `box` | `[x1, y1, x2, y2]` piksel, `x2 > x1`, `y2 > y1`, kare içinde (±1 px) |
| `visibility` | `full / partial / heavy_occlusion / head_hidden / torso_hidden / head_torso_hidden` |
| `occlusion` | `none / partial / heavy` |
| `truncated` | kadraj kenarında kesik mi |
| `annotator_confidence` | `high / medium / low` (etiketleyicinin kendi kararına güveni) |
| `person_status` | `EVALUABLE / NOT_EVALUABLE` (+ `not_evaluable_reason`: `too_small / motion_blur / low_light / truncated / crowd_overlap / other`) |
| `label_origin` | `human` veya `model_prelabel` (model önetiketi yalnız insan onayıyla — `agreed/adjudicated` — kabul edilir) |
| `reentry` | iz etiketli karelerde kaybolup döndüyse dönüşteki ilk kayıtta `true` |
| `helmet`, `vest` | `{status, box}` §1.4 |
| `review` | `{status: pending/agreed/disputed/adjudicated, reviewer, adjudicator, reason, resolution}` |

### 1.4 Ekipman durumu ve tutarlılık kuralları

| Durum | Anlam | Kutu | Metrikte |
|---|---|---|---|
| `PRESENT` | ekipman görülüyor ve kişiye ait | **zorunlu**, merkezi kişi kutusunun %10 genişletilmiş hali içinde | negatif (uygun) |
| `MISSING` | ilgili bölge açıkça görünüyor ve ekipman yok | **olamaz** | pozitif (ihlal) |
| `UNKNOWN` | kişi değerlendirilebilir; bölge bu karede görünmüyor/karar verilemiyor | olmamalı (varsa uyarı) | değerlendirme dışı, ayrı sayılır |
| `NOT_EVALUABLE` | kişinin kendisi değerlendirilemez (`person_status = NOT_EVALUABLE`) | olmamalı | değerlendirme dışı, kapsam metriğinde sayılır |

Kurallar (ihlal → `E_STATUS_CONTRADICTION` / `E_ASSOCIATION`):
- `head_hidden` / `head_torso_hidden` ise baret `PRESENT` veya `MISSING` **olamaz** (görünmeme ihlal değildir);
  `torso_hidden` / `head_torso_hidden` ise yelek için aynısı.
- `person_status = NOT_EVALUABLE` ⇔ iki ekipman da `NOT_EVALUABLE` ve neden yazılı.
- Kişi değerlendirilebilirken tek bir ekipmana `NOT_EVALUABLE` verilmez (o durum `UNKNOWN`'dur).
- Ekipman kutusu başka kişinin kutusuna düşüyorsa (kalabalık sahne) uyarı `W_ASSOCIATION_AMBIGUOUS`.

### 1.5 Incident (olay) etiketi

`{incident_id, track_id, violation_type: missing_helmet|missing_vest, start_sec, end_sec|null, uncertainty:
certain|uncertain, review}`. Aynı kişinin aynı ekipman eksikliğinin kesintisiz aralığıdır; `end_sec = null` →
video sonuna kadar sürüyor. Kurallar: iz karelerde bulunmalı; aralık video süresi içinde; aralıkta o kişi için
en az bir `MISSING` kare olmalı (`E_INCIDENT_SPAN`); aralık içinde `PRESENT` kare uyarıdır. Her incident kritik
etikettir: ikinci insan incelemesi zorunlu.

### 1.6 İnceleme (review) kuralları

- **Kritik etiket:** herhangi bir ekipman `MISSING/UNKNOWN/NOT_EVALUABLE`, `annotator_confidence = low`,
  `model_prelabel`, her incident. Kritik etiket `pending` ise → `B_REVIEW_MISSING` (BLOCKED).
- `agreed`: reviewer etiketleyiciden farklı olmalı. `adjudicated`: farklı adjudicator + yazılı `resolution`.
  `disputed`: gerekçe zorunlu; çözülene kadar `B_DISPUTE_UNRESOLVED`. **Otomatik çoğunluk kararı yoktur.**
- Belge düzeyi: en az bir bağımsız `approved` onayı ve onaylanan sürüm = güncel `annotation_version`; değilse
  `B_REVIEW_MISSING` / `B_REVIEW_STALE`.

### 1.7 Gizlilik kuralları

Belgenin hiçbir yerinde şu alanlar bulunamaz (`E_PRIVACY_FIELD`): `name, full_name, person_name, employee_id,
worker_id, national_id, tc, tc_kimlik, face, face_box, face_embedding, embedding, biometric, phone, email, address,
plate, license_plate`. Kimlikler anonim kalıplara uymalıdır (`E_ID_FORMAT`).

### 1.8 İsteğe bağlı hata profili ekleri (FAZ 10-A.5 hazırlığı)

`ppe-annotation/1` sürümü **değişmeden** iki isteğe bağlı ek kullanılabilir (anlamları: LABELING_GUIDE §8):

| Ek | Yer | Alanlar |
|---|---|---|
| `attributes` | kişi | `profile`, `episode_id`, `helmet_color`, `head_visibility`, `head_occluder`, `occluder`, `edge_contact[]`, `head_in_frame`, `near_machinery` |
| `hard_negatives` | kare | `[{box, category}]` — kişi olmayan, kişiye benzeyen nesneler |

Ekipman nesnesine (`helmet` / `vest`) ek alan **konmaz** (şemada `additionalProperties: false`). Uyumluluk (2026-11-03,
sentetik belgeyle denendi): mevcut doğrulayıcı ve JSON Schema ekleri kabul eder; gizlilik taraması `attributes` içinde de
çalışır. **Bilinen araç eksikleri (kod işi, FAZ 10-A.5 — bu belgeyle uygulanmadı):**

1. Doğrulayıcı ek alanların değerlerini ve tutarlılığını denetlemiyor (ör. `head_in_frame=false` iken baret PRESENT).
2. `to_eval_video` ekleri `ppe-eval-video/1`'e taşımıyor → FAZ 8 kırılımları yalnız `visibility` ve kişi yüksekliğine
   göre; `helmet_color`, `edge_contact`, `near_machinery` kırılımları ve `hard_negatives` kategorisine göre yanlış kişi
   sayımı yapılamıyor.
3. Bölüm (`episode_id`) bazlı süre ve "kapsam kaybı süresi" hesabı yok.
4. Gizlilik taraması alan adını **tam eşleşmeyle** arıyor: `name` yakalanır, `worker_name` yakalanmaz → alt dize/kalıp
   denetimine genişletilmeli.

## 2. Manifest `ppe-dataset-manifest/1`

| Alan | İçerik |
|---|---|
| `dataset_id`, `dataset_version` (`MAJOR.MINOR.PATCH`) | sürüm kuralı §4 |
| `annotation_schema_version` | `ppe-annotation/1` |
| `created_at` | UTC (…Z) — üretici verir (deterministik) |
| `data_classification` | `SYNTHETIC / PERSONAL_DATA / LICENSED_PUBLIC` |
| `label_map` | üretim sınıf eşlemesi `{helmet: 0, vest: 2, Person: 6}` (`ppe_core`'dan; uyuşmazsa `E_CLASS_MAP`) |
| `split_policy` | `{group_by, seed, ratios}` (SPLIT_PROTOCOL) |
| `items[]` | `media_id, media_path, media_sha256, annotation_path, annotation_sha256, annotation_version, split, source_ref, authorization, license, site_id, camera_id, session_id, source_video_id, recorded_day, dhash[]` |
| `label_counts`, `scenario_distribution` | split başına, etiketlerden **hesaplanır**; elle değiştirilirse `E_MANIFEST_TAMPERED` |
| `content_sha256` | `items` listesinin kanonik JSON özeti |
| `test_freeze` | `{frozen, test_items_sha256, frozen_at, approved_by_role}` |
| `validation` | (isteğe bağlı) doğrulayıcı adı, durum, rapor özeti — `data-validate --record` ile kopyaya eklenir |

Yollar manifest köküne göre **göreli POSIX** yoludur; mutlak yol, `..`, `.`, ters eğik çizgi, sürücü harfi ve
sembolik bağ reddedilir (`E_PATH`). Manifest ve etiket meta verisi birbirini tutmalıdır.

## 3. Doğrulama kodları

| Kod | Düzey | Anlam |
|---|---|---|
| `E_SCHEMA`, `E_MEDIA_META`, `E_EMPTY`, `E_ID_FORMAT` | FAIL | yapı/alan hatası |
| `E_BOX_INVALID` | FAIL | geçersiz/kare dışı kutu; PRESENT'ta kutu yok |
| `E_TIMESTAMP` | FAIL | kare indeksi/PTS/tarih hatası |
| `E_TRACK`, `E_TRACK_REENTRY` | FAIL | yinelenen iz, olmayan iz, işaretsiz yeniden giriş |
| `E_STATUS_CONTRADICTION` | FAIL | UNKNOWN/MISSING/NOT_EVALUABLE çelişkisi |
| `E_ASSOCIATION` | FAIL | ekipman yanlış kişiye bağlı |
| `E_INCIDENT`, `E_INCIDENT_SPAN` | FAIL | incident kimliği/türü; aralık-etiket uyuşmazlığı |
| `E_MODEL_AS_GT` | FAIL | model çıktısı/onaysız önetiket GT olarak |
| `E_REVIEW_INVALID`, `E_REVIEW_NOT_INDEPENDENT` | FAIL | inceleme kaydı hatalı/bağımsız değil |
| `E_PRIVACY_FIELD` | FAIL | kimlik/biyometri alanı |
| `E_PROVENANCE`, `E_AUTHORIZATION`, `E_LICENSE`, `E_WITHDRAWN` | FAIL | köken/izin/lisans eksik; geri çekilmiş kayıt |
| `E_MANIFEST`, `E_MANIFEST_TAMPERED`, `E_HASH_MISSING`, `E_HASH_MISMATCH`, `E_FILE_MISSING`, `E_PATH` | FAIL | manifest bütünlüğü |
| `E_SPLIT`, `E_SPLIT_POLICY`, `E_LEAKAGE`, `E_LEAKAGE_DUPLICATE`, `E_DUPLICATE_MEDIA`, `E_NEAR_DUPLICATE`, `E_TEST_CHANGED` | FAIL | ayrım ve sızıntı |
| `E_VERSION_MISMATCH`, `E_CLASS_MAP` | FAIL | sürüm / sınıf eşlemesi |
| `B_REVIEW_MISSING`, `B_REVIEW_STALE`, `B_DISPUTE_UNRESOLVED` | BLOCKED | insan incelemesi eksik |
| `B_AUTHORIZATION`, `B_LICENSE`, `B_SYNTHETIC` | BLOCKED | izin/lisans doğrulanmadı; sentetik kayıt gerçek veri kümesinde |
| `B_NO_PHASH`, `B_TEST_NOT_FROZEN` | BLOCKED | yakın kopya denetimi yapılamadı; test kümesi dondurulmadı |
| `W_*` | uyarı | insan gözü önerilir (durumu değiştirmez) |

## 4. Sürümleme

- **Etiket:** her değişiklikte `annotation_version` +1; eski onay geçersizleşir (`B_REVIEW_STALE`).
- **Veri kümesi:** `PATCH` = etiket düzeltmesi (medya aynı); `MINOR` = yeni medya eklendi (test kümesi hariç);
  `MAJOR` = test kümesi, şema veya ayrım politikası değişti (eski sonuçlarla karşılaştırılamaz).
- Her değerlendirme sonucu `dataset_id`, `dataset_version`, `content_sha256` ve `test_items_sha256` ile kaydedilir;
  sonuç hangi veri sürümüne aitse o manifestle yeniden doğrulanabilir.
- Manifestler küçük metin dosyalarıdır; gerçek veri kümelerinin manifesti de kişisel veri içerebilir (yol adları,
  kamera kimlikleri) → DATA_GOVERNANCE'a göre Git **dışında**, erişim kontrollü konumda tutulur. Repo'da yalnız
  sentetik fikstürler ve şema bulunur.

## 5. FAZ 8 değerlendirme hattına aktarım

`python -m evaluation data-convert ANN.json --split test --out EVAL.json` → `ppe-eval-video/1`. Yalnız **PASS**
belgeler dönüştürülür. Eşleme: PRESENT→present, MISSING→missing, UNKNOWN→unknown, NOT_EVALUABLE→unknown
(FAZ 8 metriklerinde değerlendirme dışı); özgün durum `gt_status_v1a3` alanında korunur. Çıktı
`python -m evaluation video VIDEO --gt EVAL.json` ile mevcut kişi-kare / incident / takip metriklerine girer
(entegrasyon testi: `tests/test_data_pipeline.py`). Model eğitimi, eşik değişikliği ve gerçek doğruluk sonucu bu
fazda **yoktur**.

## 6. Komutlar (detector/ dizininde)

```
python -m evaluation data-fixtures --out <boş_dizin> [--scenario valid]      # sentetik örnek
python -m evaluation data-manifest --root R --items I.json --dataset-id ID --dataset-version 1.0.0 \
    --created-at 2026-11-02T00:00:00Z --classification PERSONAL_DATA --group-by camera_id --seed S \
    --assign-splits --freeze veri_muhendisi --out R/manifest.json
python -m evaluation data-validate R/manifest.json --out <rapor_dizini>    # 0 PASS, 1 FAIL, 2 BLOCKED
python -m evaluation data-agreement A.json B.json --out iaa.json
python -m evaluation data-convert ANN.json --split test --out EVAL.json
```
Windows: aynı komutlar `.venv\Scripts\python -m evaluation …`; yollar manifest içinde her zaman `/` ile yazılır.
