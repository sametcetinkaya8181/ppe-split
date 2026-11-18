# Kabul metrikleri — KPI sözlüğü ve İSG/işletme onay formu

**Sürüm:** taslak v0.1 — 2026-11-02 (FAZ 10-A.2)
**Kural:** Bu belgede **hiçbir sayısal hedef yoktur ve hiçbir metrik PASS değildir.** Hedefleri İSG uzmanı ve
işletme sorumlusu yazılı olarak belirler (§3). Hedef ve etiketli bağımsız veri olmadan sonuçlar yalnız "ölçüldü"
olarak raporlanır. Teknik tanımlar `docs/MODEL_EVALUATION_PROTOCOL.md` §6 ile uyumludur.

## 1. İlkeler

1. **Model ve sistem ayrı ölçülür.** Nesne tespiti (mAP) ≠ ihlal kararı ≠ olay teslimi. mAP veya kişi-kare F1
   tek başına kabul ölçütü değildir.
2. **UNKNOWN saklanmaz.** Değerlendirilemeyen kişi/zaman ne "uygun" ne "ihlal yok" sayılır; her raporda ayrı satır.
3. **Payda açık yazılır.** Kamera-saat yalnız **gerçekten analiz edilen** süredir; kopuk/donmuş/kapalı süre ayrı.
4. **Belirsizlik gösterilir.** Oranlar Wilson %95 aralığıyla; küçük örneklemde alt sınır kararda kullanılır
   (30 incident'te gözlenen 0.90 recall → alt sınır ≈ 0.74).
5. **Koşul bazında en kötü durum** (gece, uzak, örtülme, kalabalık) ayrıca raporlanır; ortalama onu gizlemez.
6. **Kilitli test kümesinde eşik ayarı yapılmaz** (yalnız validation).

## 2. KPI sözlüğü

| ID | KPI | Tanım | Pay / payda | Kanıt / kaynak | Bugün ölçülebilir mi | Sahip |
|---|---|---|---|---|---|---|
| K-01 | Incident recall (baret, yelek ayrı) | İnsan onaylı gerçek ihlal olaylarından, tolerans penceresinde (GT başlangıcı −1 sn / bitişi +2 sn) en az bir doğru uyarı alanların oranı | algılanan GT incident / tüm GT incident | Etiketli saha videosu + `python -m evaluation video --gt` | **Hayır** — veri yok (BLOCKED) | ML + İSG |
| K-02 | Yanlış alarm / kamera-saat | İnsan incelemesinde gerçek ihlale karşılık gelmeyen uyarılar | yanlış incident / **analiz edilen** kamera-saat | Etiketli video veya pilotta FR-07 kararları | **Hayır** — veri ve FR-07 yok | ML + İSG |
| K-03 | Kişi/PPE tespit recall (nesne düzeyi) | Üretim çalışma noktasında (conf ≥ 0.25, IoU ≥ 0.5) sınıf başına recall; boy kırılımıyla | TP / GT kutusu | `evaluation detect`; FAZ 8 (dağılım içi: baret 0.906, yelek 0.888, kişi 0.814 — **saha değil**) | Yalnız dağılım içi | ML |
| K-04 | Kapsam / UNKNOWN oranı | Görülen kişi track-karelerinden güvenilirlik kapısını geçemeyenlerin oranı; nedenlere göre; ≥10 sn değerlendirilemeyen kişi sayısı | kapıdan geçemeyen track-kare / tüm track-kare | `telemetry` kapsam sayaçları, `/api/ops/metrics`; FAZ 10-A: %23.6–%91.3 (stok video) | Evet (anlık + run) | ML + İSG |
| K-05 | Analiz edilmeyen kişi riski | Etiketli veride GT ihlallerinden kişinin `ANALIZ`'de kalması nedeniyle kaçırılanların oranı | `fn_undecided` / GT ihlal | `evaluation` kişi-kare tablosu | Hayır (veri yok) | ML |
| K-06 | Duplicate / ID switch | Aynı gerçek incident için ek incident sayısı; GT izi başına ID değişimi | ek incident / GT incident; ID switch / GT izi | `evaluation/temporal.py` | Hayır (veri yok); sentetikte davranış gösterildi | ML |
| K-07 | Uyarı gecikmesi | (a) karar gecikmesi: GT başlangıcı → `start` (video zamanı); (b) teslim: `occurred_at` → `ingested_at`; (c) ekrana görünme: `ingested_at` → operatör ekranı | p50 / p95 (sn), ayrı ayrı | (a) etiketli veri; (b) DB sütunları; (c) **ölçüm yok** | (a) hayır (sentetik alt sınır 2.1–3.2 sn); (b) evet; (c) hayır | ML + Backend |
| K-08 | Kamera çalışma süresi (uptime) ve izlenmeyen süre | Kameranın taze kare ürettiği ve analiz edildiği süre oranı; kopuk/donmuş süre ayrı | analiz edilen süre / planlanan izleme süresi | `detector_runs` (başlangıç/bitiş/durum), sağlık sinyalleri | Kısmen (run süreleri); kalıcı sağlık zaman serisi yok | Backend + BT |
| K-09 | Kalıcı teslim (outbox) | Outbox'a COMMIT edilmiş olaylardan DB'ye ulaşmayan / çift yazılan; en eski bekleyen olay yaşı | kayıp / commit edilen; çift / commit edilen; p95 bekleme | Defter karşılaştırması (`tools/reliability`), outbox durum dosyası | Evet (izole: 0 kayıp, 0 çift) | Backend |
| K-10 | İnsan doğrulama süresi | Uyarının ekrana gelişinden insan kararına (onay/yanlış/belirsiz) kadar geçen süre; karar dağılımı | p50 / p95; karar türü / tüm uyarı | FR-07 kayıtları | **Hayır** — FR-07 yok | Ürün + İSG |
| K-11 | Koşul bazında en kötü recall | K-01'in gece, uzak, örtülme, kalabalık kırılımlarındaki en düşük değeri | koşul içi algılanan / GT | Koşul matrisi (protokol §5) | Hayır | ML + İSG |

**Not:** K-04 kişi sayısını değil track-kareyi sayar; track sayısı kişi sayısı değildir. Hiç tespit edilmeyen
kişiler K-04'te görünmez (yalnız K-01/K-03 ile ölçülür).

## 3. İSG / işletme onay formu

Aşağıdaki tablo **geliştirici ekip tarafından doldurulmaz**. Her satır için hedef türü (alt sınır / üst sınır), değer,
hangi koşullarda geçerli olduğu ve onaylayan kişi yazılır. Boş satır = o metrik için kabul **BLOCKED**.

| KPI | Hedef türü | Hedef değer | Koşul / kamera kapsamı | Minimum örneklem (GT incident / kamera-saat) | Onaylayan (ad, unvan) | Tarih |
|---|---|---|---|---|---|---|
| K-01 Baret eksikliği incident recall | alt sınır (Wilson alt sınırı ile) | | | | | |
| K-01 Yelek eksikliği incident recall | alt sınır | | | | | |
| K-02 Yanlış alarm / kamera-saat | üst sınır | | | | | |
| K-04 Kapsam / UNKNOWN oranı | üst sınır + eylem eşiği | | | | | |
| K-06 Duplicate oranı | üst sınır | | | | | |
| K-07 Uyarı gecikmesi p95 (uçtan uca) | üst sınır (sn) | | | | | |
| K-08 İzlenmeyen süre | üst sınır + eylem | | | | | |
| K-10 İnsan doğrulama süresi p95 | üst sınır (sn/dk) | | | | | |
| K-11 Koşul bazında en kötü recall | alt sınır | | | | | |

Onay sorumlusunun ayrıca yanıtlaması gerekenler:
1. Kapsam (K-04) eşiği aşıldığında hangi manuel eylem yapılır (devriye sıklığı, kamera düzeltme)?
2. Yanlış alarm yükü ile kaçırma riski arasındaki tercih hangi yönde (K-01 ↔ K-02)?
3. Gölge mod pilotunun süresi, kamera sayısı ve minimum örneklem büyüklüğü.
4. Hangi metrikte başarısızlık pilotu durdurur?

**Teknik ekip görüşü (onay değildir):** Gölge mod için ilk karar, incident recall alt sınırı ile yanlış alarm yükünün
**birlikte** belirlenmesi ve kapsam oranının her raporda yanında verilmesidir. 30 incident'lik bir örneklem, gözlenen
0.90 recall'da yalnız ≈ 0.74 alt sınır verir; örneklem büyüklüğü hedefle birlikte seçilmelidir.

## 4. Raporlama şablonu (her ölçüm için)

`model SHA-256 · paket sürümleri · cihaz (CUDA/MPS/CPU) · CONF/IMGSZ/VID_STRIDE · dataset manifest hash · kamera/koşul ·
analiz edilen kamera-saat · GT incident sayısı · K-01…K-11 (değer + %95 aralığı) · UNKNOWN satırı · onaylı hedef ·
sonuç (ölçüldü / hedefi karşılıyor / karşılamıyor / BLOCKED)`
