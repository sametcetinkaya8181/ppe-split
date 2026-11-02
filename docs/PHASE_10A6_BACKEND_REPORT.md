# FAZ 10-A.6 raporu — Backend insan incelemesi ve incident doğrulama

Tarih: 2026-11-02 (UTC). Başlangıç: `4660136` (FAZ 10-A.5B). Bu bir ML fazı değildir.

**Kapsam dışı ve değişmeyenler:** `best.pt` (SHA-256 `6886ad2f…`), `ppe_core.py`/PPEMonitor, IncidentTracker,
ByteTrack yapılandırması, alarm eşikleri, ana PostgreSQL kayıtları (ana DB **0006**'da; migration **uygulanmadı**),
üretim snapshot ve outbox dosyaları, ana `ppe-dashboard:latest` imajı (yeniden oluşturulmadı) ve çalışan ana
dashboard. FAZ 10-A.5B adayları üretime alınmadı. FAZ 10-A.1 ana dashboard güncellemesi ve 24 saatlik soak
**başlatılmadı**. Başka projelere dokunulmadı. İki alarm (10-A.5A inceleme paketi) **UNREVIEWED** — hiçbir karar
otomatik verilmedi.

**Bu faz şantiye pilotu için GO anlamına gelmez.** Testler ve kod incelemesi geçti; üretime alma ayrı bir onaylı adım
ve gerçek yetkili kullanıcı iş akışı gerektirir (§9).

## 0. Kabul özeti

| Teslimat | Durum |
|---|---|
| İnsan inceleme veri modeli + güvenli migration (0007) | **TAMAM** — eklemeli; mevcut tablolarda satır değişmez; veri varsa downgrade durur |
| Kamera yetkisiyle korunan REST API | **TAMAM** — supervisor+ yazar, viewer sonucu görür, kapsam dışı 404, CSRF + aynı köken |
| Karar değişiklik geçmişi | **TAMAM** — `incident_review_history` yalnız ekleme + `audit_log` (aynı transaction) |
| İnceleme durumuna göre filtre ve sayım | **TAMAM** — `review_status` filtresi, `by_review_status` özeti |
| API + veritabanı testleri | **TAMAM** — 36 yeni test (32 inceleme + 4 rol); dashboard **374/374**, tarayıcı **30/30**, dedektör **317/317** |
| Geçmiş olaylarla geriye uyumluluk | **TAMAM** — eski alanlar aynen; eski incident'lar UNREVIEWED; yaşam döngüsü ve mevcut sayımlar değişmez |
| FAZ 10-A.7 API sözleşmesi | **TAMAM** — §7 |
| Ana sisteme dağıtım | **YAPILMADI** (kapsam dışı; adımlar §8) |

## 1. Ön inceleme ve mimari uyum (A)

| Kalem | Bulgu / karar |
|---|---|
| Git | `main`, HEAD `4660136`; izlenmeyen `docs/PPE_Saha_Hazirlik_Claude_Opus_Plani.md` kullanıcıya ait — **değiştirilmedi, commit'e eklenmedi** (yalnız okundu: FAZ 10-A.2 için ayrı PRD/USER_STORIES/FEATURE_GAP_MATRIX belgesi depoda **yok**; plan "shadow mode, insan incelemesi, otomatik ceza yok" ilkesini içeriyor — uyuldu) |
| Mevcut altyapı | FastAPI + SQLAlchemy Core + PostgreSQL 17 + Alembic (0001–0006); oturum + RBAC (`viewer < supervisor < admin`) + `camera_scope`; CSRF (`X-CSRF-Token` + Origin); yalnız ekleme `audit_log`; geçmiş sorguları `db/history.py`; snapshot erişimi supervisor+ ve kapsam |
| Var olan benzer özellik | İnceleme/onay sistemi **yok**. Denetim için mevcut `audit_log` + `store.audit` **yeniden kullanıldı** (ikinci denetim sistemi kurulmadı); izin listesine yalnız 4 anahtar eklendi |
| Ad çakışması | Mevcut API'de `verified` = "start olayı var" (insan doğrulaması **değil**). İnsan kararı bu nedenle ayrı `review` nesnesinde, `review_status` adıyla; `verified` anlamı değişmedi ve raporda açıkça ayrıldı |
| Durum adları | İstenen dört ad aynen kullanıldı: `UNREVIEWED`, `CONFIRMED_VIOLATION`, `FALSE_ALARM`, `INCONCLUSIVE` (yaşam döngüsü `active/closed/stale/run_end/orphan/unknown` ile çakışmaz) |

## 2. Durumlar ve ayrım (B)

- **UNREVIEWED = `incident_reviews`'da satır yok.** Eski kayıtlar için geri doldurma yapılmadı; hiçbir kayda sessizce
  etiket atanmadı. Migration mevcut tablolarda tek satır değiştirmez (test: önce/sonra tam satır parmak izi aynı).
- İnsan kararı yaşam döngüsünü **değiştirmez**: `status`, `status_reason`, `verified`, zamanlar, olay sayıları ve
  `/api/history/summary`'deki mevcut `incidents`/`by_status` sayımları aynı kalır (testli). İnceleme sayımı ayrı:
  `by_review_status`.
- Kararlar yalnız `POST` ile, kimliği doğrulanmış yetkili kullanıcıdan gelir; ingest yolu inceleme tablolarına
  yazmaz. İnceleme sonrası gelen `ongoing/end` ve dedektör tekrar gönderimleri kararı değiştirmez (testli).

## 3. Veritabanı tasarımı (C)

**Migration `0007_incident_reviews`** (eklemeli, `0001–0006` değişmez):

| Tablo | Alanlar | Kısıtlar |
|---|---|---|
| `incident_reviews` (güncel) | `incident_id` PK/FK→`incidents` (RESTRICT), `status`, `version`, `reviewed_by` FK→`app_users` (RESTRICT), `reviewed_at` (UTC, `timestamptz`), `note`, `annotation_candidate` | `status` ∈ 3 karar; `version ≥ 1`; `note` 1–500 karakter; `ix_incident_reviews_status` |
| `incident_review_history` (yalnız ekleme) | `id` identity, `incident_id` FK, `version`, `status`, `previous_status`, `reviewer_id` FK, `decided_at`, `note`, `annotation_candidate`, `request_id` | `UNIQUE (incident_id, version)`, `UNIQUE request_id`, `(version = 1) = (previous_status IS NULL)`, durum/not kısıtları |

- **Yalnız ekleme:** `db.roles.grants` `incident_review_history` için `UPDATE`'i geri alır (DELETE/TRUNCATE uygulama
  rolünde zaten yok). `ppe_app` testte: `UPDATE/DELETE` geçmiş, `DELETE` güncel karar, `UPDATE audit_log` → **42501**.
- **İyimser kilit:** istemci gördüğü `version`'ı (`UNREVIEWED` = 0) `expected_version` olarak gönderir. İlk karar
  `INSERT … ON CONFLICT DO NOTHING` (PK), değişiklik `UPDATE … WHERE version = :beklenen` ile atomik; satır kilidi
  (`FOR UPDATE`) doğrulama ile yazma arasını korur. Uyuşmazlık → **409** + güncel durum. Veritabanı düzeyinde
  `UNIQUE (incident_id, version)` ikinci güvence.
- **Tekrar gönderim:** `request_id` (istemci UUID) tekildir. Aynı istek yeniden gelirse yeni satır/denetim kaydı
  yazılmaz, `replayed: true` ile güncel durum döner; eşzamanlı aynı istek de tek yazımla sonuçlanır. Aynı anahtar
  başka karar/incident için kullanılırsa **422 `request_id_reused`** (eşzamanlı durumda da 500 değil — kod
  incelemesinde bulunup düzeltildi, testli).
- **Karar + denetim kaydı tek transaction:** biri yazılamazsa ikisi de yazılmaz.
- **Downgrade:** inceleme verisi varsa durur (`-x allow_data_loss=1` ile bilerek); boşken 0006'ya iner.
- `scripts/ppe_ops.py` yedek doğrulama sayım listesine yeni tablolar **eklenmedi**: ana DB henüz 0006'da olduğundan
  liste bugünkü yedeği bozardı. `pg_dump` yeni tabloları zaten kapsar. Ana DB 0007'ye geçince listeye eklenmeli.

## 4. API ve yetkilendirme (D)

| Uç | viewer | supervisor | admin | Not |
|---|---|---|---|---|
| `GET /api/history/incidents` (+ `review_status` filtresi) | ✓ kapsam | ✓ | ✓ | her öğede `review` |
| `GET /api/history/incidents/{id}` | ✓ kapsam | ✓ | ✓ | `review` |
| `GET /api/history/summary` | ✓ kapsam | ✓ | ✓ | `by_review_status` |
| `POST /api/history/incidents/{id}/review` | 403 | ✓ kapsam | ✓ kapsam | CSRF + aynı köken |
| `GET /api/history/incidents/{id}/reviews` | 403 | ✓ kapsam | ✓ kapsam | notlar ve inceleyen adları |
| `GET /api/auth/me` | | | | `permissions.review` eklendi |

- **Kamera kapsamı tüm rollere uygulanır** (kapsamlı admin dahil). Kapsam dışı ve var olmayan incident **aynı 404**
  gövdesini alır; yazma denemesi `audit_log`'a `denied / not_found_or_out_of_scope` olarak düşer.
- **Viewer:** karar sonucu, `version`, `reviewed_at` görür; inceleyen adı, not, etiketleme adaylığı ve geçmiş
  **verilmez** (kişisel veri en aza indirme). Snapshot kuralları değişmedi (viewer 403, kapsam dışı 404; testli).
- **Denetim kaydı:** `incident_review` olayı `success/failure/denied`; ayrıntıda yalnız `incident_id`, `camera_id`,
  `review_status`, `previous_status`, `version`, `reason`. **Serbest not denetim kaydına yazılmaz** (testli).
- **Rol ekleme yok.** Yeni yetki tek: supervisor'ın inceleme kararı.
- **Geriye uyumluluk:** liste/detay öğelerindeki eski 27 alanın tamamı aynen (testli); yeni alanlar yalnız
  eklendi. Mevcut dashboard arayüzü yeni alanları kullanmıyor ve bozulmadı (tarayıcı testi 30/30).

## 5. İnsan incelemesi ≠ ML ground-truth (E)

- Bir incident kararı (bu kişi-olay bir ihlal miydi?) **nesne düzeyinde etiket değildir**: kutu, sınıf, görünürlük,
  fiziksel kimlik içermez. Değerlendirme GT'si (`ppe-eval-video/1`) ayrı kalır; bu tablolar ondan bağımsızdır.
- `annotation_candidate` yalnız **aday işaretidir**: hiçbir dışa aktarım, veri seti veya eğitim ucu yoktur (test,
  uygulama rotalarında `export/dataset/annotation` yolunun olmadığını doğrular). Gelecekte aktarım için açık onay,
  veri hakları (KVKK aydınlatma/saklama) ve bağımsız anotasyon gerekir — bu fazda tasarlanmadı.
- FAZ 10-A.5A inceleme paketi (`review_pack/`) **değiştirilmedi** (124 kanıt dosyasının SHA-256'ları aynı); iki alarm
  `label: null` / UNREVIEWED.

## 6. Testler (F)

**İzole ortam:** varsayılan `docker compose --profile test` ana yığının `db` kümesinde çalışır (ayrı `ppe_test`
veritabanı, süper kullanıcıyla). Bu faz için yeni `tools/reliability/isolated_dashboard_tests.py` tüm dashboard
testlerini **ayrı, geçici bir PostgreSQL 17.11 container'ında** (aynı imaj digest'i, `--internal` Docker ağı, port
yayını yok, volume yok, her koşuda rastgele parola) çalıştırır; test imajı ayrı etiket (`ppe-dashboard:review-test` =
`faz9-rel` + `requirements-dev.txt`); kod salt okunur bağlanır; alt süreç ortamı üretim gizli değerlerine karşı
denetlenir (FAZ 10-A.5B `evaluation.isolation`); sonunda yalnız kendi etiketli container'ları ve ağı silinir.

| Paket | Sonuç |
|---|---|
| Dashboard, değişiklikten önce (izole küme, taban) | 338/338 PASS |
| Dashboard, son (izole küme) | **374/374 PASS** (338 + 36) |
| Yeni `tests/test_reviews.py` (32 test; 24 fonksiyon, 2'si parametreli): varsayılan UNREVIEWED + satır yok, yaşam döngüsü/sayım bağımsızlığı, yetki matrisi (anonim/viewer/kapsamlı supervisor/admin/kapsamlı admin, 404 eşdeğerliği, denied denetimi), CSRF ×3, viewer veri en aza indirme, `me.permissions.review`, snapshot kuralları, 8 geçersiz istek, `note_required`/`no_change`, karar değişikliği geçmişi + denetim, notun denetime yazılmaması, yalnız ekleme (42501 ×4), şema kısıtları ×4, tekrar gönderim + `request_id_reused`, eski sürüm 409, eşzamanlı 8 istek ×2 (tam bir kazanan), eşzamanlı aynı istek (tek yazım), eşzamanlı aynı anahtar + farklı karar (422), DB kesintisi 503 (sır sızmaz), filtre/sayım/kapsam, inceleme sonrası olay + tekrar gönderim, etiketleme adaylığı, devre dışı inceleyen, **0007 upgrade veri koruma** (tam satır parmak izi), **0007 downgrade koruması** | **32/32** |
| Güncellenen mevcut testler | uç matrisi (+1 uç), rol yetkileri (+2 tablo, +4 reddedilen ifade = 4 yeni test), migration head/tablo/kısıt sayısı (33→41), zaman sütunu sayısı (17→19), `/health/ready` revizyonu, yeni FK nedeniyle `TRUNCATE` listeleri |
| Eşzamanlılık testleri 5 kez tekrar | 5/5 kararlı |
| Tarayıcı (Edge headless, `ui_check.mjs`, izole fixture) | **30/30 PASS** (değişiklikten önceki sayıyla aynı; koşu, 409 dalındaki son düzeltmeden önceydi — o düzeltme yalnız POST hata dalını etkiler) |
| Dedektör | **317/317 PASS** (kod değişmedi) |
| Gizli bilgi denetimi | **PASS** |
| Ana sistem (salt okunur) | ana DB `alembic_version` = **0006**, `incident_reviews` yok, sayımlar `7/74/47/104/255` (kamera/run/incident/olay/rapor; 10-A.5A ile aynı), snapshot 51, `best.pt` aynı |

Not: ana kümede önceki fazlardan kalma `ppe_ui_test` ve `ppe_ui_empty_test` veritabanları var (bu fazda
oluşturulmadı; bu fazın UI fixture'ı izole kümede çalıştı). Silinmedi; temizlik kararı kullanıcıya aittir.

## 7. FAZ 10-A.7 için API sözleşmesi

Tüm uçlar oturum çerezi gerektirir; yazma uçları `X-CSRF-Token` (giriş/`/api/auth/me` yanıtındaki `csrf_token`) ve
aynı köken ister. Zamanlar ISO 8601 UTC (`Z`).

**`review` nesnesi** (liste/detay öğelerinde; supervisor+ için tam, viewer için yalnız ilk üç alan):

```json
{"status": "UNREVIEWED | CONFIRMED_VIOLATION | FALSE_ALARM | INCONCLUSIVE", "version": 0,
 "reviewed_at": null, "reviewed_by": null, "note": null, "annotation_candidate": false}
```

**Liste filtresi:** `GET /api/history/incidents?review_status=UNREVIEWED&review_status=INCONCLUSIVE` (tekrarlanabilir;
diğer filtreler, `total`, `next_cursor` aynen). Geçersiz değer → 422 (`loc: ["query","review_status"]`).
**Özet:** `GET /api/history/summary` → `by_review_status: {"UNREVIEWED": n, "CONFIRMED_VIOLATION": n,
"FALSE_ALARM": n, "INCONCLUSIVE": n}` (kapsam ve filtrelere uyar).

**Karar:** `POST /api/history/incidents/{incident_id}/review`

```json
{"status": "FALSE_ALARM", "expected_version": 0, "request_id": "<istemcinin ürettiği UUID>",
 "note": "isteğe bağlı, ≤500 karakter; mevcut kararı DEĞİŞTİRİRKEN zorunlu", "annotation_candidate": false}
```

| Yanıt | Gövde | İstemci davranışı |
|---|---|---|
| 200 | `{"incident_id", "review": {...tam}, "replayed": false}` | kaydedildi; yeni `version`'ı sakla |
| 200 | `… "replayed": true` | aynı `request_id` daha önce işlendi (çift tıklama / ağ tekrarı); başarı gibi davran |
| 409 | `{"detail": {"code": "version_conflict", "msg", "current": {...}}}` | başkası değiştirdi; `current`'ı göster, kullanıcı yeniden karar versin (yeni `request_id`) |
| 422 | `{"detail": [{"loc": ["body", alan], "msg", "type"}]}` | `type`: `invalid_status` (UNREVIEWED dahil), `note_required`, `no_change`, `request_id_reused`, pydantic doğrulama (`extra` alan, uzunluk, kontrol karakteri) |
| 401 / 403 / 404 / 503 | standart | oturum yok / rol veya CSRF / yok ya da kapsam dışı / DB kullanılamıyor (tekrar dene, aynı `request_id`) |

Kurallar: aynı karar + aynı adaylık tekrar gönderilemez (`no_change`); yalnız `annotation_candidate` değişimi de bir
karar sürümüdür (not zorunlu); karar geri alınıp `UNREVIEWED` yapılamaz (bilinçli; bkz. §9).

**Geçmiş:** `GET /api/history/incidents/{incident_id}/reviews` (supervisor+)

```json
{"incident_id": "…", "current": {...tam review},
 "history": [{"version": 1, "status": "FALSE_ALARM", "previous_status": null, "reviewed_by": "isg_uzmani",
              "decided_at": "…Z", "note": "…", "annotation_candidate": false}]}
```

**Yetki bayrağı:** `GET /api/auth/me` → `permissions.review` (supervisor+ için `true`).

## 8. Üretime alma adımları (uygulanmadı; ayrı onay gerekir)

1. Yedek: `python scripts/backup.py --quiesce` + `scripts/restore_check.py` ile doğrulama.
2. Bakım penceresinde: `docker compose build dashboard` → `docker compose up -d` (compose `migrate` servisi
   `alembic upgrade head && python -m db.roles grants` çalıştırır; `grants` yeni geçmiş tablosundan `UPDATE`'i geri alır).
3. Doğrulama: `/health/ready` admin ayrıntısında `revision: ["0007"]`; `python -m db.roles check` ile
   `incident_review_history` = `SELECT, INSERT`.
4. Dikkat: kod 0007'yi bekler — migration çalışmadan yeni imajla başlayan dashboard `/health/ready` 503 verir (bilinçli).
   Geri alma: inceleme verisi yokken `alembic downgrade 0006` + önceki imaj; veri varsa yedekten.
5. `scripts/ppe_ops.py` `TABLES`'a iki tabloyu ekle (yedek doğrulama sayımları).

Bu adımlar FAZ 10-A.1 ana dashboard güncellemesiyle birlikte planlanmalıdır; ikisi de bu fazda yapılmadı.

## 9. Kalan riskler

| Risk | Not / öneri |
|---|---|
| Arayüz yok | Kararlar şu an yalnız API ile verilebilir — FAZ 10-A.7 |
| Not alanı kişisel veri içerebilir | Serbest metin; saklama süresi ve KVKK aydınlatması (`PRIVACY_READINESS.md`) güncellenmeli; arayüz uyarı metni göstermeli |
| Tek inceleyici yeterli | "Dört göz" (ikinci onay) yok; İSG süreci gerektiriyorsa ayrı tasarım |
| Karar geri alınamaz (UNREVIEWED'a dönüş yok) | Bilinçli: geçmişin anlamı korunur; `INCONCLUSIVE` + gerekçe kullanılır |
| `review` uç hız sınırı yok | Yalnız oturumlu supervisor+; denetim kaydı var. Gerekirse kullanıcı başına sınır |
| Kapsam yazma anında denetlenir | Kullanıcının kapsamı sonradan daralırsa eski kararları geçmişte görünür kalır (doğru davranış), yeni yazamaz |
| Ana sistemde doğrulanmadı | Yalnız izole kümede test edildi; ana DB 0006 |
| `annotation_candidate` sınırlı | Yalnız işaret; dışa aktarım iş akışı (onay, haklar, anonimleştirme, bağımsız anotasyon) tasarlanmadı |
| Test bağımlılığı uyarısı | `starlette.testclient` + `httpx` kullanımdan kaldırma uyarısı (mevcut; işlevsel değil) |
| Önceki fazlardan kalan test DB'leri | ana kümede `ppe_ui_test`, `ppe_ui_empty_test` |

## 10. FAZ 10-A.7 için önerilen frontend işleri

1. Geçmiş listesinde `review.status` rozeti ve `review_status` filtresi (varsayılan görünüm: `UNREVIEWED` kuyruğu);
   özet kartında `by_review_status`.
2. Detay penceresinde karar paneli (yalnız `permissions.review`): dört durumun açıklaması, not alanı (500 karakter
   sayacı, kişisel veri uyarısı), değişiklikte not zorunluluğu, `expected_version` + gönderim başına yeni
   `request_id`, gönderim sırasında düğme kilidi.
3. 409 çakışma diyaloğu: "Bu karar {reviewed_by} tarafından {reviewed_at} değiştirildi" + güncel durum + yeniden
   karar seçeneği; 503'te aynı `request_id` ile yeniden dene.
4. Karar geçmişi zaman çizelgesi (`/reviews`), viewer'a gizli.
5. Viewer için salt okunur sonuç rozeti; inceleyen adı/not gösterilmez.
6. Etiketleme adaylığı onay kutusu: "Bu, eğitim verisi değildir; aktarım ayrı onay gerektirir" açıklamasıyla.
7. Klavye erişimi, 390 px düzen, `ui_check.mjs`'e senaryolar (karar ver, çakışma, viewer salt okunur, kapsam).
8. Sayımların yaşam döngüsü sayımlarından ayrı gösterilmesi (`verified` ≠ insan doğrulaması etiketi).

## 11. Değişen dosyalar

Yeni: `dashboard/alembic/versions/0007_incident_reviews.py`, `dashboard/db/reviews.py`,
`dashboard/tests/test_reviews.py`, `tools/reliability/isolated_dashboard_tests.py`, bu rapor.
Güncellenen: `dashboard/app.py` (iki yeni uç, `review` alanı, filtre), `dashboard/db/history.py` (LEFT JOIN, filtre,
`by_review_status`), `dashboard/db/models.py`, `dashboard/db/__init__.py`, `dashboard/db/roles.py` (yalnız ekleme
yetkisi), `dashboard/auth/store.py` (denetim izin listesi), `dashboard/auth/routes.py` (`permissions.review`),
`tools/reliability/common.py` (izole test yığını `reset_data` TRUNCATE listesi), mevcut testler (§6),
`docs/DATABASE.md`, `docs/SECURITY.md`.
