"""FAZ 02-A.1: yedek kapasite koruması ve bakım ön kontrolünün saf kuralları (docker/PowerShell ÇAĞIRMAZ).

    python -m pytest -q scripts/tests"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_preflight as pf  # noqa: E402
import ppe_ops  # noqa: E402

GiB, MiB = 2**30, 2**20
PUB = "age1" + "q" * 58                       # biçimce geçerli (bech32 alfabesi) sahte açık anahtar


# ---------------------------------------------------------------- kapasite (backup.py FAIL-CLOSED)
def test_capacity_ok_when_margin_remains():
    assert ppe_ops.capacity_problems(free=30 * GiB, need=200 * MiB, min_free=2 * GiB) == []


def test_capacity_refused_below_floor():
    p = ppe_ops.capacity_problems(free=2 * GiB + 100 * MiB, need=100 * MiB, min_free=2 * GiB)
    assert p and "yetersiz" in p[0]


def test_capacity_reserves_factor_times_estimate():
    # 1 GiB tahmin × 2 = 2 GiB ayrılır; 5 GiB boşta 3 GiB kalır: 3 GiB taban sağlanır, 3.5 GiB sağlanmaz
    assert ppe_ops.capacity_problems(5 * GiB, 1 * GiB, 3 * GiB) == []
    assert ppe_ops.capacity_problems(5 * GiB, 1 * GiB, int(3.5 * GiB))


def test_capacity_negative_estimate_is_not_a_bypass():
    assert ppe_ops.capacity_problems(1 * GiB, -10 * GiB, 2 * GiB)


def test_existing_parent_of_not_yet_created_dir(tmp_path):
    assert ppe_ops.existing_parent(tmp_path / "a" / "b" / "c") == tmp_path.resolve()


def test_dir_bytes_counts_files_only(tmp_path):
    (tmp_path / "x.jpg").write_bytes(b"1234")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "y.jpg").write_bytes(b"12")
    assert ppe_ops.dir_bytes(tmp_path) == 6


# ---------------------------------------------------------------- ön kontrol saf kuralları
@pytest.mark.parametrize("text,want", [("134.7MB", 134_700_000), ("242.5kB", 242_500), ("0B", 0),
                                       ("1.981GB", 1_981_000_000), ("N/A", None), ("", None)])
def test_parse_docker_size(text, want):
    assert pf.parse_docker_size(text) == want


def test_broad_acl_detected_by_sid_not_name():
    entries = [("S-1-5-21-1-2-3-1001", "Allow"), ("S-1-5-32-545", "Allow"), ("S-1-1-0", "Deny")]
    p = pf.broad_acl_problems(entries)
    assert len(p) == 1 and "Users" in p[0]                  # Deny girişi sorun sayılmaz


def test_owner_only_acl_passes():
    assert pf.broad_acl_problems([("S-1-5-21-1-2-3-1001", "Allow"), ("S-1-5-18", "Allow")]) == []


def test_recipients_public_key_only_passes():
    assert pf.recipients_problems(f"# veri sorumlusu\n{PUB}\n") == []


def test_recipients_private_key_on_field_machine_fails():
    p = pf.recipients_problems(f"{PUB}\nAGE-SECRET-KEY-1{'Q' * 58}\n")
    assert p and "ÖZEL ANAHTAR" in p[0]


@pytest.mark.parametrize("text", ["", "# yalnız yorum\n", "age1short\n", "ssh-ed25519 AAAA\n"])
def test_recipients_invalid_or_empty_fails(text):
    assert pf.recipients_problems(text)


def test_inside_git_tree(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert pf.inside_git_tree(tmp_path / "repo" / "backups" / "new") == (tmp_path / "repo").resolve()
    assert pf.inside_git_tree(tmp_path / "elsewhere") in (None, *[p for p in tmp_path.parents if (p / ".git").exists()])


def test_verdict_fail_wins_warn_does_not_block():
    assert pf.verdict([{"status": "PASS"}, {"status": "WARN"}]) == "PASS"
    assert pf.verdict([{"status": "PASS"}, {"status": "FAIL"}, {"status": "WARN"}]) == "FAIL"
