# PPE Safety Monitor — Ürün Gereksinimleri Belgesi (PRD)

**Sürüm:** taslak v0.1 — 2026-11-02 (FAZ 10-A.2)
**Okuyucu:** ürün sahibi, İSG uzmanı, saha/işletme yöneticisi, veri sorumlusu/hukuk, BT, geliştirme ekibi.
**Durum:** Taslak. Hiçbir paydaş görüşmesi yapılmadı; hiçbir eşik, KVKK veya lisans kararı onaylanmadı. Bu belge
saha kullanımına **izin vermez**. Gerçek şantiye pilotu: **NO-GO**.

İlgili belgeler: [PERSONAS.md](PERSONAS.md) · [USER_STORIES.md](USER_STORIES.md) ·
[FEATURE_GAP_MATRIX.md](FEATURE_GAP_MATRIX.md) · [RISK_REGISTER.md](RISK_REGISTER.md) ·
[ACCEPTANCE_METRICS.md](ACCEPTANCE_METRICS.md) · [STAKEHOLDER_INTERVIEW_GUIDE.md](STAKEHOLDER_INTERVIEW_GUIDE.md)

---

## 1. Problem

Şantiyelerde baret ve reflektif yelek kullanımı, İSG uzmanının ve saha şeflerinin sınırlı sayıda gözle, aralıklı
devriyeyle denetlediği bir yükümlülüktür. Kameralar çoğu sahada zaten vardır, ama görüntüyü sürekli izleyen kimse
yoktur.

**Ürünün çözmeye çalıştığı problem (hipotez — paydaş görüşmesiyle doğrulanmadı):** İSG ekibinin, kameralı çalışma
alanlarında baret/yelek eksikliği **adaylarını** zamanında görmesi, bunları hızla doğrulayıp yerinde müdahale etmesi
ve dönemsel olarak hangi kamera/alanda sorunun yoğunlaştığını raporlaması.

**Ürün bir güvenlik kontrolü değildir; bir dikkat yönlendirme (triage) aracıdır.** Model hata yapar (FAZ 8:
`no_helmet` recall 0.23; makine kolu "kişi" algılandı; örtülmüş koyu baret ihlal sayıldı). Sistemin "ihlal yok"
göstermesi, sahada ihlal olmadığı anlamına gelmez.

## 2. Ürünün yapacağı ve yapmayacağı işler

| Yapar (kapsam içi) | Yapmaz (kapsam dışı — eklenmez) |
|---|---|
| Kameradaki kişide baret/yelek eksikliği **adayı** üretir, kanıt görüntüsüyle yetkili kişiye gösterir | Yüz tanıma, kişi kimliklendirme, çalışan eşleştirme |
| Kamera/analiz kapsamını ve "değerlendirilemeyen kişi" sayısını görünür kılar | Kişi bazında puanlama, performans takibi, sıralama |
| Kamera bağlantısı koptuğunda / görüntü donduğunda "izlenemiyor" gösterir | Otomatik disiplin, ceza, yaptırım, tutanak |
| İhlal geçmişi, run raporları ve günlük istatistik sunar | Tek başına iş durdurma veya güvenlik kararı |
| Rol ve kamera bazlı erişim, denetim kaydı | İnsan İSG denetiminin, devriyenin veya yasal sorumlunun yerine geçme |
| Yerel çalışır; görüntüyü üçüncü tarafa göndermez | Onaysız e-posta/SMS/WhatsApp ile görüntü iletimi; bulut aktarımı |
| — | Dinlenme, soyunma, WC alanlarının izlenmesi; ses kaydı |

**Temel ürün ilkesi:** "Kamera yok", "analiz yok" veya "kişi değerlendirilemedi" durumları **asla** "uygun" veya
"ihlal yok" gibi gösterilmez ve bu durumlar raporlarda ayrı bir kategori olarak sayılır (bkz. RISK R-01, R-03).

## 3. Kullanıcılar ve roller (özet; ayrıntı PERSONAS.md)

| Persona | Ürün rolü (bugün) | Kullanım |
|---|---|---|
| P1 Şantiye İSG uzmanı / sorumlusu | `supervisor` (atanan kameralar) | Uyarıyı görür, kanıtı inceler, sahada müdahale eder, yanlış alarmı not eder |
| P2 İzleme operatörü | `supervisor` (gerekirse yeni "reviewer" rolü — karar bekliyor) | Ekranı izler, adayları ilk elemeden geçirir, İSG'ye iletir |
| P3 Saha / proje yöneticisi | `viewer` | Görüntüsüz istatistik ve eğilim |
| P4 İşletme admin'i (BT) | `admin` | Kullanıcı, kamera, yedek, güncelleme, sağlık |
| P5 Veri sorumlusu / KVKK sorumlusu | sistem kullanıcısı olmayabilir | Amaç, saklama, erişim, aydınlatma, başvuru kararları |

**Öncelikli hedef persona (öneri, doğrulanmadı):** P1 — tek veya birkaç sahada çalışan, 2–8 kamerayı kendi
izleyen İSG uzmanı. Gerekçe: mevcut ürün tek bilgisayarda, yerel, kamera bazlı yetkiyle çalışıyor (çok saha /
merkezi izleme mimarisi yok); İSG uzmanı hem kullanıcı hem kabul eşiklerini belirleyecek kişi. Müşteri türleri
(tek saha KOBİ, çok kameralı yüklenici, OSGB/İSG hizmet firması, merkezden çok saha) arasından seçim
**paydaş görüşmesiyle** teyit edilmelidir.

## 4. Kullanım senaryoları

| # | Senaryo | Akış | İnsan kontrolü |
|---|---|---|---|
| S1 | Canlı uyarı | Kamera → aday ihlal → canlı ekranda uyarı + kanıt → İSG uzmanı inceler → sahada sözlü uyarı/eğitim | Her uyarı insan tarafından doğrulanır; sistem kararı tek başına işlem doğurmaz |
| S2 | Vardiya sonu gözden geçirme | Geçmiş ihlaller → filtre → kanıt → yanlış alarmları ayıklama → not | Doğrulama sonucu kaydedilebilmeli (**bugün eksik**, bkz. FR-07) |
| S3 | Haftalık eğilim | İstatistikler → kamera/alan bazında tekil ihlal sayısı → eğitim/düzenleme kararı | Sayılar "doğrulanmış" ve "doğrulanmamış aday" olarak ayrılmalı (**bugün ayrılamıyor**) |
| S4 | Kapsam / sağlık takibi | Kamera "izlenemiyor" / "değerlendirilemeyen kişi" uyarısı → kamera açısı/bağlantı düzeltme | Kapsam düşükse o alan için manuel denetim sıklaştırılır |
| S5 | Sistem arızası | Dashboard/dedektör/kamera arızası → olaylar outbox'ta bekler → dönüşte teslim | Arıza süresince **mevcut manuel İSG prosedürü aynen** uygulanır |

**Amaç dışı kullanım (açıkça yasak):** çalışan performans değerlendirmesi, disiplin kanıtı olarak tek başına
kullanım, belirli bir kişiyi takip etme, işyeri dışı alanların izlenmesi, ses kaydı, görüntülerin pazarlama/eğitim
amaçlı izinsiz kullanımı.

## 5. Manuel güvenlik prosedürü (ürünle birlikte zorunlu)

1. Sistem çalışırken de İSG devriyeleri ve mevcut denetim sıklığı **azaltılmaz** (kabul eşikleri onaylanıp gölge
   mod pilotunda kanıtlanana kadar).
2. Kamera "izlenemiyor", dashboard erişilemez veya bir kamerada "değerlendirilemeyen kişi" oranı yüksekse o alan
   **izlenmiyor** kabul edilir.
3. Uyarı insan tarafından doğrulanmadan kimse hakkında işlem yapılmaz; doğrulanan ihlalde müdahale sözlü/eğitim
   odaklıdır (işverenin İSG prosedürüne göre).
4. Yanlış alarm, kaçırılan ihlal (sahada görülüp sistemde olmayan) ve kamera sorunları kayda geçer (bugün sistem
   içinde kayıt alanı yok → geçici olarak manuel form; FR-07).
5. Görüntü erişim talepleri ve itirazlar veri sorumlusunun belirlediği kanaldan alınır.

## 6. Gereksinimler

Durum tanımları: **mevcut** = kod ve otomatik test repo'da doğrulandı · **kısmi** = bir kısmı var (ör. API var, ekran
yok) · **eksik** = repo incelemesinde bulunmadı (kanıt FEATURE_GAP_MATRIX'te) · **doğrulanmamış** = kod var ama
gerçek saha/kamera/veriyle kanıt yok veya repo incelemesiyle karar verilemiyor.
Öncelik: **P0** pilot öncesi zorunlu · **P1** 10-A.9 kurulundan önce · **P2** paralel/ihtiyaç doğrulanınca · **P3** sonra.

### 6.1 İşlevsel gereksinimler

| ID | Gereksinim | Durum | Öncelik | Sahip | Bağımlılık | Kabul testi | Kullanıcı değeri | Kaynak / gerekçe | GO/NO-GO bağı |
|---|---|---|---|---|---|---|---|---|---|
| FR-01 | Kişi başına baret/yelek eksikliği adayı üret (start/ongoing/end) | mevcut (algoritma) / **doğrulanmamış** (saha doğruluğu) | P0 | ML | 10-A.3/4 etiketli veri | Dedektör testleri (PASS); saha incident recall ≥ onaylı eşik | Uyarı üretimi | `ppe_core.PPEMonitor`, FAZ 8 §6–7 BLOCKED | Saha doğruluğu kanıtı olmadan NO-GO |
| FR-02 | Kanıt görüntüsü (snapshot) yalnız yetkili rol ve kapsamdaki kamera için | mevcut | P0 | Backend | — | `test_access_control`, kapsam dışı 404 | Doğrulama için kanıt | `app.py` `/snapshots/{name}` | — |
| FR-03 | Canlı izleme (kamera karoları, kişi durumu) | mevcut | P0 | Frontend | — | Tarayıcı testi 30/30 (FAZ 10-A) + manuel liste | Anlık farkındalık | `/ws`, `/api/state`, `index.html` | Manuel kabul YAPILMADI |
| FR-04 | Değerlendirilemeyen kişi ve analiz kapsamı görünür; "Uygun" sayılmaz | kısmi | **P0** | Frontend + ML | İSG kapsam eşiği | Kapsam paneli testi PASS; **etiket**: "Analiz" → "Değerlendirilemiyor (neden)" ve tile/rapor sayımında ayrı | Sessiz kör noktayı görünür kılar | `renderCoverage`, `renderPeople` ("Analiz" etiketi belirsiz), FAZ 10-A §7 (%91) | Kapsam eşiği onaysız → BLOCKED |
| FR-05 | Kamera kopması/donması "izlenemiyor"; run `disconnected` | mevcut (simülasyon) / **doğrulanmamış** (gerçek RTSP) | P0 | Backend + Dedektör | Gerçek RTSP kamera | R1–R4 simülasyon PASS; gerçek kamera kopma/yanlış parola testi | "Kamera yok" ≠ "ihlal yok" | migration 0006, FAZ 10-A §5–6 | Gerçek RTSP testi BLOCKED |
| FR-06 | İhlal geçmişi, filtre, detay, run raporu, günlük istatistik | mevcut | P1 | Backend + Frontend | — | Dashboard testleri; manuel liste §3–4 | Vardiya/hafta değerlendirmesi | `/api/history/*` | — |
| FR-07 | **İnsan doğrulama kaydı**: aday → Onaylandı / Yanlış alarm / Belirsiz + not + kim/ne zaman; model çıktısı değişmeden ayrı tutulur | **eksik** | **P0** | Backend + Frontend + İSG | Rol kararı (FR-09) | API + UI testi; audit kaydı; orijinal olay değişmez | Yanlış alarm ölçümü, KPI "insan doğrulama süresi", hesap verebilirlik | `models.py`'de review alanı/tablosu yok | Gölge pilotta insan etiketi zorunlu → NO-GO |
| FR-08 | Kaçırılan ihlal bildirimi (sahada görülen, sistemde olmayan) | **eksik** | P1 | Frontend + İSG | FR-07 | Form + rapor | Recall'un sahada kaba izlenmesi | — | Pilot metrik kanıtı |
| FR-09 | Rol modeli: viewer / supervisor / admin; kamera kapsamı | mevcut; **reviewer/operatör rolü ihtiyacı doğrulanmadı** | P1 | Ürün + Backend | Paydaş görüşmesi | RBAC testleri (PASS) | En az yetki | `auth/store.py` ROLES | — |
| FR-10 | Kullanıcı yönetimi ekranı | kısmi (API + CLI var, UI yok) | P2 | Frontend | — | UI testi | Admin işini kolaylaştırır | `/api/admin/users`; `index.html` çağırmıyor | — |
| FR-11 | Denetim kaydı görüntüleme/dışa aktarma | kısmi (API var, UI yok; saklama süresi yok) | P1 | Backend + Veri sorumlusu | Saklama kararı | API testi; saklama politikası testi | KVKK hesap verebilirlik | `/api/admin/audit`, PRIVACY_READINESS "Eksik" | KVKK |
| FR-12 | Saklama ve imha (snapshot, olay, audit, yedek) | **eksik** (yalnız rapor/karantina aracı) | **P0** | Backend + Veri sorumlusu | KVKK saklama süresi kararı | İzole imha testi + imha kaydı | Yasal uyum, veri minimizasyonu | `maintenance/storage.py` "silme YOKTUR" | KVKK NO-GO |
| FR-13 | Görüntü erişim talebinde üçüncü kişi maskeleme | **eksik** | P1 | Backend | Veri sorumlusu kararı | Maskeleme testi | İlgili kişi hakları | PRIVACY_READINESS | KVKK |
| FR-14 | Bildirim (ses/e-posta/mobil) | **eksik** — kapsam kararı bekliyor | P2 | Ürün + Veri sorumlusu | İçerik minimizasyonu, izin | — | Uyarının kaçmaması | `index.html`'de Audio/Notification yok | Görüntü içeren dış bildirim izinsiz yasak |
| FR-15 | Çok saha (`site_id`) ve saha bazlı yetki | **eksik** — ihtiyaç doğrulanmadı | P3 | Backend + Ürün | Hedef müşteri kararı | Nesne düzeyi yetki testleri | Merkezi çok saha izleme | `models.py`'de `site_id` yok | — |
| FR-16 | Rapor dışa aktarma (CSV/PDF) | **eksik** — ihtiyaç doğrulanmadı | P3 | Frontend | Paydaş görüşmesi | — | İSG dosyalaması | `index.html`'de export yok | — |
| FR-17 | Kimlik değişiminde (ID switch) yinelenen incident'ın birleştirilmesi / işaretlenmesi | **eksik** (davranış sentetik testte kanıtlandı) | P1 | ML + Backend | 10-A.4 ölçümü | Duplicate oranı ≤ onaylı eşik | Alarm yükü, sayım doğruluğu | FAZ 8 §11 | Duplicate eşiği |
| FR-18 | Baş bölgesi örtülüyken "bilinmiyor" (ihlal değil) | **eksik** | P1 | ML | 10-A.4 hata örnekleri | FP örnekleri (E-01) üzerinde ölçüm | Yanlış alarm azalır | FAZ 8 §10 E-01, §11 (≥3.5 s örtülme → yanlış alarm) | FP eşiği |

### 6.2 İşlevsel olmayan gereksinimler

| ID | Gereksinim | Durum | Öncelik | Sahip | Kabul testi | Kaynak | GO/NO-GO bağı |
|---|---|---|---|---|---|---|---|
| NFR-01 | Olay teslimi: outbox'a kabul edilen olay kaybolmaz, çift yazılmaz | mevcut (test + 4 sa soak: 0 kayıp, 0 çift) | P0 | Backend | FAZ 7/9 arıza testleri, soak | FAZ 9, 10-A §8 | — |
| NFR-02 | 24 saat kararlılık (dedektör + dashboard) | **doğrulanmamış** (4 sa PASS; 24 sa INCOMPLETE; dashboard bellek düzeltmesi FAZ 10-A.1'de doğrulanıyor) | P0 | Platform | `soak.py --hours 24 --memprobe` PASS ölçütleri | FAZ 10-A.1 raporu §3 | 10-A.9 |
| NFR-03 | Kapasite: hedef saha bilgisayarında hedef kamera sayısı gerçek zamanlı | **doğrulanmamış** (dizüstü 4 kamera PASS; hedef donanım yok) | P0 | Platform | Hedef donanımda yük matrisi | PERFORMANCE_BASELINE | 10-A.9 |
| NFR-04 | Uyarı gecikmesi (olay → operatör ekranı) ölçülür ve onaylı sınır içinde | kısmi (karar gecikmesi sentetik 2.1–3.2 sn; uçtan uca ölçülmedi) | P1 | ML + Backend | ACCEPTANCE_METRICS K-07 | FAZ 8 §7 | Eşik |
| NFR-05 | Erişim güvenliği: RBAC, CSRF, oturum süresi, localhost varsayılan, LAN yalnız TLS | mevcut | P0 | Backend | 338 dashboard + 21 LAN TLS testi | SECURITY.md | — |
| NFR-06 | Veri güvenliği: dosya izinleri, disk şifreleme, yedek şifreleme | **eksik/açık bulgu** (ACL geniş, BitLocker doğrulanmadı, yedek şifresiz) | P0 | BT + Veri sorumlusu | ACL envanteri, `manage-bde`, şifreli yedek geri yükleme tatbikatı | FAZ 10-A.1 §5–6 | KVKK |
| NFR-07 | Bağımlılık güvenliği: bilinen açık yok, sürümler sabit | mevcut (pip-audit temiz, 2026-11-02) / GPU eşdeğerlik **doğrulanmamış** | P0 | Platform | pip-audit + `gpu_parity.py` | FAZ 10-A.1 §4 | — |
| NFR-08 | Yerel çalışma, dışa veri akışı yok (Ultralytics analitiği kapalı) | mevcut (`YOLO_OFFLINE=1` testi) | P0 | Platform | telemetry_check | FAZ 8 §14, FAZ 10-A §4 | KVKK |
| NFR-09 | Yedek/geri yükleme ve geri dönüş (rollback) | mevcut (izole geri yükleme PASS); veri kaybettirmeyen uygulama geri dönüşü FAZ 10-A.1'de inceleniyor | P1 | Platform | `restore_check.py`; rollback tatbikatı | BACKUP_RESTORE.md | — |
| NFR-10 | Lisans uygunluğu (Ultralytics AGPL-3.0, `best.pt`, eğitim verisi) | **eksik karar** | P0 | Hukuk + Ürün | Yazılı lisans kararı | LICENSE_REVIEW.md | **NO-GO** |
| NFR-11 | KVKK uyumu (dayanak, aydınlatma, VERBİS, saklama, haklar) | **eksik karar** | P0 | Veri sorumlusu + Hukuk | Yazılı karar + aydınlatma metni + levha | PRIVACY_READINESS.md | **NO-GO** |
| NFR-12 | Erişilebilirlik ve Türkçe arayüz | kısmi (Türkçe; WCAG değerlendirmesi yapılmadı) | P2 | Frontend | Erişilebilirlik denetimi | — | — |

## 7. Başarı ölçütleri

KPI sözlüğü, paydalar, ölçüm kanıtı ve İSG/işletme onay formu: [ACCEPTANCE_METRICS.md](ACCEPTANCE_METRICS.md).
**Sayısal hedefler bilinçli olarak boştur.** Geliştirici ekip eşik belirlemez; "PASS" ancak onaylı eşik + etiketli
bağımsız veri ile verilir.

## 8. Faz bağımlılıkları ve önceliklendirilmiş backlog (10-A.3 → 10-B)

```text
10-A.2 (bu belge) ─► İSG/işletme eşik onayı ─┐
                  ─► KVKK + lisans kararı ───┤
10-A.3 izinli veri + etiketleme ─► 10-A.4 bağımsız benchmark ─► 10-A.5 kontrollü ML iyileştirme
10-A.6 backend/security  ┐  (10-A.3/4 ile paralel)
10-A.7 operatör UX        ├─► 10-A.9 yazılı GO/NO-GO kurulu ─► 10-B izinli, insan onaylı shadow-mode pilot
10-A.8 ticari araştırma  ┘
```

| Sıra | İş | Faz | Öncelik | Sahip | Ön koşul | Çıktı / kabul | Neden bu sırada |
|---|---|---|---|---|---|---|---|
| 1 | FAZ 10-A.1 kapanışı (bellek A/B, rollback testi, Windows GPU parity, ACL, manuel kabul, 24 sa soak planı) | 10-A.1 | P0 | Platform | — | 10-A.1 raporu; INCOMPLETE kalemler açıkça | Teknik taban kanıtı |
| 2 | Paydaş görüşmeleri (İSG, saha yöneticisi, operatör, BT, veri sorumlusu) | 10-A.2 | P0 | Ürün + İş geliştirme | Görüşme rehberi | Görüşme notları (izinli, anonim) | Persona, rol ve eşik varsayımlarını doğrular |
| 3 | KVKK kararı: amaç, dayanak, aydınlatma, saklama, erişim; veri toplama izni | 10-A.3 önkoşulu | P0 | Veri sorumlusu + Hukuk | — | Yazılı karar | **İzinsiz gerçek çalışan videosu toplanamaz** |
| 4 | Lisans kararı (AGPL uyum / Enterprise / model değişimi) | 10-A.9 önkoşulu | P0 | Hukuk + Ürün | — | Yazılı karar | Model değişimi gerekirse 10-A.4/5 baştan |
| 5 | Kabul eşiklerinin İSG/işletme tarafından doldurulması | 10-A.2→10-A.4 | P0 | İSG + İşletme | ACCEPTANCE_METRICS | İmzalı onay formu | Benchmark sonucunu yorumlamak için |
| 6 | İzinli veri hattı ve etiketleme (şema `ppe-eval-video/1` mevcut) | 10-A.3 | P0 | Veri mühendisi | 3 | Dataset spec, etiket rehberi, split, iki etiketleyici | Doğruluk kanıtının hammaddesi |
| 7 | Bağımsız benchmark (incident recall, FA/kamera-saat, kapsam, duplicate, gecikme) | 10-A.4 | P0 | ML | 5, 6 | Model card, benchmark raporu | Saha doğruluğu ilk kez ölçülür |
| 8 | İnsan doğrulama kaydı (FR-07) + kaçırılan ihlal bildirimi (FR-08) | 10-A.6/7 | P0 | Backend + Frontend | Rol kararı | API/UI + testler | Pilotta insan etiketi ve FA ölçümü için zorunlu |
| 9 | "Analiz" etiketinin "Değerlendirilemiyor (neden)" olarak netleştirilmesi; kapsam sayımlarının raporlara eklenmesi (FR-04) | 10-A.7 | P0 | Frontend + İSG | — | UI testi + kullanıcı testi | UNKNOWN ≠ uygun |
| 10 | Saklama/imha (FR-12), yedek şifreleme, ACL, BitLocker (NFR-06) | 10-A.6 | P0 | Backend + BT | 3 | Onaylı politika + izole test | KVKK |
| 11 | Kontrollü ML iyileştirme (duplicate, örtülme, kişi FP, kapsam kapısı) | 10-A.5 | P1 | ML | 7 | Aday vs baseline A/B; değişmezse baseline kalır | Yalnız ölçülmüş hataya göre |
| 12 | Gerçek RTSP + hedef donanım + 24 sa soak | 10-A.6 | P0 | Platform | Kamera ve donanım erişimi | Test raporu | NFR-02/03/05 |
| 13 | Ticari değer araştırması (ICP, TCO, alternatifler) | 10-A.8 | P2 | İş geliştirme | 2 | Araştırma planı/bulgular | Paralel; pilotu engellemez |
| 14 | Yazılı GO/NO-GO kurulu | 10-A.9 | P0 | Ürün + İSG + Hukuk + BT | 1–12 | İmzalı karar | Kritik madde BLOCKED ise NO-GO |
| 15 | İzinli, insan onaylı **shadow-mode** pilot | 10-B | — | İSG + saha | 14 = GO | Pilot planı ve kanıt | En son; şu an NO-GO |

## 9. Açık kararlar

| # | Karar | Sahip | Etkisi |
|---|---|---|---|
| K1 | Öncelikli müşteri türü ve persona | Ürün sahibi (görüşmelerle) | Rol modeli, çok saha ihtiyacı, fiyat |
| K2 | Ayrı "reviewer/operatör" rolü gerekli mi | Ürün + İSG | FR-07/09 tasarımı |
| K3 | Sayısal kabul eşikleri | İSG uzmanı + işletme | Benchmark ve pilot kabulü |
| K4 | KVKK dayanağı, aydınlatma, saklama süreleri | Veri sorumlusu + hukuk | Veri toplama, FR-11/12/13 |
| K5 | Lisans yolu | Hukuk + yönetim | Model/framework seçimi, dağıtım |
| K6 | Bildirim kanalı ve içeriği | Ürün + veri sorumlusu | FR-14 |
| K7 | Hedef saha donanımı ve kamera sayısı | BT + saha | NFR-03 |
