# Yedekleme ve geri yükleme

PostgreSQL ve `data/snapshots` **birlikte** yedeklenir. Betikler yalnız Python standart kütüphanesi ve
`docker compose` kullanır; `pg_dump`/`pg_restore` db container'ında yerel soketle çalışır (host'a parola gerekmez).

## Yedek alma

```powershell
python scripts/backup.py --quiesce     # önerilen: yedek süresince dashboard durur, sonra başlatılır
python scripts/backup.py               # canlı; tutarlılık notuna bakın
```

Çıktı `backups/ppe-<UTC>/` (Git'e girmez, `.gitignore`):

| Dosya | İçerik |
|---|---|
| `db.dump` | `pg_dump -Fc` (rol parolaları **yok**) |
| `snapshots/` | normal dosyaların kopyası (sembolik bağ ve `.tmp-*` atlanır) |
| `manifest.json` | zaman, `quiesced`, Alembic revision, PostgreSQL sürümü, tablo sayımları (dump öncesi/sonrası), dump ve her snapshot için SHA-256, DB'de referanslı ama eksik snapshot listesi |
| `SHA256SUMS`, `RESTORE.md` | bütünlük listesi, kısa talimat |

### Tutarlılık

`pg_dump` kendi içinde tek anlık görüntüdür. Snapshot klasörü dump'tan **sonra** kopyalanır; dashboard çalışıyorsa
arada gelen ihlalin JPEG'i kopyada olup dump'ta olmayabilir (zararsız). Sayımlar dump'tan önce ve sonra alınır;
farklıysa `db_changed_during_backup: true` yazılır ve doğrulamada sayım karşılaştırması SKIP olur.
`--quiesce` dashboard'u durdurur: dedektör bu sürede olayları belleğinde tutar ve `503`/bağlantı hatasıyla tekrar
dener (`EVENT_QUEUE_MAX` sınırı geçerlidir). Tamamen tutarlı kopya için dedektörü de durdurun.

## Doğrulama (izole geri yükleme)

```powershell
python scripts/restore_check.py backups/ppe-<UTC>
```

`ppe_restore_verify` veritabanını (yalnız bu ad) yeniden oluşturur, `pg_restore --no-owner --no-privileges`
uygular; SHA256SUMS, revision, tablo sayımları, snapshot referansları ve özetlerini karşılaştırır; ana DB'nin önce/
sonra aynı kaldığını kontrol eder; doğrulama DB'sini siler (`--keep` ile kalır). Çıkış kodu 0 = PASS.

FAZ 5'te gerçek veriyle iki kez PASS: 0002'de (12 olay) ve 0003'te (75 olay, 36 snapshot referansı).

## Ana veritabanına geri yükleme (felaket) — elle, test edilmedi

> Bu prosedür FAZ 5'te **çalıştırılmadı** (yalnız izole doğrulama test edildi). Önce yeni bir yedek alın; eski
> veritabanı silinmez, yeniden adlandırılır.

```powershell
# 1. Dedektörü durdurun (Ctrl+C), sonra:
docker compose stop dashboard
python scripts/backup.py                                   # mevcut durumun yedeği
# 2. Yedeği yeni bir veritabanına yükleyin
#    (PowerShell 5.1'de ikili veriyi pipe ile aktarmayın; dosyayı container'a kopyalayın)
docker compose cp backups\ppe-<UTC>\db.dump db:/tmp/restore.dump
docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" ppe_restored && pg_restore --no-owner --no-privileges --exit-on-error -U "$POSTGRES_USER" -d ppe_restored /tmp/restore.dump && rm /tmp/restore.dump'
# 3. Yer değiştirin (eski veritabanı ppe_old_<tarih> olarak kalır)
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres -c "ALTER DATABASE \"$POSTGRES_DB\" RENAME TO ppe_old_20261028" -c "ALTER DATABASE ppe_restored RENAME TO \"$POSTGRES_DB\""'
# 4. Sahiplik ve yetkiler, migration, başlatma
docker compose up -d
# 5. Snapshot'lar: yedekteki dosyaları data\snapshots'a kopyalayın (var olanların üzerine YAZMAYIN)
robocopy backups\ppe-<UTC>\snapshots data\snapshots /XC /XN /XO
# 6. Kontrol: http://127.0.0.1:8000/health/ready ve geçmiş ekranı
```

Farklı bir PostgreSQL kümesine taşırken roller yoktur: `.env`'deki parolalarla `docker compose up -d`, `db-roles`
servisini çalıştırarak rolleri ve sahiplikleri yeniden kurar.

## Volume

Veriler `ppe-split_pgdata` volume'undadır. `docker compose down -v` ve `docker volume prune` bu volume'u **siler**;
kullanmayın. `docker compose down` (–v olmadan) güvenlidir.
