# Ana dashboard manuel kabul kontrol listesi (FAZ 10-A.1)

Bu liste **ana ortamda** (gerçek veriyle çalışan `ppe-split` yığını), yöneticinin **kendi parolasıyla** tarayıcıdan
yapacağı kontroller içindir. Otomatik testler (dashboard 338, tarayıcı 30, LAN TLS 21) izole veritabanında koşar;
**bu listenin yerine geçmez**, bu liste de onların yerine geçmez.

Kurallar:
- Parola, oturum çerezi, CSRF/ingest token'ı bu dosyaya, ekran görüntüsüne veya rapora **yazılmaz**.
- Kişisel veri içeren snapshot'ların ekran görüntüsü alınmaz; sonuç yalnız PASS / FAIL / YAPILMADI + kısa not.
- Bir adım beklenmeyen sonuç verirse durun; ana veritabanında düzeltme yapmayın, notu rapora ekleyin.

## 0. Ön kontrol (yönetici, terminal; salt okuma)

| # | Komut | Beklenen | Sonuç |
|---|---|---|---|
| 0.1 | `docker compose ps` | `db` ve `dashboard` healthy | |
| 0.2 | `curl -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/health/ready` | `200` | |
| 0.3 | `docker compose exec dashboard python -c "import fastapi,starlette;print(fastapi.__version__,starlette.__version__)"` | `0.162.0 1.10.0` | |
| 0.4 | `docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from alembic_version"'` | `0006` | |
| 0.5 | Çalışan imaj kimliği (`docker inspect ppe-split-dashboard-1 --format "{{.Image}}"`) not edilir | — | |

## 1. Giriş / çıkış

| # | Adım | Beklenen | Sonuç |
|---|---|---|---|
| 1.1 | `http://127.0.0.1:8000` açılır | Giriş ekranı; kamera/geçmiş verisi görünmez | |
| 1.2 | Yanlış parola ile giriş | Genel hata mesajı (kullanıcının var olup olmadığını söylemez); ekranda veri yok | |
| 1.3 | Doğru parola ile giriş | Ana ekran; üstte kullanıcı adı ve **Çıkış**; durum göstergesi dedektör durumunu yazar ("Dedektör bağlı değil…" veya "Canlı · N/M kamera") | |
| 1.4 | **Çıkış** | Giriş ekranına döner; kamera karoları, ihlal akışı, Analiz kapsamı, geçmiş tabloları, detay paneli **boşaltılmış** | |
| 1.5 | Çıkıştan sonra tarayıcı **Geri** tuşu | Önceki içerik görünmez; giriş ekranı kalır | |
| 1.6 | Çıkıştan sonra aynı tarayıcıda `http://127.0.0.1:8000/api/state` | `401` | |
| 1.7 | Çıkıştan sonra daha önce açılmış bir snapshot adresi (`/snapshots/<ad>.jpg`) yenilenir | `401`; görüntü gelmez | |

## 2. Canlı İzleme

| # | Adım | Beklenen | Sonuç |
|---|---|---|---|
| 2.1 | Dedektör çalışmıyorken **Canlı İzleme** | "Dedektör bağlanınca kameralar burada görünür" boş durumu; hata yok | |
| 2.2 | (İsteğe bağlı) Dedektör yerel bir video dosyasıyla kısa süre çalıştırılır | Kamera karosu canlı görüntü + kişi sayısı; ihlal akışı dolar | |
| 2.3 | Kamera karosunun **Analiz kapsamı** satırı | Kapıdan geçen / değerlendirilemeyen oran ve nedenleri; "değerlendirilemeyen kişiler Uygun sayılmaz" notu; kabul eşiği "tanımlanmadı" | |
| 2.4 | Dedektör durdurulur (Ctrl+C) | Karo "bağlantı yok/izlenemiyor" durumuna geçer veya etkin pencereden düşer; sahte "Uygun" gösterilmez | |
| 2.5 | **Raporu yükle** | Son run'ın kişi raporu tablo olarak gelir | |

## 3. İhlal Geçmişi ve snapshot

| # | Adım | Beklenen | Sonuç |
|---|---|---|---|
| 3.1 | **İhlal Geçmişi** sekmesi | Kayıtlar tarih sırasıyla; toplam sayı ana DB sayımıyla tutarlı | |
| 3.2 | Kamera / tarih / tür filtresi, sonra **Sıfırla** | Sonuçlar filtreye göre değişir; sıfırlayınca tüm liste döner | |
| 3.3 | Bir satır → **Detay** | Olay zaman çizelgesi; snapshot varsa görüntü yüklenir (yönetici/supervisor) | |
| 3.4 | **Kapat** | Detay paneli kapanır | |
| 3.5 | Snapshot'ı yeni sekmede açma | Oturum açıkken görüntü gelir; çıkıştan sonra 401 (1.7) | |

## 4. Kamera / Run Raporları ve İstatistikler

| # | Adım | Beklenen | Sonuç |
|---|---|---|---|
| 4.1 | **Kamera / Run Raporları** | Run listesi; durumlar Türkçe etiketli (Çalışıyor, Bitti, Durduruldu, Hata, Yanıt vermeden kapandı, **Bağlantı koptu**) | |
| 4.2 | Durum filtresi "Bağlantı koptu" | Yalnız `disconnected` run'lar (yoksa boş liste; hata yok) | |
| 4.3 | `running` görünen eski run'lar | Uzlaştırma bekleyen sahipsiz run'lar burada "Çalışıyor" görünür (FAZ 10-A: 4 adet); **onaysız düzeltilmez** | |
| 4.4 | Run → detay | Kişi satırları, bitiş nedeni (varsa) | |
| 4.5 | **İstatistikler** → **Son 30 gün** | Günlük ihlal grafiği; sayı geçmişle tutarlı | |

## 5. Kamera bazlı yetkilendirme (rol ve kapsam)

Bu bölüm ana DB'de **geçici bir test kullanıcısı oluşturmayı** gerektirir (kullanıcı tablosu ve denetim kaydına yazar).
Yönetici karar verir; yapılırsa test sonunda kullanıcı **devre dışı bırakılır** (silinmez). Parolayı yönetici seçer
ve yalnız stdin'den verir:

```
docker compose exec -it dashboard python -m auth.cli create kabul_izleyici --role viewer --cameras <kamera_id> --password-stdin
docker compose exec -it dashboard python -m auth.cli create kabul_sorumlu --role supervisor --cameras <kamera_id> --password-stdin
# test sonunda:
docker compose exec dashboard python -m auth.cli disable kabul_izleyici
docker compose exec dashboard python -m auth.cli disable kabul_sorumlu
```

| # | Adım | Beklenen | Sonuç |
|---|---|---|---|
| 5.1 | `kabul_izleyici` (viewer) ile giriş | Canlı görüntü ve snapshot **yok**; geçmiş yalnız `<kamera_id>` | |
| 5.2 | `kabul_sorumlu` (supervisor) ile giriş | Yalnız `<kamera_id>` karosu ve geçmişi; snapshot yalnız bu kameranın | |
| 5.3 | Supervisor oturumunda başka kameranın snapshot adresi (yönetici oturumundan kopyalanmış) | `404` (kapsam dışı dosyanın var olduğu bile sızmaz); görüntü gelmez | |
| 5.4 | Viewer/supervisor oturumunda `/api/ops/metrics` | `403` (yalnız admin) | |
| 5.5 | Yönetici oturumunda `/api/ops/metrics` | `200`, JSON; kaynak adresi/token içermez | |

## 6. WebSocket bağlantısı

| # | Adım | Beklenen | Sonuç |
|---|---|---|---|
| 6.1 | Dedektör çalışırken (2.2) kamera karoları | Görüntü akıyor (WebSocket); gösterge "Canlı · N/M kamera" | |
| 6.2 | `docker compose restart dashboard` (kısa kesinti; dedektör outbox'ı olayları bekletir) | Gösterge kısa süre "Dashboard sunucusuna ulaşılamıyor" → sayfa yenilemeden "Canlı · …"ya döner; görüntü akışı yeniden başlar | |
| 6.3 | Çıkış yapılır | Gösterge "Oturum kapalı"; görüntü akışı durur; yeni veri ekrana gelmez | |
| 6.4 | Oturum boşta kalır (`SESSION_IDLE_MINUTES`, varsayılan 30 dk) | Süre dolunca giriş ekranı; içerik temizlenir | |

## 7. Sonuç

| Bölüm | PASS / FAIL / YAPILMADI | Not |
|---|---|---|
| 0 Ön kontrol | | |
| 1 Giriş/çıkış | | |
| 2 Canlı İzleme + Analiz kapsamı | | |
| 3 İhlal Geçmişi + snapshot | | |
| 4 Run Raporları + İstatistikler | | |
| 5 Kamera bazlı yetkilendirme | | |
| 6 WebSocket | | |

Kontrol eden (ad/rol), tarih (UTC), dashboard imaj kimliği: ____________________

Bu tablo doldurulana kadar raporlarda "ana ortam manuel kabul: **YAPILMADI**" yazılır.
