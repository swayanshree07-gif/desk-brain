"""LAN mode (Day 23, part 1): address checks and the access token.

LAN mode is off by default. When asked for, it may only bind to a *private* LAN
address of this machine: RFC 1918 (10/8, 172.16/12, 192.168/16), IPv4 link-local
(169.254/16), IPv6 unique-local (fc00::/7) or link-local (fe80::/10). Never
0.0.0.0/::, loopback, CGNAT, multicast or any public address.

The token lives in data/lan-token (mode 0600). It is created with O_EXCL, and is
regenerated if the file is unreadable, too short, or readable by group/others.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
import socket
import stat
from pathlib import Path

from .config import DATA_DIR

LAN_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "fc00::/7", "fe80::/10"))
TOKEN_NAME = "lan-token"
MIN_TOKEN_LEN = 32


class LanError(ValueError):
    pass


def is_lan_address(addr: str) -> bool:
    """True only for a single private-LAN unicast address (no wildcard, no hostname)."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0].strip("[]"))
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_unspecified or ip.is_loopback or ip.is_multicast:
        return False
    return any(ip in n for n in LAN_NETS if n.version == ip.version)


def local_addresses() -> list[str]:
    """This machine's IPv4 addresses (best effort, no packets sent)."""
    found: list[str] = []
    try:  # UDP "connect" only picks a route; nothing goes on the wire
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    return list(dict.fromkeys(found))


def choose_bind(requested: str | None = None, candidates: list[str] | None = None) -> str:
    """The address to bind in LAN mode. ``requested`` must be a private LAN address
    of this machine; with none, the first private local address is used."""
    cands = local_addresses() if candidates is None else candidates
    if requested:
        if not is_lan_address(requested):
            raise LanError(f"{requested!r} is not a private LAN address (refusing: LAN mode is never public)")
        if requested not in cands:
            raise LanError(f"{requested} is not an address of this machine (found: {', '.join(cands) or 'none'})")
        return requested
    for a in cands:
        if is_lan_address(a):
            return a
    raise LanError("no private LAN address found on this machine; pass one with --lan ADDR")


def token_path(data_dir: Path | None = None) -> Path:
    return Path(data_dir or DATA_DIR) / TOKEN_NAME


def load_or_create_token(data_dir: Path | None = None, rotate: bool = False) -> str:
    """Read the token from data/lan-token, or create a fresh one (0600)."""
    p = token_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not rotate:
        try:
            st = os.lstat(p)
            if stat.S_ISREG(st.st_mode) and not (st.st_mode & 0o077) and st.st_uid == os.getuid():
                tok = p.read_text().strip()
                if len(tok) >= MIN_TOKEN_LEN and tok.isascii() and tok.isprintable():
                    return tok
        except (FileNotFoundError, UnicodeDecodeError):
            pass
    try:
        os.unlink(p)
    except FileNotFoundError:
        pass
    tok = secrets.token_urlsafe(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    os.chmod(p, 0o600)
    return tok
