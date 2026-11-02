# FAZ 10-A.5A raporu — ML hata analizi (Windows CUDA)

Tarih: 2026-11-02 (UTC). Başlangıç: `99217bf` (FAZ 10-A.4). Ortam: Windows 11, PowerShell, NVIDIA RTX 3050 Laptop
(4 GB), Python 3.12.6, PyTorch 2.14.1+cu126, Ultralytics 8.4.183.

Bu fazda **değişmeyenler:** `best.pt`, `ppe_core.py`, alarm/karar eşikleri, tracker yapılandırması, dashboard,
ana PostgreSQL, snapshot'lar, outbox, kullanıcı verileri. FAZ 10-A.1 ana dashboard düzeltmesi ve 24 saatlik soak
**başlatılmadı**. İki alarm **UNREVIEWED** kaldı. Ground-truth yok: **precision, recall, yanlış alarm oranı ve model
doğruluğu hesaplanmadı.**

## Özet — hangi hata hangi katmandan

| Hata | Asıl kaynak | Katkıda bulunan | Kesinlik |
|---|---|---|---|
| E-01 V2 örtülen koyu baret → "Baretsiz" alarmı | **Model**: baret kanıtı üretmiyor (45 değerlendirilen karenin 28'inde 0.05 eşiğinde bile baret yok); `no_helmet` sınıfı hiç tetiklenmiyor | **Karar mantığı**: baret kutusunun yokluğu "baretsiz" sayılıyor (bilinmiyor durumu yok). Tracker 4 kareyi düşürüyor ama alarm üzerinde belirleyici değil | Model kanıt eksikliği ve karar mekanizması: **KESİN** (ölçüm + yeniden oynatma). Kişinin gerçekten baret taktığı: **insan onayı bekliyor** |
| E-02 V3 iş makinesi → Person → alarm | **Model**: ham Person tespiti (iz boyunca %100 ham destek, güven 0.25–0.62, medyan 0.39) | **Karar mantığı**: kare bazlı 0.40 kapısı; güven eşik etrafında dalgalanırken 14 kare geçiyor, MIN_OBS=8 sağlanıyor. **Tracker**: aynı nesne 111 → 160 olarak yeniden başlıyor, boşluklarda izi canlı tutuyor | Model kaynaklı yanlış kutu: **YÜKSEK** (nesnenin makine olduğu insan onayı bekliyor). Alarmın karar katmanında oluşması: **KESİN** |
| E-03 V2 kenardaki çalışan değerlendirilemiyor | **Karar mantığı (kapı tasarımı)** + **kamera kadrajı**: kişi-karelerin %90'ı (500/554) kenar kapısında; 437'si yalnız **alt** kenar (baş görünür) | — (model sorunu değil) | **KESİN** |
| E-04 V1 6 kimlik / eşzamanlı en çok 3 | **Tracker**: ilişkilendirme başarısızlıkları + bir eşzamanlı çift tespit | **Model**: aynı çalışana ikinci (kısmi) Person kutusu; **yapılandırma**: `CONF=0.25` ByteTrack'in düşük skor aşamasını devre dışı bırakıyor | Mekanizmalar: **KESİN/ORTA**. "2 fiziksel kişi" iddiası: **sezgisel**, GT yok, kimlik değişimi sayısı **ölçülmedi** |

## 1. Başlangıç kontrolleri

| Kontrol | Sonuç |
|---|---|
| Git | dal `main`, HEAD `99217bf` (FAZ 10-A.4), `e1f1e48` (FAZ 10-A.1) geçmişte. İzlenmeyen tek dosya `docs/PPE_Saha_Hazirlik_Claude_Opus_Plani.md` (bu faza ait değil; commit'e **eklenmedi**) |
| `best.pt` SHA-256 | `6886ad2fc301cf992152d4b047307071cac940082ab071175c0ef498fe67ba43` — FAZ 8–10-A.4 ile aynı (faz sonunda yeniden doğrulandı) |
| FAZ 10-A.4 kanıtı | `tools/reliability/output/20261102T174149Z_baseline_cuda/` (result.json, trace/events JSONL, review/) mevcut ve incelendi |
| Docker | `ppe-split` (dashboard + db) çalışıyor; `ppe-eval` ve `ppe-rel` durdurulmuş; diğer projeler (`llminsight`, `docker-flowise`) — **dokunulmadı** |
| Devam eden test | yok (çalışan iki Python süreci başka projeye ait: LLM_Insight; dokunulmadı) |
| Ana sistem izolasyonu (faz sonu) | ana DB sayımları `104\|47\|74\|255\|110` = FAZ 10-A.4 kaydı; snapshot 51 = 51; üretim outbox'ı yok (değişmedi) |

### 1.1 Gizli bilgi denetimi (INGEST_TOKEN sızıntısı)

FAZ 10-A.4'te bulunan hata: `production_settings()` → `import detector` → `detector/.env` süreç ortamına yükleniyor;
Compose'da kabuk ortamı `--env-file`'ı ezdiğinden ilk koşuda (`20261102T173539Z`) izole `ppe-eval` dashboard'u
**üretim token'ıyla** oluşturulmuştu (127.0.0.1'e bağlı, yerel). Düzeltme `common.compose` içinde (yığın anahtarları
alt süreç ortamından çıkarılır) ve container yeniden oluşturulmuştu.

Bu fazda yeni araç `tools/reliability/secret_audit.py` (salt okunur; **hiçbir değer yazdırmaz**, yalnız eşleşme/sayım):

| Denetim | Sonuç |
|---|---|
| Üretim değerleri (kök `.env` + `detector/.env`: INGEST_TOKEN, 3 DB parolası) | mevcut; iki dosyadaki INGEST_TOKEN birbirine eşit (beklenen) |
| Test env dosyaları (`output/rel.env`, `output/eval_stack/rel.env`) üretim değerine eşit mi | **hayır** (0 eşleşme) |
| Test container'ları (ppe-eval, ppe-rel; 10 container) `docker inspect` ortamı | üretim değeri **yok** |
| Kanıt klasörü `tools/reliability/output/**` (537 metin dosyası: log, json, jsonl, md, env) | üretim değeri **yok** |
| Git: izlenen 150 dosya + tüm geçmiş (`git log --all -p`, ~2 MB) | üretim değeri **yok** |
| **Karar** | **PASS** |

Kalan risk: değerlendirme süreçlerinin kendi ortamında üretim token'ı hâlâ bulunuyor (`import detector` yan etkisi).
Compose ve dedektör alt süreçleri bunu eziyor/temizliyor; ileride ortamı devralan yeni bir alt süreç eklenirse sızıntı
tekrarlayabilir. Öneri (bu fazda uygulanmadı, üretim kodu): `production_settings()` dedektör `.env`'ini süreç ortamına
yazmadan okusun. Token geçmişte yalnız yerel, dışarı kapalı bir test container'ının ortamında bulunduğundan döndürme
(rotation) zorunlu görünmüyor; ihtiyat için döndürme kararı kullanıcıya aittir.

## 2. Yöntem ve tanılama araçları

**Ultralytics/ByteTrack davranışı (kaynak kodundan doğrulandı, `ultralytics/trackers/track.py`, `byte_tracker.py`):**

1. Tracker **sınıftan bağımsız**: 11 sınıfın tamamı tek ByteTrack örneğinde eşleşir; bir izin sınıfı son eşleşen
   tespitin sınıfına döner. `model.track` sonucu **yalnız tracker çıktısını** içerir.
2. Yeni iz ilk karesinde **onaysızdır** (ilk video karesi hariç) ve çıktıya girmez → tek karelik tespitler (ör. bir
   karede görünen baret) karar katmanına **hiç ulaşmaz**.
3. Kutular ham tespit değil, Kalman ile güncellenmiş iz durumudur.
4. `model.track(conf=0.25)` → tracker'a yalnız ≥0.25 tespit gelir; `track_low_thresh=0.1` ile tanımlı **ikinci (düşük
   skorlu) eşleştirme aşaması fiilen devre dışı**.
5. Eşleştirme maliyeti `fuse_score` ile `1 − IoU×skor`; `match_thresh=0.8` → eşleşme için IoU×skor ≥ 0.2 gerekir.
   Kayıp iz tamponu 30 tracker çağrısı = VID_STRIDE 2'de ≈2.4 sn (V2: 2.5 sn).

**Tanılama aracı** `tools/reliability/failure_analysis.py`: üretim karar zinciri `pipeline.run_video` aynen çalışır;
yalnız gözlem kancaları eklenir: (a) tracker'dan **önce** çalışan geri çağrı → ham YOLO tespitleri (NMS sonrası,
≥0.25); (b) `pipeline.extract` sarmalayıcısı → tracker çıktısı tüm sınıflarıyla; (c) **ayrı model örneği** aynı kareyi
`conf=0.05` ile tahmin eder ("sonda": eşik altı adaylar; üretimde yok, yalnız tanı).

**Eşdeğerlik:** tanı koşusu `20261102T181114Z_failure_analysis`, FAZ 10-A.4 iziyle **birebir aynı** — V1 186/186,
V2 288/288, V3 157/157 kare (kimlik, kutu, güven) ve olaylar aynı. Kancalar karar zincirini etkilemiyor.

**Analiz** `detector/evaluation/failure.py` (saf fonksiyonlar, 18 birim testi):

- *Eşya kanıtı neden sınıflandırması* (karar zincirine yakından uzağa): `matched` → `tracked_item_unassigned`
  (tracker çıktısında bölgede var ama başka kişiye atanmış) → `item_outside_region` (kişi kutusunda ama baş/gövde
  bölgesi dışında) → `raw_item_dropped_by_tracker` → `item_below_conf` (yalnız 0.05–0.25) → `no_detection`.
  `no_helmet` kanıtı ayrıca raporlanır.
- *İz özellikleri* (güven, boyut, en/boy, ham destek oranı, sınıf geçmişi), *iz kayıpları* (sonrasında aynı kimlik başka
  sınıfla mı sürdü / yakında ham kişi tespiti var mıydı / tespit kesintisi), *parçalanma adayları* (kısa boşluk + yakın
  konum; **sezgisel**), *eşzamanlı çift tespit* adayları.
- *Kapsam kırılımı*: kapı nedeni birleşimleri, kenar tarafları, ısınma (kapıdan geçti ama MIN_OBS/MIN_SPAN dolmadı).
- *Karar katmanı yeniden oynatma*: tracker ve eşleştirme çıktısı sabit, yalnız PPEMonitor yeniden çalışır; değişiklik
  yokken üç videoda da üretim olaylarını **birebir** verir. Varyantlar yalnız "ne olurdu" içindir — doğruluk değildir;
  yeni çıkan/kaybolan alarmların doğru mu yanlış mı olduğu bilinmez.

### Tekrarlanabilir komutlar (proje kökü, PowerShell)

```powershell
detector\.venv\Scripts\python.exe tools\reliability\secret_audit.py
detector\.venv\Scripts\python.exe tools\reliability\failure_analysis.py collect --baseline tools\reliability\output\20261102T174149Z_baseline_cuda
detector\.venv\Scripts\python.exe tools\reliability\failure_analysis.py analyze tools\reliability\output\<UTC>_failure_analysis
detector\.venv\Scripts\python.exe tools\reliability\failure_analysis.py review  tools\reliability\output\<UTC>_failure_analysis
cd detector; .venv\Scripts\python.exe -m pytest tests\test_evaluation_failure.py -q
```

Çıktılar (Git dışı): `diag_V*.jsonl` (kare başına ham / tracker / sonda / kişi kaydı), `manifest.json`,
`analysis.json`, `ANALYSIS.md`, `review_pack/`. Toplama süresi ≈1 dk (RTX 3050).

## 3. Hata ve kanıt matrisi

Kanıt kökü: `tools/reliability/output/20261102T181114Z_failure_analysis/` (aşağıda `FA/`).

### E-01 — V2, kısmen örtülen koyu baret (track 3, alarm 4.7 → 14.8 sn)

| Kalem | İçerik |
|---|---|
| Gözlenen belirti | Track 3 için "Baretsiz" incident (start 4.7, ongoing 10.3/14.0, end 14.8). Yelek oranı %100 |
| Kanıt | `FA/analysis.json` → `videos.V2.alarms[0].series_helmet`, `helmet_causes_alarm_pids.3`; `FA/diag_V2.jsonl`; inceleme R029/R005 |
| Ham sınıf tahminleri | Track 3, 266 kare; yalnız **45 kare** kapıdan geçti (kalan 221 kare kenar ve/veya düşük güven). Bu 45 karenin baret nedeni: eşleşti 7, **tracker düşürdü 4** (ham baret 0.297 / 0.676 / 0.549 / 0.735), **yalnız eşik altı 6** (0.052–0.237), **hiç yok 28**. Person güveni 0.48–0.85 (medyan 0.78). `no_helmet`: ham **0**, sonda (≥0.05) **0** — üç videonun hiçbir karesinde `no_helmet` yok |
| PPE-kişi eşleştirmesi | Bölge dışı / yanlış kişiye atama **0** kare — eşleştirme hatası yok. Baret göründüğü karelerde doğru eşleşiyor (5.09, 13.93–14.76 sn; oran 0.62'ye çıkınca `end`) |
| Yanlış kararın oluştuğu katman | Model kanıt üretmiyor → PPEMonitor `h_rate < ENTER_RATE(0.20)` → `start`. Karar mantığı "kanıt yok" ile "ekipman yok"u ayırt etmiyor |
| Eksik tespit ↔ doğrulanmış yokluk | Model **hiçbir karede** "baret yok" kanıtı (`no_helmet`) vermedi; alarm tamamen **eksik tespit** üzerine kurulu. Doğrulanmış yokluk kanıtı **yok** |
| Yeniden oynatma | Eşyalar tracker öncesi ham tespitten eşleşse: **aynı alarm** (4.7) → tracker katkısı belirleyici değil. Eşya eşiği 0.15 veya 0.10: alarm **ikiye bölünür** (4.7 + 10.3) → eşik düşürmek kanıtı titreştirip incident sayısını artırıyor |
| Muhtemel kök neden | (1) Model: koyu, arkadan görünen, fırçayla kısmen örtülü baret için düşük duyarlılık (eğitim verisinde bu görünüm az olabilir — **doğrulanmadı**, eğitim verisi bu makinede yok). (2) Karar mantığı: üç durumlu olması gereken baret kararı iki durumlu |
| Kesinlik | Model kanıt eksikliği **KESİN** (ölçüm); karar mekanizması **KESİN** (kod + yeniden oynatma); "kişi gerçekten baret takıyor" **AI gözlemi** — inceleme görüntülerinde koyu bir baret fırçanın arkasında görünüyor; **insan onayı yok, etiketlenmedi** |
| Önerilen çözüm | **Karar mantığı:** baret için üç durum — `var` / `yok-kanıtı` / `belirsiz`; baş bölgesi örtülü veya kanıt yoksa `belirsiz` (alarm değil, "değerlendirilemiyor" sayacı). `no_helmet` sınıfının kanıt olarak kullanımı ancak bu sınıfın sahada tetiklendiği GT ile gösterildikten sonra (şu an üç videoda 0 tespit). **Model:** ayrı aday ağırlıkla koyu/örtülü/arkadan baret örnekleri |
| Olası yan etkiler | `belirsiz` durumu gerçek baretsiz çalışanları da (başı örtülü/arkası dönük) alarm dışı bırakır → **kaçırılan gerçek ihlal artabilir**. `no_helmet` şartı koşulursa, model bu sınıfı nadiren ürettiğinden gerçek ihlaller büyük ölçüde kaçırılabilir |
| Gerekli ek veri | İzinli saha videosu; baş görünürlüğü (`visible/occluded/out_of_frame`) ve baret durumu etiketli GT (`ppe-eval-video/1` `unknown/occluded` alanları); koyu baret + örtülme örnekleri; gerçek baretsiz örnekler (kaçırma riskini ölçmek için) |
| Başarı ölçütü | GT'li dondurulmuş test setinde baret incident yanlış alarmı ↓ **ve** baret incident recall'ı önceden kabul edilen marjdan fazla düşmez (Wilson %95 GA ile); "belirsiz" oranı raporlanır |
| Regresyon testi | `test_evaluation_failure.py` neden sınıflandırma testleri; V2 yeniden oynatmasında track 3 için durum `belirsiz` olmalı; gerçek baretsiz sentetik/GT örneklerinde alarm sürmeli |

### E-02 — V3, iş makinesi Person olarak algılandı (track 160, alarm 9.4 sn → video sonu)

| Kalem | İçerik |
|---|---|
| Gözlenen belirti | Track 160 "Baretsiz" incident, `run_end` ile kapandı |
| Kanıt | `FA/analysis.json` → `videos.V3.tracks.160`, `alarms[0].series_helmet`, `losses`, `fragment_candidates`, `replay_variants`; inceleme R017/R012/R020/R001/R027/R007 |
| Güven / boyut / süreklilik | 30 kare, 6.96–11.04 sn; güven min 0.254, medyan **0.39**, en yüksek 0.62; **14 kare** ≥0.40 (kapıdan geçti), 16 kare `low_conf`. Yükseklik medyan 386 px (kare yüksekliğinin %10'u), en/boy 0.61 (çalışanlar 0.34–0.35). Ham Person desteği **%100** → kutu tracker uydurması değil, **YOLO'nun kendi tespiti**. Yelek eşleşmesi %33 (makine üzerinde yelek tespiti de var), baret %0 |
| ByteTrack | Aynı konumda önce **track 111** (4.80–5.44 sn, 5 kare, hepsi `low_conf`, medyan 0.336) → kayıp; 1.52 sn sonra 160 başladı (IoU 0.56, merkez farkı 0.14×yükseklik) → aynı nesnenin parçalanması **adayı**. 160 boyunca kare boşlukları (ör. 7.04 → 7.60) tracker tamponuyla köprülendi. Sınıf geçmişi yalnız Person |
| Nasıl incident oldu | Kare bazlı kapı (güven ≥0.40) dalgalanan güvenin üst kısmını seçiyor → 9.44 sn'de 8. güvenilir gözlem, MIN_SPAN 2 sn dolu, baret oranı 0 → `start`. Track 111 hiç kapıdan geçmediği için ayrı incident oluşmadı |
| Yeniden oynatma (ne olurdu) | `PERSON_CONF_MIN` 0.45: alarm 10.9 sn'ye kayar. **0.50: alarm yok** — ancak 160'ın 5 karesi hâlâ geçiyor, MIN_OBS=8'e yalnız 3 eksik → **kırılgan**. 0.50'nin alarm dışı izlerdeki kapıdan geçen kare maliyeti: V1 224 → 218 (−6; biri çift tespit 66), V2 45+3 → 44+3 (track 3, E-01 alarmı), V3 314 → 314 |
| İz düzeyi ayrım (gözlem) | Kayan medyan güven (son ~25 gözlem, ≥8 gözlemden sonra): alarm dışı tüm izler **≥0.712**; track 160 **≤0.418**. Diğer düşük medyanlı izler (V1 66: 0.437, V2 74: 0.395, V3 111: 0.336) AI gözlemine göre çift/sahte tespitler. **3 video, yakın çekim — genellenemez** |
| Muhtemel kök neden | Model: kırmızı/turuncu, eklemli makine kolu → Person (sert negatif örnek eksikliği olasılığı — **doğrulanmadı**). Karar mantığı: iz düzeyinde değil, kare düzeyinde güven kapısı |
| Kesinlik | Yanlış kutunun modelden gelmesi **YÜKSEK** (ham destek %100; nesnenin makine kolu olduğu AI gözlemi + FAZ 10-A.4 notu; **insan onayı yok**). Alarmın karar katmanında oluşma biçimi **KESİN** (yeniden oynatma) |
| Önerilen çözüm | **Karar mantığı:** alarm için iz düzeyi güven kalıcılığı (kayan medyan ≥ τ, τ yalnız dev setinde seçilir) ve/veya kapıdan geçen gözlem oranı; **Kapsam/kurulum:** kamera başına isteğe bağlı hariç tutma bölgesi (makine park alanı; saha yapılandırması, onaylı); **Model:** ayrı aday ağırlıkla iş makinesi sert negatifleri |
| Olası yan etkiler | Uzak, kısmen örtülü, karanlıkta gerçek çalışanların izi düşük medyanlı olabilir → **kaçırılan gerçek ihlal**. Hariç tutma bölgesine giren çalışanlar hiç değerlendirilmez. Sert negatif eğitimi kişi duyarlılığını düşürebilir |
| Gerekli ek veri | Uzak/örtülü gerçek çalışanların güven dağılımı (izinli saha verisi), iş makinesi içeren GT'li klipler |
| Başarı ölçütü | GT'li test setinde kişi-olmayan nesne kaynaklı incident sayısı ↓; çalışan incident recall'ı ve kişi-kare kapsamı kabul marjı içinde; τ'nun dev ↔ test kararlılığı |
| Regresyon testi | V3 yeniden oynatmasında track 160 için `start` yok; V1–V3 alarm dışı izlerde kapıdan geçen kare kaybı raporlanır; düşük güvenli gerçek çalışan GT örnekleri alarm üretmeye devam eder |

### E-03 — Kadraj kenarı ve kapsam kaybı

| Kalem | V1 | V2 | V3 |
|---|---|---|---|
| Kişi-kare | 293 | 554 | 349 |
| Kapıdan geçen | 224 (%76.4) | **48 (%8.7)** | 328 (%94.0) |
| `edge` (yalnız) | 67 — hepsi **alt** kenar | 500 — alt 437, sol+alt 17, sağ+alt 19, üst+alt 9, sol+üst+alt 15, sağ 2, sol+üst 1 | 0 |
| `low_conf` (+ birleşik) | 2 | 2 + 4 (`low_conf+edge`) | 21 — **tamamı makine izlerinde** (111: 5, 160: 16); çalışanlarda 0 |
| `too_small` | 0 | 0 | 0 |
| Kapıdan geçti, ısınmada (MIN_OBS/MIN_SPAN dolmadı → ANALIZ) | 90 | 14 | 57 |
| Karar verilmiş kapıdan geçen kare | 134 | 34 | 271 |
| Eşleştirme hatası (kapıdan geçen karede baret tracker'da var ama atanmadı / bölge dışı) | 4 (1 + 3) | 0 | 0 |

| Kalem | İçerik |
|---|---|
| Gözlenen belirti | V2 track 1 (kırmızı baretli, AI gözlemine göre uyumlu) 24 sn boyunca hiç karar almadı (286 karenin 282'si `edge`) |
| Kanıt | `FA/analysis.json` → `videos.*.coverage`, `replay_variants.edge_exempt_*`; inceleme R024/R021/R006 vb. |
| Muhtemel kök neden | Kenar kapısı kişinin **herhangi bir** kenara değmesinde tüm kararı reddediyor; oysa baret kararı yalnız başın görünmesini gerektirir. Yakın çekim kadrajda gövde altı her zaman kesik. Model sorunu **değil** |
| Kesinlik | **KESİN** (kod + sayım). Üst kenar (baş kesik) içeren kare yalnız 25 → bunlar gerçekten değerlendirilemez |
| Yeniden oynatma (ne olurdu) | Alt kenar muafiyeti: V2 kapıdan geçen 48 → 485; track 1 karar alır (alarm yok); **ama track 3 alarmı 4.7 → 2.1 sn'ye öne çekilir ve 22.4 sn'de ikinci incident eklenir** (E-01 kaynaklı). V1: 224 → 291, alarm yok. V3: değişmez |
| Önerilen çözüm | **Karar mantığı:** eşyaya özgü kapı — baret için yalnız üst kenar + baş bölgesi görünürlüğü; yelek için gövde bölgesi görünürlüğü; eşya başına `değerlendirilemiyor` nedeni. **Kapsam:** kamera yerleşim rehberi (çalışan boyu kadrajın ≈%30–60'ı, tam boy); dashboard'da kapsam oranı (FAZ 10-A'da eklendi) |
| Olası yan etkiler | Kapsam artınca alarm sayısı artar; E-01 çözülmeden uygulanırsa örtülü baret yanlış alarmları **artar** (yeniden oynatmada gösterildi). Kesik kutuda gövde bölgesi yanlış hesaplanır → yelek kararı ayrı tutulmalı |
| Gerekli ek veri | Kişi-kare GT'sinde görünürlük etiketi; gerçek saha kamerası açıları |
| Başarı ölçütü | GT'de "baş görünür" kişi-karelerin değerlendirme oranı ↑; aynı kümede baret yanlış alarmı ve kaçırma oranı kabul marjı içinde |
| Regresyon testi | `test_replay_edge_exempt_and_items_from_raw`; `telemetry.gate_reasons` ↔ PPEMonitor eşdeğerlik testi (mevcut); V2 yeniden oynatmasında track 1 kapsamı |

### E-04 — V1, ByteTrack kimlik parçalanması (6 kişi kimliği, eşzamanlı en çok 3)

| Kalem | İçerik |
|---|---|
| Gözlenen belirti | 6 kişi kimliği (3, 6, 63, 66, 67, 81). Tüm sınıflarda 21 tracker kimliği tüketildi (V2: 36, V3: 52) |
| Kanıt | `FA/analysis.json` → `videos.V1.tracks`, `losses`, `fragment_candidates`, `concurrent_duplicates`, `person_ids_with_other_classes`; inceleme R019/R013/R008/R002 (`new_track_start`) |
| Parçalanma adayları (sezgisel) | **3 → 63**: boşluk 0.24 sn, IoU(son, ilk) 0.136 × güven 0.719 = **0.097 < 0.2** → ByteTrack eşleştirme sınırının altında; en/boy 0.43 → 0.67 (kutu şekli hızlı değişti). **63 → 81**: 0.16 sn, IoU×güven 0.238 (son kutuyla eşik üstü; Kalman tahmini kaydedilmediği için neden **belirsiz**). **6 → 67**: 1.04 sn (13 çağrı, tampon içinde), IoU×güven 0.413; kayıp sırasında önünden araç geçiyor (AI gözlemi, R008) → örtülme + Kalman sapması adayı |
| Eşzamanlı çift tespit | 9.28 sn'de **66 ve 67 aynı çalışanda iç içe** (66: 1 kare, güven 0.437, kısmi gövde). Kimlik 66 sonraki 4 karede **yelek** sınıfıyla sürdü (sınıftan bağımsız tracker). "Eşzamanlı en çok 3" değeri bu çift tespitle şişmiş |
| Kayıp türleri | V1: ilişkilendirme hatası adayı (kayıptan sonra yakında ham kişi tespiti var) 4, kimlik başka sınıfla sürdü 1, tespit kesintisi 0. V2: track 74 (2 kare, güven 0.39) kimliği 110 kare **yelek** olarak sürdü. V3: 111 → 160 (makine) |
| Sezgisel yorum | Zincirler 3→63→81 ve 6→67 (+66 çift) **2 fiziksel kişiyle tutarlı** — **GT yok; kimlik değişimi sayısı ölçülmedi**, yalnız aday listesi. İnsan doğrulaması için `new_track_start` kareleri pakette |
| Mükerrer incident riski | **KESİN (kod düzeyinde, birim testle belgelendi):** aynı kişi yeni kimlik alırsa PPEMonitor durumu sıfırlanır (MIN_OBS/MIN_SPAN yeniden), ihlal sürüyorsa **ikinci `start` + yeni incident_id**; eski incident `end` almadan STALE_SEC (10 sn) sonra `stale` kapanır (`test_id_fragmentation_creates_second_incident_for_same_person`). Bu veri setinde gerçekleşmedi (V1'de alarm yok; V3'te 111 hiç karar almadı) |
| Muhtemel kök neden | (1) Hızlı poz/kutu değişimi + VID_STRIDE 2 → IoU×skor eşik altı; (2) `CONF=0.25` → düşük skorlu ikinci eşleştirme aşaması kullanılamıyor (örtülmede 0.1–0.25 tespitlerle iz sürdürülemiyor); (3) model aynı kişiye ikinci kısmi kutu; (4) sınıftan bağımsız eşleştirme kimlikleri eşya izlerine kaptırıyor |
| Kesinlik | (2) ve (4) **KESİN** (kod/yapılandırma); (1) 3→63 için **YÜKSEK**, 63→81 ve 6→67 için **ORTA** (Kalman tahmini kaydedilmedi); etkisinin büyüklüğü **ölçülmedi** |
| Önerilen çözüm | **Takip:** yalnız Person sınıfını tracker'a vermek (eşyalar ham tespitten eşleşir — yeniden oynatmada V1–V3 alarmları değişmedi); tracker'a `conf=0.1` ile düşük skor aşamasını açıp kararı ≥0.25 kişi tespitiyle sınırlamak; `track_buffer`/`match_thresh` taraması; ReID'li izleyici (BoT-SORT) maliyetiyle. **Karar mantığı:** yeni kimlik, yakın zamanda kaybolmuş ihlalli bir izin konum/zaman komşusuysa incident'ı sürdürme (mükerrer bastırma) |
| Olası yan etkiler | Düşük skor aşaması yanlış kişi izlerini de uzatabilir (E-02 tipi nesneler); agresif eşleştirme iki farklı kişiyi birleştirebilir (ID merge) → bir kişinin ihlali diğerine yazılır; mükerrer bastırma iki ayrı gerçek ihlali tek incident'a indirebilir |
| Gerekli ek veri | Fiziksel kişi kimliği etiketli (track_id) GT; örtülme/kesişme sahneleri; uzun klipler (incident tekrarı) |
| Başarı ölçütü | GT'li sette kimlik değişimi ve parçalanma ↓ (`evaluate_against_gt` tracking bloğu), birleşme (merge) artmaz, fiziksel ihlal başına incident sayısı → 1 |
| Regresyon testi | Mükerrer incident mekanizma testi (beklenen davranış değişince güncellenir); `fragment_candidates`/`concurrent_duplicates` testleri; V1 aday listesi karşılaştırması |

### Çözüm sınıflandırması

| Sınıf | Öneriler | Hedef hata |
|---|---|---|
| Model (yalnız ayrı aday ağırlık) | koyu/örtülü/arkadan baret; iş makinesi sert negatifleri; yakın çekim kısmi gövde; `no_helmet` sınıf kalitesi | E-01, E-02, E-04(3) |
| Takip mekanizması | Person-only tracker; düşük skor aşaması; tampon/eşik taraması; ReID | E-04, E-02 (parçalanma) |
| PPE karar mantığı | üç durumlu baret; iz düzeyi güven kalıcılığı; eşyaya özgü kenar kapısı; mükerrer incident bastırma | E-01, E-02, E-03, E-04 |
| Değerlendirme kapsamı / kurulum | kamera yerleşim rehberi; hariç tutma bölgeleri; kapsam oranının raporlanması; GT ve görünürlük etiketi | E-03, E-02 |

## 4. İnsan doğrulaması (hazır, yapılmadı)

Paket: `FA/review_pack/` (Git dışı; görüntülerde kişiler var — yalnız yetkili inceleyen):

| Klasör / dosya | İçerik |
|---|---|
| `1_blind/R001…R031.jpg` | **Model çizimi olmayan** orijinal kareler; sabit tohumla karıştırılmış nötr kimlikler (alarm bilgisi yok) |
| `2_model_overlay/R0xx_model.jpg` | Aynı kareler, ayrı dosyada model kutuları (kişi durumu/güven/kapı nedeni, eşleşen baret/yelek, ham baret/yelek/no_helmet ince kutular) |
| `3_key_open_after_labeling/key.json` | Kimlik → video/zaman/seçim nedeni (etiketlemeden sonra açılır) |
| `review_sheet.csv` | Kare başına: kişi sayısı, baretsiz/yeleksiz çalışan (evet/hayır/**belirsiz**), insan-olmayan nesne notu, konum, inceleyen, tarih, overlay sonrası not |
| `review_template.json` | İki alarm (V2 pid 3 @4.7, V3 pid 160 @9.4) `label: null` — **doldurulmadıkça değerlendirme aracı reddeder** (birim testli); alarmlar UNREVIEWED kalır |
| `README.md` | Sıra: önce kör kareler → form → sonra çizimler → alarm etiketi yalnız insan tarafından |

Seçim (31 kare): alarm başlangıcı 2, öncesi 4, sonrası 4, ortası 1, bitişi 1, video sonunda açık 1; **alarm olmayan
kontrol aralıkları 12** (her 3 sn; V1 tamamı, V2 alarm dışı, V3 alarm öncesi) — kaçırılan gerçek ihlal olup
olmadığını görmek için; yeni kimlik başlangıcı 7 (E-04). Bu küçük örnek GT yerine geçmez; precision/recall
hesaplanmaz. **Hiçbir alarm FALSE_POSITIVE/TRUE_POSITIVE olarak etiketlenmedi.**

## 5. FAZ 10-A.5B kontrollü deney planı

**Değişmez kurallar:** `best.pt` (SHA `6886ad2f…`) taban çizgisi olarak sabit; üretim eşikleri/karar davranışı
değişmez — her deney ayrı yapılandırma/ayrı aday dosya (`candidate_<tarih>_<amaç>.pt`, asla `best.pt` üzerine yazılmaz);
sonuçlar `baseline_eval.py --previous` ve `failure_analysis.py` ile karşılaştırılır; üretime geçiş İSG onayıyla.

**Veri yönetimi (deneylerden ÖNCE):**
1. İzinli saha verisi (yazılı izin, KVKK aydınlatma, saklama süresi). **Stok videolar (V1–V3) ve SteelBench eğitime
   eklenmez**; V1–V3 yalnız tanı/regresyon için kalır.
2. Bölme **video/kamera/oturum** düzeyinde: `train` / `dev` (eşik ve model seçimi) / **dondurulmuş `test`**
   (yalnız son raporlama; seçim veya eşik ayarında kullanılmaz). Aynı kamera oturumu iki bölmeye girmez; yakın
   kopyalar (algısal hash) bölmeler arası denetlenir; bölme manifesti SHA-256 ile kayıtlı.
3. GT: `ppe-eval-video/1` — kişi kutusu + fiziksel kişi `track_id` + baret/yelek `present/absent/unknown` + görünürlük.
   İki bağımsız etiketleyici ve uyum ölçümü (ör. Cohen κ) bir alt kümede.

| # | Deney (taban: `best.pt` + üretim ayarı) | Katman | Hedef | Ana risk ölçümü |
|---|---|---|---|---|
| A1 | Üç durumlu baret kararı (`var/yok-kanıtı/belirsiz`) | karar | E-01 | gerçek baretsiz incident recall |
| A2 | İz düzeyi kişi güveni kalıcılığı (kayan medyan ≥ τ; τ dev setinde) | karar | E-02 | düşük güvenli gerçek çalışan kaçırma |
| A3 | Eşyaya özgü kenar kapısı (baret: üst kenar + baş; yelek: gövde) | karar | E-03 | A1 olmadan yanlış alarm artışı (yalnız A1 ile birlikte de koşulur) |
| A4 | Mükerrer incident bastırma (konum/zaman komşusu yeni kimlik) | karar | E-04 | ayrı ihlallerin birleşmesi |
| B1 | Yalnız Person izleme, eşyalar ham tespitle eşleşir | takip | E-04 | eşleştirme titreşimi, gecikme |
| B2 | Tracker'a `conf=0.1` (düşük skor aşaması), karar ≥0.25 | takip | E-04 | E-02 tipi sahte izlerin uzaması |
| B3 | `track_buffer` / `match_thresh` taraması; BoT-SORT+ReID | takip | E-04 | kimlik birleşmesi, GPU maliyeti (4 GB) |
| C1 | Aday ağırlık: koyu/örtülü baret + makine sert negatifleri + yakın çekim | model | E-01, E-02 | kişi/baret duyarlılığı genel düşüşü |

**Metrikler (dondurulmuş test, GT ile; hepsi Wilson %95 GA):** incident düzeyi precision ve **recall** (birincil
güvenlik koruması: kaçırılan gerçek ihlal), kamera-saat başına yanlış alarm, kişi-kare kapsamı (GT'de görünür
kişilerde), `belirsiz` oranı, kimlik değişimi/parçalanma/birleşme, fiziksel ihlal başına incident sayısı, gecikme
(p95) ve GPU belleği. **Kabul:** recall için önceden yazılı **non-inferiority** marjı (İSG ile belirlenir) + yanlış
alarmda anlamlı azalma; tek başına yanlış alarm düşüşü kabul ölçütü değildir. Her deney ayrı ayrı, sonra en iyi
kombinasyon (A1+A3 zorunlu birlikte) koşulur; çoklu karşılaştırma nedeniyle test seti yalnız son adayda açılır.

**Regresyon paketi:** dedektör birim testleri (275); `pipeline` ↔ `detector._process` eşdeğerliği; yeniden oynatmanın
taban olaylarını birebir vermesi; V1–V3 tanı koşusu (`collect --baseline`) ve `known_errors.json` sinyalleri; uçtan
uca teslim (`baseline_eval.py run`).

## 6. Testler

| Kalem | Sonuç |
|---|---|
| Yeni `detector/tests/test_evaluation_failure.py` (neden katmanları ×7, no_helmet ayrımı, kapıdan geçen sayım, sınıf geçmişi/iz özellikleri, kayıp türleri, parçalanma adayı, eşzamanlı çift, kapsam kırılımı, yeniden oynatma = üretim + sabitlerin geri yüklenmesi, kenar muafiyeti/ham eşya, mükerrer incident mekanizması, doldurulmamış inceleme şablonunun reddi) | **18/18 PASS** (ilk koşuda 1 test, sentetik ham listede yelek olmadığı için doğru biçimde yeleksiz alarm verdi; test verisi düzeltildi) |
| Dedektör tam paket | **275/275 PASS** (257 + 18) |
| Tanı koşusu ↔ FAZ 10-A.4 izi eşdeğerliği (gerçek GPU) | **IDENTICAL** (V1/V2/V3 tüm kareler + olaylar) |
| Yeniden oynatma ↔ üretim olayları | **birebir** (V1/V2/V3) |
| Gizli bilgi denetimi | **PASS** |
| Ana sistem izolasyonu | DB sayımları, snapshot listesi, outbox, korunan container durumları değişmedi |
| Dashboard testleri | **çalıştırılmadı** — dashboard kodu bu fazda değişmedi (FAZ 10-A.4: 338/338) |
| Uçtan uca (`ppe-eval`) | **çalıştırılmadı** — teslim zinciri değişmedi; bu faz yalnız çevrimdışı tanı |

## 7. Değişen dosyalar

Yeni: `detector/evaluation/failure.py`, `detector/tests/test_evaluation_failure.py`,
`tools/reliability/failure_analysis.py`, `tools/reliability/secret_audit.py`, bu rapor. Güncellenen:
`docs/RELIABILITY_TEST_PLAN.md` §11. Üretim kodu, model, eşikler, dashboard: **değişmedi**. Ham tanı izi, görüntüler,
inceleme paketi Git dışı (`tools/reliability/output/`).

## 8. Kalan belirsizlikler ve BLOCKED maddeler

| Madde | Durum |
|---|---|
| İki alarmın bağımsız insan incelemesi | **BLOCKED (insan)** — paket hazır; alarmlar UNREVIEWED |
| Ground-truth (kişi/baret/yelek/görünürlük/fiziksel kimlik) | **BLOCKED (veri)** — precision, recall, yanlış alarm oranı, gerçek kapsam, kimlik değişimi sayısı ölçülemez |
| V1'in gerçekte 2 kişi olduğu | doğrulanmadı (kullanıcı bildirimi + sezgisel zincir) |
| E-04 63→81 ve 6→67 mekanizması | Kalman tahmini kutusu kaydedilmedi → kesin değil (5B'de tracker iç durumu kancası) |
| E-01/E-02 model zayıflığının eğitim verisi kaynaklı olması | doğrulanmadı — eğitim verisi/etiketleri bu makinede yok |
| `no_helmet` sınıfının işe yararlılığı | üç videoda 0 tespit; sahada ölçülmeden karar kanıtı olarak kullanılamaz |
| Stok video lisansı, SteelBench kullanım izni | doğrulanmadı (FAZ 10-A.4'ten devam) — eğitime eklenmez |
| Mac ham kanıtı (`20261102T124324Z_capture_accuracy`) | yok (FAZ 10-A.4'ten devam) |
| Değerlendirme süreç ortamında üretim token'ı | kalan risk (§1.1); üretim kodu değişikliği önerisi |
