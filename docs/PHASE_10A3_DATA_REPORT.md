# FAZ 10-A.3 raporu — Veri mühendisliği, etiket kalitesi ve veri yönetişimi

Tarih: 2026-11-02 (UTC). Platform: macOS (iMac M1, 8 GB), proje kopyası. Başlangıç: `e51be64` (FAZ 10-A.2).
**Okuyucu:** ürün ekibi, İSG ve işletme sorumluları, veri mühendisi, ML geliştirici.

## Yönetici özeti

- Gerçek şantiye doğruluğunu **ileride** ölçebilmek için veri hattı kuruldu: etiket şeması, etiketleme rehberi,
  iki aşamalı inceleme kuralları, veri kümesi manifesti ve sürümleme, sızıntısız train/val/test ayrımı, otomatik
  doğrulayıcı, etiketleyici uyum ölçümü ve mevcut model değerlendirme araçlarına aktarım.
- Hepsi **sentetik** örneklerle test edildi. **Gerçek saha verisi yok; toplanmadı, etiketlenmedi.** Toplama için
  KVKK izni ve lisans kararı gerekiyor (bugün NO-GO).
- Mevcut veriler (Construction-PPE, üç stok video) saha başarısını kanıtlamaya **uygun değil**: biri modelin kendi
  eğitim verisi, diğerlerinin lisansı doğrulanmamış ve etiketsiz.
- Bir kişinin bareti "görünmüyor" (UNKNOWN) veya kişi "değerlendirilemez" (NOT_EVALUABLE) ise bu **ne ihlal ne
  uygun** sayılır; şema ve doğrulayıcı bunu zorunlu kılıyor.

| Statü | Sonuç |
|---|---|
| **A. Data engineering pipeline** | **PASS** — tüm altyapı testleri geçti (§8) |
| **B. Real authorized site dataset** | **BLOCKED** — izinli gerçek veri yok; DATA_COLLECTION_BLOCKED |
| **C. Real-site model accuracy** | **BLOCKED** — etiketli saha verisi ve İSG kabul eşikleri yok |
| **D. Real construction site pilot** | **NO-GO** |

## 1. Ön denetim

- Proje kökü `/Users/d2-18/Downloads/ppe-split-main`; dal `main`; `e51be64` (FAZ 10-A.2) doğrulandı, üstünde
  commit yok.
- FAZ 10-A.1 commit edilmemiş değişiklikleri çalışma ağacında (dashboard DB katmanı, `tools/reliability/*`,
  `docs/BACKUP_RESTORE.md`, `docs/RELIABILITY_TEST_PLAN.md`, yeni 10-A.1 dosyaları) — **dokunulmadı, bu commit'e
  eklenmedi**. Bu fazın dosyaları (`detector/evaluation/`, `detector/tests/test_data_pipeline.py`, `docs/data/`)
  onlarla çakışmıyor.
- Çalışan süreç (salt okunur `ps`/`docker ps`): FAZ 10-A.1 bellek A/B'sinin ikinci koşusu (`memtest.py` + 4
  `eventgen.py`, izole `ppe-rel` yığını) 10:31Z–~12:02Z. Müdahale edilmedi; bu sürede yalnız küçük CPU testleri
  koşuldu, tam dedektör paketi ve 350 MB'lık manifest doğrulaması koşu bitimine bırakıldı (§8).
- Okunan belgeler: `docs/product/*`, `PHASE_10A2_PRODUCT_REPORT.md`, `PHASE_8_REPORT.md`,
  `MODEL_EVALUATION_PROTOCOL.md`, kökteki yol haritası (yalnız FAZ 10-A.3 maddeleri kapsama alındı). İncelenen kod:
  `detector/evaluation/{annotations,dataset,metrics,pipeline,cli}.py`, `ppe_core.py` (yalnız sınıf sabitleri).

## 2. Veri envanteri ve köken

Ayrıntı: [docs/data/DATASET_CARD.md](data/DATASET_CARD.md). Özetler bu oturumda yeniden hesaplandı:
`best.pt` `6886ad2f…` (FAZ 8 ile aynı), Construction-PPE zip `bef8dcb5…` (aynı), V1–V3 SHA-256 kaydedildi.

| Kaynak | Lisans | Etiket | Saha temsili | Eğitimle örtüşme | Kullanım |
|---|---|---|---|---|---|
| Construction-PPE (1416 görüntü) | AGPL-3.0; kişilerin rızası belirsiz → LICENSE_UNVERIFIED | Ultralytics YOLO etiketleri | Düşük | **Eğitim kümesinin kendisi**; test'in ~%15–25'i aynı sahne | Yalnız dağılım içi gösterge |
| V1, V2, V3 (stok video, toplam 51.6 sn) | **LICENSE_UNVERIFIED** | yok | Düşük | yakın kopya yok | Yük/kapsam gözlemi |
| `best.pt` | AGPL-3.0 (meta) | — | — | Construction-PPE ile eğitildi; val ile seçildi | — |
| `ppe-fixture` (sentetik) | SYNTHETIC | sentetik | yok | — | Hat testleri |

FAZ 8'deki train/test örtüşme riski korunuyor; Construction-PPE sonuçları bağımsız saha başarısı sayılmadı. Yeni
veri kümesi **indirilmedi**.

## 3. Oluşturulan / değişen dosyalar

| Dosya | İçerik |
|---|---|
| `detector/evaluation/datacontract.py` (yeni) | `ppe-annotation/1` sözleşmesi ve belge doğrulayıcısı (PRESENT/MISSING/UNKNOWN/NOT_EVALUABLE, görünürlük çelişkisi, kişi–ekipman ilişkisi, iz/yeniden giriş, zaman, incident aralığı, inceleme bağımsızlığı, model-önetiket kuralı, gizlilik alanları, izin/lisans), `to_eval_video` dönüştürücü |
| `detector/evaluation/datamanifest.py` (yeni) | `ppe-dataset-manifest/1` üretimi, güvenli yol, dHash (görüntü/video), deterministik grup ayrımı, test dondurma, sızıntı ve yakın kopya, sayım/özet bütünlüğü, JSON + Markdown rapor |
| `detector/evaluation/agreement.py` (yeni) | Etiketleyiciler arası uyum: kutu (F1, IoU) ve durum (ham uyum, Cohen kappa, karışıklık) ayrı; anlaşmazlık listesi, otomatik karar yok |
| `detector/evaluation/fixtures.py` (yeni) | Deterministik sentetik veri kümesi + 31 senaryo (beklenen durum ve tam hata/engel kodları) |
| `detector/evaluation/schemas/*.schema.json` (yeni) | Araçtan bağımsız JSON Schema (2020-12) — etiket ve manifest |
| `detector/evaluation/cli.py` | Yeni komutlar: `data-validate`, `data-manifest`, `data-agreement`, `data-convert`, `data-fixtures` (mevcut komutlar değişmedi) |
| `detector/tests/test_data_pipeline.py` (yeni) | 63 test |
| `docs/data/DATASET_SPEC.md`, `LABELING_GUIDE.md`, `DATA_GOVERNANCE.md`, `DATASET_CARD.md`, `SPLIT_PROTOCOL.md`, `ANNOTATION_TOOL_REVIEW.md` (yeni) | Belgeler |
| `docs/PHASE_10A3_DATA_REPORT.md` (yeni) | Bu rapor |

Değişmeyenler: `best.pt`, `ppe_core.py`, `detector.py`, ByteTrack ayarları, eşikler, PostgreSQL şeması ve verisi,
snapshot'lar, outbox, migration'lar, FAZ 8 `ppe-eval-video/1` şeması ve Construction-PPE manifesti.

## 4. Etiket şeması ve kurallar (özet)

[DATASET_SPEC.md](data/DATASET_SPEC.md). Önemli kararlar:
- **UNKNOWN ≠ NOT_EVALUABLE.** UNKNOWN: kişi değerlendirilebilir, ilgili bölge görünmüyor. NOT_EVALUABLE: kişinin
  kendisi değerlendirilemez (neden zorunlu). İkisi de metrikte ayrı sayılır; dönüştürmede FAZ 8'in `unknown`
  sınıfına gider ama özgün durum korunur.
- Kafa/gövde görünmüyorsa PRESENT/MISSING yazılamaz; MISSING'de kutu olamaz; PRESENT'ta kutu ve doğru kişi zorunlu.
- `origin` her zaman `human`; model önetiketi yalnız insan onayıyla. "YOLO bulamadı" ≠ MISSING.
- `track_id` video-yereldir; kimlik/biyometri alanları belgenin hiçbir yerinde bulunamaz; saha ve kişi kimlikleri
  anonim kalıpta.
- Kritik etiket ve her incident için ikinci insan incelemesi; anlaşmazlıkta hakem; çoğunluk oyu yok; eksik inceleme
  **BLOCKED** (PASS değil).

## 5. Ayrım, sızıntı, manifest

[SPLIT_PROTOCOL.md](data/SPLIT_PROTOCOL.md): grup düzeyi (`site → camera → session → source_video`) deterministik
ayrım (seed'li, girdi sırasından bağımsız); SHA-256 ve dHash ile kopya/yakın kopya; test dondurma ve tek kullanım;
manifestte medya/etiket SHA-256, sürüm, izin/lisans, sayımlar, senaryo dağılımı, içerik özeti. Dış servis yok;
üretim PostgreSQL şeması değişmedi.

## 6. Yönetişim ve araç

- [DATA_GOVERNANCE.md](data/DATA_GOVERNANCE.md): varsayılan **DATA_COLLECTION_BLOCKED**; 11 maddelik toplama
  kapısı (G1–G11), roller, erişim, Git dışı şifreli saklama, saklama/imha (veri sorumlusu belirler; otomatik silme
  ana veriye uygulanmaz), geri çekme (WITHDRAWN → FAIL), dış aktarım yok, köken/denetim, lisans kontrolü.
- [ANNOTATION_TOOL_REVIEW.md](data/ANNOTATION_TOOL_REVIEW.md): CVAT (öneri, self-hosted, yalnız yerel) ve Label
  Studio Community karşılaştırması; **kurulum yapılmadı**, araç dönüştürücüsü araç onaylanınca yazılacak.

## 7. Model değerlendirme entegrasyonu

`data-convert` ile `ppe-annotation/1` → `ppe-eval-video/1`; dönüştürülmüş GT FAZ 8 `annotations.validate`'ten
hatasız geçiyor ve `evaluate_against_gt` ile kişi-kare, incident ve takip (ID switch/parçalanma) metriklerini
üretiyor (sentetik entegrasyon testi). Model eğitilmedi, eşik değişmedi, gerçek doğruluk sonucu üretilmedi.

## 8. Testler

| Paket | Ortam | Sonuç |
|---|---|---|
| Yeni veri hattı testleri (`tests/test_data_pipeline.py`): 31 senaryo (beklenen durum + tam kod kümesi), etiket kapsamı, doğrulayıcının dosya değiştirmemesi, manifest/rapor determinizmi, sıra bağımsızlığı, izin/READY mantığı, sentetik-gerçek ayrımı, model≠GT, gizlilik, UNKNOWN/NOT_EVALUABLE ayrımı, zaman kuralları, hakem bağımsızlığı, ayrım determinizmi/gruplama/oranlar, dondurma, yol güvenliği (8 kaçış biçimi + sembolik bağ), dHash, IAA, kappa uç durumları, FAZ 8 entegrasyonu, dönüştürücü reddi, JSON Schema tutarlılığı, CLI | macOS, CPU, `.venv` Python 3.12 | **PASS 63/63** (0.8 sn) |
| Mevcut FAZ 8 değerlendirme testleri (`test_evaluation_*`) | aynı | **PASS 49/49** |
| Tam dedektör paketi (A/B koşusu bittikten sonra, 12:01Z) | aynı | **PASS 307/308, SKIP 1** (244 mevcut + 63 yeni; SKIP: Windows ACL testi, platform gereği) |
| Construction-PPE donmuş manifesti (`fe003f10…`) doğrulaması (`verify_manifest`) | aynı | **PASS** — 1416 dosya, 0 fark (FAZ 8'den beri değişmedi) |
| GPU / soak | — | **SKIP** — üretim kodu değişmedi (gerekçe: prompt §13) |
| Dashboard / tarayıcı / LAN TLS | — | **SKIP** — dashboard'a dokunulmadı |

**Mac / Windows uyumu:** yalnız standart kütüphane (+ isteğe bağlı OpenCV dHash); dosyalar UTF-8 açık kodlamayla;
manifest yolları her platformda `/` ile; Windows yol biçimleri (`C:`, `\`) reddedilir; sembolik bağ testi Windows'ta
yetki yoksa SKIP olur. Windows'ta **koşulmadı** (bu oturumda Windows erişimi yok) — Windows'ta
`.venv\Scripts\python -m pytest tests/test_data_pipeline.py` ile doğrulanmalı.

## 9. PASS / FAIL / SKIP / BLOCKED

| Kalem | Sonuç |
|---|---|
| Veri envanteri ve köken (Construction-PPE, V1–V3, best.pt, manifestler) | PASS |
| Etiket şeması (medya, kişi, PPE, incident, inceleme, sürüm) | PASS |
| Etiketleme rehberi (A–M, metrik etkileri, sentetik örnek) | PASS |
| İki aşamalı inceleme + IAA prosedürü ve aracı | PASS (araç ve prosedür); **IAA kabul eşiği BLOCKED** (onay yok) |
| Ayrım/sızıntı/yakın kopya/dondurma | PASS (sentetik) |
| Manifest ve sürümleme | PASS (sentetik) |
| Doğrulayıcı (JSON + Markdown, PASS/FAIL/BLOCKED, ham veriyi değiştirmez) | PASS |
| Sentetik fikstürler: prompt'taki 13 durumun tamamı (uygun / baret eksik / yelek eksik / UNKNOWN `valid` veri kümesinde; iz parçalanması hem `track_reentry_unflagged` hem FAZ 8 entegrasyon testinde) + ek hata senaryoları — toplam 31 senaryo | PASS |
| FAZ 8 entegrasyonu | PASS (sentetik) |
| Etiketleme aracı | Öneri hazır; **kurulum yapılmadı** (kapı kapalı) |
| Araç dışa aktarımı dönüştürücüsü | **SKIP** (araç seçimi onaylanmadan) |
| Windows'ta test | **SKIP** (erişim yok) |
| İzinli gerçek saha veri kümesi | **BLOCKED** |
| Saha doğruluğu (recall, yanlış alarm/kamera-saat, duplicate, gecikme) | **BLOCKED** |
| KVKK / lisans | **NO-GO** |

## 10. Kalan riskler

1. Etiket rehberindeki değerlendirilebilirlik eşiği (40 px) ve IAA kabul ölçütleri İSG onayı bekliyor.
2. dHash yakın kopya denetimi kırpma/döndürmeye zayıf; asıl koruma grup ayrımı. Tek sahalı veri kümesinde sahaya
   genelleme ölçülemez.
3. Model önetiketi kullanılırsa yanlılık riski (modelin hatası GT'ye girer); test kümesinde önetiket önerilmez.
4. Etiketleme aracı ikinci bir kişisel veri deposu yaratır; saklama/imha ve yedekleme kapsamına alınmalı.
5. Gerçek veri manifesti de kişisel veri sayılabilir (kamera/oturum kimlikleri) → Git dışı tutulmalı.

## 11. FAZ 10-A.4 ön koşulları (otomatik geçilmedi)

1. KVKK kararı ve G1–G11 kapısı; lisans kararı.
2. İSG/işletme kabul eşikleri (ACCEPTANCE_METRICS onay formu) ve etiket rehberi eşiklerinin onayı.
3. Etiketleme aracı kararı + dönüştürücü; etiketleyici eğitimi ve IAA pilotu.
4. İzinli, etiketli, IAA'dan geçmiş, dondurulmuş, `data-validate` → **PASS / READY** saha veri kümesi.

## 12. Git commit

Yalnız bu fazın dosyaları, dosya bazlı staging ile commit edildi; FAZ 10-A.1 değişiklikleri ve kullanıcı belgeleri
commit dışı. Commit kimliği oturum özetinde bildirildi (rapor kendi commit kimliğini içeremez).
