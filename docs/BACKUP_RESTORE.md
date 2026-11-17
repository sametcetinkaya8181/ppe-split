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

**FAZ 01 — ana kümeye hiç dokunmadan doğrulama (önerilen):** yukarıdaki komut doğrulama veritabanını ana PostgreSQL
kümesinde oluşturur. `--target-container` ile geri yükleme, `cluster_name=ppe-ephemeral-test` işaretli geçici bir
container'da yapılır (işaretsiz hedef reddedilir); ana veritabanına hiç bağlanılmaz:

```powershell
docker network create --internal ppe-verify-net
docker run -d --name ppe-verify --network ppe-verify-net --tmpfs /var/lib/postgresql/data `
  -e POSTGRES_USER=verify -e POSTGRES_PASSWORD=<rastgele> -e POSTGRES_DB=postgres `
  postgres:17@sha256:b8faeff7712663c2773a190181e1246e37e364b887611c04658c28b44e2408fe postgres -c cluster_name=ppe-ephemeral-test
python scripts/restore_check.py backups/ppe-<UTC> --target-container ppe-verify
docker rm -f ppe-verify; docker network rm ppe-verify-net
```

Tablo sayımları: çekirdek 5 tablo her zaman; `app_users`, `user_sessions`, `audit_log` (0004+) ve
`incident_reviews`, `incident_review_history` (0007+) yalnız veritabanında VARSA sayılır ve manifest'e yazılır. Eski
manifest'lerde bulunmayan tablolar karşılaştırılmaz, bilgi olarak raporlanır (0006 sistemlerle uyumlu).
FAZ 01 sonuçları (2026-11-17, izole): `ppe-20261101T213616Z` (0005) PASS, `ppe-20261102T030016Z` (0006) PASS;
0005→0006→0007→0006→0007 geçişinde tüm sayımlar ve `incidents`/`violation_events` satır özetleri değişmedi;
0007'de inceleme kaydıyla yedek→geri yükleme PASS; inceleme verisi varken downgrade reddedildi.

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

## Yedek şifreleme (FAZ 10-A.1 — TASARIM; uygulanmadı, karar bekliyor)

Durum: `backups/ppe-<UTC>/` düz `pg_dump` + snapshot JPEG'leri içerir (kişisel veri: yüz/vücut görüntüsü, kamera,
zaman). Klasör izinleri daraltılmadıkça (bkz. `scripts/windows_acl.ps1`) ve disk şifrelemesi (BitLocker)
doğrulanmadıkça aynı bilgisayardaki diğer yerel kullanıcılar okuyabilir.

**Önerilen yöntem: açık anahtarlı şifreleme (`age`, X25519).** Saha bilgisayarı yalnız **alıcı (açık) anahtarı**
bilir; yedeği şifreleyebilir ama **çözemez**. Bilgisayar ele geçirilse bile eski yedekler okunamaz ve otomasyon için
makinede parola/anahtar saklanması gerekmez. (Alternatif: 7-Zip AES-256 parola — Windows'ta kolay, ama otomatik
yedek için parolanın makinede durması gerekir; önerilmez.) Araç seçimi ve lisansı (age: BSD-3) proje sahibince onaylanır.

Akış (uygulanırsa `scripts/backup.py --encrypt-to <alıcı-dosyası>` olarak):
1. Mevcut yedek üretilir ve `SHA256SUMS` yazılır (değişmez).
2. Klasör tek arşive alınır ve şifrelenir: `ppe-<UTC>.tar.age`; şifreli dosyanın SHA-256'sı ayrı yazılır.
3. Düz klasör **yalnız** şifreli dosya diske tam yazılıp özeti doğrulandıktan sonra kaldırılır (bayrakla; varsayılan
   kaldırmamak). Şifreli yedekler başka bir ortama (harici disk / ağ paylaşımı) kopyalanabilir.

Anahtar yönetimi:
- Özel anahtar (`age-keygen` çıktısı) **saha bilgisayarında tutulmaz**; Git'e, log'a, rapora, `.env`'e yazılmaz.
- İki kopya: (a) veri sorumlusunun belirlediği kişi — parola yöneticisi / şifreli USB, kasada; (b) yedek emanet
  (BT sorumlusu). İkisi de kaybolursa **yedekler kalıcı olarak geri alınamaz** — emanet zorunludur.
- Makinede yalnız açık anahtar (`age1…`) durur; gizli değildir ama değiştirilmesi saldırganın yeni yedekleri kendi
  anahtarına yönlendirmesi demektir → dosya ACL ile korunur ve özeti kurulum kaydında tutulur.
- Rotasyon: yılda bir veya anahtar sahibi değiştiğinde yeni anahtar çifti; eski özel anahtar, onunla şifrelenmiş son
  yedeğin saklama süresi (KVKK saklama/imha politikası) dolana kadar saklanır, sonra imha edilir ve kayda geçer.

Geri yükleme (şifreli):
1. Şifreli dosya ve özeti, özel anahtarın bulunduğu **güvenli bilgisayara** kopyalanır; özet doğrulanır.
2. `age -d -i <özel-anahtar> -o ppe-<UTC>.tar ppe-<UTC>.tar.age` → arşiv açılır → `SHA256SUMS` doğrulanır.
3. `python scripts/restore_check.py <klasör>` (izole doğrulama), ardından gerekiyorsa yukarıdaki "Ana veritabanına
   geri yükleme" adımları. Açılmış düz kopya iş bitince silinir.
4. **Tatbikat:** her anahtar rotasyonunda ve en az 3 ayda bir, anahtar sahibiyle izole geri yükleme yapılır ve
   sonucu kaydedilir (anahtar erişilebilirliğinin tek kanıtı budur).

Disk şifrelemesi bu tasarımın yerine geçmez, tamamlar: canlı veri (Docker Desktop disk imajındaki PostgreSQL
volume'u, `data/snapshots`, dedektör outbox'ı) şifreli disk (BitLocker) üzerinde olmalıdır. BitLocker durumu
yönetici yetkisiyle doğrulanır: `manage-bde -status C:` (yönetici PowerShell).
