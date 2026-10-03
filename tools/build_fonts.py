#!/usr/bin/env python3
"""Build the bundled fonts: static weights of Noto Sans / Noto Sans Mono, subset to the characters the app uses.

Qt doesn't select weights from variable fonts reliably, and many systems lack Noto Sans SemiBold (600), which the
design uses. Source: the variable fonts from https://github.com/google/fonts (ofl/notosans, ofl/notosansmono),
SIL Open Font License 1.1 (fanatec_pitbox/fonts/OFL.txt).

    python tools/build_fonts.py NotoSans[wdth,wght].ttf NotoSansMono[wdth,wght].ttf
"""
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

OUT = Path(__file__).resolve().parent.parent / "fanatec_pitbox" / "fonts"
WEIGHTS = {"Noto Sans": {400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold"},
           "Noto Sans Mono": {400: "Regular", 500: "Medium"}}
# Latin text, general punctuation, arrows, degree/minus/ellipsis/middle dot, box and check marks
UNICODES = [*range(0x20, 0x250), *range(0x2000, 0x2070), *range(0x2190, 0x2200), 0x2212, 0x25B8, 0x25BE, 0x25CF,
            0x2713, 0x2715, 0x2026]


def build(source: Path, family: str):
    for weight, style in WEIGHTS[family].items():
        font = instancer.instantiateVariableFont(TTFont(source), {"wght": weight, "wdth": 100},
                                                 updateFontNames=True)
        options = subset.Options()
        options.name_IDs = ["*"]
        options.layout_features = ["*"]
        sub = subset.Subsetter(options)
        sub.populate(unicodes=UNICODES)
        sub.subset(font)
        out = OUT / f"{family.replace(' ', '')}-{style}.ttf"
        font.save(out)
        print(f"{out.name}: {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    build(Path(sys.argv[1]), "Noto Sans")
    build(Path(sys.argv[2]), "Noto Sans Mono")
