"""FAZ 10-A.7 gerçek tarayıcı UI kontrolü (Chrome headless + DevTools Protocol). Önce izole canlı fixture'ı başlatın
(tests/ui/serve_live_fixture.py). Bağımlılık: websocket-client. Ekran görüntüleri kişi görüntüsü içermez (test JPEG).

    UI_PASSWORD_FILE=<dosya> python dashboard/tests/ui/ui_check_10a7.py <çıktı-klasörü>

Ortam: UI_BASE (varsayılan http://127.0.0.1:18011), CHROME (tarayıcı yolu). Sonuç: <çıktı>/results.json; hata varsa
çıkış kodu 1."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

BASE = os.environ.get("UI_BASE", "http://127.0.0.1:18011")
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
OUT = sys.argv[1] if len(sys.argv) > 1 else "ui-10a7"
PASSWORD = open(os.environ["UI_PASSWORD_FILE"], encoding="utf-8").read().strip()
PORT = 9339
WIDTHS = (360, 390, 640, 768, 1000, 1280, 1920)
SCORE_KEYS = ("helmet_rate", "vest_rate")
os.makedirs(OUT, exist_ok=True)
RESULTS = []

# Sayfa betiklerinden önce: WebSocket kurulumlarını ve fetch URL'lerini say (CSP bu enjeksiyonu etkilemez).
PROBE = """
window.__ws = 0; window.__fetch = [];
const _W = window.WebSocket;
window.WebSocket = function (...a) { window.__ws++; return new _W(...a); };
window.WebSocket.prototype = _W.prototype;
const _F = window.fetch;
window.fetch = function (u, o) { window.__fetch.push(String(u)); return _F.call(this, u, o); };
"""


class CDP:
    def __init__(self):
        self.proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", f"--remote-debugging-port={PORT}",
                                      "--no-first-run", "--no-default-browser-check",
                                      f"--user-data-dir={tempfile.mkdtemp(prefix='ppe-ui-')}", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        target = None
        for _ in range(80):
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/new?about:blank", method="PUT")
                target = json.load(urllib.request.urlopen(req, timeout=1))
                break
            except OSError:
                time.sleep(0.25)
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
        self.seq = 0
        self.send("Page.enable")
        self.send("Page.addScriptToEvaluateOnNewDocument", source=PROBE)

    def send(self, method, **params):
        self.seq += 1
        me = self.seq
        self.ws.send(json.dumps({"id": me, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == me:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    def ev(self, js):
        r = self.send("Runtime.evaluate", expression=f"(async () => {{ {js} }})()", awaitPromise=True,
                      returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", r["exceptionDetails"]))
        return r["result"].get("value")

    def wait(self, expr, timeout=10.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self.ev(f"return !!({expr});"):
                    return True
            except RuntimeError:
                pass
            time.sleep(0.1)
        raise TimeoutError(expr)

    def viewport(self, w, h=900):
        self.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 700)

    def go(self, url):
        self.send("Page.navigate", url=url)
        time.sleep(0.3)
        self.wait('document.readyState === "complete" && document.getElementById("view-live")')

    def shot(self, name):
        data = self.send("Page.captureScreenshot", format="png", captureBeyondViewport=False)["data"]
        with open(os.path.join(OUT, f"{name}.png"), "wb") as f:
            f.write(base64.b64decode(data))

    def close(self):
        try:
            self.ws.close()
        finally:
            self.proc.terminate()


def check(name, ok, detail=None):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" :: {detail}"), flush=True)


def login(b, user):
    b.go(BASE + "/")
    b.wait('!document.getElementById("loginView").hidden || !document.getElementById("appTabs").hidden')
    if b.ev('return !document.getElementById("appTabs").hidden;'):
        logout(b)
    b.ev(f"""const f = document.getElementById("loginForm");
             f.elements.username.value = {json.dumps(user)}; f.elements.password.value = {json.dumps(PASSWORD)};
             f.requestSubmit(); return true;""")
    b.wait('!document.getElementById("appTabs").hidden')


def logout(b):
    b.ev('document.getElementById("logout").click(); return true;')
    b.wait('!document.getElementById("loginView").hidden')


TILES_JS = """return [...document.querySelectorAll("#grid .stage")].map(t => {
  const r = t.getBoundingClientRect(), i = t.querySelector("img").getBoundingClientRect();
  return { name: t.querySelector(".cam-name").textContent, x: r.left, y: r.top, w: r.width, h: r.height,
           iw: i.width, ih: i.height, wide: t.classList.contains("wide") };
});"""


def visual_order(tiles):
    return [t["name"] for t in sorted(tiles, key=lambda t: (round(t["y"]), round(t["x"])))]


def overlap(tiles):
    for i, a in enumerate(tiles):
        for b in tiles[i + 1:]:
            if a["x"] < b["x"] + b["w"] - 1 and b["x"] < a["x"] + a["w"] - 1 and \
               a["y"] < b["y"] + b["h"] - 1 and b["y"] < a["y"] + a["h"] - 1:
                return (a["name"], b["name"])
    return None


def no_hscroll(b):
    return b.ev("return document.documentElement.scrollWidth <= window.innerWidth + 1;")


def layout_checks(b):
    b.wait('document.querySelectorAll("#grid .stage").length === 4 && typeof WS !== "undefined" && WS && WS.readyState === 1')
    base_order = None
    for w in WIDTHS:
        b.viewport(w)
        time.sleep(0.5)
        tiles = b.ev(TILES_JS)
        order = visual_order(tiles)
        base_order = base_order or order
        cols = len({round(t["x"]) for t in tiles})
        check(f"layout {w}px: yatay taşma yok", no_hscroll(b), b.ev("return document.documentElement.scrollWidth;"))
        check(f"layout {w}px: kartlar üst üste binmiyor", overlap(tiles) is None, overlap(tiles))
        check(f"layout {w}px: kart sırası tutarlı", order == base_order, order)
        check(f"layout {w}px: görüntü 16:9 korunuyor", all(abs(t["iw"] / t["ih"] - 16 / 9) < 0.03 for t in tiles),
              [round(t["iw"] / t["ih"], 3) for t in tiles])
        check(f"layout {w}px: sütun sayısı uygun", cols == 1 if w <= 640 else cols >= 2, cols)
        feed_below = b.ev('return document.querySelector("aside").getBoundingClientRect().top >= '
                          'document.querySelector("#view-live section").getBoundingClientRect().bottom - 1;')
        check(f"layout {w}px: uyarı paneli yerleşimi", feed_below == (w <= 1000), feed_below)
        b.shot(f"live_{w}_normal")

        ws_before = b.ev("return window.__ws;")
        n0 = b.ev('return window.__fetch.filter(u => u.startsWith("/api/state")).length;')
        b.ev('document.querySelectorAll("#grid .stage")[2].querySelector(".exp").click(); return true;')
        time.sleep(0.6)
        tiles = b.ev(TILES_JS)
        wide = [t for t in tiles if t["wide"]]
        grid_w = b.ev('return document.getElementById("grid").getBoundingClientRect().width;')
        check(f"expand {w}px: tek kamera büyüdü ve ilk sırada", len(wide) == 1 and visual_order(tiles)[0] == wide[0]["name"],
              visual_order(tiles))
        check(f"expand {w}px: büyüyen kart tam genişlik", abs(wide[0]["w"] - grid_w) < 2, (wide[0]["w"], grid_w))
        rest = [n for n in visual_order(tiles) if n != wide[0]["name"]]
        check(f"expand {w}px: diğer kartların sırası korunuyor", rest == [n for n in base_order if n != wide[0]["name"]],
              rest)
        check(f"expand {w}px: üst üste binme / yatay taşma yok", overlap(tiles) is None and no_hscroll(b),
              overlap(tiles))
        check(f"expand {w}px: görüntü viewport'a sığıyor", wide[0]["ih"] <= 0.75 * 900 + 1 or w < 700,
              wide[0]["ih"])
        b.shot(f"live_{w}_expanded")
        b.ev('document.querySelectorAll("#grid .stage")[1].querySelector(".exp").click(); return true;')
        time.sleep(0.3)
        check(f"expand {w}px: başka kamera seçilince yine tek büyük kart",
              b.ev('return document.querySelectorAll("#grid .stage.wide").length;') == 1)
        b.ev('document.querySelector("#grid .stage.wide .exp").click(); return true;')
        time.sleep(0.4)
        tiles = b.ev(TILES_JS)
        check(f"collapse {w}px: yerleşim eski hâline döndü", not any(t["wide"] for t in tiles)
              and visual_order(tiles) == base_order, visual_order(tiles))
        n1 = b.ev('return window.__fetch.filter(u => u.startsWith("/api/state")).length;')
        check(f"expand {w}px: WebSocket yeniden kurulmadı, yoklama sürüyor",
              b.ev("return window.__ws;") == ws_before == 1 and b.ev("return WS.readyState;") == 1 and n1 > n0,
              (ws_before, b.ev("return window.__ws;"), n0, n1))

    b.viewport(1280)
    b.ev('document.querySelectorAll("#grid .stage")[3].querySelector(".exp").click(); return true;')
    time.sleep(0.3)
    b.go(BASE + "/")
    b.wait('document.querySelectorAll("#grid .stage").length === 4')
    time.sleep(0.5)
    check("expand: seçim sayfa yenilemesinde korunuyor",
          b.ev('return document.querySelector("#grid .stage.wide .cam-name")?.textContent;') == "Kule vinci")
    b.ev('document.querySelector("#grid .stage.wide .exp").click(); return true;')
    long_ok = b.ev("""const n = [...document.querySelectorAll("#grid .cam-name")].find(x => x.textContent.startsWith("Kuzey"));
                      return n.scrollWidth > n.clientWidth && n.title === n.textContent
                             && !n.querySelector("b") && n.closest(".stage").getBoundingClientRect().width
                                <= document.getElementById("grid").getBoundingClientRect().width + 1;""")
    check("uzun kamera adı kartı bozmuyor (kısaltılıyor, HTML yorumlanmıyor)", long_ok)
    keyboard = b.ev("""const e = document.querySelectorAll("#grid .stage .exp")[0];
                       return e.tagName === "BUTTON" && e.getAttribute("aria-pressed") === "false";""")
    check("büyüt düğmesi klavyeyle erişilebilir (button + aria-pressed)", keyboard)


def header_checks(b):
    h1 = b.ev('return document.querySelector("header h1").textContent;')
    check("Saha KKD başlığı: Baret/Yelek yazısı yok", h1 == "Saha KKD İzleme"
          and "baret" not in b.ev('return document.querySelector("header").textContent.toLowerCase();')
          and "yelek" not in b.ev('return document.querySelector("header").textContent.toLowerCase();'), h1)


def key(b, k, code, vk):
    for t in ("keyDown", "keyUp"):
        b.send("Input.dispatchKeyEvent", type=t, key=k, code=code, windowsVirtualKeyCode=vk)


CARDS_JS = """return [...document.querySelectorAll("#feed li")].map(li => ({
  cam: li.querySelector(".al-cam").textContent, closed: li.classList.contains("closed"),
  inc: [...openAlerts].find(([, c]) => c.li === li)?.[0], img: li.querySelector(".al-thumb img")?.getAttribute("src"),
  ph: li.querySelector(".al-ph")?.textContent || null, text: li.textContent }));"""


def alert_checks(b):
    """FAZ 01: teknik kart alanları (iz, incident, run) ve run raporu düğmesi yalnız admin'dedir (FAZ 10-A.7 rol
    görünürlüğü); bu kontroller admin oturumunda çalışır. Admin olmayan görünüm: alert_checks_user."""
    b.viewport(1280)
    b.go(BASE + "/")
    b.wait('document.querySelectorAll("#feed li").length >= 3')
    time.sleep(1.0)
    alerts = b.ev('return await (await fetch("/api/alerts")).json();')
    cards = b.ev(CARDS_JS)
    starts = {a["incident_id"]: a.get("snapshot") for a in alerts if a["type"] == "start"}
    check("uyarı: incident başına tek kart", len(cards) == len({a["incident_id"] for a in alerts}),
          (len(cards), len(alerts)))
    check("uyarı: her kartın fotoğrafı kendi incident'ının başlangıç snapshot'ı",
          all(c["img"] == starts.get(c["inc"]) for c in cards if c["img"]) and any(c["img"] for c in cards),
          [(c["inc"][:8], c["img"]) for c in cards])
    by_cam = {c["cam"]: c for c in cards}
    check("uyarı: süren ihlal 'Sürüyor' ve son bildirim satırı", "Sürüyor" in by_cam["Giriş kapısı"]["text"]
          and "Son bildirim" in by_cam["Giriş kapısı"]["text"] and not by_cam["Giriş kapısı"]["closed"])
    long = [c for c in cards if c["cam"].startswith("Kuzey")][0]
    check("uyarı: biten ihlal aynı kartta 'Bitti' (yeni kart yok)", long["closed"] and "Bitti" in long["text"])
    check("uyarı: kart alanları (zaman, incident, run, iz, ihlal, baret, yelek)",
          all(k in by_cam["Giriş kapısı"]["text"] for k in ("Incident", "Run", "Geçici iz", "İhlal", "Baret", "Yelek")))
    check("uyarı: kamera adındaki HTML yorumlanmıyor", b.ev('return !document.querySelector("#feed b");'))
    n = b.ev("""const a = (await (await fetch("/api/alerts")).json())[0], f = document.getElementById("feed");
                const before = f.children.length; addAlert(a, false); addAlert(a, false);
                return [before, f.children.length];""")
    check("uyarı: aynı olay iki kez kart olmuyor", n[0] == n[1], n)
    ph = b.ev("""addAlert({ type: "start", event_id: crypto.randomUUID(), incident_id: crypto.randomUUID(),
                           run_id: crypto.randomUUID(), cam: "cam_x", cam_name: "Sentetik", pid: 99, t: 1, wall: "00:00:00",
                           missing: "Baretsiz", missing_helmet: true, missing_vest: false }, false);
                 return document.querySelector("#feed li .al-ph")?.textContent;""")
    check("uyarı: snapshot yoksa kontrollü boş durum", ph == "Görüntü kaydedilmedi", ph)
    bad = b.ev("""addAlert({ type: "start", event_id: crypto.randomUUID(), incident_id: crypto.randomUUID(),
                            run_id: crypto.randomUUID(), cam: "cam_y", cam_name: "Sentetik2", pid: 98, t: 1, wall: "00:00:01",
                            missing: "Baretsiz", missing_helmet: true, missing_vest: false,
                            snapshot: "/snapshots/alert_00000000-0000-0000-0000-000000000000.jpg" }, false);
                  await new Promise(r => setTimeout(r, 800));
                  return document.querySelector("#feed li .al-ph")?.textContent;""")
    check("uyarı: yüklenemeyen fotoğraf kartı bozmuyor", bad == "Görüntü yüklenemedi", bad)

    b.ev('const t = [...document.querySelectorAll("#feed li")].find(li => li.querySelector(".al-thumb"))'
         '.querySelector(".al-thumb"); t.focus(); t.click(); return true;')      # klavye/fare kullanıcısı gibi odaklı
    b.wait('document.getElementById("dlg").open')
    src_ok = b.ev("""const li = [...document.querySelectorAll("#feed li")].find(li => li.querySelector(".al-thumb"));
                     return document.getElementById("dlgImg").getAttribute("src") === li.querySelector("img").getAttribute("src");""")
    check("fotoğraf: tıklanınca aynı görüntü büyük açılıyor", src_ok)
    key(b, "Escape", "Escape", 27)
    time.sleep(0.3)
    check("fotoğraf: Escape ile kapanıyor ve odak düğmeye dönüyor", b.ev(
        'return !document.getElementById("dlg").open && document.activeElement.classList.contains("al-thumb");'))

    a_inc = by_cam["Giriş kapısı"]["inc"]
    run_id = [a for a in alerts if a["incident_id"] == a_inc][0]["run_id"]
    b.ev("window.__fetch.length = 0; return true;")
    b.ev(f"""const li = openAlerts.get({json.dumps(a_inc)}).li;
             const btn = [...li.querySelectorAll("button")].find(x => x.textContent.startsWith("Run raporu"));
             btn.click(); btn.click(); return true;""")
    b.wait('document.getElementById("detail").open && document.getElementById("detailTitle").textContent === "Run raporu"'
           ' && !document.getElementById("detailBody").textContent.includes("Yükleniyor")')
    body = b.ev('return document.getElementById("detailBody").textContent;')
    hist = b.ev(f'return await (await fetch("/api/history/runs/{run_id}")).json();')
    check("run raporu: canlı uyarıdan doğru run açılıyor (run geçmişiyle aynı veri)",
          hist["camera_id"] in body and hist["camera_id"] == "cam_a", hist.get("camera_id"))
    check("run raporu: devam eden run açıkça işaretli", hist["status"] == "running" and "devam ediyor" in body,
          hist["status"])
    calls = b.ev(f'return window.__fetch.filter(u => u.includes("/api/history/runs/{run_id}")).length;')
    check("run raporu: tekrarlanan tıklamada tek istek", calls == 2, calls)    # 1 arayüz + 1 bu testin kendi isteği
    b.shot("alert_run_report")
    b.ev("WS.close(); return true;")
    time.sleep(1.5)
    check("run raporu: canlı yayın kopması açık detayı bozmuyor",
          b.ev('return document.getElementById("detail").open && document.getElementById("detailBody").textContent;')
          == body)
    key(b, "Escape", "Escape", 27)
    time.sleep(0.3)
    check("run raporu: Escape ile kapanıyor", b.ev('return !document.getElementById("detail").open;'))

    b_inc = long["inc"]
    b.ev(f"""const li = openAlerts.get({json.dumps(b_inc)}).li;
             [...li.querySelectorAll("button")].find(x => x.textContent === "İhlal detayı").click(); return true;""")
    b.wait('document.getElementById("detailTitle").textContent === "İhlal detayı" && '
           'document.querySelector("#detailBody .detail-top img")')
    pic = b.ev('return document.querySelector("#detailBody .detail-top img").getAttribute("src");')
    check("ihlal detayı: uyarının incident'ı ve kendi fotoğrafı", pic == starts[b_inc], (pic, starts.get(b_inc)))
    race = b.ev(f"""showRun({json.dumps(run_id)}); await showIncident({json.dumps(b_inc)});
                    await new Promise(r => setTimeout(r, 400));
                    return document.getElementById("detailTitle").textContent;""")
    check("detay: geç gelen yanıt açık detayı başka olaya çevirmiyor", race == "İhlal detayı", race)
    b.shot("alert_incident_detail")
    key(b, "Escape", "Escape", 27)
    b.viewport(390)
    time.sleep(0.4)
    check("uyarı kartları 390px: yatay taşma yok", no_hscroll(b))
    b.shot("alerts_390")


def alert_checks_user(b):
    """FAZ 10-A.7 (WIP) + FAZ 01: user (supervisor) uyarı kartı operasyonel alanları gösterir; teknik alanlar ve run raporu
    düğmesi yoktur (sunucu da göndermez). İhlal detayı ve fotoğraf çalışmaya devam eder."""
    b.viewport(1280)
    b.go(BASE + "/")
    b.wait('document.querySelectorAll("#feed li").length >= 3')
    time.sleep(1.0)
    alerts = b.ev('return await (await fetch("/api/alerts")).json();')
    cards = b.ev(CARDS_JS)
    starts = {a["incident_id"]: a.get("snapshot") for a in alerts if a["type"] == "start"}
    check("user uyarı: incident başına tek kart", len(cards) == len({a["incident_id"] for a in alerts}),
          (len(cards), len(alerts)))
    check("user uyarı: her kartın fotoğrafı kendi incident'ının başlangıç snapshot'ı",
          all(c["img"] == starts.get(c["inc"]) for c in cards if c["img"]) and any(c["img"] for c in cards))
    g = {c["cam"]: c for c in cards}["Giriş kapısı"]
    check("user uyarı: operasyonel alanlar var (ihlal, baret, yelek, durum)",
          all(k in g["text"] for k in ("İhlal", "Baret", "Yelek", "Sürüyor")), g["text"][:200])
    check("user uyarı: teknik alanlar yok (geçici iz, video, incident, run)",
          not any(k in g["text"] for k in ("Geçici iz", "Video", "Incident", "Run")), g["text"][:200])
    btns = b.ev('return [...document.querySelectorAll("#feed li button")].map(x => x.textContent);')
    check("user uyarı: run raporu düğmesi yok, ihlal detayı var",
          not any(t.startswith("Run raporu") for t in btns) and "İhlal detayı" in btns, sorted(set(btns)))
    check("user uyarı: API takip no / run kimliği göndermiyor", not any(k in alerts[0] for k in ("pid", "run_id", "t", "box")),
          sorted(alerts[0]))
    inc = g["inc"]
    b.ev(f"""[...openAlerts.get({json.dumps(inc)}).li.querySelectorAll("button")].find(x => x.textContent === "İhlal detayı").click();
             return true;""")
    b.wait('document.getElementById("detailTitle").textContent === "İhlal detayı" && '
           '!document.getElementById("detailBody").textContent.includes("Yükleniyor")')
    body = b.ev('return document.getElementById("detailBody").textContent;')
    check("user ihlal detayı: açılıyor, teknik satırlar (İz ID, Video zamanı, Olay kaydı, Run) yok",
          not any(k in body for k in ("İz ID", "Video zamanı", "Olay kaydı", "Run raporunda")), body[:200])
    key(b, "Escape", "Escape", 27)
    time.sleep(0.3)


SCAN_JS = r"""
const SCORE = /(^|_)(conf|confidence|score|scores|prob|probs|probability|probabilities|logit|logits)$|_rate$/;
const ROWKEYS = new Set(["helmet", "vest"]);         // canlı kişi / rapor satırındaki oran anahtarları
const found = [];
function walk(v, path, rowish) {
  if (Array.isArray(v)) return v.forEach((x, i) => walk(x, `${path}[${i}]`, rowish));
  if (v && typeof v === "object") for (const [k, x] of Object.entries(v)) {
    if ((SCORE.test(k) && k !== "low_conf" && k !== "model_scores") || (rowish && ROWKEYS.has(k))) found.push(`${path}.${k}`);
    walk(x, `${path}.${k}`, k === "people" || k === "report_rows");
  }
}
const get = async p => { const r = await fetch(p); return [r.status, r.status === 200 ? await r.json() : null]; };
const out = {};
const [, st] = await get("/api/state?after=0"); out.state = st;
const [, al] = await get("/api/alerts"); out.alerts = al;
const [, rep] = await get("/api/report"); out.report_rows = rep;
const [, inc] = await get("/api/history/incidents?limit=200"); out.incidents = inc;
out.incident_details = [];
for (const i of (inc?.items || []).slice(0, 10)) out.incident_details.push((await get(`/api/history/incidents/${i.incident_id}`))[1]);
const [, runs] = await get("/api/history/runs?limit=200"); out.runs = runs;
out.run_details = [];
for (const r of (runs?.items || [])) out.run_details.push((await get(`/api/history/runs/${r.run_id}`))[1]);
out.summary = (await get("/api/history/summary"))[1];
out.cameras = (await get("/api/history/cameras"))[1];
out.me = (await get("/api/auth/me"))[1];
walk(out, "$", false);
const persons = out.run_details.flatMap(r => r?.persons || []);
return { found, persons: persons.length, rows: (rep || []).length, people: (st?.cameras || []).flatMap(c => c.people).length,
         timeline: out.incident_details.flatMap(d => d?.timeline || []).length, me: out.me,
         admin_sample: { person: persons[0] || null, row: (rep || [])[0] || null, people: st?.cameras?.[0]?.people?.[0] || null,
                         alert: (al || []).find(a => a.type === "start") || null } };
"""


def role_user_checks(b):
    b.viewport(1280)
    b.go(BASE + "/")
    b.wait('document.querySelectorAll("#feed li").length >= 3')
    me = b.ev('return await (await fetch("/api/auth/me")).json();')
    check("user: giriş başarılı ve rolü 'Kullanıcı' (supervisor)", me["user"]["role"] == "supervisor"
          and "Kullanıcı" in b.ev('return document.getElementById("userName").textContent;'), me["user"])
    check("user: model_scores yetkisi yok, canlı/snapshot yetkisi var",
          me["permissions"]["model_scores"] is False and me["permissions"]["live"] and me["permissions"]["snapshots"])
    s = b.ev(SCAN_JS)
    check("user: hiçbir API yanıtında olasılık/oran alanı yok (JSON içeriği)", s["found"] == [], s["found"][:10])
    check("user: taranan veri gerçekten var (boş yanıtla geçmiyor)", s["people"] > 0 and s["timeline"] > 0,
          {k: s[k] for k in ("people", "timeline")})
    check("user: run raporları ve video özeti (teknik) kişi satırı içermiyor (403)", s["persons"] == 0 and s["rows"] == 0,
          {k: s[k] for k in ("persons", "rows")})
    check("user: technical yetkisi yok", me["permissions"].get("technical") is False, me["permissions"])
    snap = b.ev("""const a = (await (await fetch("/api/alerts")).json()).find(x => x.snapshot);
                   const r = await fetch(a.snapshot); return [r.status, r.headers.get("content-type")];""")
    check("user: snapshot görüntüleme izinli (kapsam içinde)", snap == [200, "image/jpeg"], snap)
    deny = b.ev("""const h = { "Content-Type": "application/json", "X-CSRF-Token": CSRF };
                   const c = await fetch("/api/admin/users", { method: "POST", headers: h,
                     body: JSON.stringify({ username: "kotu_admin", password: "x".repeat(20), role: "admin" }) });
                   const p = [];                       // fixture kullanıcı kimlikleri 1..3 (admin, user, viewer)
                   for (const id of [1, 2, 3]) p.push((await fetch(`/api/admin/users/${id}`, { method: "PATCH",
                     headers: h, body: JSON.stringify({ role: "admin" }) })).status);
                   const l = await fetch("/api/admin/users");
                   return [c.status, ...p, l.status];""")
    check("user: admin hesabı oluşturma / admin rolü verme / kullanıcı listesi reddedilir",
          deny == [403, 403, 403, 403, 403], deny)
    me2 = b.ev('return (await (await fetch("/api/auth/me")).json()).user.role;')
    check("user: rol değişmedi", me2 == "supervisor", me2)
    dom = b.ev("""return [document.body.innerText.includes("Model oranı"), document.body.innerText.includes("model %"),
                          document.body.innerText.includes("%12"), document.body.innerText.includes("%78")];""")
    check("user: arayüzde model oranı yok (uyarı kartı, kişi tablosu)", dom == [False, False, False, False], dom)
    hidden = b.ev("""return [document.getElementById("reportBox").hidden, document.getElementById("tab-runs").hidden,
                             document.getElementById("covBox").hidden];""")
    check("user: video özeti, run sekmesi ve analiz kapsamı (teknik) arayüzde gizli", hidden == [True, True, True], hidden)
    b.ev('location.hash = "#runs"; return true;')
    time.sleep(0.5)
    check("user: #runs adresi run ekranını açmaz", b.ev('return document.getElementById("view-runs").hidden;'))
    codes = b.ev("""const r = await fetch("/api/history/runs"), d = await fetch("/api/report");
                    return [r.status, d.status];""")
    check("user: run geçmişi ve video özeti API'leri reddedilir", codes == [403, 403], codes)
    b.shot("user_live_no_technical")
    for view in ("history", "stats"):
        b.ev(f'location.hash = "#{view}"; return true;')
        b.wait(f'!document.getElementById("view-{view}").hidden && '
               f'document.getElementById("{"hStatus" if view == "history" else "sStatus"}").textContent.length > 0')
        st = b.ev(f'return document.getElementById("{"hStatus" if view == "history" else "sStatus"}").textContent;')
        check(f"user: {view} ekranı çalışıyor", "yetki" not in st and "başarısız" not in st, st)
    b.ev('location.hash = ""; return true;')


def role_admin_checks(b):
    login(b, "ui_admin")
    b.viewport(1280)
    b.wait('document.querySelectorAll("#feed li").length >= 3')
    me = b.ev('return await (await fetch("/api/auth/me")).json();')
    check("admin: giriş başarılı, model_scores yetkisi var", me["user"]["role"] == "admin"
          and me["permissions"]["model_scores"] is True)
    s = b.ev(SCAN_JS)
    sm = s["admin_sample"]
    check("admin: API'de oranlar korunuyor (uyarı, kişi, rapor satırı, run raporu)",
          sm["alert"] and "helmet_rate" in sm["alert"] and sm["people"] and "helmet" in sm["people"]
          and sm["row"] and "helmet" in sm["row"] and sm["person"] and "helmet_rate" in sm["person"], sm)
    time.sleep(1.0)
    dom = b.ev('return [document.querySelector("#feed").innerText.includes("Model oranı"), '
               '[...document.querySelectorAll("#peopleWrap th")].some(x => x.textContent.includes("model %"))];')
    check("admin: arayüzde model oranı görünüyor (uyarı kartı, kişi tablosu)", dom == [True, True], dom)
    b.shot("admin_live")
    run_id = b.ev('return (await (await fetch("/api/alerts")).json()).find(a => a.type === "start").run_id;')
    b.ev(f'showRun({json.dumps(run_id)}); return true;')
    b.wait('document.getElementById("detail").open && !document.getElementById("detailBody").textContent.includes("Yükleniyor")')
    check("admin: detay önbelleği dolu (oturum değişimi testi için)", b.ev("return DETAIL_CACHE.size;") >= 1)
    key(b, "Escape", "Escape", 27)
    time.sleep(0.3)
    b.ev('document.getElementById("loadReport").click(); return true;')
    b.wait('document.querySelector("#reportWrap table")')
    hdr = b.ev('return [...document.querySelectorAll("#reportWrap th")].map(x => x.textContent);')
    check("admin: video özeti tablosu çalışıyor", "Sonuç" in hdr, hdr)
    b.ev('location.hash = "#runs"; return true;')
    b.wait('document.querySelector("#rTable tbody tr")')
    b.ev("""const row = [...document.querySelectorAll("#rTable tbody tr")].find(r => r.textContent.includes("Depo") && r.textContent.includes("Bitti"));
            row.querySelector("button").click(); return true;""")
    b.wait('document.getElementById("detail").open && document.querySelector("#detailBody table")')
    hdr = b.ev('return [...document.querySelectorAll("#detailBody th")].map(x => x.textContent);')
    check("admin: run geçmişi → detay → run raporu çalışıyor", "Gözlem" in hdr and "Sonuç" in hdr, hdr)
    b.shot("admin_run_report")
    key(b, "Escape", "Escape", 27)
    b.ev('location.hash = ""; return true;')


def session_switch_checks(b):
    logout(b)
    check("oturum değişimi: çıkışta önbellek, kartlar ve tablolar temizlendi",
          b.ev('return DETAIL_CACHE.size === 0 && !document.querySelector("#feed li") && '
               '!document.querySelector("#peopleWrap table") && !document.getElementById("detailBody").childElementCount;'))
    login(b, "ui_user")
    b.wait('document.querySelectorAll("#feed li").length >= 3')
    time.sleep(1.5)
    dom = b.ev("""return { scores: SCORES, model: document.body.innerText.includes("Model oranı"),
                           pct: document.body.innerText.includes("%12") || document.body.innerText.includes("%78"),
                           th: [...document.querySelectorAll("th")].some(x => x.textContent.includes("model %")) };""")
    check("oturum değişimi: admin verisi user ekranına sızmıyor", dom == {"scores": False, "model": False, "pct": False,
                                                                         "th": False}, dom)
    logout(b)


def viewer_checks(b):
    login(b, "ui_viewer")
    r = b.ev("""const s = await fetch("/api/state"), a = await fetch("/api/alerts");
                const inc = (await (await fetch("/api/history/incidents")).json()).items.find(i => i.snapshot_restricted);
                const d = await (await fetch(`/api/history/incidents/${inc.incident_id}`)).json();
                return [s.status, a.status, document.getElementById("tab-live").hidden, d.snapshot,
                        d.timeline.some(e => "helmet_rate" in e)];""")
    check("viewer: canlı izleme kapalı, görüntü kısıtlı, oran yok (mevcut kurallar korunuyor)",
          r == [403, 403, True, None, False], r)
    logout(b)
    anon = b.ev('const r = await fetch("/api/state"); return r.status;')
    check("yetkisiz: oturumsuz istek reddedilir", anon == 401, anon)


def main():
    b = CDP()
    try:
        b.viewport(1280)
        login(b, "ui_user")
        header_checks(b)
        layout_checks(b)
        alert_checks_user(b)
        role_user_checks(b)
        role_admin_checks(b)
        alert_checks(b)                  # admin oturumu: teknik kart alanları ve run raporu
        session_switch_checks(b)
        viewer_checks(b)
        for extra in sys.argv[2:]:
            globals()[extra](b)
    finally:
        b.close()
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=1)
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} PASS", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
