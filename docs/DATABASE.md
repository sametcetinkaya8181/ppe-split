# Veritabanı

PostgreSQL 17, şema yalnızca Alembic ile yönetilir (`create_all()` yok). Head: **0003**.

## Tablolar

| Tablo | Anahtar | Not |
|---|---|---|
| `cameras` | `id` (`^[A-Za-z0-9_-]{1,40}$`) | `name` = güncel ad, `source_kind`, `last_seen_at` |
| `detector_runs` | `id` (UUID) | `camera_id` FK, `status` (running/finished/error/interrupted), `started_at`, `finished_at`, `last_seen_at`, `fps`, `width`, `height`, `report_metadata` (JSONB, ≤256 KiB), **0003:** `source_label`, `source_kind`, `source_ref` |
| `incidents` | `id` (UUID) | tek bir `(run_id, camera_id, pid)` üçlüsüne bağlı |
| `violation_events` | `id` (identity), `event_id` UNIQUE | `incident_id` bileşik FK, `event_type`, ekipman bayrakları, oranlar, `video_time_sec` (video saniyesi) ≠ `occurred_at` (UTC), `ingested_at`, `box`, `snapshot_path` |
| `person_run_reports` | `(run_id, pid)` | run sonu kişi özeti (upsert) |

Kısıtlar: bir incident'ta en fazla bir start ve bir end (kısmi unique index), end olayında eksik ekipman yok,
snapshot yalnızca start'ta ve `^snapshots/[A-Za-z0-9_.-]+\.jpg$`.

## Migration'lar

| Rev | İçerik | Downgrade |
|---|---|---|
| 0001 | başlangıç şeması | — |
| 0002 | `incidents`, `report_metadata` | metadata doluysa onaysız durur |
| 0003 | `detector_runs.source_label/source_kind/source_ref` + 2 CHECK; **geri doldurma yok** | sütunlar doluysa onaysız durur (`-x allow_data_loss=1`) |
| 0004 | `app_users`, `user_sessions`, `audit_log` (FAZ 6) | kullanıcı/denetim kaydı varsa durur |
| 0005 | `detector_runs.status` += `abandoned`; `reconciled_at`, `reconcile_reason` (FAZ 7) | uzlaştırılmış run varsa durur |

### Çökmüş run uzlaştırması (FAZ 7)

`docker compose exec dashboard python -m maintenance.runs reconcile` — **dry run**: `running` + rapor yok + son
canlılık 60 dk'dan (en az 10) eski run'ları listeler. `--apply --confirm <adet>` ile `abandoned` yapar
(`finished_at` = son canlılık kanıtı), her run için `audit_log 'run_reconcile'`. Sonradan canlılık gelirse `running`,
rapor gelirse rapordaki durum geçerli olur. Ana DB'de FAZ 5'ten kalan 3 run aday olarak listelendi; **onay
verilmediği için uygulanmadı**.

Uygulama: `docker compose up -d` → `db-roles` → `migrate` (`alembic upgrade head && python -m db.roles grants`,
`ppe_migrator` rolüyle) → `dashboard`. ORM ↔ migration uyumu `test_orm_matches_migration` ile denetlenir.

## Roller (least privilege)

`python -m db.roles setup` (servis: `db-roles`, süper kullanıcıyla, idempotent):

| Rol | Yetkiler |
|---|---|
| `POSTGRES_USER` (`ppe`) | süper kullanıcı; yalnızca yönetim, yedek ve `db-roles` |
| `ppe_migrator` | tüm tabloların sahibi; şemada `CREATE`; SUPERUSER/CREATEDB/CREATEROLE **yok** |
| `ppe_app` | `CONNECT`, şema `USAGE`, tablolarda `SELECT, INSERT, UPDATE`, dizilerde `USAGE, SELECT`; `alembic_version`'da yalnız `SELECT`; DELETE/TRUNCATE/DDL/SUPERUSER/CREATEDB/CREATEROLE **yok** |

Ayrıca `PUBLIC`'ten veritabanı `CONNECT/TEMP` ve şema `CREATE` geri alınır; `ppe_migrator`'ın yeni tabloları için
`ALTER DEFAULT PRIVILEGES` ile `ppe_app` yetkileri otomatik verilir. Parolalar istemci tarafında SQL literal'ine
gömülür; çıktıya/loga yazılmaz; PostgreSQL'de SCRAM-SHA-256 özeti olarak saklanır.

`PPE_APP_PASSWORD`/`PPE_MIGRATOR_PASSWORD` boşsa `db-roles` hiçbir şey yapmaz (eski tek-rol kurulumu çalışır).

Doğrulama: `docker compose exec dashboard python -m db.roles check`

## Geçmiş sorguları ve performans

İzole `ppe_perf_test` veritabanında ölçüldü (`python -m tests.perf_history`; 30 000 incident, 297 000 olay,
RTX 3050 dizüstü, Docker Desktop). En iyi süre / 3 deneme:

| Sorgu | ms |
|---|---:|
| incidents, filtresiz, 1. ve 2. sayfa (cursor) | 261 / 272 |
| incidents, 7 gün | 124 |
| incidents, 7 gün + kamera | 71 |
| incidents, run filtresi | 15 |
| incident detay + zaman çizelgesi | 7 |
| summary filtresiz / 30 gün (Europe/Istanbul) / 30 gün + kamera | 481 / 488 / 228 |
| runs 1. sayfa / run detay / cameras | 84 / 13 / 91 |

`EXPLAIN ANALYZE`: maliyet, olayların incident bazında toplanmasıdır (tam tablo hash join). Denenen
`(occurred_at, incident_id)` indeksi yalnız "7 gün" listesini 109 → 82 ms iyileştirdi, özetleri değiştirmedi; bu
yüzden **yeni indeks/migration eklenmedi**. Süreler olay sayısıyla yaklaşık doğrusal büyür. ~1–3 milyon olayın
üzerinde (özet > ~2–5 sn) önerilen adım: ingest sırasında güncellenen `incident_summary` tablosu (yeni migration).

## Saklama (retention) — öneri, uygulanmadı

Bu sürümde **otomatik silme yoktur** ve `ppe_app` rolünün DELETE yetkisi de yoktur.
Analiz: `docker compose exec dashboard python -m maintenance.storage retention`.

Ongoing olayı, süren ihlal başına ~3 sn'de bir (`REALERT_SEC`) yazılır; uzun ihlallerde hacmin çoğunu oluşturur.
Önerilen politika (ileride, ayrı onayla):

1. `start`/`end` olayları, `incidents`, `detector_runs`, `person_run_reports` **süresiz** tutulur — tüm sayımlar,
   durumlar ve raporlar bunlardan hesaplanır.
2. `ongoing` olayları N gün (ör. 90) sonra silinebilir. Kayıp: o ihlallerin zaman çizelgesindeki ara noktalar,
   `ongoing_count`, `video_last_sec` ve "ham olay sayısı" geçmişe dönük değişir; incident sayıları, ekipman
   (start'tan) ve durumlar değişmez. Bu yüzden silmeden önce incident başına `ongoing_count`/son zaman özetlenmeli.
3. Silme yönetim rolüyle, yedekten sonra, dry-run raporuyla yapılmalı.

## Sahipsiz snapshot bakımı

`docker compose exec dashboard python -m maintenance.storage snapshots` — **DRY RUN**: yalnız `alert_<uuid>.jpg` ve
`.tmp-*.jpg` değerlendirilir; eski `alert_<sıra>_*` adları, sembolik bağlar ve 24 saatten (en az 1 sa) yeni dosyalar
dokunulmaz. `--quarantine --confirm <adet>` dosyaları `data/quarantine/<zaman>/` altına **taşır** (silmez; adet
eşleşmezse hiçbir şey yapmaz; taşımadan hemen önce DB'de yeniden arar).
