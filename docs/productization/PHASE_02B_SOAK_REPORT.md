# FAZ 02-B — Uzun Süreli Çalışma (Soak) Raporu

| | |
|---|---|
| **Sonuç** | **PASS (17,8 saat).** A1–A12'nin tamamı karşılandı. Koşu 24 saat hedefinden önce, operatör kararıyla durduruldu (§6, S1) |
| Plan | `PHASE_02B_SOAK_TEST_PLAN.md` (profil P-A, eşikler §7; A5–A11 önerileri kullanıcı tarafından kabul edildi) |
| Koşu | 2026-11-19 17:18:30Z → 2026-11-20 11:09:26Z (ölçülen 17,83 saat, 2141 örnek, 30 s aralık) |
| Kanıt | `tools/reliability/output/20261119T171818Z_soak24h/cams4_stride2/` (Git dışı) |
| Durdurma | Operatör Ctrl+C. Dedektör zarif kapandı; `result.json` yazılmadı (§6, S2). Özet aynı fonksiyonlarla `result.recovered.json` olarak sonradan üretildi |

## 1. Ölçülen sürüm

| | |
|---|---|
| Dashboard imajı | `ppe-dashboard:0.10.7-rc.faz02a1` = `sha256:ea722e05b99f…`; `kind=release`, `tree-state=clean`, revision `65fe6ba` (yerel etiket `rc/0.10.7-rc.faz02a1`) |
| İmaj doğrulaması | `provenance.json`: çalışan imaj istenen imajla aynı (`matches_requested=true`) |
| Dedektör ve araçlar | `e6d6ce3` + commit'lenmemiş FAZ 02-B hazırlık değişiklikleri (`dirty=true`; §6, S3). Dedektör karar kodu (`detector/`) değişmedi |
| Yığın | İzole `ppe-rel` projesi. Üretim projesine komut verilmedi; koşu boyunca üretim yığını kapalıydı |

## 2. Başlangıç koşulları

| | |
|---|---|
| Bellek | İlk örnekte kullanılan 14,6 / 15,3 GB. Başlangıçtaki kullanılabilir bellek ve swap değerleri `result.json` ile birlikte kayboldu (S2) |
| Disk | C: boş 98,25 GB |
| GPU | 52 °C (ilk örnek), 4 GB VRAM |
| Güç | AC; uyku kapalı. Koşu boyunca uyku, askıya alma ya da yeniden başlatma olmadı (son açılış 2026-11-18, örnek aralığı en fazla 31 s) |
| Saat eşitleme ve Windows Update | Koşu sonunda `w32time` durmuş, Windows Update duraklatılmamış durumdaydı (S4) |
| Diğer yükler | Başka projelere ait ilgisiz container'lar kullanıcı kararıyla çalışır bırakıldı |

## 3. Profil ve arıza planı

- 4 kamera, stride 2. `relc1`–`relc3` yerel video döngüleri; `relnet` TCP/MPEG-TS ağ kamerası benzeticisi.
- `relnet` üzerinde her 40 dakikada bir arıza: 60 s kopma, yarı periyotta 45 s donma.
- Dashboard bellek probu (`--memprobe`) açık.
- Ölçülen sürede 52 planlı arıza gerçekleşti (26 kopma + 26 donma). DB'de 52 `source_lost_after_stall` run'ı var.

## 4. Kabul ölçütleri

| # | Ölçüt | Eşik | Ölçülen | Sonuç |
|---|---|---|---|---|
| A1 | Olay kaybı | 0 | Gönderilen 20 697, DB 20 697 (`relc2` 10 620, `relnet` 10 077; `relc1`/`relc3` 0 olay) | **PASS** |
| A2 | Tekrar eden olay | 0 | DB'de 20 697 kayıt, 20 697 tekil `event_id` | **PASS** |
| A3 | Durdurma sonrası bekleyen / ölü / karantina | 0 | 0 / 0 / 0 (outbox ve gönderici kapanış satırları) | **PASS** |
| A4 | Beklenmedik çıkış, bellek ya da disk koruması | 0 | `det_alive=false` hiç yok; süreç kümesi sabit (6 süreç, aynı PID'ler); korumalar tetiklenmedi | **PASS** |
| A5 | Dedektör RSS: pencere medyanlarında monoton artış yok; son 12 saat eğimi ≤ +10 MB/saat | | Medyanlar 3828 → 3631 → 3541 MB; eğim **−21,1 MB/saat**. Handle 2609 ve thread 109 sabit | **PASS** |
| A6 | Dashboard bellek: son 12 saat eğimi ≤ +2 MB/saat | | RssAnon **+0,05**, cgroup anon **+0,15**, docker stats toplamı +1,49 MB/saat (sayfa önbelleği dahil, §5.2) | **PASS** |
| A7 | Dosya kameralarında FPS ≥ ilk pencerenin %90'ı | | Tüm pencerelerde ilk pencerenin %100'ü ya da üstü (12,0–12,6) | **PASS** |
| A8 | Gecikme p95 ≤ 1 s (dosya kameraları; ağ kamerasında kopma dışında) | | Dosya kameraları p95 −0,07 s, maks 0,66 s; `relnet` bağlıyken p95 −0,21 s, maks 0,31 s | **PASS** |
| A9 | Her planlı arızadan sonra ≤ 120 s içinde yeniden işleme | | 52/52 toparlandı. Run bitişi → yeni run başlangıcı p50 75 s, p95 90,8 s, maks **92,4 s** | **PASS** |
| A10 | `/api/state` p95 ≤ 100 ms; HTTP 5xx = 0 | | p50 16,6, p95 **39,5**, p99 55,6, maks 266 ms; 5xx **0** | **PASS** |
| A11 | GPU ≤ 85 °C | | Maks **75 °C**, p95 74 °C | **PASS** |
| A12 | Kesinti (örnek aralığı > 90 s) | 0 | En büyük aralık 31 s | **PASS** (geçerli) |

## 5. Ayrıntı

### 5.1 6 saatlik pencereler (ilk saat ısınma olarak dışlandı)

Koşu 17,83 saatte bittiği için 4 değil 3 pencere var; üçüncüsü 4,8 saat.

| Pencere (saat) | Dedektör RSS medyan (MB) | Dashboard RssAnon medyan (MB) | Dashboard docker stats medyan (MB) | FPS medyan relc1 / relc2 / relc3 |
|---|---|---|---|---|
| 1–7 | 3 828 | 82,5 | 92,2 | 12,42 / 12,00 / 12,42 |
| 7–13 | 3 631 | 83,9 | 100,9 | 12,42 / 12,00 / 12,42 |
| 13–17,8 | 3 541 | 84,1 | 110,1 | 12,60 / 12,00 / 12,43 |

Kamera başına dedektör süreç RSS medyanları (MB): `relc1` 957 → 928, `relc2` 1034 → 862, `relc3` 787 → 773, `relnet` 746 → 741. Hiçbiri büyümedi. `torch_reserved` 130–184 MB, sabit.

### 5.2 Bellek

- **Dedektör:** ilk saatte toplam RSS ~6,4 GB (duman koşusundaki gibi). Sonra 3,5–3,8 GB'a indi ve öyle kaldı. Bu değer Windows'ta working set'tir; düşüş büyük olasılıkla işletim sisteminin kırpmasıdır. Büyüme belirtisi yok.
- **Dashboard:** RssAnon 71 → 84 MB arttı; artış ilk saatlerde, son 12 saat düz. glibc `in_use` +0,008 MB/saat; thread 9–13; fd 18–23. Docker stats toplamındaki +1,49 MB/saat sayfa önbelleğidir (cgroup anon düz); eşiğin altında.
- **İzlenecek:** dashboard Python blok sayısı 435 579 → 473 139 (+%8,6; son 12 saat +262 blok/saat). Bu artış RSS'e yansımıyor ve bir ölçütü bozmuyor. Daha uzun bir koşuda tekrar bakılmalı.
- **Sistem:** kullanılan bellek p50 13,1, p95 13,8, maks 14,6 / 15,3 GB. Bellek koruması tetiklenmedi ama pay dar (plan B5 açık).

### 5.3 Ağ kamerası arızaları

- 52 arızanın hepsi bir `source_lost_after_stall` run kapanışı ve ardından yeni bir run açılmasıyla sonuçlandı; atlanan ya da takılı kalan run yok.
- Run arası süreye kopma ya da donmanın kendi süresi (60 / 45 s) dahildir. Kaynak geri geldikten sonra işlemeye dönüş bu sürelerden de kısadır.
- Örnek tabanlı (±30 s) "bağlı değil" blokları: 52 adet, p50 60 s, maks 90 s.
- `detector.log`'daki 130 `HATA` satırının hepsi aynı türde: kopma sırasında yeniden bağlanma denemeleri (`Kaynak açılamadı … tcp://127.0.0.1:9150`). Başka hata ya da Traceback yok.
- Kopma anlarında `relnet` en düşük FPS 2,7, en yüksek gecikme 19,6 s.

### 5.4 Dashboard ve teslim yolu

- Alınan istekler: `/ingest/frame` 3 079 114, `/ingest/events` 20 688, `/ingest/report` 11 235. Bu sayılar son örnekten (durdurmadan 30 s önce); hata 0. DB'de olay hatası 0.
- DB havuzu boştaydı (`checked_out=0`). Dashboard container log'unda Traceback 0.
- Snapshot: 12 734 dosya, 464 MB (~26 MB/saat). Disk boş alanı 98,25 → 95,83 GB.
- **Oturum süresi dolumu (beklenen):** 12. saatte (2026-11-20 05:18:54Z) ölçüm istemcisinin oturumu mutlak süre sınırına ulaştı (`SESSION_MAX_HOURS=12`). WebSocket 1008 ile kapandı ve bir 401 sayıldı. İstemci yeniden bağlandı; bağlanma hatası 0, kareler arası en büyük boşluk 0,094 s. Bu bir güvenlik davranışıdır, arıza değil.

### 5.5 Kapsam dışı gözlem

Dosya kameralarında `unevaluated_persons` / `unevaluated_long` bayrakları görüldü. Bunlar aynı stok videolardaki analiz kapsamıyla ilgili (karar verilemeyen kişiler) ve güvenilirlik ölçütü değil. Algılama kalitesi bu fazın konusu değildir.

## 6. Sapmalar

| ID | Sapma | Etki | Karar / öneri |
|---|---|---|---|
| S1 | 24 saat yerine 17,83 saat (operatör kararı) | A5 3 pencereyle değerlendirildi (sonuncusu 4,8 saat). Son 12 saatin eğimi hesaplanabildi. Diğer ölçütler süreden bağımsız | Kabul edildi. 24 saatin üstündeki davranış (ör. ikinci oturum süresi dolumu) ölçülmedi |
| S2 | Ctrl+C sonrası `result.json` yazılmadı | `soak.py`'de `finally` dedektörü kapatıyor, ama `KeyboardInterrupt` `finally`'den sonra yeniden yükseliyor; teslim karşılaştırması ve sonuç yazımı çalışmıyor. Plan §8.2'deki "Ctrl+C sonuçları yazar" ifadesi yanlış. Kaybolanlar: başlangıçtaki kullanılabilir bellek/swap, arıza zaman günlüğü, `stop_detector` süresi | Ham kanıt kaybolmadı. Teslim, run, log ve memprobe özetleri `soak.py`'nin kendi fonksiyonlarıyla `result.recovered.json` olarak yeniden üretildi (izole DB'ye yalnızca SELECT). **Düzeltildi:** ölçüm döngüsü `soak.measure()` içine alındı. `KeyboardInterrupt` orada yakalanıyor ve `stop_reason=operator_interrupt` yazılıyor; ardından SIGINT yok sayılıyor ve son işlemlerle `result.json` çalışıyor. Her durma nedeni artık `stop_reason`'a yazılıyor. Testler `tools/reliability/test_soak_stop.py` (5); plan §8.2 güncellendi |
| S3 | Araç kodu commit'lenmemiş değişikliklerle çalıştı | Ölçülen dashboard imajı temiz ve doğrulanmış; dedektör karar kodu değişmedi. Araç sürümü tek bir SHA'ya bağlanamıyor | FAZ 02-B hazırlık değişiklikleri bu raporla birlikte tek commit'e alınmalı |
| S4 | `w32time` durmuş, Windows Update duraklatılmamış | Koşuya etkisi olmadı (yeniden başlatma yok, örnek aralığı ≤ 31 s). Saat kayması 17,8 saatlik göreli ölçümleri etkilemez | Bir sonraki uzun koşudan önce §4 ön kontrol listesi uygulanmalı |

## 7. 4 saatlik koşuyla karşılaştırma

| | 4 saat (önceki) | 17,8 saat (bu koşu) |
|---|---|---|
| Dashboard imajı | Düzeltme öncesi | RC `0.10.7-rc.faz02a1` |
| Olay teslimi | 4 656 / 4 656 | 20 697 / 20 697 |
| Ağ kamerası toparlanması | 40 yeniden bağlanma, hepsi toparlandı | 52 arıza, hepsi ≤ 92,4 s içinde toparlandı |
| Dedektör RSS | p50 1 792 MB, eğim −55 MB/saat (bellek baskısı altında) | İlk saat ~6,4 GB, sonra 3,5–3,8 GB; eğim −21 MB/saat |
| Dashboard bellek eğimi | `dash_mem` +3,9 MB/saat | anon +0,05–0,15 MB/saat, toplam +1,49 MB/saat (sayfa önbelleği) |
| `/api/state` p95 | 40 ms | 39,5 ms |
| HTTP 5xx | 0 | 0 |
| GPU maks | 74 °C | 75 °C |
| Dosya kamerası FPS | 12,0–12,4 | 12,0–12,6 |

Dashboard bellek eğiminin +3,9'dan +0,15 MB/saate (anon) inmesi, FAZ 10-A.1 düzeltmesinin RC imajında etkili olduğunu gösteriyor.

## 8. Sonraki adımlar

1. ✅ **S2 düzeltmesi:** `soak.py` Ctrl+C davranışı, test ve plan §8.2. Bu raporla aynı commit'te.
2. ✅ **Tek commit:** FAZ 02-B hazırlık değişiklikleri, bu rapor ve S2 düzeltmesi. Push kullanıcıda.
3. Açık kararlar (FAZ 02-A raporu): K3 saklayıcı anahtarlar ve 2026-11-18 yedeğinin şifrelenmesi, K2b harici disk, K6 `0007` penceresi, K8 sürüm numarası (RC'nin yeni SHA'dan yeniden derlenmesi), K9 `.env` yedeği ve parola rotasyonu.
4. **İsteğe bağlı:** gerçek RTSP kamerayla P-B profili (B2); 24 saati aşan koşu; dashboard Python blok sayısının izlenmesi (§5.2).
