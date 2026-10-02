"""Fanatec's recommended baseline settings per wheel base.

Source: Fanatec's official 'iRacing (PC) - Fanatec Recommended Settings' post on the Fanatec Community forum
(March 2019). Its values are close to Fanatec's recommendations for other sims and make a good general starting
point. Only the tuning values the post gives are written; anything it leaves to "user preference" (BLI, BRF) or
doesn't mention is left unchanged. FFS: Peak = 1, Linear = 0; "Off" = 0.
"""

from dataclasses import dataclass

SOURCE = "https://forum.fanatec.com/topic/541-iracing-pc-fanatec-recommended-settings/"
SOURCE_TITLE = "Fanatec Recommended Settings (Fanatec Community, 2019)"


@dataclass(frozen=True)
class Baseline:
    base: str                 # wheel base name as used in the post
    models: tuple             # Model keys (params.MODELS) this entry is offered for
    values: dict
    note: str = ""            # shown for models the post doesn't name; "{label}" is the model's name
    named: tuple = ()         # models the post names explicitly (no note needed)


_PODIUM = dict(SEN=1080, FF=90, FFS=0, NDP=16, NFR=2, NIN=8, INT=3, FEI=100, FOR=100, SPR=100, DPR=100, SHO=100)

BASELINES = (
    Baseline("CSL DD", ("csl-dd", "csl-dd-boost", "gt-dd-pro", "gt-dd-pro-boost", "clubsport-dd"),
             dict(SEN=1080, FF=100, FFS=1, NDP=15, NFR=0, NIN=0, INT=6, FEI=100, FOR=100, SPR=100, DPR=100,
                  SHO=100),
             "Fanatec's post covers the CSL DD; the same values are used for the {label}.",
             ("csl-dd", "csl-dd-boost")),
    Baseline("Podium DD1", ("podium-dd1",), _PODIUM),
    Baseline("Podium DD2", ("podium-dd2",), _PODIUM),
    Baseline("ClubSport V2", ("clubsport-v2",), dict(SEN=900, FF=100, SHO=100, DRI=2, FOR=100, SPR=100, DPR=100)),
    Baseline("ClubSport V2.5", ("clubsport-v25",),
             dict(SEN=900, FF=100, SHO=100, DRI=-2, FOR=100, SPR=100, DPR=100, FEI=100)),
    Baseline("CSL Elite (original)", ("csl-elite",),
             dict(SEN=1080, FF=100, SHO=100, DRI=3, FOR=100, SPR=100, DPR=100, FEI=100)),
    Baseline("CSL Elite V1.1 / Elite+ / PS4", ("csl-elite-v11", "csl-elite-ps4"),
             dict(SEN=1080, FF=100, SHO=100, DRI=-3, FOR=100, SPR=100, DPR=100, FEI=100)),
)


def baseline_for(model_key: str) -> Baseline | None:
    return next((b for b in BASELINES if model_key in b.models), None)


def available(models) -> bool:
    """Whether any of these models (params.Model) has a recommended baseline."""
    return any(baseline_for(m.key) for m in models)
