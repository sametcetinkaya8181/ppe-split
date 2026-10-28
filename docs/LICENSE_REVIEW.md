# Lisans incelemesi (teknik envanter)

> **Hukuki görüş değildir.** Teknik olarak dağıtılabilir olmak lisans uygunluğu anlamına gelmez. Durum:
> **NO-GO (ticari / kapalı kaynak kullanım kararı bekleniyor).** Referans: https://www.ultralytics.com/license

## Kritik bileşenler

| Bileşen | Sürüm | Lisans (paket metadata'sı) | Not |
|---|---|---|---|
| `ultralytics` (YOLO, ByteTrack entegrasyonu) | 8.4.183 | **AGPL-3.0** (AGPLv3+) | Ağ üzerinden hizmet sunulan değiştirilmiş/entegre yazılımlarda kaynak kodu paylaşım yükümlülüğü doğurabilir |
| `best.pt` ağırlıkları | — | checkpoint metadata'sında **AGPL-3.0** etiketi (FAZ 0 statik inceleme) | `yolo26n.pt` tabanından eğitilmiş; ağırlıkların lisansı ve eğitim verisinin hakları ayrıca doğrulanmalı |
| `psycopg` / `psycopg-binary` | 3.3.6 | LGPL-3.0-only | dinamik bağlantı; değiştirilmeden kullanılıyor |
| `yt-dlp` | 2026.10.26 | Unlicense | YouTube kaynaklarının kullanım koşulları ayrıca geçerli |

## Diğer bağımlılıklar (izin verici)

Dashboard: FastAPI 0.115.5 (MIT), Starlette 0.41.3 (BSD-3-Clause), uvicorn 0.32.1 (BSD-3-Clause), python-multipart
0.0.31 (Apache-2.0), SQLAlchemy 2.0.55 (MIT), Alembic 1.20.1 (MIT), Pydantic 2.14.1 (MIT), argon2-cffi 23.1.0 (MIT),
argon2-cffi-bindings 26.1.0 (MIT). Dedektör: torch 2.14.1 (Apache-2.0/BSD/MIT karışık), opencv-python 4.10.0.84
(Apache-2.0), numpy 2.5.2 (BSD-3-Clause vb.), requests 2.34.2 (Apache-2.0), pillow 12.3.0 (MIT-CMU), lap 0.5.13
(BSD-2-Clause). Altyapı: PostgreSQL (PostgreSQL License), Caddy 2.8.4 (Apache-2.0), Python (PSF).
CUDA/NVIDIA sürücüleri kendi EULA'larına tabidir.

## Karar seçenekleri (yönetim/hukuk)

1. **AGPL-3.0 ile uyum**: tüm proje kaynak kodunun (değişiklikler dahil) AGPL-3.0 koşullarıyla kullanıcılara
   sunulması. Kurum içi kullanım ile üçüncü taraflara hizmet sunma farklı değerlendirilebilir — hukuk görüşü gerekir.
2. **Ultralytics Enterprise lisansı**: kapalı kaynak/ticari kullanım için.
3. **Model/framework değişimi**: izin verici lisanslı başka bir dedektör (yeniden eğitim ve FAZ 8 ölçümü gerektirir).

Karar verilene kadar sistemin müşteriye/üçüncü tarafa dağıtımı veya ticari hizmet olarak sunulması önerilmez.
