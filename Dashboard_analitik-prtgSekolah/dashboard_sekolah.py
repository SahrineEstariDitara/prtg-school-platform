import json
import os
import re
import threading
import time

import requests
import tab_prtg  # file tab_prtg.py harus satu folder dengan file ini
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    Response,
    render_template_string,
    request,
    jsonify,
    session,
    redirect,
    url_for,
    send_file,
)

from prtg_sekolah import (
    get_down_devices,
    get_online_devices,
    build_down_device_details,
    group_down_devices,
    send_wa,
    PRTG_SERVER,
    PRTG_USERNAME,
    PRTG_PASSHASH,
    PRTG_TIMEOUT,
    VERIFY_SSL,
)

try:
    from prtg_sekolah import get_prtg_auth_params
except ImportError:
    # prtg_sekolah.py versi lama: hanya mendukung passhash
    def get_prtg_auth_params():
        return {"username": PRTG_USERNAME, "passhash": PRTG_PASSHASH}


from app import init_db_on_app, register_api_blueprints
from app.common.logging import init_request_logging
from app.config import settings

# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)


# ============================================================
# KONFIGURASI LOGIN (dimuat dari .env melalui app.config.settings)
# ============================================================

DASHBOARD_USERNAME = settings.dashboard_username
DASHBOARD_PASSWORD = settings.dashboard_password

app.secret_key = settings.flask_secret_key

init_request_logging(app)
init_db_on_app(app)
register_api_blueprints(app)


# ============================================================
# FILE RIWAYAT (untuk analitik tren)
# ============================================================

RIWAYAT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "riwayat_sekolah.json"
)

# File baru (v2): kategori online sekarang < 24 jam, 24 - 48 jam, > 48 jam.
RIWAYAT_ONLINE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "riwayat_online_v2.json"
)

MAKS_RIWAYAT = 300

# Versi pengelompokan wilayah pada riwayat. Naikkan angkanya kalau cara
# mengelompokkan wilayah diubah, supaya riwayat lama (yang salah) diabaikan
# oleh grafik per wilayah.
RIWAYAT_VERSI = 3

# Titik riwayat dicatat paling sering sekali per sekian detik. Klik tab
# wilayah atau refresh cepat tidak menambah titik baru, hanya memperbarui
# titik terakhir dengan data terkini.
JEDA_RIWAYAT = 50

# Riwayat gangguan PER SEKOLAH (untuk resume + unduh PDF/Excel).
GANGGUAN_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "gangguan_sekolah.json"
)

MAKS_GANGGUAN = 20000

# Pencatat di latar belakang: mengecek PRTG tiap sekian detik walau
# tidak ada yang membuka dashboard, supaya riwayat tidak bolong.
INTERVAL_PENCATAT = settings.interval_pencatat


# ============================================================
# PENULISAN FILE AMAN + PENGATURAN MANUAL
# ============================================================

PENGATURAN_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "pengaturan_sekolah.json"
)

_kunci_tulis = threading.Lock()
_kunci_riwayat = threading.Lock()
_kunci_riwayat_online = threading.Lock()


def tulis_json_aman(path, data):
    """
    Tulis JSON secara atomik (file sementara lalu diganti), supaya file
    lama tidak rusak/terpotong walau proses mati atau dua proses menulis
    bersamaan.
    """

    with _kunci_tulis:
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
            return True
        except OSError as e:
            print("Gagal menyimpan", os.path.basename(path), ":", e)
            return False


_cache_pengaturan = {"mtime": None, "data": None}


def baca_pengaturan():
    """
    pengaturan_sekolah.json (opsional):
      {"wilayah": {"#21755": "Bantul", "SIM063": "Sleman"}}
    Kunci wilayah = ID sekolah (kode atau #ID PRTG).
    """

    try:
        mtime = os.path.getmtime(PENGATURAN_FILE)
    except OSError:
        mtime = None

    if _cache_pengaturan["data"] is not None and _cache_pengaturan["mtime"] == mtime:
        return _cache_pengaturan["data"]

    data = {"wilayah": {}, "kata_sekolah": [], "kode_sekolah": [], "kata_bukan_sekolah": []}

    try:
        with open(PENGATURAN_FILE, "r", encoding="utf-8") as f:
            isi = json.load(f)
        if isinstance(isi, dict):
            if isinstance(isi.get("wilayah"), dict):
                data["wilayah"] = isi["wilayah"]
            if isinstance(isi.get("kata_sekolah"), list):
                data["kata_sekolah"] = isi["kata_sekolah"]
            for kunci in ("kode_sekolah", "kata_bukan_sekolah"):
                if isinstance(isi.get(kunci), list):
                    data[kunci] = isi[kunci]
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        pass

    _cache_pengaturan["mtime"] = mtime
    _cache_pengaturan["data"] = data

    return data


# ============================================================
# LOGIN DECORATOR
# ============================================================

def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped


def api_login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify(
                sukses=False,
                error="Sesi login sudah habis. Silakan login ulang.",
            ), 401
        return view_func(*args, **kwargs)
    return wrapped


# ============================================================
# DEVICE SEKOLAH
# ============================================================

# Kata penanda sekolah. Dicocokkan sebagai KATA UTUH (huruf besar/kecil
# tidak berpengaruh); angka di belakang kata diabaikan, jadi "SMAN1" tetap
# terbaca sebagai "sman". Tambahan lewat pengaturan_sekolah.json:
#   {"kata_sekolah": ["pkbm"], "kode_sekolah": ["ABC"], "kata_bukan_sekolah": ["router"]}
KATA_SEKOLAH = {
    "sma", "sman", "smas", "smk", "smkn", "smks", "smp", "smpn", "smps",
    "sd", "sdn", "sds", "sdit", "mi", "min", "mis", "mts", "mtsn", "mtss",
    "ma", "man", "mas", "slb", "slbn", "tk", "paud", "sekolah", "madrasah",
}

# Awalan kode device sekolah, mis. "SIM063", "SIMS039", "LM00".
KODE_SEKOLAH = {"SIM", "SIMS", "LM"}

_KODE_RE = re.compile(r"^([A-Za-z]{1,5})\d{2,6}(?=[\s_-]|$)")


def is_sekolah(device_name):
    """
    True hanya kalau device ini SEKOLAH, bukan perangkat lain di PRTG:
    - nama diawali kode sekolah (SIM063, SIMS039, LM00, ...), ATAU
    - nama mengandung kata penanda sekolah (SMA, SMK, SMP, SD, MI, MTs, MA,
      SLB, TK, Sekolah, Madrasah, ...).
    """

    nama = nama_bersih(device_name)

    if not nama:
        return False

    pengaturan = baca_pengaturan()

    kata_sekolah = KATA_SEKOLAH | {str(k).strip().lower() for k in pengaturan.get("kata_sekolah", []) if str(k).strip()}
    kode_sekolah = KODE_SEKOLAH | {str(k).strip().upper() for k in pengaturan.get("kode_sekolah", []) if str(k).strip()}
    kata_bukan = {str(k).strip().lower() for k in pengaturan.get("kata_bukan_sekolah", []) if str(k).strip()}

    kata = set(re.findall(r"[a-z]+", nama.lower()))

    if kata & kata_bukan:
        return False

    m = _KODE_RE.match(nama)

    if m and m.group(1).upper() in kode_sekolah:
        return True

    return bool(kata & kata_sekolah)


def filter_sekolah(sensors):

    return [
        s for s in sensors
        if is_sekolah(s.get("device", ""))
    ]


# ============================================================
# HELPER TAMPILAN
# ============================================================

def ringkas_groups(groups):

    return (
        sorted(groups.get("< 24 jam", [])),
        sorted(groups.get("24 - 48 jam", [])),
        sorted(groups.get("> 48 jam", [])),
    )


def format_lama_down(hours):

    try:
        total = int(float(hours) * 60)
    except (TypeError, ValueError):
        return "-"

    days, sisa = divmod(total, 24 * 60)
    jam, menit = divmod(sisa, 60)

    if total < 48 * 60:
        total_jam = total // 60
        if total_jam > 0:
            return f"{total_jam} h {menit} m"
        return f"{menit} m"

    if jam == 0 and menit == 0:
        return f"{days} d"

    return f"{days} d {jam} h {menit} m"


# Pola alamat IPv4 (boleh diikuti :port), mis. 10.20.5.17 atau 10.20.5.17:8080
IP_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?![\d.])")

# IP beserta tanda kurung/kurung siku di sekelilingnya, untuk dibuang dari nama
IP_DI_NAMA_RE = re.compile(
    r"[\(\[\{]?\s*(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?![\d.])\s*[\)\]\}]?"
)


def nama_bersih(device_name):
    """
    Nama device tanpa nomor urut PRTG di depan ("140. ") dan TANPA alamat IP
    ("SIM063 SMA N 1 Bantul 10.20.5.17" -> "SIM063 SMA N 1 Bantul").
    """

    asli = str(device_name or "")

    t = IP_DI_NAMA_RE.sub(" ", asli)
    t = re.sub(r"^\s*\d+\.\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -_|,/;:")

    if t:
        return t

    # Nama hanya berisi IP: pakai apa adanya supaya tidak kosong
    return re.sub(r"^\s*\d+\.\s*", "", asli).strip()


def nama_tampil(device_name):
    """
    Nama sekolah untuk DITAMPILKAN: tanpa nomor urut, tanpa IP, dan tanpa
    kode di depan ("SIMS039 SIMS039 SMA N 1 Piyungan" -> "SMA N 1 Piyungan").

    nama_bersih() sengaja tidak diubah karena tentukan_id() masih memakainya
    untuk membaca kode sekolah di balik layar.
    """

    t = nama_bersih(device_name)
    t2 = re.sub(r"^([A-Za-z]{1,5}\d{2,6}[\s_-]+)+", "", t).strip()

    return t2 or t


def normalisasi_nama_sekolah(nama):
    """Normalisasi nama sekolah untuk deduplikasi dan pencocokan akurat."""
    t = nama_tampil(nama).upper()
    t = re.sub(r"\bSMKN\b", "SMK NEGERI", t)
    t = re.sub(r"\bSMAN\b", "SMA NEGERI", t)
    t = re.sub(r"\bSMPN\b", "SMP NEGERI", t)
    t = re.sub(r"\bSDN\b", "SD NEGERI", t)
    t = re.sub(r"\bNEGERI\s+N\b", "NEGERI", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t



def cari_ip(*teks_list):
    """Alamat IP pertama yang ditemukan di salah satu teks, atau ''."""

    for teks in teks_list:
        m = IP_RE.search(str(teks or ""))
        if m:
            return m.group(0)

    return ""


def inisial(device_name):
    """Dua karakter pertama nama sekolah, untuk lingkaran avatar."""

    nama = nama_tampil(device_name)
    huruf = re.sub(r"[^A-Za-z0-9]", "", nama)

    return (huruf[:2] or "?").upper()


def _bukan_ip(teks):

    return not re.match(r"^\d+\.\d+\.\d+\.\d+(?::\d+)?$", str(teks or "").strip())


def tentukan_id(device_name, id_lama, prtg_id):
    """
    ID internal sekolah (dipakai untuk link resume dan pengaturan manual,
    TIDAK ditampilkan di daftar, dan tidak pernah berupa alamat IP):
    - Nama diawali kode (SIM063, LM123, dst.) -> pakai kode itu.
    - Kalau tidak ada kode -> #ID PRTG.
    """

    nama = nama_bersih(device_name)
    lama = str(id_lama or "").strip()

    # ID lama sudah berupa kode (bukan #id / IP) -> pertahankan
    if lama and not lama.startswith("#") and _bukan_ip(lama):
        return lama

    m = re.match(r"^([A-Za-z]{1,5}\d{2,6})(?=[\s_-])", nama)

    if m:
        return m.group(1).upper()

    pid = str(prtg_id or "").strip()

    if pid.isdigit():
        return "#" + pid

    # Terakhir: jangan pernah kembalikan IP sebagai ID
    if lama and _bukan_ip(lama):
        return lama

    return nama or "-"


# ============================================================
# WILAYAH (KABUPATEN) DARI GRUP PRTG
# ============================================================

import unicodedata
from difflib import get_close_matches

WILAYAH_URUT = ["Bantul", "Gunung Kidul", "Kulon Progo", "Sleman", "Khusus", "Kota Jogja"]

# Kata kunci untuk jalur grup PRTG (nama wilayahnya saja).
WILAYAH_KATA = {
    "Bantul": ["bantul"],
    "Gunung Kidul": ["gunung kidul", "gunungkidul", "g kidul"],
    "Kulon Progo": ["kulon progo", "kulonprogo"],
    "Sleman": ["sleman"],
    "Khusus": ["khusus"],
    "Kota Jogja": [
        "kota jogja", "kota yogyakarta", "kota yogya",
        "kota jogjakarta", "kodya yogyakarta", "kodya jogja",
    ],
}

BELUM = "Belum Dikelompokkan"

# "Baru saja online" = uptime kurang dari sekian jam.
BARU_ONLINE_JAM = 1

# ---- Pengaturan pembacaan wilayah otomatis ----
# Toleransi salah ketik pada nama sekolah ("YOGYAKRTA" tetap terbaca).
EJAAN_AKTIF = True
# Tebak wilayah dari IP: kalau >= IP_MIN_SUARA device lain di blok IP (/24)
# yang sama sudah terbaca dan SEMUANYA satu wilayah, device yang belum
# terbaca di blok itu ikut wilayah tersebut. Matikan dengan False.
IP_INFERENSI = True
IP_MIN_SUARA = 3


# Cadangan: tebak wilayah dari NAMA sekolah / device / sensor.
# CATATAN: "jetis" ada di Kota Jogja (kecamatan) dan di Bantul (kapanewon).
# Di sini diarahkan ke Bantul (mis. SMA N 1 Jetis ada di Bantul). Kalau
# ada sekolah "Jetis" yang di Kota Jogja, atur manual lewat pengaturan.
WILAYAH_KATA_NAMA = {
    "Bantul": [
        "bantul", "srandakan", "sanden", "kretek", "pundong", "bambanglipuro",
        "pandak", "imogiri", "dlingo", "banguntapan", "pleret", "piyungan",
        "sewon", "kasihan", "pajangan", "sedayu", "parangtritis", "jetis",
    ],
    "Gunung Kidul": [
        "gunung kidul", "gunungkidul", "g kidul", "wonosari", "playen", "patuk",
        "semanu", "ponjong", "karangmojo", "tepus", "saptosari", "paliyan",
        "panggang", "rongkop", "girisubo", "gedangsari", "nglipar",
        "ngawen", "semin", "tanjungsari",
    ],
    "Kulon Progo": [
        "kulon progo", "kulonprogo", "wates", "temon", "panjatan", "galur",
        "lendah", "sentolo", "pengasih", "kokap", "girimulyo", "nanggulan",
        "kalibawang", "samigaluh",
    ],
    "Sleman": [
        "sleman", "gamping", "godean", "moyudan", "minggir", "seyegan",
        "mlati", "depok", "berbah", "prambanan", "kalasan", "ngemplak",
        "ngaglik", "tempel", "turi", "pakem", "cangkringan",
    ],
    "Kota Jogja": [
        "yogyakarta", "jogja", "jogjakarta", "djogja", "yogya", "danurejan",
        "gedongtengen", "gondokusuman", "gondomanan", "kotagede", "kraton",
        "mantrijeron", "mergangsan", "ngampilan", "pakualaman", "tegalrejo",
        "umbulharjo", "wirobrajan", "jetisharjo", "gowongan", "purwodiningratan",
    ],
}


def normalisasi(teks):
    """Huruf kecil, tanpa aksen/tanda baca, spasi tunggal ("Kota-Jogja" -> "kota jogja")."""

    t = unicodedata.normalize("NFKD", str(teks or ""))
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()

    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def wilayah_valid(nilai):
    """Nama wilayah baku dari tulisan bebas ("kota jogja" -> "Kota Jogja"), atau None."""

    n = normalisasi(nilai)

    for w in WILAYAH_URUT:
        if normalisasi(w) == n:
            return w

    return None


# Pengaturan tambahan dari pengaturan_sekolah.json (opsional):
#   "kata_wilayah": {"Bantul": ["jetis", "canden"]}   -> kata tambahan
#   "host_wilayah": {"10.20.5.": "Bantul"}            -> awalan IP/host
_cache_ekstra = {"mtime": None, "data": None}


def baca_pengaturan_ekstra():

    try:
        mtime = os.path.getmtime(PENGATURAN_FILE)
    except OSError:
        mtime = None

    if _cache_ekstra["data"] is not None and _cache_ekstra["mtime"] == mtime:
        return _cache_ekstra["data"]

    data = {"kata_wilayah": {}, "host_wilayah": {}}

    try:
        with open(PENGATURAN_FILE, "r", encoding="utf-8") as f:
            isi = json.load(f)

        if isinstance(isi, dict):

            kw = isi.get("kata_wilayah")
            if isinstance(kw, dict):
                for k, v in kw.items():
                    w = wilayah_valid(k)
                    if w and isinstance(v, list):
                        data["kata_wilayah"].setdefault(w, []).extend(
                            str(x) for x in v if str(x).strip()
                        )

            hw = isi.get("host_wilayah")
            if isinstance(hw, dict):
                for awalan, wil in hw.items():
                    w = wilayah_valid(wil)
                    if w and str(awalan).strip():
                        data["host_wilayah"][str(awalan).strip()] = w

    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        pass

    _cache_ekstra["mtime"] = mtime
    _cache_ekstra["data"] = data

    return data


_cache_kamus = {"sumber": None, "grup": {}, "nama": {}}


def _siapkan(kamus):

    hasil = {}

    for w in WILAYAH_URUT:
        hasil[w] = [k for k in (normalisasi(x) for x in kamus.get(w, [])) if k]

    return hasil


def _kamus():
    """(kamus untuk grup/Location/Tags, kamus untuk nama), sudah dinormalisasi."""

    ekstra = baca_pengaturan_ekstra()

    if _cache_kamus["sumber"] is ekstra:
        return _cache_kamus["grup"], _cache_kamus["nama"]

    tambah = ekstra["kata_wilayah"]

    _cache_kamus["grup"] = _siapkan(
        {w: list(WILAYAH_KATA.get(w, [])) + tambah.get(w, []) for w in WILAYAH_URUT}
    )
    _cache_kamus["nama"] = _siapkan(
        {w: list(WILAYAH_KATA_NAMA.get(w, [])) + tambah.get(w, []) for w in WILAYAH_URUT}
    )
    _cache_kamus["sumber"] = ekstra

    return _cache_kamus["grup"], _cache_kamus["nama"]


def cocokkan(teks, kamus, urutan=None):
    """Wilayah pertama yang katanya ada di teks (kamus sudah dinormalisasi)."""

    g = normalisasi(teks)

    if not g:
        return None

    ruang = " " + g + " "

    for wilayah in (urutan or WILAYAH_URUT):
        for kata in kamus.get(wilayah, []):
            if (" " + kata + " ") in ruang:
                return wilayah

    return None


def cocokkan_terdekat(teks, kamus):
    """
    Seperti cocokkan(), tapi untuk jalur grup bertingkat ("A > B > C"):
    yang dipakai wilayah yang tertulis PALING KANAN (grup terdekat dengan
    device), bukan wilayah yang kebetulan urutannya paling awal.
    """

    g = normalisasi(teks)

    if not g:
        return None

    ruang = " " + g + " "
    posisi, hasil = -1, None

    for wilayah in WILAYAH_URUT:
        for kata in kamus.get(wilayah, []):
            pos = ruang.rfind(" " + kata + " ")

            if pos > posisi:
                posisi, hasil = pos, wilayah

    return hasil


def _cocok_ejaan(teks, kamus):
    """Cocokkan kata yang salah ketik sedikit. Return (wilayah, kata_baku) atau (None, None)."""

    g = normalisasi(teks)

    if not g:
        return None, None

    kandidat = {}

    for w in WILAYAH_URUT:
        for k in kamus.get(w, []):
            if " " not in k and len(k) >= 7:
                kandidat.setdefault(k, w)

    if not kandidat:
        return None, None

    daftar = list(kandidat)

    for token in dict.fromkeys(g.split()):

        if len(token) < 7 or token in kandidat:
            continue

        mirip = get_close_matches(token, daftar, n=1, cutoff=0.85)

        if mirip:
            return kandidat[mirip[0]], mirip[0]

    return None, None


def _prefix_ip(host):
    """Tiga angka pertama IP ("10.20.5.17" -> "10.20.5"), atau None kalau bukan IP."""

    m = re.match(r"^\s*(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}\s*$", str(host or ""))

    return m.group(1) if m else None


def _wilayah_dari_host_pengaturan(host):

    host = str(host or "").strip()

    if not host:
        return None

    for awalan, wil in baca_pengaturan_ekstra()["host_wilayah"].items():
        if host.startswith(awalan):
            return wil

    return None


# Jalur grup PRTG: id device -> "Grup Atas > Grup Tengah > Grup Induk"
_cache_jalur = {"waktu": 0.0, "coba": 0.0, "data": {}}

# Info device dari PRTG: id device -> {"nama", "host", "lokasi", "tags", "komentar"}
# "ip": awalan IP (/24) -> wilayah, hasil belajar dari device yang sudah terbaca.
_cache_info = {"data": {}, "ip": {}}


def tentukan_wilayah_info(nama_grup, nama_device="", nama_sensor="", info_prtg=None, pakai_ip=True):
    """
    Return (wilayah, sumber). Urutan pencarian (semuanya dari data PRTG):
      1. nama device / nama sensor (kata wilayah atau kecamatan),
      2. jalur grup PRTG (grup paling dekat dengan device),
      3. kolom Location, Tags, Comments pada device,
      4. aturan host di pengaturan_sekolah.json,
      5. toleransi salah ketik pada nama,
      6. tebakan dari blok IP yang sama (hanya kalau meyakinkan).
    """

    kg, kn = _kamus()
    info = info_prtg or {}

    teks_nama = (
        ("nama device", nama_device),
        ("nama device di PRTG", info.get("nama")),
        ("nama sensor", nama_sensor),
    )

    # 1. NAMA sekolah dulu: "SMA N 1 Bantul" jelas Bantul, apa pun isi grup/Location.
    for label, teks in teks_nama:
        w = cocokkan(teks, kn)

        if w:
            return w, label

    # 2. Jalur grup PRTG (grup yang paling dekat dengan device dipakai).
    w = cocokkan_terdekat(nama_grup, kg)

    if w:
        return w, "jalur grup PRTG"

    # 3. Kolom Location / Tags / Comments pada device.
    for label, teks in (
        ("kolom Location PRTG", info.get("lokasi")),
        ("kolom Tags PRTG", info.get("tags")),
        ("kolom Comments PRTG", info.get("komentar")),
    ):
        w = cocokkan(teks, kg) or cocokkan(teks, kn)

        if w:
            return w, label

    w = _wilayah_dari_host_pengaturan(info.get("host"))

    if w:
        return w, "aturan host di pengaturan_sekolah.json"

    if EJAAN_AKTIF:
        for label, teks in teks_nama:
            w, kata = _cocok_ejaan(teks, kn)

            if w:
                return w, f"tebakan ejaan {label} ({kata})"

    if pakai_ip and IP_INFERENSI:
        prefix = _prefix_ip(info.get("host"))
        w = _cache_info["ip"].get(prefix) if prefix else None

        if w:
            return w, f"tebakan dari blok IP {prefix}.x"

    return BELUM, "tidak ditemukan"


def tentukan_wilayah(nama_grup, nama_device="", nama_sensor="", info_prtg=None):

    return tentukan_wilayah_info(nama_grup, nama_device, nama_sensor, info_prtg)[0]


def _teks_prtg(nilai):
    """Teks bersih dari kolom PRTG (buang tag HTML, gabungkan kalau berupa list)."""

    if isinstance(nilai, (list, tuple)):
        nilai = " ".join(str(x) for x in nilai)

    t = re.sub(r"<[^>]+>", " ", str(nilai or ""))

    return re.sub(r"\s+", " ", t).strip()


def info_device(item):
    """Info PRTG (Location, Tags, Comments, Host) untuk satu device, atau None."""

    return _cache_info["data"].get(str(item.get("prtg_id", "") or ""))


def ip_device(item):
    """
    Alamat IP sekolah (dipakai di balik layar, mis. pencatatan riwayat).
    TIDAK ditampilkan di dashboard. Urutan: kolom Host device di PRTG,
    lalu IP yang tertulis di nama device. Return "" kalau tidak ada.
    """

    inf = info_device(item) or {}
    host = str(inf.get("host", "") or "").strip()

    ip = cari_ip(host)

    if ip:
        return ip

    ip = cari_ip(item.get("device", ""), inf.get("nama", ""))

    if ip:
        return ip

    return host


def _tabel_prtg(content, kolom):

    r = requests.get(
        f"{PRTG_SERVER}/api/table.json",
        params={
            "content": content,
            "columns": kolom,
            **get_prtg_auth_params(),
            "count": 50000,
        },
        timeout=PRTG_TIMEOUT,
        verify=VERIFY_SSL,
    )

    r.raise_for_status()

    data = r.json()

    return data.get(content, []) if isinstance(data, dict) else []


KOLOM_TAMBAHAN = ("location", "tags", "comments")


def _ambil_perangkat():
    """
    Daftar device PRTG lengkap dengan Location/Tags/Comments. Kalau PRTG
    menolak salah satu kolom, kolom itu saja yang dilewati (bukan semuanya).
    """

    dasar = "objid,parentid,name,host"

    try:
        return _tabel_prtg("devices", dasar + "," + ",".join(KOLOM_TAMBAHAN))
    except Exception:
        pass

    data = {str(d.get("objid")): dict(d) for d in _tabel_prtg("devices", dasar)}

    for kol in KOLOM_TAMBAHAN:
        try:
            for d in _tabel_prtg("devices", "objid," + kol):
                oid = str(d.get("objid"))
                if oid in data:
                    data[oid][kol] = d.get(kol)
        except Exception as e:
            print(f"Kolom '{kol}' tidak bisa dibaca dari PRTG, dilewati:", e)

    return list(data.values())


def ambil_jalur_grup(paksa=False):
    """
    Telusuri semua grup induk tiap device + baca Location/Tags/Comments/Host.
    Di-cache 5 menit. paksa=True: ambil ulang (dibatasi tiap 30 detik).
    """

    sekarang = time.time()

    if paksa:
        if sekarang - _cache_jalur["coba"] < 30 and _cache_jalur["data"]:
            return _cache_jalur["data"]
    elif _cache_jalur["data"] and sekarang - _cache_jalur["waktu"] < 300:
        return _cache_jalur["data"]

    _cache_jalur["coba"] = sekarang

    try:
        grup = _tabel_prtg("groups", "objid,parentid,name")
        perangkat = _ambil_perangkat()
    except Exception as e:
        print("Gagal mengambil pohon grup PRTG:", e)
        return _cache_jalur["data"]

    info = {}

    for g in grup:
        info[str(g.get("objid"))] = (
            str(g.get("parentid", "")),
            str(g.get("name") or ""),
        )

    jalur = {}
    info_dev = {}

    for dv in perangkat:

        nama_list = []
        pid = str(dv.get("parentid", ""))
        langkah = 0

        while pid in info and langkah < 15:
            induk, nama = info[pid]
            nama_list.append(nama)
            pid = induk
            langkah += 1

        oid = str(dv.get("objid"))

        jalur[oid] = " > ".join(reversed(nama_list))

        info_dev[oid] = {
            "nama": _teks_prtg(dv.get("name")),
            "host": _teks_prtg(dv.get("host")),
            "lokasi": _teks_prtg(dv.get("location")),
            "tags": _teks_prtg(dv.get("tags")),
            "komentar": _teks_prtg(dv.get("comments")),
        }

    # Belajar blok IP -> wilayah dari device yang wilayahnya sudah jelas.
    suara = {}

    for oid, inf in info_dev.items():

        w, _ = tentukan_wilayah_info(jalur.get(oid, ""), inf["nama"], "", inf, pakai_ip=False)
        p = _prefix_ip(inf["host"])

        if p and w != BELUM:
            suara.setdefault(p, {})
            suara[p][w] = suara[p].get(w, 0) + 1

    peta_ip = {
        p: next(iter(c))
        for p, c in suara.items()
        if len(c) == 1 and sum(c.values()) >= IP_MIN_SUARA
    }

    _cache_jalur["waktu"] = sekarang
    _cache_jalur["data"] = jalur
    _cache_info["data"] = info_dev
    _cache_info["ip"] = peta_ip

    return jalur


def grup_item(item, jalur):
    """Teks jalur grup (semua induk) untuk satu device."""

    path = jalur.get(str(item.get("prtg_id", "") or ""), "")

    return " > ".join(x for x in (path, item.get("group", "")) if x)


def _wilayah_manual(manual, sid, prtg_id):
    """Wilayah dari pengaturan_sekolah.json (kunci: ID sekolah atau #ID PRTG)."""

    for kunci in (str(sid), "#" + str(prtg_id or "")):
        if kunci in manual:
            w = wilayah_valid(manual.get(kunci))
            if w:
                return w

    return None


def diagnosa_belum(items):
    """Apa yang terbaca dari PRTG untuk tiap sekolah yang belum terkelompok."""

    jalur = _cache_jalur["data"]
    hasil = []

    for d in items or []:

        if d.get("wilayah") != BELUM:
            continue

        pid = str(d.get("prtg_id", "") or "")
        inf = _cache_info["data"].get(pid, {})

        hasil.append({
            "id": d.get("id", ""),
            "device": d.get("nama") or d.get("device", ""),
            "prtg_id": pid,
            "jalur": jalur.get(pid) or "(tidak terbaca)",
            "lokasi": inf.get("lokasi", ""),
            "tags": inf.get("tags", ""),
            "komentar": inf.get("komentar", ""),
            "host": inf.get("host", ""),
        })

    return hasil


app.jinja_env.globals["diagnosa_belum"] = diagnosa_belum


def hitung_per_wilayah(details):

    hasil = {}

    for d in details:
        w = hasil.setdefault(d["wilayah"], {"t24": 0, "t48": 0, "rekap": 0})
        kunci = {"< 24 jam": "t24", "24 - 48 jam": "t48",
                 "> 48 jam": "rekap"}.get(d["category"])
        if kunci:
            w[kunci] += 1

    return hasil


def daftar_navbar(hitung):

    daftar = []

    for nama in WILAYAH_URUT:
        c = hitung.get(nama, {})
        daftar.append({
            "nama": nama,
            "jumlah": c.get("t24", 0) + c.get("t48", 0) + c.get("rekap", 0),
        })

    c = hitung.get(BELUM)

    if c:
        daftar.append({
            "nama": BELUM,
            "jumlah": c["t24"] + c["t48"] + c["rekap"],
        })

    return daftar


def prepare_details(sensors_sekolah, online_schools=None):
    """
    Satu baris per sekolah yang down, dari build_down_device_details().
    Hanya menyertakan sekolah yang benar-benar offline (tidak masuk data online PRTG).
    Jika sekolah sudah aktif di data online PRTG meskipun ada sensor/port yang mati,
    sekolah tersebut diabaikan di sini agar tidak terjadi duplikasi antar-dashboard.
    """

    if online_schools is None:
        try:
            online_schools = _cache_online_data.get("data")
            if not online_schools:
                online_schools, _ = ambil_data_online_cached()
        except Exception:
            online_schools = []

    online_prtg_ids = set()
    online_ids = set()
    online_names = set()
    online_ips = set()

    for on in (online_schools or []):
        pid = str(on.get("prtg_id", "") or "").strip()
        if pid:
            online_prtg_ids.add(pid)
        oid = str(on.get("id", "") or "").strip()
        if oid:
            online_ids.add(oid.lower())
        nm = normalisasi_nama_sekolah(on.get("nama") or on.get("device") or "")
        if nm:
            online_names.add(nm)
        ip = str(on.get("ip", "") or "").strip()
        if ip and not _bukan_ip(ip):
            online_ips.add(ip)

    device_details = build_down_device_details(sensors_sekolah)

    fmt = "%Y-%m-%d %H:%M:%S"

    jalur = ambil_jalur_grup()

    manual_wilayah = baca_pengaturan().get("wilayah", {})

    details = []

    for item in device_details.values():
        pid = str(item.get("prtg_id", "") or "").strip()
        sid = tentukan_id(item.get("device"), item.get("id"), item.get("prtg_id"))
        nama = nama_tampil(item.get("device"))
        norm_nama = normalisasi_nama_sekolah(nama)
        ip = ip_device(item)

        # Cek apakah sekolah ini masuk ke data online di PRTG
        is_online = (
            (pid and pid in online_prtg_ids)
            or (sid and str(sid).strip().lower() in online_ids)
            or (norm_nama and norm_nama in online_names)
            or (ip and ip in online_ips)
        )

        if is_online:
            # Tetap ikuti data online di PRTG dan jangan duplikasi ke offline
            continue

        lastdown = item.get("lastdown")

        if lastdown is not None and hasattr(lastdown, "strftime"):
            down_text = lastdown.strftime(fmt)
        else:
            down_text = None

        hours = item.get("hours")

        try:
            hours_num = float(hours) if hours is not None else 0.0
        except (TypeError, ValueError):
            hours_num = 0.0

        wilayah_auto, sumber = tentukan_wilayah_info(
            grup_item(item, jalur),
            item.get("device", ""),
            " ".join(str(sn.get("sensor") or "") for sn in item.get("sensors", [])),
            info_device(item),
        )

        wilayah_manual = _wilayah_manual(manual_wilayah, sid, item.get("prtg_id"))

        details.append({
            "id": sid,
            "prtg_id": str(item.get("prtg_id", "") or ""),
            "device": item.get("device"),
            "nama": nama,
            "ip": ip,
            "inisial": inisial(item.get("device")),
            "group": grup_item(item, jalur),
            "wilayah": wilayah_manual or wilayah_auto,
            "wilayah_otomatis": wilayah_auto,
            "wilayah_manual": wilayah_manual or "",
            "wilayah_sumber": "pengaturan manual" if wilayah_manual else sumber,
            "down_count": item.get("down_count", 0),
            "sensors": item.get("sensors", []),
            "lastdown_text": down_text,
            "hours": hours_num,
            "lama_down": format_lama_down(hours),
            "category": item.get("category"),
            "warna": {"< 24 jam": "kuning", "24 - 48 jam": "merah"}.get(
                item.get("category"), "hitam"),
        })

    details.sort(key=lambda x: (x["lastdown_text"] or "9999-99-99 99:99:99"))

    return details


def parse_uptime_hours(uptime_raw):
    """Mengubah string uptimesince PRTG menjadi estimasi jam."""
    if not uptime_raw:
        return 0.0
    text = str(uptime_raw).lower()
    days = 0
    hours = 0
    minutes = 0
    m_d = re.search(r'\b(\d+)\s*(?:d\b|hari\b)', text)
    if m_d:
        days = int(m_d.group(1))
    m_h = re.search(r'\b(\d+)\s*(?:h\b|jam\b)', text)
    if m_h:
        hours = int(m_h.group(1))
    m_m = re.search(r'\b(\d+)\s*(?:m\b|mnt\b|menit\b)', text)
    if m_m:
        minutes = int(m_m.group(1))
    return days * 24.0 + hours + (minutes / 60.0)


def hitung_online_sejak(uptime_raw, sekarang=None):
    """
    Perkiraan kapan sekolah mulai online = waktu sekarang dikurangi durasi
    uptime dari PRTG (mis. "2 d 3 h"). PRTG hanya memberi durasi, bukan
    tanggal, jadi hasilnya bisa meleset sekitar satu menit.
    Return "-" kalau uptime tidak terbaca.
    """

    teks = re.sub(r"<[^>]+>", " ", str(uptime_raw or "")).lower().strip()

    if not teks:
        return "-"

    hari = jam = menit = detik = 0
    ketemu = False

    m = re.search(r"\b(\d+)\s*(?:d\b|hari\b)", teks)
    if m:
        hari, ketemu = int(m.group(1)), True

    m = re.search(r"\b(\d+)\s*(?:h\b|jam\b)", teks)
    if m:
        jam, ketemu = int(m.group(1)), True

    m = re.search(r"\b(\d+)\s*(?:m\b|mnt\b|menit\b)", teks)
    if m:
        menit, ketemu = int(m.group(1)), True

    m = re.search(r"\b(\d+)\s*(?:s\b|dtk\b|detik\b)", teks)
    if m:
        detik, ketemu = int(m.group(1)), True

    if not ketemu:
        return "-"

    sekarang = sekarang or datetime.now()

    mulai = sekarang - timedelta(days=hari, hours=jam, minutes=menit, seconds=detik)

    return mulai.strftime("%Y-%m-%d %H:%M")


def get_online_devices():
    """Mengambil SEMUA SENSOR ONLINE TERKINI langsung dari PRTG."""
    url = f"{PRTG_SERVER}/api/table.json"
    params = {
        "content": "sensors",
        "columns": "objid,parentid,group,device,sensor,status,lastcheck,uptimesince,message",
        "filter_status": [3],
        **get_prtg_auth_params(),
        "count": 50000,
    }
    try:
        response = requests.get(url, params=params, timeout=PRTG_TIMEOUT, verify=VERIFY_SSL)
        response.raise_for_status()
        data = response.json()
        return data.get("sensors", []) if isinstance(data, dict) else []
    except Exception as e:
        print("Gagal mengambil data online dari PRTG:", e)
        return []


def _baca_sensor_online(sensor, waktu_hitung):
    """Satu sensor online: terakhir diperiksa, durasi uptime, dan tanggal mulai online."""

    lc = str(sensor.get("lastcheck", ""))

    # Ekstrak bagian "[29 s ago]" jika ada
    match = re.search(r"\[(.*?)\]", lc)
    if match:
        lc_clean = match.group(1)
    else:
        lc_clean = re.sub(r"<[^>]+>", " ", lc).strip()
        lc_clean = re.sub(r"\s+", " ", lc_clean)

    # Terjemahkan ke Bahasa Indonesia
    lc_clean = re.sub(r"\b(\d+)\s*s ago\b", r"\1 dtk lalu", lc_clean)
    lc_clean = re.sub(r"\b(\d+)\s*m ago\b", r"\1 mnt lalu", lc_clean)
    lc_clean = re.sub(r"\b(\d+)\s*h ago\b", r"\1 jam lalu", lc_clean)
    lc_clean = re.sub(r"\b(\d+)\s*d ago\b", r"\1 hari lalu", lc_clean)

    up_raw = str(sensor.get("uptimesince", ""))
    up_clean = up_raw
    up_clean = re.sub(r"\b(\d+)\s*d\b", r"\1 hari", up_clean)
    up_clean = re.sub(r"\b(\d+)\s*h\b", r"\1 jam", up_clean)
    up_clean = re.sub(r"\b(\d+)\s*m\b", r"\1 mnt", up_clean)
    up_clean = re.sub(r"\b(\d+)\s*s\b", r"\1 dtk", up_clean)

    return {
        "sensor": sensor.get("sensor", ""),
        "status": "Online",
        "lastcheck": lc_clean,
        "uptimesince": up_clean,
        "online_sejak": hitung_online_sejak(up_raw, waktu_hitung),
        "uptime_hours": parse_uptime_hours(up_raw),
        "pesan": bersihkan(sensor.get("message")),
    }


def prepare_online_details(sensors_online):
    """
    Satu baris per sekolah yang online. Ringkasan (kategori, online sejak,
    durasi) mengikuti sensor pertama yang terbaca; semua sensor online
    sekolah itu disimpan di "sensors" untuk halaman resume.
    """
    from prtg_sekolah import extract_id, extract_ip

    jalur = ambil_jalur_grup()

    # Ada device yang jalur grupnya belum dikenal (mis. baru ditambah di PRTG):
    # ambil ulang pohon grup supaya wilayahnya tetap dibaca dari PRTG.
    if any(str(s.get("parentid", "")) not in jalur for s in sensors_online):
        jalur = ambil_jalur_grup(paksa=True)

    manual_wilayah = baca_pengaturan().get("wilayah", {})
    devices = {}

    waktu_hitung = datetime.now()

    for sensor in sensors_online:
        device_name = sensor.get("device", "")
        device_id = extract_id(device_name)
        parent_id = str(sensor.get("parentid", "") or "").strip()

        if device_id:
            device_key = device_id
        elif parent_id.isdigit():
            device_key = f"#{parent_id}"
        else:
            device_ip = extract_ip(device_name)
            device_key = device_ip or device_name.strip() or f"OBJID-{sensor.get('objid', '?')}"

        sn = _baca_sensor_online(sensor, waktu_hitung)

        if device_key not in devices:

            hours_up = sn["uptime_hours"]

            if hours_up >= 48:
                up_kat, up_label, up_warna = "rekap", "> 48 Jam", "hijau-tua"
            elif hours_up >= 24:
                up_kat, up_label, up_warna = "t48", "24 - 48 Jam", "hijau"
            else:
                up_kat, up_label, up_warna = "t24", "< 24 Jam", "kuning"

            devices[device_key] = {
                "id": device_key,
                "device": device_name,
                "nama": nama_tampil(device_name),
                "inisial": inisial(device_name),
                "group": sensor.get("group", ""),
                "status": "Online",
                "sensor": sn["sensor"],
                "lastcheck": sn["lastcheck"],
                "uptimesince": sn["uptimesince"],
                "online_sejak": sn["online_sejak"],
                "uptime_hours": hours_up,
                "uptime_kategori": up_kat,
                "uptime_label": up_label,
                "uptime_warna": up_warna,
                "baru_online": hours_up < BARU_ONLINE_JAM,
                "prtg_id": parent_id,
                "sensors": [],
            }

        devices[device_key]["sensors"].append(sn)

    details = []

    for item in devices.values():

        item["jumlah_sensor"] = len(item["sensors"])

        item["id"] = tentukan_id(item.get("device"), item.get("id"), item.get("prtg_id"))

        item["ip"] = ip_device(item)

        wilayah_auto, sumber = tentukan_wilayah_info(
            grup_item(item, jalur),
            item.get("device", ""),
            " ".join(str(sn.get("sensor") or "") for sn in item["sensors"]),
            info_device(item),
        )

        wilayah_manual = _wilayah_manual(manual_wilayah, item.get("id"), item.get("prtg_id"))

        item["wilayah"] = wilayah_manual or wilayah_auto
        item["wilayah_sumber"] = "pengaturan manual" if wilayah_manual else sumber
        details.append(item)

    # Deduplikasi sekolah yang memiliki entri ganda di PRTG (seperti SMA N 1 Semanu atau SMKN 3 Wonosari)
    seen_names = {}
    seen_ips = {}
    deduped_details = []

    for item in details:
        norm = normalisasi_nama_sekolah(item.get("nama") or item.get("device") or "")
        ip = item.get("ip") or ""
        has_valid_ip = ip and not _bukan_ip(ip)

        existing = None
        if norm and norm in seen_names:
            existing = seen_names[norm]
        elif has_valid_ip and ip in seen_ips:
            existing = seen_ips[ip]

        if existing is not None:
            existing_objids = {s.get("objid") for s in existing.get("sensors", []) if s.get("objid")}
            for sn in item.get("sensors", []):
                if sn.get("objid") not in existing_objids:
                    existing["sensors"].append(sn)
                    if sn.get("objid"):
                        existing_objids.add(sn.get("objid"))
            existing["jumlah_sensor"] = len(existing["sensors"])

            if len(item.get("nama", "")) > len(existing.get("nama", "")):
                existing["nama"] = item["nama"]
                existing["device"] = item["device"]

            if (item.get("uptime_hours") or 0) > (existing.get("uptime_hours") or 0):
                existing["uptime_hours"] = item["uptime_hours"]
                existing["uptimesince"] = item["uptimesince"]
                existing["online_sejak"] = item["online_sejak"]
                existing["uptime_kategori"] = item["uptime_kategori"]
                existing["uptime_label"] = item["uptime_label"]
                existing["uptime_warna"] = item["uptime_warna"]
                existing["baru_online"] = item["baru_online"]
        else:
            deduped_details.append(item)
            if norm:
                seen_names[norm] = item
            if has_valid_ip:
                seen_ips[ip] = item

    deduped_details.sort(key=lambda x: x["nama"].lower())
    return deduped_details


def hitung_online_per_wilayah(details_online):
    """Menghitung jumlah sekolah online per wilayah."""
    hasil = {}
    for d in details_online:
        w = d.get("wilayah", BELUM)
        hasil[w] = hasil.get(w, 0) + 1
    return hasil


def hitung_online_detail_per_wilayah(details_online):
    """Menghitung detail kategori online (< 24 jam, 24 - 48 jam, > 48 jam) per wilayah."""
    hasil = {}
    for d in details_online:
        wil = d.get("wilayah") or BELUM
        if wil not in hasil:
            hasil[wil] = {"t24": 0, "t48": 0, "rekap": 0, "total": 0}
        hasil[wil]["total"] += 1
        kat = d.get("uptime_kategori")
        if kat in hasil[wil]:
            hasil[wil][kat] += 1
    return hasil


def daftar_navbar_online(hitung):
    """Menghasilkan list wilayah untuk navbar di halaman online."""
    daftar = []
    for nama in WILAYAH_URUT:
        daftar.append({
            "nama": nama,
            "jumlah": hitung.get(nama, 0),
        })
    if BELUM in hitung and hitung[BELUM] > 0:
        daftar.append({
            "nama": BELUM,
            "jumlah": hitung[BELUM],
        })
    return daftar


# Cache in-memory untuk navigasi cepat antar halaman dan filter wilayah
_cache_online_data = {"waktu": 0.0, "data": [], "sensors_count": 0}
_cache_down_data = {"waktu": 0.0, "data": []}


def ambil_data_online_cached(max_age=30):
    sekarang = time.time()
    if _cache_online_data["data"] and sekarang - _cache_online_data["waktu"] < max_age:
        return _cache_online_data["data"], _cache_online_data["sensors_count"]
    try:
        sensors_all = get_online_devices()
        sensors_sekolah = filter_sekolah(sensors_all)
        details = prepare_online_details(sensors_sekolah)
        _cache_online_data["waktu"] = sekarang
        _cache_online_data["data"] = details
        _cache_online_data["sensors_count"] = len(sensors_all)
        return details, len(sensors_all)
    except Exception as e:
        print("Gagal ambil data online:", e)
        return _cache_online_data["data"] or [], _cache_online_data.get("sensors_count", 0)


def ambil_data_down_cached(max_age=30):
    sekarang = time.time()
    if _cache_down_data["data"] and sekarang - _cache_down_data["waktu"] < max_age:
        return _cache_down_data["data"]
    try:
        sensors_all = get_down_devices()
        sensors_sekolah = filter_sekolah(sensors_all)
        details = prepare_details(sensors_sekolah)
        _cache_down_data["waktu"] = sekarang
        _cache_down_data["data"] = details
        return details
    except Exception as e:
        print("Gagal ambil data down:", e)
        return _cache_down_data["data"] or []


def statistik(anggota):
    """
    Jumlah, rata-rata lama down, yang terlama, dan 4 nama contoh
    (anggota harus sudah urut dari downtime terlama).
    """

    jam = [d["hours"] for d in anggota]

    rata = (sum(jam) / len(jam)) if jam else None
    maks = max(jam) if jam else None

    return {
        "jumlah": len(anggota),
        "rata": format_lama_down(rata) if rata is not None else "-",
        "terlama": format_lama_down(maks) if maks is not None else "-",
        "contoh": [d["nama"] for d in anggota[:4]],
    }


def ringkas_kategori(details):
    """
    Tiga kategori (< 24 jam, 24 - 48 jam, > 48 jam) beserta statistik
    dan daftar sekolahnya (terlama di depan).
    """

    daftar = [
        ("< 24 jam", "kuning", "< 24 Jam"),
        ("24 - 48 jam", "merah", "24 - 48 Jam"),
        ("> 48 jam", "hitam", "> 48 Jam"),
    ]

    hasil = []

    for kunci, warna, judul in daftar:

        anggota = sorted(
            [d for d in details if d["category"] == kunci],
            key=lambda x: -x["hours"],
        )

        item = statistik(anggota)
        item.update({"judul": judul, "warna": warna, "anggota": anggota})

        hasil.append(item)

    return hasil


def statistik_online(anggota):
    """
    Jumlah, rata-rata durasi online, terlama, dan 4 nama contoh
    (anggota harus sudah urut dari durasi online terlama).
    """

    jam = [d.get("uptime_hours", 0) for d in anggota]

    rata = (sum(jam) / len(jam)) if jam else None
    maks = max(jam) if jam else None

    return {
        "jumlah": len(anggota),
        "rata": format_lama_down(rata) if rata is not None else "-",
        "terlama": format_lama_down(maks) if maks is not None else "-",
        "contoh": [d["nama"] for d in anggota[:4]],
    }


def ringkas_kategori_online(details):
    """
    Tiga kategori uptime (< 24 Jam, 24 - 48 Jam, > 48 Jam) beserta statistik
    dan daftar sekolahnya (durasi online terlama di depan).
    """

    daftar = [
        ("t24", "kuning", "< 24 Jam"),
        ("t48", "biru", "24 - 48 Jam"),
        ("rekap", "hijau-tua", "> 48 Jam"),
    ]

    hasil = []

    for kunci, warna, judul in daftar:

        anggota = sorted(
            [d for d in details if d.get("uptime_kategori") == kunci],
            key=lambda x: -x.get("uptime_hours", 0),
        )

        item = statistik_online(anggota)
        item.update({"kunci": kunci, "judul": judul, "warna": warna, "anggota": anggota})

        hasil.append(item)

    return hasil


def format_pesan_sekolah(ids_24, ids_48, ids_rekap):

    lines = [
        "🔴 *REKAP GANGGUAN SEKOLAH*",
        "",
        "*< 24 JAM*",
    ]

    lines += ids_24 if ids_24 else ["-"]

    lines += ["", "*24 - 48 JAM*"]

    lines += ids_48 if ids_48 else ["-"]

    lines += ["", "*> 48 JAM*"]

    lines += ids_rekap if ids_rekap else ["-"]

    lines += ["", f"Total gangguan: {len(ids_24) + len(ids_48) + len(ids_rekap)} sekolah"]

    return "\n".join(lines)


def format_pesan_online(ids_24, ids_48, ids_rekap):

    lines = [
        "🟢 *REKAP SEKOLAH ONLINE*",
        "",
        "*< 24 JAM*",
    ]

    lines += ids_24 if ids_24 else ["-"]

    lines += ["", "*24 - 48 JAM*"]

    lines += ids_48 if ids_48 else ["-"]

    lines += ["", "*> 48 JAM*", f"Total: {len(ids_rekap)} sekolah"]

    lines += ["", f"*TOTAL ONLINE: {len(ids_24) + len(ids_48) + len(ids_rekap)} sekolah*"]

    return "\n".join(lines)


def _nama_online():
    """NAMA sekolah online per kategori (data terbaru, tanpa cache)."""

    details, _ = ambil_data_online_cached(max_age=0)

    def nama(kunci):
        return sorted(
            str(d.get("nama") or d.get("id")) for d in details
            if d.get("uptime_kategori") == kunci
        )

    return nama("t24"), nama("t48"), nama("rekap")


def _nama_down():
    """NAMA sekolah down per kategori (data terbaru dari PRTG)."""

    details = prepare_details(filter_sekolah(get_down_devices()))

    def nama(kategori):
        return sorted(
            str(d.get("nama") or d.get("id")) for d in details
            if d.get("category") == kategori
        )

    return nama("< 24 jam"), nama("24 - 48 jam"), nama("> 48 jam")


# ============================================================
# RIWAYAT UNTUK ANALITIK TREN
# ============================================================

def baca_riwayat():

    try:
        with open(RIWAYAT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        pass

    return []


def tambah_riwayat(total_24, total_48, total_rekap, per_wilayah=None):
    """Catat titik riwayat (dengan kunci supaya request bersamaan aman)."""

    with _kunci_riwayat:
        return _tambah_riwayat(total_24, total_48, total_rekap, per_wilayah)


def _tambah_riwayat(total_24, total_48, total_rekap, per_wilayah=None):

    riwayat = baca_riwayat()

    sekarang = datetime.now()

    titik = {
        "waktu": sekarang.strftime("%Y-%m-%d %H:%M:%S"),
        "t24": total_24,
        "t48": total_48,
        "rekap": total_rekap,
        "total": total_24 + total_48 + total_rekap,
    }

    # Data wilayah hanya disimpan kalau pengelompokannya valid.
    if per_wilayah is not None:
        titik["v"] = RIWAYAT_VERSI
        titik["w"] = per_wilayah

    ganti_terakhir = False

    if riwayat:
        try:
            terakhir = datetime.strptime(
                str(riwayat[-1].get("waktu", "")), "%Y-%m-%d %H:%M:%S"
            )
            selisih = (sekarang - terakhir).total_seconds()
            ganti_terakhir = 0 <= selisih < JEDA_RIWAYAT
        except (ValueError, TypeError, AttributeError):
            pass

    if ganti_terakhir:
        riwayat[-1] = titik
    else:
        riwayat.append(titik)

    riwayat = riwayat[-MAKS_RIWAYAT:]

    tulis_json_aman(RIWAYAT_FILE, riwayat)

    return riwayat


def riwayat_wilayah(riwayat, wilayah):
    """Riwayat satu wilayah (hanya titik yang sudah menyimpan data wilayah)."""

    hasil = []

    for r in riwayat:

        if r.get("v") != RIWAYAT_VERSI or "w" not in r:
            continue

        c = r["w"].get(wilayah, {})
        a, b, e = c.get("t24", 0), c.get("t48", 0), c.get("rekap", 0)

        hasil.append({
            "waktu": r["waktu"],
            "t24": a, "t48": b, "rekap": e,
            "total": a + b + e,
        })

    return hasil


def _bangun_grafik(riwayat, daftar, lebar=900, tinggi=300, maks_titik=60):
    """
    Grafik garis naik-turun (SVG, dihitung manual tanpa library JS).

    `daftar` = [(kunci, label, warna, tebal), ...]; kunci pertama harus "total".
    Sumbu Y selalu mulai dari 0 supaya naik turunnya terlihat jelas,
    dan setiap titik punya tooltip (arahkan kursor ke titik).

    Return None kalau riwayat belum cukup (minimal 2 titik).
    """

    titik = riwayat[-maks_titik:]

    if len(titik) < 2:
        return None

    kiri, kanan, atas, bawah = 44, 22, 18, 34
    w = lebar - kiri - kanan
    h = tinggi - atas - bawah
    n = len(titik)

    nmax = max(max(t.get("total", 0) for t in titik), 1)

    langkah = 1000
    for kandidat in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500):
        if nmax / kandidat <= 5:
            langkah = kandidat
            break

    # selalu ada ruang di atas nilai tertinggi
    ymax = ((nmax // langkah) + 1) * langkah

    def px(i):
        return kiri + (i / (n - 1)) * w

    def py(v):
        return atas + h - (v / ymax) * h

    seri = []

    for kunci, label, warna, tebal in daftar:

        pts = []

        for i, t in enumerate(titik):
            v = t.get(kunci, 0)
            pts.append({
                "x": round(px(i), 1),
                "y": round(py(v), 1),
                "v": v,
                "waktu": t["waktu"][5:16].replace("-", "/"),
            })

        poly = " ".join(f"{p['x']},{p['y']}" for p in pts)

        area = (
            f"{pts[0]['x']},{round(atas + h, 1)} " + poly +
            f" {pts[-1]['x']},{round(atas + h, 1)}"
        ) if kunci == "total" else None

        seri.append({
            "kunci": kunci,
            "label": label,
            "warna": warna,
            "tebal": tebal,
            "poly": poly,
            "area": area,
            "pts": pts,
        })

    grid = [
        {"y": round(py(v), 1), "label": v}
        for v in range(0, ymax + 1, langkah)
    ]

    idx = sorted(set(round(i * (n - 1) / 5) for i in range(6)))

    label_x = [
        {"x": round(px(i), 1), "label": titik[i]["waktu"][11:16]}
        for i in idx
    ]

    nilai = [t.get("total", 0) for t in titik]

    return {
        "lebar": lebar,
        "tinggi": tinggi,
        "kiri": kiri,
        "kanan_x": lebar - kanan,
        "dasar": round(atas + h, 1),
        "label_y": tinggi - 8,
        "grid": grid,
        "label_x": label_x,
        "seri": seri,
        "nmin": min(nilai),
        "nmax": max(nilai),
        "sekarang": nilai[-1],
        "selisih": nilai[-1] - nilai[0],
        "jumlah_titik": n,
        "awal": titik[0]["waktu"][5:16].replace("-", "/"),
        "akhir": titik[-1]["waktu"][5:16].replace("-", "/"),
    }


def buat_grafik(riwayat, lebar=900, tinggi=300, maks_titik=60):
    """Grafik tren sekolah DOWN: total, < 24 jam, 24 - 48 jam, > 48 jam."""

    return _bangun_grafik(
        riwayat,
        [
            ("total", "Total", "#1d5fd6", 3),
            ("t24", "< 24 jam", "#e0a800", 2),
            ("t48", "24 - 48 jam", "#d92d2d", 2),
            ("rekap", "> 48 jam", "#25272b", 2),
        ],
        lebar, tinggi, maks_titik,
    )


# ============================================================
# RIWAYAT UNTUK ANALITIK TREN SEKOLAH ONLINE
# ============================================================

def baca_riwayat_online():
    try:
        with open(RIWAYAT_ONLINE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        pass
    return []


def tambah_riwayat_online(n24, n48, nrekap, per_wilayah=None):
    with _kunci_riwayat_online:
        return _tambah_riwayat_online(n24, n48, nrekap, per_wilayah)


def _tambah_riwayat_online(n24, n48, nrekap, per_wilayah=None):
    riwayat = baca_riwayat_online()
    sekarang = datetime.now()

    titik = {
        "waktu": sekarang.strftime("%Y-%m-%d %H:%M:%S"),
        "t24": n24,
        "t48": n48,
        "rekap": nrekap,
        "total": n24 + n48 + nrekap,
    }

    if per_wilayah is not None:
        titik["v"] = 2
        titik["w"] = per_wilayah

    ganti_terakhir = False
    if riwayat:
        try:
            terakhir = datetime.strptime(
                str(riwayat[-1].get("waktu", "")), "%Y-%m-%d %H:%M:%S"
            )
            selisih = (sekarang - terakhir).total_seconds()
            ganti_terakhir = 0 <= selisih < JEDA_RIWAYAT
        except (ValueError, TypeError, AttributeError):
            pass

    if ganti_terakhir:
        riwayat[-1] = titik
    else:
        riwayat.append(titik)

    riwayat = riwayat[-MAKS_RIWAYAT:]
    tulis_json_aman(RIWAYAT_ONLINE_FILE, riwayat)
    return riwayat


def catat_riwayat_online(details):
    """Catat satu titik riwayat online dari daftar sekolah online."""

    if not details:
        return None

    per_w = hitung_online_detail_per_wilayah(details)

    def n(kunci):
        return sum(1 for d in details if d.get("uptime_kategori") == kunci)

    return tambah_riwayat_online(
        n("t24"), n("t48"), n("rekap"),
        per_w if ambil_jalur_grup() else None,
    )


def riwayat_online_wilayah(riwayat, wilayah):
    """Riwayat satu wilayah untuk sekolah online."""
    hasil = []
    for r in riwayat:
        if "w" not in r:
            continue
        c = r["w"].get(wilayah, {})
        a, b, e = c.get("t24", 0), c.get("t48", 0), c.get("rekap", 0)
        hasil.append({
            "waktu": r["waktu"],
            "t24": a, "t48": b, "rekap": e,
            "total": a + b + e,
        })
    return hasil


def buat_grafik_online(riwayat, lebar=900, tinggi=300, maks_titik=60):
    """Grafik tren sekolah ONLINE: total, < 24 jam, 24 - 48 jam, > 48 jam."""

    return _bangun_grafik(
        riwayat,
        [
            ("total", "Total", "#1d5fd6", 3),
            ("t24", "< 24 jam", "#e0a800", 2),
            ("t48", "24 - 48 jam", "#0284c7", 2),
            ("rekap", "> 48 jam", "#16a34a", 2),
        ],
        lebar, tinggi, maks_titik,
    )


# ============================================================
# RIWAYAT GANGGUAN PER SEKOLAH + RESUME
# ============================================================

FMT_WAKTU = "%Y-%m-%d %H:%M:%S"

_kunci_gangguan = threading.Lock()


def baca_gangguan():

    try:
        with open(GANGGUAN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        pass

    return []


def _parse(teks):

    try:
        return datetime.strptime(str(teks), FMT_WAKTU)
    except (ValueError, TypeError):
        return None


def catat_gangguan(details):
    """
    Catat awal & akhir gangguan tiap sekolah.

    - Sekolah down dan belum punya gangguan terbuka -> buka gangguan baru
      (mulai = perkiraan awal down dari PRTG).
    - Sekolah yang tadinya down lalu tidak muncul lagi selama >= 90 detik
      -> gangguan ditutup (selesai = saat pertama kali hilang). Jeda ini
      mencegah gangguan "putus" palsu kalau PRTG sesaat tidak menjawab.
    """

    sekarang = datetime.now()
    teks_sekarang = sekarang.strftime(FMT_WAKTU)

    with _kunci_gangguan:

        data = baca_gangguan()

        terbuka = {x.get("id"): x for x in data if x.get("selesai") is None}

        ada = set()

        for d in details:

            sid = d.get("id")

            if not sid:
                continue

            ada.add(sid)

            x = terbuka.get(sid)

            if x is None:
                data.append({
                    "id": sid,
                    "nama": d.get("nama", ""),
                    "ip": d.get("ip", ""),
                    "wilayah": d.get("wilayah", BELUM),
                    "mulai": d.get("lastdown_text") or teks_sekarang,
                    "selesai": None,
                    "jam_maks": d.get("hours", 0.0),
                    "sensor_maks": d.get("down_count", 0),
                })
            else:
                x["nama"] = d.get("nama", x.get("nama", ""))
                x["ip"] = d.get("ip") or x.get("ip", "")
                x["wilayah"] = d.get("wilayah", x.get("wilayah", BELUM))
                x["jam_maks"] = max(x.get("jam_maks", 0.0), d.get("hours", 0.0))
                x["sensor_maks"] = max(x.get("sensor_maks", 0), d.get("down_count", 0))
                x.pop("hilang_sejak", None)

        for sid, x in terbuka.items():

            if sid in ada:
                continue

            hilang = _parse(x.get("hilang_sejak"))

            if hilang is None:
                x["hilang_sejak"] = teks_sekarang
            elif (sekarang - hilang).total_seconds() >= 90:
                x["selesai"] = x.pop("hilang_sejak")

        batas = (sekarang - timedelta(days=400)).strftime(FMT_WAKTU)

        data = [
            x for x in data
            if x.get("selesai") is None or x["selesai"] >= batas
        ][-MAKS_GANGGUAN:]

        tulis_json_aman(GANGGUAN_FILE, data)


def pencatat_latar():
    """Loop di thread terpisah: catat gangguan & riwayat online tiap INTERVAL_PENCATAT detik."""

    while True:

        try:
            details = prepare_details(filter_sekolah(get_down_devices()))
            catat_gangguan(details)
        except Exception as e:
            print("Pencatat gangguan gagal:", e)

        try:
            online_semua, _ = ambil_data_online_cached(max_age=INTERVAL_PENCATAT)
            catat_riwayat_online(online_semua)
        except Exception as e:
            print("Pencatat riwayat online gagal:", e)

        time.sleep(INTERVAL_PENCATAT)


def jam_gangguan(x):

    mulai = _parse(x.get("mulai"))

    if mulai is None:
        return 0.0

    selesai = _parse(x.get("selesai")) or datetime.now()

    return max((selesai - mulai).total_seconds() / 3600, 0.0)


def bersihkan(teks):
    """Buang tag HTML dari pesan sensor PRTG."""

    import html

    t = re.sub(r"<[^>]+>", " ", str(teks or ""))
    t = html.unescape(t)

    return re.sub(r"\s+", " ", t).strip() or "-"


def ringkas_riwayat(gangguan):

    sekarang = datetime.now()

    def periode(hari):
        batas = sekarang - timedelta(days=hari)
        sub = [
            x for x in gangguan
            if (_parse(x.get("mulai")) or sekarang) >= batas
        ]
        return len(sub), sum(jam_gangguan(x) for x in sub)

    n7, j7 = periode(7)
    n30, j30 = periode(30)

    jam = [jam_gangguan(x) for x in gangguan]

    return {
        "n7": n7, "jam7": format_lama_down(j7),
        "n30": n30, "jam30": format_lama_down(j30),
        "n_semua": len(gangguan),
        "jam_semua": format_lama_down(sum(jam)),
        "rata": format_lama_down(sum(jam) / len(jam)) if jam else "-",
        "terpanjang": format_lama_down(max(jam)) if jam else "-",
        "terakhir": gangguan[0].get("mulai", "-") if gangguan else "-",
        "dicatat_sejak": (
            min(x.get("mulai", "9999") for x in gangguan) if gangguan else "-"
        ),
    }


def baris_riwayat(gangguan):

    hasil = []

    for i, x in enumerate(gangguan, 1):
        selesai = x.get("selesai")
        hasil.append({
            "no": i,
            "mulai": x.get("mulai", "-"),
            "selesai": selesai or "-",
            "status": "Selesai" if selesai else "Berlangsung",
            "durasi": format_lama_down(jam_gangguan(x)),
            "sensor": x.get("sensor_maks", 0),
        })

    return hasil


NAMA_BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
NAMA_HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

# Warna kotak harian pada riwayat 30 hari (dipakai halaman, PDF, dan Excel).
WARNA_HARI = {
    "ok": "#2e9d57",
    "singkat": "#e8a600",
    "lama": "#d64545",
    "belum": "#c9cfe0",
}


def waktu_awam(dt, dengan_hari=False):
    """datetime -> "11 Okt 2026, 06.12" (atau "Minggu, 11 Okt 2026, 06.12")."""

    if dt is None:
        return "-"

    teks = f"{dt.day} {NAMA_BULAN[dt.month - 1]} {dt.year}, {dt.hour:02d}.{dt.minute:02d}"

    return f"{NAMA_HARI[dt.weekday()]}, {teks}" if dengan_hari else teks


def _ke_datetime(teks):
    """Teks waktu dari dashboard ("2026-10-11 06:12:03" atau "2026-10-11 06:12") -> datetime / None."""

    dt = _parse(teks)

    if dt is not None:
        return dt

    try:
        return datetime.strptime(str(teks), "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None


def waktu_awam_teks(teks, dengan_hari=False):

    return waktu_awam(_ke_datetime(teks), dengan_hari)


def waktu_kalimat(dt):
    """datetime -> "Minggu, 11 Okt 2026 pukul 06.12" (untuk kalimat ringkasan)."""

    if dt is None:
        return "-"

    return (
        f"{NAMA_HARI[dt.weekday()]}, {dt.day} {NAMA_BULAN[dt.month - 1]} {dt.year}"
        f" pukul {dt.hour:02d}.{dt.minute:02d}"
    )


def durasi_awam(jam):
    """Jam (angka) -> "2 hari 3 jam 32 menit"."""

    try:
        menit_total = int(round(float(jam) * 60))
    except (TypeError, ValueError):
        return "-"

    if menit_total < 1:
        return "kurang dari 1 menit"

    hari, sisa = divmod(menit_total, 24 * 60)
    j, m = divmod(sisa, 60)

    bagian = []

    if hari:
        bagian.append(f"{hari} hari")
    if j:
        bagian.append(f"{j} jam")
    if m:
        bagian.append(f"{m} menit")

    return " ".join(bagian)


def tingkat_gangguan(jam):
    """(label, kelas) tingkat keparahan satu gangguan berdasarkan lamanya."""

    if jam < 1:
        return "Singkat", "singkat"

    if jam < 24:
        return "Sedang", "sedang"

    return "Lama", "lama"


def _irisan_jam(x, awal, akhir, sekarang):
    """Berapa jam gangguan x berada di dalam rentang [awal, akhir]."""

    mulai = _parse(x.get("mulai"))

    if mulai is None:
        return 0.0

    selesai = _parse(x.get("selesai")) or sekarang

    a = max(mulai, awal)
    b = min(selesai, akhir)

    return max((b - a).total_seconds() / 3600, 0.0)


def _awal_pencatatan(semua_gangguan):
    """
    Perkiraan kapan dashboard mulai mencatat (paling awal dari seluruh
    catatan gangguan dan riwayat tren). None kalau belum ada catatan sama sekali.
    """

    kandidat = [_parse(x.get("mulai")) for x in semua_gangguan]

    for baca in (baca_riwayat, baca_riwayat_online):
        try:
            r = baca()
            if r:
                kandidat.append(_parse(r[0].get("waktu")))
        except Exception:
            pass

    kandidat = [k for k in kandidat if k is not None]

    return min(kandidat) if kandidat else None


def _persen_teks(nilai):

    if nilai >= 99.95:
        return "100%"

    return f"{nilai:.1f}".replace(".", ",") + "%"


def bangun_resume(sid):
    """
    Kumpulkan data resume satu sekolah dalam bentuk yang mudah dibaca orang
    awam: ringkasan kalimat, angka penting, riwayat 30 hari (per hari), dan
    daftar kejadian putus. None kalau sekolah tidak dikenal sama sekali.
    """

    galat = None

    try:
        details = prepare_details(filter_sekolah(get_down_devices()))
        catat_gangguan(details)
    except Exception as e:
        details = []
        galat = f"Data terkini dari PRTG gagal diambil ({e}). Yang tampil hanya data lain yang tersedia."

    down = next((d for d in details if str(d["id"]) == str(sid)), None)

    online = None

    try:
        semua_online, _ = ambil_data_online_cached()
        online = next((d for d in semua_online if str(d.get("id")) == str(sid)), None)
    except Exception as e:
        galat = galat or f"Data online terkini gagal diambil ({e})."

    semua_gangguan = baca_gangguan()

    gangguan = sorted(
        [x for x in semua_gangguan if x.get("id") == sid],
        key=lambda x: x.get("mulai", ""),
        reverse=True,
    )

    if down is None and online is None and not gangguan:
        return None

    sekarang = datetime.now()

    if down is not None and online is not None:
        status, kelas = "Sedang putus (sebagian pemeriksaan masih normal)", "merah"
    elif down is not None:
        status, kelas = "Sedang putus (tidak terhubung)", "merah"
    elif online is not None:
        status, kelas = "Terhubung (normal)", "hijau"
    elif galat:
        status, kelas = "Tidak diketahui", "hitam"
    else:
        status, kelas = "Tidak terbaca di PRTG saat ini", "hitam"

    acuan = down or online or {}
    terakhir = gangguan[0] if gangguan else {}

    nama = acuan.get("nama") or terakhir.get("nama") or sid
    wilayah = acuan.get("wilayah") or terakhir.get("wilayah", BELUM)

    # Alamat IP disimpan di data internal saja, TIDAK ditampilkan di resume.
    ip_sekolah = (
        (down or {}).get("ip")
        or (online or {}).get("ip")
        or next((x.get("ip") for x in gangguan if x.get("ip")), "")
        or "-"
    )

    # ---- Rentang waktu ----
    awal30 = sekarang - timedelta(days=30)
    awal7 = sekarang - timedelta(days=7)
    awal_catat = _awal_pencatatan(semua_gangguan)

    ev30 = [
        x for x in gangguan
        if x.get("selesai") is None
        or (_parse(x.get("selesai")) or sekarang) >= awal30
    ]

    n30 = len(ev30)
    jam30 = sum(_irisan_jam(x, awal30, sekarang, sekarang) for x in ev30)

    ev7 = [x for x in ev30 if _irisan_jam(x, awal7, sekarang, sekarang) > 0]
    n7 = len(ev7)
    jam7 = sum(_irisan_jam(x, awal7, sekarang, sekarang) for x in ev7)

    durasi_ev = [jam_gangguan(x) for x in ev30]

    # ---- Seberapa stabil (persen waktu menyala) ----
    pct = None
    catatan_jendela = ""

    if awal_catat is not None:
        awal_hitung = max(awal30, awal_catat)
        lebar = (sekarang - awal_hitung).total_seconds() / 3600

        if lebar >= 1:
            mati = sum(_irisan_jam(x, awal_hitung, sekarang, sekarang) for x in ev30)
            pct = max(0.0, min(100.0, 100.0 - mati / lebar * 100.0))

        if awal_catat > awal30:
            catatan_jendela = (
                f"Riwayat baru dicatat sejak {waktu_awam(awal_catat)}, "
                "jadi angka 30 hari hanya mencakup waktu sejak itu."
            )

    if pct is None:
        stabil_teks, stabil_label, stabil_kelas = "-", "Belum cukup data", "none"
    else:
        stabil_teks = _persen_teks(pct)

        if pct >= 99:
            stabil_label, stabil_kelas = "Stabil", "baik"
        elif pct >= 95:
            stabil_label, stabil_kelas = "Cukup stabil", "cukup"
        else:
            stabil_label, stabil_kelas = "Kurang stabil", "kurang"

    # ---- Kotak angka penting ----
    if down is not None:
        label_mulai = "Mulai putus sejak"
        mulai_txt = waktu_awam_teks(down.get("lastdown_text"), True)
        label_lama = "Sudah putus selama"
        lama_jam = down.get("hours")
        lama_txt = durasi_awam(lama_jam)
        ket_lama = "Terus bertambah selama belum pulih"
    elif online is not None:
        label_mulai = "Menyala sejak"
        mulai_txt = waktu_awam_teks(online.get("online_sejak"), True)
        label_lama = "Sudah menyala selama"
        lama_jam = online.get("uptime_hours")
        lama_txt = durasi_awam(lama_jam)
        ket_lama = (
            "Baru pulih, masih perlu dipantau"
            if online.get("baru_online")
            else "Dihitung dari data uptime di PRTG"
        )
    else:
        label_mulai = "Gangguan terakhir mulai"
        mulai_txt = waktu_awam_teks(terakhir.get("mulai"), True) if terakhir else "-"
        label_lama = "Lama gangguan terakhir"
        lama_jam = jam_gangguan(terakhir) if terakhir else None
        lama_txt = durasi_awam(lama_jam) if terakhir else "-"
        ket_lama = "Sekolah tidak terbaca di PRTG saat ini"

    # ---- Ringkasan kalimat ----
    if down is not None:
        mulai_dt = _ke_datetime(down.get("lastdown_text"))
        kalimat = f"{nama} tidak terhubung ke internet"
        if mulai_dt is not None:
            kalimat += f" sejak {waktu_kalimat(mulai_dt)}"
        kalimat += f", sudah sekitar {lama_txt}."
        if online is not None:
            kalimat += (
                " Sebagian pemeriksaan (sensor) masih normal, tetapi sebagian lain gagal, "
                "sehingga sekolah dihitung putus."
            )
    elif online is not None:
        kalimat = f"{nama} saat ini terhubung normal dan sudah menyala selama {lama_txt}."
        tutup = [
            (_parse(x.get("selesai")), x) for x in gangguan if _parse(x.get("selesai"))
        ]
        if tutup:
            selesai_dt, x = max(tutup, key=lambda t: t[0])
            if (sekarang - selesai_dt).total_seconds() <= 48 * 3600:
                kalimat += (
                    f" Sebelumnya sempat putus selama {durasi_awam(jam_gangguan(x))} "
                    f"(pulih {waktu_kalimat(selesai_dt)})."
                )
    else:
        kalimat = (
            f"{nama} tidak terbaca di PRTG saat ini, baik sebagai terhubung maupun putus "
            "(mungkin sensor di-pause atau device dihapus). Yang tampil hanya riwayat tersimpan."
        )

    if n30:
        kalimat += f" Dalam 30 hari terakhir tercatat {n30} kali putus dengan total sekitar {durasi_awam(jam30)}"
        if pct is not None:
            kalimat += f", sehingga internetnya menyala sekitar {stabil_teks} dari waktu"
        kalimat += "."

        if down is not None and n30 > 1:
            lainnya = [jam_gangguan(x) for x in ev30 if x.get("selesai") is not None]
            if lainnya and (down.get("hours") or 0) >= max(lainnya):
                kalimat += " Gangguan kali ini adalah yang terlama dalam 30 hari terakhir."
    else:
        kalimat += " Dalam 30 hari terakhir belum ada gangguan tercatat."

    # ---- Riwayat 30 hari, satu kotak per hari ----
    hari = []
    hari_ini = sekarang.date()

    for i in range(30):
        tgl = hari_ini - timedelta(days=29 - i)
        awal_h = datetime.combine(tgl, datetime.min.time())
        akhir_h = awal_h + timedelta(days=1)
        label = f"{tgl.day} {NAMA_BULAN[tgl.month - 1]}"

        if awal_catat is not None and akhir_h <= awal_catat:
            hari.append({"tgl": label, "kelas": "belum", "jam": 0.0,
                         "tip": f"{label}: belum dicatat", "warna": WARNA_HARI["belum"]})
            continue

        jam_h = sum(_irisan_jam(x, awal_h, min(akhir_h, sekarang), sekarang) for x in gangguan)

        if jam_h <= 0:
            kls, tip = "ok", f"{label}: normal seharian"
        elif jam_h < 1:
            kls, tip = "singkat", f"{label}: putus {durasi_awam(jam_h)}"
        else:
            kls, tip = "lama", f"{label}: putus {durasi_awam(jam_h)}"

        hari.append({"tgl": label, "kelas": kls, "jam": jam_h, "tip": tip, "warna": WARNA_HARI[kls]})

    # ---- Daftar kejadian putus ----
    kejadian = []

    for x in ev30[:100]:
        jam_x = jam_gangguan(x)
        label_t, kelas_t = tingkat_gangguan(jam_x)
        masih = x.get("selesai") is None

        kejadian.append({
            "mulai": waktu_awam_teks(x.get("mulai")),
            "selesai": "Masih putus sekarang" if masih else waktu_awam_teks(x.get("selesai")),
            "durasi": durasi_awam(jam_x) + (" (masih berjalan)" if masih else ""),
            "tingkat": label_t,
            "kelas": kelas_t,
            "masih": masih,
        })

    selesai_semua = [x.get("selesai") for x in gangguan if x.get("selesai")]

    stat = {
        "n7": n7,
        "jam7": durasi_awam(jam7) if n7 else "tidak ada",
        "n30": n30,
        "jam30": durasi_awam(jam30) if n30 else "tidak ada",
        "terpanjang": durasi_awam(max(durasi_ev)) if durasi_ev else "-",
        "rata": durasi_awam(sum(durasi_ev) / len(durasi_ev)) if durasi_ev else "-",
        "pulih": waktu_awam_teks(max(selesai_semua)) if selesai_semua else "-",
        "stabil": stabil_teks,
        "stabil_label": stabil_label,
        "stabil_kelas": stabil_kelas,
    }

    sensors = []

    if down is not None:
        for sn in down.get("sensors", []):
            sensors.append({
                "sensor": sn.get("sensor") or "-",
                "status": bersihkan(sn.get("status")),
                "lama": sn.get("downtimesince") or "-",
                "pesan": bersihkan(sn.get("message")),
            })

    sensors_online = []

    if online is not None:
        for i, sn in enumerate(online.get("sensors", [])):
            sensors_online.append({
                "sensor": sn.get("sensor") or "-",
                "status": "Online",
                "lastcheck": sn.get("lastcheck") or "-",
                "online_sejak": sn.get("online_sejak") or "-",
                "durasi": sn.get("uptimesince") or "-",
                "pesan": sn.get("pesan") or "-",
                "acuan": i == 0,
            })

    return {
        "sid": sid,
        "nama": nama,
        "ip": ip_sekolah,
        "device": acuan.get("device") or "-",
        "wilayah": wilayah,
        "wilayah_tampil": "Belum dikelompokkan" if wilayah == BELUM else wilayah,
        "wilayah_sumber": acuan.get("wilayah_sumber") or "riwayat tersimpan",
        "status": status,
        "kelas": kelas,
        "sedang_down": down is not None,
        "sedang_online": online is not None,
        "ringkasan_teks": kalimat,
        "kartu": {
            "label_mulai": label_mulai, "mulai": mulai_txt,
            "label_lama": label_lama, "lama": lama_txt, "ket_lama": ket_lama,
        },
        "hari": hari,
        "ada_belum": any(h["kelas"] == "belum" for h in hari),
        "stat": stat,
        "kejadian": kejadian,
        "catatan_jendela": catatan_jendela,
        "sensors": sensors,
        "sensors_online": sensors_online,
        "galat": galat,
        "dibuat": sekarang.strftime(FMT_WAKTU),
        "dibuat_awam": waktu_awam(sekarang),
    }


def _pasangan_resume(c):
    """Daftar (label, nilai) untuk bagian identitas, angka penting, dan riwayat."""

    s = c["stat"]
    k = c["kartu"]

    # Tanpa Kode Sekolah dan tanpa Alamat IP.
    identitas = [
        ("Nama Sekolah", c["nama"]),
        ("Kabupaten/Kota", c["wilayah_tampil"]),
        ("Kondisi Sekarang", c["status"]),
        ("Laporan Dibuat", c["dibuat_awam"]),
    ]

    angka = [
        (k["label_mulai"], k["mulai"]),
        (k["label_lama"], f"{k['lama']} ({k['ket_lama']})"),
        ("Berapa kali putus dalam 30 hari", f"{s['n30']} kali, total {s['jam30']}"),
        ("Seberapa stabil (30 hari)", f"{s['stabil']} menyala ({s['stabil_label']})"),
    ]

    riwayat = [
        ("7 hari terakhir", f"{s['n7']} kali putus, total {s['jam7']}"),
        ("Putus paling lama (30 hari)", s["terpanjang"]),
        ("Rata-rata sekali putus (30 hari)", s["rata"]),
        ("Terakhir pulih (menyala lagi)", s["pulih"]),
    ]

    return identitas, angka, riwayat


def buat_xlsx(c):

    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    kepala_isi = PatternFill("solid", fgColor="161A52")
    kepala_font = Font(bold=True, color="FFFFFF")

    def kepala(ws, judul):
        ws.append(judul)
        for cell in ws[ws.max_row]:
            cell.fill = kepala_isi
            cell.font = kepala_font

    def judul_bagian(ws, teks):
        ws.append([])
        ws.append([teks])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12, color="161A52")

    identitas, angka, riwayat = _pasangan_resume(c)

    wb = Workbook()
    ws = wb.active
    ws.title = "Resume"

    ws.append(["RESUME KONDISI JARINGAN SEKOLAH"])
    ws["A1"].font = Font(bold=True, size=14, color="161A52")

    judul_bagian(ws, "IDENTITAS")
    for label, nilai in identitas:
        ws.append([label, nilai])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)

    judul_bagian(ws, "RINGKASAN")
    ws.append([c["ringkasan_teks"]])
    ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=2)
    ws.row_dimensions[ws.max_row].height = 95

    judul_bagian(ws, "ANGKA PENTING")
    for label, nilai in angka:
        ws.append([label, nilai])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)

    judul_bagian(ws, "RIWAYAT 30 HARI TERAKHIR")
    for label, nilai in riwayat:
        ws.append([label, nilai])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)

    if c["catatan_jendela"]:
        ws.append([])
        ws.append([c["catatan_jendela"]])

    if c["galat"]:
        ws.append([])
        ws.append([c["galat"]])

    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 70

    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    ws2 = wb.create_sheet("Kejadian Putus")
    kepala(ws2, ["No", "Mulai putus", "Menyala lagi", "Berapa lama", "Tingkat"])

    for i, b in enumerate(c["kejadian"], 1):
        ws2.append([i, b["mulai"], b["selesai"], b["durasi"], b["tingkat"]])

    for kol, lebar in zip("ABCDE", (6, 24, 26, 34, 12)):
        ws2.column_dimensions[kol].width = lebar

    ws3 = wb.create_sheet("Per Hari (30 Hari)")
    kepala(ws3, ["Tanggal", "Kondisi", "Total putus"])

    nama_kondisi = {
        "ok": "Normal seharian",
        "singkat": "Putus sebentar (kurang dari 1 jam)",
        "lama": "Putus cukup lama",
        "belum": "Belum dicatat",
    }

    for h in c["hari"]:
        ws3.append([
            h["tgl"], nama_kondisi[h["kelas"]],
            durasi_awam(h["jam"]) if h["jam"] > 0 else "-",
        ])
        ws3.cell(row=ws3.max_row, column=2).fill = PatternFill(
            "solid", fgColor=WARNA_HARI[h["kelas"]].lstrip("#")
        )

    for kol, lebar in zip("ABC", (14, 38, 24)):
        ws3.column_dimensions[kol].width = lebar

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    return buf


def buat_pdf(c):

    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    gaya = getSampleStyleSheet()
    kecil = ParagraphStyle("kecil", parent=gaya["BodyText"], fontSize=8.5, leading=11)
    badan = ParagraphStyle("badan", parent=gaya["BodyText"], fontSize=10.5, leading=15)
    abu = ParagraphStyle("abu", parent=kecil, textColor=colors.HexColor("#5a6180"))
    h2 = ParagraphStyle("h2", parent=gaya["Heading3"], spaceBefore=14, spaceAfter=5,
                        textColor=colors.HexColor("#161a52"))

    def p(teks, st=kecil):
        return Paragraph(escape(str(teks)), st)

    def tebal(teks):
        return Paragraph(f"<b>{escape(str(teks))}</b>", kecil)

    def tabel_label(data):
        t = Table(data, colWidths=[6 * cm, 11 * cm])
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9cfe0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f3fa")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return t

    identitas, angka, riwayat = _pasangan_resume(c)

    isi = [
        Paragraph("Resume Kondisi Jaringan Sekolah", gaya["Title"]),
        Paragraph(escape(c["nama"]), ParagraphStyle(
            "nm", parent=gaya["Heading2"], alignment=1, textColor=colors.HexColor("#161a52"))),
        Spacer(1, 6),
        tabel_label([[tebal(a), p(b)] for a, b in identitas]),
        Paragraph("Ringkasan", h2),
        p(c["ringkasan_teks"], badan),
        Paragraph("Angka Penting", h2),
        tabel_label([[tebal(a), p(b)] for a, b in angka]),
        Paragraph("Riwayat 30 Hari Terakhir", h2),
        p("Setiap kotak adalah satu hari, dari 30 hari lalu (kiri) sampai hari ini (kanan).", abu),
        Spacer(1, 4),
    ]

    strip = Table([[""] * len(c["hari"])], colWidths=[17 * cm / len(c["hari"])] * len(c["hari"]),
                  rowHeights=[0.9 * cm])
    gaya_strip = [("GRID", (0, 0), (-1, -1), 1.2, colors.white)]

    for i, h in enumerate(c["hari"]):
        gaya_strip.append(("BACKGROUND", (i, 0), (i, 0), colors.HexColor(h["warna"])))

    strip.setStyle(TableStyle(gaya_strip))
    isi.append(strip)

    legenda = (
        "<font color='#2e9d57'>&#9632;</font> Normal seharian &nbsp;&nbsp;"
        "<font color='#e8a600'>&#9632;</font> Putus sebentar (kurang dari 1 jam) &nbsp;&nbsp;"
        "<font color='#d64545'>&#9632;</font> Putus cukup lama"
    )

    if c["ada_belum"]:
        legenda += " &nbsp;&nbsp;<font color='#c9cfe0'>&#9632;</font> Belum dicatat"

    isi += [Spacer(1, 4), Paragraph(legenda, kecil), Spacer(1, 8),
            tabel_label([[tebal(a), p(b)] for a, b in riwayat])]

    isi.append(Paragraph("Daftar Kejadian Putus", h2))

    if c["kejadian"]:
        def kep(x):
            return Paragraph(f"<font color='white'><b>{escape(x)}</b></font>", kecil)

        data = [[kep("No"), kep("Mulai putus"), kep("Menyala lagi"), kep("Berapa lama"), kep("Tingkat")]]

        for i, b in enumerate(c["kejadian"], 1):
            data.append([p(i), p(b["mulai"]), p(b["selesai"]), p(b["durasi"]), p(b["tingkat"])])

        t = Table(data, colWidths=[1.1 * cm, 3.8 * cm, 4.2 * cm, 5.9 * cm, 2.0 * cm], repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9cfe0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#161a52")),
        ]))
        isi.append(t)
    else:
        isi.append(p("Tidak ada kejadian putus tercatat dalam 30 hari terakhir."))

    isi.append(Spacer(1, 8))

    catatan = (
        "Catatan: waktu mulai putus adalah perkiraan dari data PRTG; waktu menyala lagi adalah "
        "saat dashboard mendeteksi sekolah kembali normal. Waktu \"menyala sejak\" dihitung dari "
        "durasi uptime PRTG dan bisa meleset sekitar satu menit."
    )

    if c["catatan_jendela"]:
        catatan += " " + c["catatan_jendela"]

    isi.append(p(catatan, abu))

    if c["galat"]:
        isi += [Spacer(1, 4), p(c["galat"])]

    buf = BytesIO()

    SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.9 * cm, rightMargin=1.9 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title="Resume Sekolah",
    ).build(isi)

    buf.seek(0)

    return buf


def buat_xlsx_online(items, nama_wilayah, nama_kategori="Semua"):
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Sekolah Online"

    # Judul
    ws.append(["DAFTAR SEKOLAH ONLINE - PRTG MONITORING"])
    ws["A1"].font = Font(bold=True, size=14, color="161A52")
    ws.append([f"Wilayah: {nama_wilayah} | Kategori: {nama_kategori} | Dicetak: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Total: {len(items)} Sekolah"])
    ws["A2"].font = Font(size=10, color="555555")
    ws.append([])

    # Header Tabel (tanpa Device PRTG, ID, dan IP)
    headers = ["No", "Nama Sekolah", "Wilayah", "Sensor", "Terakhir Diperiksa", "Online Sejak", "Durasi Online", "Kategori Uptime"]
    ws.append(headers)
    header_row = 4

    kepala_fill = PatternFill("solid", fgColor="161A52")
    kepala_font = Font(bold=True, color="FFFFFF")
    thin_border = Border(
        left=Side(style='thin', color='DDDDDD'),
        right=Side(style='thin', color='DDDDDD'),
        top=Side(style='thin', color='DDDDDD'),
        bottom=Side(style='thin', color='DDDDDD')
    )

    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=header_row, column=col_num)
        cell.fill = kepala_fill
        cell.font = kepala_font
        cell.alignment = Alignment(horizontal="center" if col_num == 1 else "left", vertical="center")

    for idx, item in enumerate(items, 1):
        row = [
            idx,
            item.get("nama", ""),
            item.get("wilayah", ""),
            item.get("sensor", ""),
            item.get("lastcheck", ""),
            item.get("online_sejak", "-"),
            item.get("uptimesince", ""),
            item.get("uptime_label", "")
        ]
        ws.append(row)
        curr_row = ws.max_row
        for col_num in range(1, len(row) + 1):
            c = ws.cell(row=curr_row, column=col_num)
            c.border = thin_border
            if col_num == 1:
                c.alignment = Alignment(horizontal="center", vertical="center")
            else:
                c.alignment = Alignment(vertical="center")

    widths = [6, 36, 16, 20, 20, 18, 18, 16]
    for col_num, w in enumerate(widths, 1):
        col_letter = ws.cell(row=header_row, column=col_num).column_letter
        ws.column_dimensions[col_letter].width = w

    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def buat_pdf_online(items, nama_wilayah, nama_kategori="Semua"):
    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title="Daftar Sekolah Online",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Heading1"], fontSize=16, leading=20, textColor=colors.HexColor("#161a52"))
    meta_style = ParagraphStyle("M", parent=styles["Normal"], fontSize=9, leading=12, textColor=colors.HexColor("#555555"))
    cell_style = ParagraphStyle("C", parent=styles["Normal"], fontSize=8, leading=10)
    cell_bold = ParagraphStyle("CB", parent=styles["Normal"], fontSize=8, leading=10, fontName="Helvetica-Bold")
    header_style = ParagraphStyle("H", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.white, fontName="Helvetica-Bold")

    elements = [
        Paragraph("DAFTAR SEKOLAH ONLINE - PRTG MONITORING", title_style),
        Paragraph(f"Wilayah: <b>{escape(str(nama_wilayah))}</b> &middot; Kategori: <b>{escape(str(nama_kategori))}</b> &middot; Dicetak: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} &middot; Total: <b>{len(items)} Sekolah Online</b>", meta_style),
        Spacer(1, 10),
    ]

    # Lebar total 26.6 cm (landscape A4 dikurangi margin). Tanpa kolom ID.
    col_widths = [1.0 * cm, 7.6 * cm, 3.4 * cm, 4.0 * cm, 4.0 * cm, 3.6 * cm, 3.0 * cm]
    headers = ["NO", "NAMA SEKOLAH", "WILAYAH", "SENSOR", "TERAKHIR DIPERIKSA", "ONLINE SEJAK", "DURASI ONLINE"]
    table_data = [[Paragraph(h, header_style) for h in headers]]

    for idx, item in enumerate(items, 1):
        row = [
            Paragraph(str(idx), cell_style),
            Paragraph(escape(str(item.get("nama", ""))), cell_bold),
            Paragraph(escape(str(item.get("wilayah", ""))), cell_style),
            Paragraph(escape(str(item.get("sensor", ""))), cell_style),
            Paragraph(escape(str(item.get("lastcheck", ""))), cell_style),
            Paragraph(escape(str(item.get("online_sejak", "-"))), cell_style),
            Paragraph(escape(str(item.get("uptimesince", ""))), cell_style)
        ]
        table_data.append(row)

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#161a52")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d4e4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fd")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(t)

    doc.build(elements)
    buf.seek(0)
    return buf


def buat_xlsx_offline(items, nama_wilayah, nama_kategori="Semua"):
    """Excel daftar gangguan offline (layout sama dengan Excel halaman online)."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Gangguan Offline"

    # Judul
    ws.append(["DAFTAR GANGGUAN OFFLINE - PRTG MONITORING"])
    ws["A1"].font = Font(bold=True, size=14, color="161A52")
    ws.append([f"Wilayah: {nama_wilayah} | Kategori: {nama_kategori} | Dicetak: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Total: {len(items)} Sekolah"])
    ws["A2"].font = Font(size=10, color="555555")
    ws.append([])

    # Header Tabel (tanpa Device PRTG, ID, dan IP)
    headers = ["No", "Nama Sekolah", "Wilayah", "Sensor Down", "Down Sejak (Perkiraan)", "Lama Down", "Kategori Down"]
    ws.append(headers)
    header_row = 4

    kepala_fill = PatternFill("solid", fgColor="161A52")
    kepala_font = Font(bold=True, color="FFFFFF")
    thin_border = Border(
        left=Side(style='thin', color='DDDDDD'),
        right=Side(style='thin', color='DDDDDD'),
        top=Side(style='thin', color='DDDDDD'),
        bottom=Side(style='thin', color='DDDDDD')
    )

    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=header_row, column=col_num)
        cell.fill = kepala_fill
        cell.font = kepala_font
        cell.alignment = Alignment(horizontal="center" if col_num == 1 else "left", vertical="center")

    for idx, item in enumerate(items, 1):
        row = [
            idx,
            item.get("nama", ""),
            item.get("wilayah", ""),
            f"{item.get('down_count', 0)}x",
            item.get("lastdown_text") or "-",
            item.get("lama_down", "-"),
            item.get("category", ""),
        ]
        ws.append(row)
        curr_row = ws.max_row
        for col_num in range(1, len(row) + 1):
            c = ws.cell(row=curr_row, column=col_num)
            c.border = thin_border
            if col_num == 1:
                c.alignment = Alignment(horizontal="center", vertical="center")
            else:
                c.alignment = Alignment(vertical="center")

    widths = [6, 36, 16, 14, 24, 18, 16]
    for col_num, w in enumerate(widths, 1):
        col_letter = ws.cell(row=header_row, column=col_num).column_letter
        ws.column_dimensions[col_letter].width = w

    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def buat_pdf_offline(items, nama_wilayah, nama_kategori="Semua"):
    """PDF daftar gangguan offline (layout sama dengan PDF halaman online)."""
    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title="Daftar Gangguan Offline",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Heading1"], fontSize=16, leading=20, textColor=colors.HexColor("#161a52"))
    meta_style = ParagraphStyle("M", parent=styles["Normal"], fontSize=9, leading=12, textColor=colors.HexColor("#555555"))
    cell_style = ParagraphStyle("C", parent=styles["Normal"], fontSize=8, leading=10)
    cell_bold = ParagraphStyle("CB", parent=styles["Normal"], fontSize=8, leading=10, fontName="Helvetica-Bold")
    header_style = ParagraphStyle("H", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.white, fontName="Helvetica-Bold")

    elements = [
        Paragraph("DAFTAR GANGGUAN OFFLINE - PRTG MONITORING", title_style),
        Paragraph(f"Wilayah: <b>{escape(str(nama_wilayah))}</b> &middot; Kategori: <b>{escape(str(nama_kategori))}</b> &middot; Dicetak: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} &middot; Total: <b>{len(items)} Sekolah Offline</b>", meta_style),
        Spacer(1, 10),
    ]

    # Lebar total 26.6 cm (landscape A4 dikurangi margin). Tanpa kolom ID.
    col_widths = [1.0 * cm, 8.0 * cm, 3.6 * cm, 3.4 * cm, 3.0 * cm, 4.6 * cm, 3.0 * cm]
    headers = ["NO", "NAMA SEKOLAH", "WILAYAH", "KATEGORI", "SENSOR DOWN", "DOWN SEJAK", "LAMA DOWN"]
    table_data = [[Paragraph(h, header_style) for h in headers]]

    for idx, item in enumerate(items, 1):
        row = [
            Paragraph(str(idx), cell_style),
            Paragraph(escape(str(item.get("nama", ""))), cell_bold),
            Paragraph(escape(str(item.get("wilayah", ""))), cell_style),
            Paragraph(escape(str(item.get("category", ""))), cell_style),
            Paragraph(f"{item.get('down_count', 0)}x", cell_style),
            Paragraph(escape(str(item.get("lastdown_text") or "-")), cell_style),
            Paragraph(escape(str(item.get("lama_down", "-"))), cell_style),
        ]
        table_data.append(row)

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#161a52")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d4e4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fd")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(t)

    doc.build(elements)
    buf.seek(0)
    return buf


# ===== AKHIR BAGIAN 1 =====
# Lanjutkan dengan menempel BAGIAN 2 tepat di bawah baris ini.


# ============================================================
# FAVICON (ikon di tab browser) - logo yang sama dengan di dashboard
# ============================================================

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -7.5 100 100" fill="none" stroke-width="13">
<path d="M17.1 71 A38 38 0 0 1 25.6 22.9" stroke="#b5d334"/>
<path d="M25.6 22.9 A38 38 0 0 1 74.4 22.9" stroke="#f5a31a"/>
<path d="M74.4 22.9 A38 38 0 0 1 82.9 71" stroke="#f0156b"/>
<polygon points="22,33 52.2,48.7 47.8,55.3" fill="#0b2a6b" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round"/>
<circle cx="50" cy="52" r="4.5" fill="#0b2a6b" stroke="#ffffff" stroke-width="1.5"/>
</svg>"""


# ============================================================
# CSS & TEMPLATE
# ============================================================

SHARED_CSS = """
    :root {
        --navy: #161a52;
        --navy-2: #0f1238;
        --biru: #1d5fd6;
        --biru-muda: #a9cdf5;
        --hijau: #1fa83a;
        --kuning: #f5c542;
        --kuning-tua: #c07a1e;
        --merah: #d92d2d;
        --bg-atas: #f1f0ff;
        --bg-bawah: #c5d9e6;
        --card: #ffffff;
        --border: #e6e9f2;
        --text: #141a2b;
        --muted: #8a90a2;
    }
    * { box-sizing: border-box; }
    body {
        font-family: 'Inter', 'Segoe UI', Arial, sans-serif;
        background: linear-gradient(180deg, var(--bg-atas) 0%, var(--bg-bawah) 100%);
        background-attachment: fixed;
        margin: 0;
        color: var(--text);
        min-height: 100vh;
    }
    button, input { font-family: inherit; }

    /* ---------- Global Search Dashboard ---------- */
    .global-search-card {
        padding: 20px 24px;
        position: relative;
        z-index: 30;
        border: 1px solid var(--border);
        transition: box-shadow 0.2s ease, border-color 0.2s ease;
    }
    .global-search-card:focus-within {
        border-color: var(--biru);
        box-shadow: 0 8px 24px rgba(29, 95, 214, 0.12);
    }
    .gs-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 12px;
        margin-bottom: 14px;
    }
    .gs-title-wrap {
        display: flex;
        align-items: center;
        gap: 12px;
    }
    .gs-icon-box {
        width: 38px;
        height: 38px;
        border-radius: 10px;
        background: rgba(29, 95, 214, 0.1);
        color: var(--biru);
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
    }
    .gs-icon-box svg { width: 20px; height: 20px; }
    .gs-title {
        margin: 0;
        font-size: 17px;
        font-weight: 700;
        color: var(--navy);
        line-height: 1.25;
    }
    .gs-sub {
        font-size: 12px;
        color: var(--muted);
        margin-top: 2px;
    }
    .gs-meta-stats {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
    }
    .gs-stat-pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 12px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 500;
        background: #f1f4fb;
        color: var(--text);
        border: 1px solid var(--border);
    }
    .gs-stat-pill b { font-weight: 700; }
    .gs-stat-pill.offline { background: #fde8ea; color: #b71c1c; border-color: #f8b4bc; }
    .gs-stat-pill.online { background: #e6f9ed; color: #1b5e20; border-color: #b7e4c4; }
    .gs-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        display: inline-block;
    }
    .gs-dot.merah { background: #d92d2d; box-shadow: 0 0 5px rgba(217,45,45,0.6); }
    .gs-dot.hijau { background: #1fa83a; box-shadow: 0 0 5px rgba(31,168,58,0.6); }

    .gs-input-box {
        position: relative;
        display: flex;
        align-items: center;
        width: 100%;
    }
    .gs-search-icon {
        position: absolute;
        left: 16px;
        width: 18px;
        height: 18px;
        color: var(--muted);
        pointer-events: none;
        transition: color 0.2s;
    }
    .gs-input {
        width: 100%;
        height: 48px;
        padding: 0 46px 0 46px;
        font-size: 14.5px;
        border-radius: 12px;
        border: 1.5px solid var(--border);
        background: #f7f9fd;
        color: var(--text);
        outline: none;
        transition: all 0.2s ease;
    }
    .gs-input:focus {
        background: #ffffff;
        border-color: var(--biru);
        box-shadow: 0 0 0 3px rgba(29, 95, 214, 0.15);
    }
    .gs-input-box:focus-within .gs-search-icon {
        color: var(--biru);
    }
    .gs-clear {
        position: absolute;
        right: 12px;
        width: 28px;
        height: 28px;
        border: none;
        background: #e4e8f3;
        color: #4b5578;
        border-radius: 50%;
        font-size: 18px;
        line-height: 1;
        cursor: pointer;
        display: none;
        align-items: center;
        justify-content: center;
        transition: background 0.15s;
    }
    .gs-clear:hover { background: #d0d7e8; color: #111; }

    /* Dropdown results */
    .gs-dropdown {
        position: absolute;
        top: calc(100% + 8px);
        left: 0;
        right: 0;
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 14px;
        box-shadow: 0 16px 36px rgba(16,24,64,0.18);
        overflow: hidden;
        z-index: 100;
    }
    .gs-dropdown-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 10px 18px;
        background: #f6f8fc;
        border-bottom: 1px solid var(--border);
        font-size: 12px;
        color: var(--muted);
        font-weight: 600;
    }
    .gs-hint { font-weight: 400; font-size: 11.5px; color: var(--muted); }
    .gs-list {
        max-height: 380px;
        overflow-y: auto;
        padding: 6px 0;
    }
    .gs-item {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 18px;
        text-decoration: none;
        color: var(--text);
        border-bottom: 1px solid rgba(0,0,0,0.04);
        transition: background 0.15s ease;
        cursor: pointer;
        gap: 14px;
    }
    .gs-item:last-child { border-bottom: none; }
    .gs-item:hover, .gs-item.fokus {
        background: #eef4fe;
    }
    .gs-item-left {
        display: flex;
        flex-direction: column;
        gap: 4px;
        min-width: 0;
    }
    .gs-item-nama {
        font-size: 14px;
        font-weight: 700;
        color: var(--navy);
        line-height: 1.3;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .gs-item-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        font-size: 12px;
        color: var(--muted);
    }
    .gs-badge-wilayah {
        background: #e9eefb;
        color: #28448f;
        padding: 2px 8px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 11.5px;
    }
    .gs-item-id {
        color: var(--muted);
        font-family: monospace;
        font-size: 11.5px;
    }
    .gs-item-right {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 4px;
        flex-shrink: 0;
    }
    .gs-status-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 700;
        white-space: nowrap;
    }
    .gs-status-badge.offline {
        background: #fde8ea;
        color: #c62828;
        border: 1px solid #f8b4bc;
    }
    .gs-status-badge.online {
        background: #e6f9ed;
        color: #2e7d32;
        border: 1px solid #b7e4c4;
    }
    .gs-item-ket {
        font-size: 11.5px;
        color: var(--muted);
    }
    .gs-empty {
        padding: 28px 20px;
        text-align: center;
        color: var(--muted);
        font-size: 13.5px;
    }
    .gs-empty-icon { font-size: 24px; margin-bottom: 6px; }

    /* Dark theme for global search */
    html[data-theme="dark"] .global-search-card {
        background: var(--card);
        border-color: var(--border);
    }
    html[data-theme="dark"] .gs-title { color: #ffffff; }
    html[data-theme="dark"] .gs-stat-pill { background: #151a36; border-color: var(--border); color: #c3caea; }
    html[data-theme="dark"] .gs-stat-pill.offline { background: #3a1520; color: #ff8a9b; border-color: #632230; }
    html[data-theme="dark"] .gs-stat-pill.online { background: #133320; color: #7de8a0; border-color: #1f5733; }
    html[data-theme="dark"] .gs-input {
        background: #151a36;
        border-color: var(--border);
        color: var(--text);
    }
    html[data-theme="dark"] .gs-input:focus {
        background: #1c2246;
        border-color: var(--biru);
    }
    html[data-theme="dark"] .gs-dropdown {
        background: var(--card);
        border-color: var(--border);
        box-shadow: 0 16px 36px rgba(0,0,0,0.5);
    }
    html[data-theme="dark"] .gs-dropdown-top {
        background: #151a36;
        border-color: var(--border);
        color: var(--muted);
    }
    html[data-theme="dark"] .gs-item {
        border-bottom-color: rgba(255,255,255,0.05);
        color: var(--text);
    }
    html[data-theme="dark"] .gs-item:hover,
    html[data-theme="dark"] .gs-item.fokus {
        background: #232c58;
    }
    html[data-theme="dark"] .gs-item-nama { color: #ffffff; }
    html[data-theme="dark"] .gs-badge-wilayah { background: #263363; color: #9bb7f5; }
    html[data-theme="dark"] .gs-clear { background: #2b3358; color: #e8ebf7; }
    html[data-theme="dark"] .gs-clear:hover { background: #3a4575; }
"""

# Header atas: satu sumber gaya (tab_prtg.TOPBAR_CSS) untuk semua halaman.
SHARED_CSS += tab_prtg.TOPBAR_CSS

SHARED_CSS += """
    .global-search-card { padding: 12px 16px; }
    .global-search-card .gs-header { margin-bottom: 10px; flex-wrap: nowrap; }
    .global-search-card .gs-icon-box { width: 34px; height: 34px; border-radius: 9px; }
    .global-search-card .gs-icon-box svg { width: 18px; height: 18px; }
    .global-search-card .gs-title { font-size: 15px; }
    .global-search-card .gs-sub { font-size: 11.5px; }
    .global-search-card .gs-input { height: 40px; font-size: 14px; border-radius: 10px; }
    .global-search-card .gs-search-icon { left: 14px; width: 17px; height: 17px; }
    .global-search-card .gs-input { padding-left: 42px; }
    .global-search-card .gs-meta-stats { flex-shrink: 0; }
    @media (max-width: 700px) {
        .global-search-card .gs-header { flex-wrap: wrap; }
    }
"""

SHARED_CSS += """
    .hint-resume {
        display: inline-flex; align-items: center; gap: 6px;
        margin-left: 12px; padding: 4px 12px;
        background: #eaf1ff; color: #1d3a8a;
        border: 1px solid #c9daf8; border-radius: 999px;
        font-size: 12px; font-weight: 600; line-height: 1.3;
        text-transform: none; letter-spacing: 0; white-space: nowrap;
        vertical-align: middle;
    }
    .hint-resume svg { width: 13px; height: 13px; flex-shrink: 0; }
    html[data-theme="dark"] .hint-resume {
        background: #232c58; color: #b9ccff; border-color: #34417a;
    }
"""

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Login - Dashboard Sekolah</title>
<link rel="icon" type="image/svg+xml" href="{{ url_for('favicon') }}">
<style>
{{ shared_css | safe }}
    body { display: flex; align-items: center; justify-content: center; padding: 20px; }
    .box {
        background: var(--card); border-radius: 18px; padding: 32px; width: 100%;
        max-width: 370px; box-shadow: 0 18px 40px rgba(22,26,82,0.18);
        border-top: 6px solid var(--navy);
    }
    .logo {
        display: flex; align-items: center; justify-content: center;
        gap: 12px; margin-bottom: 8px;
    }
    .logo-ikon { width: 52px; height: 47px; flex-shrink: 0; }
    .logo-teks { text-align: left; line-height: 1.1; }
    .logo-teks .baris-1 {
        font-size: 15px; font-weight: 800; letter-spacing: 1.2px;
        color: var(--biru); white-space: nowrap;
    }
    .logo-teks .baris-2 {
        font-size: 30px; font-weight: 800; letter-spacing: 3px;
        color: var(--navy); margin-top: 2px;
    }
    .box .sub { font-size: 12.5px; color: var(--muted); text-align: center; margin-bottom: 22px; }
    .field { margin-bottom: 14px; }
    .field label { display: block; font-size: 12.5px; font-weight: 600; margin-bottom: 6px; }
    .field input {
        width: 100%; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border);
        background: #f6f8fc; font-size: 13.5px;
    }
    .btn {
        width: 100%; background: var(--navy); color: #fff; border: none; padding: 11px;
        border-radius: 10px; font-weight: 700; cursor: pointer; margin-top: 6px;
    }
    .err { background: #fef1f2; color: var(--merah); border-radius: 8px; padding: 9px 12px;
           font-size: 12.5px; margin-bottom: 14px; }
</style>
</head>
<body>
    <div class="box">
        <div class="logo">
            <svg class="logo-ikon" viewBox="0 0 100 90" fill="none" stroke-width="13">
                <path d="M17.1 71 A38 38 0 0 1 25.6 22.9" stroke="#b5d334"/>
                <path d="M25.6 22.9 A38 38 0 0 1 74.4 22.9" stroke="#f5a31a"/>
                <path d="M74.4 22.9 A38 38 0 0 1 82.9 71" stroke="#f0156b"/>
                <polygon points="22,33 52.2,48.7 47.8,55.3" fill="#0b2a6b" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round"/>
                <circle cx="50" cy="52" r="4.5" fill="#0b2a6b" stroke="#ffffff" stroke-width="1.5"/>
            </svg>
            <div class="logo-teks">
                <div class="baris-1">NETWORK MONITORING</div>
                <div class="baris-2">SEKOLAH</div>
            </div>
        </div>
        <div class="sub">Dashboard gangguan sekolah</div>
        {% if error %}<div class="err">{{ error }}</div>{% endif %}
        <form method="POST">
            <div class="field"><label>Username</label><input name="username" autofocus required></div>
            <div class="field"><label>Password</label><input type="password" name="password" required></div>
            <button class="btn" type="submit">Masuk</button>
        </form>
    </div>
</body>
</html>
"""

TEMPLATE = """
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard Gangguan Sekolah</title>
<link rel="icon" type="image/svg+xml" href="{{ url_for('favicon') }}">
<script>
    /* Terapkan tema tersimpan sebelum halaman digambar (tanpa kedip) */
    try {
        var tema = localStorage.getItem('tema');
        if (tema) { document.documentElement.setAttribute('data-theme', tema); }
    } catch (e) {}
</script>
<style>
{{ shared_css | safe }}

    .search { display: flex; }
    .search input {
        border: none; outline: none; padding: 8px 14px; border-radius: 6px 0 0 6px;
        width: 190px; font-size: 13px;
    }
    .search span {
        background: #fff; border-left: 1px solid var(--border); padding: 8px 12px;
        border-radius: 0 6px 6px 0; color: var(--navy); font-size: 13px;
    }

    /* ---------- Tab Navigasi Halaman Utama ---------- */
    .page-nav-bar {
        background: #0c0f2e;
        display: flex;
        gap: 8px;
        padding: 0 34px;
        border-top: 1px solid rgba(255,255,255,0.06);
        border-bottom: 1px solid rgba(255,255,255,0.08);
        overflow-x: auto;
    }
    .page-nav-tab {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        padding: 13px 20px 14px;
        font-size: 13.5px;
        font-weight: 700;
        color: #929cd0;
        text-decoration: none;
        border-bottom: 3px solid transparent;
        transition: all 0.2s ease;
        white-space: nowrap;
    }
    .page-nav-tab:hover {
        color: #ffffff;
        background: rgba(255,255,255,0.04);
    }
    .page-nav-tab.offline.aktif {
        color: #ffffff;
        border-bottom-color: #ff4757;
        background: rgba(255, 71, 87, 0.08);
    }
    .page-nav-tab.online.aktif {
        color: #ffffff;
        border-bottom-color: #2ed573;
        background: rgba(46, 213, 115, 0.08);
    }
    .tab-dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
        position: relative;
    }
    .tab-dot.offline {
        background: #ff4757;
        box-shadow: 0 0 8px #ff4757;
    }
    .tab-dot.offline::after {
        content: ""; position: absolute; inset: -2px; border-radius: 50%;
        border: 2px solid #ff4757; animation: riak 1.6s ease-out infinite;
    }
    .tab-dot.online {
        background: #2ed573;
        box-shadow: 0 0 8px #2ed573;
    }
    .tab-dot.online::after {
        content: ""; position: absolute; inset: -2px; border-radius: 50%;
        border: 2px solid #2ed573; animation: riak 1.6s ease-out infinite;
    }
    .tab-badge {
        padding: 2px 9px;
        border-radius: 999px;
        font-size: 11.5px;
        font-weight: 800;
    }
    .tab-badge.merah {
        background: rgba(255, 71, 87, 0.2);
        color: #ff7675;
        border: 1px solid rgba(255, 71, 87, 0.35);
    }
    .page-nav-tab.offline.aktif .tab-badge.merah {
        background: #ff4757;
        color: #ffffff;
        border-color: #ff4757;
    }
    .tab-badge.hijau {
        background: rgba(46, 213, 115, 0.2);
        color: #7bed9f;
        border: 1px solid rgba(46, 213, 115, 0.35);
    }
    .page-nav-tab.online.aktif .tab-badge.hijau {
        background: #2ed573;
        color: #ffffff;
        border-color: #2ed573;
    }
    @media (max-width: 600px) {
        .page-nav-bar { padding: 0 10px; }
        .page-nav-tab { padding: 10px 14px; font-size: 12.5px; }
    }

    /* ---------- Navbar wilayah ---------- */
    .navwil {
        background: #0f1238; display: flex; gap: 4px; padding: 0 34px;
        overflow-x: auto; border-top: 1px solid rgba(255,255,255,0.08);
    }
    .nw {
        color: #b8c0ee; text-decoration: none; font-size: 13px; font-weight: 600;
        padding: 12px 16px; border-bottom: 3px solid transparent; white-space: nowrap;
        display: inline-flex; align-items: center; gap: 7px;
    }
    .nw:hover { color: #fff; background: rgba(255,255,255,0.06); }
    .nw.aktif { color: #fff; border-bottom-color: #3b8cff; }
    .nw b {
        background: rgba(255,255,255,0.14); border-radius: 999px;
        padding: 1px 8px; font-size: 11.5px;
    }
    .nw b.ada { background: #d92d2d; color: #fff; }
    @media (max-width: 600px) { .navwil { padding: 0 10px; } }

    .btn-resume {
        display: inline-block; background: var(--navy); color: #fff; text-decoration: none;
        padding: 5px 12px; border-radius: 999px; font-size: 12px; font-weight: 600;
    }
    .btn-resume:hover { background: #2a3190; }
    html[data-theme="dark"] .btn-resume { background: #3a4aa8; }
    .pilih-wilayah {
        border: 1px solid var(--border); background: var(--card); color: var(--text);
        border-radius: 8px; padding: 5px 8px; font-size: 12px; max-width: 190px;
    }

    /* ---------- Layout ---------- */
    .container { width: 100%; max-width: 100%; margin: 0 auto; padding: 26px 34px 50px; }
    .layout { display: block; }
    .main { display: flex; flex-direction: column; gap: 22px; min-width: 0; }

    .card {
        background: var(--card); border-radius: 14px;
        box-shadow: 0 4px 14px rgba(22,26,82,0.07);
    }

    /* ---------- Kartu kategori (off) ---------- */
    .kartu-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }
    .kartu-grid .k-judul { font-size: 15px; }
    .k-avatar.ungu { --dot: #8b5cf6; --glow: #8b5cf6; }
    .k-stat b.k { color: var(--kuning-tua); }
    .k-card { padding: 34px 18px 20px; text-align: center; position: relative; }

    /* Titik indikator kecil di pojok kanan: bercahaya & kelap-kelip */
    .k-avatar {
        position: absolute; top: 14px; right: 14px;
        width: 12px; height: 12px; border-radius: 50%;
        background: var(--dot);
        box-shadow: 0 0 6px 2px var(--glow);
        animation: kelap 1.6s ease-in-out infinite;
    }
    .k-avatar::after {
        content: ""; position: absolute; inset: -2px; border-radius: 50%;
        border: 2px solid var(--glow);
        animation: riak 1.6s ease-out infinite;
    }
    .k-avatar.kuning { --dot: #f5a623; --glow: #f5a623; }
    .k-avatar.merah  { --dot: #ff3a3a; --glow: #ff3a3a; }
    .k-avatar.hitam  { --dot: #25272b; --glow: #7d8494; }
    .k-avatar.biru   { --dot: #2f7bff; --glow: #2f7bff; }
    @keyframes kelap {
        0%, 100% { opacity: 1;    box-shadow: 0 0 4px 1px var(--glow), 0 0 12px 3px var(--glow); }
        50%      { opacity: 0.35; box-shadow: 0 0 2px 0 var(--glow); }
    }
    @keyframes riak {
        0%   { transform: scale(1);   opacity: 0.8; }
        100% { transform: scale(2.6); opacity: 0; }
    }

    /* Titik indikator di judul panel (sama dengan halaman online) */
    .panel-dot {
        --glow: #ff4757;
        width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0;
        background: #ff4757;
        box-shadow: 0 0 8px 2px var(--glow);
        animation: kelap 1.6s ease-in-out infinite;
    }

    .k-judul { font-size: 17px; font-weight: 600; }
    .k-sub { font-size: 12px; color: var(--muted); margin-bottom: 14px; }
    .k-stat { display: flex; flex-direction: column; text-align: left; }
    .k-stat > div {
        display: flex; justify-content: space-between; align-items: center;
        padding: 10px 4px; border-top: 1px solid var(--border);
    }
    .k-stat b { display: block; font-size: 14px; font-weight: 600; white-space: nowrap; order: 2; }
    .k-stat b.m { color: var(--merah); }
    .k-stat b.b { color: var(--biru); }
    .k-stat span { font-size: 12px; color: var(--muted); order: 1; }

    /* ---------- Grafik tren ---------- */
    .kosong { color: var(--muted); font-style: italic; padding: 18px; }

    .grafik-card { padding: 20px 22px 16px; }
    .grafik-head { display: flex; align-items: flex-start; justify-content: space-between;
                   gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }
    .grafik-head h3 { margin: 0; font-size: 16px; font-weight: 600; }
    .grafik-head .ket { font-size: 12px; color: var(--muted); margin-top: 2px; }
    .stat-row { display: flex; gap: 22px; flex-wrap: wrap; }
    .stat b { display: block; font-size: 20px; font-weight: 600; line-height: 1.1; }
    .stat span { font-size: 11px; color: var(--muted); }
    .stat b.naik  { color: var(--merah); }
    .stat b.turun { color: var(--hijau); }

    .legend { display: flex; gap: 8px; flex-wrap: wrap; margin: 6px 0 4px; }
    .lg {
        border: 1px solid var(--border); background: #fff; border-radius: 999px;
        padding: 4px 12px 4px 9px; font-size: 12px; cursor: pointer; display: inline-flex;
        align-items: center; gap: 6px; color: var(--text);
    }
    .lg i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .lg.mati { opacity: 0.4; text-decoration: line-through; }

    .grafik-svg { width: 100%; height: auto; display: block; }
    .grafik-svg .g-grid { stroke: #e6e9f2; stroke-width: 1; }
    .grafik-svg text { font-size: 11px; fill: #8a90a2; font-family: 'Segoe UI', Arial, sans-serif; }
    .grafik-svg circle { cursor: pointer; }
    .grafik-svg circle:hover { r: 6; }

    /* ---------- Ringkasan Gangguan ---------- */
    .panel-head {
        display: flex; align-items: center; justify-content: space-between; gap: 12px;
        flex-wrap: wrap; padding: 18px 24px; border-bottom: 1px solid var(--border);
    }
    .panel-head h2 { margin: 0; font-size: 17px; font-weight: 600; }
    .panel-head .ket { font-size: 12px; color: var(--muted); margin-top: 2px; }
    .filter { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .fchip {
        border: 1px solid var(--border); background: #fff; color: #6b7390;
        padding: 7px 15px; border-radius: 999px; font-size: 12.5px; cursor: pointer;
    }
    .fchip:hover { border-color: #b9c0d9; }
    .fchip.aktif { background: var(--navy); color: #fff; border-color: var(--navy); font-weight: 600; }
    .btn-wa {
        border: none; background: #0f7a52; color: #fff; padding: 8px 16px;
        border-radius: 999px; font-weight: 700; cursor: pointer; font-size: 12.5px;
    }
    .btn-wa:hover { background: #0b6444; }
    .btn-wa:disabled { opacity: 0.6; cursor: not-allowed; }

    .btn-unduh {
        text-decoration: none; color: #fff; font-weight: 700; font-size: 12.5px;
        padding: 8px 16px; border-radius: 999px; display: inline-flex; align-items: center;
        gap: 6px; cursor: pointer; border: none; transition: all 0.2s ease;
    }
    .btn-unduh.pdf { background: #c0392b; }
    .btn-unduh.pdf:hover { background: #a93226; transform: translateY(-1px); }
    .btn-unduh.xls { background: #0f7a52; }
    .btn-unduh.xls:hover { background: #0b6444; transform: translateY(-1px); }

    .tabel-scroll { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 14px 24px; text-align: left; border-bottom: 1px solid var(--border);
             font-size: 13px; vertical-align: middle; }
    th { background: #f7f8fc; font-size: 10.5px; text-transform: uppercase;
         color: var(--muted); letter-spacing: 0.5px; }
    td.jml { font-size: 14px; font-weight: 700; }

    .badge-lg {
        display: inline-flex; align-items: center; gap: 7px; padding: 7px 14px;
        border-radius: 999px; font-size: 12px; font-weight: 700; white-space: nowrap;
    }
    .badge-lg i { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
    .badge-lg.kuning { background: #fff3c4; color: #765700; }
    .badge-lg.kuning i { background: #e0a800; }
    .badge-lg.merah  { background: #fde2e6; color: #8b1626; }
    .badge-lg.merah i { background: var(--merah); }
    .badge-lg.hitam  { background: #25272b; color: #fff; }
    .badge-lg.hitam i { background: #fff; }

    .badge-wilayah {
        display: inline-block; background: #eef2ff; color: #3730a3;
        padding: 3px 8px; border-radius: 6px; font-size: 11.5px; font-weight: 600;
        white-space: nowrap;
    }

    .chips-wrap { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 6px; max-height: 190px; overflow-y: auto; }
    .chips-wrap .chip-link {
        display: flex; align-items: center; gap: 8px; min-width: 0; text-decoration: none;
        background: #f6f8fc; border: 1px solid var(--border); border-radius: 8px;
        padding: 6px 10px; font-family: inherit; font-size: 12.5px; color: var(--text, #1b2147);
        transition: all 0.15s ease;
    }
    .chips-wrap .chip-link span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
    .chips-wrap .chip-link:hover { background: var(--navy); border-color: var(--navy); color: #fff; }
    .chips-wrap .chip-link.sembunyi { display: none !important; }
    html[data-theme="dark"] .chips-wrap .chip-link { background: var(--card); color: #c3caea; }
    html[data-theme="dark"] .chips-wrap .chip-link:hover { background: #3a4aa8; border-color: #3a4aa8; color: #fff; }
    .cari-chip-wrap input.cari-chip:focus { border-color: var(--navy); }
    .cari-chip-wrap { padding: 4px 24px 14px; }
    .cari-chip-wrap input.cari-chip {
        width: 100%; max-width: 420px; padding: 10px 14px; border: 1px solid var(--border);
        border-radius: 999px; font-size: 13.5px; background: var(--card, #fff); color: inherit;
    }
    .chip { display: inline-block; background: #f6f8fc; border: 1px solid var(--border);
            padding: 3px 9px; border-radius: 6px; font-size: 11.5px;
            font-family: Consolas, monospace; }
    .kosong-kecil { color: var(--muted); }

    .mata {
        height: 36px; padding: 0 14px; border-radius: 999px; border: 1px solid var(--border);
        background: #fff; cursor: pointer; display: inline-flex; align-items: center; gap: 7px;
        font: inherit; font-size: 12.5px; font-weight: 700; white-space: nowrap;
        color: var(--navy); transition: all 0.2s ease;
    }
    .mata:hover { background: var(--navy); color: #fff; border-color: var(--navy); }
    .mata svg { width: 16px; height: 16px; flex-shrink: 0; }

    .rinci { width: 100%; border-collapse: collapse; }
    .rinci th { background: transparent; padding: 12px 18px; font-size: 11px; text-transform: uppercase;
                color: var(--muted); letter-spacing: 0.5px; border-bottom: 1px solid var(--border); text-align: left; }
    .rinci td { padding: 12px 18px; border-bottom: 1px solid var(--border); font-size: 13px;
                vertical-align: middle; background: transparent; }

    .footer { text-align: center; color: #5d6785; font-size: 11.5px; margin-top: 22px; line-height: 1.6; }

    /* ---------- Pop up rincian ---------- */
    .modal-box.lebar { max-width: 1060px; padding: 0; display: flex; flex-direction: column;
                       max-height: 86vh; overflow: hidden; border-radius: 16px;
                       box-shadow: 0 16px 40px rgba(0,0,0,0.22); }
    .mr-head { display: flex; align-items: center; justify-content: space-between;
               padding: 18px 24px 12px; border-bottom: 1px solid var(--border); }
    .mr-head h3 { margin: 0; font-size: 17px; font-weight: 700; }
    .mr-tutup { border: none; background: #eef1f7; width: 32px; height: 32px; border-radius: 50%;
                font-size: 20px; line-height: 1; cursor: pointer; color: #4b5578;
                display: inline-flex; align-items: center; justify-content: center; }
    .mr-tutup:hover { background: #dfe4f0; }
    .mr-search { padding: 14px 24px 12px; border-bottom: 1px solid var(--border); }
    .mr-search input { width: 100%; padding: 10px 14px; border-radius: 10px;
                       border: 1px solid var(--border); background: #f6f8fc; font-size: 13px; outline: none;
                       transition: all 0.2s; }
    .mr-search input:focus { border-color: var(--biru); background: #fff;
                             box-shadow: 0 0 0 3px rgba(29, 95, 214, 0.12); }
    .mr-body { overflow-y: auto; padding: 0; max-height: calc(86vh - 140px); }
    .mr-body .rinci th { position: sticky; top: 0; background: #f7f8fc; z-index: 2; }

    /* ---------- Modal & toast ---------- */
    .modal {
        display: none; position: fixed; inset: 0; background: rgba(10,12,40,0.55);
        align-items: center; justify-content: center; padding: 20px; z-index: 50;
    }
    .modal.show { display: flex; }
    .modal-box { background: var(--card); width: 100%; max-width: 440px; padding: 24px;
                 border-radius: 16px; }
    .modal-box h3 { margin-top: 0; }
    .preview { background: #f7f8fc; border: 1px solid var(--border); border-radius: 8px;
               padding: 10px; white-space: pre-wrap; font-family: Consolas, monospace;
               font-size: 12px; max-height: 220px; overflow: auto; margin: 12px 0; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
    .modal-actions button { border: none; padding: 9px 15px; border-radius: 8px;
                            font-weight: 700; cursor: pointer; }
    .batal { background: #e5e7eb; }
    .konfirmasi { background: var(--hijau); color: #fff; }
    .konfirmasi:disabled { opacity: 0.6; cursor: not-allowed; }

    .toast { display: none; position: fixed; right: 22px; bottom: 22px; padding: 12px 16px;
             border-radius: 9px; color: #fff; font-size: 12.5px; z-index: 100; }
    .toast.show { display: block; }
    .toast.sukses { background: var(--hijau); }
    .toast.gagal { background: var(--merah); }

    /* ---------- Mode gelap ---------- */
    html[data-theme="dark"] {
        --bg-atas: #0b0e22;
        --bg-bawah: #151a36;
        --card: #1a2040;
        --border: #2b3358;
        --text: #e8ebf7;
        --muted: #8f98ba;
        --biru: #4d8dff;
        --navy: #0f1238;
    }
    html[data-theme="dark"] .card { box-shadow: 0 4px 16px rgba(0,0,0,0.35); }
    html[data-theme="dark"] .k-avatar.hitam { --dot: #d5d9e6; --glow: #aeb4c6; }
    html[data-theme="dark"] .k-stat b.m { color: #ff6b6b; }
    html[data-theme="dark"] th { background: #212850; }
    html[data-theme="dark"] .mr-body .rinci th { background: #212850; }
    html[data-theme="dark"] .rinci th { border-bottom-color: var(--border); }
    html[data-theme="dark"] .rinci td { border-bottom-color: var(--border); }
    html[data-theme="dark"] .chip { background: #232a4f; color: var(--text); }
    html[data-theme="dark"] .badge-wilayah { background: #312e81; color: #c7d2fe; }
    html[data-theme="dark"] .fchip { background: var(--card); color: #aab2d3; }
    html[data-theme="dark"] .fchip:hover { border-color: #4a5589; }
    html[data-theme="dark"] .fchip.aktif { background: #3a4aa8; border-color: #3a4aa8; color: #fff; }
    html[data-theme="dark"] .lg { background: var(--card); }
    html[data-theme="dark"] .mata { background: var(--card); color: #c3caea; }
    html[data-theme="dark"] .mata:hover { background: #3a4aa8; color: #fff; }
    html[data-theme="dark"] .mr-tutup { background: #232a4f; color: #c3caea; }
    html[data-theme="dark"] .mr-tutup:hover { background: #2d3560; }
    html[data-theme="dark"] .mr-search input { background: #151a36; color: var(--text); }
    html[data-theme="dark"] .mr-search input:focus { background: #1a2040; }
    html[data-theme="dark"] .preview { background: #151a36; color: var(--text); }
    textarea.preview { width: 100%; height: 260px; max-height: none; resize: vertical;
                       line-height: 1.5; color: var(--text); outline: none; display: block; }
    textarea.preview:focus { border-color: var(--biru); }
    .edit-info { font-size: 11.5px; color: var(--muted); margin: -4px 0 8px; }
    html[data-theme="dark"] .batal { background: #2b3358; color: var(--text); }
    html[data-theme="dark"] .badge-lg.kuning { background: #4a3a0c; color: #ffd966; }
    html[data-theme="dark"] .badge-lg.merah { background: #4a1a24; color: #ff9aa8; }
    html[data-theme="dark"] .badge-lg.hitam { background: #05060f; }
    html[data-theme="dark"] .grafik-svg .g-grid { stroke: #2b3358; }
    html[data-theme="dark"] .grafik-svg text { fill: #8f98ba; }
    html[data-theme="dark"] .grafik-svg circle { fill: var(--card); }
    html[data-theme="dark"] .footer { color: #8f98ba; }

    /* ---------- Responsif ---------- */
    @media (max-width: 1000px) {
        .kartu-grid { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 600px) {
        .container { padding: 18px 12px 30px; }
        .kartu-grid { grid-template-columns: 1fr; }
        .filter { width: 100%; justify-content: flex-start; }
    }
    @media screen and (min-width: 1024px) and (max-width: 2560px) {
        html { zoom: 1.12; }
    }
</style>
</head>
<body>

<div class="topbar">
    <div class="brand">
        <svg viewBox="0 0 100 90" fill="none" stroke-width="13">
            <path d="M17.1 71 A38 38 0 0 1 25.6 22.9" stroke="#b5d334"/>
            <path d="M25.6 22.9 A38 38 0 0 1 74.4 22.9" stroke="#f5a31a"/>
            <path d="M74.4 22.9 A38 38 0 0 1 82.9 71" stroke="#f0156b"/>
            <polygon points="22,33 52.2,48.7 47.8,55.3" fill="#0b2a6b" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round"/>
            <circle cx="50" cy="52" r="4.5" fill="#0b2a6b" stroke="#ffffff" stroke-width="1.5"/>
        </svg>
        <div class="brand-teks">
            <div class="b1">NETWORK MONITORING</div>
            <div class="b2">SEKOLAH</div>
        </div>
    </div>
    <div class="welcome">
        Selamat datang, Admin
        <small>Monitoring gangguan sekolah &middot; PRTG {{ prtg_server }}</small>
    </div>
    <div class="top-right">
        <div class="pill-top"><span class="dot"></span>Refresh <span id="countdown">60</span> dtk</div>
        <button type="button" class="tema-btn" id="tema-btn" title="Ganti mode gelap / terang">&#127769; Gelap</button>
        <a class="logout" href="{{ url_for('logout') }}">&#8617; Keluar</a>
    </div>
</div>

<div class="page-nav-bar">
    <a href="{{ url_for('index', w=wilayah_pilih) }}" class="page-nav-tab offline aktif">
        <span class="tab-dot offline"></span>
        <span class="tab-text">Dashboard Gangguan Offline</span>
        <span class="tab-badge merah">{{ total_offline }}</span>
    </a>
    <a href="{{ url_for('dashboard_online', w=wilayah_pilih) }}" class="page-nav-tab online">
        <span class="tab-dot online"></span>
        <span class="tab-text">Dashboard Sekolah Online</span>
        <span class="tab-badge hijau">{{ total_online }}</span>
    </a>
</div>

<div class="navwil">
    <a href="{{ url_for('index') }}" class="nw {% if wilayah_pilih == 'semua' %}aktif{% endif %}">
        &#127758; Semua Wilayah <b class="{{ 'ada' if total_semua }}">{{ total_semua }}</b>
    </a>
    {% for w in daftar_wilayah %}
    <a href="{{ url_for('index', w=w.nama) }}" class="nw {% if wilayah_pilih == w.nama %}aktif{% endif %}">
        &#128205; {{ w.nama }} <b class="{{ 'ada' if w.jumlah }}">{{ w.jumlah }}</b>
    </a>
    {% endfor %}
</div>

<div class="container">

<div class="layout">

    <!-- ================= KOLOM UTAMA ================= -->
    <div class="main">

        <!-- ================= GLOBAL SEARCH DASHBOARD ================= -->
        <div class="card global-search-card">
                        <div class="gs-header">
                <div class="gs-title-wrap">
                    <div class="gs-icon-box">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="11" cy="11" r="8"></circle>
                            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                        </svg>
                    </div>
                    <div>
                        <h2 class="gs-title">Pencarian Sekolah Global</h2>
                        <div class="gs-sub">Cari seluruh sekolah di semua wilayah &middot; Mencakup status Online maupun Offline</div>
                    </div>
                </div>
                <div class="gs-meta-stats">
                    <span class="gs-stat-pill offline"><i class="gs-dot merah"></i>Offline: <b>{{ total_offline }}</b></span>
                    <span class="gs-stat-pill">Total: <b>{{ total_offline }}</b></span>
                </div>
            </div>
            
            <div class="gs-input-box">
                <svg class="gs-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                </svg>
                <input type="text" id="global-search-input" class="gs-input" placeholder="Cari nama sekolah, wilayah, atau nomor ID PRTG..." autocomplete="off" spellcheck="false">
                <button type="button" id="global-search-clear" class="gs-clear" title="Bersihkan pencarian" aria-label="Bersihkan pencarian">&times;</button>
            </div>

            <div id="global-search-dropdown" class="gs-dropdown" style="display:none;">
                <div class="gs-dropdown-top">
                    <span id="gs-hasil-info">Hasil pencarian</span>
                    <span class="gs-hint">Gunakan tombol &uarr; &darr; lalu Enter atau Klik untuk membuka detail sekolah</span>
                </div>
                <div id="global-search-list" class="gs-list" role="listbox"></div>
            </div>
        </div>

                <!-- Kartu kategori + total -->
        <div class="kartu-grid">
            {% for k in kategori %}
            <div class="card k-card">
                <div class="k-avatar {{ k.warna }}"></div>
                <div class="k-judul">{{ k.judul }}</div>
                <div class="k-sub">sekolah down</div>
                <div class="k-stat">
                    <div><b class="m">{{ k.jumlah }}</b><span>Sekolah</span></div>
                    <div><b class="b">{{ k.rata }}</b><span>Rata-rata</span></div>
                    <div><b>{{ k.terlama }}</b><span>Terlama</span></div>
                </div>
            </div>
            {% endfor %}
            <div class="card k-card">
                <div class="k-avatar biru"></div>
                <div class="k-judul">Total Sekolah Down</div>
                <div class="k-sub">semua kategori</div>
                <div class="k-stat">
                    <div><b class="m">{{ total_kartu.jumlah }}</b><span>Sekolah</span></div>
                    <div><b class="b">{{ total_kartu.rata }}</b><span>Rata-rata</span></div>
                    <div><b>{{ total_kartu.terlama }}</b><span>Terlama</span></div>
                </div>
            </div>
            <div class="card k-card">
                <div class="k-avatar ungu"></div>
                <div class="k-judul">Baru Saja Down</div>
                <div class="k-sub">down &lt; 1 jam</div>
                <div class="k-stat">
                    <div><b class="m">{{ stat_offline.baru }}</b><span>Sekolah</span></div>
                    <div><b>{{ stat_offline.pct_baru }}%</b><span>Persentase</span></div>
                    <div><b class="k">{% if stat_offline.baru %}Waspada{% else %}Aman{% endif %}</b><span>Status</span></div>
                </div>
            </div>
        </div>

        <!-- Ringkasan Gangguan -->
        <div class="card">
            <div class="panel-head">
                <div style="display:flex;align-items:center;gap:10px;">
                    <div class="panel-dot"></div>
                    <div>
                        <h2>Daftar Gangguan Offline</h2>
                        <div class="ket">Wilayah: <strong>{{ nama_wilayah }}</strong> &middot; Diambil: <strong>{{ updated_at }}</strong> &middot; Total: <strong>{{ total_offline_wilayah }} sekolah</strong></div>
                    </div>
                </div>
                <div class="filter">
                    <button type="button" class="fchip aktif" data-f="semua">Semua</button>
                    <button type="button" class="fchip" data-f="kuning">&lt; 24 Jam</button>
                    <button type="button" class="fchip" data-f="merah">24 - 48 Jam</button>
                    <button type="button" class="fchip" data-f="hitam">&gt; 48 Jam</button>
                    <button type="button" class="btn-wa" id="btn-buka-modal">&#128228; Kirim WhatsApp</button>
                    <a class="btn-unduh pdf" id="btn-export-pdf" href="{{ url_for('unduh_offline', jenis='pdf', w=wilayah_pilih) }}" title="Unduh PDF">&#128196; Unduh PDF</a>
                    <a class="btn-unduh xls" id="btn-export-excel" href="{{ url_for('unduh_offline', jenis='xlsx', w=wilayah_pilih) }}" title="Unduh Excel">&#128202; Unduh Excel</a>
                </div>
            </div>
            
            <div class="tabel-scroll">
            <table>
                <thead>
                    <tr>
                        <th style="width:160px;">KATEGORI</th>
                        <th style="width:100px;">JUMLAH</th>
                        <th>SEKOLAH <span class="hint-resume"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11V5a2 2 0 0 1 4 0v6"/><path d="M13 11V9a2 2 0 0 1 4 0v5a6 6 0 0 1-6 6h-1a6 6 0 0 1-5-3l-2-4a2 2 0 0 1 3-2l2 2"/></svg>Klik nama sekolah untuk membuka resume</span></th>
                    </tr>
                </thead>
                <tbody>
                {% for k in kategori %}
                <tr data-kat="{{ k.warna }}">
                    <td><span class="badge-lg {{ k.warna }}"><i></i>{{ k.judul }}</span></td>
                    <td class="jml">{{ k.jumlah }}</td>
                    <td>
                        <div class="chips-wrap">
                            {% for r in k.anggota %}<a class="chip chip-link" href="{{ url_for('detail_sekolah', id=r.id, dari='offline', w=wilayah_pilih) }}" data-cari="{{ (r.nama ~ ' ' ~ r.wilayah)|lower }}" title="{{ r.nama }} &middot; Klik untuk membuka resume"><span>{{ r.nama }}</span></a>{% else %}<span class="kosong-kecil">-</span>{% endfor %}
                        </div>
                    </td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
            </div>
        </div>

        <!-- Isi pop up rincian (disalin ke pop up saat tombol Lihat daftar diklik) -->
        {% for k in kategori %}
        <div class="rinci-data" id="rinci-{{ loop.index }}" style="display:none">
            {% if k.anggota %}
            <table class="rinci">
                <thead>
                    <tr>
                        <th>NAMA SEKOLAH</th>
                        <th style="width:130px;">WILAYAH</th>
                        <th style="width:110px;">SENSOR DOWN</th>
                        <th style="width:170px;">DOWN SEJAK (PERKIRAAN)</th>
                        <th style="width:140px;">LAMA DOWN</th>
                        <th style="text-align:center;width:90px;">RESUME</th>
                    </tr>
                </thead>
                <tbody>
                    {% for r in k.anggota %}
                    <tr class="rinci-item" data-cari="{{ (r.nama ~ ' ' ~ r.wilayah)|lower }}">
                        <td>
                            <div style="font-weight:700;font-size:13.5px;">{{ r.nama }}</div>
                        </td>
                        <td><span class="badge-wilayah" title="Sumber wilayah: {{ r.wilayah_sumber }}">{{ r.wilayah }}</span></td>
                        <td>{{ r.down_count }}x</td>
                        <td><span style="font-size:12px;">{{ r.lastdown_text or "-" }}</span></td>
                        <td>{{ r.lama_down }}</td>
                        <td style="text-align:center"><a class="btn-resume" href="{{ url_for('detail_sekolah', id=r.id, dari='offline', w=wilayah_pilih) }}">Resume</a></td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            <div class="kosong mr-kosong" style="display:none;padding:24px;text-align:center;">Tidak ada sekolah yang cocok dengan pencarian.</div>
            {% else %}
            <div class="kosong" style="padding:24px;text-align:center;">Tidak ada sekolah di kategori ini. &#127881;</div>
            {% endif %}
        </div>
        {% endfor %}

        {% if wilayah_pilih == 'Belum Dikelompokkan' %}
        {% set belum_detail = diagnosa_belum(details) %}
        {% if belum_detail %}
        <div class="card" style="padding:18px 22px">
            <h3 style="margin:0 0 4px;font-size:16px;font-weight:600">Kenapa belum dikelompokkan?</h3>
            <div style="font-size:12px;color:var(--muted);margin-bottom:10px;line-height:1.5">
                Ini isi data PRTG untuk tiap sekolah di wilayah ini. Kalau nama wilayahnya terlihat di salah satu kolom
                tapi tidak terbaca, kirim contohnya agar kolom itu dibaca. Kalau semua kolom kosong, isi
                Location/Tags di device PRTG, atau tambahkan aturan di pengaturan_sekolah.json.
            </div>
            <div class="tabel-scroll">
            <table class="rinci">
                <thead><tr><th>NAMA SEKOLAH</th><th>JALUR GRUP</th><th>LOCATION</th><th>TAGS</th><th>COMMENTS</th></tr></thead>
                <tbody>
                {% for b in belum_detail %}
                <tr>
                    <td>{{ b.device }}</td>
                    <td>{{ b.jalur }}</td>
                    <td>{{ b.lokasi or '-' }}</td>
                    <td>{{ b.tags or '-' }}</td>
                    <td>{{ b.komentar or '-' }}</td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
            </div>
        </div>
        {% endif %}
        {% endif %}

        <!-- Grafik naik turun -->
        <div class="card grafik-card">
            <div class="grafik-head">
                <div>
                    <h3>Tren Sekolah Down &middot; {{ nama_wilayah }}</h3>
                    {% if grafik %}
                    <div class="ket">{{ grafik.awal }} &ndash; {{ grafik.akhir }} &middot; {{ grafik.jumlah_titik }} titik riwayat</div>
                    {% else %}
                    <div class="ket">Dibangun dari riwayat setiap dashboard ini dibuka/refresh</div>
                    {% endif %}
                </div>
                {% if grafik %}
                <div class="stat-row">
                    <div class="stat"><b>{{ grafik.sekarang }}</b><span>Sekarang</span></div>
                    <div class="stat"><b>{{ grafik.nmin }}</b><span>Terendah</span></div>
                    <div class="stat"><b>{{ grafik.nmax }}</b><span>Tertinggi</span></div>
                    <div class="stat">
                        <b class="{% if grafik.selisih > 0 %}naik{% elif grafik.selisih < 0 %}turun{% endif %}">{% if grafik.selisih > 0 %}+{% endif %}{{ grafik.selisih }}</b>
                        <span>Perubahan</span>
                    </div>
                </div>
                {% endif %}
            </div>

            {% if grafik %}
            <div class="legend">
                {% for s in grafik.seri %}
                <button type="button" class="lg" data-seri="{{ s.kunci }}"><i style="background:{{ s.warna }}"></i>{{ s.label }}</button>
                {% endfor %}
            </div>
            <svg class="grafik-svg" viewBox="0 0 {{ grafik.lebar }} {{ grafik.tinggi }}">
                {% for g in grafik.grid %}
                <line class="g-grid" x1="{{ grafik.kiri }}" x2="{{ grafik.kanan_x }}" y1="{{ g.y }}" y2="{{ g.y }}"></line>
                <text x="{{ grafik.kiri - 8 }}" y="{{ g.y + 4 }}" text-anchor="end">{{ g.label }}</text>
                {% endfor %}
                {% for lx in grafik.label_x %}
                <text x="{{ lx.x }}" y="{{ grafik.label_y }}" text-anchor="middle">{{ lx.label }}</text>
                {% endfor %}
                {% for s in grafik.seri %}
                <g data-seri="{{ s.kunci }}">
                    {% if s.area %}<polygon points="{{ s.area }}" fill="{{ s.warna }}" opacity="0.08"></polygon>{% endif %}
                    <polyline points="{{ s.poly }}" fill="none" stroke="{{ s.warna }}" stroke-width="{{ s.tebal }}"
                              stroke-linejoin="round" stroke-linecap="round"></polyline>
                    {% for p in s.pts %}
                    <circle cx="{{ p.x }}" cy="{{ p.y }}" r="{{ 3.5 if s.kunci == 'total' else 2.5 }}"
                            fill="#fff" stroke="{{ s.warna }}" stroke-width="2"><title>{{ s.label }}: {{ p.v }} sekolah &middot; {{ p.waktu }}</title></circle>
                    {% endfor %}
                </g>
                {% endfor %}
            </svg>
            {% else %}
            <div class="kosong">Riwayat belum cukup untuk grafik. Buka/refresh dashboard ini beberapa kali lagi untuk mulai melihat naik turunnya.</div>
            {% endif %}
        </div>

    </div>

</div>

{% if abaikan %}
<div class="card" style="margin-top:22px;padding:14px 22px">
    <details>
        <summary style="cursor:pointer;font-weight:600;font-size:13.5px">{{ abaikan|length }} device down tidak dihitung</summary>
        <div class="chips-wrap" style="margin-top:8px">{% for n in abaikan %}<span class="chip">{{ n }}</span>{% endfor %}</div>
    </details>
</div>
{% endif %}

<div class="footer">
    Sensor down dari PRTG: {{ info_data.sensor }} &middot; sekolah dihitung: {{ info_data.sekolah }}
    &middot; belum dikelompokkan: {{ info_data.belum }} &middot; tidak dihitung: {{ info_data.abaikan }}<br>
    Data diambil langsung dari PRTG API (sumber sama dengan bot WhatsApp utama). Akses PRTG ini khusus
    sekolah, jadi semua device dihitung. Wilayah mengikuti nama grup di PRTG. Refresh otomatis setiap 60 detik.
</div>

</div>

<div class="modal" id="modal">
    <div class="modal-box">
        <h3>Kirim rekap sekolah ke WhatsApp?</h3>
        <p style="font-size:13px;color:var(--muted)">Data akan diambil ulang dari PRTG sebelum dikirim. Rekap mencakup semua wilayah.</p>
        <textarea class="preview" id="preview" spellcheck="false">Memuat...</textarea>
        <div class="edit-info">&#9998; Isi pesan bisa diedit langsung sebelum dikirim.</div>
        <div class="modal-actions">
            <button type="button" class="batal" id="batal">Batal</button>
            <button type="button" class="konfirmasi" id="konfirmasi">Ya, Kirim</button>
        </div>
    </div>
</div>

<div class="modal" id="modal-rinci">
    <div class="modal-box lebar">
        <div class="mr-head">
            <h3 id="mr-judul">Daftar sekolah</h3>
            <button type="button" class="mr-tutup" id="mr-tutup" title="Tutup">&times;</button>
        </div>
        <div class="mr-search">
            <input id="cari" type="text" placeholder="Cari sekolah..." autocomplete="off">
        </div>
        <div class="mr-body" id="mr-body"></div>
    </div>
</div>

<div class="toast" id="toast"></div>

<script>
(function () {
    var INTERVAL = 60;
    var KUNCI = 'refresh_deadline';
    var el = document.getElementById('countdown');
    var cari = document.getElementById('cari');

    function bacaDeadline() {
        try {
            var v = parseInt(sessionStorage.getItem(KUNCI), 10);
            return isNaN(v) ? 0 : v;
        } catch (e) { return 0; }
    }

    function simpanDeadline(v) {
        try { sessionStorage.setItem(KUNCI, String(v)); } catch (e) {}
    }

    /* Batas waktu refresh dipertahankan antar halaman/tab wilayah */
    var deadline = bacaDeadline();
    var kini = Date.now();

    if (!deadline || deadline <= kini || deadline > kini + INTERVAL * 1000) {
        deadline = kini + INTERVAL * 1000;
        simpanDeadline(deadline);
    }

    function tampilSisa() {
        el.textContent = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    }

    tampilSisa();

    setInterval(function () {
        var jeda = document.querySelector('.modal.show') ||
                   (document.activeElement === cari && cari.value);

        if (jeda) {
            deadline += 1000;
            simpanDeadline(deadline);
        }

        if (deadline - Date.now() <= 0) {
            simpanDeadline(Date.now() + INTERVAL * 1000);
            location.reload();
            return;
        }

        tampilSisa();
    }, 1000);

    // Mode gelap / terang (disimpan di browser)
    var btnTema = document.getElementById('tema-btn');

    function labelTema() {
        var gelap = document.documentElement.getAttribute('data-theme') === 'dark';
        btnTema.innerHTML = gelap ? '&#9728;&#65039; Terang' : '&#127769; Gelap';
    }

    btnTema.addEventListener('click', function () {
        var gelap = document.documentElement.getAttribute('data-theme') === 'dark';
        var baru = gelap ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', baru);
        try { localStorage.setItem('tema', baru); } catch (e) {}
        labelTema();
    });

    labelTema();

    // Legenda grafik: klik untuk menyembunyikan/menampilkan garis
    var tombol = document.querySelectorAll('.lg');
    for (var j = 0; j < tombol.length; j++) {
        tombol[j].addEventListener('click', function () {
            var k = this.getAttribute('data-seri');
            var g = document.querySelector('svg g[data-seri="' + k + '"]');
            var mati = this.classList.toggle('mati');
            if (g) { g.style.display = mati ? 'none' : ''; }
        });
    }

    // Filter kategori (Semua / < 24 / 24 - 48 / > 48): ringkasan + rincian
    var filterAktif = 'semua';

    function terapkanFilter() {
        var rows = document.querySelectorAll('tr[data-kat]');
        for (var i = 0; i < rows.length; i++) {
            var kat = rows[i].getAttribute('data-kat');
            rows[i].style.display =
                (filterAktif === 'semua' || filterAktif === kat) ? '' : 'none';
        }
    }

    // Unduh PDF & Excel: mengikuti wilayah terpilih dan filter kategori
    var btnPdf = document.getElementById('btn-export-pdf');
    var btnExcel = document.getElementById('btn-export-excel');

    function updateDownloadLinks() {
        var params = new URLSearchParams();
        params.set('w', '{{ wilayah_pilih }}');
        if (filterAktif && filterAktif !== 'semua') {
            params.set('kat', filterAktif);
        }
        var qs = params.toString();
        if (btnPdf) {
            btnPdf.href = '{{ url_for("unduh_offline", jenis="pdf") }}?' + qs;
        }
        if (btnExcel) {
            btnExcel.href = '{{ url_for("unduh_offline", jenis="xlsx") }}?' + qs;
        }
    }

    var fchips = document.querySelectorAll('.filter .fchip');
    for (var f = 0; f < fchips.length; f++) {
        fchips[f].addEventListener('click', function () {
            for (var x = 0; x < fchips.length; x++) { fchips[x].classList.remove('aktif'); }
            this.classList.add('aktif');
            filterAktif = this.getAttribute('data-f');
            terapkanFilter();
            updateDownloadLinks();
        });
    }

    // Pencarian sekolah (nama) pada daftar chip
    (function () {
        var kolom = document.querySelector('.cari-chip');
        if (!kolom) { return; }
        kolom.addEventListener('input', function () {
            var q = kolom.value.toLowerCase().trim();
            var chips = document.querySelectorAll('.chip-link');
            for (var i = 0; i < chips.length; i++) {
                var ok = !q || (chips[i].getAttribute('data-cari') || '').indexOf(q) > -1;
                chips[i].classList.toggle('sembunyi', !ok);
            }
        });
    })();

    // Pop up rincian (tombol Lihat daftar) + pencarian di dalam pop up
    var modalRinci = document.getElementById('modal-rinci');
    var mrBody = document.getElementById('mr-body');
    var mrJudul = document.getElementById('mr-judul');

    function saringPopup() {
        var q = cari.value.toLowerCase();
        var items = mrBody.querySelectorAll('.rinci-item');
        var tampil = 0;
        for (var i = 0; i < items.length; i++) {
            var ok = !q || items[i].getAttribute('data-cari').indexOf(q) > -1;
            items[i].style.display = ok ? '' : 'none';
            if (ok) { tampil++; }
        }
        var kosong = mrBody.querySelector('.mr-kosong');
        if (kosong) { kosong.style.display = (items.length && !tampil) ? '' : 'none'; }
    }

    cari.addEventListener('input', saringPopup);

    var mata = document.querySelectorAll('.mata');
    for (var m = 0; m < mata.length; m++) {
        mata[m].addEventListener('click', function () {
            mrJudul.textContent = this.getAttribute('data-judul');
            mrBody.innerHTML = document.getElementById(this.getAttribute('data-rinci')).innerHTML;
            cari.value = '';
            modalRinci.classList.add('show');
            saringPopup();
            cari.focus();
        });
    }

    function tutupRinci() { modalRinci.classList.remove('show'); }

    document.getElementById('mr-tutup').addEventListener('click', tutupRinci);

    modalRinci.addEventListener('click', function (e) {
        if (e.target === modalRinci) { tutupRinci(); }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { tutupRinci(); }
    });
})();

var modal = document.getElementById('modal');
var preview = document.getElementById('preview');
var konfirmasi = document.getElementById('konfirmasi');
var toast = document.getElementById('toast');

function showToast(text, jenis) {
    toast.textContent = text;
    toast.className = 'toast show ' + jenis;
    setTimeout(function () { toast.className = 'toast'; }, 4000);
}

document.getElementById('btn-buka-modal').addEventListener('click', function () {
    preview.value = 'Mengambil data terbaru...';
    preview.readOnly = true;
    konfirmasi.disabled = true;
    modal.classList.add('show');
    fetch('/pratinjau-wa')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.sukses) { preview.value = data.pesan; preview.readOnly = false; konfirmasi.disabled = false; }
            else { preview.value = 'Gagal: ' + data.error; }
        })
        .catch(function (e) { preview.value = 'Gagal: ' + e.message; });
});

document.getElementById('batal').addEventListener('click', function () {
    modal.classList.remove('show');
});

konfirmasi.addEventListener('click', function () {
    if (!preview.value.trim()) { showToast('Pesan masih kosong.', 'gagal'); return; }
    konfirmasi.disabled = true;
    konfirmasi.textContent = 'Mengirim...';
    fetch('/kirim-wa-sekolah', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pesan: preview.value })
    })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            modal.classList.remove('show');
            konfirmasi.disabled = false;
            konfirmasi.textContent = 'Ya, Kirim';
            if (data.sukses) { showToast('Rekap sekolah terkirim ke WhatsApp.', 'sukses'); }
            else { showToast('Gagal: ' + data.error, 'gagal'); }
        })
        .catch(function (e) {
            modal.classList.remove('show');
            konfirmasi.disabled = false;
            konfirmasi.textContent = 'Ya, Kirim';
            showToast('Gagal: ' + e.message, 'gagal');
        });
});

modal.addEventListener('click', function (e) {
    if (e.target === modal) { modal.classList.remove('show'); }
});
</script>


<script>

// ============================================================
// GLOBAL SEARCH DASHBOARD INTERAKTIF
// ============================================================
(function () {
    var dataGlobal = {{ daftar_global_json | safe }} || [];
    var input = document.getElementById('global-search-input');
    var dropdown = document.getElementById('global-search-dropdown');
    var list = document.getElementById('global-search-list');
    var clearBtn = document.getElementById('global-search-clear');
    var info = document.getElementById('gs-hasil-info');
    var indeksFokus = -1;

    if (!input || !dropdown || !list) return;

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function filterSekolah(q) {
        q = (q || '').trim().toLowerCase();
        if (!q) return [];
        var kata = q.split(/\\s+/).filter(Boolean);
        return dataGlobal.filter(function (s) {
            var cariTeks = ((s.nama || '') + ' ' + (s.wilayah || '') + ' ' + (s.id || '') + ' ' + (s.prtg_id || '') + ' ' + (s.status || '') + ' ' + (s.kategori || '')).toLowerCase();
            return kata.every(function (k) { return cariTeks.indexOf(k) > -1; });
        });
    }

    function renderHasil(hasil, q) {
        list.innerHTML = '';
        indeksFokus = -1;

        if (hasil.length === 0) {
            list.innerHTML = '<div class="gs-empty"><div class="gs-empty-icon">&#128269;</div>Tidak ditemukan sekolah dengan kata kunci "<strong>' + escapeHtml(q) + '</strong>"</div>';
            if (info) info.textContent = '0 sekolah ditemukan';
            dropdown.style.display = 'block';
            return;
        }

        var nOffline = 0, nOnline = 0;
        hasil.forEach(function (s) {
            if (s.status === 'offline') nOffline++; else nOnline++;
        });

        if (info) {
            info.textContent = hasil.length + ' sekolah ditemukan (' + nOffline + ' Offline, ' + nOnline + ' Online)';
        }

        var maxTampil = 40;
        var itemTampil = hasil.slice(0, maxTampil);

        itemTampil.forEach(function (s, idx) {
            var a = document.createElement('a');
            a.className = 'gs-item';
            a.href = s.url;
            a.setAttribute('role', 'option');
            a.setAttribute('data-idx', idx);

            var isOff = s.status === 'offline';
            var dotClass = isOff ? 'merah' : 'hijau';
            var badgeClass = isOff ? 'offline' : 'online';
            var statusTeks = isOff ? 'Sekolah Offline' : 'Sekolah Online';

            a.innerHTML =
                '<div class="gs-item-left">' +
                    '<div class="gs-item-nama">' + escapeHtml(s.nama) + '</div>' +
                    '<div class="gs-item-meta">' +
                        '<span class="gs-badge-wilayah">&#128205; ' + escapeHtml(s.wilayah) + '</span>' +
                        '<span class="gs-item-id">ID: #' + escapeHtml(s.id) + '</span>' +
                    '</div>' +
                '</div>' +
                '<div class="gs-item-right">' +
                    '<span class="gs-status-badge ' + badgeClass + '">' +
                        '<i class="gs-dot ' + dotClass + '"></i> ' + statusTeks +
                    '</span>' +
                    '<span class="gs-item-ket">' + escapeHtml(s.kategori) + (s.ket ? ' &middot; ' + escapeHtml(s.ket) : '') + '</span>' +
                '</div>';

            list.appendChild(a);
        });

        if (hasil.length > maxTampil) {
            var lebih = document.createElement('div');
            lebih.className = 'gs-empty';
            lebih.style.padding = '10px 18px';
            lebih.style.fontSize = '12px';
            lebih.textContent = '... dan ' + (hasil.length - maxTampil) + ' sekolah lainnya. Persempit kata kunci untuk hasil lebih spesifik.';
            list.appendChild(lebih);
        }

        dropdown.style.display = 'block';
    }

    function updateFokus() {
        var items = list.querySelectorAll('.gs-item');
        items.forEach(function (it, idx) {
            it.classList.toggle('fokus', idx === indeksFokus);
            if (idx === indeksFokus) {
                it.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    input.addEventListener('input', function () {
        var q = this.value;
        if (clearBtn) clearBtn.style.display = q ? 'flex' : 'none';
        if (!q.trim()) {
            dropdown.style.display = 'none';
            indeksFokus = -1;
            return;
        }
        var hasil = filterSekolah(q);
        renderHasil(hasil, q);
    });

    input.addEventListener('focus', function () {
        if (this.value.trim()) {
            var hasil = filterSekolah(this.value);
            renderHasil(hasil, this.value);
        }
    });

    input.addEventListener('keydown', function (e) {
        var items = list.querySelectorAll('.gs-item');
        if (!items.length || dropdown.style.display === 'none') return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            indeksFokus = (indeksFokus + 1) % items.length;
            updateFokus();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            indeksFokus = (indeksFokus - 1 + items.length) % items.length;
            updateFokus();
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (indeksFokus >= 0 && items[indeksFokus]) {
                items[indeksFokus].click();
            } else if (items.length > 0) {
                items[0].click();
            }
        } else if (e.key === 'Escape') {
            dropdown.style.display = 'none';
            indeksFokus = -1;
        }
    });

    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            input.value = '';
            this.style.display = 'none';
            dropdown.style.display = 'none';
            indeksFokus = -1;
            input.focus();
        });
    }

    document.addEventListener('click', function (e) {
        if (!e.target.closest('.global-search-card')) {
            dropdown.style.display = 'none';
        }
    });
})();

</script>
"""


ONLINE_TEMPLATE = """
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard Sekolah Online - Network Monitoring</title>
<link rel="icon" type="image/svg+xml" href="{{ url_for('favicon') }}">
<script>
    try {
        var tema = localStorage.getItem('tema');
        if (tema) { document.documentElement.setAttribute('data-theme', tema); }
    } catch (e) {}
</script>
<style>
{{ shared_css | safe }}

    .search { display: flex; }
    .search input {
        border: none; outline: none; padding: 8px 14px; border-radius: 6px 0 0 6px;
        width: 190px; font-size: 13px;
    }
    .search span {
        background: #fff; border-left: 1px solid var(--border); padding: 8px 12px;
        border-radius: 0 6px 6px 0; color: var(--navy); font-size: 13px;
    }

    /* ---------- Tab Navigasi Halaman Utama ---------- */
    .page-nav-bar {
        background: #0c0f2e;
        display: flex;
        gap: 8px;
        padding: 0 34px;
        border-top: 1px solid rgba(255,255,255,0.06);
        border-bottom: 1px solid rgba(255,255,255,0.08);
        overflow-x: auto;
    }
    .page-nav-tab {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        padding: 13px 20px 14px;
        font-size: 13.5px;
        font-weight: 700;
        color: #929cd0;
        text-decoration: none;
        border-bottom: 3px solid transparent;
        transition: all 0.2s ease;
        white-space: nowrap;
    }
    .page-nav-tab:hover {
        color: #ffffff;
        background: rgba(255,255,255,0.04);
    }
    .page-nav-tab.offline.aktif {
        color: #ffffff;
        border-bottom-color: #ff4757;
        background: rgba(255, 71, 87, 0.08);
    }
    .page-nav-tab.online.aktif {
        color: #ffffff;
        border-bottom-color: #2ed573;
        background: rgba(46, 213, 115, 0.08);
    }
    .tab-dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
        position: relative;
    }
    .tab-dot.offline {
        background: #ff4757;
        box-shadow: 0 0 8px #ff4757;
    }
    .tab-dot.offline::after {
        content: ""; position: absolute; inset: -2px; border-radius: 50%;
        border: 2px solid #ff4757; animation: riak 1.6s ease-out infinite;
    }
    .tab-dot.online {
        background: #2ed573;
        box-shadow: 0 0 8px #2ed573;
    }
    .tab-dot.online::after {
        content: ""; position: absolute; inset: -2px; border-radius: 50%;
        border: 2px solid #2ed573; animation: riak 1.6s ease-out infinite;
    }
    .tab-badge {
        padding: 2px 9px;
        border-radius: 999px;
        font-size: 11.5px;
        font-weight: 800;
    }
    .tab-badge.merah {
        background: rgba(255, 71, 87, 0.2);
        color: #ff7675;
        border: 1px solid rgba(255, 71, 87, 0.35);
    }
    .page-nav-tab.offline.aktif .tab-badge.merah {
        background: #ff4757;
        color: #ffffff;
        border-color: #ff4757;
    }
    .tab-badge.hijau {
        background: rgba(46, 213, 115, 0.2);
        color: #7bed9f;
        border: 1px solid rgba(46, 213, 115, 0.35);
    }
    .page-nav-tab.online.aktif .tab-badge.hijau {
        background: #2ed573;
        color: #ffffff;
        border-color: #2ed573;
    }
    @media (max-width: 600px) {
        .page-nav-bar { padding: 0 10px; }
        .page-nav-tab { padding: 10px 14px; font-size: 12.5px; }
    }

    /* ---------- Navbar wilayah ---------- */
    .navwil {
        background: #0f1238; display: flex; gap: 4px; padding: 0 34px;
        overflow-x: auto; border-top: 1px solid rgba(255,255,255,0.08);
    }
    .nw {
        color: #b8c0ee; text-decoration: none; font-size: 13px; font-weight: 600;
        padding: 12px 16px; border-bottom: 3px solid transparent; white-space: nowrap;
        display: inline-flex; align-items: center; gap: 7px;
    }
    .nw:hover { color: #fff; background: rgba(255,255,255,0.06); }
    .nw.aktif { color: #fff; border-bottom-color: #2ed573; }
    .nw b {
        background: rgba(255,255,255,0.14); border-radius: 999px;
        padding: 1px 8px; font-size: 11.5px;
    }
    .nw b.online-b {
        background: rgba(46, 213, 115, 0.18);
        color: #7bed9f;
        border: 1px solid rgba(46, 213, 115, 0.35);
    }
    .nw.aktif b.online-b {
        background: #2ed573;
        color: #ffffff;
        border-color: #2ed573;
    }
    @media (max-width: 600px) { .navwil { padding: 0 10px; } }

    .btn-resume {
        display: inline-block; background: var(--navy); color: #fff; text-decoration: none;
        padding: 5px 12px; border-radius: 999px; font-size: 12px; font-weight: 600;
    }
    .btn-resume:hover { background: #2a3190; }
    html[data-theme="dark"] .btn-resume { background: #3a4aa8; }

    .pilih-wilayah {
        border: 1px solid var(--border); background: var(--card); color: var(--text);
        border-radius: 8px; padding: 5px 8px; font-size: 12px;
    }

    /* ---------- Layout ---------- */
    .container { width: 100%; max-width: 100%; margin: 0 auto; padding: 26px 34px 50px; }
    .layout { display: block; }
    .main { display: flex; flex-direction: column; gap: 22px; min-width: 0; }

    .card {
        background: var(--card); border-radius: 14px;
        box-shadow: 0 4px 14px rgba(22,26,82,0.07);
    }

    /* ---------- Kartu kategori Online ---------- */
    .kartu-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; }
    .kartu-grid.lima { grid-template-columns: repeat(5, 1fr); gap: 14px; }
    .k-card { padding: 34px 18px 20px; text-align: center; position: relative; }

    .k-avatar {
        position: absolute; top: 14px; right: 14px;
        width: 12px; height: 12px; border-radius: 50%;
        background: var(--dot);
        box-shadow: 0 0 6px 2px var(--glow);
        animation: kelap 1.6s ease-in-out infinite;
    }
    .k-avatar::after {
        content: ""; position: absolute; inset: -2px; border-radius: 50%;
        border: 2px solid var(--glow);
        animation: riak 1.6s ease-out infinite;
    }
    .k-avatar.hijau     { --dot: #2ed573; --glow: #2ed573; }
    .k-avatar.hijau-tua { --dot: #10b981; --glow: #10b981; }
    .k-avatar.biru      { --dot: #3b82f6; --glow: #3b82f6; }
    .k-avatar.kuning    { --dot: #f59e0b; --glow: #f59e0b; }
    .k-avatar.ungu      { --dot: #8b5cf6; --glow: #8b5cf6; }

    @keyframes kelap {
        0%, 100% { opacity: 1; box-shadow: 0 0 4px 1px var(--glow), 0 0 12px 3px var(--glow); }
        50%      { opacity: 0.35; box-shadow: 0 0 2px 0 var(--glow); }
    }
    @keyframes riak {
        0%   { transform: scale(1);   opacity: 0.8; }
        100% { transform: scale(2.6); opacity: 0; }
    }

    /* Titik indikator di judul panel (sama dengan halaman offline) */
    .panel-dot {
        --glow: #2ed573;
        width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0;
        background: #2ed573;
        box-shadow: 0 0 8px 2px var(--glow);
        animation: kelap 1.6s ease-in-out infinite;
    }

    .k-judul { font-size: 17px; font-weight: 600; }
    .kartu-grid.lima .k-judul { font-size: 15px; }
    .k-sub { font-size: 12px; color: var(--muted); margin-bottom: 14px; }
    .k-stat { display: flex; flex-direction: column; text-align: left; }
    .k-stat > div {
        display: flex; justify-content: space-between; align-items: center;
        padding: 10px 4px; border-top: 1px solid var(--border);
    }
    .k-stat b { display: block; font-size: 14px; font-weight: 600; white-space: nowrap; order: 2; }
    .k-stat b.h { color: var(--hijau); }
    .k-stat b.b { color: var(--biru); }
    .k-stat b.k { color: var(--kuning-tua); }
    .k-stat span { font-size: 12px; color: var(--muted); order: 1; }

    /* ---------- Panel Head & Filters ---------- */
    .panel-head {
        display: flex; align-items: center; justify-content: space-between; gap: 12px;
        flex-wrap: wrap; padding: 18px 24px; border-bottom: 1px solid var(--border);
    }
    .panel-head h2 { margin: 0; font-size: 17px; font-weight: 600; }
    .panel-head .ket { font-size: 12px; color: var(--muted); margin-top: 2px; }
    .filter { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .fchip {
        border: 1px solid var(--border); background: #fff; color: #6b7390;
        padding: 7px 15px; border-radius: 999px; font-size: 12.5px; cursor: pointer;
    }
    .fchip:hover { border-color: #b9c0d9; }
    .fchip.aktif { background: var(--navy); color: #fff; border-color: var(--navy); font-weight: 600; }
    .btn-wa {
        border: none; background: #0f7a52; color: #fff; padding: 8px 16px;
        border-radius: 999px; font-weight: 700; cursor: pointer; font-size: 12.5px;
    }
    .btn-wa:hover { background: #0b6444; }

    .btn-unduh {
        text-decoration: none; color: #fff; font-weight: 700; font-size: 12.5px;
        padding: 8px 16px; border-radius: 999px; display: inline-flex; align-items: center;
        gap: 6px; cursor: pointer; border: none; transition: all 0.2s ease;
    }
    .btn-unduh.pdf { background: #c0392b; }
    .btn-unduh.pdf:hover { background: #a93226; transform: translateY(-1px); }
    .btn-unduh.xls { background: #0f7a52; }
    .btn-unduh.xls:hover { background: #0b6444; transform: translateY(-1px); }

    .tabel-scroll { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 14px 24px; text-align: left; border-bottom: 1px solid var(--border);
             font-size: 13px; vertical-align: middle; }
    th { background: #f7f8fc; font-size: 10.5px; text-transform: uppercase;
         color: var(--muted); letter-spacing: 0.5px; }

    .badge-uptime {
        display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px;
        border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap;
    }
    .badge-uptime i { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
    .badge-uptime.hijau-tua { background: #dcfce7; color: #15803d; }
    .badge-uptime.hijau-tua i { background: #16a34a; }
    .badge-uptime.hijau { background: #e0f2fe; color: #0369a1; }
    .badge-uptime.hijau i { background: #0284c7; }
    .badge-uptime.kuning { background: #fef3c7; color: #b45309; }
    .badge-uptime.kuning i { background: #f59e0b; }

    .badge-wilayah {
        display: inline-block; background: #eef2ff; color: #3730a3;
        padding: 3px 8px; border-radius: 6px; font-size: 11.5px; font-weight: 600;
        white-space: nowrap;
    }

    .chips-wrap { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 6px; max-height: 190px; overflow-y: auto; }
    .chips-wrap .chip-link {
        display: flex; align-items: center; gap: 8px; min-width: 0; text-decoration: none;
        background: #f6f8fc; border: 1px solid var(--border); border-radius: 8px;
        padding: 6px 10px; font-family: inherit; font-size: 12.5px; color: var(--text, #1b2147);
        transition: all 0.15s ease;
    }
    .chips-wrap .chip-link span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
    .chips-wrap .chip-link:hover { background: var(--navy); border-color: var(--navy); color: #fff; }
    .chips-wrap .chip-link.sembunyi { display: none !important; }
    html[data-theme="dark"] .chips-wrap .chip-link { background: var(--card); color: #c3caea; }
    html[data-theme="dark"] .chips-wrap .chip-link:hover { background: #3a4aa8; border-color: #3a4aa8; color: #fff; }
    .cari-chip-wrap input.cari-chip:focus { border-color: var(--navy); }
    .cari-chip-wrap { padding: 4px 24px 14px; }
    .cari-chip-wrap input.cari-chip {
        width: 100%; max-width: 420px; padding: 10px 14px; border: 1px solid var(--border);
        border-radius: 999px; font-size: 13.5px; background: var(--card, #fff); color: inherit;
    }
    .chip { display: inline-block; background: #f6f8fc; border: 1px solid var(--border);
            padding: 3px 9px; border-radius: 6px; font-size: 11.5px;
            font-family: Consolas, monospace; }
    .kosong-kecil { color: var(--muted); }

    .badge-lg {
        display: inline-flex; align-items: center; gap: 7px; padding: 7px 14px;
        border-radius: 999px; font-size: 12px; font-weight: 700; white-space: nowrap;
    }
    .badge-lg i { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
    .badge-lg.kuning { background: #fff3c4; color: #765700; }
    .badge-lg.kuning i { background: #e0a800; }
    .badge-lg.biru { background: #dbeafe; color: #1e40af; }
    .badge-lg.biru i { background: #2563eb; }
    .badge-lg.hijau,
    .badge-lg.hijau-tua { background: #dcfce7; color: #15803d; }
    .badge-lg.hijau i,
    .badge-lg.hijau-tua i { background: #16a34a; }

    .mata {
        height: 36px; padding: 0 14px; border-radius: 999px; border: 1px solid var(--border);
        background: #fff; cursor: pointer; display: inline-flex; align-items: center; gap: 7px;
        font: inherit; font-size: 12.5px; font-weight: 700; white-space: nowrap;
        color: var(--navy); transition: all 0.2s ease;
    }
    .mata:hover { background: var(--navy); color: #fff; border-color: var(--navy); }
    .mata svg { width: 16px; height: 16px; flex-shrink: 0; }

    .rinci { width: 100%; border-collapse: collapse; }
    .rinci th { background: transparent; padding: 12px 18px; font-size: 11px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.5px; border-bottom: 1px solid var(--border); text-align: left; }
    .rinci td { padding: 12px 18px; border-bottom: 1px solid var(--border); font-size: 13px; vertical-align: middle; }

    /* ---------- Pop up rincian ---------- */
    .modal {
        display: none; position: fixed; inset: 0; background: rgba(10,12,40,0.55);
        align-items: center; justify-content: center; padding: 20px; z-index: 999;
    }
    .modal.show { display: flex; }
    .modal-box { background: var(--card); width: 100%; max-width: 440px; padding: 24px; border-radius: 16px; }
    .modal-box h3 { margin-top: 0; }
    .modal-box.lebar {
        background: var(--card); width: 100%; max-width: 1060px; padding: 0; display: flex; flex-direction: column;
        max-height: 86vh; overflow: hidden; border-radius: 16px; box-shadow: 0 16px 40px rgba(0,0,0,0.22);
    }
    .mr-head {
        display: flex; align-items: center; justify-content: space-between;
        padding: 18px 24px 12px; border-bottom: 1px solid var(--border);
    }
    .mr-head h3 { margin: 0; font-size: 17px; font-weight: 700; }
    .mr-tutup {
        border: none; background: #eef1f7; width: 32px; height: 32px; border-radius: 50%;
        font-size: 20px; line-height: 1; cursor: pointer; color: #4b5578; display: inline-flex;
        align-items: center; justify-content: center; transition: background 0.15s;
    }
    .mr-tutup:hover { background: #dfe4f0; }
    .mr-search { padding: 14px 24px 12px; border-bottom: 1px solid var(--border); }
    .mr-search input {
        width: 100%; padding: 10px 14px; border-radius: 10px;
        border: 1px solid var(--border); background: #f6f8fc; font-size: 13px; outline: none;
        transition: all 0.2s;
    }
    .mr-search input:focus { border-color: var(--biru); background: #fff; box-shadow: 0 0 0 3px rgba(29, 95, 214, 0.12); }
    .mr-body { overflow-y: auto; padding: 0; max-height: calc(86vh - 140px); }
    .mr-body .rinci th { position: sticky; top: 0; background: #f7f8fc; z-index: 2; }

    /* ---------- Modal WhatsApp & toast ---------- */
    .preview { background: #f7f8fc; border: 1px solid var(--border); border-radius: 8px; padding: 10px;
               white-space: pre-wrap; font-family: Consolas, monospace; font-size: 12px;
               max-height: 220px; overflow: auto; margin: 12px 0; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
    .modal-actions button { border: none; padding: 9px 15px; border-radius: 8px; font-weight: 700; cursor: pointer; }
    .batal { background: #e5e7eb; }
    .konfirmasi { background: var(--hijau); color: #fff; }
    .konfirmasi:disabled { opacity: 0.6; cursor: not-allowed; }
    .toast { display: none; position: fixed; right: 22px; bottom: 22px; padding: 12px 16px;
             border-radius: 9px; color: #fff; font-size: 12.5px; z-index: 1000; }
    .toast.show { display: block; }
    .toast.sukses { background: var(--hijau); }
    .toast.gagal { background: var(--merah); }

    .footer { text-align: center; color: #5d6785; font-size: 11.5px; margin-top: 22px; line-height: 1.6; }

    /* ---------- Mode gelap ---------- */
    html[data-theme="dark"] {
        --bg-atas: #0b0e22;
        --bg-bawah: #151a36;
        --card: #1a2040;
        --border: #2b3358;
        --text: #e8ebf7;
        --muted: #8f98ba;
        --biru: #4d8dff;
        --navy: #0f1238;
    }
    html[data-theme="dark"] .card { box-shadow: 0 4px 16px rgba(0,0,0,0.35); }
    html[data-theme="dark"] th { background: #212850; }
    html[data-theme="dark"] .chip { background: #232a4f; color: var(--text); }
    html[data-theme="dark"] .fchip { background: var(--card); color: #aab2d3; }
    html[data-theme="dark"] .fchip:hover { border-color: #4a5589; }
    html[data-theme="dark"] .fchip.aktif { background: #22c55e; border-color: #22c55e; color: #fff; }
    html[data-theme="dark"] .badge-lg.kuning { background: #4a3a0c; color: #ffd966; }
    html[data-theme="dark"] .badge-lg.biru { background: #1e295d; color: #93c5fd; }
    html[data-theme="dark"] .badge-lg.hijau,
    html[data-theme="dark"] .badge-lg.hijau-tua { background: #064e3b; color: #86efac; }
    html[data-theme="dark"] .mata { background: var(--card); color: #c3caea; border-color: var(--border); }
    html[data-theme="dark"] .mata:hover { background: #3a4aa8; color: #fff; border-color: #3a4aa8; }
    html[data-theme="dark"] .mr-tutup { background: #232a4f; color: #c3caea; }
    html[data-theme="dark"] .mr-tutup:hover { background: #2d3560; }
    html[data-theme="dark"] .mr-search input { background: #151a36; color: var(--text); border-color: var(--border); }
    html[data-theme="dark"] .mr-search input:focus { background: #1a2040; border-color: #4d8dff; }
    html[data-theme="dark"] .mr-body .rinci th { background: #212850; }
    html[data-theme="dark"] .rinci th { border-bottom-color: var(--border); }
    html[data-theme="dark"] .rinci td { border-bottom-color: var(--border); }
    html[data-theme="dark"] .badge-uptime.hijau-tua { background: #064e3b; color: #86efac; }
    html[data-theme="dark"] .badge-uptime.hijau { background: #0c4a6e; color: #7dd3fc; }
    html[data-theme="dark"] .badge-uptime.kuning { background: #451a03; color: #fde68a; }
    html[data-theme="dark"] .badge-wilayah { background: #312e81; color: #c7d2fe; }
    html[data-theme="dark"] .preview { background: #151a36; color: var(--text); }
    textarea.preview { width: 100%; height: 260px; max-height: none; resize: vertical;
                       line-height: 1.5; color: var(--text); outline: none; display: block; }
    textarea.preview:focus { border-color: var(--biru); }
    .edit-info { font-size: 11.5px; color: var(--muted); margin: -4px 0 8px; }
    html[data-theme="dark"] .batal { background: #2b3358; color: var(--text); }
    html[data-theme="dark"] .footer { color: #8f98ba; }

    /* ---------- Grafik tren Sekolah Online ---------- */
    .grafik-card { padding: 20px 22px 16px; }
    .grafik-head { display: flex; align-items: flex-start; justify-content: space-between;
                   gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }
    .grafik-head h3 { margin: 0; font-size: 16px; font-weight: 600; }
    .grafik-head .ket { font-size: 12px; color: var(--muted); margin-top: 2px; }
    .stat-row { display: flex; gap: 22px; flex-wrap: wrap; }
    .stat b { display: block; font-size: 20px; font-weight: 600; line-height: 1.1; }
    .stat span { font-size: 11px; color: var(--muted); }
    .stat b.naik  { color: var(--merah); }
    .stat b.turun { color: var(--hijau); }
    .stat b.naik-online  { color: #2ed573; }
    .stat b.turun-online { color: #ff4757; }
    .kosong { color: var(--muted); font-style: italic; padding: 18px; }

    .legend { display: flex; gap: 8px; flex-wrap: wrap; margin: 6px 0 4px; }
    .lg {
        border: 1px solid var(--border); background: #fff; border-radius: 999px;
        padding: 4px 12px 4px 9px; font-size: 12px; cursor: pointer; display: inline-flex;
        align-items: center; gap: 6px; color: var(--text);
    }
    .lg i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .lg.mati { opacity: 0.4; text-decoration: line-through; }

    .grafik-svg { width: 100%; height: auto; display: block; }
    .grafik-svg .g-grid { stroke: #e6e9f2; stroke-width: 1; }
    .grafik-svg text { font-size: 11px; fill: #8a90a2; font-family: 'Segoe UI', Arial, sans-serif; }
    .grafik-svg circle { cursor: pointer; }
    .grafik-svg circle:hover { r: 6; }

    html[data-theme="dark"] .lg { background: var(--card); border-color: var(--border); color: var(--text); }
    html[data-theme="dark"] .grafik-svg .g-grid { stroke: #2b3358; }
    html[data-theme="dark"] .grafik-svg text { fill: #8f98ba; }
    html[data-theme="dark"] .grafik-svg circle { fill: var(--card); }

    /* ---------- Responsif ---------- */
    @media (max-width: 1000px) {
        .kartu-grid, .kartu-grid.lima { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 600px) {
        .container { padding: 18px 12px 30px; }
        .kartu-grid, .kartu-grid.lima { grid-template-columns: 1fr; }
        .filter { width: 100%; justify-content: flex-start; }
    }
    @media screen and (min-width: 1024px) and (max-width: 2560px) {
        html { zoom: 1.12; }
    }
</style>
</head>
<body>

<div class="topbar">
    <div class="brand">
        <svg viewBox="0 0 100 90" fill="none" stroke-width="13">
            <path d="M17.1 71 A38 38 0 0 1 25.6 22.9" stroke="#b5d334"/>
            <path d="M25.6 22.9 A38 38 0 0 1 74.4 22.9" stroke="#f5a31a"/>
            <path d="M74.4 22.9 A38 38 0 0 1 82.9 71" stroke="#f0156b"/>
            <polygon points="22,33 52.2,48.7 47.8,55.3" fill="#0b2a6b" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round"/>
            <circle cx="50" cy="52" r="4.5" fill="#0b2a6b" stroke="#ffffff" stroke-width="1.5"/>
        </svg>
        <div class="brand-teks">
            <div class="b1">NETWORK MONITORING</div>
            <div class="b2">SEKOLAH</div>
        </div>
    </div>
    <div class="welcome">
        Selamat datang, Admin
        <small>Monitoring status online sekolah &middot; PRTG {{ prtg_server }}</small>
    </div>
    <div class="top-right">
        <div class="pill-top"><span class="dot"></span>Refresh <span id="countdown">60</span> dtk</div>
        <button type="button" class="tema-btn" id="tema-btn" title="Ganti mode gelap / terang">&#127769; Gelap</button>
        <a class="logout" href="{{ url_for('logout') }}">&#8617; Keluar</a>
    </div>
</div>

<div class="page-nav-bar">
    <a href="{{ url_for('index', w=wilayah_pilih) }}" class="page-nav-tab offline">
        <span class="tab-dot offline"></span>
        <span class="tab-text">Dashboard Gangguan Offline</span>
        <span class="tab-badge merah">{{ total_offline }}</span>
    </a>
    <a href="{{ url_for('dashboard_online', w=wilayah_pilih) }}" class="page-nav-tab online aktif">
        <span class="tab-dot online"></span>
        <span class="tab-text">Dashboard Sekolah Online</span>
        <span class="tab-badge hijau">{{ total_online }}</span>
    </a>
</div>

<div class="navwil">
    <a href="{{ url_for('dashboard_online', w='semua') }}" class="nw {% if wilayah_pilih == 'semua' %}aktif{% endif %}">
        &#127758; Semua Wilayah <b class="online-b">{{ total_semua_online }}</b>
    </a>
    {% for w in daftar_wilayah %}
    <a href="{{ url_for('dashboard_online', w=w.nama) }}" class="nw {% if wilayah_pilih == w.nama %}aktif{% endif %}">
        &#128205; {{ w.nama }} <b class="online-b">{{ w.jumlah }}</b>
    </a>
    {% endfor %}
</div>

<div class="container">
<div class="layout">
    <div class="main">

        <!-- ================= GLOBAL SEARCH DASHBOARD ================= -->
        <div class="card global-search-card">
                        <div class="gs-header">
                <div class="gs-title-wrap">
                    <div class="gs-icon-box">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="11" cy="11" r="8"></circle>
                            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                        </svg>
                    </div>
                    <div>
                        <h2 class="gs-title">Pencarian Sekolah Global</h2>
                        <div class="gs-sub">Cari seluruh sekolah di semua wilayah &middot; Mencakup status Online maupun Offline</div>
                    </div>
                </div>
                <div class="gs-meta-stats">
                    <span class="gs-stat-pill online"><i class="gs-dot hijau"></i>Online: <b>{{ total_online }}</b></span>
                    <span class="gs-stat-pill">Total: <b>{{ total_online }}</b></span>
                </div>
            </div>

            <div class="gs-input-box">
                <svg class="gs-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                </svg>
                <input type="text" id="global-search-input" class="gs-input" placeholder="Cari nama sekolah, wilayah, atau nomor ID PRTG..." autocomplete="off" spellcheck="false">
                <button type="button" id="global-search-clear" class="gs-clear" title="Bersihkan pencarian" aria-label="Bersihkan pencarian">&times;</button>
            </div>

            <div id="global-search-dropdown" class="gs-dropdown" style="display:none;">
                <div class="gs-dropdown-top">
                    <span id="gs-hasil-info">Hasil pencarian</span>
                    <span class="gs-hint">Gunakan tombol &uarr; &darr; lalu Enter atau Klik untuk membuka detail sekolah</span>
                </div>
                <div id="global-search-list" class="gs-list" role="listbox"></div>
            </div>
        </div>

        <!-- Kartu Statistik Online: < 24 jam, 24 - 48 jam, > 48 jam, total, baru saja online -->
        <div class="kartu-grid lima">
            {% for k in kategori_online %}
            <div class="card k-card">
                <div class="k-avatar {{ k.warna }}"></div>
                <div class="k-judul">{{ k.judul }}</div>
                <div class="k-sub">sekolah online</div>
                <div class="k-stat">
                    <div><b class="h">{{ k.jumlah }}</b><span>Sekolah</span></div>
                    <div><b class="b">{{ k.rata }}</b><span>Rata-rata</span></div>
                    <div><b>{{ k.terlama }}</b><span>Terlama</span></div>
                </div>
            </div>
            {% endfor %}
            <div class="card k-card">
                <div class="k-avatar hijau"></div>
                <div class="k-judul">Total Sekolah Online</div>
                <div class="k-sub">{{ nama_wilayah }}</div>
                <div class="k-stat">
                    <div><b class="h">{{ total_kartu.jumlah }}</b><span>Sekolah</span></div>
                    <div><b class="b">{{ total_kartu.rata }}</b><span>Rata-rata</span></div>
                    <div><b>{{ total_kartu.terlama }}</b><span>Terlama</span></div>
                </div>
            </div>
            <div class="card k-card">
                <div class="k-avatar ungu"></div>
                <div class="k-judul">Baru Saja Online</div>
                <div class="k-sub">uptime &lt; 1 jam</div>
                <div class="k-stat">
                    <div><b class="k">{{ stat_online.baru }}</b><span>Sekolah</span></div>
                    <div><b>{{ stat_online.pct_baru }}%</b><span>Persentase</span></div>
                    <div><b class="k">Pulih</b><span>Status</span></div>
                </div>
            </div>
        </div>

        <!-- Ringkasan Sekolah Online -->
        <div class="card">
            <div class="panel-head">
                <div style="display:flex;align-items:center;gap:10px;">
                    <div class="panel-dot"></div>
                    <div>
                        <h2>Daftar Sekolah Online</h2>
                        <div class="ket">Wilayah: <strong>{{ nama_wilayah }}</strong> &middot; Diperiksa: <strong>{{ updated_at }}</strong> &middot; Total: <strong>{{ total_online_wilayah }} sekolah</strong></div>
                    </div>
                </div>
                <div class="filter">
                    <button type="button" class="fchip aktif" data-f="semua">Semua</button>
                    <button type="button" class="fchip" data-f="t24">&lt; 24 Jam</button>
                    <button type="button" class="fchip" data-f="t48">24 - 48 Jam</button>
                    <button type="button" class="fchip" data-f="rekap">&gt; 48 Jam</button>
                    <button type="button" class="btn-wa" id="btn-buka-modal">&#128228; Kirim WhatsApp</button>
                    <a class="btn-unduh pdf" id="btn-export-pdf" href="{{ url_for('unduh_online', jenis='pdf', w=wilayah_pilih) }}" title="Unduh PDF">&#128196; Unduh PDF</a>
                    <a class="btn-unduh xls" id="btn-export-excel" href="{{ url_for('unduh_online', jenis='xlsx', w=wilayah_pilih) }}" title="Unduh Excel">&#128202; Unduh Excel</a>
                </div>
            </div>

    
            <div class="tabel-scroll">
                <table>
                    <thead>
                        <tr>
                            <th style="width:160px;">KATEGORI</th>
                            <th style="width:100px;">JUMLAH</th>
                            <th>SEKOLAH <span class="hint-resume"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11V5a2 2 0 0 1 4 0v6"/><path d="M13 11V9a2 2 0 0 1 4 0v5a6 6 0 0 1-6 6h-1a6 6 0 0 1-5-3l-2-4a2 2 0 0 1 3-2l2 2"/></svg>Klik nama sekolah untuk membuka resume</span></th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for k in kategori_online %}
                        <tr data-kat="{{ k.kunci }}">
                            <td><span class="badge-lg {{ k.warna }}"><i></i>{{ k.judul }}</span></td>
                            <td class="jml" style="font-weight:700;font-size:14px;">{{ k.jumlah }}</td>
                            <td>
                                <div class="chips-wrap">
                                    {% for r in k.anggota %}
                                    <a class="chip chip-link" href="{{ url_for('detail_sekolah', id=r.id, dari='online', w=wilayah_pilih) }}" data-cari="{{ (r.nama ~ ' ' ~ r.wilayah)|lower }}" title="{{ r.nama }} &middot; Klik untuk membuka resume"><span>{{ r.nama }}</span></a>
                                    {% else %}
                                    <span class="kosong-kecil">-</span>
                                    {% endfor %}
                                </div>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Isi pop up rincian per kategori (disalin ke modal saat tombol Lihat daftar diklik) -->
        {% for k in kategori_online %}
        <div class="rinci-data" id="rinci-{{ loop.index }}" style="display:none">
            {% if k.anggota %}
            <table class="rinci">
                <thead>
                    <tr>
                        <th>NAMA SEKOLAH</th>
                        <th style="width:130px;">WILAYAH</th>
                        <th style="width:110px;">SENSOR</th>
                        <th style="width:170px;">TERAKHIR DIPERIKSA</th>
                        <th style="width:150px;">ONLINE SEJAK</th>
                        <th style="width:140px;">DURASI ONLINE</th>
                        <th style="text-align:center;width:90px;">RESUME</th>
                    </tr>
                </thead>
                <tbody>
                    {% for r in k.anggota %}
                    <tr class="rinci-item" data-cari="{{ (r.nama ~ ' ' ~ r.sensor ~ ' ' ~ r.wilayah ~ ' ' ~ r.online_sejak)|lower }}">
                        <td>
                            <div style="font-weight:700;font-size:13.5px;">{{ r.nama }}</div>
                        </td>
                        <td><span class="badge-wilayah" title="Sumber wilayah: {{ r.wilayah_sumber }}">{{ r.wilayah }}</span></td>
                        <td><span style="font-size:12px;">{{ r.sensor }}</span>{% if r.jumlah_sensor and r.jumlah_sensor > 1 %}<div style="color:var(--muted);font-size:11px;">+{{ r.jumlah_sensor - 1 }} sensor lain</div>{% endif %}</td>
                        <td><span style="font-size:12px;color:var(--muted);">{{ r.lastcheck|safe }}</span></td>
                        <td><span style="font-size:12px;">{{ r.online_sejak }}</span></td>
                        <td>
                            <span class="badge-uptime {{ r.uptime_warna }}">
                                <i></i>{{ r.uptimesince }}
                            </span>
                        </td>
                        <td style="text-align:center">
                            <a class="btn-resume" href="{{ url_for('detail_sekolah', id=r.id, dari='online', w=wilayah_pilih) }}">Resume</a>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            <div class="kosong mr-kosong" style="display:none;padding:24px;text-align:center;">Tidak ada sekolah yang cocok dengan pencarian.</div>
            {% else %}
            <div class="kosong" style="padding:24px;text-align:center;">Tidak ada sekolah di kategori ini. &#127881;</div>
            {% endif %}
        </div>
        {% endfor %}

        {% if wilayah_pilih == 'Belum Dikelompokkan' %}
        {% set belum_detail = diagnosa_belum(online_schools) %}
        {% if belum_detail %}
        <div class="card" style="padding:18px 22px">
            <h3 style="margin:0 0 4px;font-size:16px;font-weight:600">Kenapa belum dikelompokkan?</h3>
            <div style="font-size:12px;color:var(--muted);margin-bottom:10px;line-height:1.5">
                Ini isi data PRTG untuk tiap sekolah di wilayah ini. Kalau nama wilayahnya terlihat di salah satu kolom
                tapi tidak terbaca, kirim contohnya agar kolom itu dibaca. Kalau semua kolom kosong, isi
                Location/Tags di device PRTG, atau tambahkan aturan di pengaturan_sekolah.json.
            </div>
            <div class="tabel-scroll">
            <table class="rinci">
                <thead><tr><th>NAMA SEKOLAH</th><th>JALUR GRUP</th><th>LOCATION</th><th>TAGS</th><th>COMMENTS</th></tr></thead>
                <tbody>
                {% for b in belum_detail %}
                <tr>
                    <td>{{ b.device }}</td>
                    <td>{{ b.jalur }}</td>
                    <td>{{ b.lokasi or '-' }}</td>
                    <td>{{ b.tags or '-' }}</td>
                    <td>{{ b.komentar or '-' }}</td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
            </div>
        </div>
        {% endif %}
        {% endif %}

        <!-- Grafik naik turun Sekolah Online -->
        <div class="card grafik-card">
            <div class="grafik-head">
                <div>
                    <h3>Tren Sekolah Online &middot; {{ nama_wilayah }}</h3>
                    {% if grafik %}
                    <div class="ket">{{ grafik.awal }} &ndash; {{ grafik.akhir }} &middot; {{ grafik.jumlah_titik }} titik riwayat</div>
                    {% else %}
                    <div class="ket">Dibangun dari riwayat setiap dashboard ini dibuka/refresh</div>
                    {% endif %}
                </div>
                {% if grafik %}
                <div class="stat-row">
                    <div class="stat"><b>{{ grafik.sekarang }}</b><span>Sekarang</span></div>
                    <div class="stat"><b>{{ grafik.nmin }}</b><span>Terendah</span></div>
                    <div class="stat"><b>{{ grafik.nmax }}</b><span>Tertinggi</span></div>
                    <div class="stat">
                        <b class="{% if grafik.selisih > 0 %}naik-online{% elif grafik.selisih < 0 %}turun-online{% endif %}">{% if grafik.selisih > 0 %}+{% endif %}{{ grafik.selisih }}</b>
                        <span>Perubahan</span>
                    </div>
                </div>
                {% endif %}
            </div>

            {% if grafik %}
            <div class="legend">
                {% for s in grafik.seri %}
                <button type="button" class="lg" data-seri="{{ s.kunci }}"><i style="background:{{ s.warna }}"></i>{{ s.label }}</button>
                {% endfor %}
            </div>
            <svg class="grafik-svg" viewBox="0 0 {{ grafik.lebar }} {{ grafik.tinggi }}">
                {% for g in grafik.grid %}
                <line class="g-grid" x1="{{ grafik.kiri }}" x2="{{ grafik.kanan_x }}" y1="{{ g.y }}" y2="{{ g.y }}"></line>
                <text x="{{ grafik.kiri - 8 }}" y="{{ g.y + 4 }}" text-anchor="end">{{ g.label }}</text>
                {% endfor %}
                {% for lx in grafik.label_x %}
                <text x="{{ lx.x }}" y="{{ grafik.label_y }}" text-anchor="middle">{{ lx.label }}</text>
                {% endfor %}
                {% for s in grafik.seri %}
                <g data-seri="{{ s.kunci }}">
                    {% if s.area %}<polygon points="{{ s.area }}" fill="{{ s.warna }}" opacity="0.08"></polygon>{% endif %}
                    <polyline points="{{ s.poly }}" fill="none" stroke="{{ s.warna }}" stroke-width="{{ s.tebal }}"
                              stroke-linejoin="round" stroke-linecap="round"></polyline>
                    {% for p in s.pts %}
                    <circle cx="{{ p.x }}" cy="{{ p.y }}" r="{{ 3.5 if s.kunci == 'total' else 2.5 }}"
                            fill="#fff" stroke="{{ s.warna }}" stroke-width="2"><title>{{ s.label }}: {{ p.v }} sekolah &middot; {{ p.waktu }}</title></circle>
                    {% endfor %}
                </g>
                {% endfor %}
            </svg>
            {% else %}
            <div class="kosong">Riwayat belum cukup untuk grafik. Buka/refresh dashboard ini beberapa kali lagi untuk mulai melihat trennya.</div>
            {% endif %}
        </div>

    </div>
</div>

<div class="modal" id="modal-rinci">
    <div class="modal-box lebar">
        <div class="mr-head">
            <h3 id="mr-judul">Daftar sekolah</h3>
            <button type="button" class="mr-tutup" id="mr-tutup" title="Tutup">&times;</button>
        </div>
        <div class="mr-search">
            <input id="cari-rinci" type="text" placeholder="Cari sekolah..." autocomplete="off">
        </div>
        <div class="mr-body" id="mr-body"></div>
    </div>
</div>

<div class="footer">
    Sensor online dari PRTG: {{ info_data.total_sensor }} &middot; sekolah online: {{ info_data.sekolah_online }}<br>
    Data diambil langsung dari PRTG API disaring khusus status OK (Online). Tanggal "Online Sejak" dihitung dari durasi uptime PRTG (bisa meleset sekitar satu menit). Refresh otomatis setiap 60 detik.
    {% if wilayah_pilih == 'Belum Dikelompokkan' %}<br>
    Wilayah dibaca dari PRTG berurutan: jalur grup, kolom Location, Tags, lalu nama device/sensor. Lihat tabel &quot;Kenapa belum dikelompokkan?&quot; di atas untuk melihat apa yang terbaca dari PRTG.
    Isi kolom <strong>Location</strong> (atau nama grup) di device PRTG-nya, atau atur manual lewat pengaturan_sekolah.json.
    {% endif %}
</div>

<div class="modal" id="modal">
    <div class="modal-box">
        <h3>Kirim rekap sekolah online ke WhatsApp?</h3>
        <p style="font-size:13px;color:var(--muted)">Data diambil ulang dari PRTG sebelum dikirim. Rekap mencakup semua wilayah.</p>
        <textarea class="preview" id="preview" spellcheck="false">Memuat...</textarea>
        <div class="edit-info">&#9998; Isi pesan bisa diedit langsung sebelum dikirim.</div>
        <div class="modal-actions">
            <button type="button" class="batal" id="batal">Batal</button>
            <button type="button" class="konfirmasi" id="konfirmasi">Ya, Kirim</button>
        </div>
    </div>
</div>
<div class="toast" id="toast"></div>

<script>
(function () {
    var INTERVAL = 60;
    var KUNCI = 'refresh_deadline';
    var el = document.getElementById('countdown');
    var cariRinci = document.getElementById('cari-rinci');

    function bacaDeadline() {
        try {
            var v = parseInt(sessionStorage.getItem(KUNCI), 10);
            return isNaN(v) ? 0 : v;
        } catch (e) { return 0; }
    }

    function simpanDeadline(v) {
        try { sessionStorage.setItem(KUNCI, String(v)); } catch (e) {}
    }

    var deadline = bacaDeadline();
    var kini = Date.now();
    if (!deadline || deadline <= kini || deadline > kini + INTERVAL * 1000 + 5000) {
        deadline = kini + INTERVAL * 1000;
        simpanDeadline(deadline);
    }

    function tampilSisa() {
        var sisa = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
        if (el) { el.textContent = sisa; }
    }
    tampilSisa();

    setInterval(function () {
        var modalTerbuka = document.querySelector('.modal.show');
        var jeda = modalTerbuka || (cariRinci && document.activeElement === cariRinci && cariRinci.value);
        if (jeda) {
            deadline += 1000;
            simpanDeadline(deadline);
        }
        if (deadline - Date.now() <= 0) {
            simpanDeadline(Date.now() + INTERVAL * 1000);
            location.reload();
            return;
        }
        tampilSisa();
    }, 1000);

    // Mode gelap / terang
    var btnTema = document.getElementById('tema-btn');
    function labelTema() {
        var gelap = document.documentElement.getAttribute('data-theme') === 'dark';
        if (btnTema) { btnTema.innerHTML = gelap ? '&#9728;&#65039; Terang' : '&#127769; Gelap'; }
    }
    if (btnTema) {
        btnTema.addEventListener('click', function () {
            var gelap = document.documentElement.getAttribute('data-theme') === 'dark';
            var baru = gelap ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', baru);
            try { localStorage.setItem('tema', baru); } catch (e) {}
            labelTema();
        });
        labelTema();
    }

    // Filter baris kategori (< 24 Jam / 24 - 48 Jam / > 48 Jam / Semua)
    var filterAktif = 'semua';
    function terapkanFilter() {
        var rows = document.querySelectorAll('tr[data-kat]');
        for (var i = 0; i < rows.length; i++) {
            var kat = rows[i].getAttribute('data-kat');
            rows[i].style.display = (filterAktif === 'semua' || filterAktif === kat) ? '' : 'none';
        }
    }

    var fchips = document.querySelectorAll('.filter .fchip');
    for (var f = 0; f < fchips.length; f++) {
        fchips[f].addEventListener('click', function () {
            for (var x = 0; x < fchips.length; x++) { fchips[x].classList.remove('aktif'); }
            this.classList.add('aktif');
            filterAktif = this.getAttribute('data-f');
            terapkanFilter();
            updateDownloadLinks();
        });
    }

    // Pencarian sekolah (nama) pada daftar chip
    (function () {
        var kolom = document.querySelector('.cari-chip');
        if (!kolom) { return; }
        kolom.addEventListener('input', function () {
            var q = kolom.value.toLowerCase().trim();
            var chips = document.querySelectorAll('.chip-link');
            for (var i = 0; i < chips.length; i++) {
                var ok = !q || (chips[i].getAttribute('data-cari') || '').indexOf(q) > -1;
                chips[i].classList.toggle('sembunyi', !ok);
            }
        });
    })();

    // Modal Pop up rincian (tombol Lihat daftar) + pencarian interaktif di dalam pop up
    var modalRinci = document.getElementById('modal-rinci');
    var mrBody = document.getElementById('mr-body');
    var mrJudul = document.getElementById('mr-judul');

    function saringPopup() {
        if (!cariRinci || !mrBody) return;
        var q = cariRinci.value.toLowerCase().trim();
        var items = mrBody.querySelectorAll('.rinci-item');
        var tampil = 0;
        for (var i = 0; i < items.length; i++) {
            var cocok = !q || (items[i].getAttribute('data-cari') && items[i].getAttribute('data-cari').indexOf(q) > -1);
            items[i].style.display = cocok ? '' : 'none';
            if (cocok) { tampil++; }
        }
        var kosong = mrBody.querySelector('.mr-kosong');
        if (kosong) {
            kosong.style.display = (items.length > 0 && tampil === 0) ? '' : 'none';
        }
    }

    if (cariRinci) {
        cariRinci.addEventListener('input', saringPopup);
    }

    var mata = document.querySelectorAll('.mata');
    for (var m = 0; m < mata.length; m++) {
        mata[m].addEventListener('click', function () {
            if (mrJudul) {
                mrJudul.innerHTML = this.getAttribute('data-judul');
            }
            var dataDiv = document.getElementById(this.getAttribute('data-rinci'));
            if (dataDiv && mrBody) {
                mrBody.innerHTML = dataDiv.innerHTML;
            }
            if (cariRinci) {
                cariRinci.value = '';
            }
            if (modalRinci) {
                modalRinci.classList.add('show');
            }
            saringPopup();
            if (cariRinci) {
                setTimeout(function () { cariRinci.focus(); }, 60);
            }
        });
    }

    function tutupRinci() {
        if (modalRinci) {
            modalRinci.classList.remove('show');
        }
    }

    var mrTutup = document.getElementById('mr-tutup');
    if (mrTutup) {
        mrTutup.addEventListener('click', tutupRinci);
    }

    if (modalRinci) {
        modalRinci.addEventListener('click', function (e) {
            if (e.target === modalRinci) {
                tutupRinci();
            }
        });
    }

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && modalRinci && modalRinci.classList.contains('show')) {
            tutupRinci();
        }
    });

    // Ekspor PDF & Excel
    var btnPdf = document.getElementById('btn-export-pdf');
    var btnExcel = document.getElementById('btn-export-excel');

    function updateDownloadLinks() {
        var params = new URLSearchParams();
        params.set('w', '{{ wilayah_pilih }}');
        if (filterAktif && filterAktif !== 'semua') {
            params.set('up', filterAktif);
        }
        var qs = params.toString();
        if (btnPdf) {
            btnPdf.href = '{{ url_for("unduh_online", jenis="pdf") }}?' + qs;
        }
        if (btnExcel) {
            btnExcel.href = '{{ url_for("unduh_online", jenis="xlsx") }}?' + qs;
        }
    }

    // Legenda grafik: klik untuk menyembunyikan/menampilkan garis
    var tombolLegenda = document.querySelectorAll('.lg');
    for (var j = 0; j < tombolLegenda.length; j++) {
        tombolLegenda[j].addEventListener('click', function () {
            var k = this.getAttribute('data-seri');
            var g = document.querySelector('svg g[data-seri="' + k + '"]');
            var mati = this.classList.toggle('mati');
            if (g) { g.style.display = mati ? 'none' : ''; }
        });
    }
})();

// Kirim rekap online ke WhatsApp
(function () {
    var modal = document.getElementById('modal');
    var preview = document.getElementById('preview');
    var konfirmasi = document.getElementById('konfirmasi');
    var toast = document.getElementById('toast');

    function showToast(text, jenis) {
        toast.textContent = text;
        toast.className = 'toast show ' + jenis;
        setTimeout(function () { toast.className = 'toast'; }, 4000);
    }

    document.getElementById('btn-buka-modal').addEventListener('click', function () {
        preview.value = 'Mengambil data terbaru...';
        preview.readOnly = true;
        konfirmasi.disabled = true;
        modal.classList.add('show');
        fetch('/pratinjau-wa-online')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.sukses) { preview.value = d.pesan; preview.readOnly = false; konfirmasi.disabled = false; }
                else { preview.value = 'Gagal: ' + d.error; }
            })
            .catch(function (e) { preview.value = 'Gagal: ' + e.message; });
    });

    document.getElementById('batal').addEventListener('click', function () {
        modal.classList.remove('show');
    });

    konfirmasi.addEventListener('click', function () {
        if (!preview.value.trim()) { showToast('Pesan masih kosong.', 'gagal'); return; }
        konfirmasi.disabled = true;
        konfirmasi.textContent = 'Mengirim...';
        fetch('/kirim-wa-online', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pesan: preview.value })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                modal.classList.remove('show');
                konfirmasi.disabled = false;
                konfirmasi.textContent = 'Ya, Kirim';
                if (d.sukses) { showToast('Rekap online terkirim ke WhatsApp.', 'sukses'); }
                else { showToast('Gagal: ' + d.error, 'gagal'); }
            })
            .catch(function (e) {
                modal.classList.remove('show');
                konfirmasi.disabled = false;
                konfirmasi.textContent = 'Ya, Kirim';
                showToast('Gagal: ' + e.message, 'gagal');
            });
    });

    modal.addEventListener('click', function (e) {
        if (e.target === modal) { modal.classList.remove('show'); }
    });
})();
</script>

<script>
// ============================================================
// GLOBAL SEARCH DASHBOARD INTERAKTIF
// ============================================================
(function () {
    var dataGlobal = {{ daftar_global_json | safe }} || [];
    var input = document.getElementById('global-search-input');
    var dropdown = document.getElementById('global-search-dropdown');
    var list = document.getElementById('global-search-list');
    var clearBtn = document.getElementById('global-search-clear');
    var info = document.getElementById('gs-hasil-info');
    var indeksFokus = -1;

    if (!input || !dropdown || !list) return;

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function filterSekolah(q) {
        q = (q || '').trim().toLowerCase();
        if (!q) return [];
        var kata = q.split(/\\s+/).filter(Boolean);
        return dataGlobal.filter(function (s) {
            var cariTeks = ((s.nama || '') + ' ' + (s.wilayah || '') + ' ' + (s.id || '') + ' ' + (s.prtg_id || '') + ' ' + (s.status || '') + ' ' + (s.kategori || '')).toLowerCase();
            return kata.every(function (k) { return cariTeks.indexOf(k) > -1; });
        });
    }

    function renderHasil(hasil, q) {
        list.innerHTML = '';
        indeksFokus = -1;

        if (hasil.length === 0) {
            list.innerHTML = '<div class="gs-empty"><div class="gs-empty-icon">&#128269;</div>Tidak ditemukan sekolah dengan kata kunci "<strong>' + escapeHtml(q) + '</strong>"</div>';
            if (info) info.textContent = '0 sekolah ditemukan';
            dropdown.style.display = 'block';
            return;
        }

        var nOffline = 0, nOnline = 0;
        hasil.forEach(function (s) {
            if (s.status === 'offline') nOffline++; else nOnline++;
        });

        if (info) {
            info.textContent = hasil.length + ' sekolah ditemukan (' + nOffline + ' Offline, ' + nOnline + ' Online)';
        }

        var maxTampil = 40;
        var itemTampil = hasil.slice(0, maxTampil);

        itemTampil.forEach(function (s, idx) {
            var a = document.createElement('a');
            a.className = 'gs-item';
            a.href = s.url;
            a.setAttribute('role', 'option');
            a.setAttribute('data-idx', idx);

            var isOff = s.status === 'offline';
            var dotClass = isOff ? 'merah' : 'hijau';
            var badgeClass = isOff ? 'offline' : 'online';
            var statusTeks = isOff ? 'Sekolah Offline' : 'Sekolah Online';

            a.innerHTML =
                '<div class="gs-item-left">' +
                    '<div class="gs-item-nama">' + escapeHtml(s.nama) + '</div>' +
                    '<div class="gs-item-meta">' +
                        '<span class="gs-badge-wilayah">&#128205; ' + escapeHtml(s.wilayah) + '</span>' +
                        '<span class="gs-item-id">ID: #' + escapeHtml(s.id) + '</span>' +
                    '</div>' +
                '</div>' +
                '<div class="gs-item-right">' +
                    '<span class="gs-status-badge ' + badgeClass + '">' +
                        '<i class="gs-dot ' + dotClass + '"></i> ' + statusTeks +
                    '</span>' +
                    '<span class="gs-item-ket">' + escapeHtml(s.kategori) + (s.ket ? ' &middot; ' + escapeHtml(s.ket) : '') + '</span>' +
                '</div>';

            list.appendChild(a);
        });

        if (hasil.length > maxTampil) {
            var lebih = document.createElement('div');
            lebih.className = 'gs-empty';
            lebih.style.padding = '10px 18px';
            lebih.style.fontSize = '12px';
            lebih.textContent = '... dan ' + (hasil.length - maxTampil) + ' sekolah lainnya. Persempit kata kunci untuk hasil lebih spesifik.';
            list.appendChild(lebih);
        }

        dropdown.style.display = 'block';
    }

    function updateFokus() {
        var items = list.querySelectorAll('.gs-item');
        items.forEach(function (it, idx) {
            it.classList.toggle('fokus', idx === indeksFokus);
            if (idx === indeksFokus) {
                it.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    input.addEventListener('input', function () {
        var q = this.value;
        if (clearBtn) clearBtn.style.display = q ? 'flex' : 'none';
        if (!q.trim()) {
            dropdown.style.display = 'none';
            indeksFokus = -1;
            return;
        }
        var hasil = filterSekolah(q);
        renderHasil(hasil, q);
    });

    input.addEventListener('focus', function () {
        if (this.value.trim()) {
            var hasil = filterSekolah(this.value);
            renderHasil(hasil, this.value);
        }
    });

    input.addEventListener('keydown', function (e) {
        var items = list.querySelectorAll('.gs-item');
        if (!items.length || dropdown.style.display === 'none') return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            indeksFokus = (indeksFokus + 1) % items.length;
            updateFokus();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            indeksFokus = (indeksFokus - 1 + items.length) % items.length;
            updateFokus();
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (indeksFokus >= 0 && items[indeksFokus]) {
                items[indeksFokus].click();
            } else if (items.length > 0) {
                items[0].click();
            }
        } else if (e.key === 'Escape') {
            dropdown.style.display = 'none';
            indeksFokus = -1;
        }
    });

    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            input.value = '';
            this.style.display = 'none';
            dropdown.style.display = 'none';
            indeksFokus = -1;
            input.focus();
        });
    }

    document.addEventListener('click', function (e) {
        if (!e.target.closest('.global-search-card')) {
            dropdown.style.display = 'none';
        }
    });
})();
</script>
</body>
</html>
"""



# ============================================================
# GLOBAL SEARCH HELPER
# ============================================================

def bangun_daftar_pencarian_global(daftar_offline, daftar_online):
    """
    Menyiapkan daftar seluruh sekolah (offline dan online) lintas wilayah
    dan kategori untuk fitur Global Search di dashboard utama.
    """
    hasil = []
    sudah = set()

    for d in (daftar_offline or []):
        sid = str(d.get("id", "")).strip()
        if not sid or sid in sudah:
            continue
        sudah.add(sid)
        wil = d.get("wilayah") or "Belum Dikelompokkan"
        hasil.append({
            "id": sid,
            "prtg_id": str(d.get("prtg_id", "") or ""),
            "nama": d.get("nama") or sid,
            "wilayah": wil,
            "status": "offline",
            "status_label": "Sekolah Offline",
            "kategori": d.get("category") or "Offline",
            "warna": d.get("warna") or "merah",
            "ket": d.get("lama_down") or (f"{d.get('hours', 0):.1f} jam" if d.get("hours") else "Sedang down"),
            "url": url_for("detail_sekolah", id=sid, dari="offline", w=wil),
        })

    for o in (daftar_online or []):
        sid = str(o.get("id", "")).strip()
        if not sid or sid in sudah:
            continue
        sudah.add(sid)
        wil = o.get("wilayah") or "Belum Dikelompokkan"
        hasil.append({
            "id": sid,
            "prtg_id": str(o.get("prtg_id", "") or ""),
            "nama": o.get("nama") or sid,
            "wilayah": wil,
            "status": "online",
            "status_label": "Sekolah Online",
            "kategori": o.get("uptime_kategori") or "Online",
            "warna": "hijau",
            "ket": o.get("uptimesince") or (f"Menyala {o.get('uptime_label', '')}" if o.get("uptime_label") else "Normal"),
            "url": url_for("detail_sekolah", id=sid, dari="online", w=wil),
        })

    hasil.sort(key=lambda x: x["nama"].lower())
    return hasil


@app.route("/favicon.svg")
def favicon():
    """Ikon tab browser (tanpa login, karena browser memintanya di halaman login juga)."""

    resp = Response(FAVICON_SVG, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/favicon.ico")
def favicon_ico():
    return favicon()


# ============================================================
# LOGIN / LOGOUT
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if session.get("logged_in"):
        return redirect(url_for("index"))

    error = None

    if request.method == "POST":

        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if username == DASHBOARD_USERNAME and password == DASHBOARD_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))

        error = "Username atau password salah."

    return render_template_string(LOGIN_TEMPLATE, shared_css=SHARED_CSS, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# DASHBOARD UTAMA
# ============================================================

def halaman_galat_prtg(e):
    """Halaman ramah kalau PRTG tidak bisa diambil (bukan error 500 mentah)."""

    from markupsafe import escape

    status = getattr(getattr(e, "response", None), "status_code", None)

    if status in (401, 403):
        saran = (
            "Login ke PRTG ditolak. Cek PRTG_USERNAME dan PRTG_PASSHASH / "
            "PRTG_PASSWORD di file .env (harus akun PRTG yang benar, "
            "bukan placeholder ISI_...). Lalu restart dashboard."
        )
    else:
        saran = (
            "Dashboard tidak bisa mengambil data dari PRTG. Cek koneksi ke "
            "server PRTG dan lihat bot_log.txt untuk detailnya."
        )

    return (
        "<!DOCTYPE html><meta charset='utf-8'>"
        "<link rel='icon' type='image/svg+xml' href='/favicon.svg'>"
        "<body style='font-family:Segoe UI,Arial,sans-serif;max-width:640px;"
        "margin:60px auto;padding:0 20px;line-height:1.6'>"
        "<h2>Data PRTG belum bisa diambil</h2>"
        f"<p>{escape(saran)}</p>"
        f"<p style='color:#8a90a2;font-size:13px'>Detail: {escape(str(e))[:300]}</p>"
        "<p><a href='/'>Coba lagi</a> &middot; <a href='/logout'>Keluar</a></p>"
        "</body>"
    )


@app.route("/")
@app.route("/offline")
@login_required
def index():

    try:
        sensors_all = get_down_devices()
    except Exception as e:
        print("Dashboard gagal mengambil data PRTG:", e)
        return halaman_galat_prtg(e), 502

    sensors_sekolah = filter_sekolah(sensors_all)

    nama_abaikan = sorted({
        nama_bersih(sn.get("device", ""))
        for sn in sensors_all
        if not is_sekolah(sn.get("device", ""))
    })

    details_semua = prepare_details(sensors_sekolah)

    # Sinkronkan kelompok waktu dari details_semua yang sudah bebas duplikasi
    groups = {
        "< 24 jam": sorted([d["id"] for d in details_semua if d.get("category") == "< 24 jam"]),
        "24 - 48 jam": sorted([d["id"] for d in details_semua if d.get("category") == "24 - 48 jam"]),
        "> 48 jam": sorted([d["id"] for d in details_semua if d.get("category") == "> 48 jam"]),
    }

    ids_24, ids_48, ids_rekap = ringkas_groups(groups)

    catat_gangguan(details_semua)

    hitung = hitung_per_wilayah(details_semua)

    riwayat = tambah_riwayat(
        len(ids_24), len(ids_48), len(ids_rekap),
        hitung if ambil_jalur_grup() else None,
    )

    daftar_wilayah = daftar_navbar(hitung)

    wilayah_pilih = request.args.get("w", "semua")

    if wilayah_pilih not in [w["nama"] for w in daftar_wilayah]:
        wilayah_pilih = "semua"

    if wilayah_pilih == "semua":
        details = details_semua
        grafik = buat_grafik(riwayat)
    else:
        details = [d for d in details_semua if d["wilayah"] == wilayah_pilih]
        grafik = buat_grafik(riwayat_wilayah(riwayat, wilayah_pilih))

    total = len(ids_24) + len(ids_48) + len(ids_rekap)
    n_baru_down = sum(
        1 for d in details
        if d.get("lastdown_text") and d.get("hours", 0) < 1
    )
    pct_baru_down = round(n_baru_down / len(details) * 100, 1) if details else 0
    
    # Ambil data online dari cache untuk badge switcher
    online_cached, _ = ambil_data_online_cached()
    total_online = len(online_cached)

    daftar_global = bangun_daftar_pencarian_global(details_semua, online_cached)

    return render_template_string(
        TEMPLATE,
        total_offline_global=len(details_semua),
        total_online_global=total_online,
        daftar_global_json=json.dumps(daftar_global),
        shared_css=SHARED_CSS,
        prtg_server=PRTG_SERVER,
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_24=len(ids_24),
        total_48=len(ids_48),
        total_rekap=len(ids_rekap),
        total_semua=total,
        total_offline=total,
        total_offline_wilayah=len(details),
        total_online=total_online,
        active_page="offline",
        grafik=grafik,
        stat_offline={"baru": n_baru_down, "pct_baru": pct_baru_down},
        details=details,
        kategori=ringkas_kategori(details),
        semua=sorted(details, key=lambda d: -d["hours"]),
        total_kartu=statistik(sorted(details, key=lambda d: -d["hours"])),
        wilayah_pilih=wilayah_pilih,
        daftar_wilayah=daftar_wilayah,
        nama_wilayah="Semua Wilayah" if wilayah_pilih == "semua" else wilayah_pilih,
        pilihan_wilayah=WILAYAH_URUT,
        abaikan=nama_abaikan,
        info_data={
            "sensor": len(sensors_all),
            "sekolah": len(details_semua),
            "belum": sum(1 for x in details_semua if x["wilayah"] == BELUM),
            "abaikan": len(nama_abaikan),
        },
    )


@app.route("/offline/unduh/<jenis>")
@login_required
def unduh_offline(jenis):
    """Mengunduh daftar gangguan offline dalam format PDF atau Excel (XLSX)."""

    if jenis not in ("pdf", "xlsx"):
        return "Format tidak dikenal.", 404

    try:
        details_semua = prepare_details(filter_sekolah(get_down_devices()))
    except Exception as e:
        print("Gagal mengambil data offline untuk ekspor:", e)
        return f"Gagal mengambil data PRTG: {e}", 502

    wilayah_pilih = request.args.get("w", "semua").strip()
    kat_filter = request.args.get("kat", "semua").strip()

    peta_kategori = {
        "kuning": ("< 24 jam", "< 24 Jam"),
        "merah": ("24 - 48 jam", "24 - 48 Jam"),
        "hitam": ("> 48 jam", "> 48 Jam"),
    }

    if wilayah_pilih != "semua":
        items = [d for d in details_semua if d.get("wilayah") == wilayah_pilih]
    else:
        items = list(details_semua)

    nama_kategori = "Semua"

    if kat_filter in peta_kategori:
        kunci_data, nama_kategori = peta_kategori[kat_filter]
        items = [d for d in items if d.get("category") == kunci_data]

    # Downtime terlama di depan, sama seperti tampilan dashboard
    items.sort(key=lambda d: -d.get("hours", 0))

    nama_wilayah = "Semua Wilayah" if wilayah_pilih == "semua" else wilayah_pilih
    aman_wilayah = re.sub(r"[^A-Za-z0-9_-]+", "_", wilayah_pilih).strip("_") or "semua"

    try:
        if jenis == "pdf":
            buf = buat_pdf_offline(items, nama_wilayah, nama_kategori)
            mime = "application/pdf"
        else:
            buf = buat_xlsx_offline(items, nama_wilayah, nama_kategori)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except ImportError as e:
        return (
            "Pustaka belum terpasang. Jalankan di terminal: "
            f"pip install openpyxl reportlab  ({e})"
        ), 500
    except Exception as e:
        return f"Gagal membuat berkas {jenis.upper()}: {e}", 500

    tgl = datetime.now().strftime("%Y%m%d_%H%M")
    return send_file(
        buf,
        mimetype=mime,
        as_attachment=True,
        download_name=f"sekolah_offline_{aman_wilayah}_{tgl}.{jenis}",
    )


@app.route("/online")
@login_required
def dashboard_online():
    try:
        details_online_semua, total_sensor = ambil_data_online_cached()
    except Exception as e:
        print("Dashboard gagal mengambil data PRTG Online:", e)
        return halaman_galat_prtg(e), 502

    hitung = hitung_online_per_wilayah(details_online_semua)
    daftar_wilayah = daftar_navbar_online(hitung)

    wilayah_pilih = request.args.get("w", "semua")
    if wilayah_pilih not in [w["nama"] for w in daftar_wilayah]:
        wilayah_pilih = "semua"

    if wilayah_pilih == "semua":
        details_online = details_online_semua
    else:
        details_online = [d for d in details_online_semua if d["wilayah"] == wilayah_pilih]

    total_online_wil = len(details_online)

    n_baru = sum(1 for d in details_online if d.get("baru_online"))
    pct_baru = round(n_baru / total_online_wil * 100, 1) if total_online_wil else 0

    total_kartu = statistik_online(
        sorted(details_online, key=lambda d: -d.get("uptime_hours", 0))
    )

    # Riwayat online untuk grafik analitik tren
    riwayat_online = catat_riwayat_online(details_online_semua) or baca_riwayat_online()

    if wilayah_pilih == "semua":
        grafik = buat_grafik_online(riwayat_online)
    else:
        grafik = buat_grafik_online(riwayat_online_wilayah(riwayat_online, wilayah_pilih))

    # Ambil total offline untuk badge tab switcher
    down_cached = ambil_data_down_cached()
    total_offline = len(down_cached)

    kategori_online = ringkas_kategori_online(details_online)

    daftar_global = bangun_daftar_pencarian_global(down_cached, details_online_semua)

    return render_template_string(
        ONLINE_TEMPLATE,
        total_offline_global=total_offline,
        total_online_global=len(details_online_semua),
        daftar_global_json=json.dumps(daftar_global),
        shared_css=SHARED_CSS,
        prtg_server=PRTG_SERVER,
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        online_schools=details_online,
        kategori_online=kategori_online,
        total_kartu=total_kartu,
        total_online_wilayah=total_online_wil,
        total_sensor_online=total_sensor,
        total_semua_online=len(details_online_semua),
        total_offline=total_offline,
        total_online=len(details_online_semua),
        active_page="online",
        grafik=grafik,
        stat_online={"baru": n_baru, "pct_baru": pct_baru},
        wilayah_pilih=wilayah_pilih,
        daftar_wilayah=daftar_wilayah,
        nama_wilayah="Semua Wilayah" if wilayah_pilih == "semua" else wilayah_pilih,
        pilihan_wilayah=WILAYAH_URUT,
        info_data={
            "total_sensor": total_sensor,
            "sekolah_online": len(details_online_semua),
        },
    )


@app.route("/online/unduh/<jenis>")
@login_required
def unduh_online(jenis):
    """Mengunduh daftar sekolah online dalam format PDF atau Excel (XLSX)."""
    if jenis not in ("pdf", "xlsx"):
        return "Format tidak dikenal.", 404

    try:
        details_online_semua, _ = ambil_data_online_cached()
    except Exception as e:
        print("Gagal mengambil data online untuk ekspor:", e)
        return f"Gagal mengambil data PRTG: {e}", 502

    wilayah_pilih = request.args.get("w", "semua").strip()
    uptime_filter = request.args.get("up", "semua").strip()
    query_cari = request.args.get("q", "").strip().lower()

    if wilayah_pilih != "semua":
        items = [d for d in details_online_semua if d.get("wilayah") == wilayah_pilih]
    else:
        items = list(details_online_semua)

    nama_kategori = "Semua"

    if uptime_filter and uptime_filter != "semua":
        items = [d for d in items if d.get("uptime_kategori") == uptime_filter]
        nama_kategori = {
            "t24": "< 24 Jam",
            "t48": "24 - 48 Jam",
            "rekap": "> 48 Jam",
        }.get(uptime_filter, "Semua")

    if query_cari:
        def cocok(d):
            gabung = f"{d.get('nama', '')} {d.get('sensor', '')} {d.get('wilayah', '')}".lower()
            return query_cari in gabung
        items = [d for d in items if cocok(d)]

    nama_wilayah = "Semua Wilayah" if wilayah_pilih == "semua" else wilayah_pilih
    aman_wilayah = re.sub(r"[^A-Za-z0-9_-]+", "_", wilayah_pilih).strip("_") or "semua"

    try:
        if jenis == "pdf":
            buf = buat_pdf_online(items, nama_wilayah, nama_kategori)
            mime = "application/pdf"
        else:
            buf = buat_xlsx_online(items, nama_wilayah, nama_kategori)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except ImportError as e:
        return (
            "Pustaka belum terpasang. Jalankan di terminal: "
            f"pip install openpyxl reportlab  ({e})"
        ), 500
    except Exception as e:
        return f"Gagal membuat berkas {jenis.upper()}: {e}", 500

    tgl = datetime.now().strftime("%Y%m%d_%H%M")
    return send_file(
        buf,
        mimetype=mime,
        as_attachment=True,
        download_name=f"sekolah_online_{aman_wilayah}_{tgl}.{jenis}",
    )


# ============================================================
# PRATINJAU & KIRIM WHATSAPP (rekap sekolah)
# ============================================================

def _pesan_edit():
    """Teks pesan hasil edit dari dashboard (kosong kalau tidak dikirim)."""

    data = request.get_json(silent=True) or {}

    return str(data.get("pesan") or "").strip()[:6000]


@app.route("/pratinjau-wa")
@api_login_required
def pratinjau_wa():

    try:
        nama_24, nama_48, nama_rekap = _nama_down()
        pesan = format_pesan_sekolah(nama_24, nama_48, nama_rekap)
        return jsonify(sukses=True, pesan=pesan)
    except Exception as e:
        return jsonify(sukses=False, error=str(e)), 500


@app.route("/kirim-wa-sekolah", methods=["POST"])
@api_login_required
def kirim_wa_sekolah():

    try:
        pesan = _pesan_edit() or format_pesan_sekolah(*_nama_down())

        terkirim = send_wa(pesan)

        if terkirim:
            return jsonify(sukses=True)

        return jsonify(
            sukses=False,
            error="GreenAPI gagal mengirim. Cek bot_log.txt.",
        ), 502

    except Exception as e:
        return jsonify(sukses=False, error=str(e)), 500


@app.route("/pratinjau-wa-online")
@api_login_required
def pratinjau_wa_online():

    try:
        return jsonify(sukses=True, pesan=format_pesan_online(*_nama_online()))
    except Exception as e:
        return jsonify(sukses=False, error=str(e)), 500


@app.route("/kirim-wa-online", methods=["POST"])
@api_login_required
def kirim_wa_online():

    try:
        pesan = _pesan_edit() or format_pesan_online(*_nama_online())

        if send_wa(pesan):
            return jsonify(sukses=True)

        return jsonify(
            sukses=False,
            error="GreenAPI gagal mengirim. Cek bot_log.txt.",
        ), 502

    except Exception as e:
        return jsonify(sukses=False, error=str(e)), 500


# ============================================================
# RESUME PER SEKOLAH + UNDUH PDF / EXCEL
# ============================================================

@app.route("/sekolah/unduh/<jenis>")
@login_required
def unduh_resume(jenis):

    sid = request.args.get("id", "").strip()

    if jenis not in ("pdf", "xlsx"):
        return "Format tidak dikenal.", 404

    c = bangun_resume(sid)

    if c is None:
        return "Sekolah tidak ditemukan atau belum punya riwayat.", 404

    aman = re.sub(r"[^A-Za-z0-9_-]+", "_", sid).strip("_") or "sekolah"

    try:
        if jenis == "pdf":
            buf = buat_pdf(c)
            mime = "application/pdf"
        else:
            buf = buat_xlsx(c)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except ImportError as e:
        return (
            "Pustaka belum terpasang. Jalankan di terminal: "
            f"pip install openpyxl reportlab  ({e})"
        ), 500

    return send_file(
        buf,
        mimetype=mime,
        as_attachment=True,
        download_name=f"resume_{aman}_{datetime.now().strftime('%Y%m%d')}.{jenis}",
    )


# ============================================================
# DEBUG WILAYAH
# ============================================================

@app.route("/debug-wilayah")
@login_required
def debug_wilayah():

    details = prepare_details(filter_sekolah(get_down_devices()))

    hasil = {}

    for d in details:
        hasil.setdefault(d["group"] or "(kosong)", set()).add(d["wilayah"])

    return jsonify({k: sorted(v) for k, v in hasil.items()})


@app.route("/debug-wilayah-semua")
@login_required
def debug_wilayah_semua():
    """
    Semua sekolah (down + online): wilayah yang dipakai, DARI MANA wilayah itu
    didapat, dan isi jalur grup / Location / Tags / Comments PRTG-nya.
    Dipakai untuk mencari kenapa suatu sekolah masuk wilayah yang salah.
    Contoh: /debug-wilayah-semua?w=Kulon Progo  (hanya wilayah itu)
    """

    jalur = ambil_jalur_grup(paksa=True)
    saring = request.args.get("w", "").strip()

    semua = list(prepare_details(filter_sekolah(get_down_devices()))) + list(ambil_data_online_cached(max_age=0)[0])

    hasil, dilihat = [], set()

    for d in semua:
        pid = str(d.get("prtg_id", "") or "")

        if pid in dilihat:
            continue

        dilihat.add(pid)

        if saring and d.get("wilayah") != saring:
            continue

        inf = _cache_info["data"].get(pid, {})

        hasil.append({
            "sekolah": d.get("nama") or d.get("device", ""),
            "wilayah": d.get("wilayah"),
            "sumber": d.get("wilayah_sumber"),
            "jalur_grup": jalur.get(pid, "(tidak terbaca)"),
            "location": inf.get("lokasi", ""),
            "tags": inf.get("tags", ""),
            "comments": inf.get("komentar", ""),
        })

    hasil.sort(key=lambda x: (str(x["wilayah"]), str(x["sekolah"])))

    return jsonify(jumlah=len(hasil), sekolah=hasil)


@app.route("/debug-wilayah-online")
@login_required
def debug_wilayah_online():
    """Daftar sekolah online yang wilayahnya belum terbaca, beserta jalur grup PRTG-nya."""

    jalur = ambil_jalur_grup(paksa=True)

    hasil = []

    for d in ambil_data_online_cached(max_age=0)[0]:
        if d["wilayah"] == BELUM:
            info = info_device(d) or {}
            hasil.append({
                "id": d["id"],
                "device": d["device"],
                "group_sensor": d["group"],
                "prtg_id": d["prtg_id"],
                "jalur": jalur.get(str(d["prtg_id"]), "(TIDAK ADA)"),
                "location_prtg": info.get("lokasi", ""),
                "tags_prtg": info.get("tags", ""),
                "host_prtg": info.get("host", ""),
            })

    return jsonify(jumlah_jalur=len(jalur), belum=hasil)


# ============================================================
# PENCATAT REKAP ONLINE HARIAN
#   Menyimpan ringkasan jumlah sekolah online per hari
#   (rekap_online_harian.json). Dicatat tiap titik riwayat online
#   disimpan.
# ============================================================

REKAP_ONLINE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "rekap_online_harian.json"
)

REKAP_HARI_SIMPAN = 120   # rekap lebih tua dari ini dibuang
REKAP_CELAH_MENIT = 10    # jeda antar titik > ini dianggap "celah" (program mati / sleep)

_kunci_rekap = threading.Lock()


def baca_rekap_online():

    try:
        with open(REKAP_ONLINE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        pass

    return {}


def _simpan_rekap_online(data):
    """Tulis atomik dan RAPI (indent), supaya file ini enak dibaca langsung."""

    with _kunci_tulis:
        tmp = REKAP_ONLINE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, REKAP_ONLINE_FILE)
        except OSError as e:
            print("Gagal menyimpan rekap harian:", e)


def _stat_baru(total, waktu):

    return {
        "n": 1, "jumlah": total,
        "min": total, "min_w": waktu,
        "maks": total, "maks_w": waktu,
        "akhir": total,
    }


def _stat_tambah(s, total, waktu):

    s["n"] += 1
    s["jumlah"] += total

    if total < s["min"]:
        s["min"], s["min_w"] = total, waktu

    if total > s["maks"]:
        s["maks"], s["maks_w"] = total, waktu

    s["akhir"] = total


def _akumulasi_rekap(rekap, titik):
    """Masukkan satu titik riwayat online ke rekap hari yang sesuai."""

    waktu = str(titik.get("waktu", ""))
    dt = _parse(waktu)

    if dt is None:
        return

    tgl = waktu[:10]

    nilai = {"semua": int(titik.get("total", 0))}

    if isinstance(titik.get("w"), dict):
        for nama, c in titik["w"].items():
            nilai[nama] = int(c.get("total", 0))

    hari = rekap.get(tgl)

    if hari is None:
        rekap[tgl] = {
            "awal": waktu, "akhir": waktu,
            "cakupan": 0, "celah": 0, "celah_maks": 0,
            "w": {k: _stat_baru(v, waktu) for k, v in nilai.items()},
        }
        return

    prev = _parse(hari.get("akhir"))

    if prev is not None and dt > prev:
        menit = (dt - prev).total_seconds() / 60

        if menit <= REKAP_CELAH_MENIT:
            hari["cakupan"] += menit
        else:
            hari["celah"] += 1
            hari["celah_maks"] = max(hari.get("celah_maks", 0), round(menit))

    if prev is None or dt >= prev:
        hari["akhir"] = waktu

    for k, v in nilai.items():
        if k in hari["w"]:
            _stat_tambah(hari["w"][k], v, waktu)
        else:
            hari["w"][k] = _stat_baru(v, waktu)


def catat_rekap_online(riwayat):
    """
    Dipanggil setiap titik riwayat online dicatat.
    Pertama kali (file belum ada): rekap diisi dari riwayat yang masih
    tersimpan, jadi hari-hari yang masih ada di riwayat ikut terisi.
    """

    with _kunci_rekap:

        sudah_ada = os.path.exists(REKAP_ONLINE_FILE)
        rekap = baca_rekap_online() if sudah_ada else {}

        if not sudah_ada:
            for r in riwayat:
                _akumulasi_rekap(rekap, r)
        elif riwayat:
            _akumulasi_rekap(rekap, riwayat[-1])

        batas = (datetime.now() - timedelta(days=REKAP_HARI_SIMPAN)).strftime("%Y-%m-%d")
        rekap = {k: v for k, v in rekap.items() if k >= batas}

        _simpan_rekap_online(rekap)


# Sambungkan ke pencatat riwayat online yang sudah ada (tanpa mengubah fungsinya).
_tambah_riwayat_online_asli = tambah_riwayat_online


def tambah_riwayat_online(n24, n48, nrekap, per_wilayah=None):

    riwayat = _tambah_riwayat_online_asli(n24, n48, nrekap, per_wilayah)

    try:
        catat_rekap_online(riwayat)
    except Exception as e:
        print("Rekap harian online gagal dicatat:", e)

    return riwayat


# ============================================================
# HALAMAN REKAP HARIAN (offline & online, masing-masing sendiri)
# ============================================================

from collections import Counter

PERIODE_REKAP = {
    "semua": ("Semua", None, "semua periode"),
    "hari": ("Hari ini", 1, "hari ini"),
    "7": ("7 Hari Terakhir", 7, "7 hari terakhir"),
    "30": ("30 Hari Terakhir", 30, "30 hari terakhir"),
}

KAT_REKAP = {
    "semua": "Semua",
    "t24": "< 24 Jam",
    "t48": "24 - 48 Jam",
    "rekap": "> 48 Jam",
}

_WARNA_KAT = {"t24": "kuning", "t48": "merah", "rekap": "hitam", "none": "hijau"}


# ------------------------------------------------------------
# 1. Pasang tombol "Rekap Harian"
# ------------------------------------------------------------

_JANGKAR_XLS = 'title="Unduh Excel">&#128202; Unduh Excel</a>'

_TOMBOL_REKAP = (
    '\n                    <a class="btn-rekap" '
    'href="{{ url_for(\'halaman_rekap\', mode=\'MODE\', w=wilayah_pilih) }}" '
    'title="Buka rekap harian">&#128197; Rekap Harian</a>'
)


def _pasang_tombol_rekap(template, mode, nama):

    if _JANGKAR_XLS not in template:
        print(f"[rekap] PERINGATAN: tombol Rekap Harian tidak terpasang di {nama} (titik sisip tidak ditemukan).")
        return template

    return template.replace(
        _JANGKAR_XLS,
        _JANGKAR_XLS + _TOMBOL_REKAP.replace("MODE", mode),
        1,
    )


TEMPLATE = _pasang_tombol_rekap(TEMPLATE, "offline", "halaman offline")
ONLINE_TEMPLATE = _pasang_tombol_rekap(ONLINE_TEMPLATE, "online", "halaman online")

SHARED_CSS += """
    .btn-rekap {
        text-decoration: none; color: #fff; font-weight: 700; font-size: 12.5px;
        padding: 8px 16px; border-radius: 999px; display: inline-flex; align-items: center;
        gap: 6px; background: #2f55d4; transition: all 0.2s ease;
    }
    .btn-rekap:hover { background: #2342b0; transform: translateY(-1px); }
"""


# ------------------------------------------------------------
# 2. Pengolah data rekap (dari gangguan_sekolah.json)
# ------------------------------------------------------------

def _kat_jam(jam):

    if jam < 24:
        return "t24"

    if jam < 48:
        return "t48"

    return "rekap"


def _awal_periode(p, sekarang):

    hari = PERIODE_REKAP[p][1]

    # Periode "semua": tidak ada batas awal.
    if hari is None:
        return datetime.min

    return datetime.combine(sekarang.date() - timedelta(days=hari - 1), datetime.min.time())


def _hari_lengkap(d):

    return f"{NAMA_HARI[d.weekday()]}, {d.day} {NAMA_BULAN[d.month - 1]} {d.year}"


def _baris_rekap(semua, mode, p, sekarang):
    """
    Offline: gangguan yang MULAI putus di dalam periode (sesuai tanggalnya).
    Online : gangguan yang SUDAH PULIH di dalam periode (sesuai tanggal
             sekolah menyala lagi).
    Periode "semua" = tanpa batas awal (seluruh riwayat yang tercatat).
    """

    awal = _awal_periode(p, sekarang)
    hasil = []

    for x in semua:

        mulai = _parse(x.get("mulai"))

        if mulai is None:
            continue

        selesai = _parse(x.get("selesai"))

        if mode == "online":
            if selesai is None:
                continue
            acuan = selesai
        else:
            acuan = mulai

        if acuan < awal:
            continue

        masih = selesai is None
        jam = jam_gangguan(x)
        kat = _kat_jam(jam)
        sid = str(x.get("id", ""))
        nama = x.get("nama") or sid
        wilayah = x.get("wilayah") or BELUM

        hasil.append({
            "id": sid,
            "nama": nama,
            "ip": x.get("ip", ""),
            "wilayah": wilayah,
            "mulai": mulai,
            "selesai": selesai,
            "acuan": acuan,
            "tgl": acuan.date(),
            "tgl_txt": _hari_lengkap(acuan.date()),
            "mulai_txt": waktu_awam(mulai),
            "selesai_txt": waktu_awam(selesai) if selesai else "-",
            "masih": masih,
            "jam": jam,
            "durasi": durasi_awam(jam) + (" (berjalan)" if masih else ""),
            "kat": kat,
            "warna": _WARNA_KAT[kat],
            "label_kat": KAT_REKAP[kat],
            "cari": f"{nama} {wilayah}".lower(),
        })

    hasil.sort(key=lambda r: r["acuan"], reverse=True)

    return hasil


def _baris_online_semua(semua):
    """
    Periode "semua" untuk halaman ONLINE: satu baris per sekolah yang online
    SAAT INI (bukan per kejadian). Info putus terakhir diambil dari riwayat
    gangguan (kalau ada).
    """

    # Gangguan terakhir yang sudah pulih, per sekolah
    terakhir = {}

    for x in semua:

        mulai = _parse(x.get("mulai"))
        selesai = _parse(x.get("selesai"))

        if mulai is None or selesai is None:
            continue

        sid = str(x.get("id", ""))

        if sid not in terakhir or selesai > terakhir[sid][0]:
            terakhir[sid] = (selesai, mulai, x)

    try:
        on, _ = ambil_data_online_cached()
    except Exception as e:
        print("Rekap: data online gagal diambil:", e)
        on = []

    hasil = []
    sudah = set()

    for o in on:

        sid = str(o.get("id", ""))

        if sid in sudah:
            continue

        sudah.add(sid)

        nama = o.get("nama") or sid
        wilayah = o.get("wilayah") or BELUM
        ip = o.get("ip", "")

        if sid in terakhir:
            selesai, mulai, x = terakhir[sid]
            jam = jam_gangguan(x)
            kat = _kat_jam(jam)
            mulai_txt = waktu_awam(mulai)
            selesai_txt = waktu_awam(selesai)
            durasi = durasi_awam(jam)
            acuan = selesai
            tgl = selesai.date()
            tgl_txt = _hari_lengkap(tgl)
        else:
            jam = 0
            kat = "none"
            mulai_txt = "-"
            selesai_txt = "-"
            durasi = "Belum pernah putus"
            acuan = datetime.min
            tgl = None
            tgl_txt = "Belum pernah putus"

        hasil.append({
            "id": sid,
            "nama": nama,
            "ip": ip,
            "wilayah": wilayah,
            "mulai": None,
            "selesai": None,
            "acuan": acuan,
            "tgl": tgl,
            "tgl_txt": tgl_txt,
            "mulai_txt": mulai_txt,
            "selesai_txt": selesai_txt,
            "masih": False,
            "jam": jam,
            "durasi": durasi,
            "kat": kat,
            "warna": _WARNA_KAT[kat],
            "label_kat": "Belum pernah putus" if kat == "none" else KAT_REKAP[kat],
            "cari": f"{nama} {wilayah}".lower(),
        })

    # Urut nama dulu, lalu tanggal terbaru di atas (sort stabil).
    # Sekolah yang belum pernah putus otomatis jatuh di paling bawah.
    hasil.sort(key=lambda r: str(r["nama"]).lower())
    hasil.sort(key=lambda r: r["acuan"], reverse=True)

    return hasil


def _data_rekap(mode, wilayah, p, kat):

    sekarang = datetime.now()
    semua = baca_gangguan()

    if mode == "online" and p == "semua":
        dasar = _baris_online_semua(semua)
    else:
        dasar = _baris_rekap(semua, mode, p, sekarang)

    hitung = Counter(r["wilayah"] for r in dasar)

    daftar = [{"nama": n, "jumlah": hitung.get(n, 0)} for n in WILAYAH_URUT]

    if hitung.get(BELUM):
        daftar.append({"nama": BELUM, "jumlah": hitung[BELUM]})

    if wilayah not in {d["nama"] for d in daftar}:
        wilayah = "semua"

    baris = [r for r in dasar if wilayah == "semua" or r["wilayah"] == wilayah]

    if kat != "semua":
        baris = [r for r in baris if r["kat"] == kat]

    return {
        "sekarang": sekarang,
        "semua": semua,
        "dasar": dasar,
        "daftar": daftar,
        "wilayah": wilayah,
        "baris": baris,
    }


def _param_rekap():

    w = "semua"  # rekap selalu menampilkan semua wilayah
    p = request.args.get("p", "hari").strip()
    k = request.args.get("k", "semua").strip()

    if p not in PERIODE_REKAP:
        p = "hari"

    if k not in KAT_REKAP:
        k = "semua"

    return w, p, k


def bangun_rekap(mode, wilayah, p, kat):

    d = _data_rekap(mode, wilayah, p, kat)

    sekarang = d["sekarang"]
    wilayah = d["wilayah"]
    baris = d["baris"]
    semua = d["semua"]

    online_semua = (mode == "online" and p == "semua")

    nama_wilayah = "Semua Wilayah" if wilayah == "semua" else wilayah
    ket_periode = PERIODE_REKAP[p][2]

    # ---- Angka-angka ----
    n_kejadian = len(baris)
    n_sekolah = len({r["id"] for r in baris})
    n_pulih = sum(1 for r in baris if not r["masih"])

    masih_global = len({
        x.get("id") for x in semua
        if x.get("selesai") is None
        and (wilayah == "semua" or (x.get("wilayah") or BELUM) == wilayah)
    })

    terlama_r = max(baris, key=lambda r: r["jam"]) if baris else None

    if terlama_r and terlama_r["jam"] > 0:
        terlama = f"{terlama_r['nama']}"
        terlama_cat = durasi_awam(terlama_r["jam"])
    else:
        terlama = "-"
        terlama_cat = "Belum ada gangguan"

    pulih_r = [r for r in baris if not r["masih"] and r["kat"] != "none"]
    rata = durasi_awam(sum(r["jam"] for r in pulih_r) / len(pulih_r)) if pulih_r else "-"

    n_online = None

    if mode == "online":
        try:
            on, _ = ambil_data_online_cached()
            if wilayah != "semua":
                on = [x for x in on if x.get("wilayah") == wilayah]
            n_online = len(on)
        except Exception as e:
            print("Rekap: data online gagal diambil:", e)

    # ---- Kalimat ringkasan ----
    suffix_kat = "" if kat == "semua" else f" dengan lama putus {KAT_REKAP[kat]}"

    if mode == "offline":

        if not baris:
            ringkasan = (
                f"Pada {ket_periode}{suffix_kat} tidak ada gangguan yang tercatat untuk {nama_wilayah}."
            )
        else:
            ringkasan = (
                f"Pada {ket_periode}{suffix_kat}, tercatat {n_kejadian} gangguan di {n_sekolah} sekolah "
                f"({nama_wilayah}). {n_pulih} gangguan sudah pulih dan {masih_global} sekolah masih putus "
                f"sampai sekarang. Gangguan terlama: {terlama} ({terlama_cat})."
            )

        kotak = [
            {"lbl": "Sekolah sempat putus", "val": n_sekolah, "cat": f"{n_kejadian} kejadian gangguan"},
            {"lbl": "Sudah pulih", "val": n_pulih, "cat": "Internet sudah menyala lagi"},
            {"lbl": "Masih putus sekarang", "val": masih_global, "cat": "Belum pulih saat ini"},
            {"lbl": "Putus paling lama", "val": terlama, "cat": terlama_cat, "teks": True},
        ]

    elif online_semua:

        n_pernah = sum(1 for r in baris if r["kat"] != "none")
        n_belum = n_kejadian - n_pernah

        if not baris:
            ringkasan = f"Tidak ada sekolah online yang cocok dengan pilihan ini di {nama_wilayah}."
        else:
            ringkasan = (
                f"Menampilkan {n_kejadian} sekolah yang online saat ini di {nama_wilayah}{suffix_kat}. "
                f"{n_pernah} sekolah pernah putus lalu pulih (rata-rata putus {rata}), "
                f"sedangkan {n_belum} sekolah belum pernah putus sejak pencatatan dimulai."
            )

        kotak = [
            {"lbl": "Online sekarang", "val": n_online if n_online is not None else n_kejadian,
             "cat": "Sekolah yang terhubung saat ini"},
            {"lbl": "Pernah putus lalu pulih", "val": n_pernah, "cat": "Ada riwayat putus terakhir"},
            {"lbl": "Belum pernah putus", "val": n_belum, "cat": "Tidak ada gangguan tercatat"},
            {"lbl": "Putus paling lama", "val": terlama, "cat": terlama_cat, "teks": True},
        ]

    else:

        sekarang_txt = (
            f"Saat ini {n_online} sekolah terhubung di {nama_wilayah}. " if n_online is not None else ""
        )

        if not baris:
            ringkasan = (
                f"{sekarang_txt}Pada {ket_periode}{suffix_kat} belum ada sekolah yang tercatat putus lalu menyala lagi."
            )
        else:
            ringkasan = (
                f"{sekarang_txt}Pada {ket_periode}{suffix_kat}, {n_sekolah} sekolah sempat putus lalu "
                f"menyala lagi ({n_kejadian} kejadian). Rata-rata mereka putus selama {rata}."
            )

        kotak = [
            {"lbl": "Online sekarang", "val": n_online if n_online is not None else "-",
             "cat": "Sekolah yang terhubung saat ini"},
            {"lbl": "Sempat putus lalu pulih", "val": n_sekolah, "cat": f"{n_kejadian} kejadian"},
            {"lbl": "Rata-rata lama putus", "val": rata, "cat": "Sebelum menyala lagi", "teks": True},
            {"lbl": "Putus paling lama", "val": terlama, "cat": terlama_cat, "teks": True},
        ]

    # ---- Kelompok (per tanggal; atau per wilayah untuk Online + Semua) ----
    hari_ini = sekarang.date()
    grup = []

    for r in baris:
        if not grup or grup[-1]["tgl"] != r["tgl"]:
            grup.append({
                "tgl": r["tgl"],
                # tgl None hanya terjadi di Online + Semua (sekolah belum pernah putus)
                "label": r["tgl_txt"],
                "hari_ini": r["tgl"] == hari_ini,
                "satuan": "sekolah" if online_semua else "kejadian",
                "baris": [],
            })
        grup[-1]["baris"].append(r)

    # ---- Catatan data ----
    catatan = ""
    awal_catat = _awal_pencatatan(semua)

    if awal_catat is not None and awal_catat > _awal_periode(p, sekarang):
        catatan = (
            f"Pencatatan baru dimulai sejak {waktu_awam(awal_catat)}, jadi gangguan "
            "sebelum waktu itu tidak ada di daftar ini."
        )

    return {
        "mode": mode,
        "wilayah_pilih": wilayah,
        "nama_wilayah": nama_wilayah,
        "daftar_wilayah": d["daftar"],
        "total_semua": len(d["dasar"]),
        "periode": p,
        "periode_opsi": [(k, v[0]) for k, v in PERIODE_REKAP.items()],
        "ket_periode": ket_periode,
        "online_semua": online_semua,
        "kat": kat,
        "kat_opsi": list(KAT_REKAP.items()),
        "ringkasan": ringkasan,
        "kotak": kotak,
        "grup": grup,
        "jumlah_baris": n_kejadian,
        "catatan": catatan,
        "diambil": sekarang.strftime(FMT_WAKTU),
    }


# ------------------------------------------------------------
# 3. Unduh PDF / Excel
# ------------------------------------------------------------

def _judul_rekap(mode, p=None):

    if mode == "online" and p == "semua":
        return "REKAP SEKOLAH ONLINE (SEMUA)"

    if mode == "offline":
        return "REKAP HARIAN GANGGUAN OFFLINE" + (" (SEMUA PERIODE)" if p == "semua" else "")

    return "REKAP HARIAN SEKOLAH ONLINE (SUDAH PULIH)"


def buat_xlsx_rekap(baris, mode, nama_wilayah, ket_periode, nama_kat, p=None):

    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "Rekap Harian"

    online_semua = (mode == "online" and p == "semua")
    satuan = "sekolah" if online_semua else "kejadian"

    ws.append([_judul_rekap(mode, p)])
    ws["A1"].font = Font(bold=True, size=14, color="161A52")
    ws.append([
        f"Wilayah: {nama_wilayah} | Periode: {ket_periode} | Kategori: {nama_kat} | "
        f"Dicetak: {datetime.now().strftime(FMT_WAKTU)} | Total: {len(baris)} {satuan}"
    ])
    ws["A2"].font = Font(size=10, color="555555")
    ws.append([])

    # Tanpa kolom ID
    judul = [
        "No",
        "Wilayah/Tanggal" if online_semua else "Tanggal",
        "Nama Sekolah", "Wilayah",
        "Putus Terakhir" if online_semua else "Mulai Offline",
        "Nyala Lagi",
        "Lama Putus Terakhir" if online_semua else "Lama Putus",
        "Kategori",
    ]
    ws.append(judul)

    for col in range(1, len(judul) + 1):
        c = ws.cell(row=4, column=col)
        c.fill = PatternFill("solid", fgColor="161A52")
        c.font = Font(bold=True, color="FFFFFF")
        c.alignment = Alignment(vertical="center")

    for i, r in enumerate(baris, 1):
        ws.append([
            i,
            r["tgl_txt"],
            r["nama"],
            r["wilayah"],
            r["mulai_txt"],
            "Masih putus" if r["masih"] else r["selesai_txt"],
            r["durasi"],
            r["label_kat"],
        ])

    for kol, lebar in zip("ABCDEFGH", (6, 24, 36, 18, 22, 22, 30, 18)):
        ws.column_dimensions[kol].width = lebar

    ws.freeze_panes = "A5"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    return buf


def buat_pdf_rekap(baris, mode, nama_wilayah, ket_periode, nama_kat, p=None):

    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    buf = BytesIO()

    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        title="Rekap Harian",
    )

    online_semua = (mode == "online" and p == "semua")
    satuan = "sekolah" if online_semua else "kejadian"

    st = getSampleStyleSheet()
    judul = ParagraphStyle("T", parent=st["Heading1"], fontSize=16, leading=20, textColor=colors.HexColor("#161a52"))
    meta = ParagraphStyle("M", parent=st["Normal"], fontSize=9, leading=12, textColor=colors.HexColor("#555555"))
    sel = ParagraphStyle("C", parent=st["Normal"], fontSize=8, leading=10)
    sel_b = ParagraphStyle("CB", parent=st["Normal"], fontSize=8, leading=10, fontName="Helvetica-Bold")
    kep = ParagraphStyle("H", parent=st["Normal"], fontSize=8, leading=10, textColor=colors.white, fontName="Helvetica-Bold")

    isi = [
        Paragraph(escape(_judul_rekap(mode, p)), judul),
        Paragraph(
            f"Wilayah: <b>{escape(nama_wilayah)}</b> &middot; Periode: <b>{escape(ket_periode)}</b> &middot; "
            f"Kategori: <b>{escape(nama_kat)}</b> &middot; Dicetak: {datetime.now().strftime(FMT_WAKTU)} "
            f"&middot; Total: <b>{len(baris)} {satuan}</b>", meta),
        Spacer(1, 10),
    ]

    # Tanpa kolom ID
    judul_kolom = [
        "NO", "TANGGAL", "NAMA SEKOLAH", "WILAYAH",
        "PUTUS TERAKHIR" if online_semua else "MULAI OFFLINE",
        "NYALA LAGI",
        "LAMA PUTUS TERAKHIR" if online_semua else "LAMA PUTUS",
    ]
    data = [[Paragraph(h, kep) for h in judul_kolom]]

    for i, r in enumerate(baris, 1):
        data.append([
            Paragraph(str(i), sel),
            Paragraph(escape(r["tgl_txt"]), sel),
            Paragraph(escape(r["nama"]), sel_b),
            Paragraph(escape(r["wilayah"]), sel),
            Paragraph(escape(r["mulai_txt"]), sel),
            Paragraph("<b>Masih putus</b>" if r["masih"] else escape(r["selesai_txt"]), sel),
            Paragraph(escape(r["durasi"]), sel),
        ])

    t = Table(
        data,
        colWidths=[1.0 * cm, 4.0 * cm, 7.0 * cm, 3.2 * cm, 3.8 * cm, 3.8 * cm, 3.8 * cm],
        repeatRows=1,
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#161a52")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d4e4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fd")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    isi.append(t)

    doc.build(isi)
    buf.seek(0)

    return buf


# ------------------------------------------------------------
# 4. Tampilan halaman rekap
# ------------------------------------------------------------

REKAP_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rekap Harian {{ 'Offline' if mode == 'offline' else 'Online' }}</title>
<link rel="icon" type="image/svg+xml" href="{{ url_for('favicon') }}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<style>
{{ shared_css | safe }}

    :root{
        --ui-bg:#f4f6fb;--ui-surface:#fff;--ui-surface-soft:#f6f8fc;
        --ui-navy:#161a52;--ui-text:#1b2540;--ui-muted:#56627f;--ui-subtle:#7a86a1;
        --ui-border:#e1e6f0;--ui-border-soft:#ebeef5;
        --ui-red:#8b1626;--ui-red-bg:#fde7ea;--ui-green:#14643b;--ui-green-bg:#dcf5e6;
        --ui-amber:#765000;--ui-amber-bg:#fff1cc;--ui-blue-bg:#eef2fb;
        --ui-radius-card:16px;--ui-radius-control:12px;--ui-radius-small:8px;
        --ui-radius-indicator:4px;--ui-pill:999px;
        --ui-shadow:0 1px 2px rgba(16,24,64,.05),0 8px 24px rgba(16,24,64,.06);
        --ui-shadow-selected:0 1px 3px rgba(22,26,82,.18);
        --ui-shadow-pulse-start:0 0 0 0 rgba(139,22,38,0);
        --ui-shadow-pulse-end:0 0 0 4px rgba(139,22,38,.12);
        --ui-focus:#6d83e8;--ui-brand-light:#a9c8ff;--ui-welcome-text:#d0d7f2;
        --ui-header-border:rgba(255,255,255,.24);
        --ui-danger-edge:#b3263b;--ui-neutral-bg:#e9edf4;--ui-muted-icon:#c5cbe0;
        --ui-priority:#1d3a8a;--ui-safe-border:#b7e4c4;--ui-empty-bg:#f7f8fd;
        --ui-font:Inter,"Segoe UI",system-ui,-apple-system,Roboto,"Helvetica Neue",Arial,sans-serif;
    }
    *,*::before,*::after{box-sizing:border-box}
    body{margin:0;background:var(--ui-bg);color:var(--ui-text);font:400 14px/1.6 var(--ui-font);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}

    /* ---------- HEADER ATAS ---------- */
    .topbar {
        background: var(--navy, var(--ui-navy, #161a52)); color: #fff;
        display: flex; align-items: center; gap: 22px; flex-wrap: wrap;
        padding: 14px 34px; line-height: normal;
        font-family: 'Inter', 'Segoe UI', Arial, sans-serif;
    }
    .topbar .brand { display: flex; align-items: center; gap: 12px; }
    .topbar .brand svg { width: 42px; height: 38px; flex-shrink: 0; }
    .topbar .brand-teks { line-height: 1.1; font-family: 'Segoe UI', Arial, sans-serif; }
    .topbar .brand-teks .b1 { font-size: 11px; font-weight: 700; letter-spacing: 2.2px; color: #7fb0ff; }
    .topbar .brand-teks .b2 { font-size: 22px; font-weight: 800; letter-spacing: 3px; color: #fff; margin-top: 2px; }
    .topbar .welcome {
        font-size: 17px; font-weight: 600; flex: 1; min-width: 200px; line-height: 1.3;
        padding-left: 22px; margin-left: 4px;
        border-left: 1px solid rgba(255,255,255,0.2);
    }
    .topbar .welcome small {
        display: block; font-size: 11.5px; color: #9aa3d8; font-weight: 400;
        font-family: 'Segoe UI', Arial, sans-serif;
    }
    .topbar .top-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .topbar .logout {
        background: #fff; color: var(--navy, var(--ui-navy, #161a52)); padding: 7px 15px;
        border-radius: 999px; font-size: 12.5px; font-weight: 700; text-decoration: none;
    }
    @media (max-width: 600px) {
        .topbar { padding: 12px 14px; }
        .topbar .welcome { border-left: none; padding-left: 0; margin-left: 0; min-width: 100%; }
    }
    @media screen and (min-width: 1024px) and (max-width: 2560px) {
        .topbar { zoom: 1.12; }
    }

    /* ---------- kerangka halaman ---------- */
    .wrap{--ui-section-gap:20px;width:100%;max-width:100%;margin:0 auto;padding:26px 34px 56px;display:flex;flex-direction:column;gap:var(--ui-section-gap)}
    .wrap.resume-wrap{padding:12px 10px 56px}
    .atas{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding:8px 28px;border-bottom:1px solid var(--ui-border-soft)}
    .kembali{display:inline-flex;align-items:center;min-height:44px;color:var(--ui-navy);font-weight:600;text-decoration:none;font-size:14px}
    .kembali:hover{text-decoration:underline;text-underline-offset:3px}
    .unduh{display:flex;flex-wrap:wrap;gap:8px}
    .btn-unduh{display:inline-flex;align-items:center;justify-content:center;min-height:46px;color:var(--ui-surface);font-weight:600;font-size:14px;padding:8px 20px;border-radius:var(--ui-pill);text-decoration:none}
    .btn-unduh.pdf{background:var(--ui-red)}
    .btn-unduh.xls{background:var(--ui-green)}
    .btn-unduh:hover{filter:brightness(.92)}

    /* ---------- satu panel besar ---------- */
    .panel{position:relative;overflow:hidden;background:var(--ui-surface);border:1px solid var(--ui-border);border-radius:var(--ui-radius-card);box-shadow:var(--ui-shadow)}
    .card{background:var(--ui-surface);border:1px solid var(--ui-border);border-radius:var(--ui-radius-card);padding:24px 28px;display:flex;flex-direction:column;gap:14px;box-shadow:var(--ui-shadow)}
    .panel>.card,.panel>.tabs-prtg-wrap{background:transparent;border:0;border-top:1px solid var(--ui-border-soft);border-radius:0;box-shadow:none}
    .panel>.kepala{border-top:0;padding:26px 28px 22px}

    h1{margin:0;color:var(--ui-navy);font-size:clamp(22px,2.6vw,28px);font-weight:700;line-height:1.25;letter-spacing:-.01em}
    .kepala-judul-wrap{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:0 0 4px}
    .badge-kategori-asal{display:inline-flex;align-items:center;gap:7px;padding:5px 14px;border-radius:var(--ui-pill);font-size:13px;font-weight:700;letter-spacing:.2px;line-height:1.2}
    .badge-kategori-asal.asal-online{background:var(--ui-green-bg);color:var(--ui-green);border:1.5px solid var(--ui-safe-border)}
    .badge-kategori-asal.asal-offline{background:var(--ui-red-bg);color:var(--ui-red);border:1.5px solid #f8b4bc}
    .bka-dot{width:9px;height:9px;border-radius:50%;display:inline-block}
    .asal-online .bka-dot{background:var(--ui-green);box-shadow:0 0 6px rgba(20,100,59,.5)}
    .asal-offline .bka-dot{background:var(--ui-red);box-shadow:0 0 6px rgba(139,22,38,.5);animation:status-pulse 2.2s ease-in-out infinite}
    .badge-kategori-chip{font-weight:600!important}

    h2{margin:0;color:var(--ui-navy);font-size:16.5px;line-height:1.4;font-weight:650}
    .sub{font-size:11.5px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--ui-subtle)}
    .ket{font-size:13px;color:var(--ui-muted);line-height:1.6}
    .catatan{font-size:12.5px;color:var(--ui-muted);line-height:1.6}
    .ringkasan{margin:0;font-size:14.5px;line-height:1.7;color:var(--ui-text)}

    .meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
    .meta span{display:inline-flex;align-items:center;gap:5px;min-height:30px;background:var(--ui-blue-bg);color:var(--ui-text);border-radius:var(--ui-pill);padding:5px 12px;font-size:12.5px;font-weight:400}
    .meta span b{font-weight:600}
    .meta span.k-ok{background:var(--ui-green-bg);color:var(--ui-green);font-weight:600}
    .meta span.k-putus{background:var(--ui-red-bg);color:var(--ui-red);font-weight:600;animation:status-pulse 2.2s ease-in-out infinite}

    /* ---------- deretan kotak angka ---------- */
    .info,.angka,.mini{display:flex;flex-wrap:wrap;gap:14px;align-items:stretch}
    .kotak,.mini>div{flex:1 1 max(calc((100% - 14px * (var(--kol,4) - 1)) / var(--kol,4) - 1px),200px);min-width:0;min-height:0;background:var(--ui-surface-soft);border:1px solid var(--ui-border);border-radius:var(--ui-radius-control);padding:16px 20px;display:flex;flex-direction:column;gap:3px;transition:border-color .18s ease,box-shadow .18s ease}
    .kotak:hover,.mini>div:hover{border-color:#c7d0e6;box-shadow:0 2px 10px rgba(16,24,64,.07)}
    .kotak .lbl,.mini .l{font-size:12.5px;color:var(--ui-muted);font-weight:500}
    .kotak b,.mini b{font-size:19px;line-height:1.35;color:var(--ui-navy);font-weight:650;overflow-wrap:anywhere;font-variant-numeric:tabular-nums;margin-top:2px}
    .mini b{font-size:16px}
    .kotak .cat{font-size:12.5px;color:var(--ui-muted);line-height:1.5}

    /* ---------- tabel ---------- */
    .scroll,.tabel-scroll{overflow-x:auto}
    table{width:100%;border-collapse:collapse;min-width:640px}
    th{text-align:left;padding:10px 14px;font-size:12px;font-weight:600;color:var(--ui-muted);border-bottom:1px solid var(--ui-border);vertical-align:bottom;background:transparent}
    td{padding:12px 14px;font-size:13.5px;line-height:1.55;border-bottom:1px solid var(--ui-border-soft);vertical-align:top}
    tbody tr:last-child td{border-bottom:0}
    tr.rata td{background:var(--ui-surface-soft);font-weight:600}
    .badge,.tk{display:inline-flex;align-items:center;min-height:24px;font-weight:600;font-size:12px;padding:2px 10px;border-radius:var(--ui-pill);white-space:nowrap;background:var(--ui-neutral-bg);color:var(--ui-muted)}
    .badge.putus{background:var(--ui-red-bg);color:var(--ui-red)}
    .badge.ok{background:var(--ui-green-bg);color:var(--ui-green)}
    .badge.sebagian{background:var(--ui-amber-bg);color:var(--ui-amber)}
    .badge.kosong,.badge.netral{background:var(--ui-neutral-bg);color:var(--ui-muted)}
    .kosong{color:var(--ui-muted);font-style:italic}

    /* ---------- filter chip ---------- */
    .chip{display:inline-flex;align-items:center;justify-content:center;min-height:34px;padding:6px 14px;border-radius:var(--ui-pill);border:1px solid var(--ui-border);background:var(--ui-surface);font-size:12.5px;font-weight:600;color:var(--ui-muted);text-decoration:none}
    .chip:hover{border-color:var(--ui-navy);color:var(--ui-navy)}
    .chip.on{background:var(--ui-navy);border-color:var(--ui-navy);color:var(--ui-surface)}
    .chip:focus-visible,.kembali:focus-visible,.btn-unduh:focus-visible{outline:3px solid var(--ui-focus);outline-offset:3px}

    @keyframes status-pulse{0%,100%{box-shadow:var(--ui-shadow-pulse-start)}50%{box-shadow:var(--ui-shadow-pulse-end)}}
    @media(max-width:700px){
        .wrap{--ui-section-gap:16px;width:100%;max-width:100%;padding:18px 14px 40px}
        .wrap.resume-wrap{padding:12px 14px 40px}
        .panel>.card,.panel>.kepala{padding:20px 20px}
    }
    @media(max-width:420px){
        .wrap{width:100%;padding:14px 12px 30px}
        .wrap.resume-wrap{padding:10px 12px 30px}
        .panel>.card,.panel>.kepala{padding:18px 16px}
        .atas{align-items:stretch}
        .unduh{width:100%}
        .btn-unduh{flex:1;padding-right:10px;padding-left:10px;text-align:center}
        .meta span{max-width:100%;overflow-wrap:anywhere}
    }
    @media(prefers-reduced-motion:reduce){
        *,*::before,*::after{scroll-behavior:auto!important;animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}
    }

    /* ---------- tambahan khusus halaman rekap ---------- */
    .badge.hitam{background:#25272b;color:#fff}
    .badge.hijau{background:var(--ui-green-bg);color:var(--ui-green)}
    .badge.kuning{background:var(--ui-amber-bg);color:var(--ui-amber)}
    .badge.merah{background:var(--ui-red-bg);color:var(--ui-red)}
    .filter-baris{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
    .filter-nama{min-width:84px;font-size:12.5px;font-weight:600;color:var(--ui-muted)}
    .hari-ini{display:inline-flex;align-items:center;margin-left:8px;padding:2px 10px;border-radius:var(--ui-pill);background:var(--ui-navy);color:#fff;font-size:11.5px;font-weight:600}
    .jml{margin-left:10px;font-size:12.5px;font-weight:400;color:var(--ui-muted)}
    .masih{color:var(--ui-red);font-weight:700}
</style>
</head>
<body>

<div class="topbar">
    <div class="brand">
        <svg viewBox="0 0 100 90" fill="none" stroke-width="13" aria-hidden="true">
            <path d="M17.1 71 A38 38 0 0 1 25.6 22.9" stroke="#b5d334"/>
            <path d="M25.6 22.9 A38 38 0 0 1 74.4 22.9" stroke="#f5a31a"/>
            <path d="M74.4 22.9 A38 38 0 0 1 82.9 71" stroke="#f0156b"/>
            <polygon points="22,33 52.2,48.7 47.8,55.3" fill="#0b2a6b" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round"/>
            <circle cx="50" cy="52" r="4.5" fill="#0b2a6b" stroke="#ffffff" stroke-width="1.5"/>
        </svg>
        <div class="brand-teks">
            <div class="b1">NETWORK MONITORING</div>
            <div class="b2">SEKOLAH</div>
        </div>
    </div>
    <div class="welcome">
        Selamat datang, Admin
        <small>Rekap harian {{ 'gangguan offline' if mode == 'offline' else 'sekolah online' }} &middot; PRTG {{ prtg_server }}</small>
    </div>
    <div class="top-right">
        <a class="logout" href="{{ url_for('logout') }}">&#8617; Keluar</a>
    </div>
</div>

<div class="wrap resume-wrap">

    <main class="panel">

    <div class="atas">
        {% if mode == 'offline' %}
        <a class="kembali" href="{{ url_for('index', w=wilayah_pilih) }}">&larr; Kembali ke daftar gangguan</a>
        {% else %}
        <a class="kembali" href="{{ url_for('dashboard_online', w=wilayah_pilih) }}">&larr; Kembali ke daftar sekolah online</a>
        {% endif %}
        <div class="unduh">
            <a class="btn-unduh pdf" href="{{ url_for('unduh_rekap', mode=mode, jenis='pdf', w=wilayah_pilih, p=periode, k=kat) }}">Unduh PDF (untuk dicetak)</a>
            <a class="btn-unduh xls" href="{{ url_for('unduh_rekap', mode=mode, jenis='xlsx', w=wilayah_pilih, p=periode, k=kat) }}">Unduh Excel</a>
        </div>
    </div>

    <div class="card kepala">
        <div class="sub">Rekap harian</div>
        <div class="kepala-judul-wrap">
            <h1>{{ 'Rekap Harian Gangguan Offline' if mode == 'offline' else ('Rekap Sekolah Online (Semua)' if online_semua else 'Rekap Harian Sekolah Online') }}</h1>
            {% if mode == 'online' %}
            <span class="badge-kategori-asal asal-online"><span class="bka-dot"></span> Sekolah Online</span>
            {% else %}
            <span class="badge-kategori-asal asal-offline"><span class="bka-dot"></span> Sekolah Offline</span>
            {% endif %}
        </div>
        <div class="meta">
            <span><b>Wilayah:</b> {{ nama_wilayah }}</span>
            <span><b>Periode:</b> {{ ket_periode }}</span>
            <span><b>Diambil:</b> {{ diambil }}</span>
        </div>
    </div>

    <div class="card">
        <p class="ringkasan">{{ ringkasan }}</p>
        <div class="angka">
            {% for k in kotak %}
            <div class="kotak">
                <span class="lbl">{{ k.lbl }}</span>
                <b>{{ k.val }}</b>
                <span class="cat">{{ k.cat }}</span>
            </div>
            {% endfor %}
        </div>
    </div>

    <div class="card">
        <div class="filter-baris">
            <span class="filter-nama">Periode</span>
            {% for key, nm in periode_opsi %}
            <a class="chip {{ 'on' if periode == key }}" href="{{ url_for('halaman_rekap', mode=mode, w=wilayah_pilih, p=key, k=kat) }}">{{ nm }}</a>
            {% endfor %}
        </div>
        <div class="filter-baris">
            <span class="filter-nama">Lama putus</span>
            {% for key, nm in kat_opsi %}
            <a class="chip {{ 'on' if kat == key }}" href="{{ url_for('halaman_rekap', mode=mode, w=wilayah_pilih, p=periode, k=key) }}">{{ nm }}</a>
            {% endfor %}
        </div>
    </div>

    <div class="card">
        <h2>{{ 'Daftar sekolah online' if online_semua else 'Daftar kejadian' }}</h2>
        {% if grup %}
        <div class="tabel-scroll">
        <table>
            <thead>
                <tr>
                    <th>Sekolah</th>
                    <th>Wilayah</th>
                    <th>{{ 'Putus terakhir' if online_semua else 'Mulai offline' }}</th>
                    <th>Menyala lagi</th>
                    <th>{{ 'Lama putus terakhir' if online_semua else 'Berapa lama' }}</th>
                </tr>
            </thead>
            <tbody>
                {% for g in grup %}
                <tr class="rata">
                    <td colspan="5">{{ g.label }}{% if g.hari_ini %}<span class="hari-ini">Hari ini</span>{% endif %}<span class="jml">{{ g.baris|length }} {{ g.satuan }}</span></td>
                </tr>
                {% for r in g.baris %}
                <tr>
                    <td><b>{{ r.nama }}</b></td>
                    <td><span class="badge netral">{{ r.wilayah }}</span></td>
                    <td>{{ r.mulai_txt }}</td>
                    <td>{% if r.masih %}<span class="masih">Masih putus</span>{% else %}{{ r.selesai_txt }}{% endif %}</td>
                    <td><span class="badge {{ r.warna }}">{{ r.durasi }}</span></td>
                </tr>
                {% endfor %}
                {% endfor %}
            </tbody>
        </table>
        </div>
        {% else %}
        <div class="kosong">
            {% if mode == 'offline' %}Tidak ada gangguan yang cocok dengan pilihan ini.{% elif online_semua %}Tidak ada sekolah online yang cocok dengan pilihan ini.{% else %}Belum ada sekolah yang tercatat putus lalu menyala lagi pada pilihan ini.{% endif %}
        </div>
        {% endif %}
    </div>

    <div class="card">
        <div class="catatan">
            Lama putus: &lt; 24 Jam = kuning, 24 - 48 Jam = merah, &gt; 48 Jam = hitam (dihitung dari lama satu kali gangguan).
            Waktu mulai putus adalah perkiraan dari data PRTG; waktu menyala lagi adalah saat dashboard mendeteksi sekolah kembali normal.
            {% if online_semua %}<br>Periode Semua pada halaman online menampilkan seluruh sekolah yang online saat ini, dikelompokkan per tanggal menyala lagi terakhir. Sekolah yang belum pernah putus ada di kelompok paling bawah (hijau).{% endif %}
            {% if catatan %}<br>{{ catatan }}{% endif %}
        </div>
    </div>

    </main>

</div>

<script>
(function () {
    var K = 'rekap_scroll';
    try { if ('scrollRestoration' in history) { history.scrollRestoration = 'manual'; } } catch (e) {}
    try {
        var y = sessionStorage.getItem(K);
        if (y !== null) {
            sessionStorage.removeItem(K);
            window.scrollTo(0, parseInt(y, 10) || 0);
        }
    } catch (e) {}
    document.addEventListener('click', function (e) {
        var a = e.target.closest ? e.target.closest('a.chip') : null;
        if (a) {
            try { sessionStorage.setItem(K, String(window.scrollY)); } catch (err) {}
        }
    });
})();
</script>
</body>
</html>
"""


REKAP_PAGE_TEMPLATE = REKAP_PAGE_TEMPLATE.replace("/*TOPBAR_ZOOM*/", tab_prtg.TOPBAR_ZOOM_CSS)


# ------------------------------------------------------------
# 5. Rute
# ------------------------------------------------------------

@app.route("/rekap/<mode>")
@login_required
def halaman_rekap(mode):

    if mode not in ("offline", "online"):
        return "Halaman tidak ditemukan.", 404

    w, p, k = _param_rekap()

    try:
        c = bangun_rekap(mode, w, p, k)
    except Exception as e:
        print("Halaman rekap gagal dibuat:", e)
        return f"Rekap gagal dibuat: {e}", 500

    return render_template_string(
        REKAP_PAGE_TEMPLATE, shared_css=SHARED_CSS, prtg_server=PRTG_SERVER, **c
    )


@app.route("/rekap/<mode>/unduh/<jenis>")
@login_required
def unduh_rekap(mode, jenis):

    if mode not in ("offline", "online") or jenis not in ("pdf", "xlsx"):
        return "Format tidak dikenal.", 404

    w, p, k = _param_rekap()

    d = _data_rekap(mode, w, p, k)

    nama_wilayah = "Semua Wilayah" if d["wilayah"] == "semua" else d["wilayah"]
    aman = re.sub(r"[^A-Za-z0-9_-]+", "_", d["wilayah"]).strip("_") or "semua"

    try:
        if jenis == "pdf":
            buf = buat_pdf_rekap(d["baris"], mode, nama_wilayah, PERIODE_REKAP[p][2], KAT_REKAP[k], p)
            mime = "application/pdf"
        else:
            buf = buat_xlsx_rekap(d["baris"], mode, nama_wilayah, PERIODE_REKAP[p][2], KAT_REKAP[k], p)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except ImportError as e:
        return (
            "Pustaka belum terpasang. Jalankan di terminal: "
            f"pip install openpyxl reportlab  ({e})"
        ), 500
    except Exception as e:
        return f"Gagal membuat berkas {jenis.upper()}: {e}", 500

    return send_file(
        buf,
        mimetype=mime,
        as_attachment=True,
        download_name=f"rekap_{mode}_{aman}_{p}_{datetime.now().strftime('%Y%m%d_%H%M')}.{jenis}",
    )


# ============================================================
# MAIN
# ============================================================

# ============================================================
# TAB PRTG (Overview, Alarms, 2 Hari, 30 Hari, 365 Hari, Log)
# ============================================================

tab_prtg.pasang(
    app,
    login_required=login_required,
    ambil_down=ambil_data_down_cached,
    ambil_online=lambda: ambil_data_online_cached()[0],
    bangun_resume=bangun_resume,
    shared_css=SHARED_CSS,
    prtg_server=PRTG_SERVER,
    prtg_auth=get_prtg_auth_params,
    prtg_timeout=PRTG_TIMEOUT,
    verify_ssl=VERIFY_SSL,
)


def _ringkasan_incident_aktif_db():
    """Membaca jumlah incident aktif dari shared PostgreSQL DB untuk konsistensi web/mobile (FR-027)."""
    if "sqlalchemy" not in app.extensions:
        return None
    try:
        from sqlalchemy import func, select
        from app.infrastructure.database import db
        from app.modules.incidents.models import INCIDENT_RESOLVED, Incident

        total_aktif = db.session.scalar(
            select(func.count(Incident.id)).where(Incident.incident_status != INCIDENT_RESOLVED)
        )
        return int(total_aktif or 0)
    except Exception:
        return None


if __name__ == "__main__":

    # Port 5001, dibedakan dari dashboard_login.py (5000)
    threading.Thread(target=pencatat_latar, daemon=True).start()

    from app.infrastructure.workers import start_operational_worker
    start_operational_worker(app)

    try:
        from waitress import serve
    except ImportError:
        serve = None

    host = settings.flask_host
    port = settings.flask_port

    if serve is not None:
        print(f"Dashboard berjalan (waitress): http://localhost:{port}")
        serve(app, host=host, port=port, threads=8)
    else:
        app.run(host=host, port=port, debug=False)