"""Deterministic free-text query parser: maps TR/EN terms to structured filters."""
from __future__ import annotations

from dataclasses import dataclass, field

COLOR_ALIASES: dict[str, str] = {
    "siyah": "black", "black": "black",
    "gri": "gray", "gray": "gray", "grey": "gray",
    "beyaz": "white", "white": "white",
    "kırmızı": "red", "red": "red",
    "turuncu": "orange", "orange": "orange",
    "sarı": "yellow", "yellow": "yellow",
    "yeşil": "green", "green": "green",
    "camgöbeği": "cyan", "cyan": "cyan",
    "mavi": "blue", "blue": "blue",
    "mor": "purple", "purple": "purple",
    "pembe": "pink", "pink": "pink",
    "kahverengi": "brown", "brown": "brown",
}
# Canonical values are English, aligned with the detector's (English) class labels so
# search matches stored detections; both Turkish and English input keys are accepted.
GARMENT_ALIASES: dict[str, str] = {
    "tisort": "tshirt", "tişört": "tshirt", "tshirt": "tshirt", "t-shirt": "tshirt",
    "gomlek": "shirt", "gömlek": "shirt", "shirt": "shirt",
    "ceket": "jacket", "jacket": "jacket", "mont": "jacket",
    "hoodie": "hoodie", "kapüşonlu": "hoodie", "sweatshirt": "hoodie",
    "elbise": "dress", "dress": "dress",
}
ACCESSORY_ALIASES: dict[str, str] = {
    "sırt çantası": "backpack", "backpack": "backpack",
    "şemsiye": "umbrella", "umbrella": "umbrella",
    "çanta": "handbag", "handbag": "handbag", "bag": "handbag",
    "valiz": "suitcase", "suitcase": "suitcase",
}
HEIGHT_ALIASES: dict[str, str] = {
    "uzun": "tall", "tall": "tall", "kısa": "short", "short": "short",
    "orta": "medium", "medium": "medium", "orta boy": "medium", "orta boylu": "medium",
}
BUILD_ALIASES: dict[str, str] = {
    "ince": "slim", "slim": "slim", "geniş": "broad", "broad": "broad",
    "orta yapı": "medium", "orta yapılı": "medium", "medium build": "medium",
}
EVENT_TYPE_ALIASES: dict[str, str] = {
    "kişi": "PERSON", "person": "PERSON", "insan": "PERSON",
    "araç": "VEHICLE", "araba": "VEHICLE", "vehicle": "VEHICLE", "car": "VEHICLE",
    "hayvan": "ANIMAL", "animal": "ANIMAL",
    "hareket": "MOTION", "motion": "MOTION",
    "şimşek": "LIGHTNING", "lightning": "LIGHTNING",
    "meteor": "METEOR",
    "uydu": "SATELLITE", "satellite": "SATELLITE",
}
# Recognized but not yet available (3C/3E). Reported, never queried.
DEFERRED_TERMS: set[str] = {
    "koşma", "koşan", "running", "kavga", "fighting", "loiter", "loitering",
    "abandoned", "terk", "düşme", "falling", "yasak", "restricted", "kuyruk",
    "queue", "kalabalık", "crowd", "tailgating", "u-dönüşü", "u-turn",
}

_MULTIWORD: list[str] = sorted(
    {k for d in (COLOR_ALIASES, GARMENT_ALIASES, ACCESSORY_ALIASES, HEIGHT_ALIASES,
                 BUILD_ALIASES, EVENT_TYPE_ALIASES) for k in d if " " in k}
    | {t for t in DEFERRED_TERMS if " " in t},
    key=len, reverse=True,
)


@dataclass
class ForensicQuery:
    colors: list[str] = field(default_factory=list)
    clothing_types: list[str] = field(default_factory=list)
    accessories: list[str] = field(default_factory=list)
    height_bands: list[str] = field(default_factory=list)
    builds: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)
    deferred_terms: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    def has_attribute_filters(self) -> bool:
        return bool(self.colors or self.clothing_types or self.accessories
                    or self.height_bands or self.builds)

    def has_event_filters(self) -> bool:
        return bool(self.event_types)


def _add(target: list[str], value: str) -> None:
    if value not in target:
        target.append(value)


def _apply(q: ForensicQuery, term: str) -> bool:
    if term in COLOR_ALIASES:
        _add(q.colors, COLOR_ALIASES[term])
    elif term in GARMENT_ALIASES:
        _add(q.clothing_types, GARMENT_ALIASES[term])
    elif term in ACCESSORY_ALIASES:
        _add(q.accessories, ACCESSORY_ALIASES[term])
    elif term in HEIGHT_ALIASES:
        _add(q.height_bands, HEIGHT_ALIASES[term])
    elif term in BUILD_ALIASES:
        _add(q.builds, BUILD_ALIASES[term])
    elif term in EVENT_TYPE_ALIASES:
        _add(q.event_types, EVENT_TYPE_ALIASES[term])
    elif term in DEFERRED_TERMS:
        _add(q.deferred_terms, term)
    else:
        return False
    return True


def parse_query(text: str) -> ForensicQuery:
    q = ForensicQuery()
    remaining = " " + " ".join(text.lower().split()) + " "
    for key in _MULTIWORD:
        pad = " " + key + " "
        while pad in remaining:
            _apply(q, key)
            remaining = remaining.replace(pad, " ", 1)
    for token in remaining.split():
        if not _apply(q, token):
            _add(q.unmatched, token)
    return q
