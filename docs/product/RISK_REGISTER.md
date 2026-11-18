# Risk sicili (FMEA benzeri)

**Sürüm:** taslak v0.1 — 2026-11-02 (FAZ 10-A.2)
**Kapsam:** ürünün sahada kullanılmasıyla ortaya çıkabilecek **güvenlik, gizlilik, hukuki, operasyonel ve insan
faktörü** riskleri. Puanlar geliştirici ekibin **ön değerlendirmesidir**; şiddet (Ş) puanları İSG uzmanı tarafından
onaylanmalıdır.

Puanlama (1–5): **Ş** şiddet (5 = ciddi yaralanma riskini artırır / yasal ihlal), **O** olasılık (5 = kanıtlanmış
veya sık), **T** tespit edilebilirlik (5 = fark edilmeden geçer). **RÖS** = Ş × O × T. Kanıt düzeyi: *ölçüldü*
(gerçek veri), *sentetik*, *gözlem* (etiketsiz videoda tek gözden geçiren), *varsayım*.

## 1. Özet (RÖS'e göre)

| ID | Risk | Ş | O | T | RÖS | Durum |
|---|---|---|---|---|---|---|
| R-01 | Kapsam açığı: değerlendirilemeyen kişiler sessizce kaçırılır | 5 | 5 | 4 | **100** | Açık |
| R-02 | Ürüne aşırı güven (otomasyon yanlılığı), manuel denetimin azalması | 5 | 4 | 4 | **80** | Açık |
| R-03 | "Kamera/analiz yok" durumunun "uygun/ihlal yok" sanılması | 5 | 3 | 4 | **60** | Kısmen azaltıldı |
| R-04 | Gerçek ihlalin kaçırılması (model FN: sahte baret, küçük kafa, örtülme) | 5 | 4 | 3 | **60** | Açık |
| R-05 | Hukuki engel: KVKK dayanak/aydınlatma/saklama kararı yok | 5 | 5 | 2 | **50** | NO-GO |
| R-07 | Yanlış alarm yükü → alarm yorgunluğu → gerçek uyarının göz ardı edilmesi | 4 | 4 | 3 | **48** | Açık |
| R-06 | Lisans: Ultralytics AGPL-3.0, `best.pt` ve eğitim verisi hakları | 4 | 5 | 2 | **40** | NO-GO |
| R-08 | Kimlik değişimi (ID switch) → duplicate alarm, yanlış "bitti" | 3 | 4 | 3 | **36** | Açık |
| R-09 | Kişisel veri sızıntısı (geniş dosya izinleri, şifresiz yedek, disk şifrelemesi doğrulanmadı) | 4 | 3 | 3 | **36** | Açık (FAZ 10-A.1) |
| R-10 | Sistemin disiplin/yaptırım için kötüye kullanımı | 4 | 3 | 3 | **36** | Açık (idari kontrol) |
| R-11 | Bayat kare / gecikmeli analiz canlı sanılır | 4 | 2 | 3 | **24** | Kısmen azaltıldı |
| R-12 | Yanlış durum etiketlemesi (run/incident) | 3 | 2 | 3 | **18** | Kısmen azaltıldı |
| R-14 | Saha donanımında kapasite/kararlılık yetersizliği | 3 | 3 | 2 | **18** | Doğrulanmadı |
| R-13 | Olay kaybı (outbox öncesi çökme, disk dolu) | 4 | 1 | 2 | **8** | Azaltıldı |

## 2. Ayrıntı

### R-01 — Kapsam açığı (değerlendirilemeyen kişi)
- **Arıza modu:** Güvenilirlik kapısı (güven < 0.40, boy < 60 px, kadraj kenarına ≤ 6 px) kişiyi `ANALIZ`'de bırakır;
  o kişi için hiç ihlal üretilmez.
- **Kanıt:** *ölçüldü (stok video, saha değil)* — FAZ 10-A 4 sa soak: V2 dosyada track-karelerin **%91.3**'ü, V2 ağ
  benzetiminde %91.2'si değerlendirilemedi; V1'de %23.6. *Gözlem* — FAZ 8 E-03: yakın plan işçi 286/286 karede ANALIZ.
  *Sentetik* — uzak (50 px) baretsiz kişi: hiç alarm yok.
- **Güvenlik etkisi:** O kişi baretsiz olsa da sistem susar. Operatör kişi tablosunu görmezse "ihlal yok" sanır.
- **Mevcut kontroller:** Analiz kapsamı paneli, "≥10 sn değerlendirilemeyen kişi" uyarısı, "Uygun sayılmaz" notu.
- **Boşluklar:** Kişi etiketi "Analiz" (anlamı belirsiz); geçmiş ve istatistikte kapsam görünmez; kabul eşiği yok;
  kamera yerleşim rehberi yok.
- **Önlem/sahip:** FR-04 (Frontend + İSG); kapsam eşiği K-04 (İSG); kamera yerleşimi ve ölçümü 10-A.4 (ML); kapsam
  düşük alanda manuel denetim (PRD §5, İSG).
- **GO/NO-GO:** Kapsam eşiği onaysız → BLOCKED.

### R-02 — Otomasyon yanlılığı / aşırı güven
- **Arıza modu:** Kullanıcılar sistemi "güvenlik kontrolü" olarak görür, devriye azaltılır.
- **Kanıt:** *varsayım* (insan faktörü literatüründe bilinen etki; bu projede ölçülmedi).
- **Önlem/sahip:** PRD §5 manuel prosedür, arayüzde "aday" dili, operatör eğitimi, pilotta devriye sıklığının
  korunması (İSG + Ürün). Görüşmelerde sorulacak (STAKEHOLDER_INTERVIEW_GUIDE).

### R-03 — "Analiz yok" ≠ "uygun"
- **Arıza modu:** Kopan kamera, donmuş görüntü veya çalışmayan dedektör ekranda boş/yeşil görünür.
- **Kanıt:** *sentetik/simülasyon* — FAZ 10-A R1–R4: ~6 sn'de "izlenemiyor", run `disconnected`.
- **Mevcut kontroller:** "İZLENEMİYOR" etiketi, "bağlantı yok" karesi, `disconnected` durumu, "Dedektör bağlı değil".
- **Boşluk:** Gerçek RTSP ile doğrulanmadı; istatistik ekranı izlenmeyen süreyi göstermez.
- **Önlem/sahip:** Gerçek kamera testi (Platform), izlenmeyen süre raporu (Backend, K-08).

### R-04 — Kaçırılan gerçek ihlal (model hatası)
- **Kanıt:** *ölçüldü (dağılım içi)* — baret 16–32 px recall 0.68–0.79; Person recall 0.81; `no_helmet` recall 0.23
  (karara eklenmedi). *Sentetik* — kare başına sahte baret olasılığı ≥ 0.4 ise ihlal hiç algılanmaz.
- **Boşluk:** Saha recall'ü bilinmiyor (etiketli veri yok).
- **Önlem/sahip:** 10-A.3/4 (Veri + ML); incident recall eşiği K-01 (İSG); kaçırılan ihlal bildirimi FR-08.

### R-05 — KVKK
- **Durum:** Hukuki dayanak, aydınlatma metni, saklama süresi, VERBİS, ilgili kişi başvuru süreci **kararlaştırılmadı**.
  Snapshot'lar süresiz saklanıyor (otomatik imha yok, FR-12).
- **Önlem/sahip:** Veri sorumlusu + hukuk (karar); Backend (FR-11/12/13 uygulaması). Karar olmadan gerçek çalışan
  videosu toplanmaz. **NO-GO.**

### R-06 — Lisans
- **Durum:** `ultralytics` 8.4.183 AGPL-3.0; `best.pt` metadata AGPL-3.0; eğitim verisi hakları doğrulanmadı.
- **Önlem/sahip:** Hukuk + ürün: AGPL uyumu / Enterprise lisans / model değişimi kararı. **NO-GO.**

### R-07 — Yanlış alarm yükü
- **Kanıt:** *gözlem* — etiketsiz 51.5 sn videoda 2 alarm, ikisi de aday yanlış alarm (örtülmüş koyu baret; makine
  kolu "kişi"). *Sentetik* — ~1 sn ilişkili baret kaçırmalarında 0.95 tespit olasılığıyla bile ~14 yanlış/kişi-saat.
- **Boşluk:** Sahada yanlış alarm oranı bilinmiyor; işaretleme kaydı yok (FR-07).
- **Önlem/sahip:** FA eşiği K-02 (İSG + işletme); FR-07; 10-A.5 örtülme/kişi FP çalışması.

### R-08 — ID switch / duplicate
- **Kanıt:** *sentetik* — kadrajdan çıkıp dönme, kesişme → 2 incident, gerçek ihlal sürerken yanlış `end`.
  *Gözlem* — V1: ~2–3 kişi için 6 iz ID'si.
- **Önlem/sahip:** K-06 ölçümü (ML), FR-17 (ML + Backend). Raporlarda "iz ID gerçek kişi değildir" notu mevcut.

### R-09 — Veri sızıntısı
- **Kanıt:** FAZ 10-A: proje klasörü `BUILTIN\Users` değiştirme izni; yedekler şifresiz; BitLocker doğrulanmadı.
- **Önlem/sahip:** FAZ 10-A.1 §5–6 (ACL aracı, şifreleme tasarımı) — uygulama kullanıcı onayı bekliyor (BT).

### R-10 — Kötüye kullanım (disiplin, takip)
- **Önlem/sahip:** PRD §2 kapsam dışı listesi; kişi kimliklendirme yok (teknik); kullanım politikası ve eğitim
  (İşletme + veri sorumlusu); denetim kaydı (mevcut).

### R-11 — Bayat kare
- **Mevcut kontroller:** kare yaşı ve sağlık (`last_frame_age_sec`, `stalled`), donma ~6 sn'de görünür (simülasyon).
- **Boşluk:** Operatör ekranında gecikme ölçümü yok (K-07).

### R-12 — Yanlış durum etiketi
- **Mevcut kontroller:** 0006 `disconnected`, bitiş nedeni, rapor bariyeri, uzlaştırma aracı (dry-run).
- **Açık:** Windows ana DB'de 4 sahipsiz `running` run (uzlaştırma onayı bekliyor).

### R-13 — Olay kaybı
- **Kanıt:** *ölçüldü (izole)* — FAZ 7/9 arıza testleri, 4 sa soak 4 656 olay 0 kayıp 0 çift; FAZ 10-A.1 bellek
  testleri 0 kayıp.
- **Kalan:** outbox COMMIT öncesi çökme; disk dolu + bellek kuyruğu dolu (sayılır).

### R-14 — Saha donanımı
- **Kanıt:** dizüstü (RTX 3050) 4 kamera, 4 sa PASS; hedef saha bilgisayarı ve gerçek RTSP çözme ölçülmedi; 24 sa
  soak koşulmadı.
- **Önlem/sahip:** Platform + BT (NFR-02/03).

## 3. Gözden geçirme

Risk sicili her faz sonunda ve her kabul eşiği kararından sonra güncellenir. Şiddet puanlarını İSG uzmanı onaylar:
onaylayan / tarih: ____________________
