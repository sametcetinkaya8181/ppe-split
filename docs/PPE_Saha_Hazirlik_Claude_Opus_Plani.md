# PPE Safety Monitor — Gerçek Şantiye Ortamına Hazırlık (FAZ 6–10)

**Referans durum:** FAZ 5 raporuna göre son commit `0c2e9cb`; PostgreSQL 17, FastAPI, YOLO + ByteTrack + PPEMonitor, Sender, Alembic 0003, güvenli kamera kimliği, geçmiş API ve dashboard. FAZ 5'in 119 dedektör + 260 dashboard + 23 tarayıcı PASS sonuçları *kullanıcı tarafından iletilen Claude Code raporudur*; burada yeniden çalıştırılmamıştır.

**İşletim ilkesi:** Saha pilotunun ilk aşaması yalnızca gözlem/insan doğrulama amaçlıdır. Otomatik disiplin, personel kimliklendirme, yüz tanıma veya bağımsız güvenlik müdahalesi yapılmaz. Her faz ayrı oturumda çalıştırılır; kritiklik derecesi yüksek FAIL varsa ilerlenmez.

## Faz haritası

| Faz | Ana hedef | Kabul eşiği / geçiş kapısı |
|---|---|---|
| 6 | Kimlik doğrulama, rol bazlı yetki, TLS/erişim güvenliği ve KVKK/lisans denetimi | Tüm korumalı API/video/snapshot/WS kaynaklarına yetkisiz erişim engelli, tüm regresyonlar PASS; KVKK ve lisans için açık go/no-go kayıtları |
| 7 | Kalıcı disk outbox, kurtarma, sıralama, hata sınıflandırması ve bozuk `running` run uzlaştırması | Çökme, restart, DB kopması ve tekrar gönderimde çift kayıt yok; yanlış ACK yok; sınırlar testli |
| 8 | Etiketli şantiye veri setiyle model ve **incident-level** doğruluk ölçümü | Ground truth verisi, karışıklık matrisi, katmanlı koşul analizi, güven aralıkları; hedefler İSG sorumlusu tarafından onaylı |
| 9 | Çoklu RTSP, kaynak/GPU yükü, kesinti senaryoları, metrikler ve soak test | Hedeflenen saha yükünde izlenebilir test, veri bütünlüğü ve geri dönüş prosedürü doğrulanmış |
| 10 | İzinli, kontrollü gölge mod pilot, operasyonel süreçler, sürümleme, devreye alma kapısı | İSG + veri sorumlusu + teknik sorumluların belgeli kararı; otomatik yaptırım yok |

## Ortak koruma kuralları (bütün fazlar)

- Çalışma dizinini ve bağımsız Git deposunu doğrula. `git status --short`, `git log -5 --oneline`, `git branch --show-current` al. En son güvenilir checkpoint `0c2e9cb`.
- Kullanıcı değişikliklerini ezme; `git reset --hard`, `git clean`, `docker compose down -v`, `docker volume prune` veya başka projelerin container/volume/depolarına dokunma. `llminsight_pgdata` kapsam dışı.
- `best.pt`, `ppe_core.py`, tespit karar eşikleri, mevcut WebSocket binary biçimi ve geçmiş migration dosyaları 0001–0003 korunacak. Gerekli yeni DB değişikliği *additive* migration olarak yapılır ve sadece izole test DB'sinde downgrade edilir.
- Gerçek video/snapshot, token, RTSP URL, kişisel veri ve `.env` kopyalarını Git'e, test fixture'larına veya loglara yazma. Güvenilir gizli bilgileri de raporda maskeli göster.
- Üretim veya yerel ana DB'deki olayları ve snapshot'ları onaysız silme. Mümkün olduğunca ayrı test veritabanı, ayrı test kamera kaynağı ve ayrı saklama yolu kullan.
- Aşamaya başlamadan önce mevcut 260 dashboard, 119 dedektör, 23 tarayıcı testinin çalıştırılabilirliğini belirle. Gerçekte çalışmayan testler PASS sayılmayacak.
- `INGEST_TOKEN` değişikliğinde sender'ın 401/403 karşısında olay düşürmesi riskini dikkate al. Kontrollü bakım penceresinde, dedektör ve sunucu eşzamanlı güncellenmeli.
- Önce gerçek kodu incele; bu belgedeki varsayımlar yanlışsa farklılığı raporla. Büyük refactor veya yeni framework ekleme.
- Her faz sonunda `docs/PHASE_N_REPORT.md` hazırla (N ilgili faz); PASS/FAIL/SKIP, komutlar, kanıt, risk, varsa commit hash ve sonraki faz ön koşullarını yaz. Ancak kritik testler başarısızsa 'tamamlandı' deme.

---

## FAZ 6 — Erişim, ağ ve gizlilik sertleştirme

### Claude Code Opus promptu

```text
PPE SAFETY MONITOR — FAZ 6: SAHA ERİŞİM GÜVENLİĞİ VE TEHDİT MODELİ
MODEL: Claude Opus | EFFORT: HIGH

BAĞLAM:
FAZ 0–5 tamamlandı (kullanıcı raporuna göre). Son commit 0c2e9cb.
Mevcut: FastAPI + PostgreSQL 17 + Alembic 0003 + ppe_app düşük yetki rolü; detector host; Docker dashboard ve DB; canlı kamera, /ws, /video_feed, /snapshots, history API; INGEST_TOKEN aktif; dashboard 127.0.0.1'de. Son raporda 260/260 dashboard, 119/119 detector, 23/23 tarayıcı testi geçmiştir (yeniden doğrula).

HEDEF:
Gerçek şantiye LAN erişimine hazırlayan, ancak yetkisiz yayına yol açmayan güvenlik temeli oluştur. Varsayılan network davranışını localhost-only bırak. Bu fazın çıktısı kimlik/yetki/test altyapısı ve kontrollü ağ dağıtım tasarımıdır; canlı sahaya veya internete açma.

ÖNCE:
1. Git durumu, mevcut commit, test ve kurulum denetimini yap. Kullanıcı değişikliklerini koru.
2. Tüm HTTP endpoint'leri, /ws, /video_feed, /snapshots, ana sayfa, statik dosya ve health uçları için erişim envanteri çıkar. RTSP URL, token, snapshot, .env kopyaları ve DB şifresi yüzeylerini değerlendir.
3. OWASP API Security Top 10 açısından kimlik doğrulama, nesne yetkilendirme, brute-force, CSRF, XSS, CORS, rate limit, WebSocket Origin ve veri sızıntısı için THREAT_MODEL.md hazırla.
4. Mevcut/önerilen kullanım ortamını ayır: localhost / yetkili LAN / internet. Güvenli yapılandırma ve geçiş kontrolü belgele.

KOD GÖREVLERİ:
A. Browser kullanıcıları için mevcut minimal teknolojiye uyumlu server-side oturum ve yetkilendirme uygula. Rol tasarımı en az admin, supervisor, viewer; pratik ihtiyaç varsa basitleştir ama nedenini açıkla. Argon2id veya olgun parola hashing altyapısı, brute force kontrolü ve güvenli parola yönetimi; raw parola loglarına izin verme. Parola kurtarma/e-posta gerektiren bir servis uydurma.
B. Oturum cookie'leri HttpOnly + SameSite ve HTTPS altında Secure olsun. LAN/TLS etkinleştirilmeden Secure cookie nedeniyle localhost deneyimini bozmayacak açık bir dev/test profili oluştur; insecure profil LAN'da kabul edilmesin. Oturum süresi, logout, CSRF koruması ve session invalidation testleri ekle.
C. History API, camera/run/incident detayları, JPEG snapshots, MJPEG video, canlı state/alerts/report API, /ws ve dashboard sayfalarına uygun aynı erişim politikası uygula. /healthz ve /health/ready için sadece hassas olmayan asgari çıktı kullan. Rol bazlı nesne düzeyi izinleri uygula; yalnız UUID bilmek erişim hakkı vermesin.
D. Detector ingest için insan oturumu değil, ayrı makine-to-makine kimliği kullan. INGEST_TOKEN, rol/session cookie'siyle asla karıştırılmasın. Token rotation için kesinti/kayıp yaratmayan, test edilmiş bir geçiş planı ver; güvenlik ayarını çalışan dedektörde tek taraflı değiştirme.
E. LAN için TLS reverse proxy yaklaşımını belgeli ve test edilebilir biçimde hazırla. Örn. Caddy/Nginx; gerçek sertifika veya alan adı yoksa üretim TLS'si kuruldu iddia etme. HTTP→HTTPS, proxy trust, WS/WSS, origin, secure cookie, HSTS'nin uygun ortamlarda uygulanması, CORS default deny ve ağ kısıtlamasını test et. Compose varsayılan portu 127.0.0.1 olarak kalsın; açık onay olmadan 0.0.0.0 veya internete yayın yapma.
F. Audit log: login, logout, auth failure, yetkisiz istek, erişim politikası değişikliği, snapshot erişimi gibi güvenlik olaylarını hassas veri içermeyen şekilde kaydet. Güvenlik kayıtlarını kişisel takibe dönüştürme.
G. Docker container non-root imajı, read-only filesystem/yalnız yazılabilir volume'lar, resource limits, image pinning, healthcheck ve dependency review için gerçek koda uyumlu risk-temelli iyileştirmeleri uygula. Uyumsuz veya riskli değişiklikleri belgele; sırf 'hardened' görünmek için çalışan sistemi bozma.
H. KVKK açısından kişisel veri envanteri, amaç sınırlaması, işyeri kamera aydınlatması, rol matrisi, saklama/erişim ve imha süreçleri ile insan inceleme akışını docs/PRIVACY_READINESS.md içinde belgele. Hukuki onay verilmediğini açıkça yaz. Yüz tanıma veya personel kimliği çıkarımı ekleme.
I. Ultralytics model ve ağırlıklarının lisans koşulları için docs/LICENSE_REVIEW.md oluştur. Kapalı kaynaklı ticari uygulama kararı verilmeden teknik dağıtım = lisans onayı varsayma.

TESTLER:
- Yetkisiz kullanıcı: history/list/detail, snapshots (URL bilinmesine rağmen), live video, WebSocket, canlı API erişimlerinin tümü kapalı.
- Viewer yazma/yönetim yetkisi alamaz; rol/escalation, object-level authorization ve CSRF girişimleri reddedilir.
- Admin/supervisor/viewer izinleri doğru.
- CSRF, same-site, cookie flag, session expiry/revocation, login brute force kontrolü.
- Token doğru/yanlış/missing; insan oturumu ingest'e erişim vermez.
- WebSocket kimlik doğrulama ve Origin kontrolü.
- HTTP ve TLS-dev profile ayarları regresyonu; varsayılan dış port açılmaz.
- SQL injection, XSS, traversal, secret masking, security logs.
- Mevcut ingest, DB idempotency, WebSocket 1-byte idx + JPEG ve live video çalışır.
- 260 dashboard + 119 dedektör + mümkünse 23 gerçek tarayıcı testi yeniden PASS.
- Gerçekte koşulamayan TLS/LAN entegrasyon testleri SKIP olarak dürüstçe raporlanır.

SINIRLAR:
Best.pt, ppe_core, YOLO/ByteTrack mantığı ve 0001–0003 migration geçmişine dokunma. Hiçbir ana DB kaydı veya snapshot silinmesin. Yıkıcı Docker komutu çalıştırma. Sahaya doğrudan bağlanma, yeni dış port açma, internet yayını yapma. Lisans ve KVKK uygunluğunu hukuki inceleme yapılmadan PASS diye gösterme.

KAPANIŞ RAPORU:
Değişen dosyalar, auth/session mimarisi, endpoint matrisi, TLS/LAN config, threat model, güvenlik test kanıtları, regresyonlar, açık riskler, KVKK/lisans go/no-go, PASS/FAIL/SKIP ve git commit. Kritik test başarısızsa FAZ 6'yı tamamlandı sayma. FAZ 7'ye otomatik geçme.
```

**FAZ 6 geçiş koşulu:** Hangi URL/endpoint'in hangi rolle açıldığı gösterilmeli; yetkisiz snapshot ve WebSocket sızıntıları engellenmeli; LAN'a henüz kontrolsüz açılmamalı. KVKK/lisans hukuki değerlendirmeleri ayrı blokaj olarak izlenmeli.

---

## FAZ 7 — Disk tabanlı outbox ve kesintilere dayanıklılık

### Claude Code Opus promptu

```text
PPE SAFETY MONITOR — FAZ 7: DURABLE OUTBOX & RECOVERY
MODEL: Claude Opus | EFFORT: HIGH

FAZ 6 raporunu, son HEAD'i, gerçek Sender/protocol.py ve ingester repository.py'yi oku. Korumaları ve Git durumunu doğrula. Önce risk ve failure-mode matrisini hazırla.

Hedef: Dedektör çökerse/sistem kapanırsa henüz gönderilmemiş start/end ve report mesajlarını kontrollü biçimde kurtarmak. Yerel persistent spool/outbox (ör. SQLite WAL), append/commit/fsync ve atomik ACK durumu tasarla. Yüksek hacimli JPEG frame'lerini kalıcı kuyruğa alarak diski doldurma. Olay/snapshot yükü için içerik güvenli ve sınırlı saklama kullan.

KRİTİK İLKELER:
1. Event_id, incident_id, run_id ve JSON payload üretildiğinde sabitlensin. Kuyruğa atomik kalıcı yazım başarıyla yapılmadan teslim edilebilir sayma. Disk yazma hatası kullanıcıya/operatöre açıkça bildirilsin; sessiz kayıp yaşanmasın.
2. Sunucuda 2xx yalnız DB commit sonrası ACK olduğu için outbox'tan ancak 2xx sonrası kaldır. Ağ timeout'unda tam aynı payload tekrar gönderilsin. Duplicate event idempotency, çelişkili 409 ve kalıcı 4xx durumlarında davranış özel olarak tanımlansın.
3. Event/report FIFO ve run barrier korunsun; farklı run/kamera paralelliğinde karışma olmasın. İşlem yarıda kalırsa restart'ta güvenli replay yap. Orphan snapshot ve yarım dosya durumlarını test et.
4. Tek işlem/süreç/host dosya kilitleme, SQLite bütünlük kontrolü, dosya sahipliği, permissions, disk dolması ve quota'ları belirle. Dosya yolu sistem dışına çıkamasın. Varsayılan şifreleme/işletim sistemi disk korumasını belgele, gizli bilgiler loga düşmesin.
5. Queue max bytes, high-watermark alarm, retention, backpressure, queue-age ve delivery-lag metrikleri ekle. Ongoing atılması gerekiyorsa ölç ve göster; start/end/report kritik olaylarını sessizce düşürme. Tam kayıpsız teslim iddia etme; disk/OS arızası sınırını yaz.
6. 401/403 token mismatch sırasında sessiz drop yerine güvenli quarantine/pause ve operatör uyarısı tasarla; aynı payload daha sonra tekrar işlenebilsin. Geçersiz kalıcı 4xx için dead-letter/quarantine, audit ve kontrollü yeniden işleme. Sonsuz zehirli retry olmasın.
7. Çökmüş run'lar için heartbeat + 'interrupted / unknown' uzlaştırma prosedürünü tasarla. DB'de 'running' görünen FAZ 5 test run'larını otomatik silme/düzeltme; yalnız tanımlı koşul ve denetim iziyle değiştirme, kullanıcı onayı olmadan ana kayıtları değiştirme.
8. Alarm üretiminin ve video akışının storage işlerinden gereksiz beklememesi için kuyruk önceliği ve performans testlerini yap.

TESTLER:
- Sender process kill (kontrollü, test DB), restart ve aynı event_id ile replay.
- DB 503, ağ timeout, ACK kaybı, 409 conflict, 401/403, yeniden token eşitleme.
- Disk dolu, SQLite lock, corrupt spool, queue overflow, spool cleanup.
- Report sırası, farklı run kamera, concurrent producers, Ctrl+C.
- Snapshot path bütünlüğü, duplicate ve outbox'a atomik kabul.
- 260+119+23 mevcut regresyon (değişenlerle güncellenecek).
- 2xx öncesi veri kaybı yok denmesi için kapsamı tanımlı crash/restart kanıtı; 'exactly-once delivery' iddiası yok, DB dedup ile exactly-once effect sınırı net.

Mevcut demo ve modelleri değiştirme; yeni disk formatı için migrasyon gerekiyorsa yedek ve geri uyum oluştur. Büyük refactor yapma. FAZ 7 sonunda docs/PHASE_7_REPORT.md, testler, riskler, commit oluştur; FAZ 8'e geçme.
```

**FAZ 7 geçiş koşulu:** Üretilen kritik olay disk kuyruğuna dayanıklı şekilde kabul edilmeli; test ortamında ani işlem ölümü ve DB kesintisi sonrası tekrar iletim ile PostgreSQL tekilleştirme kanıtlanmalı. Diskin bütünüyle bozulması ve donanım kaybı ayrı sınır olarak kalır.

---

## FAZ 8 — Model doğruluk ve hata analizi

### Claude Code Opus promptu

```text
PPE SAFETY MONITOR — FAZ 8: FIELD MODEL VALIDATION
MODEL: Claude Opus | EFFORT: HIGH

FAZ 6–7 raporlarını ve son commit'i oku, Git durumunu kontrol et. Bu faz, best.pt'yi yeniden eğitmek için DEĞİL, mevcut modeli gerçek kullanım koşullarında objektif ölçmek içindir. Önce ölçüm metodolojisi geliştir; ölçüm verisi yoksa PASS uydurma.

1. Gerçek model sınıflarını runtime'dan doğrula (önceki rapor: helmet=0, vest=2, Person=6, no_helmet=7). 11 sınıfın tamamını listele; mevcut PPEMonitor'un pozitif tespit üzerinden eksik ekipman çıkarımı yaptığını belgeleyip alternatif sınıf kullanımını sessizce etkinleştirme. best.pt SHA256, Ultralytics sürümü, VID_STRIDE, confidence, IoU ve tracker ayarlarını dataset manifestine sabitle.
2. İzinli/anonymize edilmiş görüntüler için test seti protokolü oluştur. Kamera/saha/tarih bazında eğitimden ve threshold tuning'den ayrı, mümkünse hiç görülmemiş test görüntüleri seç. Sınıflar: person, helmet, vest ve person-level PPE state. Görünmeyen/engellenmiş parçalarda 'unknown/occluded' etiketini kullan; görünmemeyi otomatik ihlal sayma. Tekil incident ground truth, ilk görülme/bitiş zamanı ve kamera ID verisi ayrı tutulmalı.
3. Koşul matrisi: gündüz, akşam/gece, gölge, yağmur/toz, düşük çözünürlük, kalabalık, uzak kişi, kısmi kapanma, oturma/eğilme, kamera açısı, farklı renk/reflektif kıyafet, benzer görünen şapka/yelek, işçi olmayan kişi. Gerekli izin ve görüntü kalitesi olmadan çekim yapma. Küçük gruplar için belirsizliği raporla.
4. Ayrı ölçümler: nesne detection için class-wise precision/recall, mAP50 ve mAP50–95; insan+ekipman eşleme doğruluğu; person-level missing helmet/vest precision/recall/F1; yanlış alarm/kamera-saat; kaçırılan gerçek ihlal; incident-level precision/recall; alarm gecikmesi p50/p95; ID switch, fragmentation ve track kaybı. Confusion matrix ve örnek hata analizi üret.
5. Değişken eşikler için validation split kullan; test split'i tuning için tekrar tekrar kullanma. Tüm eşik/konfigürasyon varyantlarını aynı veri ve test senaryosunda karşılaştır. Baseline ile candidate A/B kıyasla; ppe_core'ya bu fazda dokunma, ürün koduna karar mantığı taşımadan önce ayrı onay iste.
6. Güven aralıkları, örnek sayısı, pozitif/negatif dağılımı ve kamera bazlı ayrışmayı raporla. Sonuçlar yalnız örneklenen saha koşulları için geçerli. Özel veriyi Git'e yükleme.
7. Teknik hedefleri öner ama güvenlik kabul kriterlerini geliştirici tek başına belirlemesin: İSG uzmanı ve saha sorumlusu yanlış negatif toleransı, yanlış alarm yükü, gecikme ve operatör tepkisi için yazılı eşikleri onaylamalı.
8. Etiketli test veri seti yoksa araç/şema/senaryo/CLI hazırlanabilir fakat accuracy, recall veya false alarm metrikleri PASS sayılmaz; DATA_REQUIRED/BLOCKED olarak raporla. Model eğitimi veya otomatik personel yaptırımı yok.

ÇIKTI: docs/MODEL_EVAL_PROTOCOL.md, örnek veri sözleşmesi, CLI/rapor üretimi, ölçüm testleri, model hash, gerçek sonuç varsa sınıf/koşul matrisleri, başarısız sahne örnekleri, go/no-go eksikleri, PASS/FAIL/SKIP ve commit. FAZ 9'a geçme.
```

**FAZ 8 geçiş koşulu:** İzinli, etiketli ve saha koşullarını temsil eden veri üzerinde ölçülen sonuçlar ve uzman tarafından onaylı eşikler olmadan saha güvenliği yeterliliği PASS sayılmaz.

---

## FAZ 9 — Uzun çalışma, çoklu RTSP ve operasyonel dayanıklılık

### Claude Code Opus promptu

```text
PPE SAFETY MONITOR — FAZ 9: RELIABILITY & SOAK TESTS
MODEL: Claude Opus | EFFORT: HIGH

FAZ 6–8 raporları ve son HEAD'i incele. Henüz gerçek sahaya/İnternet'e açma. Mevcut veritabanı, snapshot ve test verilerini koru.

- Test matrisi: tek kamera, 3 kamera, hedeflenen daha yüksek kamera sayısı; RTSP timeout, kopma/yeniden bağlanma, kesik yayın, yanlış parola, kamera donması, video loop, GPU reset/OOM, CPU fallback mümkünse, Docker container restart, PostgreSQL kesintisi, disk dolması, bilgisayar uyku/yeniden başlatma.
- Kamera sağlık durumu: son kare zamanı, freeze detector, frame drop, reconnect sayısı, run status ve lag. 'İhlal yok' ile 'kamera izlenemiyor' durumlarını asla birleştirme. Her bağlantı sonrası kamera kimliği ve run kimliği tutarlı olsun.
- Operasyonel metrikler: FPS, inference latency p50/p95, ingest latency p50/p95, alert latency p50/p95, CPU/GPU/RAM/VRAM, queue length/age, failed delivery/retry, dead-letter, snapshot disk, DB pool, lag, incident throughput. Gizli veri/çalışan görüntüsünü telemetry dışına taşıma.
- Gerçek hedef donanımda mümkün olan uzun soak test (ör. 24 saatlik hedef; tamamlandı demek için gerçekten 24 saat koşmalı). RAM/artış eğrisi ve dosya tanıtıcısı sızıntıları ölç. Test kısa kalırsa süreyi doğru söyle.
- Backup/restore, migration upgrade, health/readiness ve alert thresholds; fail-safe operatör bildirimi. Kamera/sunucu offline iken gerçek tehlike görmüyormuş gibi yeşil gösterme.
- Eski FAZ 5'te açık kalan 3 run için yıkıcı olmayan 'stale-run reconciliation' politika ve audit testini doğrula; ana kayıtları onaysız değiştirme.
- Anlamlı SLO önerileri: kamera kullanılabilirlik, olay kaydı teslim süresi, toplam alarm gecikmesi, kritik olay veri kaybı, tekrar kayıt oranı, disk kapasitesi. Hedef sayıları gerçek hardware + pilot iş ihtiyacı ve İSG onayıyla belirlensin.

Testler gerçek PostgreSQL, test videosu/izole kamera ve kontrollü arıza ortamıyla çalıştırılsın. CPU/GPU/video müsait değilse dürüst SKIP. Mevcut 119/260/23 regresyonlarını da çalıştır. docs/OPERATIONS.md, RUNBOOK.md, RELIABILITY_REPORT.md, sonuçlar ve commit üret. FAZ 10'a geçme.
```

**FAZ 9 geçiş koşulu:** Kesintiler alarmları sessizce kaybettirmemeli, izleme dışı kalan kamera açıkça görünmeli, hedef yük altında ölçülmüş gecikme ve kaynak kullanımı bulunmalı.

---

## FAZ 10 — Pilot ve kontrollü kullanım kararı

### Claude Code Opus promptu

```text
PPE SAFETY MONITOR — FAZ 10: SHADOW-MODE SITE PILOT & RELEASE GATE
MODEL: Claude Opus | EFFORT: HIGH

FAZ 6–9 raporlarını oku. Son commit ve Git durumunu doğrula. Bu fazdan önce hukuki/operasyonel ve teknik GO/NO-GO kararlarını yazılı hale getir. Hangi saha/kameralara erişim iznimiz olduğu doğrulanmadan gerçek RTSP yayınlarına bağlanma.

HEDEF:
Saha iş güvenliği uzmanının insan incelemesiyle yürüttüğü, pasif uyarı 'shadow mode' pilotunu planlamak; yetkili olunan ortamlarda uygulamak; başarı/başarısızlık kararını kanıta dayandırmak.

1. Yetkilendirme ve mahremiyet: işveren/veri sorumlusu onayı, çalışan aydınlatması, kamera konumu, amacı, veri minimizasyonu, saklama/imha ve erişim yetkileri. Türkiye KVKK rehberine göre kontrol listesi; hukuki görüş yerine geçme.
2. Ultralytics AGPL/Enterprise lisans dağıtım kararını açık tut. Lisans durumu netleşmeden kapalı kaynak ticari devreye alımı GO işaretleme.
3. Model değerlendirme setindeki sahaya uygunluk ve İSG tarafından imzalanan doğruluk/yanlış alarm/latency kabul eşiklerini pilot kapısı yap.
4. Shadow mode: Uyarılar yetkili operatöre ön inceleme için gider. Otomatik ceza, personel puanlama, yüz tanıma, iş durdurma kararı veya tek başına riskli müdahale yok. Kamera kesintilerinde mevcut İSG prosedürü aynen geçerli.
5. Pilot runbook: kurulum, konfigürasyon, kalıcı kamera ID, TLS, kullanıcı rolü, token rotation, backup doğrulama, kurtarma, audit, rollback, operatör eğitimi, arıza bildirimi, olay inceleme. Secret'ları dosyaya yazma.
6. Pilot değerlendirmesi: uzman doğruladığı gerçek olaylar, yanlış pozitif, yanlış negatif, uçtan uca gecikme, kamera uptime, insan doğrulama süresi, sistemin yanlış kullanımına ilişkin geri bildirim. Günlük ve kamera bazlı sonuç.
7. Pilot tamamlanmadıysa sonuç uydurma; 'PILOT NOT EXECUTED' olarak bildir. Kısmi veya başarısız testleri PASS yapma.
8. Release gate: teknik güvenlik PASS, model eşikleri PASS, lisans uygunluğu ve KVKK süreçleri belgeli, operasyon sahibi atanmış, gizlilik uygun, rollback ve manuel fallback testli. Herhangi bir kritik koşul FAIL/BLOCKED ise NO-GO.

ÇIKTILAR: docs/PILOT_PLAN.md, docs/RELEASE_CHECKLIST.md, docs/INCIDENT_RESPONSE.md, docs/GO_NO_GO.md, gerçek pilot yapılmışsa gözlem raporu, testler, açık riskler, commit. Projeye açık onay olmadan internete/LAN'a yayılma değişikliği yapma.
```

**FAZ 10 çıkışı:** Yalnız yazılı onaylar ve saha verisine dayalı testlerle sınırlı pilot. Üretim/safety-certified sonucu otomatik olarak çıkarılmaz.

---

## Önemli referanslar

- OWASP API Security Top 10 (2023): https://api-security.owasp.org/editions/2023/en/0x11-t10/
- KVKK — İş yerlerinde güvenlik kamerası kullanımı: https://www.kvkk.gov.tr/Icerik/8770/is-yerlerinde-guvenlik-kamerasi-sistemi-kullaniminda-dikkat-edilecek-hususlara-dair-kamuoyu-duyurusu
- NIST AI Risk Management Framework: https://airc.nist.gov/airmf-resources/airmf/5-sec-core/
- Ultralytics lisans rehberi: https://www.ultralytics.com/license

## Çalışma sırası ve token yönetimi

1. FAZ 5 raporu Git'te korunuyorsa, Claude Code oturumunu `/clear` ile temizle.
2. Bir fazın *yalnız kendi* promptunu gönder. Diğer fazları bilgi amaçlı tut; Opus kendiliğinden sonraki faza geçmesin.
3. Her faz sonunda otomatik test kanıtlarını, bilinen riskleri ve commit'i al; gerekirse o fazın raporunu inceleyip takip işlerini ayrı başlat.
4. `/clear` öncesi `git status` ve son rapor/commit'i kontrol et. Bir sonraki oturuma “son commit, mevcut testler, planlanan faz, açık riskler” bağlamını aktar.
