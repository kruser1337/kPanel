"""Gamerules: applied live over RCON, no restart, not in git.

Gamerules live in the world save, not in server.properties, and newer Minecraft
moved several classic properties here (pvp, mob spawning, ...). The candidate
list and descriptions come from gamerules_meta.json (tools/gen_gamerules.py,
Minecraft Wiki). The SERVER decides which exist: every candidate is queried,
and rules it doesn't answer for are dropped. On 2026-10-01 that was 58 of 59;
max_minecart_speed needs an experimental feature flag.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import rcon

META = json.loads((Path(__file__).parent / "gamerules_meta.json").read_text())["rules"]
CATEGORY_ORDER = ["Player", "Mobs", "Spawning", "Drops", "World Updates", "Chat", "Miscellaneous"]
_RULE = re.compile(r"[a-z][a-z0-9_]*")
INT_MIN, INT_MAX = -(2 ** 31), 2 ** 31 - 1


@dataclass(frozen=True)
class Rule:
    name: str
    category: str
    desc: str
    default: str
    kind: str          # "bool" | "int"
    min: int
    max: int


def _rule(name, m):
    typ = m["type"]
    kind = "int" if typ.split()[0].startswith("int") else "bool"
    lo = re.search(r"min:\s*(-?\d+)", typ)
    hi = re.search(r"max:\s*(-?\d+)", typ)
    default = m["default"]
    if kind == "bool":
        # A rule the wiki lists as bool-or-string has both defaults in one cell,
        # scraped together ("trueeveryone"). The server answers true/false.
        b = re.match(r"true|false", default)
        default = b.group(0) if b else default
    return Rule(name, m["category"], m["desc"], default, kind,
                int(lo.group(1)) if lo else INT_MIN, int(hi.group(1)) if hi else INT_MAX)


RULES = {n: _rule(n, m) for n, m in META.items() if _RULE.fullmatch(n)}


def current(client) -> dict:
    """{name: value} for every candidate the server knows, in ONE RCON connection."""
    names = sorted(RULES)
    replies = client.run(*(f"gamerule {n}" for n in names))
    return {n: v for n, r in zip(names, replies) if (v := rcon.parse_gamerule(r)) is not None}


def grouped(names):
    out = []
    for cat in CATEGORY_ORDER + sorted({RULES[n].category for n in names} - set(CATEGORY_ORDER)):
        ns = sorted(n for n in names if RULES[n].category == cat)
        if ns:
            out.append((cat, ns))
    return out


def validate(rule: Rule, raw: str) -> str:
    v = (raw or "").strip().lower()
    if rule.kind == "bool":
        if v not in ("true", "false"):
            raise ValueError(f"{rule.name}: must be true or false")
        return v
    try:
        n = int(v)
    except ValueError:
        raise ValueError(f"{rule.name}: must be a whole number") from None
    if not rule.min <= n <= rule.max:
        raise ValueError(f"{rule.name}: must be between {rule.min} and {rule.max}")
    return str(n)
