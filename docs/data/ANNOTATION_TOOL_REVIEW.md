# Etiketleme aracı değerlendirmesi (FAZ 10-A.3)

**Okuyucu:** veri mühendisi, BT, veri sorumlusu. **Durum:** karşılaştırma ve öneri. **Hiçbir araç kurulmadı,
hiçbir servise veri gönderilmedi.** Aşağıdaki ürün bilgileri genel bilgidir; kurulum kararından önce seçilen
sürümün resmî belgeleri ve lisans metniyle **doğrulanmalıdır** (sürümler hızlı değişir).

## 1. İhtiyaç

Video üzerinde kişi kutusu + iz (track) takibi, kişiye bağlı baret/yelek durumu (PRESENT/MISSING/UNKNOWN/
NOT_EVALUABLE) ve görünürlük öznitelikleri, olay (incident) aralığı, iki aşamalı inceleme + hakem, yerel
barındırma, dış aktarım yok, `ppe-annotation/1`'e dönüştürülebilir dışa aktarım, Mac ve Windows'ta çalışabilme.

## 2. Karşılaştırma

| Ölçüt | CVAT (self-hosted, açık kaynak) | Label Studio Community (açık kaynak) | Araçsız: JSON + basit yerel araç |
|---|---|---|---|
| Video etiketleme | Güçlü: video görevleri, kare atlama, ara kare enterpolasyonu | Var (video nesne takibi), CVAT kadar olgun değil | Zahmetli, hataya açık |
| Bounding box + öznitelik | Kutu başına öznitelik (durum, görünürlük) | Var (etiket arayüzü yapılandırmasıyla) | Elle |
| Nesne takibi (track) | Yerleşik "track" nesneleri | Video bölgeleri üzerinden | Yok |
| İki etiketleyici + inceleme | Görev/iş (job) aşamaları (annotation → validation/acceptance); ayrıntılı inceleme/uyum özellikleri sürüme göre — doğrulanmalı | Inceleme iş akışının gelişmiş kısmı ağırlıkla ücretli sürümde — doğrulanmalı | Kendi sürecimiz (bu fazın doğrulayıcısı + `data-agreement`) |
| Dışa aktarım | CVAT video XML, COCO, YOLO, MOT, Datumaro… | Kendi JSON'u, COCO/YOLO (görüntü ağırlıklı) | Doğrudan `ppe-annotation/1` |
| Kendi sunucunda çalışma | Docker Compose (birden çok servis) | pip veya Docker (tek süreç + veritabanı) | Gerekmez |
| Veri gizliliği | Yerel; bulut depolama entegrasyonları ve otomatik etiketleme servisleri **kapalı** tutulmalı; yalnız 127.0.0.1/LAN | Yerel; telemetri/analitik ayarı kurulumda doğrulanıp kapatılmalı | En iyi (dış bileşen yok) |
| Lisans | MIT (çekirdek) — doğrulanmalı | Apache-2.0 (Community) — doğrulanmalı | — |
| Kaynak tüketimi | Yüksek: birkaç container; 8 GB RAM'li makinede dashboard + test yığınıyla birlikte zor | Orta/düşük | Çok düşük |
| Mac / Windows | Docker Desktop ile her ikisi (Windows'ta WSL2) | Her ikisi | Her ikisi |
| Riskler | Hesap/oturum yönetimi, yedekleme, kişisel veriyi ikinci bir veritabanında tutma; port açıkta kalırsa veri sızıntısı | Video takibi ve iki aşamalı incelemenin Community sürümünde yetersiz kalması | Ölçeklenmez; etiketleyici hatası artar |

## 3. Öneri

1. **Tercih: CVAT (self-hosted)** — video + iz + öznitelik ihtiyacına en uygun olanı. Koşullar: yalnız onaylı,
   şifreli makinede; yalnız `127.0.0.1` (gerekirse LAN + TLS, dashboard ile aynı ilkeler); bulut depolama,
   otomatik etiketleme (serverless) ve dış analitik kapalı; ayrı kullanıcı hesapları; CVAT verisi de saklama/imha
   politikasına tabi (DATA_GOVERNANCE §5).
2. **Kaynak sınırı:** 8 GB iMac'te dashboard yığını ve testlerle birlikte çalıştırılmamalı; etiketleme için ayrı
   veya daha güçlü bir makine (veya Windows dizüstünde yalnız etiketleme oturumlarında) önerilir.
3. **Alternatif:** küçük ilk veri kümesi (ör. birkaç saatlik izinli kayıt) için Label Studio Community daha hafiftir;
   iki aşamalı inceleme bu repodaki doğrulayıcı + `data-agreement` ile dışarıda yürütülür.

## 4. Entegrasyon planı (uygulanmadı)

1. G1–G11 kapısı (DATA_GOVERNANCE) açılmadan araç **gerçek veriyle** kurulmaz. Önce yalnız sentetik videoyla
   (ör. `temporal.py` benzeri üretilmiş görüntü) deneme kurulumu yapılabilir.
2. Etiket şeması: kişi kutusu + iz; öznitelikler `visibility, occlusion, truncated, annotator_confidence,
   person_status, not_evaluable_reason, helmet_status, vest_status`; baret/yelek kutuları ayrı etiket + kişi
   ilişkisi (grup/iz kimliği); incident aralıkları ayrı görev veya iz öznitelik aralığı olarak.
3. Dönüştürücü: araç dışa aktarımı (CVAT video XML veya Datumaro) → `ppe-annotation/1`. **Bu fazda yazılmadı**
   (araç seçimi onaylanmadan biçime bağlanmamak için); seçimden sonra sentetik dışa aktarım örneğiyle test edilerek
   eklenir ve çıktısı `data-validate` ile doğrulanır.
4. Önetiket: yalnız yerel `best.pt`; her önetiket `label_origin = model_prelabel` ve insan onayı zorunlu (doğrulayıcı
   onaysız önetiketi reddeder). Önetiketin yanlılık riski (modelin hatalarını GT'ye taşıma) IAA'da ayrıca izlenir;
   test kümesinde önetiket **kullanılmaması** önerilir.
5. Yedek ve imha: araç veritabanı ve medya deposu şifreli yedeklenir; proje sonunda imha kaydıyla silinir.
