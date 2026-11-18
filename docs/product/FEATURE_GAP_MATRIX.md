# Özellik envanteri ve boşluk matrisi (gap analysis)

**Sürüm:** taslak v0.1 — 2026-11-02 (FAZ 10-A.2)
**Yöntem:** Yalnız repo dosyaları incelenerek (HEAD `d67854d` + commit edilmemiş FAZ 10-A.1 değişiklikleri; bu
değişiklikler yalnız DB erişim katmanı ve test araçlarıdır, özellik kümesini değiştirmez). Raporlardaki iddialar
envantere kanıt sayılmadı; her satır dosya/fonksiyon konumu gösterir. "Eksik" yalnız ilgili dosyalarda arama ve
okuma ile bulunamayan özellik için yazıldı; sahada kanıtı olmayanlar "doğrulanmamış".
Amaç refactor değil, PRD gereksinimlerinin bugünkü karşılığını göstermektir.

## 1. Mevcut özellik envanteri

### 1.1 Dashboard API (`dashboard/app.py`, `dashboard/auth/routes.py`)

| Uç | Yetki | İşlev |
|---|---|---|
| `POST /ingest/frame`, `/ingest/events`, `/ingest/report` | ingest token | Dedektörden kare, olay, run raporu (idempotent; çelişkide 409/422) |
| `GET /` | — | Tek sayfa arayüz (`static/index.html`) |
| `WS /ws` | supervisor, aynı köken, ~15 sn'de oturum yeniden doğrulama | Canlı kareler (kapsamdaki kameralar) |
| `GET /video_feed/{cid}` | supervisor | MJPEG akışı (arayüz kullanmıyor) |
| `GET /api/state`, `/api/alerts`, `/api/report` | supervisor | Canlı durum, son uyarılar, son run raporu |
| `GET /snapshots/{name}` | supervisor + kamera kapsamı; kapsam dışı 404 | Kanıt görüntüsü; erişim denetim kaydına yazılır |
| `GET /api/history/incidents[/{id}]`, `/summary`, `/cameras`, `/runs[/{id}]` | viewer+ + kapsam | Geçmiş, istatistik, run raporları |
| `GET /api/ops/metrics` | admin | Kamera anlık sağlık/kapsam/outbox, HTTP/DB/WS sayaçları |
| `GET /healthz`, `/health/ready` | — | Canlılık / hazırlık (DB + şema revizyonu) |
| `POST /api/auth/login`, `/logout`, `/password`; `GET /api/auth/me` | — / oturum | Oturum, CSRF, parola değişimi |
| `GET/POST/PATCH /api/admin/users…`, `/revoke-sessions`, `GET /api/admin/audit` | admin | Kullanıcı yönetimi, oturum iptali, denetim kaydı |

### 1.2 Veritabanı (`dashboard/db/models.py`, Alembic 0001–0006)

| Tablo | İçerik |
|---|---|
| `cameras` | kamera kimliği, ad, kaynak türü |
| `detector_runs` | run durumu (`running/finished/error/interrupted/abandoned/disconnected`), süre, kaynak, rapor metadata |
| `incidents` | (run, kamera, pid) başına ihlal yaşam döngüsü kimliği |
| `violation_events` | start/ongoing/end olayları, eksik ekipman, oranlar, kutu, video zamanı, `occurred_at`, `ingested_at`, snapshot yolu |
| `person_run_reports` | run başına iz (kişi) raporu |
| `app_users`, `user_sessions`, `audit_log` | kullanıcı (rol, `camera_scope`), oturum, denetim |

**Bulunmayan:** inceleme/doğrulama kararı, not, yanlış alarm, `site_id`, saklama/imha kaydı, bildirim tablosu.

### 1.3 Arayüz (`dashboard/static/index.html`)

Sekmeler: **Canlı İzleme** (kamera karoları, kişi tablosu `Uygun/İhlal/Analiz`, ihlal akışı, **Analiz kapsamı**
paneli, "İZLENEMİYOR" durumu, run raporu yükleme) · **İhlal Geçmişi** (filtre, detay, kanıt) · **İstatistikler**
(son 30 gün) · **Kamera / Run Raporları** (durum etiketleri, "Bağlantı koptu" filtresi). Giriş/çıkış ve çıkışta
içerik temizliği.
**Bulunmayan (arama: `api/admin`, `audit`, `Audio`, `Notification`, `csv`, `export` → 0 eşleşme):** kullanıcı
yönetimi ekranı, denetim kaydı ekranı, sesli/tarayıcı bildirimi, dışa aktarma, inceleme kuyruğu.

### 1.4 Dedektör (`detector/`)

| Bileşen | İşlev |
|---|---|
| `detector.py` | YOLO (`best.pt`) + ByteTrack, çok kaynak (dosya/RTSP/kamera), üstel yeniden bağlanma, bitiş nedeni sınıflandırması, "bağlantı yok" yer tutucu karesi |
| `ppe_core.py` `PPEMonitor` | Kişi–ekipman eşleme; güvenilirlik kapısı (`PERSON_CONF_MIN` 0.40, `MIN_PERSON_H_PX` 60, `EDGE_MARGIN_PX` 6); 4 sn pencere, `MIN_SPAN_SEC` 2, giriş/çıkış oranları; durumlar `UYGUN / IHLAL / ANALIZ` |
| `protocol.py` `IncidentTracker` | olay kimlikleri, incident yaşam döngüsü (pid başına) |
| `outbox.py` | SQLite WAL kalıcı kuyruk, yeniden deneme, replay, ölü/karantina |
| `telemetry.py` | kapsam sayaçları (değerlendirilemeyen track-kare ve nedenleri, ≥10 sn değerlendirilemeyen kişi) |
| `evaluation/` | değerlendirme araçları: GT şeması `ppe-eval-video/1`, metrikler (kişi-kare, incident, takip, kamera-saat), dataset/manifest/sızıntı kontrolü (37 test fonksiyonu) |

### 1.5 Operasyon araçları

`scripts/backup.py`, `restore_check.py` (izole geri yükleme), `maintenance.runs reconcile` (dry-run varsayılan),
`maintenance.storage` (sahipsiz snapshot raporu/karantina, saklama **analizi** — silme yok), `deploy/lan` (Caddy TLS),
`tools/reliability/*` (yük, arıza, soak, bellek), `scripts/windows_acl.ps1` (ACL envanter/daraltma/geri alma).

## 2. Gereksinim ↔ mevcut karşılık

| Gereksinim | Durum | Mevcut karşılık (kanıt) | Boşluk | Önerilen iş (faz) |
|---|---|---|---|---|
| FR-01 ihlal adayı | mevcut / saha **doğrulanmamış** | `ppe_core.PPEMonitor.update`; dedektör testleri | Saha recall/FA ölçülmedi (FAZ 8 BLOCKED) | 10-A.3/4 |
| FR-02 kanıt görüntüsü | mevcut | `app.py` `snapshot()` — supervisor + kapsam, 404, audit | — | — |
| FR-03 canlı izleme | mevcut | `/ws`, `/api/state`, `renderPeople`, karolar | Ana ortam manuel kabul yapılmadı | 10-A.1 |
| FR-04 kapsam / UNKNOWN | kısmi | `renderCoverage`, `telemetry.gate_reasons`, uyarı notu "Uygun sayılmaz" | Etiket "Analiz" belirsiz; geçmiş/istatistikte kapsam yok; kapsam eşiği yok | 10-A.7 (+ İSG eşiği) |
| FR-05 kamera kopması | mevcut / gerçek RTSP **doğrulanmamış** | migration 0006, `classify_end`, "İZLENEMİYOR" | Gerçek kamera testi | 10-A.6 |
| FR-06 geçmiş/istatistik | mevcut | `/api/history/*`, 3 sekme | — | — |
| FR-07 insan doğrulama kaydı | **eksik** | — (models'de alan/tablo yok, UI'da kontrol yok) | Tümü | 10-A.6 + 10-A.7 |
| FR-08 kaçırılan ihlal bildirimi | **eksik** | — | Tümü | 10-A.7 |
| FR-09 roller + kapsam | mevcut | `auth/store.ROLES`, `camera_scope`, RBAC testleri | reviewer rolü ihtiyacı doğrulanmadı | 10-A.2 görüşmeleri |
| FR-10 kullanıcı yönetimi ekranı | kısmi | API + `auth.cli` | UI | 10-A.7 (P2) |
| FR-11 denetim kaydı | kısmi | `audit_log`, `/api/admin/audit` | UI, saklama süresi, kurcalamaya dayanıklılık | 10-A.6 |
| FR-12 saklama/imha | **eksik** | `maintenance.storage retention` yalnız rapor | Politika + imha + imha kaydı | 10-A.6 (KVKK kararı sonrası) |
| FR-13 üçüncü kişi maskeleme | **eksik** | — | Tümü | 10-A.6 |
| FR-14 bildirim | **eksik** | — | Kapsam kararı | Karar sonrası |
| FR-15 çok saha | **eksik** | — (`site_id` yok) | İhtiyaç doğrulanmadı | Karar sonrası |
| FR-16 dışa aktarma | **eksik** | — | İhtiyaç doğrulanmadı | Karar sonrası |
| FR-17 duplicate/ID switch | **eksik** (davranış ölçüldü) | `IncidentTracker` pid başına; FAZ 8 §11 sentetik: yeni ID → yeni incident | Birleştirme/işaretleme | 10-A.5 (ölçüm sonrası) |
| FR-18 örtülme → bilinmiyor | **eksik** | Kapı yalnız güven/boy/kenar (`gate_reasons`) | Örtülme algısı | 10-A.5 |
| NFR-01 teslim güvenilirliği | mevcut | outbox + 4 sa soak 0 kayıp/0 çift | — | — |
| NFR-02 24 sa kararlılık | **doğrulanmamış** | soak aracı hazır | 24 sa GPU koşusu | 10-A.1/10-A.6 |
| NFR-03 hedef donanım kapasitesi | **doğrulanmamış** | dizüstü 4 kamera | Hedef donanım | 10-A.6 |
| NFR-04 uyarı gecikmesi | kısmi | `occurred_at`/`ingested_at` (teslim gecikmesi hesaplanabilir); karar gecikmesi sentetik | Operatör ekranına görünme ölçülmüyor | 10-A.4/6 |
| NFR-06 veri güvenliği | **eksik / açık bulgu** | ACL aracı, yedek şifreleme tasarımı (uygulanmadı) | Uygulama + onay | 10-A.1/10-A.6 |
| NFR-08 dış veri akışı yok | mevcut | `YOLO_OFFLINE=1` testi | — | — |
| NFR-10/11 lisans, KVKK | **karar eksik** | LICENSE_REVIEW, PRIVACY_READINESS (teknik taslak) | Yazılı karar | Hukuk / veri sorumlusu |

## 3. KPI ölçülebilirliği (bugünkü veriyle)

| KPI (ACCEPTANCE_METRICS) | Bugün ölçülebilir mi | Kaynak | Eksik |
|---|---|---|---|
| Incident recall | Araç var, **veri yok** | `evaluation/metrics.py` | Etiketli saha videosu |
| Yanlış alarm / kamera-saat | Araç var, veri yok; sahada insan kararı kaydı yok | `evaluation/`; FR-07 | Etiket veya FR-07 |
| Kapsam / UNKNOWN oranı | Evet (anlık + run toplamı) | `telemetry`, `/api/ops/metrics` | Kalıcı geçmiş kaydı |
| Duplicate / ID switch | Araç var (GT gerekli) | `evaluation/temporal.py` | Etiketli veri |
| Teslim gecikmesi (olay → DB) | Evet | `violation_events.occurred_at`, `ingested_at` | — |
| Uyarı gecikmesi (olay → operatör ekranı) | Hayır | — | İstemci tarafı ölçüm |
| Kamera uptime | Kısmen (anlık; run süreleri) | `/api/ops/metrics`, `detector_runs` | Kalıcı sağlık zaman serisi |
| Outbox kalıcı teslim | Evet | outbox durum dosyası, ops metrics | — |
| İnsan doğrulama süresi | Hayır | — | FR-07 |
