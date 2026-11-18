# SteelBench — tekrar çalıştırılabilir dedektör referans değerlendirmesi

**Okuyucu:** ML geliştirici, veri mühendisi, ürün/İSG (özet ve "nasıl yorumlanır" bölümleri).
**Kod:** `tools/reliability/steelbench.py` (CLI), `steelbench_tap.py` (test başlatıcısı), `steelbench_expected.json`
(girdiler + bilinen hatalar), `steelbench_baseline.json` (referans özeti), `test_steelbench.py`.

## Ne yapar, ne yapmaz

**Yapar:** Gerçek `detector.py`'yi (best.pt + ByteTrack + PPEMonitor; kod **değişmez**) 3 yerel stok video üzerinde
çalıştırır, dedektörün ürettiği her olayı/raporu/kareyi kaydeder ve bunları izole PostgreSQL, snapshot dosyaları ve
dashboard API ile karşılaştırır. Alarmları bilinen hatalara eşler, inceleme tabloları üretir, önceki çalıştırmayla
fark çıkarır. Model veya karar mantığı değişikliğinin **davranışı nasıl değiştirdiğini** gösterir.

**Yapmaz:** Saha doğruluğunu ölçmez. Videolarda doğrulanmış gerçek ihlal yok; insan incelemesi tek inceleyicili ve
örnek karelere dayalı. Bu yüzden precision / recall / yanlış alarm oranı **ölçülmez**
(`INSUFFICIENT_GROUND_TRUTH` / `NOT_MEASURABLE`). "Alarm kayboldu" = "hata giderildi" demek değildir.

## Çalıştırma (tek komut)

Ön koşul: Docker Desktop; dedektör venv'i (`best.pt` çalışan ortam); araç için `requests` (+ `--images` için OpenCV)
içeren bir Python. Proje kökünde:

```bash
# macOS / Linux
REL_DETECTOR_PY=$PWD/.venv/bin/python python3 tools/reliability/steelbench.py run --images
# Windows (PowerShell)
$env:REL_DETECTOR_PY="$PWD\detector\.venv\Scripts\python.exe"; python tools\reliability\steelbench.py run --images
```

Seçenekler: `--device auto|mps|0|cpu` (auto: CUDA → MPS → CPU), `--stride 2`, `--imgsz 640`,
`--model models/aday.pt` (aday model; `detector/` dizinine göre), `--compare ÖNCEKİ/result.json`
(varsayılan: `steelbench_baseline.json`), `--keep-stack` (127.0.0.1:8200'ü incelemek için açık bırak),
`--write-baseline` (özeti referans dosyasına yaz — yalnız bilinçli olarak, ayrı commit'le).
Diğer komutlar: `steelbench.py compare ESKİ.json YENİ.json`, `steelbench.py report RESULT.json`.
Birim testleri: `.venv/bin/python -m pytest -q tools/reliability/test_steelbench.py`.

**Çıkış kodu:** `0` BASELINE_REPRODUCED (bütünlük + izolasyon PASS, referansa göre fark yok) · `1` FAIL (girdi
SHA-256 uyuşmazlığı, bütünlük veya izolasyon hatası, dedektör hatası) · `2` REVIEW_REQUIRED (yeni alarm, bilinen
hata yeniden üretilmedi/değişti, iz/kapsam/incident farkı).

## Çıktılar (Git dışı: `tools/reliability/output/<UTC>_steelbench/`)

| Dosya | İçerik |
|---|---|
| `result.json` | manifest, bütünlük, alarmlar + sınıflama, bilinen hatalar, metrik durumları, video inceleme, izolasyon, karşılaştırma, `summary` |
| `report.md` | insan için rapor |
| `ledger/tap_*.jsonl` | ham dedektör kayıtları: `event`, `report`, `frame` (kişi kutusu, güven, durum, ANALIZ nedeni, eşleşen/eşleşmeyen baret-yelek kutuları) |
| `outbox/`, `detector.log` | bu çalıştırmanın kendi outbox'ı ve günlüğü |
| `review/*.jpg` (`--images`) | alarm anında tam kare: kişi kutuları (durum + iz + kapı nedeni), baret (sarı) / yelek (yeşil) kutuları — **kişi görüntüsü içerir, paylaşılmaz** |

## Nasıl yorumlanır

| Alan | Anlam |
|---|---|
| `KNOWN_FALSE_ALARM` | Alarm, kayıtlı bir yanlış alarma (E-01, E-02) eşleşti — başarılı tespit **değildir** |
| `UNREVIEWED_NEW_ALARM` | Bilinen hiçbir kayda uymayan alarm — insan incelemesi gerekir; doğru da yanlış da sayılmaz |
| `VERIFIED_TRUE_VIOLATION` | Yalnız `verified_true_incidents` listesindeki bağımsız doğrulanmış ihlale eşleşirse (bugün liste boş) |
| `REPRODUCED` / `NOT_REPRODUCED` | Bilinen hata bu çalıştırmada görüldü / görülmedi. NOT_REPRODUCED = **FIX_CANDIDATE** |
| `system_coverage` | Değerlendirilen kişi-kare / sistemin **tespit ettiği** kişi-kare (tespit edilmeyen kişileri kapsamaz) |
| `MEASURED_SYSTEM_ONLY` | Yalnız sistem iç ölçüsü; ground-truth'a dayanmaz |

**Bir hatanın giderildiğini teyit etmek:** (1) SteelBench'te ilgili hata `NOT_REPRODUCED` ve başka yeni alarm/değişiklik
yok; (2) etiketleyiciden bağımsız bir kişi ilgili video aralığını ve `review/` görsellerini inceler; (3) aynı hata için
sentetik/birim regresyon testi eklenir; (4) `steelbench_expected.json`'da kaydın `review.status` alanı bağımsız
inceleme sonucuyla güncellenir ve referans özeti (`--write-baseline`) **ayrı** commit'te yenilenir. Bu adımlar olmadan
"giderildi" denmez. Gerçek doğruluk kanıtı ise FAZ 10-A.3/10-A.4 etiketli saha verisini gerektirir.

## İzolasyon

- Ayrı compose projesi `ppe-bench` (aynı `docker-compose.reliability.yml`; kendi PostgreSQL ve snapshot volume'u,
  `127.0.0.1:8200`, kendi rastgele parolaları `tools/reliability/output/bench.env`). `ppe-rel` ve ana yığın
  (`ppe-split`) kullanılmaz; araç bu adlarla veya 8000/8100 portuyla çalışmayı reddeder.
- Her çalıştırmada yalnız `ppe-bench` DB'sindeki olay tabloları boşaltılır; çalıştırma sonunda yığın durdurulur
  (volume silinmez; `down -v` kullanılmaz).
- Geçme koşulu doğrudan kanıttır: bench kamera kimlikleri (`sbv1–3`) ana ve ppe-rel DB'lerinde yok (salt okunur
  SELECT), bench snapshot dosyaları `data/snapshots`'ta yok, üretim `detector/outbox`'ında bench dosyası yok, ppe-rel ve
  ana yığın container'ları değişmedi, dedektörün hedefi bench adresi. Önce/sonra sayımlar ayrıca bilgi olarak yazılır
  (ana yığın eşzamanlı kullanılıyorsa değişebilir).
- `detector/.env` dedektör tarafından okunur; SteelBench `DEVICE, IMGSZ, VID_STRIDE, LOOP, MODEL_PATH, DASHBOARD_URL,
  INGEST_TOKEN, OUTBOX_DIR`'ı açıkça verir. Diğer karar ayarları (CONF, TRACKER, PPEMonitor eşikleri) koddan veya
  `.env`'den gelir ve **gerçekleşen değerleriyle** manifestte kaydedilir; `.env`'deki karar anahtarlarının adları
  raporda listelenir.

## Lisans ve veri kısıtları

- 3 video yerel stok videodur; lisans/kullanım koşulu **doğrulanmadı** (`LICENSE_UNVERIFIED`,
  `docs/data/DATASET_CARD.md`). Yalnız yerel, iç test için; Git'e eklenmez, paylaşılmaz. Kapsam lisansı doğrulanmamış
  yeni videolarla **genişletilmez**; yeni veri `docs/data/DATA_GOVERNANCE.md` kapısından geçer.
- `best.pt` ve Ultralytics AGPL-3.0 (lisans kararı NO-GO).
- Git'e yalnız kod, beklenen değerler ve sayısal referans özeti girer (görüntü, snapshot, token, parola, ham defter yok).

## Cihaz farkları

Referans özeti macOS / MPS'te üretildi. Başka cihaz/sürümde (Windows CUDA) iz kimlikleri ve zamanlar küçük farklar
gösterebilir; ortam farkı raporda `environment_differences` olarak yazılır ve eşleşme toleransı ±1.5 sn, IoU ≥ 0.3'tür.
Cihaza özgü karşılaştırma için o cihazdaki ilk çalıştırmanın `result.json`'ı `--compare` ile verilir.
