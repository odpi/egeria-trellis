"""EA's pages in the three looks (Dark, Light, Paper): every text colour against the grounds it is drawn on, 4.5:1.

EA's Tailwind classes follow the look through ea_tailwind.js, which points each palette shade at a variable in
ea_looks.css, whose roles take their values from trellis-ui's themes.css per look (A-U3). This test resolves those
variables per look the way the browser does, then checks:

  - every text-on-ground pair EA's own markup puts on ONE element (`bg-violet-700 text-white`, a hover pair with a
    hover pair), with a translucent ground (`bg-red-950/20`) blended over the page;
  - every text colour EA uses, against the page (`slate-900`) and a card (`slate-800`).

A failure names the look, the pair and the ratio. Fix the role's value in ea_looks.css, or, when no value can
serve every place a shade is used, the class (and list it in the PR).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

STATIC = Path(__file__).resolve().parents[2] / "advisor" / "web" / "static"
THEMES = Path(__file__).resolve().parents[3] / "trellis-ui" / "trellis_ui" / "static" / "themes.css"
EA_LOOKS = STATIC / "ea_looks.css"
EA_TAILWIND = STATIC / "ea_tailwind.js"
SOURCES = [STATIC / "index.html", STATIC / "admin.html", *sorted(STATIC.glob("*.js"))]
LOOKS = ("dark", "light", "paper")
TEXT_MIN = 4.5

# ea_tailwind.js: which palettes borrow another's variables (checked against the file below).
ALIASES = {"violet": "violet", "cyan": "violet", "sky": "violet", "blue": "violet", "indigo": "violet",
           "emerald": "emerald", "green": "emerald", "teal": "emerald",
           "amber": "amber", "orange": "amber", "yellow": "amber",
           "red": "red", "rose": "red", "pink": "red", "slate": "slate"}
COLOR = re.compile(r"^(?:(hover|focus):)?(bg|text)-([a-z]+)(?:-(\d{2,3}))?(?:/(\d{1,3}))?$")


def _blocks(css: str) -> list[tuple[str, dict[str, str]]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        decls = dict((k.strip(), v.strip()) for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))
        if decls:
            out.append((" ".join(sel.split()), decls))
    return out


def _vars(look: str) -> dict[str, str]:
    """The custom properties on <html data-look=look>: :root blocks, then that look's (later wins, as in CSS
    with equal or higher specificity here)."""
    merged: dict[str, str] = {}
    for path in (THEMES, EA_LOOKS):
        for sel, decls in _blocks(path.read_text(encoding="utf-8")):
            parts = [s.strip() for s in sel.split(",")]
            if ":root" in parts and not any("data-look" in p for p in parts):
                merged.update(decls)
        for sel, decls in _blocks(path.read_text(encoding="utf-8")):
            if f':root[data-look="{look}"]' in [s.strip() for s in sel.split(",")]:
                merged.update(decls)
    return merged


def _resolve(value: str, env: dict[str, str], depth: int = 0) -> tuple[int, int, int]:
    assert depth < 20, f"var() loop at {value}"
    m = re.fullmatch(r"var\((--[\w-]+)(?:\s*,\s*(.+))?\)", value.strip())
    if m:
        name, fallback = m.groups()
        if name in env:
            return _resolve(env[name], env, depth + 1)
        assert fallback, f"{name} is not defined"
        return _resolve(fallback, env, depth + 1)
    nums = [int(n) for n in re.findall(r"\d+", value)][:3]
    assert len(nums) == 3, f"not a channel triple: {value!r}"
    return nums[0], nums[1], nums[2]


def _lum(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _ratio(a, b) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _color(cls: str, env) -> tuple[tuple[int, int, int], float]:
    m = COLOR.match(cls)
    assert m, cls
    _state, _kind, palette, shade, alpha = m.groups()
    if palette == "white":
        rgb = (255, 255, 255)
    elif palette == "black":
        rgb = (0, 0, 0)
    else:
        rgb = _resolve(f"var(--ea-{ALIASES[palette]}-{shade})", env)
    return rgb, (int(alpha) / 100 if alpha else 1.0)


def _over(rgb, alpha, ground):
    return tuple(round(c * alpha + g * (1 - alpha)) for c, g in zip(rgb, ground))


def _classes_in(text: str):
    for m in re.finditer(r'class(?:Name)?\s*=\s*"([^"]*)"|class(?:Name)?\s*=\s*\'([^\']*)\'|`([^`]*)`', text):
        yield (m.group(1) or m.group(2) or m.group(3) or "").split()


def _ours(c: str) -> bool:
    m = COLOR.match(c)
    return bool(m) and m.group(1) in (None, "hover") and (m.group(3) in ALIASES or m.group(3) in ("white", "black")) \
        and (m.group(4) is not None or m.group(3) in ("white", "black"))


def _pairs():
    pairs, texts = set(), set()
    for path in SOURCES:
        for classes in _classes_in(path.read_text(encoding="utf-8")):
            ours = [c for c in classes if _ours(c)]
            for state in (None, "hover"):
                bgs = [c for c in ours if COLOR.match(c).group(2) == "bg" and COLOR.match(c).group(1) == state]
                txs = [c for c in ours if COLOR.match(c).group(2) == "text" and COLOR.match(c).group(1) == state]
                texts.update(t for t in txs if state is None)
                pairs.update((b, t) for b in bgs for t in txs)
    return sorted(pairs), sorted(texts)


def test_ea_tailwind_borrows_the_palettes_this_test_assumes():
    js = EA_TAILWIND.read_text(encoding="utf-8")
    for palette, role in ALIASES.items():
        if palette == role:
            continue
        group = {"violet": "accent", "emerald": "ok", "amber": "warn", "red": "danger"}[role]
        assert re.search(rf"\b{palette}: {group}\b", js), f"{palette} should borrow {role}'s variables ({group})"


def test_every_shade_ea_uses_has_a_value_in_every_look():
    used = set()
    for path in SOURCES:
        used.update(m.group(0) for m in re.finditer(r"\b(?:bg|text|border|ring|divide)-([a-z]+)-(\d{2,3})\b",
                                                      path.read_text(encoding="utf-8"))
                    if m.group(1) in ALIASES)
    for look in LOOKS:
        env = _vars(look)
        for cls in sorted(used):
            palette, shade = re.match(r"\w+-([a-z]+)-(\d+)", cls).groups()
            _resolve(f"var(--ea-{ALIASES[palette]}-{shade})", env)   # raises if it does not resolve


@pytest.mark.parametrize("look", LOOKS)
def test_text_on_its_own_ground(look):
    env = _vars(look)
    page = _resolve("var(--ea-slate-900)", env)
    low = []
    pairs, _texts = _pairs()
    for bg, tx in pairs:
        brgb, balpha = _color(bg, env)
        ground = _over(brgb, balpha, page)
        trgb, talpha = _color(tx, env)
        r = _ratio(_over(trgb, talpha, ground), ground)
        if r < TEXT_MIN:
            low.append(f"{bg} + {tx}: {r:.2f}")
    assert not low, f"{look}: text under {TEXT_MIN}:1 on its own ground:\n  " + "\n  ".join(low)


@pytest.mark.parametrize("look", LOOKS)
def test_every_text_colour_on_the_page_and_a_card(look):
    env = _vars(look)
    grounds = {"page (slate-900)": _resolve("var(--ea-slate-900)", env),
               "card (slate-800)": _resolve("var(--ea-slate-800)", env)}
    low = []
    _pairs_, texts = _pairs()
    for tx in texts:
        if tx.startswith("text-white"):
            continue   # white is only ever on a solid (checked above), never on the page
        trgb, talpha = _color(tx, env)
        for name, ground in grounds.items():
            r = _ratio(_over(trgb, talpha, ground), ground)
            if r < TEXT_MIN:
                low.append(f"{tx} on {name}: {r:.2f}")
    assert not low, f"{look}: text under {TEXT_MIN}:1:\n  " + "\n  ".join(low)


def _css_colour(value: str, env, page):
    """`rgb(var(--ea-x))`, `rgb(var(--ea-x) / .4)` or `#fff` -> an opaque RGB over the page."""
    value = value.strip()
    if value in ("#fff", "#ffffff"):
        return (255, 255, 255)
    m = re.fullmatch(r"rgb\((var\(--[\w-]+\))\s*(?:/\s*([\d.]+))?\)", value)
    assert m, f"an override colour this test cannot read: {value!r}"
    rgb = _resolve(m.group(1), env)
    return _over(rgb, float(m.group(2)) if m.group(2) else 1.0, page)


@pytest.mark.parametrize("look", LOOKS)
def test_the_page_style_overrides_keep_their_text_readable(look):
    """ea_looks.css's overrides of the pages' own <style> rules: wherever a rule sets both a ground and a text
    colour, the text reads on it."""
    env = _vars(look)
    page = _resolve("var(--ea-slate-900)", env)
    css = re.sub(r"/\*.*?\*/", "", EA_LOOKS.read_text(encoding="utf-8"), flags=re.S)
    low, checked = [], 0
    for sel, body in re.findall(r"(:root [^{}]+)\{([^{}]*)\}", css):
        bg = re.search(r"background:\s*([^;]+);", body)
        fg = re.search(r"(?<!-)color:\s*([^;]+);", body)
        if not (bg and fg):
            continue
        ground = _css_colour(bg.group(1), env, page)
        r = _ratio(_css_colour(fg.group(1), env, ground), ground)
        checked += 1
        if r < TEXT_MIN:
            low.append(f"{' '.join(sel.split())}: {r:.2f}")
    assert checked >= 20, f"only {checked} override rules read: the parser has drifted from ea_looks.css"
    assert not low, f"{look}: override text under {TEXT_MIN}:1:\n  " + "\n  ".join(low)
