# Dedektör kalıcı outbox (FAZ 7)

Kod: [detector/outbox.py](../detector/outbox.py). Dashboard ucu: `POST /ingest/events`.

## Ne garanti edilir, ne edilmez

- Bir olay/rapor, outbox'ta SQLite transaction'ı **commit edildiğinde** kabul edilmiş sayılır (`synchronous=FULL`:
  commit, WAL'ı diske fsync eder). Kabul edilmiş mesaj süreç çökmesi, zorla öldürme, Ctrl+C, dashboard/PostgreSQL
  kesintisi ve bilgisayarın yeniden başlatılmasından sonra **aynı kimlik ve aynı gövdeyle** yeniden gönderilir.
- Mesaj yalnız HTTP 2xx alınınca teslim edildi işaretlenir; dashboard 2xx'i yalnız PostgreSQL commit'inden sonra
  döner. ACK kaybolursa tekrar gönderilir; PostgreSQL `event_id` / rapor tekrarını ayıklar.
  **Bu "en az bir kez teslim + sunucuda tekilleştirme"dir; "exactly-once delivery" değildir.**
- Garanti dışı: outbox'a yazılmadan önceki çökme (olay üretildi ama commit olmadı — milisaniyeler), disk/donanım
  arızası, işletim sistemi veya disk önbelleğinin fsync'e uymaması, dosyanın elle silinmesi, WAL dosyasının bozulması
  (bozulma noktasından sonraki işlemler kurtarılamaz — testle belgelendi).
- Kareler (canlı JPEG) kalıcı DEĞİLDİR: en yenisi kazanır.

## Tasarım

| Konu | Karar |
|---|---|
| Depolama | SQLite WAL, **kamera başına ayrı dosya** `OUTBOX_DIR/<camera_id>.sqlite3` |
| Çoklu süreç | Dedektör kamera başına ayrı süreç çalıştırır → dosyalar arası çekişme yok. `locking_mode=EXCLUSIVE`: aynı kamerayı ikinci süreç açamaz (`OutboxLocked`, kamera başlatılmaz, diğerleri sürer) |
| Süreç içi | Üretici (video döngüsü) ve gönderici thread tek bağlantıyı kilitle paylaşır |
| Değişmezlik | Gövde kuyruğa girerken JSON metnine dondurulur + SHA-256; gönderilen bayt dizisi birebir aynı. Kaynak etiketi (`source_label`) olayla dondurulur |
| FIFO / bariyer | Gönderim sırası `seq`; rapor, kendinden önceki tüm satırlar teslim/ölü/karantina olmadan gönderilmez |
| Bütünlük | Açılışta `PRAGMA quick_check`; satır başına SHA-256; bozuk satır `dead` |
| Gizlilik | Teslimden sonra gövde (snapshot dahil) hemen boşaltılır; meta satır 1 saat sonra silinir. Yeni outbox dizinine kısıtlı ACL (Windows: miras kesilir, yalnız kullanıcı + SYSTEM + Administrators; POSIX 0700) |

## Yanıt sınıfları

| Yanıt | Davranış |
|---|---|
| 2xx | teslim edildi |
| ağ hatası, zaman aşımı, 408/425/429/5xx | aynı gövde; üstel geri çekilme + jitter (üst sınır `OUTBOX_RETRY_BACKOFF_MAX`), `Retry-After`'a uyulur |
| 401/403 | **duraklat** (düşürme yok); operatör uyarısı; `detector/.env` her 30 sn yeniden okunur — token düzeltilince yeniden başlatmadan devam eder |
| 404/405 | dashboard `/ingest/events`'i bilmiyor (eski sürüm): duraklat, düşürme yok |
| 409 | çok öğeli batch bölünür; tek öğe `quarantined` |
| 400/413/422 ve diğer 4xx | çok öğeli batch bölünür; tek öğe `dead` (sonsuz tekrar yok; diğer olaylar devam eder) |

Ölü/karantinadaki satır silinmez; `requeue` ile aynı gövdeyle yeniden kuyruğa alınabilir.

## Kapasite

| Değişken | Varsayılan | Anlamı |
|---|---|---|
| `OUTBOX_ENABLED` | `1` | `0` = eski bellek içi Sender (çökmede kayıp) |
| `OUTBOX_DIR` | `detector/outbox` | Git dışı (`.gitignore`) |
| `OUTBOX_MAX_BYTES` | 512 MiB | bekleyen gövde toplamı |
| `OUTBOX_MAX_ITEMS` | 100 000 | bekleyen mesaj sayısı |
| `OUTBOX_HIGH_WATERMARK` | 0.8 | aşılınca metrikte `high_watermark: true` |
| `OUTBOX_RETRY_BACKOFF_MAX` | 30 sn | geri çekilme üst sınırı |

Sınıra ulaşılınca yalnız `ongoing` seyreltilir (önce en eski bekleyen ongoing atılır, yoksa gelen ongoing kabul
edilmez; sayılır, loglanır). `start`/`end`/rapor için sınırın %25 üzerine kadar yedek alan vardır; o da dolarsa veya
disk yazılamazsa mesaj **bellekte** bekletilir, ERROR loglanır ve her döngüde yeniden yazılmaya çalışılır (süreç bu
sırada çökerse kaybolur — `memory_fallback` metriği). Hiçbir kritik mesaj sessizce düşürülmez.

## Metrikler

`OUTBOX_DIR/<kamera>.status.json` (5 sn'de bir, gizli bilgi içermez) ve `python outbox.py status`:
`pending_count`, `pending_bytes`, `oldest_pending_age_sec`, `pending_retry_attempts`, `delivered_count`,
`dead_letter_count`, `quarantine_count`, `disk_write_errors`, `evicted_ongoing`, `dropped_ongoing_full`,
`high_watermark`, `state` (`ok`/`backoff`/`paused_auth`/`paused_incompatible`), `memory_fallback`, gönderim sayaçları.

FAZ 9 ekleri: `replay_success_count` (önceki oturumdan kalıp bu oturumda teslim edilen), `capacity_used_ratio`,
`db_bytes`, `wal_bytes` (WAL `journal_size_limit` ile `OUTBOX_WAL_LIMIT_BYTES`'a kırpılır), `disk_free_bytes`,
`warnings`: `high_watermark`, `pending_age` (`OUTBOX_AGE_WARN_SEC`), `dead_letters`, `quarantine`,
`disk_write_errors`, `disk_free_low` (`OUTBOX_MIN_FREE_BYTES`), `memory_fallback`, `delivery_paused`,
`messages_lost`. Yeni uyarı loga bir kez `OUTBOX UYARISI: …` olarak yazılır, kalkınca bildirilir; kare meta'sıyla
dashboard'a gider (canlı karoda "olay kuyruğu: N bekleyen", `/api/ops/metrics`). **Uyarılar hiçbir mesajı silmez
veya düşürmez.**

## Komutlar (detector klasöründe, venv etkin)

```powershell
python outbox.py status                       # tüm kameralar (çalışan dedektör varsa status.json)
python outbox.py check giris                  # bütünlük + metrik (dedektör kapalıyken)
python outbox.py list-failed giris            # ölü/karantina (gövde/snapshot yazdırılmaz)
python outbox.py requeue giris --seq 42       # veya --all
python outbox.py drain giris --timeout 60     # video işlemeden bekleyenleri gönder (kamera kapalıyken)
```

## Kurtarma prosedürü (outbox açılamıyor)

Dedektör "outbox açılamadı — bu kamera BAŞLATILMADI" yazar; diğer kameralar çalışır. **Dosyayı silmeyin.**

1. `OutboxLocked`: aynı kamera için başka bir dedektör çalışıyor → onu kapatın.
2. `OutboxCorrupt` / bütünlük hatası: dedektörü durdurun; `<kamera>.sqlite3`, `-wal`, `-shm` dosyalarını birlikte
   güvenli bir yere **kopyalayın**. Kopyadan kurtarma: `sqlite3 kopya.sqlite3 ".recover" | sqlite3 kurtarilan.sqlite3`
   (sqlite3 CLI gerekir). Kurtarılan dosyayı `python outbox.py --dir <klasör> check <kamera>` ile doğrulayın, sonra
   asıl dosyanın yerine koyun. Kurtarılamıyorsa bozuk dosyayı `<kamera>.sqlite3.corrupt-<tarih>` olarak yeniden
   adlandırın (dedektör yeni boş outbox oluşturur); kayıp olayları rapora not edin.
3. "başka kameraya/sürüme ait": dosya yanlış ada kopyalanmış → doğru kamera kimliğine geri adlandırın.

## Güvenlik notları

Outbox ihlal anı görüntülerini (snapshot) teslim edilene kadar diskte tutar. BitLocker gibi disk şifrelemesi önerilir
(bu bilgisayarda durum doğrulanamadı — yönetici yetkisi gerekir). Bu kurulumda proje klasörü `BUILTIN\Users`'a
**değiştirme** izni miras bırakıyor (`.env`, `data/`, `backups/` dahil); yalnız yeni outbox dizini kısıtlanır.
Proje klasörünü kısıtlamak için (yönetici/sahip olarak, bilinçli karar):
`icacls C:\Users\PC\Desktop\ppe-split-main /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F"`.
