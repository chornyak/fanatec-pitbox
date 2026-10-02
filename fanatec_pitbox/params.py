"""Tuning-menu parameter table for the hid-fanatecff `ftec_tuning` sysfs interface.

Ranges and steps mirror FTEC_TUNING_ATTRS in hid-ftec.h. The driver rounds values to its own
step (e.g. FOR 105 -> 100), so the UI snaps to the same steps and never sends values it would reject.
"""

from dataclasses import dataclass, field

# Product id -> (series, model) shown in the header.
PRODUCTS = {
    0x0020: ("CSL DD / DD PRO / CLUBSPORT DD", "DD WHEEL BASE"),
    0x0E03: ("CSL ELITE", "WHEEL BASE"),
    0x0005: ("CSL ELITE", "WHEEL BASE PS4"),
    0x0001: ("CLUBSPORT", "WHEEL BASE V2"),
    0x0004: ("CLUBSPORT", "WHEEL BASE V2.5"),
    0x0006: ("PODIUM", "WHEEL BASE DD1"),
    0x0007: ("PODIUM", "WHEEL BASE DD2"),
    0x0011: ("CSR ELITE", "WHEEL BASE"),
    0x0197: ("PORSCHE 911", "WHEEL BASE"),
}

# Driver's max_range per product; the max value means AUTO (see hid-ftec.c).
_SEN_MAX = {0x0001: 900, 0x0004: 900, 0x0011: 900, 0x0197: 900,
            0x0006: 2530, 0x0007: 2530, 0x0020: 2530}


def sen_max(product: int) -> int:
    return _SEN_MAX.get(product, 1090)


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    desc: str
    min: int = 0
    max: int = 100
    step: int = 1
    unit: str = ""
    specials: dict = field(default_factory=dict)  # value -> display text
    choices: tuple = ()  # ((value, label), ...) -> rendered as radio/combo instead of slider
    section: str = "left"  # left | right | other

    def snap(self, value: int) -> int:
        value = max(self.min, min(self.max, int(value)))
        return self.min + (value - self.min) // self.step * self.step  # floors, like the driver

    def fmt(self, value) -> str:
        if value is None:
            return "—"
        if value in self.specials:
            return self.specials[value]
        for v, text in self.choices:
            if v == value:
                return text
        return f"{value}{self.unit}"


_PARAMS = [
    Param("SEN", "Sensitivity",
          "Steering rotation from lock to lock. AUTO lets the game set it per car, which is best for "
          "modern sims. Use a fixed value only for games that don't control rotation themselves.",
          min=90, max=1090, step=10, unit="°", section="left"),
    Param("FF", "Force Feedback Strength",
          "Master force output, as a share of the base's maximum torque; everything else scales from it. "
          "Lower it if the wheel is too heavy or forces clip. Stronger bases are often run well below 100%.",
          unit="%", section="left"),
    Param("FFS", "Force Feedback Scale",
          "How game forces are mapped to motor torque. <b>Peak</b> emphasises big forces such as impacts "
          "and kerbs for more punch, at some cost to mid-range detail. <b>Linear</b> keeps the output "
          "proportional across the whole range. Peak is the more common choice.",
          min=0, max=1, choices=((0, "Linear"), (1, "Peak")), section="left"),
    Param("NDP", "Natural Damper",
          "Damping added by the base itself: it resists fast wheel movement. It stops oscillation (e.g. "
          "hands off on a straight), but too much dulls road feel. Typically 5–30%; weaker bases need less.",
          unit="%", specials={0: "OFF"}, section="left"),
    Param("NFR", "Natural Friction",
          "Constant friction, like a steering column without power assistance. Makes the wheel heavier "
          "and steadier around the centre. Usually OFF; 5–10% for older cars or a more planted feel.",
          unit="%", specials={0: "OFF"}, section="left"),
    Param("NIN", "Natural Inertia",
          "Simulated rim weight: the wheel keeps its momentum and resists changes of direction. Mainly "
          "for very light rims; usually OFF on direct drive, where it can feel sluggish.",
          unit="%", specials={0: "OFF"}, section="left"),
    Param("INT", "FFB Interpolation Filter",
          "Smooths the incoming force feedback signal. Lower is sharper and more detailed; higher is "
          "smoother but adds a little delay. Sims with clean FFB (ACC, AC) suit 0–1; grainier signals "
          "(iRacing) suit around 3–6.",
          min=0, max=20, section="right"),
    Param("FEI", "Force Effect Intensity",
          "Sharpness of short, high-frequency effects such as kerbs, bumps and road texture. 100 keeps "
          "full detail; 80–90 tames harsh or bumpy tracks.",
          step=10, section="right"),
    Param("FOR", "Force Effect Strength",
          "Scales the game's main force effects (constant and periodic forces), which carry most of a "
          "sim's FFB. Normally left at 100%; set overall strength with FF instead.",
          max=120, step=10, unit="%", section="right"),
    Param("SPR", "Spring Effect Strength",
          "Scales spring (self-centring) effects requested by the game. Keep at 100% for sims that use "
          "their own spring effect (e.g. iRacing); sims that put everything in their main signal (e.g. "
          "ACC) are often run with it OFF. Some games only use springs in menus.",
          max=120, step=10, unit="%", section="right"),
    Param("DPR", "Damper Effect Strength",
          "Scales damper effects requested by the game. As with SPR: 100% for sims that send damper "
          "effects (e.g. iRacing), often OFF for sims that don't rely on them (e.g. ACC).",
          max=120, step=10, unit="%", section="right"),
    Param("DRI", "Drift Mode",
          "Negative values make the wheel lighter and quicker to turn for drifting; positive values add "
          "resistance. OFF for normal driving.",
          min=-5, max=3, specials={0: "OFF"}, section="right"),
    Param("SHO", "Wheel Vibration Motor",
          "Strength of the rim's vibration motors, used for game rumble and the brake level warning. "
          "OFF disables them. Not every rim has motors.",
          step=10, unit="%", specials={0: "OFF"}, section="other"),
    Param("BLI", "Brake Level Indicator",
          "Brake pedal position at which the rim vibrates as a lock-up warning, e.g. 80 vibrates beyond "
          "80% brake. Works with pedals connected to the base. OFF disables it.",
          max=101, unit="%", specials={101: "OFF"}, section="other"),
    Param("brF", "Brake Force (load cell)",
          "Brake force of a load-cell brake pedal connected to the base: how hard you must press for "
          "100% brake. Higher needs more physical force, lower needs less. Around 75–85% is a common, "
          "realistic choice.",
          step=10, unit="%", section="other"),
    Param("ACP", "Analogue Paddles",
          "Function of the rim's analogue paddles. Read-only on rims with a mode selector switch.",
          min=1, max=4, choices=((1, "CbP"), (2, "CH"), (3, "Bt"), (4, "AnA")), section="other"),
    Param("FUL", "FullForce",
          "Strength of FullForce effects: extra high-frequency feedback (e.g. engine and gear shifts) in "
          "games that support Fanatec FullForce. Mainly a ClubSport DD feature; may have no effect on "
          "other bases.",
          unit="%", specials={0: "OFF"}, section="other"),
]

# Settings the base's tuning menu offers in Standard mode (Fanatec FAQ: SEN, FF, NDP, BRF).
STANDARD_KEYS = {"SEN", "FF", "NDP", "brF"}

ACP_LABELS = {1: "Clutch bite point", 2: "Clutch / Handbrake", 3: "Brake / Throttle", 4: "Analogue axes"}

# Keys stored in a profile, in the order they are applied. advanced_mode is deliberately excluded:
# switching the base to Standard mode and back can overwrite the active setup slot (seen on CSL DD).
PROFILE_KEYS = [p.key for p in _PARAMS]


def params_for(product: int) -> dict[str, Param]:
    """Parameter table adjusted to the device (SEN max/AUTO depends on the base)."""
    out = {}
    for p in _PARAMS:
        if p.key == "SEN":
            top = sen_max(product)
            p = Param(p.key, p.label, p.desc, p.min, top, p.step, p.unit,
                      {top: "AUTO"}, p.choices, p.section)
        out[p.key] = p
    return out
