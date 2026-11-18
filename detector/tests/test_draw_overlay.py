"""FAZ 10-A.7: canlı kare çizimi model türevli baret/yelek oranını (%) piksele yazmaz (kare tüm rollere aynı JPEG
olarak gider; oran yalnız admin'e rol filtreli JSON ile). Çizim kararı etkilemez: PPEMonitor çıktısı aynı kalır."""
import numpy as np

import detector as d
import ppe_core as core


def test_overlay_text_has_track_and_status_but_no_rates(monkeypatch):
    texts = []
    monkeypatch.setattr(d.cv2, "putText", lambda img, text, *a, **k: texts.append(text))
    frame = np.zeros((200, 300, 3), np.uint8)
    persons = [(7, [10, 20, 80, 190], 0.91), (8, [150, 20, 220, 190], 0.55)]
    statuses = {7: ("IHLAL", 0.12, 0.78), 8: ("UYGUN", 1.0, 1.0)}
    out = d.draw(frame, persons, statuses, {8: [160, 15, 200, 45]}, {})
    assert texts == ["ID7 IHLAL", "ID8 UYGUN"]
    assert not any("%" in t or "B:" in t or "Y:" in t for t in texts)
    assert out is frame and frame.any()                       # kutular yine çiziliyor


def test_draw_does_not_change_monitor_decisions():
    """Aynı girdiyle PPEMonitor kararı ve olayları çizimden bağımsızdır (çizim karar durumunu okumaz/yazmaz)."""
    persons = [(7, [100, 100, 220, 460], 0.9)]
    vest = {7: [110, 200, 210, 340]}                          # yelek var, baret yok -> baret ihlali
    a, b = core.PPEMonitor(), core.PPEMonitor()
    starts = []
    for i in range(80):
        t = i * 0.1
        sa, ea = a.update(t, (1280, 720), persons, {}, vest)
        sb, eb = b.update(t, (1280, 720), persons, {}, vest)
        d.draw(np.zeros((720, 1280, 3), np.uint8), persons, sa, {}, vest)    # yalnız a'nın karesine çizilir
        assert (sa, ea) == (sb, eb)
        starts += [e for e in ea if e["type"] == "start"]
    assert sa[7][0] == "IHLAL" and len(starts) == 1 and starts[0]["missing"] == "Baretsiz"
