# Kullanıcı hikâyeleri

**Sürüm:** taslak v0.1 — 2026-11-02 (FAZ 10-A.2)
Hikâyeler personalardan (PERSONAS.md) türetilmiştir; kullanıcı görüşmesiyle **doğrulanmadı**. Her hikâye PRD
gereksinimine bağlıdır. Durum ve öncelik PRD ile aynıdır.

Biçim: *[Persona] olarak, [ihtiyaç], böylece [değer].* Kabul ölçütleri Given/When/Then (Verilen/Olduğunda/O zaman).

## 1. Uyarı ve doğrulama

**US-01 — Canlı uyarı** (FR-01, FR-03) · durum: mevcut (algoritma), saha doğruluğu doğrulanmamış · P0
İSG uzmanı olarak, atandığım kameralarda baret/yelek eksikliği adayını kanıt görüntüsüyle görmek istiyorum, böylece
sahada hızlı müdahale edebilirim.
- Verilen bir kişi ≥ 2 sn baretsiz göründüğünde, o zaman canlı ekranda kamera, zaman, eksik ekipman ve kanıt
  görüntüsüyle bir uyarı görünür.
- Verilen uyarı bir aday olduğunda, o zaman ekran "doğrulanmamış aday" dilini kullanır, "ihlal kesinleşti" demez.
- Kabul testi: dedektör testleri (mevcut) + gölge pilotta incident recall ve uyarı gecikmesi (K-01, K-07).

**US-02 — Doğrulama kaydı** (FR-07) · **eksik** · P0
İSG uzmanı olarak, her adayı "Onaylandı / Yanlış alarm / Belirsiz" olarak işaretleyip kısa not düşmek istiyorum,
böylece yanlış alarm yükü ölçülür ve kararlarım kayıt altında olur.
- Verilen bir aday, olduğunda işaretlediğimde, o zaman kararım, kim/ne zaman bilgisiyle ayrı kaydedilir; modelin
  orijinal olayı değişmez.
- Verilen işaretlenmemiş adaylar, o zaman raporda "doğrulanmamış" olarak ayrı sayılır.
- Verilen bir kararı değiştirdiğimde, o zaman önceki karar denetim kaydında kalır.

**US-03 — Kaçırılan ihlal bildirimi** (FR-08) · **eksik** · P1
İSG uzmanı olarak, sahada gördüğüm ama sistemin uyarmadığı ihlali kamera ve zaman aralığıyla bildirmek istiyorum,
böylece kaçırılan ihlaller görünür olur.

**US-04 — Değerlendirilemeyen kişiler** (FR-04) · kısmi · P0
İSG uzmanı olarak, sistemin hangi kişiler hakkında karar **veremediğini** ve nedenini (uzak/küçük, kadraj kenarı,
düşük güven) görmek istiyorum, böylece o alanı kendim kontrol ederim.
- Verilen bir kişi güvenilirlik kapısından geçemediğinde, o zaman durum etiketi "Uygun" değil, "Değerlendirilemiyor
  (neden)" olur. *(Bugün: gri "Analiz" etiketi — anlamı belirsiz.)*
- Verilen bir kamerada ≥ 10 sn değerlendirilemeyen kişi olduğunda, o zaman karo üstünde uyarı görünür (mevcut).
- Verilen günlük rapor, o zaman değerlendirilemeyen kişi-zaman oranı ihlal sayısının yanında gösterilir (**eksik**).

**US-05 — Kamera izlenemiyor** (FR-05) · mevcut (simülasyon) · P0
Operatör olarak, bir kameranın bağlantısı koptuğunda veya görüntüsü donduğunda bunu açıkça görmek istiyorum, böylece
o alanın izlenmediğini bilirim.
- Verilen kamera akışı kesildiğinde, o zaman ~6 sn içinde "izlenemiyor/yeniden bağlanılıyor" görünür ve run
  "Bağlantı koptu" olarak kapanır (simülasyonda PASS; gerçek RTSP ile BLOCKED).

## 2. Geçmiş ve raporlama

**US-06 — Vardiya sonu gözden geçirme** (FR-06) · mevcut · P1
İSG uzmanı olarak, geçmiş ihlalleri kamera/tarih/türe göre filtreleyip detay ve kanıtı görmek istiyorum.

**US-07 — Eğilim raporu** (FR-06, FR-07) · kısmi · P1
Saha yöneticisi olarak, kamera bazında günlük tekil ihlal sayılarını görmek istiyorum; doğrulanmış ve doğrulanmamış
sayıların ayrı olmasını istiyorum, böylece yanlış alarmlar kararlarımı çarpıtmaz.
- Bugün günlük sayılar var; doğrulanmış/doğrulanmamış ayrımı FR-07'ye bağlı (**eksik**).

**US-08 — Rapor dışa aktarma** (FR-16) · eksik, ihtiyaç doğrulanmadı · P3

## 3. Yönetim ve güvenlik

**US-09 — Kullanıcı ve kapsam yönetimi** (FR-09, FR-10) · kısmi · P2
Admin olarak, kullanıcı açmak, rol ve kamera kapsamı atamak, hesabı devre dışı bırakmak istiyorum.
- Bugün: API ve CLI ile mevcut ve test edilmiş; **ekran yok**.

**US-10 — Denetim kaydı** (FR-11) · kısmi · P1
Admin/veri sorumlusu olarak, kim hangi kanıt görüntüsüne ne zaman eriştiğini görmek istiyorum.
- Bugün: kayıt tutuluyor (`audit_log`, snapshot erişimi dahil) ve API'den okunuyor; ekran ve saklama süresi yok.

**US-11 — Saklama ve imha** (FR-12) · **eksik** · P0
Veri sorumlusu olarak, kanıt görüntülerinin ve olay kayıtlarının onaylanan süre sonunda imha edilmesini ve imhanın
kayıt altına alınmasını istiyorum.
- Bugün: yalnız rapor/karantina aracı var; silme yok.

**US-12 — Oturum sonrası gizlilik** (NFR-05) · mevcut · P0
Supervisor olarak, çıkış yaptığımda veya oturumum dolduğunda ekranda hiçbir kanıt/canlı görüntü kalmamasını
istiyorum. (Otomatik testlerde PASS; ana ortam manuel kabulü yapılmadı — MANUAL_ACCEPTANCE_CHECKLIST.)

**US-13 — Sağlık ve kapsam izleme** (NFR-02, K-08) · kısmi · P1
Admin olarak, her kameranın son kare yaşını, analiz durumunu ve outbox birikmesini görmek; geçmişe dönük kamera
çalışma süresini raporlayabilmek istiyorum.
- Bugün: anlık değerler `/api/ops/metrics`'te var; geçmişe dönük uptime yok, sayaçlar yeniden başlatmada sıfırlanır.

## 4. Kapsam dışı (hikâye yazılmaz)

Yüz tanıma, kişi takibi/puanlama, otomatik disiplin, kişi bazında performans raporu, görüntülerin onaysız dış
kanallara gönderilmesi. Bu yönde gelen talepler PRD §2'ye göre reddedilir.
