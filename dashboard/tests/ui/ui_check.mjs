// Gerçek tarayıcı (Microsoft Edge / Chromium, headless) ile dashboard UI testi. Bağımlılık yok: Node 22 yerleşik
// WebSocket + fetch ile Chrome DevTools Protocol. Önce izole fixture'ı başlatın (tests/ui/serve_fixture.py).
//   node dashboard/tests/ui/ui_check.mjs <ekran-görüntüsü-klasörü> [tarayıcı-yolu]
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const OUT = process.argv[2] || "ui-shots";
const BROWSER = process.argv[3] || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const FULL = "http://127.0.0.1:8001", BAD = "http://127.0.0.1:8002", EMPTY = "http://127.0.0.1:8003";
const PORT = 9333;
mkdirSync(OUT, { recursive: true });
const results = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---------------------------------------------------------------- CDP
const browser = spawn(BROWSER, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`, "--no-first-run",
  "--no-default-browser-check", `--user-data-dir=${mkdtempSync(join(tmpdir(), "ppe-ui-"))}`, "about:blank"], { stdio: "ignore" });
let target;
for (let i = 0; i < 60 && !target; i++) {
  try { target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json(); }
  catch { await sleep(250); }
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r, { once: true }));
let seq = 0;
const pending = new Map();
ws.addEventListener("message", e => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
});
const cdp = (method, params = {}) => new Promise((res, rej) => {
  const id = ++seq;
  pending.set(id, m => m.error ? rej(new Error(`${method}: ${m.error.message}`)) : res(m.result));
  ws.send(JSON.stringify({ id, method, params }));
});
async function ev(expr) {
  const r = await cdp("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}
async function waitFor(expr, timeout = 8000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    try { if (await ev(`return !!(${expr});`)) return true; } catch { /* sayfa yükleniyor */ }
    await sleep(100);
  }
  throw new Error(`zaman aşımı: ${expr}`);
}
async function viewport(width, height, mobile = false) {
  await cdp("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
}
async function go(url) {                     // her zaman tam sayfa yüklemesi (aynı sayfada yalnızca hash değişmesin)
  await cdp("Page.navigate", { url: "about:blank" });
  await sleep(100);
  await cdp("Page.navigate", { url });
  await sleep(150);
  await waitFor(`document.readyState === "complete" && document.getElementById("view-live")`);
}
async function shot(name) {
  const { data } = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  writeFileSync(join(OUT, `${name}.png`), Buffer.from(data, "base64"));
}
async function check(name, fn) {
  try { const detail = await fn(); results.push(["PASS", name, detail || ""]); }
  catch (e) { results.push(["FAIL", name, String(e.message || e).slice(0, 300)]); }
}
const assert = (c, msg) => { if (!c) throw new Error(msg); };
const rows = id => ev(`return document.querySelectorAll("#${id} tbody tr").length;`);
const text = sel => ev(`return document.querySelector(${JSON.stringify(sel)})?.textContent ?? null;`);
async function submit(formId, values) {
  await ev(`const f = document.getElementById("${formId}");
    for (const [k, v] of Object.entries(${JSON.stringify(values)})) f.elements[k].value = v;
    f.requestSubmit(); return true;`);
  await sleep(150);
}
async function idle(statusId) {             // yükleme başlamış VE bitmiş olmalı (boş durum satırı = henüz başlamadı)
  await waitFor(`(t => t && !t.startsWith("Yükleniyor"))(document.getElementById("${statusId}").textContent)`);
}
async function clickAt(selector) {
  const box = await ev(`const el = document.querySelector(${JSON.stringify(selector)});
    el.scrollIntoView({ block: "center" }); const r = el.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };`);
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"])
    await cdp("Input.dispatchMouseEvent", { type, x: box.x, y: box.y, button: "left", clickCount: 1 });
}

await cdp("Page.enable");
await cdp("Runtime.enable");
const api = async (base, path) => (await fetch(base + path)).json();
const summary = await api(FULL, "/api/history/summary");

// ================================================================ masaüstü
await viewport(1400, 1000);
await go(FULL + "/");
await check("Canlı İzleme: varsayılan sekme, diğer ekranlar gizli", async () => {
  const s = await ev(`return { sel: document.getElementById("tab-live").getAttribute("aria-selected"),
    live: !document.getElementById("view-live").hidden, h: document.getElementById("view-history").hidden,
    st: document.getElementById("view-stats").hidden, r: document.getElementById("view-runs").hidden };`);
  assert(s.sel === "true" && s.live && s.h && s.st && s.r, JSON.stringify(s));
});
await check("Canlı İzleme: WebSocket ile gelen kare tarayıcıda görüntüleniyor (binary protokol)", async () => {
  const inc = (await api(FULL, "/api/history/incidents?status=closed&limit=200")).items.find(i => i.snapshot);
  const jpg = new Uint8Array(await (await fetch(FULL + inc.snapshot)).arrayBuffer());
  // gerçek dedektör gibi akış: karo /api/state ile oluşana kadar gelen kareler atlanır, sonrakiler çizilir
  let shown = false;
  for (let i = 0; i < 15 && !shown; i++) {
    const fd = new FormData();
    fd.append("frame", new Blob([jpg], { type: "image/jpeg" }), "f.jpg");
    fd.append("payload", JSON.stringify({ cam: "cam9", name: "Canlı test", meta: { status: "running", width: 8, height: 8,
      proc_fps: 1, source: "videos/x.mp4" }, summary: { t: i, people: 0, ok: 0, viol: 0, analyzing: 0 }, people: [] }));
    assert((await fetch(FULL + "/ingest/frame", { method: "POST", body: fd })).ok, "kare gönderilemedi");
    await sleep(300);
    shown = await ev(`return [...document.querySelectorAll("#grid .stage img")].some(i => i.src.startsWith("blob:") && i.naturalWidth > 0);`);
  }
  assert(shown, "kare tarayıcıda çizilmedi");
  await shot("01_canli_masaustu");
  return "blob JPEG çizildi";
});

await ev(`document.getElementById("tab-history").click(); return true;`);
await idle("hStatus");
await check("İhlal Geçmişi: ilk sayfa 50 satır + toplam", async () => {
  const n = await rows("hTable"), s = await text("#hStatus");
  assert(n === 50 && s.includes(`${summary.incidents.incidents} ihlal`), `${n} satır, '${s}'`);
  return s;
});
await check("İhlal Geçmişi: 'Daha fazla yükle' kalan satırları getiriyor", async () => {
  await ev(`document.querySelector("#hMore button").click(); return true;`);
  await waitFor(`document.querySelectorAll("#hTable tbody tr").length === ${summary.incidents.incidents}`);
  assert(await ev(`return document.getElementById("hMore").hidden;`), "düğme gizlenmedi");
  return `${summary.incidents.incidents} satır`;
});
await check("İhlal Geçmişi: 6 durumun hepsi etiketli pill olarak görünüyor", async () => {
  const pills = await ev(`return [...new Set([...document.querySelectorAll("#hTable .pill")].map(p => p.className.split(" ")[1]))].sort();`);
  assert(JSON.stringify(pills) === JSON.stringify(["active", "closed", "orphan", "run_end", "stale", "unknown"]), pills);
});
await check("Kamera adı: eski kayıt doğrulanamadı olarak, güncel ad geçmişe yazılmadan", async () => {
  const t = await ev(`return [...document.querySelectorAll("#hTable tbody tr td:nth-child(2)")].map(td => td.textContent);`);
  assert(t.some(x => x.startsWith("Eski kayıt: kaynak doğrulanamadı") && x.includes("cam3")), "eski kayıt etiketi yok");
  assert(!t.some(x => x.includes("Bugün başka video")), "güncel ad geçmiş kayıtta gösterildi");
  assert(t.some(x => x.startsWith("Giriş kapısı")), "run etiketi yok");
});
await check("XSS: kamera adındaki HTML metin olarak gösteriliyor, çalışmıyor", async () => {
  const r = await ev(`return { flag: window.__xss === 1, img: [...document.images].some(i => i.getAttribute("src") === "x"),
    shown: [...document.querySelectorAll("#hTable td")].some(td => td.textContent.includes('<img src=x onerror=')) };`);
  assert(!r.flag && !r.img && r.shown, JSON.stringify(r));
});
await shot("02_gecmis_masaustu");
await check("Filtre: durum = Sürüyor", async () => {
  await submit("hForm", { status: "active" }); await idle("hStatus");
  const p = await ev(`return [...document.querySelectorAll("#hTable .pill")].map(p => p.textContent);`);
  assert(p.length === summary.by_status.active && p.every(x => x === "Sürüyor"), p);
});
await check("Filtre: ekipman = ikisi birden", async () => {
  await submit("hForm", { status: "", equipment: "both" }); await idle("hStatus");
  const s = await text("#hStatus");
  assert(s.startsWith(`${summary.incidents.both} ihlal`), s);
});
await check("Filtre: kamera seçenekleri DB'den, kamera filtresi", async () => {
  const opts = await ev(`return [...document.querySelector("#hForm select[name=camera_id]").options].map(o => o.value);`);
  assert(["", "cam1", "cam2", "cam3"].every(v => opts.includes(v)), opts);
  await submit("hForm", { equipment: "", camera_id: "cam2" }); await idle("hStatus");
  const c = await ev(`return [...document.querySelectorAll("#hTable tbody tr .why")].filter(x => /^cam/.test(x.textContent)).map(x => x.textContent.split(" ")[0]);`);
  assert(c.length > 0 && c.every(x => x === "cam2"), c);
});
await check("Filtre: veri olmayan tarih aralığı -> boş durum mesajı", async () => {
  await submit("hForm", { camera_id: "", from: "2020-01-01", to: "2020-01-02" }); await idle("hStatus");
  assert((await text("#hStatus")) === "Bu filtrelere uyan ihlal kaydı yok." && (await rows("hTable")) === 0, await text("#hStatus"));
});
await ev(`document.getElementById("hReset").click(); return true;`);
await idle("hStatus");
await check("Detay penceresi: snapshot, kamera, durum ve sıralı zaman çizelgesi", async () => {
  await ev(`const tr = [...document.querySelectorAll("#hTable tbody tr")].find(r => r.querySelector("img.thumb"));
    tr.querySelector("button").click(); return true;`);
  await waitFor(`document.getElementById("detail").open && document.querySelector("#detailBody .detail-top img")`);
  await waitFor(`document.querySelector("#detailBody .detail-top img").naturalWidth > 0`);
  const d = await ev(`const tl = [...document.querySelectorAll("#detailBody tbody tr")].map(r => [...r.cells].map(c => c.textContent));
    return { types: tl.map(r => r[1]), times: tl.map(r => r[0]), body: document.getElementById("detailBody").textContent };`);
  assert(JSON.stringify(d.types) === JSON.stringify(["Başladı", "Sürüyor", "Bitti"]), d.types);
  const ts = d.times.map(t => { const [dd, mm, yy, h, mi, s] = t.match(/\d+/g).map(Number); return Date.UTC(yy, mm - 1, dd, h, mi, s); });
  assert(ts.every((t, i) => i === 0 || t >= ts[i - 1]), d.times);
  assert(d.body.includes("Giriş kapısı") && d.body.includes("Kapandı") && d.body.includes("cam1"), "detay alanları eksik");
  assert(!/null|undefined/.test(d.body), "detayda 'null'/'undefined' metni var");
  await shot("03_detay_masaustu");
  await ev(`document.getElementById("detailClose").click(); return true;`);
  assert(!(await ev(`return document.getElementById("detail").open;`)), "pencere kapanmadı");
});

await ev(`document.getElementById("tab-stats").click(); return true;`);
await idle("sStatus");
await check("İstatistikler: kutucuklar API özetiyle aynı", async () => {
  const s = await api(FULL, `/api/history/summary?${new URLSearchParams(await ev(`
    const f = document.getElementById("sForm"); return { from: dayStartISO(f.elements.from.value), to: dayStartISO(f.elements.to.value, 1), tz: TZ };`))}`);
  const tiles = await ev(`return Object.fromEntries([...document.querySelectorAll("#sTiles div")].map(d => [d.querySelector("span").textContent, d.querySelector("b").textContent]));`);
  const want = { "Doğrulanmış ihlal": s.incidents.verified, "Baret eksik": s.incidents.helmet, "Yelek eksik": s.incidents.vest,
    "İkisi birden": s.incidents.both, "Şu an süren": s.by_status.active, "Belirsiz / başlangıçsız": s.by_status.unknown + s.by_status.orphan,
    "Kapanan": s.by_status.closed, "Ham olay kaydı": s.events.total };
  for (const [k, v] of Object.entries(want)) assert(tiles[k] === v.toLocaleString("tr-TR"), `${k}: ${tiles[k]} != ${v}`);
  return JSON.stringify(tiles);
});
await check("İstatistikler: günlük grafik çubukları ve hover ipucu", async () => {
  const n = await ev(`return document.querySelectorAll("#sChart path.bar[d^=M]").length;`);
  assert(n >= 1, "çubuk yok");
  const sel = `#sChart rect.hit:nth-of-type(${await ev(`return document.querySelectorAll("#sChart rect.hit").length;`)})`;
  await clickAt(sel);
  try { await waitFor(`!document.querySelector("#sChart .tip").hidden`, 3000); }
  catch (e) {
    throw new Error("ipucu açılmadı; tıklanan nokta: " + await ev(`const el = document.querySelector(${JSON.stringify(sel)});
      const r = el.getBoundingClientRect(); const at = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return (at ? at.tagName + "." + at.getAttribute("class") : "yok") + " @" + Math.round(r.x) + "," + Math.round(r.y) + " " + Math.round(r.width) + "x" + Math.round(r.height);`));
  }
  const tip = await text("#sChart .tip");
  const inside = await ev(`const c = document.getElementById("sChart").getBoundingClientRect(),
    t = document.querySelector("#sChart .tip").getBoundingClientRect(); return t.left >= c.left && t.right <= c.right;`);
  assert(inside, "ipucu grafik kutusundan taşıyor");
  assert(/ihlal \(baret \d+, yelek \d+\)/.test(tip), tip);
  await shot("04_istatistik_masaustu");
  return tip;
});

await ev(`document.getElementById("tab-runs").click(); return true;`);
await idle("rStatus");
await check("Kamera / Run Raporları: kamera ve run tabloları", async () => {
  const c = await rows("rCams"), r = await rows("rTable");
  assert(c === 4 && r === 3, `${c} kamera, ${r} run`);  // cam9: run kimliksiz canlı test karesi (run oluşturmaz)
});
await check("Run detayı -> 'Bu run'ın ihlallerini listele' geçmişi run'a göre filtreliyor", async () => {
  await ev(`const tr = [...document.querySelectorAll("#rTable tbody tr")].find(r => r.textContent.includes("Bitti"));
    tr.querySelector("button").click(); return true;`);
  await waitFor(`document.getElementById("detail").open && document.querySelector("#detailBody table")`);
  const body = await text("#detailBody");
  assert(body.includes("Kişi (iz) raporu") && body.includes("Kişi bir süre görülmedi"), "run detayı eksik");
  await shot("05_run_detay_masaustu");
  await ev(`[...document.querySelectorAll("#detailBody button")].find(b => b.textContent.includes("ihlallerini")).click(); return true;`);
  await waitFor(`!document.getElementById("view-history").hidden && !document.getElementById("hRunChip").hidden`);
  await idle("hStatus");
  const s = await text("#hStatus");
  assert(s.startsWith("63 ihlal"), s);
  return s;
});

// ================================================================ 390 px mobil
await viewport(390, 844, true);
for (const [tab, idx] of [["live", "06"], ["history", "07"], ["stats", "08"], ["runs", "09"]]) {
  await check(`390px: ${tab} sekmesinde yatay taşma yok`, async () => {
    await go(`${FULL}/#${tab}`);
    if (tab !== "live") await idle({ history: "hStatus", stats: "sStatus", runs: "rStatus" }[tab]);
    const w = await ev(`return { sw: document.documentElement.scrollWidth, iw: window.innerWidth };`);
    await shot(`${idx}_${tab}_390`);
    assert(w.sw <= w.iw, JSON.stringify(w));
    return JSON.stringify(w);
  });
}
await check("390px: detay penceresi ekrana sığıyor", async () => {
  await go(`${FULL}/#history`); await idle("hStatus");
  await ev(`document.querySelector("#hTable tbody tr button").click(); return true;`);
  await waitFor(`document.getElementById("detail").open && document.querySelector("#detailBody dl")`);
  const r = await ev(`const b = document.getElementById("detail").getBoundingClientRect();
    const over = [...document.querySelectorAll("#detailBody dd")].some(dd => dd.getBoundingClientRect().right > b.right + 1);
    return { l: b.left, r: b.right, w: innerWidth, over, nul: /null/.test(document.getElementById("detailBody").textContent) };`);
  assert(!r.over && !r.nul, "değer taşması veya null metni: " + JSON.stringify(r));
  await shot("10_detay_390");
  assert(r.l >= 0 && r.r <= r.w, JSON.stringify(r));
});

// ================================================================ boş veri ve DB hatası
await viewport(1400, 1000);
await check("Boş veritabanı: anlaşılır boş durum mesajları", async () => {
  await go(`${EMPTY}/#history`); await idle("hStatus");
  const h = await text("#hStatus");
  await go(`${EMPTY}/#stats`); await idle("sStatus");
  const c = await text("#sChart");
  await go(`${EMPTY}/#runs`); await idle("rStatus");
  const r = await text("#rStatus"), cams = await text("#rCams");
  await shot("11_bos_runs");
  assert(h === "Bu filtrelere uyan ihlal kaydı yok." && c === "Bu aralıkta ihlal kaydı yok." &&
    r === "Bu filtrelere uyan run yok." && cams === "Henüz kayıtlı kamera yok.", JSON.stringify({ h, c, r, cams }));
});
await check("DB erişilemez (503): her ekranda hata mesajı, canlı sekme çalışıyor", async () => {
  const msgs = [];
  for (const [tab, id] of [["history", "hStatus"], ["stats", "sStatus"], ["runs", "rStatus"]]) {
    await go(`${BAD}/#${tab}`); await idle(id);
    msgs.push(await ev(`const s = document.getElementById("${id}"); return s.classList.contains("err") ? s.textContent : "";`));
  }
  await shot("12_db_503");
  assert(msgs.every(m => m.includes("Veritabanına şu an ulaşılamıyor")), msgs);
  await ev(`document.getElementById("tab-live").click(); return true;`);
  await waitFor(`document.getElementById("connText").textContent.includes("Dedektör bağlı değil")`);
});

// ---------------------------------------------------------------- rapor
ws.close();
browser.kill();
for (const [s, n, d] of results) console.log(`[${s}] ${n}${d ? " — " + d : ""}`);
const failed = results.filter(r => r[0] === "FAIL").length;
console.log(`SONUÇ: ${results.length - failed}/${results.length} PASS`);
process.exit(failed ? 1 : 0);
