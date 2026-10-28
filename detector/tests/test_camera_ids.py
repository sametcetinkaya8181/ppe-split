"""FAZ 5: kaynak sırasından bağımsız, kalıcı kamera kimliği (protocol.camera_entries)."""
import pytest

import protocol as p

RTSP = "rtsp://admin:S3cr3t@10.0.0.5:554/stream1?token=T0K3N"


def ids(entries):
    return {src: cid for cid, _, src in entries}


def test_explicit_ids_survive_reordering():
    a = ["[giris]Giriş=videos/a.mp4", f"[kule]Kule={RTSP}", "[yard]videos/b.mp4"]
    first, w1 = p.camera_entries(a)
    second, w2 = p.camera_entries(list(reversed(a)))
    assert ids(first) == ids(second) == {"videos/a.mp4": "giris", RTSP: "kule", "videos/b.mp4": "yard"}
    assert w1 == w2 == []                                   # açık kimlik: uyarı yok
    assert {c: lbl for c, lbl, _ in first} == {"giris": "Giriş", "kule": "Kule", "yard": "b"}


def test_source_mode_ids_are_order_independent_and_secret_free():
    a = ["videos/5434223-hd_1920_1080_24fps.mp4", "videos/16533594_1920_1080_25fps.mp4", RTSP, "0"]
    first, w = p.camera_entries(a, "source")
    second, _ = p.camera_entries(list(reversed(a)), "source")
    assert ids(first) == ids(second) and w == []
    got = ids(first)
    assert got["videos/5434223-hd_1920_1080_24fps.mp4"] == "5434223-hd_1920_1080_24fps"
    assert got["0"] == "webcam0" and got[RTSP].startswith("rtsp_")
    for cid, label, _ in first:
        assert p.CAMERA_ID_RE.match(cid)
        for secret in ("admin", "S3cr3t", "T0K3N"):
            assert secret not in cid and secret not in label
    # yalnızca parola değişirse kimlik aynı kalır (kimlik bilgisi özete girmez)
    other_pw, _ = p.camera_entries([RTSP.replace("S3cr3t", "baska")], "source")
    assert other_pw[0][0] == got[RTSP]


def test_index_mode_is_legacy_and_warns():
    entries, warnings = p.camera_entries(["videos/a.mp4", "Kapı=" + RTSP])
    assert [c for c, _, _ in entries] == ["cam1", "cam2"]
    assert [lbl for _, lbl, _ in entries] == ["a", "Kapı"]
    assert len(warnings) == 2 and all("SIRASINDAN" in w for w in warnings)
    swapped, _ = p.camera_entries(["Kapı=" + RTSP, "videos/a.mp4"])
    assert ids(swapped)["videos/a.mp4"] == "cam2"           # eski davranış: sıra değişince kimlik kayar


def test_mixed_explicit_and_legacy_only_warns_for_legacy():
    entries, warnings = p.camera_entries(["[kapi]" + RTSP, "videos/a.mp4"])
    assert [c for c, _, _ in entries] == ["kapi", "cam2"] and len(warnings) == 1 and warnings[0].startswith("cam2")


@pytest.mark.parametrize("items,mode", [
    (["[giris]videos/a.mp4", "[giris]videos/b.mp4"], "index"),          # aynı açık kimlik
    (["[cam2]videos/a.mp4", "videos/b.mp4"], "index"),                   # açık kimlik eski kimlikle çakışıyor
    (["videos/a.mp4", "other/a.mp4"], "source"),                         # aynı dosya adı
    (["[kapı 1]videos/a.mp4"], "index"),                                 # geçersiz karakter
    (["[a:b@c]videos/a.mp4"], "index"),
    (["[" + "x" * 41 + "]videos/a.mp4"], "index"),
    (["[]videos/a.mp4"], "index"),
    (["[ok]"], "index"),                                                 # kaynak yok
    (["videos/a.mp4"], "hash"),                                          # bilinmeyen mod
])
def test_invalid_or_duplicate_ids_rejected(items, mode):
    with pytest.raises(p.ProtocolError):
        p.camera_entries(items, mode)


def test_error_messages_do_not_leak_credentials():
    with pytest.raises(p.ProtocolError) as ei:
        p.camera_entries([f"[x]{RTSP}", f"[x]{RTSP}"])
    assert "S3cr3t" not in str(ei.value)
