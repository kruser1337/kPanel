"""Regenerate kpanel/properties_meta.json: one entry per server.properties key.

    python kpanel/tools/gen_meta.py <live-server.properties> > kpanel/properties_meta.json

Sources:
  * The live server.properties the server generated decides WHICH keys exist.
    Newer Minecraft drops keys (pvp, spawn-monsters, ... became gamerules), and
    an entry for a key the server no longer reads would be a lie in the UI.
  * itzg's files/property-definitions.json: the env var the image maps to each
    key. Keys without one go through CUSTOM_SERVER_PROPERTIES.
  * The Minecraft Wiki "Server.properties" page: type, default, description,
    and per-value explanations (the ":'''value''' - text" lines). That text is
    CC BY-NC-SA 3.0 (https://minecraft.wiki/w/Server.properties) and is
    credited in the panel.

Rerun it after a Minecraft upgrade adds or removes keys.
"""

import json
import re
import sys
import urllib.request

UA = {"User-Agent": "kpanel-gen-meta (github.com/kruser1337/kPanel)"}
WIKI = "https://minecraft.wiki/api.php?action=parse&page=Server.properties&prop=wikitext&format=json&formatversion=2"
DEFS = "https://raw.githubusercontent.com/itzg/docker-minecraft-server/master/files/property-definitions.json"


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read().decode()


def clean(s: str) -> str:
    """Wiki markup -> plain text."""
    s = re.sub(r"\{\{[Ii]nfo needed[^{}]*\}\}", "", s)
    s = re.sub(r"\{\{(?:cd|code|key)\|([^{}|]*)[^{}]*\}\}", r"\1", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", "", s, flags=re.S)
    s = re.sub(r"</?(code|span|br|sup|small)[^>]*>", "", s)
    s = s.replace("'''", "").replace("''", "")
    return re.sub(r"[ \t]+", " ", s).strip()


def parse_wiki(wt: str) -> dict:
    out = {}
    for block in re.split(r'\n\|- id="', wt)[1:]:
        key = block.split('"', 1)[0]
        cells = re.split(r"\n\|(?!-)", "\n" + block.split("\n", 1)[1])
        cells = [c for c in cells if c.strip()]
        if len(cells) < 4:
            continue
        head, typ, default, desc = cells[0], cells[1], cells[2], "\n|".join(cells[3:])
        if "upcoming" in head.lower():
            continue  # documented for a future version, not 26.3
        desc = desc.split("\n|}")[0]
        options, text = [], []
        for line in desc.splitlines():
            m = re.match(r"^:+\s*'''([^']+)'''\s*(?:[-–—:]|\(.*?\)\s*[-–—])?\s*(.*)$", line)
            if m:
                options.append({"value": clean(m.group(1)), "desc": clean(m.group(2))})
            elif line.strip() and not line.startswith(("{|", "|}", "!")):
                text.append(clean(line))
        out[key] = {
            "type": clean(typ).lower(),
            "wiki_default": clean(default),
            "desc": " ".join(t for t in text if t)[:600],
            "options": options,
        }
    return out


def main(live_path):
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from settings import parse_properties

    live = parse_properties(Path(live_path).read_text())
    defs = json.loads(fetch(DEFS))
    wiki = parse_wiki(json.loads(fetch(WIKI))["parse"]["wikitext"])
    meta = {}
    for key in sorted(live):
        w = wiki.get(key, {})
        meta[key] = {
            "env": (defs.get(key) or {}).get("env"),
            "type": w.get("type", "string"),
            "default": w.get("wiki_default", ""),
            "desc": w.get("desc", ""),
            "options": w.get("options", []),
        }
    json.dump({"_source": "Minecraft Wiki, Server.properties (CC BY-NC-SA 3.0); "
                          "itzg/docker-minecraft-server property-definitions.json",
               "properties": meta}, sys.stdout, indent=1, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    missing = [k for k in live if k not in wiki]
    if missing:
        print(f"no wiki entry for: {', '.join(missing)}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
