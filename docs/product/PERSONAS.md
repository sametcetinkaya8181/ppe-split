# Personalar ve rol matrisi

**Sürüm:** taslak v0.1 — 2026-11-02 (FAZ 10-A.2)
**Önemli:** Aşağıdaki personalar **varsayımdır**; hiçbir kullanıcı görüşmesi yapılmadı. Her personanın "doğrulanacak
varsayımlar" satırı [STAKEHOLDER_INTERVIEW_GUIDE.md](STAKEHOLDER_INTERVIEW_GUIDE.md) ile test edilmelidir.
Gerçek kişi adı, şirket veya alıntı içermez.

## 1. Müşteri türleri (bağlam)

| Tür | Tipik durum | Ürünün bugünkü uyumu | Not |
|---|---|---|---|
| A. Tek sahalı küçük/orta yüklenici | 1 saha, birkaç kamera, yarı zamanlı İSG uzmanı veya OSGB hizmeti | **İyi** — tek bilgisayar, yerel kurulum | Öncelikli aday |
| B. Çok kameralı büyük yüklenici | 1–3 büyük saha, 10+ kamera, tam zamanlı İSG ekibi | Kısmi — kapasite hedef donanımda ölçülmedi (NFR-03) | Kapasite ve rol ayrımı ihtiyacı |
| C. OSGB / İSG hizmet firması | Birden çok müşterinin sahası | Zayıf — `site_id`/çok kiracı yok (FR-15) | Hukuki rol (veri işleyen) karmaşık |
| D. Merkezden çok saha izleme | Merkezi operasyon odası | Zayıf — LAN/TLS var, çok saha mimarisi yok | Ağ ve gizlilik tasarımı gerekir |

Öneri (onay değil): **A** ile başlanması; B için kapasite ve rol ihtiyacı görüşmelerle ölçülmeli. C/D yalnız
doğrulanmış ihtiyaçla.

## 2. Personalar

### P1 — Şantiye İSG uzmanı / sorumlusu (öncelikli)

| | |
|---|---|
| Amaç | Baret/yelek eksikliğini erken görüp yerinde düzeltmek; hangi alanda sorun yoğunlaşıyor görmek; kayıt tutmak |
| Bugünkü ürün rolü | `supervisor` (atanan kameralar; canlı görüntü + kanıt görüntüsü) |
| Kullandığı ekranlar | Canlı İzleme, İhlal Geçmişi + detay, İstatistikler, Run Raporları |
| Kaygılar (varsayım) | Yanlış alarm yorgunluğu; "sistem gördü" diye sorumluluğun kendisine kalması; çalışanlarla güven ilişkisi |
| Ürünün ona söz vermemesi gereken şey | "Tüm ihlaller yakalanır", "devriye gerekmez" |
| Eksik olan (repo doğrulamalı) | Uyarıyı **doğrulama/yanlış alarm işaretleme** kaydı (FR-07); kaçırılan ihlal bildirimi (FR-08) |
| Doğrulanacak varsayımlar | Kaç kamerayı, günün hangi saatlerinde izleyebilir? Kabul edebileceği yanlış alarm yükü? Uyarıya tepki süresi? |

### P2 — İzleme operatörü

| | |
|---|---|
| Amaç | Ekranı sürekli izleyip adayları ilk elemeden geçirmek, İSG'ye iletmek |
| Bugünkü ürün rolü | Ayrı rol yok; `supervisor` verilirse tüm kanıt görüntülerine erişir |
| Kaygılar (varsayım) | Çok kamerada dikkat dağınıklığı; "Analiz" etiketinin ne anlama geldiği |
| Eksik / belirsiz | Ayrı "reviewer" rolünün gerekip gerekmediği **doğrulanmadı** (PRD K2); inceleme kuyruğu yok |
| Doğrulanacak varsayımlar | Sahalarda böyle bir rol var mı, yoksa İSG uzmanı mı izliyor? |

### P3 — Saha / proje yöneticisi

| | |
|---|---|
| Amaç | Eğilimleri görmek, alt yükleniciyle konuşmak, eğitim planlamak |
| Bugünkü ürün rolü | `viewer` (görüntüsüz istatistik ve liste) |
| Kaygılar (varsayım) | Sayıların doğruluğu; doğrulanmamış adayların "ihlal" olarak raporlanması |
| Eksik | Doğrulanmış / doğrulanmamış ayrımı olan rapor (FR-07'ye bağlı); dışa aktarma (FR-16, ihtiyaç doğrulanmadı) |

### P4 — İşletme admin'i (BT / sistem yöneticisi)

| | |
|---|---|
| Amaç | Kurulum, kullanıcı/kamera yönetimi, yedek, güncelleme, sağlık izleme |
| Bugünkü ürün rolü | `admin` (kullanıcı yönetimi API/CLI, denetim kaydı API, `/api/ops/metrics`) |
| Eksik | Kullanıcı yönetimi ve denetim kaydı **ekranı** yok (FR-10, FR-11); geçmişe dönük kamera uptime yok |
| Kaygılar (varsayım) | Windows dosya izinleri, şifresiz yedek, güncelleme sonrası geri dönüş |

### P5 — Veri sorumlusu / KVKK sorumlusu / hukuk

| | |
|---|---|
| Amaç | İşlemenin hukuka uygunluğu, aydınlatma, saklama/imha, ilgili kişi başvuruları |
| Sistemle ilişkisi | Çoğunlukla kullanıcı değil; karar ve politika sahibi |
| Bekleyen kararlar | Dayanak, aydınlatma metni, saklama süreleri, VERBİS, erişim matrisi onayı, lisans yolu |
| Eksik (repo doğrulamalı) | Otomatik saklama/imha (FR-12), üçüncü kişi maskeleme (FR-13), audit saklama süresi |

### Dolaylı paydaş — Sahadaki çalışan (izlenen kişi)

Sistemi kullanmaz ama verisi işlenir. Aydınlatılmalı, itiraz/başvuru kanalı olmalı, görüntüsü yalnız amaçla sınırlı
kullanılmalıdır. Ürün kişiyi tanımaz; iz ID run içi takma addır.

## 3. Rol matrisi (bugünkü sistem — repo doğrulamalı)

| Yetki | viewer | supervisor | admin | Kaynak |
|---|---|---|---|---|
| İstatistik, ihlal listesi (görüntüsüz), run raporu | ✓ (kapsamdaki kameralar) | ✓ | ✓ | `auth/routes.py` `/api/auth/me` permissions |
| Canlı görüntü (`/ws`), kanıt görüntüsü (`/snapshots`) | — | ✓ (kapsamdaki kameralar) | ✓ | `app.py` `require("supervisor")` |
| Kullanıcı yönetimi, oturum iptali, denetim kaydı | — | — | ✓ (API/CLI) | `/api/admin/*` |
| İşletim metrikleri | — | — | ✓ | `/api/ops/metrics` |
| Kamera kapsamı | `camera_scope` (NULL = tümü) | aynı | aynı | `app_users.camera_scope` |
| Uyarıyı doğrulama / yanlış alarm işaretleme | — | **yok (eksik)** | — | review alanı yok |

Önerilen eşleme (onay bekliyor): P1 → supervisor; P2 → supervisor veya yeni reviewer (K2); P3 → viewer;
P4 → admin (az sayıda); P5 → sistem hesabı yok, gerekiyorsa denetim kaydı için sınırlı erişim.
