"""Regenerate kpanel/gamerules_meta.json from the Minecraft Wiki "Game rule" page.

    python kpanel/tools/gen_gamerules.py > kpanel/gamerules_meta.json

This is only the *candidate* list, with descriptions. At runtime the panel
asks the server for every candidate over RCON and shows only the rules the
server answers for, so a rule the wiki lists but this version lacks never
shows up. Java names only (snake_case since 1.21.11; keepInventory is now
keep_inventory). Bedrock-only rules are skipped.

Text: Minecraft Wiki, CC BY-NC-SA 3.0 (https://minecraft.wiki/w/Game_rule).
"""

import json
import re
import sys
import urllib.request

sys.path.insert(0, __import__("os").path.dirname(__file__))
from gen_meta import clean  # same wiki-markup cleaner

URL = "https://minecraft.wiki/api.php?action=parse&page=Game_rule&prop=wikitext&format=json&formatversion=2"
UA = {"User-Agent": "kpanel-gen-gamerules (github.com/kruser1337/kPanel)"}


def java_name(cell: str):
    if "{{el|je" in cell:
        m = re.search(r"\{\{el\|je[^}]*\}\}:\s*<code>\{\{va\|([a-z0-9_]+)\}\}", cell)
        return m.group(1) if m else None
    if "{{el|be" in cell:
        return None  # Bedrock only
    m = re.search(r"<code>\{\{va\|([a-z0-9_]+)\}\}", cell)
    return m.group(1) if m else None


def main():
    with urllib.request.urlopen(urllib.request.Request(URL, headers=UA), timeout=30) as r:
        wt = json.loads(r.read())["parse"]["wikitext"]
    body = wt.split("== List of game rules ==", 1)[1].split("== Additional behavior ==", 1)[0]
    rules = {}
    for section in re.split(r"\n=== ", body)[1:]:
        category = clean(section.split("===", 1)[0])
        if "bedrock" in category.lower() or category.lower() == "notes":
            continue
        table = section.split("{|", 1)[1].split("\n|}", 1)[0] if "{|" in section else ""
        for row in table.split("\n|-")[1:]:
            cells = [c.strip() for c in re.split(r"\n\|(?!-)", "\n" + row.strip("\n")) if c.strip()]
            if len(cells) < 4:
                continue
            name = java_name(cells[0])
            if not name:
                continue
            default = clean(cells[2].split("|", 1)[-1] if "style=" in cells[2] else cells[2])
            rules[name] = {
                "category": category,
                "desc": clean(cells[1])[:400],
                "default": default,
                "type": clean(cells[3]).lower(),
            }
    json.dump({"_source": "Minecraft Wiki, Game rule (CC BY-NC-SA 3.0)", "rules": rules},
              sys.stdout, indent=1, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    print(f"{len(rules)} Java rules", file=sys.stderr)


if __name__ == "__main__":
    main()
