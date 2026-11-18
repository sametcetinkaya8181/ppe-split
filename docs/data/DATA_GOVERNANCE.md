# Veri yönetişimi (FAZ 10-A.3)

**Okuyucu:** veri sorumlusu / KVKK sorumlusu, hukuk, İSG, ürün sahibi, veri mühendisi.
**Bu belge hukuki görüş değildir.** Hukuki uygunluk kararını geliştirici ekip vermez; veri sorumlusu ve hukuk verir.

## 0. Varsayılan durum: **DATA_COLLECTION_BLOCKED**

Gerçek çalışan görüntüsü içeren hiçbir veri, §1'deki kapının tüm maddeleri **yazılı olarak** onaylanana kadar
**toplanmaz, kopyalanmaz, etiketlenmez, başka bir servise yüklenmez**. Bugün (2026-11-02) hiçbir madde onaylı
değildir; KVKK ve lisans kararları **NO-GO** (FAZ 10-A.2). Bu fazda gerçek veri toplanmadı.

## 1. Veri toplama kapısı (hepsi gerekli)

| # | Madde | Karar sahibi | Kanıt | Durum |
|---|---|---|---|---|
| G1 | Toplama amacı yazılı: model doğruluğunun **ölçülmesi** (ve ayrı kararla eğitimi) — izleme/disiplin değil | Veri sorumlusu + ürün sahibi | İmzalı amaç beyanı | BEKLİYOR |
| G2 | Hukuki dayanak (KVKK md. 5/6) belirlendi | Veri sorumlusu + hukuk | Hukuk görüşü | BEKLİYOR |
| G3 | Aydınlatma (md. 10): kamera konumu, amaç, saklama, erişen roller, başvuru kanalı; sahada levha | Veri sorumlusu | Aydınlatma metni + levha fotoğrafı (kişisiz) | BEKLİYOR |
| G4 | Kamera görüş alanı yalnız çalışma alanı; dinlenme/soyunma/WC hariç | İSG + saha | Görüş alanı kaydı | BEKLİYOR |
| G5 | Kişi sayısı/süre minimize: yalnız gerekli kamera ve zaman aralıkları | Veri sorumlusu + veri mühendisi | Örnekleme planı | BEKLİYOR |
| G6 | Erişim listesi (kimler, hangi rol) | Veri sorumlusu | Erişim matrisi (§3) | BEKLİYOR |
| G7 | Şifreli saklama yeri hazır ve test edildi (§4) | BT | Kurulum ve geri yükleme kaydı | BEKLİYOR |
| G8 | Saklama süresi ve imha yöntemi onaylı (§5) | Veri sorumlusu | Saklama politikası | BEKLİYOR |
| G9 | Etiketleme aracı ve barındırma onaylı (yerel, dış aktarım yok) | BT + veri sorumlusu | ANNOTATION_TOOL_REVIEW kararı | BEKLİYOR |
| G10 | Model/araç/veri lisansları değerlendirildi | Hukuk | Lisans kararı | **NO-GO** |
| G11 | Etiketleyici/inceleyici gizlilik taahhüdü ve eğitimi | Veri sorumlusu + İSG | İmzalı taahhüt, eğitim kaydı | BEKLİYOR |

Kapı açıldığında her medya kaydına `authorization.status = APPROVED` ve onay **referans numarası** yazılır
(belge numarası; belgenin kendisi değil). Referanssız APPROVED doğrulayıcıda hatadır.

## 2. Roller

| Rol | Sorumluluk | Veriye erişim |
|---|---|---|
| Veri sorumlusu (işveren temsilcisi) | Amaç, dayanak, aydınlatma, saklama, başvurular, kapı onayı | Gerektiğinde |
| Hukuk / KVKK sorumlusu | Dayanak ve lisans görüşü | Hayır (belgeler) |
| Veri mühendisi | Toplama, aktarım, manifest, saklama, imha uygulaması | Evet (ham) |
| Etiketleyici / inceleyici / hakem | Etiket | Yalnız atanan medya, araç içinden |
| ML geliştirici | Değerlendirme | Dondurulmuş kümeye salt okuma; ham veriyi kopyalamaz |
| İSG uzmanı | Etiket rehberi ve eşik onayı | Gerektiğinde örnek inceleme |
| BT | Saklama altyapısı, yedek, erişim kontrolü | Altyapı (içerik değil) |

Anonim rol kimlikleri (`ann_A`, `rev_B`) ile gerçek kişi eşleşmesi yalnız veri sorumlusunda, veri kümesi dışında
tutulur.

## 3. Erişim kuralları

- En az yetki; ad bazlı erişim listesi; ortak hesap yok; erişimler kayıtlı (§8).
- Ham veri yalnız onaylı saklama yerinde; dizüstü/USB/kişisel bulut kopyası yok.
- Ekran görüntüsü, rapor veya sohbet/issue aracına görüntü konmaz; raporlarda yalnız sayı ve anonim kimlik.

## 4. Saklama yeri ve şifreleme

- **Git dışı.** Repo'da yalnız şema, kod, sentetik fikstür bulunur (`.gitignore`: `detector/evaluation/datasets/`,
  `detector/evaluation/output/`, `*.mp4`). Gerçek veri kümesinin manifesti de Git dışındadır.
- Şifreli disk (BitLocker / FileVault) **ve** yalnız yetkili kullanıcılara açık dizin (Windows ACL; bkz.
  `scripts/windows_acl.ps1`) — ikisi de doğrulanmadan G7 açılmaz.
- Yedekler şifreli (açık anahtarlı; `docs/BACKUP_RESTORE.md` "Yedek şifreleme" tasarımı); özel anahtar veri
  kümesiyle aynı makinede tutulmaz.
- Önerilen dizin düzeni (Git dışı, şifreli birimde): `ppe-data/<dataset_id>/<dataset_version>/{media,annotations,
  manifest.json,reports}`.

## 5. Saklama ve imha

- Süreler **veri sorumlusu** tarafından belirlenir (öneri yapılmaz). Ham medya, etiket, manifest, değerlendirme
  çıktısı ve yedekler için ayrı süre yazılır.
- İmha prosedürü: (1) silinecek kayıt listesi manifestten çıkarılır, (2) **izole kopyada** imha betiği denenir ve
  doğrulanır, (3) veri sorumlusu onayıyla gerçek konumda uygulanır, (4) yedeklerdeki karşılıkların süresi dolunca
  imha edilir, (5) imha kaydı (tarih, kapsam, onaylayan, manifest özeti) tutulur.
- **Otomatik silme/retention ana veriye (üretim PostgreSQL, snapshot, outbox) bu fazda uygulanmaz.** Veri kümesi
  imha aracı yazılırsa önce sentetik/izole kopyada test edilir.

## 6. Geri çekme (withdrawal) ve başvurular

- İlgili kişi başvurusu veya izin geri çekilmesi → ilgili medya kayıtları `authorization.status = WITHDRAWN`.
  Doğrulayıcı WITHDRAWN kaydı olan veri kümesini **FAIL** eder; kayıt çıkarılıp **yeni sürüm** üretilmeden kullanılamaz.
- Dondurulmuş test kümesinden kayıt çıkarmak MAJOR sürümdür; eski sonuçlar yeni sürümle karşılaştırılmaz ve rapora
  not düşülür.
- Başvuruya yanıt için görüntü gösterilecekse başka kişiler maskelenmelidir (araç yok — PRD FR-13).

## 7. Dış hizmetlere aktarım

- Varsayılan: **yok.** Bulut etiketleme (SaaS), bulut depolama, dış model API'si, yurt dışı sunucu kullanılmaz.
- Gerekirse ayrı hukuk kararı (yurt dışı aktarım dahil), sözleşme ve G-kapısı güncellemesi gerekir.
- Önetiket (model_prelabel) yalnız yerel modelle; hiçbir görüntü dışarı gönderilmez. Ultralytics analitiği kapalı
  (`YOLO_OFFLINE=1`).

## 8. Köken (provenance) ve denetim

Her medya: `source_ref` (aktarım kaydı no), `site_id/camera_id/session_id/source_video_id/recorded_day`, SHA-256,
izin ve lisans durumu. Her etiket: sürüm, etiketleyici/inceleyici/hakem rolleri, inceleme zamanı. Her veri kümesi
sürümü: manifest özeti, test dondurma özeti, doğrulama raporu özeti. Her değerlendirme sonucu hangi manifest
özetine ait olduğunu yazar. Erişim ve imha kayıtları veri sorumlusunda tutulur.

## 9. Lisans kontrolü (açık veri kümeleri)

Her dış veri kümesi için: lisans metni, ticari kullanım, türev/model ağırlığı yükümlülükleri, içindeki kişilerin
rıza durumu. Doğrulanmadıysa `LICENSE_UNVERIFIED` (doğrulayıcıda BLOCKED). Yeni veri kümesi otomatik
**indirilmez**. Bugünkü durum: DATASET_CARD.md.

## 10. Bu fazın uyumu

| Kural | Durum |
|---|---|
| Gerçek çalışan görüntüsü toplandı / etiketlendi / yüklendi | **Hayır** |
| Yeni veri kümesi indirildi | **Hayır** |
| Ana veri (PostgreSQL, snapshot, outbox) değiştirildi | **Hayır** |
| Repo'ya eklenen veri | Yalnız sentetik fikstür kodu ve JSON Schema |

## 11. Hata odaklı veri (E-01 – E-04) için ek kısıtlar

- Profil verisi (örtülü baret, iş makinesi sahneleri, kadraj kenarı, örtülme) de **kişisel veridir**; §1 kapısı aynen
  geçerlidir. "Kişisiz" görünen zor negatif kareler (iş makinesi) de arka planda kişi veya plaka içerebilir → aynı kapı.
- **SteelBench videoları (V1–V3) hiçbir eğitim, doğrulama veya test kümesine girmez:** lisansları doğrulanmadı
  (`LICENSE_UNVERIFIED`) ve SteelBench'in regresyon kapısı olarak bağımsız kalmaları gerekir; eğitime girerlerse
  regresyon sonucu anlamını yitirir.
- Construction-PPE modelin eğitim kümesidir; E-01–E-04 ölçümünde **test** olarak kullanılamaz (FAZ 8 sızıntı bulgusu).
- Hedefli veri toplama (ör. yalnız koyu baretli çalışan) kişiyi hedefleme sayılmamalı: sahne/koşul bazlı örnekleme
  yapılır, kişi seçilmez; örnekleme planı veri sorumlusuna onaylatılır (G5).
- Sentetik örnekler yalnız hat testleri içindir; doğruluk metriğine katılmaz.
