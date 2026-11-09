"""Optional local LLM phrasing for Ask the desk (Day 22). Off by default.

Enabled only when ``DESKBRAIN_LLM_URL`` points at an OpenAI-compatible server
(``.../v1``) on loopback (127.0.0.0/8, ::1 or ``localhost``); any other host is
refused, so desk text never leaves the laptop. ``DESKBRAIN_LLM_MODEL`` picks the
model (default ``local``), ``DESKBRAIN_LLM_TIMEOUT`` the timeout in seconds (20).

The model only sees the question and the numbered cited sections, and must cite
``[n]`` after each sentence. Afterwards every sentence is checked: sentences
without a valid citation number are dropped, unknown numbers are removed. If
nothing valid is left, or the server fails, the extractive answer is kept.
Requests ignore proxy env vars and never follow redirects.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.request
from urllib.parse import urlsplit

MAX_SOURCE_CHARS, MAX_ANSWER_CHARS = 1500, 2000
_CITE = re.compile(r"\[(\d{1,2})\]")
_SENT = re.compile(r"(?<=[.!?\]])\s+(?=\S)")
DONT_KNOW = "DONT_KNOW"


class LLMConfigError(ValueError):
    pass


def check_loopback_url(url: str) -> str:
    """Return the normalised base URL, or raise if it is not plain http(s) on loopback."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise LLMConfigError("LLM URL must be http(s)")
    if parts.username or parts.password:
        raise LLMConfigError("LLM URL must not contain credentials")
    host = (parts.hostname or "").lower()
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise LLMConfigError(f"LLM host {host!r} is not loopback")
        except ValueError:
            raise LLMConfigError(f"LLM host {host!r} is not a loopback IP or 'localhost'") from None
    return url.strip().rstrip("/")


def config_from_env(env=None) -> dict | None:
    """None when off. Raises LLMConfigError on a non-loopback URL (fail loudly at start)."""
    env = os.environ if env is None else env
    url = env.get("DESKBRAIN_LLM_URL", "").strip()
    if not url:
        return None
    return {"url": check_loopback_url(url), "model": env.get("DESKBRAIN_LLM_MODEL", "local") or "local",
            "timeout": float(env.get("DESKBRAIN_LLM_TIMEOUT", "20") or 20)}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def post_chat(cfg: dict, messages: list[dict]) -> str:
    """POST /chat/completions; returns the reply text. Tests monkeypatch this."""
    body = json.dumps({"model": cfg["model"], "messages": messages, "temperature": 0.1,
                       "max_tokens": 400, "stream": False}).encode()
    req = urllib.request.Request(check_loopback_url(cfg["url"]) + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with _OPENER.open(req, timeout=cfg["timeout"]) as r:
        data = json.loads(r.read(1_000_000))
    return data["choices"][0]["message"]["content"]


def build_messages(question: str, citations: list[dict]) -> list[dict]:
    src = "\n\n".join(f"[{c['n']}] {c['rel']} > {c.get('heading') or ''}\n{c['text'][:MAX_SOURCE_CHARS]}"
                      for c in citations)
    system = ("You answer questions using ONLY the numbered sources from the user's notes. "
              "Write 1-4 short sentences. End every sentence with the source number(s) it is based on, "
              "like [1] or [1][3]. Do not use outside knowledge. If the sources do not answer the "
              f"question, reply exactly {DONT_KNOW}.")
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"Sources:\n\n{src}\n\nQuestion: {question}"}]


def validate(reply: str, valid: set[int]) -> list[dict] | None:
    """Split the reply into sentences; keep those citing at least one valid number.
    Returns [{text, n, cites}] (text without the [n] markers), [] for don't-know, None if unusable."""
    reply = (reply or "").strip()[:MAX_ANSWER_CHARS]
    if not reply:
        return None
    if reply.strip(" .").upper() == DONT_KNOW:
        return []
    out = []
    for s in _SENT.split(" ".join(reply.split())):
        cites = list(dict.fromkeys(int(m) for m in _CITE.findall(s) if int(m) in valid))
        text = _CITE.sub("", s).strip()
        text = re.sub(r"\s+([.,;:!?])", r"\1", text)
        if cites and len(re.findall(r"\w+", text)) >= 2:
            out.append({"text": text, "n": cites[0], "cites": cites})
    return out or None


def phrase(cfg: dict | None, out: dict, post=None) -> dict:
    """Replace ``out['answer']`` with an LLM-phrased one when configured and valid.
    Only runs when the extractive step found the evidence good enough (``known``)."""
    out.setdefault("llm", "off" if cfg is None else "skipped")
    if cfg is None or not out.get("known") or not out.get("citations"):
        return out
    post = post or post_chat
    try:
        reply = post(cfg, build_messages(out["question"], out["citations"]))
    except Exception as e:  # server down, timeout, bad JSON: keep the extractive answer
        out["llm"] = f"error: {type(e).__name__}"
        return out
    got = validate(reply, {c["n"] for c in out["citations"]})
    if got is None:
        out["llm"] = "rejected"
        return out
    if not got:   # model declined: keep the verbatim quotes (they cannot be made up)
        out["llm"] = "dont-know"
        return out
    out["quotes"] = out["answer"]
    out.update(answer=got, answer_method="llm", llm="ok")
    return out
