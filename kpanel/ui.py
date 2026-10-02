"""kPanel's design system: tokens, component styles and icons, in one place.

The rules behind it are in DESIGN.md, next to this file. In short:

* Monochrome first. Ink on warm neutrals; colour appears only where it means
  something (online/warning/error), and always with an icon or a word, never
  colour alone.
* Colours are Radix Colors steps (MIT, see ui.LICENSE.txt), used by their
  documented role: 1-2 backgrounds, 3-5 component states, 6-8 borders,
  9-10 solids, 11-12 text. Light and dark are each picked, not inverted.
* System fonts, tabular figures, monospace for anything technical (keys,
  names, addresses). Nothing is fetched from another origin.
* Icons are Tabler Icons (MIT), inlined: 24px grid, 2px stroke, currentColor.
"""

# Tabler Icons outline paths, copied verbatim (https://tabler.io/icons).
ICONS = {
    "lock": '<path d="M5 13a2 2 0 0 1 2 -2h10a2 2 0 0 1 2 2v6a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-6"/><path d="M11 16a1 1 0 1 0 2 0a1 1 0 0 0 -2 0"/><path d="M8 11v-4a4 4 0 1 1 8 0v4"/>',
    "external-link": '<path d="M12 6h-6a2 2 0 0 0 -2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-6"/><path d="M11 13l9 -9"/><path d="M15 4h5v5"/>',
    "check": '<path d="M5 12l5 5l10 -10"/>',
    "alert-triangle": '<path d="M12 9v4"/><path d="M10.363 3.591l-8.106 13.534a1.914 1.914 0 0 0 1.636 2.871h16.214a1.914 1.914 0 0 0 1.636 -2.87l-8.106 -13.536a1.914 1.914 0 0 0 -3.274 0"/><path d="M12 16h.01"/>',
    "x": '<path d="M18 6l-12 12"/><path d="M6 6l12 12"/>',
    "circle-arrow-up": '<path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0"/><path d="M12 8l-4 4"/><path d="M12 8v8"/><path d="M16 12l-4 -4"/>',
    "coffee": '<path d="M3 14c.83 .642 2.077 1.017 3.5 1c1.423 .017 2.67 -.358 3.5 -1c.83 -.642 2.077 -1.017 3.5 -1c1.423 -.017 2.67 .358 3.5 1"/><path d="M8 3a2.4 2.4 0 0 0 -1 2a2.4 2.4 0 0 0 1 2"/><path d="M12 3a2.4 2.4 0 0 0 -1 2a2.4 2.4 0 0 0 1 2"/><path d="M3 10h14v5a6 6 0 0 1 -6 6h-2a6 6 0 0 1 -6 -6v-5"/><path d="M16.746 16.726a3 3 0 1 0 .252 -5.555"/>',
    "info-circle": '<path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0"/><path d="M12 9h.01"/><path d="M11 12h1v4h1"/>',
}


def icon(name, label=""):
    """An inline icon. With a label it is announced; without, it is decoration."""
    a11y = f'role=img aria-label="{label}"' if label else 'aria-hidden=true'
    return (f'<svg class=i {a11y} viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=2 '
            f'stroke-linecap=round stroke-linejoin=round>{ICONS[name]}</svg>')


# Tokens. Names say what a colour is for, never what it looks like, so a page
# never picks a hex value. Values: Radix sand (neutral), grass, amber, red, blue.
TOKENS = """
:root{color-scheme:light;
--page:#f9f9f8;--surface:#fdfdfc;--sunken:#f1f0ef;--hover:#e9e8e6;--selected:#e2e1de;
--line:#dad9d6;--line-strong:#cfceca;--line-hover:#bcbbb5;
--fg:#21201c;--muted:#63635e;--faint:#8d8d86;
--ink:#21201c;--ink-hover:#3b3a37;--on-ink:#fdfdfc;
--focus:#5eb1ef;--edited:#e6f4fe;--edited-line:#acd8fc;
--ok:#2a7e3b;--ok-bg:#e9f6e9;--ok-line:#b2ddb5;--ok-dot:#46a758;
--warn:#ab6400;--warn-bg:#fefbe9;--warn-line:#f3d673;--warn-dot:#ffc53d;
--bad:#ce2c31;--bad-bg:#fff7f7;--bad-line:#fdbdbe;--bad-dot:#e5484d;
--chart-line:#bcbbb5;--chart-now:#21201c}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;
--page:#111110;--surface:#191918;--sunken:#222221;--hover:#2a2a28;--selected:#31312e;
--line:#3b3a37;--line-strong:#494844;--line-hover:#62605b;
--fg:#eeeeec;--muted:#b5b3ad;--faint:#7c7b74;
--ink:#eeeeec;--ink-hover:#b5b3ad;--on-ink:#111110;
--focus:#2870bd;--edited:#0d2847;--edited-line:#104d87;
--ok:#71d083;--ok-bg:#1b2a1e;--ok-line:#2d5736;--ok-dot:#46a758;
--warn:#ffca16;--warn-bg:#1d180f;--warn-line:#5c3d05;--warn-dot:#ffc53d;
--bad:#ff9592;--bad-bg:#201314;--bad-line:#72232d;--bad-dot:#e5484d;
--chart-line:#62605b;--chart-now:#eeeeec}}
:root{
--font:system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans",Ubuntu,Cantarell,sans-serif;
--mono:ui-monospace,"SF Mono","Cascadia Mono","JetBrains Mono",Menlo,Consolas,"Liberation Mono",monospace;
--t-xs:12px;--t-sm:13px;--t-md:14px;--t-lg:16px;--t-xl:20px;--t-hero:48px;
--s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:24px;--s6:32px;
--r-sm:4px;--r:6px;--r-lg:8px;--control:32px;--control-sm:28px;--width:1040px}
"""

CSS = TOKENS + """
/* base */
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--fg);font:var(--t-md)/1.5 var(--font);
-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
a{color:inherit;text-decoration:underline;text-decoration-color:var(--line-hover);text-underline-offset:3px}
a:hover{text-decoration-color:currentColor}
code{font-family:var(--mono);font-size:var(--t-sm)}
.muted{color:var(--muted)}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.i{width:16px;height:16px;flex:none;vertical-align:-3px}
[title]{cursor:help}a[title],button[title]{cursor:pointer}
@media (prefers-reduced-motion:no-preference){a,button,input,select,.tile{transition:background-color .12s,border-color .12s,color .12s}}

/* app header: brand, tabs, page filter */
header{position:sticky;top:0;z-index:2;background:var(--surface);border-bottom:1px solid var(--line)}
.bar-in{max-width:var(--width);margin:0 auto;padding:0 var(--s4);display:flex;align-items:center;gap:var(--s5);min-height:52px;flex-wrap:wrap}
header h1{font-size:var(--t-lg);font-weight:600;letter-spacing:-.01em;margin:0;display:flex;align-items:center;gap:var(--s2)}
.logo{width:20px;height:20px;image-rendering:pixelated}
nav{display:flex;gap:var(--s1);flex-wrap:wrap;min-width:0;align-self:stretch}
nav a{display:inline-flex;align-items:center;gap:6px;padding:0 var(--s2);color:var(--muted);text-decoration:none;
font-weight:500;border-bottom:2px solid transparent;margin-bottom:-1px}
nav a:hover{color:var(--fg)}nav a.on{color:var(--fg);border-bottom-color:var(--ink)}
nav a .i{width:14px;height:14px;color:var(--faint)}
#q{max-width:220px;margin-left:auto;min-width:0}
main{max-width:var(--width);margin:0 auto;padding:var(--s5) var(--s4)}
footer{color:var(--muted);font-size:var(--t-xs);text-align:center;padding:var(--s5) var(--s4);
display:flex;flex-direction:column;gap:var(--s1)}
footer a{color:var(--muted)}footer a:hover{color:var(--fg)}
footer a.update{color:var(--fg);font-weight:600}
/* Inline, not inline-flex: a flex box takes its baseline from the icon and
   lifts the label above the rest of the line. */
footer a .i{width:14px;height:14px;vertical-align:-2px;margin-right:4px}

/* panel: the one container. Hairline border, no shadow */
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--s3) var(--s4);margin:0 0 var(--s4)}
h2{font-size:var(--t-sm);font-weight:600;margin:0 0 var(--s2);display:flex;align-items:center;gap:var(--s2);flex-wrap:wrap}
h2 .muted{font-weight:400}
.group{margin:0 0 var(--s5)}.group>h2{color:var(--muted);font-weight:500;margin-bottom:var(--s2)}

/* tables: rows divided by hairlines, keys in mono */
table{width:100%;border-collapse:collapse}
td{border-top:1px solid var(--line);padding:var(--s2) var(--s1);vertical-align:middle;height:44px}
tr:first-child td{border-top:0}td.k{width:50%}td.act{text-align:right;white-space:nowrap}
.k code[title]{text-decoration:underline dotted var(--line-hover);text-underline-offset:4px}
tr.changed td{background:var(--edited)}tr.changed td:first-child{box-shadow:inset 2px 0 var(--focus)}

/* controls */
input,select{width:100%;height:var(--control);padding:0 var(--s2);border:1px solid var(--line-strong);border-radius:var(--r);
background:var(--surface);color:var(--fg);font:inherit}
input:hover,select:hover{border-color:var(--line-hover)}
input:focus-visible,select:focus-visible{outline:2px solid var(--focus);outline-offset:-1px;border-color:transparent}
input:disabled,select:disabled{background:var(--sunken);color:var(--muted)}
input[type=checkbox]{width:16px;height:16px;accent-color:var(--ink);vertical-align:-3px;margin:0 var(--s2) 0 0}
::placeholder{color:var(--faint)}
button{display:inline-flex;align-items:center;gap:6px;height:var(--control);padding:0 var(--s3);border:1px solid var(--ink);
border-radius:var(--r);background:var(--ink);color:var(--on-ink);font:inherit;font-weight:500;cursor:pointer;white-space:nowrap}
button:hover{background:var(--ink-hover);border-color:var(--ink-hover)}
button:disabled{background:var(--sunken);border-color:var(--line);color:var(--faint);cursor:default}
button.sec{height:var(--control-sm);padding:0 10px;background:var(--surface);color:var(--fg);border-color:var(--line-strong);font-size:var(--t-sm)}
button.sec:hover{background:var(--hover);border-color:var(--line-hover)}
button.danger{color:var(--bad)}button.danger:hover{background:var(--bad-bg);border-color:var(--bad-line)}
.row{display:flex;gap:var(--s2);align-items:center}.row input{max-width:260px}
.bar{position:sticky;bottom:0;display:flex;justify-content:flex-end;gap:var(--s3);align-items:center;
background:var(--page);padding:var(--s3) 0;border-top:1px solid var(--line);margin-top:calc(-1 * var(--s1))}

/* badges and status: colour never travels without a word */
.badge,.pill{display:inline-flex;align-items:center;gap:4px;font-size:var(--t-xs);font-weight:500;line-height:18px;padding:0 6px;
border-radius:var(--r-sm);background:var(--sunken);color:var(--muted);border:1px solid var(--line);margin-left:6px;vertical-align:1px}
h2 .pill{margin:0}
.badge.drift{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}
.status{display:inline-flex;align-items:center;gap:6px;font-weight:500;font-size:var(--t-sm)}
.status::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.status.up{color:var(--ok)}.status.up::before{background:var(--ok-dot)}
.status.down{color:var(--bad)}.status.down::before{background:var(--bad-dot)}
.lock{color:var(--faint);display:inline-flex}

/* callouts: tinted surface, coloured border and icon; the text stays ink */
.note{display:flex;gap:var(--s2);align-items:flex-start}.note>.i{margin-top:2px}
.note.ok>.i{color:var(--ok)}
.card.warn{background:var(--warn-bg);border-color:var(--warn-line)}.card.warn>h2,.card.warn .note>.i{color:var(--warn)}
.card.err{background:var(--bad-bg);border-color:var(--bad-line)}.card.err .note>.i{color:var(--bad)}
.card.warn td{border-color:var(--warn-line)}

/* dashboard */
.herocard{display:flex;align-items:center;gap:var(--s5);flex-wrap:wrap;padding:var(--s5)}
.icon{width:64px;height:64px;border-radius:var(--r-lg);flex:none}.icon.px{image-rendering:pixelated}
.herotext{flex:1 1 0;min-width:0}.herospark{align-self:center;text-align:right}
.hero{display:flex;align-items:baseline;gap:var(--s3);flex-wrap:wrap}
.hero .num{font-size:var(--t-hero);font-weight:600;letter-spacing:-.02em;line-height:1}
.hero .of{font-size:var(--t-lg);color:var(--muted)}
.motd{margin-top:var(--s2)}
.tag{font-size:10px;font-weight:600;letter-spacing:.06em;color:var(--muted);border:1px solid var(--line);
border-radius:var(--r-sm);padding:1px 4px;vertical-align:1px;margin-right:6px}
.meta{display:flex;gap:var(--s2) var(--s4);flex-wrap:wrap;margin-top:var(--s2);color:var(--muted);font-size:var(--t-sm)}
.meta code{color:var(--fg)}
.who{display:flex;gap:6px;flex-wrap:wrap;margin-top:var(--s3)}
.who code{font-size:var(--t-xs);padding:2px 8px;border-radius:var(--r-sm);background:var(--sunken);border:1px solid var(--line)}
.sparkcap{font-size:var(--t-xs);color:var(--muted);margin-top:var(--s1)}
.spark{display:block;margin-top:var(--s2);max-width:100%;overflow:visible}
.spark .line{fill:none;stroke:var(--chart-line);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.spark .now{fill:var(--chart-now);stroke:var(--surface);stroke-width:2}
.spark circle{cursor:help}
/* stat strip: one panel split by hairlines (the 1px grid gap shows the line) */
.tiles{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;background:var(--line);border:1px solid var(--line);
border-radius:var(--r-lg);overflow:hidden}
@media (min-width:720px){.tiles{grid-template-columns:repeat(4,1fr)}}
.tile{background:var(--surface);padding:var(--s3) var(--s4) var(--s4);min-width:0}
.tile .l{font-size:var(--t-xs);color:var(--muted);font-weight:500;display:flex;align-items:center;gap:4px}
.tile .v{font-size:var(--t-xl);font-weight:600;letter-spacing:-.01em;margin-top:2px;word-break:break-word;display:flex;align-items:center;gap:6px}
.tile .s{font-size:var(--t-xs);color:var(--muted)}
.tile.is-warn .v .i{color:var(--warn)}.tile.is-bad .v .i{color:var(--bad)}
.tile.is-warn .s{color:var(--warn)}.tile.is-bad .s{color:var(--bad)}
a.tile{display:block;color:inherit;text-decoration:none}a.tile:hover{background:var(--sunken)}
a.tile .l .i{width:12px;height:12px}

/* log pane: always dark, it is a terminal (Radix sand dark) */
.term{background:#111110;color:#eeeeec;border:1px solid #2a2a28;border-radius:var(--r);padding:var(--s2) var(--s3);max-height:70vh;overflow:auto}
.term table{width:max-content;min-width:100%}
.term td{border-top:0;padding:0;white-space:pre;height:auto}
.term code{color:inherit;font-size:12.5px;line-height:1.6}
.term code.warn{color:#ffca16}.term code.err{color:#ff9592}
.term .muted{color:#7c7b74}

@media (max-width:640px){
.hero .num{font-size:40px}.icon{width:48px;height:48px}.herocard{padding:var(--s4);gap:var(--s4)}
.herospark{flex-basis:100%}.herospark .spark{width:100%}td.k{width:45%}main{padding:var(--s4) var(--s3)}
.bar-in{gap:var(--s2) var(--s4);padding-top:var(--s2)}nav{order:3;flex-basis:100%;overflow-x:auto;flex-wrap:nowrap;min-height:40px}
#q{max-width:none;flex:1 1 0}td.act{white-space:normal}}
"""
