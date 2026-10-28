# LAN dağıtımı (TLS ters vekil) — hazır, ETKİNLEŞTİRİLMEDİ

Bu belge yapılandırmayı ve kontrol listesini tanımlar. FAZ 6'da dashboard **LAN'a açılmadı**; testler yalnız
`127.0.0.1` üzerinde, yerel test CA'sıyla yapıldı. Gerçek sertifika veya alan adı kurulmadı.

## Mimari

```
Tarayıcı (LAN) ──HTTPS 443──▶ Caddy (proxy, 172.31.250.10) ──HTTP──▶ dashboard:8000 (lan profili, 172.31.250.11)
                    HTTP 80 ──▶ 308 HTTPS'e yönlendirme
Dedektör (aynı host) ──HTTP──▶ 127.0.0.1:8000 /ingest/*  (X-Token; LAN'a açık değil)
```

Dosyalar: [docker-compose.lan.yml](../docker-compose.lan.yml), [deploy/lan/Caddyfile](../deploy/lan/Caddyfile),
smoke testi [deploy/lan/lan_smoke.py](../deploy/lan/lan_smoke.py).

## Ön koşullar (hepsi sağlanmadan açmayın)

1. KVKK/İSG onayı ve aydınlatma ([PRIVACY_READINESS.md](PRIVACY_READINESS.md)) — şu an **NO-GO**.
2. Lisans kararı ([LICENSE_REVIEW.md](LICENSE_REVIEW.md)).
3. Kurum sertifikası (iç CA veya kamu CA) ve alan adı (`PPE_SITE`). `tls internal` yalnız denemedir.
4. Sunucu güvenlik duvarı: 443/80 yalnız yetkili VLAN/IP'lere; 8000 dışarıya kapalı.
5. `INGEST_TOKEN` dolu; yönetici hesabı oluşturulmuş; varsayılan dışı parolalar.
6. Yedekleme ve disk şifrelemesi (BitLocker) etkin; `backups/` erişimi kısıtlı.
7. FastAPI/Starlette yükseltmesi değerlendirilmiş (SECURITY.md "Container ve bağımlılıklar").

## Etkinleştirme

```powershell
# .env: PPE_SITE=ppe.santiye.local, LAN_BIND=<sunucunun LAN IP'si>, PPE_TLS="/certs/cert.pem /certs/key.pem"
docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d
python deploy/lan/lan_smoke.py      # izole doğrulama (127.0.0.1:8443), ana sisteme dokunmaz
```

Gerçek sertifika için compose'a `- ./certs:/certs:ro` bağlaması eklenmelidir. Geri dönüş: override'sız
`docker compose up -d` (yerel profil).

## Test edilenler (izole, gerçek TLS, `lan_smoke.py` 21/21)

HTTP→HTTPS yönlendirme; sertifika doğrulaması (yerel test CA); HSTS; CSP; `Server` başlığı yok; oturumsuz 401;
yabancı Origin ile giriş 403; vekil üzerinden giriş; `__Host-` + Secure + HttpOnly + SameSite çerez; CORS kapalı;
ingest token'sız/oturum çereziyle 401, token'la 200; WSS yabancı Origin ve oturumsuz 403; WSS 101 + ikili kare;
CSRF'siz çıkış 403; çıkış sonrası 401; vekili atlayan sahte `X-Forwarded-Proto` 403.

**Test edilmedi (SKIP):** gerçek LAN istemcisi, kurum sertifikası/ACME, güvenlik duvarı kuralları, birden fazla
dashboard örneği.
