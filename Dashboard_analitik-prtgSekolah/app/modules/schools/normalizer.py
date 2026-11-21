"""School detection, name normalization, and DIY region inference (extracted from existing web)."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

SCHOOL_KEYWORDS = {
    "sma", "sman", "smas", "smk", "smkn", "smks", "smp", "smpn", "smps",
    "sd", "sdn", "sds", "sdit", "mi", "min", "mis", "mts", "mtsn", "mtss",
    "ma", "man", "mas", "slb", "slbn", "tk", "paud", "sekolah", "madrasah",
}

SCHOOL_CODE_PREFIXES = {"SIM", "SIMS", "LM"}

_SCHOOL_CODE_RE = re.compile(r"^([A-Za-z]{1,5})(\d{2,6})(?=[\s_-]|$)")
_IP_IN_NAME_RE = re.compile(
    r"[\(\[\{]?\s*(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?![\d.])\s*[\)\]\}]?"
)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

DIY_REGION_ORDER = (
    ("bantul", "Bantul"),
    ("gunung_kidul", "Gunung Kidul"),
    ("kulon_progo", "Kulon Progo"),
    ("sleman", "Sleman"),
    ("khusus", "Khusus"),
    ("kota_jogja", "Kota Jogja"),
)
FALLBACK_REGION_CODE = "belum_dikelompokkan"

_REGION_GROUP_KEYWORDS = {
    "bantul": ("bantul",),
    "gunung_kidul": ("gunung kidul", "gunungkidul", "g kidul"),
    "kulon_progo": ("kulon progo", "kulonprogo"),
    "sleman": ("sleman",),
    "khusus": ("khusus",),
    "kota_jogja": (
        "kota jogja", "kota yogyakarta", "kota yogya",
        "kota jogjakarta", "kodya yogyakarta", "kodya jogja",
    ),
}

_REGION_NAME_KEYWORDS = {
    "bantul": (
        "bantul", "srandakan", "sanden", "kretek", "pundong", "bambanglipuro",
        "pandak", "imogiri", "dlingo", "banguntapan", "pleret", "piyungan",
        "sewon", "kasihan", "pajangan", "sedayu", "parangtritis", "jetis",
    ),
    "gunung_kidul": (
        "gunung kidul", "gunungkidul", "g kidul", "wonosari", "playen", "patuk",
        "semanu", "ponjong", "karangmojo", "tepus", "saptosari", "paliyan",
        "panggang", "rongkop", "girisubo", "gedangsari", "nglipar",
        "ngawen", "semin", "tanjungsari",
    ),
    "kulon_progo": (
        "kulon progo", "kulonprogo", "wates", "temon", "panjatan", "galur",
        "lendah", "sentolo", "pengasih", "kokap", "girimulyo", "nanggulan",
        "kalibawang", "samigaluh",
    ),
    "sleman": (
        "sleman", "gamping", "godean", "moyudan", "minggir", "seyegan",
        "mlati", "depok", "berbah", "prambanan", "kalasan", "ngemplak",
        "ngaglik", "tempel", "turi", "pakem", "cangkringan",
    ),
    "kota_jogja": (
        "yogyakarta", "jogja", "jogjakarta", "djogja", "yogya", "danurejan",
        "gedongtengen", "gondokusuman", "gondomanan", "kotagede", "kraton",
        "mantrijeron", "mergangsan", "ngampilan", "pakualaman", "tegalrejo",
        "umbulharjo", "wirobrajan", "jetisharjo", "gowongan", "purwodiningratan",
    ),
}


def clean_device_name(device_name: object) -> str:
    """Remove leading PRTG numbering and embedded IP addresses."""
    raw = str(device_name or "")
    cleaned = _IP_IN_NAME_RE.sub(" ", raw)
    cleaned = re.sub(r"^\s*\d+\.\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_|,/;:")
    return cleaned or re.sub(r"^\s*\d+\.\s*", "", raw).strip()


def display_school_name(device_name: object) -> str:
    """Human-readable school name without leading device codes (e.g. SIM063)."""
    cleaned = clean_device_name(device_name)
    stripped = re.sub(r"^([A-Za-z]{1,5}\d{2,6}[\s_-]+)+", "", cleaned).strip()
    return stripped or cleaned


def extract_school_code(device_name: object) -> Optional[str]:
    """Extract leading code such as SIM063 or LM000450 if present."""
    cleaned = clean_device_name(device_name)
    match = _SCHOOL_CODE_RE.match(cleaned)
    if match and match.group(1).upper() in SCHOOL_CODE_PREFIXES:
        return f"{match.group(1).upper()}{match.group(2)}"
    return None


def extract_ipv4(text: object) -> Optional[str]:
    """Extract first valid IPv4 address from text."""
    match = _IP_RE.search(str(text or ""))
    if not match:
        return None
    ip = match.group(0)
    parts = ip.split(".")
    if len(parts) == 4 and all(p.isdigit() and int(p) <= 255 for p in parts):
        return ip
    return None


def is_school_device(
    device_name: object,
    *,
    extra_keywords: Optional[set[str]] = None,
    extra_prefixes: Optional[set[str]] = None,
    excluded_keywords: Optional[set[str]] = None,
) -> bool:
    """Return True if PRTG device name identifies a school."""
    cleaned = clean_device_name(device_name)
    if not cleaned:
        return False

    words = set(re.findall(r"[a-z]+", cleaned.lower()))
    if excluded_keywords and (words & excluded_keywords):
        return False

    prefixes = SCHOOL_CODE_PREFIXES | (extra_prefixes or set())
    match = _SCHOOL_CODE_RE.match(cleaned)
    if match and match.group(1).upper() in prefixes:
        return True

    keywords = SCHOOL_KEYWORDS | (extra_keywords or set())
    return bool(words & keywords)


def normalize_school_name(name: object) -> str:
    """Normalize school name for multi-device deduplication (M1 audit enhancements).

    Examples:
    - "SMA N 1 TURI (103.210.35.72)" -> "SMA NEGERI 1 TURI"
    - "SIM063 SMA Negeri 01 Gamping"  -> "SMA NEGERI 1 GAMPING"
    - "SMKN 1 Gedangsari"             -> "SMK NEGERI 1 GEDANGSARI"
    """
    t = display_school_name(name).upper()
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    t = re.sub(r"\bSMKN\b", "SMK NEGERI", t)
    t = re.sub(r"\bSMAN\b", "SMA NEGERI", t)
    t = re.sub(r"\bSMPN\b", "SMP NEGERI", t)
    t = re.sub(r"\bSDN\b", "SD NEGERI", t)
    t = re.sub(r"\bSLBN\b", "SLB NEGERI", t)
    t = re.sub(r"\b(SMA|SMK|SMP|SD|SLB)\s+N\b", r"\1 NEGERI", t)
    t = re.sub(r"\bNEGERI\s+N\b", "NEGERI", t)
    # Remove leading zeros in school numbers ("01" -> "1")
    t = re.sub(r"\b0+(\d+)\b", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _ascii_lower(text: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


def infer_diy_region_code(
    group_path: object = "",
    device_name: object = "",
    sensor_names: object = "",
) -> str:
    """Infer canonical DIY region code from PRTG group path and school name."""
    group_norm = f" {_ascii_lower(group_path)} "
    for code, keywords in _REGION_GROUP_KEYWORDS.items():
        if any(f" {kw} " in group_norm for kw in keywords):
            return code

    combined = f" {_ascii_lower(device_name)} {_ascii_lower(sensor_names)} "
    for code, keywords in _REGION_NAME_KEYWORDS.items():
        if any(f" {kw} " in combined for kw in keywords):
            return code

    return FALLBACK_REGION_CODE
