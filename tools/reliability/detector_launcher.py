"""Gerçek detector.main() başlatıcısı (yalnız test). Windows'ta ayrı süreç grubuna CTRL_C gönderilemediğinden
CTRL_BREAK (SIGBREAK) KeyboardInterrupt'a çevrilir; bu modül 'spawn' ile açılan kamera süreçlerinde de (__mp_main__)
yüklendiği için her süreç konsoldaki Ctrl+C ile AYNI kapanış yolunu izler (run 'interrupted', outbox boşaltma).
Dedektör kodu değiştirilmez."""
import signal
import sys
from pathlib import Path


def _to_kbi(*_):
    raise KeyboardInterrupt


if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _to_kbi)

DET = Path(__file__).resolve().parents[2] / "detector"
if str(DET) not in sys.path:
    sys.path.insert(0, str(DET))

if __name__ == "__main__":
    import detector
    detector.main()
