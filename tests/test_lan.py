"""Day 23: LAN mode part 1 — address checks, token file, default stays loopback."""
import os
import stat
import subprocess
import sys

import pytest

from deskbrain import app as appmod
from deskbrain.lan import LanError, choose_bind, is_lan_address, load_or_create_token, token_path


@pytest.mark.parametrize("a", ["192.168.1.20", "10.0.0.5", "172.16.0.1", "172.31.255.254",
                               "169.254.3.4", "fd12:3456::1", "fe80::1%eth0", "::ffff:192.168.0.9"])
def test_lan_ok(a):
    assert is_lan_address(a)


@pytest.mark.parametrize("a", ["0.0.0.0", "::", "127.0.0.1", "::1", "8.8.8.8", "172.32.0.1",
                               "100.64.0.1", "224.0.0.1", "ff02::1", "2001:4860::8888",
                               "::ffff:8.8.8.8", "localhost", "192.168.1.0/24", "", "lan.local"])
def test_lan_refused(a):
    assert not is_lan_address(a)


def test_choose_bind():
    c = ["127.0.0.1", "203.0.113.7", "192.168.1.20"]
    assert choose_bind(None, c) == "192.168.1.20"
    assert choose_bind("192.168.1.20", c) == "192.168.1.20"
    for bad in ("0.0.0.0", "203.0.113.7", "127.0.0.1", "10.9.9.9"):
        with pytest.raises(LanError):
            choose_bind(bad, c)
    with pytest.raises(LanError):
        choose_bind(None, ["127.0.0.1", "203.0.113.7"])


def test_token_file(tmp_path):
    t = load_or_create_token(tmp_path)
    p = token_path(tmp_path)
    assert len(t) >= 32 and stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert load_or_create_token(tmp_path) == t          # stable
    os.chmod(p, 0o644)                                  # too open -> regenerated, 0600
    t2 = load_or_create_token(tmp_path)
    assert t2 != t and stat.S_IMODE(os.stat(p).st_mode) == 0o600
    p.write_text("short\n"); os.chmod(p, 0o600)
    assert load_or_create_token(tmp_path) not in ("short", t2)
    t3 = load_or_create_token(tmp_path)
    assert load_or_create_token(tmp_path, rotate=True) != t3


def test_token_symlink_not_followed(tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    (tmp_path / "d").mkdir()
    token_path(tmp_path / "d").symlink_to(target)
    load_or_create_token(tmp_path / "d")
    assert target.read_text() == "keep"
    assert not token_path(tmp_path / "d").is_symlink()


def test_default_is_loopback():
    assert appmod.HOST == "127.0.0.1"


def test_lan_flag_refuses_public_and_starts_nothing(tmp_path):
    env = dict(os.environ, DESKBRAIN_DATA=str(tmp_path))
    r = subprocess.run([sys.executable, "-m", "deskbrain.app", "--lan", "8.8.8.8"],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode != 0 and "not a private LAN address" in r.stderr
    assert not (tmp_path / "lan-token").exists()
    r = subprocess.run([sys.executable, "-m", "deskbrain.app", "--lan", "0.0.0.0"],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode != 0 and "refused" in r.stderr
