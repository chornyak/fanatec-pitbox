"""Tuning-menu parameter table for the hid-fanatecff `ftec_tuning` sysfs interface.

Ranges and steps mirror FTEC_TUNING_ATTRS in hid-ftec.h. The driver rounds values to its own
step (e.g. FOR 105 -> 100), so the UI snaps to the same steps and never sends values it would reject.
"""

from dataclasses import dataclass, field

@dataclass(frozen=True)
class Model:
    """A wheel base model. Several models can share one USB product id, so the user picks theirs once."""
    key: str
    series: str       # small line in the header, as in Fanatec's app ("CSL")
    name: str         # title in the header ("DD Wheel Base")
    label: str        # in lists ("CSL DD with Boost Kit (8 Nm)")
    torque: int | None = None  # peak torque in Nm, where it matters for recommendations
    boost: bool = False


# USB product id -> models using it (the driver README lists 0020 as CSL DD / DD Pro / ClubSport DD).
MODELS = {
    0x0020: (Model("csl-dd", "CSL", "DD Wheel Base", "CSL DD (5 Nm)", 5),
             Model("csl-dd-boost", "CSL", "DD Wheel Base", "CSL DD with Boost Kit (8 Nm)", 8, boost=True),
             Model("gt-dd-pro", "GT", "DD Pro Wheel Base", "GT DD Pro (5 Nm)", 5),
             Model("gt-dd-pro-boost", "GT", "DD Pro Wheel Base", "GT DD Pro with Boost Kit (8 Nm)", 8, boost=True),
             Model("clubsport-dd", "CLUBSPORT", "DD Wheel Base", "ClubSport DD (12 Nm)", 12)),
    0x0E03: (Model("csl-elite", "CSL ELITE", "Wheel Base", "CSL Elite (original)"),
             Model("csl-elite-v11", "CSL ELITE", "Wheel Base V1.1 / +", "CSL Elite V1.1 / CSL Elite+")),
    0x0005: (Model("csl-elite-ps4", "CSL ELITE", "Wheel Base PS4", "CSL Elite PS4"),),
    0x0001: (Model("clubsport-v2", "CLUBSPORT", "Wheel Base V2", "ClubSport V2"),),
    0x0004: (Model("clubsport-v25", "CLUBSPORT", "Wheel Base V2.5", "ClubSport V2.5"),),
    0x0006: (Model("podium-dd1", "PODIUM", "Wheel Base DD1", "Podium DD1", 20),),
    0x0007: (Model("podium-dd2", "PODIUM", "Wheel Base DD2", "Podium DD2", 25),),
    0x0011: (Model("csr-elite", "CSR ELITE", "Wheel Base", "CSR Elite"),),
    0x0197: (Model("porsche-911", "PORSCHE 911", "Wheel Base", "Porsche 911 Turbo S / GT3 RS"),),
}


def models_for(product: int) -> tuple:
    return MODELS.get(product, (Model(f"usb-{product:04x}", "FANATEC", f"Wheel Base {product:04X}",
                                      f"Fanatec wheel base {product:04X}"),))


def find_model(product: int, key: str | None) -> Model | None:
    """The model the user chose, or the only model for this id; None if they still have to choose."""
    models = models_for(product)
    if len(models) == 1:
        return models[0]
    return next((m for m in models if m.key == key), None)


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
    group: str = "steering"  # panel on the Tuning tab, see GROUPS

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
          min=90, max=1090, step=10, unit="°", group="steering"),
    Param("FF", "Force Feedback Strength",
          "Master force output, as a share of the base's maximum torque; everything else scales from it. "
          "Lower it if the wheel is too heavy or forces clip. Stronger bases are often run well below 100%.",
          unit="%", group="steering"),
    Param("FFS", "Force Feedback Scale",
          "How game forces are mapped to motor torque. <b>Peak</b> emphasises big forces such as impacts "
          "and kerbs for more punch, at some cost to mid-range detail. <b>Linear</b> keeps the output "
          "proportional across the whole range. Peak is the more common choice.",
          min=0, max=1, choices=((0, "Linear"), (1, "Peak")), group="steering"),
    Param("NDP", "Natural Damper",
          "Damping added by the base itself: it resists fast wheel movement. It stops oscillation (e.g. "
          "hands off on a straight), but too much dulls road feel. Typically 5–30%; weaker bases need less.",
          unit="%", specials={0: "OFF"}, group="feel"),
    Param("NFR", "Natural Friction",
          "Constant friction, like a steering column without power assistance. Makes the wheel heavier "
          "and steadier around the centre. Usually OFF; 5–10% for older cars or a more planted feel.",
          unit="%", specials={0: "OFF"}, group="feel"),
    Param("NIN", "Natural Inertia",
          "Simulated rim weight: the wheel keeps its momentum and resists changes of direction. Mainly "
          "for very light rims; usually OFF on direct drive, where it can feel sluggish.",
          unit="%", specials={0: "OFF"}, group="feel"),
    Param("INT", "FFB Interpolation Filter",
          "Smooths the incoming force feedback signal. Lower is sharper and more detailed; higher is "
          "smoother but adds a little delay. Sims with clean FFB (ACC, AC) suit 0–1; grainier signals "
          "(iRacing) suit around 3–6.",
          min=0, max=20, group="effects"),
    Param("FEI", "Force Effect Intensity",
          "Sharpness of short, high-frequency effects such as kerbs, bumps and road texture. 100 keeps "
          "full detail; 80–90 tames harsh or bumpy tracks.",
          step=10, group="effects"),
    Param("FOR", "Force Effect Strength",
          "Scales the game's main force effects (constant and periodic forces), which carry most of a "
          "sim's FFB. Normally left at 100%; set overall strength with FF instead.",
          max=120, step=10, unit="%", group="effects"),
    Param("SPR", "Spring Effect Strength",
          "Scales spring (self-centring) effects requested by the game. Keep at 100% for sims that use "
          "their own spring effect (e.g. iRacing); sims that put everything in their main signal (e.g. "
          "ACC) are often run with it OFF. Some games only use springs in menus.",
          max=120, step=10, unit="%", group="effects"),
    Param("DPR", "Damper Effect Strength",
          "Scales damper effects requested by the game. As with SPR: 100% for sims that send damper "
          "effects (e.g. iRacing), often OFF for sims that don't rely on them (e.g. ACC).",
          max=120, step=10, unit="%", group="effects"),
    Param("DRI", "Drift Mode",
          "Negative values make the wheel lighter and quicker to turn for drifting; positive values add "
          "resistance. OFF for normal driving.",
          min=-5, max=3, specials={0: "OFF"}, group="steering"),
    Param("SHO", "Wheel Vibration Motor",
          "Strength of the rim's vibration motors, used for game rumble and the brake level warning. "
          "OFF disables them. Not every rim has motors.",
          step=10, unit="%", specials={0: "OFF"}, group="rim"),
    Param("BLI", "Brake Level Indicator",
          "Brake pedal position at which the rim vibrates as a lock-up warning, e.g. 80 vibrates beyond "
          "80% brake. Works with pedals connected to the base. OFF disables it.",
          max=101, unit="%", specials={101: "OFF"}, group="rim"),
    Param("brF", "Brake Force (load cell)",
          "Brake force of a load-cell brake pedal connected to the base: how hard you must press for "
          "100% brake. Higher needs more physical force, lower needs less. Around 75–85% is a common, "
          "realistic choice.",
          step=10, unit="%", group="rim"),
    Param("ACP", "Analogue Paddles",
          "Function of the rim's analogue paddles. Read-only on rims with a mode selector switch.",
          min=1, max=4, choices=((1, "CbP"), (2, "CH"), (3, "Bt"), (4, "AnA")), group="rim"),
    Param("FUL", "FullForce",
          "Strength of FullForce effects: extra high-frequency feedback (e.g. engine and gear shifts) in "
          "games that support Fanatec FullForce. Mainly a ClubSport DD feature; may have no effect on "
          "other bases.",
          unit="%", specials={0: "OFF"}, group="rim"),
]

# Settings the base's tuning menu offers in Standard mode (Fanatec FAQ: SEN, FF, NDP, BRF).
STANDARD_KEYS = {"SEN", "FF", "NDP", "brF"}

ACP_LABELS = {1: "Clutch bite point", 2: "Clutch / Handbrake", 3: "Brake / Throttle", 4: "Analogue axes"}

# Keys stored in a profile, in the order they are applied. advanced_mode is deliberately excluded:
# switching the base to Standard mode and back can overwrite the active setup slot (seen on CSL DD).
PROFILE_KEYS = [p.key for p in _PARAMS]


# Tuning tab panels: (key, title), in two columns (left, right).
GROUPS = ((("steering", "STEERING & FORCE"), ("feel", "BASE FEEL")),
          (("effects", "GAME EFFECTS"), ("rim", "RIM & PEDALS")))


def params_for(product: int) -> dict[str, Param]:
    """Parameter table adjusted to the device (SEN max/AUTO depends on the base)."""
    out = {}
    for p in _PARAMS:
        if p.key == "SEN":
            top = sen_max(product)
            p = Param(p.key, p.label, p.desc, p.min, top, p.step, p.unit,
                      {top: "AUTO"}, p.choices, p.group)
        out[p.key] = p
    return out
