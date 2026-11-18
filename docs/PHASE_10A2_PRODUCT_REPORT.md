# FAZ 10-A.2 raporu — Ürün tanımı, İSG risk modeli ve iş gereksinimleri

Tarih: 2026-11-02 (UTC). Platform: macOS (iMac M1, 8 GB). Dal `main`, HEAD `d67854d` (FAZ 10-A).
Bu faz **yalnız dokümantasyon** üretti: kod, veritabanı, snapshot, model, outbox, Docker kaynakları değiştirilmedi.
FAZ 10-A.3'e, model eğitimine, yeni API/UI geliştirmeye veya saha pilotuna geçilmedi.

| Karar | Sonuç |
|---|---|
| Ürün gereksinimi ve risk dokümantasyonu (bu faz) | **PASS** — belgeler oluşturuldu ve repo kanıtıyla doğrulandı |
| Kabul eşikleri | **BLOCKED** — İSG/işletme onayı yok (form hazır, boş) |
| Paydaş doğrulaması | **BLOCKED / NOT RUN** — görüşme yapılmadı (rehber hazır) |
| KVKK, model lisansı | **NO-GO** (değişmedi) |
| Gerçek şantiye pilotu | **NO-GO** |

## 1. Ön denetim

- Proje kökü `/Users/d2-18/Downloads/ppe-split-main`, macOS. `git log`: son commit `d67854d` (FAZ 10-A); FAZ 10-A.1
  commit'i **yok**.
- Çalışma ağacında commit edilmemiş FAZ 10-A.1 işi var (dashboard DB katmanı bellek düzeltmeleri, test araçları,
  `docs/BACKUP_RESTORE.md`, `docs/RELIABILITY_TEST_PLAN.md`, `docs/MANUAL_ACCEPTANCE_CHECKLIST.md`,
  `docs/PHASE_10A1_REMEDIATION_REPORT.md` taslağı). Bu fazda **dokunulmadı** ve bu fazın commit'ine eklenmedi.
- Untracked kullanıcı belgeleri: `Claude_Opus_FAZ_10A2_Baslangic_Promptu.md`,
  `PPE_Safety_Monitor_Urunlestirme_Faz_Yol_Haritasi.md` (kökte), `docs/PPE_Saha_Hazirlik_Claude_Opus_Plani.md` —
  korundu, taşınmadı, commit'e eklenmedi.
- `.env` (kök), `detector/.env`, `tools/reliability/output/rel.env` **mevcut** (içerik okunmadı); `dashboard/.env` yok.
- Mac ve Windows depolarının senkron olduğu varsayılmadı. Windows'taki ana veritabanı ve süreçlere erişilmedi.
- Aktif test: FAZ 10-A.1 bellek A/B'sinin ikinci koşusu (izole `ppe-rel` yığını) bu faz boyunca sürüyordu;
  durdurulmadı, ek ağır iş yüklenmedi (yalnız dosya okuma/yazma).

**Belge uyuşmazlığı:** Başlangıç promptu `docs/PRODUCT_ML_QUALITY_ROADMAP.md` dosyasını işaret ediyor; bu dosya
**yok**. Aynı içerik kökte `PPE_Safety_Monitor_Urunlestirme_Faz_Yol_Haritasi.md` olarak bulundu ve okundu. Yol haritası
"güncel repo doğrudan incelenmedi" diyor; bu fazdaki envanter repo üzerinden yapıldı ve yol haritasıyla şu farklar
görüldü: (a) değerlendirme altyapısı (`detector/evaluation/`, GT şeması, incident/takip metrikleri) **zaten var** —
10-A.3/4 sıfırdan değil, veri ve etiketleme tarafından başlar; (b) kapsam (UNKNOWN) paneli ve "izlenemiyor" durumu
**zaten var** — eksik olan etiket netliği ve geçmiş/istatistikte kapsam gösterimi; (c) admin ve denetim kaydı API'si
**var**, yalnız ekranları yok.

İncelenen belgeler: `PHASE_10A_PREPILOT_REPORT.md`, `PHASE_10A1_REMEDIATION_REPORT.md` (taslak), `PHASE_8_REPORT.md`,
`PHASE_9_REPORT.md` (başlıklar ve ilgili bölümler), `MODEL_EVALUATION_PROTOCOL.md`, `PRIVACY_READINESS.md`,
`LICENSE_REVIEW.md`, `PPE_Saha_Hazirlik_Claude_Opus_Plani.md`, kökteki yol haritası.
İncelenen kod (envanter için): `dashboard/app.py`, `dashboard/auth/{routes,store,cli}.py`, `dashboard/db/models.py`,
Alembic 0001–0006, `dashboard/maintenance/*`, `dashboard/static/index.html`, `detector/ppe_core.py`,
`detector/telemetry.py`, `detector/evaluation/cli.py`.

## 2. Oluşturulan / değişen dosyalar

| Dosya | İçerik |
|---|---|
| `docs/product/PRD.md` | Problem, yapar/yapmaz, kullanım senaryoları, amaç dışı kullanım, manuel güvenlik prosedürü, 18 işlevsel + 12 işlevsel olmayan gereksinim (durum, öncelik, sahip, bağımlılık, kabul testi, değer, kaynak, GO/NO-GO bağı), 10-A.3→10-B bağımlılıkları ve 15 maddelik önceliklendirilmiş backlog, açık kararlar K1–K7 |
| `docs/product/PERSONAS.md` | Müşteri türleri A–D, personalar P1–P5 + dolaylı paydaş (çalışan), repo doğrulamalı rol matrisi |
| `docs/product/USER_STORIES.md` | US-01…US-13, kabul ölçütleri, PRD bağları; kapsam dışı hikâyeler |
| `docs/product/FEATURE_GAP_MATRIX.md` | API/DB/UI/dedektör/araç envanteri (dosya kanıtlı), gereksinim ↔ karşılık matrisi, KPI ölçülebilirliği |
| `docs/product/RISK_REGISTER.md` | 14 risk, FMEA benzeri Ş×O×T puanı, kanıt düzeyi, kontroller, boşluklar, sahip |
| `docs/product/ACCEPTANCE_METRICS.md` | 11 KPI (tanım, pay/payda, kanıt, bugün ölçülebilirlik, sahip), boş İSG/işletme onay formu, raporlama şablonu |
| `docs/product/STAKEHOLDER_INTERVIEW_GUIDE.md` | 12 soruluk yarı yapılandırılmış rehber, kurallar, anonim not şablonu, sentez yöntemi |
| `docs/PHASE_10A2_PRODUCT_REPORT.md` | Bu rapor |

## 3. Kritik problem — güvenlik riski olarak (talep C)

| Problem | Kanıt | Güvenlik etkisi | Belgedeki karşılığı |
|---|---|---|---|
| Track-karelerin %91'e varan kısmı analiz dışı | FAZ 10-A 4 sa soak (stok V2: %91.3; ağ benzetimi %91.2); FAZ 8 E-03 (286/286 kare ANALIZ) | Değerlendirilemeyen kişi baretsiz olsa da **uyarı yok** → sessiz kör nokta | R-01 (RÖS 100, en yüksek), FR-04, K-04/K-05, US-04 |
| "Analiz" etiketi / UNKNOWN ↔ uygun karışıklığı | `index.html` `renderPeople`: `ANALIZ` → "Analiz" (gri); kapsam notu "Uygun sayılmaz" | "Analiz ediliyor" gibi okunabilir; istatistiklerde kapsam görünmez | FR-04, US-04, R-01, backlog #9 |
| Yanlış kişi tespiti (makine kolu "Person"), örtülmüş koyu baret | FAZ 8 E-01, E-02 (aday FP, etiketsiz) | Yanlış alarm yükü → alarm yorgunluğu | R-07, FR-18, K-02 |
| ByteTrack ID switch → duplicate, yanlış `end` | FAZ 8 §11 (sentetik), V1 6 ID / ~2–3 kişi (gözlem) | Sayım şişmesi; gerçek ihlal sürerken "bitti" | R-08, FR-17, K-06 |
| Gerçek saha verisi yok | FAZ 8: saha doğruluğu BLOCKED | Kaçırma ve FA oranı bilinmiyor | R-04, K-01/K-02 BLOCKED |

İlke her belgede tekrarlandı: **"değerlendirilemedi", "kamera yok", "analiz yok" ≠ "uygun" / "ihlal yok"**;
raporlarda ayrı kategori.

## 4. KPI sözlüğü (talep D)

`ACCEPTANCE_METRICS.md` §2: K-01 incident recall · K-02 FA/kamera-saat (payda: **analiz edilen** saat) · K-03
nesne recall · K-04 kapsam/UNKNOWN · K-05 analiz edilmeyen kişi kaynaklı kaçırma · K-06 duplicate/ID switch · K-07
uyarı gecikmesi (karar / teslim / ekrana görünme ayrı) · K-08 kamera uptime ve izlenmeyen süre · K-09 kalıcı outbox
teslimi · K-10 insan doğrulama süresi · K-11 koşul bazında en kötü recall. **Sayısal eşik yazılmadı**; onay formu boş.

Bugün ölçülebilenler: K-04, K-07(b) teslim gecikmesi, K-09 (izole testlerde 0 kayıp/0 çift), K-08 kısmen, K-03 yalnız
dağılım içi. K-01, K-02, K-05, K-06, K-11 etiketli saha verisi; K-10 insan doğrulama kaydı (FR-07, **eksik**) gerektirir.

## 5. PASS / FAIL / SKIP / BLOCKED

| Kalem | Sonuç | Kanıt |
|---|---|---|
| A. Roller, problem, senaryolar, amaç dışı kullanım, insan kontrolü, manuel prosedür | **PASS** (taslak) | PRD §1–5, PERSONAS |
| B. PRD, PERSONAS, USER_STORIES, FEATURE_GAP_MATRIX, RISK_REGISTER, ACCEPTANCE_METRICS | **PASS** | `docs/product/` |
| B. Her gereksinimde durum/öncelik/sahip/bağımlılık/kabul testi/değer | **PASS** | PRD §6 tabloları |
| "Eksik" etiketlerinin repo kanıtı | **PASS** | FEATURE_GAP_MATRIX §1 "Bulunmayan" satırları (arama/okuma) |
| C. Kritik problemler güvenlik riski olarak | **PASS** | RISK_REGISTER R-01, R-03, R-04, R-07, R-08 |
| D. KPI sözlüğü, paydalar, kanıt | **PASS**; sayısal eşikler **BLOCKED** (onay yok) | ACCEPTANCE_METRICS |
| E. 10-A.3→10-B bağımlılık ve backlog | **PASS** | PRD §8 |
| F. KVKK ve lisans NO-GO korundu; karar sahipleri ve gereken kanıtlar | **PASS** (karar: **NO-GO**) | PRD §6.2 NFR-10/11, §9 K4/K5; RISK R-05/R-06 |
| G. Paydaş görüşme rehberi | **PASS** | STAKEHOLDER_INTERVIEW_GUIDE |
| Paydaş görüşmeleri | **NOT RUN** | — |
| İSG/işletme eşik onayı | **BLOCKED** | ACCEPTANCE_METRICS §3 boş |
| Öncelikli persona seçimi | Öneri (P1, müşteri türü A) — **doğrulanmadı** | PRD §3 |
| Kod/DB/model/snapshot/outbox değişikliği | yapılmadı (kapsam gereği) | `git status` |

Kritik FAIL yok. Test paketi koşulmadı (kod değişmedi); son sonuçlar FAZ 10-A.1 raporunda.

## 6. Açık kararlar (sahip)

| # | Karar | Sahip |
|---|---|---|
| K1 | Öncelikli müşteri türü ve persona | Ürün sahibi (görüşmelerle) |
| K2 | Ayrı reviewer/operatör rolü | Ürün + İSG |
| K3 | Sayısal kabul eşikleri (ACCEPTANCE_METRICS §3) | İSG uzmanı + işletme |
| K4 | KVKK dayanağı, aydınlatma, saklama süreleri, VERBİS | Veri sorumlusu + hukuk |
| K5 | Lisans yolu (AGPL uyumu / Enterprise / model değişimi) | Hukuk + yönetim |
| K6 | Bildirim kanalı ve içeriği | Ürün + veri sorumlusu |
| K7 | Hedef saha donanımı ve kamera sayısı | BT + saha |
| — | Risk şiddet puanlarının onayı | İSG uzmanı |

## 7. Sonraki adımlar (otomatik geçilmedi)

1. FAZ 10-A.1'in kapanışı ve commit'i (bellek A/B sonucu, rollback testi, Windows kalemleri).
2. Paydaş görüşmeleri (rehberle) ve kabul eşiği atölyesi.
3. KVKK ve lisans kararları — gerçek çalışan verisi toplamanın ön koşulu (FAZ 10-A.3).

## 8. Git commit

Yalnız bu fazın dosyaları (`docs/product/*`, bu rapor) commit edildi; FAZ 10-A.1 çalışma ağacı değişiklikleri ve
kullanıcı belgeleri commit dışı bırakıldı. Commit kimliği: `git log --oneline -1 -- docs/PHASE_10A2_PRODUCT_REPORT.md`
(kimlik commit sonrası oturum özetinde bildirildi; rapor kendi commit kimliğini içeremez).
