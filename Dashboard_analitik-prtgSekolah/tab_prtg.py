import csv
import html
import io
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta

import requests
from flask import Response, current_app, redirect, render_template_string, request, url_for
from markupsafe import Markup, escape

_K = {}

BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

WARNA = {
    "ok": "#2e9d57",
    "sebagian": "#e8a600",
    "putus": "#d64545",
    "kosong": "#c9cfe0",
}

NAMA_KONDISI = {
    "ok": "Tersambung",
    "sebagian": "Sempat putus sebentar",
    "putus": "Putus",
    "kosong": "Tidak ada data",
}

# Tab sekolah (kode, tulisan). "resume" ditangani rute lama /sekolah.
TAB_DEF = [
    ("resume", "Resume"),
    ("overview", "Overview"),
    ("alarms", "Alarms"),
    ("log", "Log"),
]

# Tab sensor, sejajar dengan halaman sensor di PRTG.
SUB_SENSOR = [
    ("overview", "Overview"),
    ("live", "Live Data"),
    ("2hari", "2 Hari"),
    ("30hari", "30 Hari"),
    ("365hari", "365 Hari"),
    ("historic", "Historic Data"),
    ("log", "Log"),
]

# avg = rata-rata tiap berapa detik (sama dengan grafik PRTG: 1 menit, 5 menit, 1 jam, 1 hari)
PERIODE = {
    "live": dict(hari=2 / 24.0, avg=60, kotak=24, nama="2 jam terakhir",
                 per_kotak="5 menit", per_data="1 menit", judul="Live Data"),
    "2hari": dict(hari=2, avg=300, kotak=48, nama="2 hari terakhir",
                  per_kotak="1 jam", per_data="5 menit", judul="2 Hari"),
    "30hari": dict(hari=30, avg=3600, kotak=30, nama="30 hari terakhir",
                   per_kotak="1 hari", per_data="1 jam", judul="30 Hari"),
    "365hari": dict(hari=365, avg=86400, kotak=52, nama="365 hari terakhir",
                    per_kotak="1 minggu", per_data="1 hari", judul="365 Hari"),
}

RENTANG_OPSI = [
    ("hari_ini", "Hari ini"), ("kemarin", "Kemarin"), ("7", "7 hari terakhir"),
    ("30", "30 hari terakhir"), ("365", "365 hari terakhir"), ("khusus", "Tanggal sendiri"),
]

AVG_PILIHAN = [(60, "1 menit"), (300, "5 menit"), (900, "15 menit"),
               (3600, "1 jam"), (21600, "6 jam"), (86400, "1 hari")]

MAKS_BARIS_HISTORIC = 20000

BARIS_PER_HALAMAN = 50
MAKS_SENSOR = 6


# ============================================================
# AMBIL DATA DARI PRTG (dengan cache pendek)
# ============================================================

_cache = {}
_kunci_cache = threading.Lock()


def _prtg(path, params, ttl=60):
    """GET ke PRTG (login otomatis), hasil JSON. Gagal -> exception."""

    kunci = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    sekarang = time.time()

    with _kunci_cache:
        c = _cache.get(kunci)
        if c and sekarang - c[0] < ttl:
            return c[1]

    r = requests.get(
        _K["server"] + path,
        params={**params, **_K["auth"]()},
        timeout=max(int(_K.get("timeout") or 30), 30),
        verify=_K.get("verify", True),
    )
    r.raise_for_status()
    data = r.json()

    with _kunci_cache:
        if len(_cache) > 300:
            _cache.clear()
        _cache[kunci] = (sekarang, data)

    return data


def _tabel(content, kolom, **extra):

    data = _prtg(
        "/api/table.json",
        {"content": content, "columns": kolom, "count": 5000, **extra},
        ttl=30,
    )

    return data.get(content, []) if isinstance(data, dict) else []


def _tabel_aman(content, wajib, opsional, **extra):
    """Coba semua kolom sekaligus; kalau PRTG menolak, kolom opsional dicoba satu per satu."""

    try:
        return _tabel(content, ",".join(wajib + opsional), **extra)
    except Exception:
        pass

    baris = _tabel(content, ",".join(wajib), **extra)
    peta = {str(b.get("objid")): dict(b) for b in baris}

    for kol in opsional:
        try:
            for b in _tabel(content, "objid," + kol, **extra):
                oid = str(b.get("objid"))
                if oid in peta:
                    peta[oid][kol] = b.get(kol)
        except Exception:
            pass

    return list(peta.values())


def _fmt(dt):
    return dt.strftime("%Y-%m-%d-%H-%M-%S")


def _historic(objid, avg, hari):
    """Riwayat data satu objek (sensor/device). Waktu dibulatkan 5 menit supaya cache berguna."""

    sekarang = datetime.now().replace(second=0, microsecond=0)
    sekarang -= timedelta(minutes=sekarang.minute % 5)

    data = _prtg(
        "/api/historicdata.json",
        {
            "id": objid,
            "avg": avg,
            "sdate": _fmt(sekarang - timedelta(days=hari)),
            "edate": _fmt(sekarang),
            "usecaption": 1,
        },
        ttl=120,
    )

    return data.get("histdata", []) if isinstance(data, dict) else []


def _historic_rentang(objid, avg, awal, akhir):
    """Riwayat data dengan tanggal awal dan akhir yang ditentukan sendiri."""

    data = _prtg(
        "/api/historicdata.json",
        {"id": objid, "avg": avg, "sdate": _fmt(awal), "edate": _fmt(akhir), "usecaption": 1},
        ttl=120,
    )

    return data.get("histdata", []) if isinstance(data, dict) else []


# ============================================================
# PEMBERSIH TEKS, WAKTU, ANGKA
# ============================================================

def _bersih(teks):

    t = re.sub(r"<[^>]+>", " ", str(teks if teks is not None else ""))
    t = html.unescape(t)

    return re.sub(r"\s+", " ", t).strip()


def _num(v):

    if v is None or isinstance(v, bool):
        return None

    if isinstance(v, (int, float)):
        return float(v)

    t = str(v).strip().replace(",", ".")

    if not t:
        return None

    m = re.match(r"^-?\d+(?:\.\d+)?", t)

    return float(m.group(0)) if m else None


def _durasi_prtg(teks):
    """'377 d' -> '377 hari', '1 h 2 m' -> '1 jam 2 mnt'."""

    t = _bersih(teks)
    t = re.sub(r"\b(\d+)\s*d\b", r"\1 hari", t)
    t = re.sub(r"\b(\d+)\s*h\b", r"\1 jam", t)
    t = re.sub(r"\b(\d+)\s*m\b", r"\1 mnt", t)
    t = re.sub(r"\b(\d+)\s*s\b", r"\1 dtk", t)

    return t


def _jam_dari_teks(teks):
    """Perkiraan jam dari teks durasi PRTG ('377 d', '2 h 5 m'). 0 kalau tidak terbaca."""

    t = _bersih(teks).lower()
    total = 0.0

    for pola, kali in ((r"(\d+)\s*d\b", 24.0), (r"(\d+)\s*h\b", 1.0),
                       (r"(\d+)\s*m\b", 1 / 60.0), (r"(\d+)\s*s\b", 1 / 3600.0)):
        m = re.search(pola, t)
        if m:
            total += int(m.group(1)) * kali

    return total


def _durasi_menit(menit):
    """Menit -> '2 hari 3 jam 32 menit'."""

    try:
        total = int(round(float(menit)))
    except (TypeError, ValueError):
        return "-"

    if total < 1:
        return "kurang dari 1 menit"

    hari, sisa = divmod(total, 1440)
    jam, mnt = divmod(sisa, 60)
    bagian = []

    if hari:
        bagian.append(f"{hari} hari")
    if jam:
        bagian.append(f"{jam} jam")
    if mnt:
        bagian.append(f"{mnt} menit")

    return " ".join(bagian)


_RE_TGL = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?)?",
    re.I,
)


def parse_waktu(teks):
    """Teks waktu gaya PRTG ('10/14/2026 2:25:00 PM') -> datetime, atau None."""

    m = _RE_TGL.search(str(teks or ""))

    if not m:
        return None

    a, b, tahun, jam, mnt, dtk, ap = m.groups()
    bulan, hari = int(a), int(b)

    if bulan > 12 and hari <= 12:
        bulan, hari = hari, bulan

    jam = int(jam) if jam is not None else 0
    mnt = int(mnt) if mnt is not None else 0
    dtk = int(dtk) if dtk is not None else 0

    if ap:
        if ap.upper() == "PM" and jam < 12:
            jam += 12
        if ap.upper() == "AM" and jam == 12:
            jam = 0

    try:
        return datetime(int(tahun), bulan, hari, jam, mnt, dtk)
    except ValueError:
        return None


def _dt_ole(raw):
    """Waktu PRTG gaya Excel (hari sejak 1899-12-30) -> datetime."""

    try:
        return datetime(1899, 12, 30) + timedelta(days=float(raw))
    except (TypeError, ValueError, OverflowError):
        return None


def waktu_id(dt, detik=False):

    if dt is None:
        return "-"

    teks = f"{dt.day} {BULAN[dt.month - 1]} {dt.year}, {dt.hour:02d}.{dt.minute:02d}"

    if detik:
        teks += f".{dt.second:02d}"

    return teks


def tanggal_id(dt):

    return f"{dt.day} {BULAN[dt.month - 1]} {dt.year}" if dt else "-"


def _waktu_label(raw):
    """Label pendek untuk sumbu/tooltip: hanya tanggal kalau tepat tengah malam."""

    dt = parse_waktu(raw)

    if dt is None:
        return _bersih(raw) or "-"

    if dt.hour == 0 and dt.minute == 0:
        return tanggal_id(dt)

    return waktu_id(dt)


def rentang_id(teks):
    """'10/14/2026 2:25:00 PM - 2:30:00 PM' -> '14 Okt 2026, 14.25 - 14.30'."""

    t = str(teks or "")
    awal = parse_waktu(t)

    if awal is None:
        return _bersih(t) or "-"

    bagian = re.split(r"\s[-–]\s", t, maxsplit=1)
    akhir = None

    if len(bagian) == 2:
        akhir = parse_waktu(bagian[1])

        if akhir is None:
            m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?", bagian[1], re.I)
            if m:
                jam, mnt, _d, ap = m.groups()
                jam = int(jam)
                if ap and ap.upper() == "PM" and jam < 12:
                    jam += 12
                if ap and ap.upper() == "AM" and jam == 12:
                    jam = 0
                akhir = awal.replace(hour=jam, minute=int(mnt), second=0)

    if awal.hour == 0 and awal.minute == 0 and (
        akhir is None or (akhir - awal) >= timedelta(hours=23)
    ):
        return tanggal_id(awal)

    if akhir is None:
        return waktu_id(awal)

    if akhir.date() == awal.date():
        return f"{waktu_id(awal)} - {akhir.hour:02d}.{akhir.minute:02d}"

    return f"{waktu_id(awal)} - {waktu_id(akhir)}"


def _unit(k):

    m = re.search(r"\(([^()]*)\)\s*$", k)

    return m.group(1).strip() if m else ""


def _fmt_angka(v, unit=""):

    if v is None:
        return "-"

    if abs(v - round(v)) < 1e-9:
        s = str(int(round(v)))
    else:
        s = f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")

    if unit == "%":
        return s + "%"

    if unit in ("", "#"):
        return s

    if unit.lower() == "msec":
        return s + " ms"

    return f"{s} {unit}"


LABEL_KOLOM = [
    ("ping time", "Waktu respon"),
    ("minimum", "Terendah"),
    ("maximum", "Tertinggi"),
    ("packet loss", "Paket hilang"),
    ("downtime", "Waktu putus"),
    ("coverage", "Kelengkapan data"),
    ("response time index", "Indeks waktu respon"),
    ("cpu load index", "Indeks beban perangkat"),
    ("traffic index", "Indeks lalu lintas data"),
    ("alarms", "Jumlah alarm"),
]


def _label_kolom(k):

    lk = k.lower()

    for awalan, label in LABEL_KOLOM:
        if lk.startswith(awalan):
            return label

    return re.sub(r"\s*\([^()]*\)\s*$", "", k) or k


def _bintang(v):

    n = _num(v)

    if n is None:
        return 0

    return max(0, min(5, int(round(n))))


# ============================================================
# STATUS & PENJELASAN PESAN
# ============================================================

def peta_status(teks):
    """Status PRTG -> (tulisan Indonesia, kelas warna)."""

    t = _bersih(teks)
    l = t.lower()

    if not t:
        return "-", "netral"
    if l.startswith("down") and "partial" in l:
        return "Putus sebagian", "sebagian"
    if l.startswith("down") and "acknowledged" in l:
        return "Putus (sudah diakui)", "putus"
    if l.startswith("down"):
        return "Putus", "putus"
    if l.startswith("up"):
        return "Tersambung", "ok"
    if l.startswith("warning"):
        return "Peringatan", "sebagian"
    if l.startswith("unusual"):
        return "Tidak biasa", "sebagian"
    if l.startswith("paused"):
        return "Dijeda", "netral"
    if l.startswith("unknown"):
        return "Tidak diketahui", "netral"

    return t, "netral"


def penjelasan_pesan(pesan):
    """Penjelasan sederhana untuk pesan PRTG yang umum. Kosong kalau tidak dikenal."""

    t = _bersih(pesan)
    l = t.lower()

    if not t:
        return ""
    if "destination host unreachable" in l:
        return "Perangkat sekolah tidak bisa dihubungi dari server (tidak ada balasan)."
    if "timed out" in l or "timeout" in l:
        return "Perangkat sekolah tidak membalas dalam batas waktu."

    m = re.search(r"no data since\s+(.+)$", t, re.I)

    if m:
        dt = parse_waktu(m.group(1))
        awal = f" sejak {waktu_id(dt, True)}" if dt else ""
        return f"Data pemeriksaan tidak masuk{awal}, jadi status belum bisa dipastikan."

    if l == "ok":
        return "Pemeriksaan berhasil, perangkat sekolah membalas."
    if "refused" in l:
        return "Koneksi ditolak oleh perangkat."
    if "could not be resolved" in l or "name resolution" in l:
        return "Nama alamat tidak bisa diterjemahkan (masalah DNS)."

    return ""


def _alarm(status_teks):

    l = _bersih(status_teks).lower()

    return l.startswith(("down", "warning", "unusual"))


# ============================================================
# DEVICE, SENSOR, OVERVIEW, ALARMS
# ============================================================

def cari_device(sid):
    """Cari sekolah dari ID yang tampil di dashboard. Return dict (prtg_id kosong = tidak ketemu)."""

    sid = str(sid or "").strip()

    down = online = None

    try:
        down = next((d for d in (_K["ambil_down"]() or []) if str(d.get("id")) == sid), None)
    except Exception:
        pass

    try:
        online = next((d for d in (_K["ambil_online"]() or []) if str(d.get("id")) == sid), None)
    except Exception:
        pass

    acuan = down or online or {}
    pid = str(acuan.get("prtg_id") or "").strip()

    if not pid.isdigit():
        if sid.startswith("#") and sid[1:].isdigit():
            pid = sid[1:]
        elif sid.isdigit():
            pid = sid
        else:
            pid = ""

    if down and online:
        status, kelas = "Putus (sebagian pemeriksaan masih normal)", "sebagian"
    elif down:
        status, kelas = "Putus (tidak terhubung)", "putus"
    elif online:
        status, kelas = "Terhubung (normal)", "ok"
    else:
        status, kelas = "Tidak terbaca di daftar PRTG saat ini", "netral"

    return {
        "sid": sid,
        "prtg_id": pid,
        "nama": acuan.get("nama") or sid,
        "wilayah": acuan.get("wilayah") or "",
        "down": down is not None,
        "online": online is not None,
        "status": status,
        "kelas": kelas,
    }


KOL_PERANGKAT_WAJIB = ["objid", "device", "host", "status"]
KOL_PERANGKAT_OPSI = [
    "message", "dependency", "intervalx",
    "upsens", "downsens", "warnsens", "pausedsens", "unusualsens", "undefinedsens", "totalsens",
]

KOL_SENSOR_WAJIB = ["objid", "sensor", "status"]
KOL_SENSOR_OPSI = ["device", "type", "message", "lastvalue", "priority", "downtimesince", "uptimesince"]

KOL_SENSOR_INFO_OPSI = ["message", "lastcheck", "lastup", "lastdown", "uptime",
                        "downtime", "type", "interval", "lastvalue"]


def info_perangkat(did):

    for b in _tabel_aman("devices", KOL_PERANGKAT_WAJIB, KOL_PERANGKAT_OPSI, filter_objid=did):
        if str(b.get("objid")) == str(did):
            return b

    return {}


def sensor_perangkat(did):

    return _tabel_aman("sensors", KOL_SENSOR_WAJIB, KOL_SENSOR_OPSI, id=did)


def info_sensor(sensor_id):
    """Detail satu sensor (terakhir diperiksa, menyala, putus, uptime, dst)."""

    for b in _tabel_aman("sensors", ["objid", "sensor", "status"], KOL_SENSOR_INFO_OPSI,
                         filter_objid=sensor_id):
        if str(b.get("objid")) == str(sensor_id):
            return b

    return {}


def _baris_sensor(sn, no):

    status, kelas = peta_status(sn.get("status"))
    pesan = _bersih(sn.get("message"))
    nilai = _bersih(sn.get("lastvalue"))

    if not nilai:
        nilai = "Tidak ada data" if kelas == "putus" else "-"

    return {
        "no": no,
        "id": sn.get("objid"),
        "sensor": _bersih(sn.get("sensor")) or f"#{sn.get('objid')}",
        "device": _bersih(sn.get("device")),
        "status": status,
        "kelas": kelas,
        "pesan": pesan or "-",
        "penjelasan": penjelasan_pesan(pesan),
        "nilai": nilai,
        "bintang": _bintang(sn.get("priority")),
        "lama": _durasi_prtg(sn.get("downtimesince")) or "-",
        "jam": _jam_dari_teks(sn.get("downtimesince")),
        "alarm": _alarm(sn.get("status")),
    }


def bangun_info(did, galat):
    """Kotak informasi perangkat. Hanya field yang benar-benar dibalas PRTG yang ditampilkan."""

    try:
        b = info_perangkat(did)
    except Exception as e:
        galat.append(f"Informasi perangkat gagal diambil dari PRTG ({e}).")
        return []

    if not b:
        galat.append("PRTG tidak mengembalikan informasi perangkat ini.")
        return []

    def n(k):
        v = _num(b.get(k))
        return int(v) if v is not None else None

    kotak = []

    status = _bersih(b.get("status"))
    if status:
        kotak.append({"label": "Status perangkat", "asli": "Status", "nilai": status,
                      "cat": "Menurut PRTG. Status tiap sensor ada di tabel di bawah.", "kelas": ""})

    bagian = [("Putus", n("downsens")), ("Tersambung", n("upsens")), ("Peringatan", n("warnsens")),
              ("Tidak biasa", n("unusualsens")), ("Dijeda", n("pausedsens")), ("Tidak diketahui", n("undefinedsens"))]
    total = n("totalsens")

    if total is None and any(v is not None for _, v in bagian):
        total = sum(v for _, v in bagian if v)

    if total is not None:
        rinci = ", ".join(f"{a} {v}" for a, v in bagian if v)
        kotak.append({"label": "Jumlah sensor", "asli": "Sensors", "nilai": str(total),
                      "cat": rinci or "Tidak ada rincian status", "kelas": "merah" if (n("downsens") or 0) > 0 else ""})

    host = _bersih(b.get("host"))
    if host:
        kotak.append({"label": "Alamat IP / DNS", "asli": "DNS/IP", "nilai": host,
                      "cat": "Alamat yang diperiksa PRTG", "kelas": ""})

    dep = _bersih(b.get("dependency"))
    if dep:
        kotak.append({"label": "Ketergantungan", "asli": "Dependency", "nilai": dep,
                      "cat": "Perangkat/sensor yang menjadi acuan PRTG", "kelas": ""})

    interval = _durasi_prtg(b.get("intervalx"))
    if interval:
        kotak.append({"label": "Seberapa sering diperiksa", "asli": "Default Interval", "nilai": interval,
                      "cat": "Interval pemeriksaan bawaan", "kelas": ""})

    kotak.append({"label": "Nomor ID di PRTG", "asli": "ID", "nilai": f"#{did}",
                  "cat": "Nomor unik perangkat", "kelas": ""})

    return kotak


# ============================================================
# RIWAYAT: LIVE / 2 / 30 / 365 HARI (dipakai halaman sensor)
# ============================================================

def _normal(rows):
    """histdata PRTG -> [{'waktu': teks, 'nilai': {kolom: angka|None}}]."""

    hasil = []

    for r in rows or []:
        if not isinstance(r, dict):
            continue

        nilai = {}

        for k, v in r.items():
            lk = str(k).lower()
            if lk.startswith("datetime") or lk.endswith("(raw)"):
                continue
            nilai[str(k)] = _num(v)

        hasil.append({"waktu": str(r.get("datetime", "")), "nilai": nilai})

    # urutkan dari terlama ke terbaru (kiri ke kanan di grafik), apa pun urutan dari PRTG
    if all(parse_waktu(h["waktu"]) for h in hasil):
        hasil.sort(key=lambda h: parse_waktu(h["waktu"]))

    return hasil


def _kunci_kolom(keys):

    low = {k: k.lower() for k in keys}
    lewat = ("downtime", "coverage", "alarms", "minimum", "maximum", "packet loss")

    return {
        "down": next((k for k in keys if low[k].startswith("downtime")), None),
        "cov": next((k for k in keys if low[k].startswith("coverage")), None),
        "alarm": next((k for k in keys if low[k].startswith("alarms")), None),
        "resp": next((k for k in keys if not low[k].startswith(lewat)), None),
    }


def _kumpulkan_kunci(rows):

    urut = []

    for r in rows[:50]:
        for k in r["nilai"]:
            if k not in urut:
                urut.append(k)

    return urut


def _status_baris(nilai, kk):
    """ok | sebagian | putus | kosong untuk satu baris data."""

    cov = nilai.get(kk["cov"]) if kk["cov"] else None

    if cov is not None and cov <= 0:
        return "kosong"

    if kk["down"]:
        d = nilai.get(kk["down"])
        if d is None:
            return "kosong"
        if d >= 99.5:
            return "putus"
        return "sebagian" if d > 0 else "ok"

    if kk["alarm"]:
        a = nilai.get(kk["alarm"])
        if a is None:
            return "kosong"
        return "putus" if a > 0 else "ok"

    return "kosong"


def _status_kotak(grup, kk, avg):

    if not grup:
        return "kosong", 0.0

    if kk["down"]:
        ds = [r["nilai"].get(kk["down"]) for r in grup]
        ds = [d for d in ds if d is not None]

        if not ds:
            return "kosong", 0.0

        d = sum(ds) / len(ds)
        menit_kotak = len(grup) * avg / 60.0
        menit_putus = d / 100.0 * menit_kotak

        if d >= 99.5:
            return "putus", menit_putus
        if d <= 0:
            return "ok", 0.0

        return ("sebagian" if menit_putus < 60 else "putus"), menit_putus

    st = [_status_baris(r["nilai"], kk) for r in grup]

    if all(s == "kosong" for s in st):
        return "kosong", 0.0
    if "putus" in st:
        return "putus", 0.0

    return "ok", 0.0


BERAT = {"kosong": 0, "ok": 1, "sebagian": 2, "putus": 3}


def ambil_seri(sensors, p):
    """Riwayat tiap sensor untuk satu periode."""

    seri, galat = [], []

    for sn in sensors[:MAKS_SENSOR]:
        sid = sn.get("objid")
        nama = _bersih(sn.get("sensor")) or f"#{sid}"

        try:
            rows = _normal(_historic(sid, p["avg"], p["hari"]))
        except Exception as e:
            rows = []
            galat.append(f"Riwayat {p['nama']} untuk sensor {nama} gagal diambil dari PRTG ({e}).")

        keys = _kumpulkan_kunci(rows)
        kk = _kunci_kolom(keys)

        seri.append({"sensor": nama, "id": sid, "rows": rows, "keys": keys, "kk": kk})

    return seri, galat


def bangun_strip(seri, p):
    """Baris kotak warna (per jam / hari / minggu) dari semua sensor: yang terburuk menang."""

    punya = [s for s in seri if s["rows"]]

    if not punya:
        return None

    utama = punya[0]
    n = len(utama["rows"])
    jumlah = min(p["kotak"], n)

    gabung = []

    for i in range(jumlah):
        a, b = i * n // jumlah, (i + 1) * n // jumlah
        grup = utama["rows"][a:b]
        awal = _waktu_label(grup[0]["waktu"]) if grup else "-"
        akhir = _waktu_label(grup[-1]["waktu"]) if grup else "-"
        gabung.append({"status": "kosong", "menit": 0.0,
                       "rentang": awal if awal == akhir else f"{awal} s.d. {akhir}"})

    for s in punya:
        rows, m = s["rows"], len(s["rows"])

        for i in range(jumlah):
            a, b = i * m // jumlah, (i + 1) * m // jumlah
            st, menit = _status_kotak(rows[a:b], s["kk"], p["avg"])

            if BERAT[st] > BERAT[gabung[i]["status"]]:
                gabung[i]["status"] = st
            gabung[i]["menit"] = max(gabung[i]["menit"], menit)

    kotak, hitung = [], {"ok": 0, "sebagian": 0, "putus": 0, "kosong": 0}

    for g in gabung:
        hitung[g["status"]] += 1
        tip = f"{g['rentang']}: {NAMA_KONDISI[g['status']]}"

        if g["status"] in ("sebagian", "putus") and g["menit"] > 0:
            tip += f" (sekitar {_durasi_menit(g['menit'])} putus)"

        kotak.append({"warna": WARNA[g["status"]], "tip": tip})

    return {
        "kotak": kotak,
        "hitung": hitung,
        "n": jumlah,
        "kiri": gabung[0]["rentang"].split(" s.d. ")[0],
        "tengah": gabung[jumlah // 2]["rentang"].split(" s.d. ")[0],
        "kanan": gabung[-1]["rentang"].split(" s.d. ")[-1],
        "ada_data": any(v for k, v in hitung.items() if k != "kosong"),
    }


def _persen_putus(seri):
    """Persen waktu putus terbesar di antara sensor (atau None kalau tidak ada angkanya)."""

    hasil = []

    for s in seri:
        k = s["kk"]["down"]
        if not k:
            continue
        ds = [r["nilai"].get(k) for r in s["rows"]]
        ds = [d for d in ds if d is not None]
        if ds:
            hasil.append(sum(ds) / len(ds))

    return max(hasil) if hasil else None


def _urut_terbaru(rows):

    if len(rows) >= 2:
        a, b = parse_waktu(rows[0]["waktu"]), parse_waktu(rows[-1]["waktu"])
        if a and b and a > b:
            return list(rows)

    return list(reversed(rows))


def _badge_baris(nilai, kk):

    st = _status_baris(nilai, kk)

    return {"teks": NAMA_KONDISI[st], "kelas": st}


def bangun_tabel(rows, judul, per_data, hal, kk, nama_sensor="", tampil=None):
    """Tabel data per interval, terbaru di atas, dengan baris rata-rata seperti PRTG.

    tampil = daftar nama kolom yang ditampilkan (None = semua kolom).
    """

    keys = _kumpulkan_kunci(rows)

    if tampil:
        keys = [k for k in keys if k in tampil]

    kolom = [{"label": _label_kolom(k), "asli": k, "unit": _unit(k)} for k in keys]

    rata = []

    for k in keys:
        v = [r["nilai"].get(k) for r in rows]
        v = [x for x in v if x is not None]
        rata.append(_fmt_angka(sum(v) / len(v), _unit(k)) if v else "-")

    urut = _urut_terbaru(rows)
    total = len(urut)
    hal_maks = max(1, (total + BARIS_PER_HALAMAN - 1) // BARIS_PER_HALAMAN)
    hal = max(1, min(hal, hal_maks))
    potong = urut[(hal - 1) * BARIS_PER_HALAMAN: hal * BARIS_PER_HALAMAN]

    baris = []

    for r in potong:
        baris.append({
            "waktu": rentang_id(r["waktu"]),
            "asli": r["waktu"],
            "badge": _badge_baris(r["nilai"], kk) if kk is not None else None,
            "sel": [_fmt_angka(r["nilai"].get(k), _unit(k)) for k in keys],
        })

    return {
        "judul": judul, "sensor": nama_sensor, "per_data": per_data,
        "kolom": kolom, "rata": rata, "baris": baris,
        "total": total, "hal": hal, "hal_maks": hal_maks,
        "dari": (hal - 1) * BARIS_PER_HALAMAN + 1 if total else 0,
        "sampai": min(hal * BARIS_PER_HALAMAN, total),
    }


# ---------- grafik kecepatan respon (SVG sederhana) ----------

def _skala_y(vmax):

    for s in (1, 2, 4, 5, 8, 10, 20, 40, 50, 80, 100, 200, 400, 500, 800, 1000,
              2000, 4000, 5000, 8000, 10000, 20000, 50000, 100000, 1000000):
        if s >= vmax * 1.05:
            return float(s)

    return float(vmax * 1.1)


def buat_grafik(s):
    """Grafik garis waktu respon satu sensor. None kalau PRTG tidak punya angkanya."""

    rows, kk = s["rows"], s["kk"]
    k_resp = kk["resp"]

    if not rows or not k_resp:
        return None

    nilai = [r["nilai"].get(k_resp) for r in rows]
    ada = [v for v in nilai if v is not None]

    if not ada:
        return None

    n = len(rows)
    unit = _unit(k_resp)
    x0, x1, yt, yb = 52.0, 980.0, 29.0, 230.0
    vmax = _skala_y(max(ada))

    def px(i):
        return x0 + (x1 - x0) * (i / (n - 1) if n > 1 else 0.0)

    def py(v):
        return yb - (v / vmax) * (yb - yt)

    out = []

    for i in range(5):
        y = yb - (yb - yt) * i / 4
        out.append(f'<line x1="{x0:.0f}" x2="{x1:.0f}" y1="{y:.1f}" y2="{y:.1f}" '
                   f'stroke="{"#c5cbe0" if i == 0 else "#e8ecf6"}"></line>')
        out.append(f'<text x="{x0 - 8:.0f}" y="{y + 4:.1f}" text-anchor="end">'
                   f'{escape(_fmt_angka(vmax * i / 4, unit))}</text>')

    lebar = (x1 - x0) / max(n - 1, 1) / 2 if n > 1 else 4
    i = 0

    while i < n:
        if _status_baris(rows[i]["nilai"], kk) == "putus":
            j = i
            while j + 1 < n and _status_baris(rows[j + 1]["nilai"], kk) == "putus":
                j += 1
            xa, xb = max(x0, px(i) - lebar), min(x1, px(j) + lebar)
            out.insert(0, f'<rect x="{xa:.1f}" y="{yt - 9:.0f}" width="{max(xb - xa, 3):.1f}" '
                          f'height="{yb - yt + 9:.0f}" fill="#fde2e6"></rect>')
            i = j + 1
        else:
            i += 1

    d, buka = [], False

    for i, v in enumerate(nilai):
        if v is None:
            buka = False
            continue
        d.append(("L" if buka else "M") + f" {px(i):.1f} {py(v):.1f}")
        buka = True

    if len(ada) == 1:
        i = next(i for i, v in enumerate(nilai) if v is not None)
        out.append(f'<circle cx="{px(i):.1f}" cy="{py(nilai[i]):.1f}" r="3.5" fill="#1d5fd6"></circle>')
    else:
        out.append(f'<path d="{" ".join(d)}" fill="none" stroke="#1d5fd6" stroke-width="2.5" '
                   f'stroke-linejoin="round" stroke-linecap="round"></path>')

    out.append(f'<text x="{x0:.0f}" y="256">{escape(_waktu_label(rows[0]["waktu"]))}</text>')
    out.append(f'<text x="{(x0 + x1) / 2:.0f}" y="256" text-anchor="middle">'
               f'{escape(_waktu_label(rows[n // 2]["waktu"]))}</text>')
    out.append(f'<text x="{x1:.0f}" y="256" text-anchor="end">{escape(_waktu_label(rows[-1]["waktu"]))}</text>')

    judul = escape(f"Grafik {_label_kolom(k_resp)} {s['sensor']}")

    return Markup(
        f'<svg class="grafik" viewBox="0 0 1000 270" role="img" aria-label="{judul}">'
        + "".join(out) + "</svg>"
    ), (unit or "")


def buat_gauge(v, vmax, teks):
    """Jarum ukur setengah lingkaran (SVG)."""

    vmax = vmax if vmax and vmax > 0 else 1.0
    frac = max(0.0, min(1.0, v / vmax))
    cx, cy, r = 110.0, 110.0, 85.0
    ang = math.pi * (1 - frac)
    x, y = cx + r * math.cos(ang), cy - r * math.sin(ang)
    nx, ny = cx + (r - 18) * math.cos(ang), cy - (r - 18) * math.sin(ang)

    isi = ""

    if frac > 0:
        isi = (f'<path d="M {cx - r:.0f} {cy:.0f} A {r:.0f} {r:.0f} 0 0 1 {x:.1f} {y:.1f}" '
               f'fill="none" stroke="#1d5fd6" stroke-width="16" stroke-linecap="round"></path>')

    return Markup(
        '<svg class="gauge" viewBox="0 0 220 150" role="img" aria-label="Jarum ukur">'
        f'<path d="M {cx - r:.0f} {cy:.0f} A {r:.0f} {r:.0f} 0 0 1 {cx + r:.0f} {cy:.0f}" '
        'fill="none" stroke="#e8ecf6" stroke-width="16" stroke-linecap="round"></path>'
        + isi +
        f'<line x1="{cx:.0f}" y1="{cy:.0f}" x2="{nx:.1f}" y2="{ny:.1f}" stroke="#161a52" '
        'stroke-width="3" stroke-linecap="round"></line>'
        f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="5" fill="#161a52"></circle>'
        f'<text x="{cx - r:.0f}" y="132" text-anchor="middle">0</text>'
        f'<text x="{cx + r:.0f}" y="132" text-anchor="middle">{escape(_fmt_angka(vmax))}</text>'
        f'<text x="{cx:.0f}" y="146" text-anchor="middle" style="font-size:15px;font-weight:700">'
        f'{escape(teks)}</text></svg>'
    )


def kalimat_ringkas(nama, p, persen, strip, jumlah_data):

    if jumlah_data == 0:
        return f"PRTG tidak mengembalikan data riwayat {p['nama']} untuk {nama}."

    if persen is None:
        teks = (f"Data riwayat {p['nama']} untuk {nama} tersedia ({jumlah_data} data), "
                "tetapi PRTG tidak menyediakan angka persen waktu putus untuk sensor ini.")
    elif persen >= 99.5:
        teks = f"Selama {p['nama']}, {nama} tidak pernah tersambung: sekitar 100% waktu tercatat putus."
    elif persen <= 0:
        teks = f"Selama {p['nama']}, {nama} tersambung terus. Tidak ada waktu putus yang tercatat."
    else:
        menit = persen / 100.0 * p["hari"] * 1440
        teks = (f"Selama {p['nama']}, {nama} menyala sekitar {_fmt_angka(100 - persen)}% dari waktu. "
                f"Waktu putus sekitar {_fmt_angka(persen)}% (kira-kira {_durasi_menit(menit)}).")

    if strip and strip["hitung"]["kosong"]:
        teks += f" Ada {strip['hitung']['kosong']} kotak waktu tanpa data."

    return teks


def bangun_periode(did, sensors, tab, hal, pakai_device=True):
    """Semua isi untuk satu periode (Live / 2 Hari / 30 Hari / 365 Hari).

    pakai_device=False dipakai halaman sensor: hanya data sensor itu sendiri.
    """

    p = PERIODE[tab]
    galat = []

    seri, g = ambil_seri(sensors, p)
    galat += g

    strip = bangun_strip(seri, p)
    persen = _persen_putus(seri)

    tabel_device = None
    catatan_tabel = ""

    if pakai_device:
        try:
            rows_dev = _normal(_historic(did, p["avg"], p["hari"]))
            if rows_dev and _kumpulkan_kunci(rows_dev):
                kk_dev = _kunci_kolom(_kumpulkan_kunci(rows_dev))
                tabel_device = bangun_tabel(rows_dev, "Data perangkat", p["per_data"], hal, kk_dev)
        except Exception:
            pass

    tabel = []

    if tabel_device:
        tabel.append(tabel_device)
    else:
        for s in seri:
            if s["rows"]:
                tabel.append(bangun_tabel(s["rows"], f"Data sensor {s['sensor']}", p["per_data"], hal, s["kk"], s["sensor"]))

        if tabel and pakai_device:
            catatan_tabel = ("PRTG tidak menyediakan data tingkat perangkat (indeks gabungan) lewat API, "
                             "jadi yang ditampilkan adalah data tiap sensor.")

    grafik = []

    for s in seri:
        g = buat_grafik(s)
        grafik.append({"sensor": s["sensor"], "svg": g[0] if g else None, "unit": g[1] if g else "",
                       "label": _label_kolom(s["kk"]["resp"]) if s["kk"]["resp"] else "Nilai",
                       "ada_rows": bool(s["rows"])})

    utama = tabel[0] if tabel else None
    kartu = []

    if utama:
        for kol, rata in list(zip(utama["kolom"], utama["rata"]))[:8]:
            kartu.append({"label": kol["label"], "asli": kol["asli"], "nilai": rata})

    jumlah_data = max((len(s["rows"]) for s in seri), default=0)

    return {
        "p": p,
        "strip": strip,
        "grafik": grafik,
        "tabel": tabel,
        "kartu": kartu,
        "jumlah_data": jumlah_data,
        "catatan_tabel": catatan_tabel,
        "galat": galat,
        "persen": persen,
        "ringkas_kalimat": None,
    }


# ============================================================
# OVERVIEW SENSOR (pesan terakhir, data ringkas, jarum ukur, channel, grafik)
# ============================================================

def _waktu_kolom(b, k):
    """Kolom waktu PRTG (lastcheck/lastup/lastdown) -> teks tanggal Indonesia."""

    dt = _dt_ole(b.get(k + "_raw"))

    if dt is None or dt.year < 2000:
        dt = parse_waktu(b.get(k))

    if dt:
        return waktu_id(dt, True)

    t = _durasi_prtg(b.get(k))

    if not t or t.lower() in ("never", "-"):
        return "Belum pernah"

    return t


def kanal_sensor(sensor_id):
    """Daftar channel + nilai terakhir. Exception kalau PRTG menolak."""

    rows = _tabel("channels", "objid,name,lastvalue", id=sensor_id)
    hasil = []

    for r in rows:
        nama = _bersih(r.get("name"))

        if not nama:
            continue

        hasil.append({"nama": nama, "label": _label_kolom(nama),
                      "nilai": _bersih(r.get("lastvalue")) or "-", "num": _num(r.get("lastvalue"))})

    return hasil


def bangun_overview_sensor(sensor_id, sn, galat):

    out = {}

    try:
        b = info_sensor(sensor_id)
    except Exception as e:
        b = {}
        galat.append(f"Informasi sensor gagal diambil dari PRTG ({e}).")

    status, kelas = peta_status(b.get("status") or sn.get("status"))
    pesan = _bersih(b.get("message") or sn.get("message"))
    out["pesan"] = {"status": status, "kelas": kelas, "teks": pesan or "-",
                    "penjelasan": penjelasan_pesan(pesan)}

    ringkas = []

    def tambah(label, asli, nilai, cat=""):
        ringkas.append({"label": label, "asli": asli, "nilai": nilai, "cat": cat, "kelas": ""})

    if "lastcheck" in b:
        tambah("Terakhir diperiksa", "Last Check", _waktu_kolom(b, "lastcheck"))
    if "lastup" in b:
        tambah("Terakhir menyala", "Last Up", _waktu_kolom(b, "lastup"))
    if "lastdown" in b:
        tambah("Terakhir putus", "Last Down", _waktu_kolom(b, "lastdown"))

    up, down = _num(b.get("uptime")), _num(b.get("downtime"))

    if up is not None:
        tambah("Persen waktu menyala", "Uptime", _fmt_angka(up, "%"), "Hitungan kumulatif PRTG")
    if down is not None:
        tambah("Persen waktu putus", "Downtime", _fmt_angka(down, "%"), "Hitungan kumulatif PRTG")

    # grafik 2 jam / 2 hari / 30 hari (dan cakupan data dari seri 30 hari)
    grafik, cakupan = [], None

    for kode, judul, asli in (("live", "2 jam terakhir", "Last 2 hours"),
                              ("2hari", "2 hari terakhir", "Last 2 days"),
                              ("30hari", "30 hari terakhir", "Last 30 days")):
        seri, g = ambil_seri([sn], PERIODE[kode])
        galat += g
        s = seri[0]
        gr = buat_grafik(s)

        grafik.append({"judul": judul, "asli": asli, "svg": gr[0] if gr else None,
                       "unit": gr[1] if gr else "",
                       "label": _label_kolom(s["kk"]["resp"]) if s["kk"]["resp"] else "Nilai",
                       "ada_rows": bool(s["rows"])})

        if kode == "30hari" and s["kk"]["cov"]:
            v = [r["nilai"].get(s["kk"]["cov"]) for r in s["rows"]]
            v = [x for x in v if x is not None]
            cakupan = sum(v) / len(v) if v else None

    if cakupan is not None:
        tambah("Kelengkapan data", "Coverage", _fmt_angka(cakupan, "%"), "Rata-rata 30 hari terakhir")

    if _bersih(b.get("type")):
        tambah("Jenis pemeriksaan", "Type", _bersih(b.get("type")))

    interval = _durasi_prtg(b.get("interval"))

    if interval:
        tambah("Seberapa sering diperiksa", "Interval", interval)

    tambah("Nomor ID di PRTG", "ID", f"#{sensor_id}", "Nomor unik sensor")

    out["ringkas"] = ringkas
    out["grafik"] = grafik

    # channel, jarum ukur, terendah / tertinggi / paket hilang
    out["kanal"], out["stat"], out["gauge"], out["gauge_label"] = [], [], None, ""

    try:
        kanal = kanal_sensor(sensor_id)
    except Exception as e:
        kanal = []
        galat.append(f"Daftar kanal (channel) gagal diambil dari PRTG ({e}).")

    out["kanal"] = kanal

    for c in kanal:
        ln = c["nama"].lower()
        if ln.startswith(("minimum", "maximum", "packet loss")):
            merah = ln.startswith("packet loss") and (c["num"] or 0) > 0
            out["stat"].append({"label": c["label"], "asli": c["nama"], "nilai": c["nilai"],
                                "kelas": "merah" if merah else ""})

    lewat = ("downtime", "minimum", "maximum", "packet loss", "coverage")
    utama = next((c for c in kanal if c["num"] is not None and not c["nama"].lower().startswith(lewat)), None)

    if utama:
        angka_max = max([c["num"] for c in kanal
                         if c["nama"].lower().startswith("maximum") and c["num"] is not None] or [0])
        vmax = _skala_y(max(utama["num"], angka_max, 0.0001))
        out["gauge"] = buat_gauge(max(utama["num"], 0.0), vmax, utama["nilai"])
        out["gauge_label"] = f"{utama['label']} terakhir ({utama['nama']})"

    return out


# ============================================================
# HISTORIC DATA (pilih tanggal, rata-rata, kanal, format)
# ============================================================

def _int(v, default=1):

    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _dt_input(t):

    t = str(t or "").strip()

    for f in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, f)
        except ValueError:
            pass

    return None


def _rentang_historic(rentang, tgl1, tgl2):

    now = datetime.now().replace(second=0, microsecond=0)
    hari0 = now.replace(hour=0, minute=0)

    if rentang == "hari_ini":
        return hari0, now, rentang
    if rentang == "kemarin":
        return hari0 - timedelta(days=1), hari0, rentang
    if rentang in ("30", "365"):
        return now - timedelta(days=int(rentang)), now, rentang
    if rentang == "khusus":
        a, b = _dt_input(tgl1), _dt_input(tgl2)
        if a and b and a < b:
            return a, b, rentang

    return now - timedelta(days=7), now, "7"


def _csv_response(rows, keys, sensor_id):

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Waktu"] + keys)

    for r in rows:
        w.writerow([r["waktu"]] + ["" if r["nilai"].get(k) is None else r["nilai"].get(k) for k in keys])

    return Response(
        "\ufeff" + buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="sensor_{sensor_id}_historic.csv"'},
    )


def bangun_historic(sensor_id, nama_sensor, args, hal, galat, url_dasar):

    awal, akhir, rentang = _rentang_historic(args.get("rentang", "7"), args.get("tgl1"), args.get("tgl2"))
    span = (akhir - awal).total_seconds()

    avg = _int(args.get("avg"), 0)

    if avg not in dict(AVG_PILIHAN):
        avg = 300 if span <= 2 * 86400 else (3600 if span <= 40 * 86400 else 86400)

    fmt = "csv" if args.get("format") == "csv" else "tabel"
    label_avg = dict(AVG_PILIHAN)[avg]

    # daftar kanal diambil dari contoh data 2 hari (di-cache)
    try:
        keys = _kumpulkan_kunci(_normal(_historic(sensor_id, 3600, 2)))
    except Exception:
        keys = []

    dipilih = [k for k in args.getlist("kanal") if k in keys]

    h = {
        "rentang": rentang, "rentang_opsi": RENTANG_OPSI,
        "tgl1": awal.strftime("%Y-%m-%dT%H:%M"), "tgl2": akhir.strftime("%Y-%m-%dT%H:%M"),
        "awal_teks": waktu_id(awal), "akhir_teks": waktu_id(akhir),
        "avg": avg, "avg_label": label_avg, "avg_opsi": AVG_PILIHAN, "format": fmt,
        "kanal_opsi": [{"key": k, "label": _label_kolom(k), "on": (not dipilih) or k in dipilih} for k in keys],
        "tabel": None, "csv": None,
    }

    n = span / avg

    if n > MAKS_BARIS_HISTORIC:
        galat.append(f"Terlalu banyak data (sekitar {int(n)} baris). Pilih rata-rata yang lebih besar "
                     "atau persempit rentang tanggalnya.")
        return h

    try:
        rows = _normal(_historic_rentang(sensor_id, avg, awal, akhir))
    except Exception as e:
        galat.append(f"Data historis gagal diambil dari PRTG ({e}).")
        return h

    if not rows:
        return h

    semua_kunci = _kumpulkan_kunci(rows)

    if fmt == "csv":
        h["csv"] = _csv_response(_urut_terbaru(rows), dipilih or semua_kunci, sensor_id)
        return h

    kk = _kunci_kolom(semua_kunci)
    t = bangun_tabel(rows, f"Data sensor {nama_sensor}", label_avg, hal, kk, nama_sensor,
                     tampil=dipilih or None)

    def u(nomor):
        return url_for("sensor_sekolah", **url_dasar, rentang=rentang, tgl1=h["tgl1"], tgl2=h["tgl2"],
                       avg=avg, kanal=dipilih, hal=nomor)

    t["url_mundur"] = u(t["hal"] - 1) if t["hal"] > 1 else None
    t["url_lanjut"] = u(t["hal"] + 1) if t["hal"] < t["hal_maks"] else None
    h["tabel"] = t

    return h


# ============================================================
# LOG
# ============================================================

def _tgl_input(teks):

    try:
        return datetime.strptime(str(teks or ""), "%Y-%m-%d")
    except ValueError:
        return None


def bangun_log(did, rentang, tgl1, tgl2, hal, galat, nama_ok=None):
    """Log kejadian. did bisa ID perangkat (log sekolah) atau ID sensor (log sensor)."""

    sekarang = datetime.now()
    rentang = rentang if rentang in ("7", "30", "365", "khusus") else "7"

    if rentang == "khusus":
        a, b = _tgl_input(tgl1), _tgl_input(tgl2)
        if a and b and a <= b:
            awal, akhir = a, b.replace(hour=23, minute=59, second=59)
        else:
            rentang = "7"

    if rentang != "khusus":
        awal, akhir = sekarang - timedelta(days=int(rentang)), sekarang

    drel = {"7": "7days", "30": "30days"}.get(rentang, "12months")
    kolom = "objid,datetime,parent,type,name,status,message"

    rows, err = None, None

    for extra in ({"filter_drel": drel}, {}):
        try:
            rows = _tabel("messages", kolom, id=did, **extra)
            break
        except Exception as e:
            err = e

    if rows is None:
        galat.append(f"Log gagal diambil dari PRTG ({err}).")
        rows = []

    hasil = []

    for b in rows:
        dt = _dt_ole(b.get("datetime_raw")) or parse_waktu(b.get("datetime"))

        if dt is None or dt < awal or dt > akhir:
            continue

        if nama_ok and (_bersih(b.get("name")) or "").lower() not in nama_ok:
            continue

        status, kelas = peta_status(b.get("status"))
        pesan = _bersih(b.get("message"))

        hasil.append({
            "dt": dt,
            "waktu": waktu_id(dt, True),
            "sensor": _bersih(b.get("name")) or _bersih(b.get("parent")) or "-",
            "status": status,
            "kelas": kelas,
            "pesan": pesan or "-",
            "penjelasan": penjelasan_pesan(pesan),
        })

    hasil.sort(key=lambda x: x["dt"], reverse=True)

    total = len(hasil)
    hal_maks = max(1, (total + BARIS_PER_HALAMAN - 1) // BARIS_PER_HALAMAN)
    hal = max(1, min(hal, hal_maks))

    return {
        "rentang": rentang,
        "tgl1": awal.strftime("%Y-%m-%d"),
        "tgl2": akhir.strftime("%Y-%m-%d"),
        "awal_teks": tanggal_id(awal),
        "akhir_teks": tanggal_id(akhir),
        "baris": hasil[(hal - 1) * BARIS_PER_HALAMAN: hal * BARIS_PER_HALAMAN],
        "total": total,
        "hal": hal,
        "hal_maks": hal_maks,
    }


# ============================================================
# NAVIGASI TAB (dipakai juga oleh halaman Resume)
# ============================================================

def nav_html(sid, aktif, dari="", wil=""):
    """Deretan tab sekolah. Kosong kalau modul belum dipasang (supaya halaman Resume tidak error)."""

    if "tab_sekolah" not in current_app.view_functions:
        return Markup("")

    item = []

    for kode, label in TAB_DEF:
        if kode == "resume":
            href = url_for("detail_sekolah", id=sid, dari=dari or None, w=wil or None)
        else:
            href = url_for("tab_sekolah", tab=kode, id=sid, dari=dari or None, w=wil or None)

        kelas = ' class="on" aria-current="page"' if kode == aktif else ""
        item.append(f'<a href="{escape(href)}"{kelas}>{escape(label)}</a>')

    return Markup(
        '<div class="tabs-prtg-wrap">'
        + '<div class="tabs-prtg-head">'
        + '<span class="tabs-prtg-title">Pilih tampilan</span>'
        + '<span class="tabs-prtg-hint">Klik salah satu tab untuk melihat data sekolah</span>'
        + "</div>"
        + '<nav class="tabs-prtg" aria-label="Halaman data sekolah">'
        + "".join(item)
        + "</nav></div>"
    )


def nav_sensor_html(sid, sensor_id, aktif, dari="", wil=""):
    """Deretan tab sensor, diawali panah kembali ke Overview sekolah."""

    item = [
        '<a class="tab-balik" href="{}" title="Kembali ke Overview sekolah" '
        'aria-label="Kembali ke Overview sekolah">&larr;</a>'.format(
            escape(url_for("tab_sekolah", tab="overview", id=sid, dari=dari or None, w=wil or None))
        )
    ]

    for kode, label in SUB_SENSOR:
        href = url_for("sensor_sekolah", sensor_id=sensor_id, id=sid,
                       dari=dari or None, w=wil or None, t=kode)
        kelas = ' class="on" aria-current="page"' if kode == aktif else ""
        item.append(f'<a href="{escape(href)}"{kelas}>{escape(label)}</a>')

    return Markup(
        '<div class="tabs-prtg-wrap">'
        '<div class="tabs-prtg-head">'
        '<span class="tabs-prtg-title">Pilih tampilan sensor</span>'
        '<span class="tabs-prtg-hint">Tanda panah &larr; untuk kembali ke Overview sekolah</span>'
        "</div>"
        '<nav class="tabs-prtg" aria-label="Halaman data sensor">' + "".join(item) + "</nav></div>"
    )
# ============================================================
# TEMPLATE
# ============================================================

TOPBAR_CSS = '''
    /* ---------- HEADER ATAS: satu sumber gaya untuk SEMUA halaman ----------
       Ubah ukuran/warna header cukup di sini (tab_prtg.TOPBAR_CSS). */
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
    .topbar .pill-top {
        background: rgba(255,255,255,0.12); border-radius: 999px; padding: 7px 14px;
        font-size: 12.5px; font-weight: 600;
    }
    .topbar .pill-top .dot {
        width: 8px; height: 8px; border-radius: 50%; background: #3ddc84;
        display: inline-block; margin-right: 6px;
    }
    .topbar #countdown { color: #ffd966; font-weight: 700; }
    .topbar .tema-btn {
        background: rgba(255,255,255,0.12); color: #fff; border: none; cursor: pointer;
        border-radius: 999px; padding: 7px 14px; font-size: 12.5px; font-weight: 600;
        font-family: inherit;
    }
    .topbar .tema-btn:hover { background: rgba(255,255,255,0.22); }
    .topbar .logout {
        background: #fff; color: var(--navy, var(--ui-navy, #161a52)); padding: 7px 15px;
        border-radius: 999px; font-size: 12.5px; font-weight: 700; text-decoration: none;
    }
    @media (max-width: 600px) {
        .topbar { padding: 12px 14px; }
        .topbar .welcome { border-left: none; padding-left: 0; margin-left: 0; min-width: 100%; }
    }
'''

TOPBAR_ZOOM_CSS = '''
    @media screen and (min-width: 1024px) and (max-width: 2560px) {
        .topbar { zoom: 1.12; }
    }
'''

# CSS khusus halaman sensor: jarum ukur, formulir Historic Data
CSS_TAMBAHAN = '''
    .gauge-wrap{display:flex;flex-wrap:wrap;gap:24px;align-items:center}
    .gauge-kotak{flex:0 0 260px;max-width:100%}
    .gauge{width:100%;max-width:260px;height:auto;display:block;margin:0 auto}
    .gauge text{font-size:11.5px;fill:var(--ui-navy);font-family:var(--ui-font)}
    .hform{display:flex;flex-direction:column;gap:18px}
    .hgrid{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-end}
    .hgrid input[type=datetime-local]{min-height:50px;border:2px solid var(--ui-navy);border-radius:var(--ui-radius-control);padding:8px 12px;font:inherit;font-size:14.5px;background:var(--ui-surface);color:var(--ui-navy)}
    .hgrid select{max-width:240px}
    .kanal-pilih{display:flex;flex-wrap:wrap;gap:8px 20px}
    .kanal-pilih label{display:inline-flex;align-items:center;gap:8px;font-size:13.5px;font-weight:500;cursor:pointer}
    .kanal-pilih input{width:18px;height:18px;accent-color:var(--ui-navy)}
    .atas .kembali-grup{display:flex;flex-wrap:wrap;gap:6px}
    .atas .kembali{min-height:40px;padding:8px 16px;border:0;border-radius:var(--ui-pill);background:transparent;color:var(--ui-navy);font-weight:600;font-size:14px;text-decoration:none;transition:background-color .15s ease}
    .atas .kembali:hover{background:var(--ui-blue-bg);text-decoration:none}
    .atas .kembali.utama{background:var(--ui-blue-bg)}
    .atas .kembali.utama:hover{background:#dde5f7}
'''

CSS_DASAR = """
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

    /*TOPBAR_CSS*/

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

    /* ---------- satu panel besar: semua bagian digabung ---------- */
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
    .judul-bagian{margin:0 0 12px;color:var(--ui-navy);font-size:16.5px;font-weight:650}

    .meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
    .meta span{display:inline-flex;align-items:center;gap:5px;min-height:30px;background:var(--ui-blue-bg);color:var(--ui-text);border-radius:var(--ui-pill);padding:5px 12px;font-size:12.5px;font-weight:400}
    .meta span b{font-weight:600}
    .meta span.k-ok{background:var(--ui-green-bg);color:var(--ui-green);font-weight:600}
    .meta span.k-putus{background:var(--ui-red-bg);color:var(--ui-red);font-weight:600;animation:status-pulse 2.2s ease-in-out infinite}
    .meta span.k-sebagian{background:var(--ui-amber-bg);color:var(--ui-amber);font-weight:600}
    .meta span.k-netral{background:var(--ui-blue-bg);color:var(--ui-muted);font-weight:600}

    .galat{display:flex;align-items:center;gap:12px;background:var(--ui-red-bg);color:var(--ui-red);border-left:4px solid var(--ui-danger-edge);border-radius:var(--ui-radius-small);padding:12px 16px;font-size:13.5px;line-height:1.55}
    .galat::before{content:"!";display:inline-flex;align-items:center;justify-content:center;flex:0 0 22px;width:22px;height:22px;border:2px solid currentColor;border-radius:var(--ui-pill);font-size:13px;font-weight:700}

    /* ---------- deretan kotak angka: satu blok tersambung ---------- */
    .info,.angka,.mini{display:flex;flex-wrap:wrap;gap:14px;align-items:stretch}
    .kotak,.mini>div{flex:1 1 max(calc((100% - 14px * (var(--kol,4) - 1)) / var(--kol,4) - 1px),200px);min-width:0;min-height:0;background:var(--ui-surface-soft);border:1px solid var(--ui-border);border-radius:var(--ui-radius-control);padding:16px 20px;display:flex;flex-direction:column;gap:3px;transition:border-color .18s ease,box-shadow .18s ease}
    .kotak:hover,.mini>div:hover{border-color:#c7d0e6;box-shadow:0 2px 10px rgba(16,24,64,.07)}
    .kotak .lbl,.mini .l{font-size:12.5px;color:var(--ui-muted);font-weight:500}
    .kotak .asli{font-size:11.5px;color:var(--ui-subtle)}
    .kotak b,.mini b{font-size:19px;line-height:1.35;color:var(--ui-navy);font-weight:650;overflow-wrap:anywhere;font-variant-numeric:tabular-nums;margin-top:2px}
    .mini b{font-size:16px}
    .info .kotak:first-child b::before,.kotak b.merah::before{content:"";display:inline-block;width:8px;height:8px;margin:0 7px 2px 0;border-radius:var(--ui-pill);background:var(--ui-green)}
    .kotak b.merah{color:var(--ui-red)}
    .kotak b.merah::before{background:var(--ui-red)}
    .kotak .cat{font-size:12.5px;color:var(--ui-muted);line-height:1.5}
    .st-baik{color:var(--ui-green)!important;font-weight:600}
    .st-cukup{color:var(--ui-amber)!important;font-weight:600}
    .st-kurang{color:var(--ui-red)!important;font-weight:600}
    .st-none{color:var(--ui-muted)!important}

    /* ---------- tabel ---------- */
    .scroll,.tabel-scroll{overflow-x:auto}
    table{width:100%;border-collapse:collapse;min-width:640px}
    th{text-align:left;padding:10px 14px;font-size:12px;font-weight:600;color:var(--ui-muted);border-bottom:1px solid var(--ui-border);vertical-align:bottom;background:transparent}
    th small{display:block;font-weight:400;font-size:11px;color:var(--ui-subtle)}
    td{padding:12px 14px;font-size:13.5px;line-height:1.55;border-bottom:1px solid var(--ui-border-soft);vertical-align:top}
    tbody tr:last-child td{border-bottom:0}
    tr.rata td{background:var(--ui-surface-soft);font-weight:600}
    .baris-klik{cursor:pointer;transition:background-color .15s ease}
    .baris-klik:hover{background:var(--ui-blue-bg)}
    .tautan-sensor{color:var(--ui-navy);font-weight:600;text-decoration:none}
    .tautan-sensor:hover{text-decoration:underline;text-underline-offset:3px}
    .badge,.tk{display:inline-flex;align-items:center;min-height:24px;font-weight:600;font-size:12px;padding:2px 10px;border-radius:var(--ui-pill);white-space:nowrap;background:var(--ui-neutral-bg);color:var(--ui-muted)}
    .badge.putus{background:var(--ui-red-bg);color:var(--ui-red)}
    .badge.ok{background:var(--ui-green-bg);color:var(--ui-green)}
    .badge.sebagian{background:var(--ui-amber-bg);color:var(--ui-amber)}
    .badge.kosong,.badge.netral{background:var(--ui-neutral-bg);color:var(--ui-muted)}
    .tk-singkat{background:var(--ui-blue-bg);color:var(--ui-navy)}
    .tk-sedang{background:var(--ui-amber-bg);color:var(--ui-amber)}
    .tk-lama{background:var(--ui-red-bg);color:var(--ui-red)}
    .kosong{color:var(--ui-muted);font-style:italic}
    .asli2{display:block;font-size:11.5px;color:var(--ui-subtle);margin-top:3px}
    .bintang{color:var(--ui-muted-icon);font-size:14px;letter-spacing:1px}
    .bintang.on{color:var(--ui-priority)}

    /* ---------- kotak warna riwayat, legenda, grafik ---------- */
    .strip{display:grid;grid-template-columns:repeat(30,minmax(0,1fr));gap:3px}
    .strip div{height:30px;border-radius:6px}
    .sumbu{display:flex;justify-content:space-between;gap:8px;font-size:11.5px;color:var(--ui-muted)}
    .legenda{display:flex;flex-wrap:wrap;gap:16px;font-size:12.5px;color:var(--ui-muted)}
    .legenda span{display:inline-flex;align-items:center;gap:6px}
    .legenda i{width:12px;height:12px;border-radius:var(--ui-radius-indicator);display:inline-block}
    .grafik{width:100%;height:auto;display:block}
    .grafik text{font-size:11.5px;fill:var(--ui-muted);font-family:var(--ui-font)}

    .aman{display:flex;flex-wrap:wrap;align-items:center;gap:16px;background:var(--ui-green-bg);border:1px solid var(--ui-safe-border);border-radius:var(--ui-radius-control);padding:18px 20px}
    .aman .ikon{width:48px;height:48px;border-radius:var(--ui-pill);background:var(--ui-green);display:flex;align-items:center;justify-content:center;flex-shrink:0}
    .aman .teks{display:flex;flex-direction:column;gap:3px}
    .aman .teks b{font-size:17px;font-weight:650;color:var(--ui-green)}
    .masalah{display:flex;flex-wrap:wrap;align-items:center;gap:16px}
    .masalah .besar{font-size:30px;font-weight:700;color:var(--ui-red);line-height:1.1}
    .kosongbox{border:1.5px dashed var(--ui-border);border-radius:var(--ui-radius-control);background:var(--ui-empty-bg);padding:26px 20px;display:flex;flex-direction:column;align-items:center;gap:4px;text-align:center;font-size:13.5px}

.pilih{display:flex;flex-wrap:wrap;align-items:center;gap:10px}
    .pilih label{font-size:13px;font-weight:600;color:var(--ui-navy)}
    .pilih select{min-height:38px;min-width:280px;max-width:100%;border:1px solid var(--ui-border);border-radius:var(--ui-radius-small);padding:6px 12px;font:inherit;font-size:13.5px;background:var(--ui-surface);color:var(--ui-text);cursor:pointer}
    .area-pantau{margin-bottom:20px}
    .pantau-kepala{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;margin:6px 0 12px}
    .pantau-judul{font-size:15px;font-weight:700;color:var(--ui-navy)}
    .pantau-hitung{margin-left:8px;font-size:12.5px;font-weight:600;color:var(--ui-muted);background:var(--ui-blue-bg);border-radius:var(--ui-pill);padding:3px 10px;white-space:nowrap}
    .tombol-ubah{display:inline-flex;align-items:center;gap:8px;min-height:40px;padding:8px 18px;border:2px solid var(--ui-navy);border-radius:var(--ui-pill);background:var(--ui-surface);color:var(--ui-navy);font:inherit;font-size:14px;font-weight:700;cursor:pointer;white-space:nowrap}
    .tombol-ubah:hover,.tombol-ubah[aria-expanded=true]{background:var(--ui-navy);color:var(--ui-surface)}
    .tombol-ubah:focus-visible{outline:3px solid var(--ui-focus);outline-offset:3px}
    .tombol-ubah .panah{font-size:12px;transition:transform .18s ease}
    .tombol-ubah[aria-expanded=true] .panah{transform:rotate(180deg)}
    .pantau{display:flex;flex-direction:column;gap:8px}
    .pantau-item{display:flex;flex-wrap:wrap;align-items:center;gap:12px;border:1px solid var(--ui-border);border-radius:var(--ui-radius-control);background:var(--ui-surface-soft);padding:12px 16px;cursor:pointer;transition:border-color .18s ease,background-color .18s ease}
    .pantau-item:hover{border-color:var(--ui-navy);background:var(--ui-blue-bg)}
    .pantau-item .nm{flex:1 1 240px;font-size:15px;font-weight:700}
    .pantau-item .nl{color:var(--ui-muted);font-size:13px}
    .tautan-pantau{color:var(--ui-navy);text-decoration:underline;text-underline-offset:3px;text-decoration-thickness:2px}
    .tautan-pantau:hover{text-decoration-thickness:3px}
    .panel-pilih{margin-top:14px;border:1px solid var(--ui-border);border-radius:var(--ui-radius-control);background:var(--ui-surface);overflow:hidden}
    .panel-pilih[hidden]{display:none}
    .panel-bar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;background:var(--ui-surface-soft);border-bottom:1px solid var(--ui-border)}
    .panel-bar .info{flex:1 1 220px;font-size:13.5px;font-weight:600;color:var(--ui-navy)}
    .panel-bar .aksi{display:flex;gap:10px;align-items:center}
    .btn-simpan{min-height:40px;border:2px solid var(--ui-navy);border-radius:var(--ui-pill);padding:8px 22px;background:var(--ui-navy);color:var(--ui-surface);font:inherit;font-size:14px;font-weight:700;cursor:pointer}
    .btn-simpan:hover{filter:brightness(1.2)}
    .btn-simpan:disabled{opacity:.6;cursor:wait}
    .btn-simpan:focus-visible,.btn-tutup:focus-visible{outline:3px solid var(--ui-focus);outline-offset:3px}
    .btn-tutup{min-height:40px;border:2px solid var(--ui-border);border-radius:var(--ui-pill);padding:8px 18px;background:var(--ui-surface);color:var(--ui-navy);font:inherit;font-size:14px;font-weight:600;cursor:pointer}
    .btn-tutup:hover{border-color:var(--ui-navy)}
    .pilih-daftar{display:flex;flex-direction:column;max-height:380px;overflow:auto;padding:6px}
    .pilih-daftar label{display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:var(--ui-radius-small);cursor:pointer;font-size:14px}
    .pilih-daftar label:hover{background:var(--ui-blue-bg)}
    .pilih-daftar label:has(input:checked){background:var(--ui-blue-bg);font-weight:700}
    .pilih-daftar input[type=checkbox]{width:20px;height:20px;flex-shrink:0;accent-color:var(--ui-navy);cursor:pointer}
    .pilih-daftar input:disabled+span{opacity:.45}
    .pilih-sebaris{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:14px}
    .pilih-sebaris .pilih-kotak{flex:1 1 380px}
    .nama-sensor{display:flex;align-items:center;min-height:50px;font-size:15px;font-weight:700;color:var(--ui-navy)}
    .pilih-kotak{display:flex;flex-direction:column;gap:8px;align-items:flex-start}
    .pilih-kotak label{font-size:14.5px;font-weight:700;color:var(--ui-navy)}
    .pilih-kotak select{appearance:none;-webkit-appearance:none;width:min(100%,560px);min-height:50px;padding:10px 46px 10px 16px;border:2px solid var(--ui-navy);border-radius:var(--ui-radius-control);background-color:var(--ui-surface);background-image:linear-gradient(45deg,transparent 50%,var(--ui-navy) 50%),linear-gradient(135deg,var(--ui-navy) 50%,transparent 50%);background-position:calc(100% - 24px) 50%,calc(100% - 17px) 50%;background-size:7px 7px,7px 7px;background-repeat:no-repeat;color:var(--ui-navy);font:inherit;font-size:15px;font-weight:600;cursor:pointer;box-shadow:0 2px 8px rgba(22,26,82,.10);transition:box-shadow .18s ease,background-color .18s ease}
    .pilih-kotak select:hover{background-color:var(--ui-blue-bg);box-shadow:0 3px 12px rgba(22,26,82,.18)}
    .pilih-kotak select:focus-visible{outline:3px solid var(--ui-focus);outline-offset:3px}
    .pilih select:focus-visible,.daftar summary:focus-visible{outline:3px solid var(--ui-focus);outline-offset:3px}
    .daftar summary{cursor:pointer;font-weight:600;font-size:13.5px;color:var(--ui-navy);padding:6px 0;list-style:none}
    .daftar summary::-webkit-details-marker{display:none}
    .daftar summary::before{content:"\\25B8  "}
    .daftar[open] summary::before{content:"\\25BE  "}
    .daftar[open] summary{margin-bottom:10px}

    /* ---------- halaman, filter ---------- */
    .hal{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;font-size:12.5px;color:var(--ui-muted)}
    .hal a,.hal span.mati{display:inline-flex;align-items:center;justify-content:center;min-height:34px;padding:6px 12px;border-radius:var(--ui-radius-small);background:var(--ui-blue-bg);color:var(--ui-navy);font-weight:600;font-size:12.5px;text-decoration:none;margin-left:6px}
    .hal span.mati{opacity:.55}
    .filter{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
    .chip{display:inline-flex;align-items:center;justify-content:center;min-height:34px;padding:6px 14px;border-radius:var(--ui-pill);border:1px solid var(--ui-border);background:var(--ui-surface);font-size:12.5px;font-weight:600;color:var(--ui-muted);text-decoration:none}
    .chip:hover{border-color:var(--ui-navy);color:var(--ui-navy)}
    .chip.on{background:var(--ui-navy);border-color:var(--ui-navy);color:var(--ui-surface)}
    .filter form{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:12.5px;color:var(--ui-muted)}
    .filter input[type=date]{min-height:34px;border:1px solid var(--ui-border);border-radius:var(--ui-radius-small);padding:5px 10px;font:inherit;font-size:12.5px;background:var(--ui-surface);color:var(--ui-text)}
    .filter button{min-height:34px;border:0;border-radius:var(--ui-radius-small);padding:6px 16px;background:var(--ui-navy);color:var(--ui-surface);font:inherit;font-size:12.5px;font-weight:600;cursor:pointer}

    /* ---------- tab ---------- */
    .tabs-prtg-wrap{background:var(--ui-surface);border:1px solid var(--ui-border);border-radius:var(--ui-radius-card);padding:14px 28px 16px;box-shadow:var(--ui-shadow)}
    .tabs-prtg-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 10px;margin:0 0 8px}
    .tabs-prtg-title{font-size:12.5px;font-weight:600;color:var(--ui-navy)}
    .tabs-prtg-hint{font-size:12px;color:var(--ui-subtle)}
    .tabs-prtg{display:flex;gap:4px;flex-wrap:wrap;background:var(--ui-surface-soft);border:1px solid var(--ui-border-soft);border-radius:var(--ui-pill);padding:4px}
    .tabs-prtg a{display:inline-flex;align-items:center;justify-content:center;min-width:72px;min-height:36px;padding:6px 16px;border:1px solid transparent;border-radius:var(--ui-pill);font-size:13.5px;font-weight:600;color:var(--ui-muted);text-decoration:none;white-space:nowrap;transition:background-color .18s ease,color .18s ease,border-color .18s ease}
    .tabs-prtg a:hover{background:var(--ui-surface);border-color:var(--ui-border);color:var(--ui-navy)}
    .tabs-prtg a.on{background:var(--ui-navy);border-color:var(--ui-navy);color:var(--ui-surface);box-shadow:var(--ui-shadow-selected)}
    .tabs-prtg a:focus-visible,.kembali:focus-visible,.btn-unduh:focus-visible,.chip:focus-visible,.hal a:focus-visible,.filter button:focus-visible,.tautan-sensor:focus-visible{outline:3px solid var(--ui-focus);outline-offset:3px}

    @keyframes status-pulse{0%,100%{box-shadow:var(--ui-shadow-pulse-start)}50%{box-shadow:var(--ui-shadow-pulse-end)}}
    @media(max-width:700px){
        .wrap{--ui-section-gap:16px;width:100%;max-width:100%;padding:18px 14px 40px}
        .wrap.resume-wrap{padding:12px 14px 40px}
        .panel>.card,.panel>.kepala{padding:20px 20px}
        .panel>.tabs-prtg-wrap{padding:12px 20px 14px}
        .tabs-prtg{flex-wrap:nowrap;overflow-x:auto;overscroll-behavior-x:contain;scrollbar-width:none;-webkit-overflow-scrolling:touch}
        .tabs-prtg::-webkit-scrollbar{display:none}
        .tabs-prtg a{flex:0 0 auto}
    }
    @media(max-width:420px){
        .wrap{width:100%;padding:14px 12px 30px}
        .wrap.resume-wrap{padding:10px 12px 30px}
        .panel>.card,.panel>.kepala{padding:18px 16px}
        .panel>.tabs-prtg-wrap{padding:12px 16px 14px}
        .atas{align-items:stretch}
        .unduh{width:100%}
        .btn-unduh{flex:1;padding-right:10px;padding-left:10px;text-align:center}
        .meta span{max-width:100%;overflow-wrap:anywhere}
    }
    @media(prefers-reduced-motion:reduce){
        *,*::before,*::after{scroll-behavior:auto!important;animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}
    }
"""


TAB_TEMPLATE = """
{% macro form_pilih(asal, buka_awal) %}
<div class="panel-pilih" id="panel-pilih"{% if not (request.args.get('buka') or buka_awal) %} hidden{% endif %}>
    <form method="post" action="{{ url_for('pilih_sensor_sekolah') }}" id="form-pantau" data-maks="{{ maks_pilih }}">
        <input type="hidden" name="id" value="{{ sid }}">
        {% if asal %}<input type="hidden" name="sensor_asal" value="{{ asal }}"><input type="hidden" name="t" value="{{ sub }}">{% endif %}
        {% if dari %}<input type="hidden" name="dari" value="{{ dari }}">{% endif %}{% if wil %}<input type="hidden" name="w" value="{{ wil }}">{% endif %}
        <div class="panel-bar">
            <span class="info" id="info-pantau" role="status" aria-live="polite">Centang 1 atau 2 pemeriksaan, lalu klik Simpan pilihan.</span>
            <span class="aksi">
                <button type="button" class="btn-tutup" data-tutup-pilih>Tutup</button>
                <button type="submit" class="btn-simpan">Simpan pilihan</button>
            </span>
        </div>
        <div class="pilih-daftar">
            {% for s in sensor_baris %}
            <label><input type="checkbox" name="sensor" value="{{ s.id }}"{% if s.id|string in terpilih_id %} checked{% endif %}><span>{{ s.sensor }} <span class="badge {{ s.kelas }}">{{ s.status }}</span></span></label>
            {% endfor %}
        </div>
    </form>
</div>
{% endmacro %}
{% macro skrip_pilih() %}
<script>
(function(){
    function pulihkan(){
        try{
            var v=sessionStorage.getItem("pantauScroll"); if(!v) return;
            var o=JSON.parse(v);
            if(location.pathname.indexOf("/sekolah/sensor/")>=0 && o.k==="sensor"){ window.scrollTo(0,o.y); }
        }catch(e){}
    }
    function bersihkan(){ try{ sessionStorage.removeItem("pantauScroll"); }catch(e){} }
    pulihkan();
    window.addEventListener("DOMContentLoaded",pulihkan);
    window.addEventListener("load",function(){ pulihkan(); bersihkan(); });
    if(window.__pantauInit) return;
    window.pindahHalaman=function(url){
        if(!url) return;
        try{ sessionStorage.setItem("pantauScroll",JSON.stringify({k:"sensor",y:window.scrollY})); }catch(e){}
        location.href=url;
    };
    window.__pantauInit=true;
    function el(i){return document.getElementById(i);}
    function hitung(pesan){
        var f=el("form-pantau"); if(!f) return;
        var m=parseInt(f.getAttribute("data-maks"),10)||2;
        var c=f.querySelectorAll("input[type=checkbox]"), n=0;
        c.forEach(function(x){ if(x.checked) n++; });
        c.forEach(function(x){ x.disabled=(n>=m && !x.checked); });
        var i=el("info-pantau");
        if(i) i.textContent=(pesan?pesan+" ":"")+"Dipilih "+n+" dari maksimal "+m+"."+(n>=m?" Hapus centang dulu untuk mengganti.":"");
    }
    function atur(buka){
        var p=el("panel-pilih"); if(!p) return;
        p.hidden=!buka;
        document.querySelectorAll("[data-toggle-pilih]").forEach(function(b){ b.setAttribute("aria-expanded",buka?"true":"false"); });
        if(buka) hitung();
    }
    document.addEventListener("click",function(e){
        if(e.target.closest("[data-toggle-pilih]")){ var p=el("panel-pilih"); if(p) atur(p.hidden); return; }
        if(e.target.closest("[data-tutup-pilih]")){ atur(false); return; }
        var ch=e.target.closest("a.chip");
        if(ch && location.pathname.indexOf("/sekolah/sensor/")>=0 && ch.getAttribute("href")){ e.preventDefault(); window.pindahHalaman(ch.href); return; }
        var k=e.target.closest(".pantau-item");
        if(k && !e.target.closest("a")){ var a=k.querySelector("a"); if(a) location.href=a.href; }
    });
    document.addEventListener("change",function(e){ if(e.target.closest("#form-pantau")) hitung(); });
    document.addEventListener("submit",function(e){
        var f=e.target;
        if(!f || !f.matches || !f.matches("#form-pantau") || !window.fetch || !window.FormData) return;
        e.preventDefault();
        var tb=f.querySelector(".btn-simpan"); tb.disabled=true; tb.textContent="Menyimpan...";
        var buka=!el("panel-pilih").hidden;
        fetch(f.action,{method:"POST",body:new FormData(f),credentials:"same-origin"}).then(function(r){
            if(!r.ok) throw new Error("gagal");
            return r.text().then(function(h){ return {url:r.url,html:h}; });
        }).then(function(x){
            var d=new DOMParser().parseFromString(x.html,"text/html");
            var baru=d.getElementById("area-pantau"), lama=el("area-pantau");
            if(!baru || !lama || new URL(x.url).pathname!==location.pathname){ window.pindahHalaman(x.url); return; }
            lama.innerHTML=baru.innerHTML;
            atur(buka);
            hitung("Pilihan tersimpan.");
        }).catch(function(){ f.submit(); });
    });
    hitung();
})();
</script>
{% endmacro %}
{% macro tabel_data(t, judul, mundur, lanjut) %}
<div class="card">
    <div>
        <h2>{{ judul }}</h2>
        <div class="ket">Terbaru di atas, sama seperti tabel DATA di PRTG.</div>
    </div>
    <div class="scroll">
    <table>
        <thead><tr>
            <th>Waktu<small>Date Time</small></th>
            {% if t.baris and t.baris[0].badge %}<th>Kondisi</th>{% endif %}
            {% for k in t.kolom %}<th>{{ k.label }}<small>{{ k.asli }}</small></th>{% endfor %}
        </tr></thead>
        <tbody>
            <tr class="rata"><td>Rata-rata (dari {{ t.total }} data)</td>{% if t.baris and t.baris[0].badge %}<td></td>{% endif %}{% for r in t.rata %}<td>{{ r }}</td>{% endfor %}</tr>
            {% for b in t.baris %}
            <tr>
                <td title="{{ b.asli }}">{{ b.waktu }}</td>
                {% if b.badge %}<td><span class="badge {{ b.badge.kelas }}">{{ b.badge.teks }}</span></td>{% endif %}
                {% for c in b.sel %}<td>{{ c }}</td>{% endfor %}
            </tr>
            {% endfor %}
        </tbody>
    </table>
    </div>
    <div class="hal">
        <span>Menampilkan {{ t.dari }} sampai {{ t.sampai }} dari {{ t.total }} data</span>
        <span>
            {% if mundur %}<a href="{{ mundur }}">&larr; Sebelumnya</a>{% else %}<span class="mati">&larr; Sebelumnya</span>{% endif %}
            {% if lanjut %}<a href="{{ lanjut }}">Berikutnya &rarr;</a>{% else %}<span class="mati">Berikutnya &rarr;</span>{% endif %}
        </span>
    </div>
</div>
{% endmacro %}
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ judul_tab }} - {{ nama }}</title>
<link rel="icon" type="image/svg+xml" href="{{ url_for('favicon') }}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<style>
/*CSS_DASAR*/
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
        <small>Monitoring gangguan sekolah &middot; PRTG {{ prtg_server }}</small>
    </div>
</div>

<div class="wrap resume-wrap">

    {% for g in galat %}<div class="galat">{{ g }}</div>{% endfor %}

    <main class="panel">
        <div class="atas">
        <div class="kembali-grup">
            <a class="kembali" href="{{ kembali_url }}">&larr; {{ kembali_teks }}</a>
            {% if daftar_url %}<a class="kembali utama" href="{{ daftar_url }}">&larr; {{ daftar_teks }}</a>{% endif %}
        </div>
    </div>
    
    {% set ke_online = (dari == 'online') or (not dari and dev.online and not dev.down) %}
    <div class="card kepala">
        <div class="sub">{{ judul_tab_panjang }}</div>
        <div class="kepala-judul-wrap">
            <h1>{{ nama }}</h1>
            {% if ke_online %}
            <span class="badge-kategori-asal asal-online" title="Kategori Asal: Sekolah Online">
                <span class="bka-dot"></span> Sekolah Online
            </span>
            {% else %}
            <span class="badge-kategori-asal asal-offline" title="Kategori Asal: Sekolah Offline">
                <span class="bka-dot"></span> Sekolah Offline
            </span>
            {% endif %}
        </div>
        <div class="meta">
            <span class="badge-kategori-chip {{ 'k-ok' if ke_online else 'k-putus' }}">
                <b>Kategori Asal:</b> {{ 'Sekolah Online' if ke_online else 'Sekolah Offline' }}
            </span>
            <span class="k-{{ dev.kelas }}">&#9679; {{ dev.status }}</span>
            {% if dev.wilayah %}<span><b>Kabupaten/Kota:</b> {{ dev.wilayah }}</span>{% endif %}
            <span><b>Nomor ID PRTG:</b> #{{ dev.prtg_id }}</span>
            <span><b>Laporan dibuat:</b> {{ dibuat }}</span>
        </div>
    </div>

    {{ nav }}

    {# ===================== OVERVIEW SEKOLAH ===================== #}
    {% if tab == 'overview' %}

    <div class="card">
        <h2>Informasi perangkat</h2>
        {% if info %}
        {% set n = info|length %}{% set kol = 3 if n in (3,5,6,9) else (4 if n > 4 else (n if n else 1)) %}<div class="info" style="--kol:{{ kol }}">
            {% for k in info %}
            <div class="kotak">
                <span class="lbl">{{ k.label }}</span><span class="asli">{{ k.asli }}</span>
                <b class="{{ k.kelas }}">{{ k.nilai }}</b>
                <span class="cat">{{ k.cat }}</span>
            </div>
            {% endfor %}
        </div>
        {% else %}
        <div class="kosongbox"><b>Informasi perangkat tidak tersedia</b><span class="ket">PRTG tidak mengembalikan data perangkat ini.</span></div>
        {% endif %}
    </div>

    <div class="card">
        <div>
            <h2>Pemeriksaan (sensor)</h2>
            <div class="ket">Pemeriksaan yang dipilih ditampilkan di sini dan di tab Log.</div>
        </div>
        {% if sensor_baris %}
        <div class="area-pantau" id="area-pantau">
            <div class="pantau-kepala">
                <div class="pantau-judul">Pemeriksaan yang dipantau <span class="pantau-hitung">{{ terpilih|length }} dari maks. {{ maks_pilih }}</span></div>
                <button type="button" class="tombol-ubah" data-toggle-pilih aria-controls="panel-pilih" aria-expanded="{{ 'true' if (request.args.get('buka') or not terpilih) else 'false' }}">{% if terpilih %}Ubah pilihan{% else %}Pilih pemeriksaan{% endif %} <span class="panah" aria-hidden="true">&#9662;</span></button>
            </div>
            <div class="pantau">
                {% for s in terpilih %}
                <div class="pantau-item">
                    <a class="nm tautan-pantau" href="{{ url_for('sensor_sekolah', sensor_id=s.id, id=sid, dari=dari or None, w=wil or None) }}">{{ s.sensor }}</a>
                    <span class="badge {{ s.kelas }}">{{ s.status }}</span>
                    <span class="nl">Nilai terakhir: {{ s.nilai }}</span>
                </div>
                {% else %}
                <div class="kosongbox"><b>Belum ada pemeriksaan yang dipilih</b><span class="ket">Centang 1 atau 2 pemeriksaan pada daftar di bawah, lalu klik "Simpan pilihan".</span></div>
                {% endfor %}
            </div>
            {{ form_pilih(None, not terpilih) }}
        </div>
        {{ skrip_pilih() }}
        {% else %}
        <div class="kosongbox"><b>Tidak ada sensor yang terbaca</b><span class="ket">PRTG tidak mengembalikan daftar sensor perangkat ini.</span></div>
        {% endif %}
    </div>

    {# ===================== ALARMS ===================== #}
    {% elif tab == 'alarms' %}

    <div class="card">
        <h2>Gangguan yang sedang berlangsung</h2>
        {% if alarm_baris %}
        <div class="masalah">
            {% if terlama and terlama.lama != '-' %}<div class="besar">{{ terlama.lama }}</div>{% endif %}
            <div style="display:flex;flex-direction:column;gap:4px">
                <b style="font-size:17px">{% if terlama and terlama.lama != '-' %}Sudah bermasalah selama {{ terlama.lama }}{% else %}Ada sensor yang bermasalah{% endif %}</b>
                <span class="ket">{{ alarm_baris|length }} pemeriksaan (sensor) bermasalah saat ini.</span>
            </div>
        </div>
        {% elif sensor_ada %}
        <div class="aman">
            <div class="ikon"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"></path></svg></div>
            <div class="teks"><b>Tidak ada gangguan saat ini</b><span class="ket">Semua pemeriksaan (sensor) di sekolah ini normal menurut PRTG.</span></div>
        </div>
        {% else %}
        <div class="kosongbox"><b>Data sensor tidak tersedia</b><span class="ket">PRTG tidak mengembalikan daftar sensor perangkat ini.</span></div>
        {% endif %}
    </div>

    <div class="card">
        <h2>Rincian sensor yang bermasalah</h2>
        <div class="scroll">
        <table>
            <thead><tr>
                <th>Sudah bermasalah selama<small>Down for</small></th><th>Perangkat<small>Device</small></th>
                <th>Pemeriksaan<small>Sensor</small></th><th>Nilai terakhir<small>Last Value</small></th>
                <th>Status<small>Status</small></th><th>Penyebab<small>Message</small></th><th>Prioritas<small>Priority</small></th>
            </tr></thead>
            <tbody>
            {% for s in alarm_baris %}
            <tr>
                <td><b style="color:#a31621">{{ s.lama }}</b></td>
                <td>{{ s.device or nama }}</td>
                <td>{{ s.sensor }}</td>
                <td>{{ s.nilai }}</td>
                <td><span class="badge {{ s.kelas }}">{{ s.status }}</span></td>
                <td>{% if s.penjelasan %}{{ s.penjelasan }}<span class="asli2">Pesan asli PRTG: {{ s.pesan }}</span>{% else %}{{ s.pesan }}{% endif %}</td>
                <td>{% if s.bintang %}<span role="img" aria-label="Prioritas {{ s.bintang }} dari 5">{% for i in range(5) %}<span class="bintang {{ 'on' if i < s.bintang }}">&#9733;</span>{% endfor %}</span>{% else %}-{% endif %}</td>
            </tr>
            {% else %}
            <tr><td colspan="7" style="text-align:center;color:#5a6180;font-style:italic;padding:22px 12px">Tidak ada sensor dengan alarm</td></tr>
            {% endfor %}
            </tbody>
        </table>
        </div>
    </div>


    {# ===================== HALAMAN SENSOR ===================== #}
    {% elif tab == 'sensor' %}

    <div class="card">
        <div class="area-pantau" id="area-pantau">
            <div class="pilih-sebaris">
                <div class="pilih-kotak">
                    <label for="ganti-sensor">Pemeriksaan yang ditampilkan</label>
                    {% if daftar_sensor|length > 1 %}
                    <select id="ganti-sensor" onchange="pindahHalaman(this.value)">
                        {% for d in daftar_sensor %}<option value="{{ url_for('sensor_sekolah', sensor_id=d.id, id=sid, dari=dari or None, t=sub, w=wil or None) }}"{% if d.id|string == sensor_id|string %} selected{% endif %}>{{ d.nama }}</option>{% endfor %}
                    </select>
                    {% else %}
                    <div class="nama-sensor">{{ judul_tab_panjang }}</div>
                    {% endif %}
                </div>
                <button type="button" class="tombol-ubah" data-toggle-pilih aria-controls="panel-pilih" aria-expanded="{{ 'true' if request.args.get('buka') else 'false' }}">Ubah pilihan <span class="panah" aria-hidden="true">&#9662;</span></button>
            </div>
            {{ form_pilih(sensor_id, false) }}
        </div>
        {{ skrip_pilih() }}
    </div>

    {# ---------- SENSOR: OVERVIEW ---------- #}
    {% if sub == 'overview' %}

    <div class="card">
        <div>
            <h2>Pesan terakhir</h2>
            <div class="asli2" style="margin-top:0">Last Message</div>
        </div>
        <div class="masalah">
            <span class="badge {{ ov.pesan.kelas }}">{{ ov.pesan.status }}</span>
            <div style="display:flex;flex-direction:column;gap:4px">
                <b style="font-size:16px">{{ ov.pesan.penjelasan or ov.pesan.teks }}</b>
                {% if ov.pesan.penjelasan %}<span class="asli2" style="margin-top:0">Pesan asli PRTG: {{ ov.pesan.teks }}</span>{% endif %}
            </div>
        </div>
    </div>

    <div class="card">
        <h2>Data ringkas</h2>
        {% set n = ov.ringkas|length %}{% set kol = 3 if n in (3,5,6,9) else (4 if n > 4 else (n if n else 1)) %}
        <div class="angka" style="--kol:{{ kol }}">
            {% for k in ov.ringkas %}
            <div class="kotak">
                <span class="lbl">{{ k.label }}</span><span class="asli">{{ k.asli }}</span>
                <b>{{ k.nilai }}</b>
                {% if k.cat %}<span class="cat">{{ k.cat }}</span>{% endif %}
            </div>
            {% endfor %}
        </div>
    </div>

    {% if ov.gauge or ov.stat %}
    <div class="card">
        <h2>Nilai terkini</h2>
        <div class="gauge-wrap">
            {% if ov.gauge %}
            <div class="gauge-kotak">{{ ov.gauge }}<div class="ket" style="text-align:center">{{ ov.gauge_label }}</div></div>
            {% endif %}
            {% if ov.stat %}
            <div class="angka" style="--kol:{{ 3 if ov.stat|length >= 3 else ov.stat|length }};flex:1 1 360px">
                {% for k in ov.stat %}
                <div class="kotak">
                    <span class="lbl">{{ k.label }}</span><span class="asli">{{ k.asli }}</span>
                    <b class="{{ k.kelas }}">{{ k.nilai }}</b>
                </div>
                {% endfor %}
            </div>
            {% endif %}
        </div>
    </div>
    {% endif %}

    {% if ov.kanal %}
    <div class="card">
        <div>
            <h2>Kanal (channel)</h2>
            <div class="ket">Satu pemeriksaan bisa mengukur beberapa hal sekaligus. Ini nilai terakhir tiap kanal.</div>
        </div>
        <div class="scroll">
        <table>
            <thead><tr><th>Kanal<small>Channel</small></th><th>Nilai terakhir<small>Last Value</small></th></tr></thead>
            <tbody>
            {% for c in ov.kanal %}
            <tr><td>{{ c.label }}<span class="asli2">{{ c.nama }}</span></td><td>{{ c.nilai }}</td></tr>
            {% endfor %}
            </tbody>
        </table>
        </div>
    </div>
    {% endif %}

    {% for g in ov.grafik %}
    <div class="card">
        <div>
            <h2>Grafik {{ g.label|lower }}, {{ g.judul }}</h2>
            <div class="asli2" style="margin-top:0">{{ g.asli }}</div>
            <div class="ket">{% if g.unit %}Satuan: {{ g.unit }}. {% endif %}Area merah muda adalah saat sekolah putus.</div>
        </div>
        {% if g.svg %}{{ g.svg }}
        {% else %}
        <div class="kosongbox"><b>Tidak ada data grafik</b><span class="ket">{% if g.ada_rows %}PRTG tidak menyimpan angka untuk periode ini.{% else %}PRTG tidak mengembalikan data riwayat untuk periode ini.{% endif %}</span></div>
        {% endif %}
    </div>
    {% endfor %}

    {# ---------- SENSOR: HISTORIC DATA ---------- #}
    {% elif sub == 'historic' %}

    <div class="card">
        <div>
            <h2>Data historis</h2>
            <div class="asli2" style="margin-top:0">Historic Data</div>
            <div class="ket">Pilih tanggal, rata-rata per berapa menit, dan kanal yang ingin dilihat.</div>
        </div>
        <form class="hform" method="get" action="{{ url_for('sensor_sekolah', sensor_id=sensor_id) }}">
            <input type="hidden" name="id" value="{{ sid }}">
            {% if dari %}<input type="hidden" name="dari" value="{{ dari }}">{% endif %}{% if wil %}<input type="hidden" name="w" value="{{ wil }}">{% endif %}
            <input type="hidden" name="t" value="historic">
            <div class="hgrid">
                <div class="pilih-kotak">
                    <label for="h-rentang">Rentang cepat <span class="asli2" style="display:inline">Quick range</span></label>
                    <select id="h-rentang" name="rentang">
                        {% for v, t in h.rentang_opsi %}<option value="{{ v }}"{% if h.rentang == v %} selected{% endif %}>{{ t }}</option>{% endfor %}
                    </select>
                </div>
                <div class="pilih-kotak">
                    <label for="h-tgl1">Dari <span class="asli2" style="display:inline">Start</span></label>
                    <input type="datetime-local" id="h-tgl1" name="tgl1" value="{{ h.tgl1 }}">
                </div>
                <div class="pilih-kotak">
                    <label for="h-tgl2">Sampai <span class="asli2" style="display:inline">End</span></label>
                    <input type="datetime-local" id="h-tgl2" name="tgl2" value="{{ h.tgl2 }}">
                </div>
                <div class="pilih-kotak">
                    <label for="h-avg">Rata-rata tiap <span class="asli2" style="display:inline">Average interval</span></label>
                    <select id="h-avg" name="avg">
                        {% for v, t in h.avg_opsi %}<option value="{{ v }}"{% if h.avg == v %} selected{% endif %}>{{ t }}</option>{% endfor %}
                    </select>
                </div>
                <div class="pilih-kotak">
                    <label for="h-format">Format hasil <span class="asli2" style="display:inline">Output</span></label>
                    <select id="h-format" name="format">
                        <option value="tabel"{% if h.format == 'tabel' %} selected{% endif %}>Tampil di halaman (tabel)</option>
                        <option value="csv"{% if h.format == 'csv' %} selected{% endif %}>Unduh CSV (Excel)</option>
                    </select>
                </div>
            </div>
            {% if h.kanal_opsi %}
            <div class="pilih-kotak">
                <label>Kanal yang ditampilkan <span class="asli2" style="display:inline">Channels</span></label>
                <div class="kanal-pilih">
                    {% for k in h.kanal_opsi %}<label><input type="checkbox" name="kanal" value="{{ k.key }}"{% if k.on %} checked{% endif %}>{{ k.label }}</label>{% endfor %}
                </div>
            </div>
            {% endif %}
            <div><button type="submit" class="btn-simpan">Tampilkan</button></div>
        </form>
        <script>
        (function(){
            var s=document.getElementById("h-rentang"),a=document.getElementById("h-tgl1"),b=document.getElementById("h-tgl2");
            if(!s||!a||!b) return;
            function p(n){return n<10?"0"+n:n}
            function f(d){return d.getFullYear()+"-"+p(d.getMonth()+1)+"-"+p(d.getDate())+"T"+p(d.getHours())+":"+p(d.getMinutes())}
            s.addEventListener("change",function(){
                var n=new Date(),t0=new Date(n.getFullYear(),n.getMonth(),n.getDate()),x,y=n,v=s.value;
                if(v==="khusus") return;
                if(v==="hari_ini") x=t0;
                else if(v==="kemarin"){ x=new Date(t0.getTime()-864e5); y=t0; }
                else x=new Date(n.getTime()-parseInt(v,10)*864e5);
                a.value=f(x); b.value=f(y);
            });
            a.addEventListener("input",function(){ s.value="khusus"; });
            b.addEventListener("input",function(){ s.value="khusus"; });
        })();
        </script>
        <div class="ket">Menampilkan {{ h.awal_teks }} sampai {{ h.akhir_teks }}, rata-rata tiap {{ h.avg_label }}.</div>
    </div>

    {% if h.tabel %}
    {{ tabel_data(h.tabel, 'Hasil data historis', h.tabel.url_mundur, h.tabel.url_lanjut) }}
    {% else %}
    <div class="card">
        <div class="kosongbox"><b>Tidak ada data untuk ditampilkan</b><span class="ket">PRTG tidak mengembalikan data pada rentang ini, atau ada pesan galat di atas.</span></div>
    </div>
    {% endif %}

    {# ---------- SENSOR: LOG ---------- #}
    {% elif sub == 'log' %}

    <div class="card">
        <h2>Riwayat kejadian sensor ini</h2>
        <div class="ket">Catatan kapan pemeriksaan ini berubah status. Terbaru di atas.</div>
        <div class="filter">
            {% for r, t in (('7', '7 hari'), ('30', '30 hari'), ('365', '365 hari')) %}
            <a class="chip {{ 'on' if log.rentang == r }}" href="{{ url_for('sensor_sekolah', sensor_id=sensor_id, id=sid, dari=dari or None, w=wil or None, t='log', rentang=r) }}">{{ t }}</a>
            {% endfor %}
            <form method="get" action="{{ url_for('sensor_sekolah', sensor_id=sensor_id) }}">
                <input type="hidden" name="id" value="{{ sid }}">
                {% if dari %}<input type="hidden" name="dari" value="{{ dari }}">{% endif %}{% if wil %}<input type="hidden" name="w" value="{{ wil }}">{% endif %}
                <input type="hidden" name="t" value="log">
                <input type="hidden" name="rentang" value="khusus">
                <label>Dari <input type="date" name="tgl1" value="{{ log.tgl1 }}"></label>
                <label>sampai <input type="date" name="tgl2" value="{{ log.tgl2 }}"></label>
                <button type="submit">Terapkan</button>
            </form>
        </div>
        <div class="ket"><b>{{ log.total }} kejadian</b> pada rentang {{ log.awal_teks }} sampai {{ log.akhir_teks }}</div>
    </div>

    <div class="card">
        <div class="scroll">
        <table>
            <thead><tr>
                <th>Waktu<small>Date Time</small></th>
                <th>Status<small>Status</small></th><th>Artinya<small>Message</small></th>
            </tr></thead>
            <tbody>
            {% for b in log.baris %}
            <tr>
                <td>{{ b.waktu }}</td>
                <td><span class="badge {{ b.kelas }}">{{ b.status }}</span></td>
                <td>{% if b.penjelasan %}{{ b.penjelasan }}<span class="asli2">Pesan asli PRTG: {{ b.pesan }}</span>{% else %}{{ b.pesan }}{% endif %}</td>
            </tr>
            {% else %}
            <tr><td colspan="3" style="text-align:center;color:#5a6180;font-style:italic;padding:22px 12px">Tidak ada kejadian pada rentang ini</td></tr>
            {% endfor %}
            </tbody>
        </table>
        </div>
        {% if log.total > 0 %}
        <div class="hal">
            <span>Halaman {{ log.hal }} dari {{ log.hal_maks }}</span>
            <span>
                {% if log.hal > 1 %}<a href="{{ url_for('sensor_sekolah', sensor_id=sensor_id, id=sid, dari=dari or None, w=wil or None, t='log', rentang=log.rentang, tgl1=log.tgl1, tgl2=log.tgl2, hal=log.hal - 1) }}">&larr; Sebelumnya</a>{% else %}<span class="mati">&larr; Sebelumnya</span>{% endif %}
                {% if log.hal < log.hal_maks %}<a href="{{ url_for('sensor_sekolah', sensor_id=sensor_id, id=sid, dari=dari or None, w=wil or None, t='log', rentang=log.rentang, tgl1=log.tgl1, tgl2=log.tgl2, hal=log.hal + 1) }}">Berikutnya &rarr;</a>{% else %}<span class="mati">Berikutnya &rarr;</span>{% endif %}
            </span>
        </div>
        {% endif %}
    </div>

    {# ---------- SENSOR: LIVE DATA / 2 HARI / 30 HARI / 365 HARI ---------- #}
    {% else %}

    {% set judul_waktu = {'live':'menit demi menit','2hari':'jam demi jam','30hari':'hari demi hari','365hari':'minggu demi minggu'}[sub] %}

    <div class="card">
        <h2>{{ per.p.judul }}</h2>
        <p class="ringkasan">{{ per.ringkas_kalimat }}</p>
    </div>

    {% if per.kartu %}
    <div class="card">
        <div>
            <h2>Rata-rata {{ per.p.nama }}</h2>
            <div class="ket">Dihitung dari {{ per.jumlah_data }} data (satu data tiap {{ per.p.per_data }}), sama seperti baris "Averages" di PRTG.</div>
        </div>
        {% set n = per.kartu|length %}{% set kol = 3 if n in (3,5,6,9) else (4 if n > 4 else (n if n else 1)) %}<div class="angka" style="--kol:{{ kol }}">
            {% for k in per.kartu %}
            <div class="kotak"><span class="lbl">{{ k.label }}</span><span class="asli">{{ k.asli }}</span><b>{{ k.nilai }}</b></div>
            {% endfor %}
        </div>
    </div>
    {% endif %}

    <div class="card">
        <div>
            <h2>Kondisi koneksi, {{ judul_waktu }}</h2>
            <div class="ket">Setiap kotak adalah {{ per.p.per_kotak }}. Dari kiri (terlama) sampai kanan (terbaru). Arahkan kursor ke kotak untuk melihat waktunya.</div>
        </div>
        {% if per.strip and per.strip.ada_data %}
        <div class="strip" style="grid-template-columns:repeat({{ per.strip.n }},minmax(0,1fr))">
            {% for k in per.strip.kotak %}<div title="{{ k.tip }}" style="background:{{ k.warna }}"></div>{% endfor %}
        </div>
        <div class="sumbu"><span>{{ per.strip.kiri }}</span><span>{{ per.strip.tengah }}</span><span>{{ per.strip.kanan }}</span></div>
        <div class="legenda">{{ legenda|safe }}</div>
        {% else %}
        <div class="kosongbox"><b>Data riwayat tidak tersedia</b><span class="ket">PRTG tidak mengembalikan data untuk periode ini.</span></div>
        {% endif %}
    </div>

    {% for g in per.grafik %}
    <div class="card">
        <div>
            <h2>{{ g.label }}</h2>
            <div class="ket">{% if g.unit %}Satuan: {{ g.unit }}. {% endif %}Area merah muda adalah saat sekolah putus, jadi garisnya terputus.</div>
        </div>
        {% if g.svg %}{{ g.svg }}
        {% else %}
        <div class="kosongbox"><b>Tidak ada data {{ g.label|lower }}</b><span class="ket">{% if g.ada_rows %}PRTG tidak menyimpan angka untuk sensor ini pada periode ini.{% else %}PRTG tidak mengembalikan data riwayat sensor ini.{% endif %}</span></div>
        {% endif %}
    </div>
    {% endfor %}

    {% for t in per.tabel %}
    {{ tabel_data(
        t,
        'Rincian tiap ' ~ per.p.per_data,
        url_for('sensor_sekolah', sensor_id=sensor_id, id=sid, dari=dari or None, w=wil or None, t=sub, hal=t.hal - 1) if t.hal > 1 else None,
        url_for('sensor_sekolah', sensor_id=sensor_id, id=sid, dari=dari or None, w=wil or None, t=sub, hal=t.hal + 1) if t.hal < t.hal_maks else None
    ) }}
    {% endfor %}

    {% endif %}


    {# ===================== LOG SEKOLAH ===================== #}
    {% elif tab == 'log' %}

    <div class="card">
        <h2>Riwayat kejadian</h2>
        <div class="ket">Catatan kapan pemeriksaan sekolah berubah status. Terbaru di atas.</div>
        <div class="filter">
            {% for r, t in (('7', '7 hari'), ('30', '30 hari'), ('365', '365 hari')) %}
            <a class="chip {{ 'on' if log.rentang == r }}" href="{{ url_for('tab_sekolah', tab='log', id=sid, dari=dari or None, w=wil or None, rentang=r) }}">{{ t }}</a>
            {% endfor %}
            <form method="get" action="{{ url_for('tab_sekolah', tab='log') }}">
                <input type="hidden" name="id" value="{{ sid }}">
                {% if dari %}<input type="hidden" name="dari" value="{{ dari }}">{% endif %}{% if wil %}<input type="hidden" name="w" value="{{ wil }}">{% endif %}
                <input type="hidden" name="rentang" value="khusus">
                <label>Dari <input type="date" name="tgl1" value="{{ log.tgl1 }}"></label>
                <label>sampai <input type="date" name="tgl2" value="{{ log.tgl2 }}"></label>
                <button type="submit">Terapkan</button>
            </form>
        </div>
        <div class="ket"><b>{{ log.total }} kejadian</b> pada rentang {{ log.awal_teks }} sampai {{ log.akhir_teks }}</div>
        {% if log_nama %}<div class="ket">Hanya menampilkan pemeriksaan yang dipilih: <b>{{ log_nama|join(', ') }}</b>. Ubah pilihan di tab Overview.</div>{% else %}<div class="ket">Belum ada pemeriksaan yang dipilih di tab Overview, jadi semua pemeriksaan ditampilkan.</div>{% endif %}
    </div>

    <div class="card">
        <div class="scroll">
        <table>
            <thead><tr>
                <th>Waktu<small>Date Time</small></th><th>Pemeriksaan<small>Sensor</small></th>
                <th>Status<small>Status</small></th><th>Artinya<small>Message</small></th>
            </tr></thead>
            <tbody>
            {% for b in log.baris %}
            <tr>
                <td>{{ b.waktu }}</td>
                <td>{{ b.sensor }}</td>
                <td><span class="badge {{ b.kelas }}">{{ b.status }}</span></td>
                <td>{% if b.penjelasan %}{{ b.penjelasan }}<span class="asli2">Pesan asli PRTG: {{ b.pesan }}</span>{% else %}{{ b.pesan }}{% endif %}</td>
            </tr>
            {% else %}
            <tr><td colspan="4" style="text-align:center;color:#5a6180;font-style:italic;padding:22px 12px">Tidak ada kejadian pada rentang ini</td></tr>
            {% endfor %}
            </tbody>
        </table>
        </div>
        {% if log.total > 0 %}
        <div class="hal">
            <span>Halaman {{ log.hal }} dari {{ log.hal_maks }}</span>
            <span>
                {% if log.hal > 1 %}<a href="{{ url_for('tab_sekolah', tab='log', id=sid, dari=dari or None, w=wil or None, rentang=log.rentang, tgl1=log.tgl1, tgl2=log.tgl2, hal=log.hal - 1) }}">&larr; Sebelumnya</a>{% else %}<span class="mati">&larr; Sebelumnya</span>{% endif %}
                {% if log.hal < log.hal_maks %}<a href="{{ url_for('tab_sekolah', tab='log', id=sid, dari=dari or None, w=wil or None, rentang=log.rentang, tgl1=log.tgl1, tgl2=log.tgl2, hal=log.hal + 1) }}">Berikutnya &rarr;</a>{% else %}<span class="mati">Berikutnya &rarr;</span>{% endif %}
            </span>
        </div>
        {% endif %}
    </div>

    {% endif %}

    </main>

</div>
</body>
</html>
"""

# ============================================================
# HALAMAN RESUME (tampilan setelah klik nama sekolah)
# Ubah tampilan Resume di sini. Datanya dari bangun_resume()
# di dashboard_sekolah.py.
# ============================================================

RESUME_TEMPLATE = """
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Resume {{ nama }}</title>
<link rel="icon" type="image/svg+xml" href="{{ url_for('favicon') }}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<style>
{{ shared_css | safe }}
/*CSS_DASAR*/
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
        <small>Monitoring gangguan sekolah &middot; PRTG {{ prtg_server }}</small>
    </div>
</div>

<div class="wrap resume-wrap">

    {% set dari = request.args.get('dari', '') %}
    {% set ke_online = (dari == 'online') or (not dari and sedang_online and not sedang_down) %}

    {% if galat %}<div class="galat">{{ galat }}</div>{% endif %}

    <main class="panel">
    <div class="atas">
        {% if ke_online %}
        <a class="kembali" href="{{ url_for('dashboard_online', w=(wil_asal or wilayah)) }}">&larr; Kembali ke daftar sekolah online</a>
        {% else %}
        <a class="kembali" href="{{ url_for('index', w=(wil_asal or wilayah)) }}">&larr; Kembali ke daftar gangguan</a>
        {% endif %}
        <div class="unduh">
            <a class="btn-unduh pdf" href="{{ url_for('unduh_resume', jenis='pdf', id=sid) }}">Unduh PDF (untuk dicetak)</a>
            <a class="btn-unduh xls" href="{{ url_for('unduh_resume', jenis='xlsx', id=sid) }}">Unduh Excel</a>
        </div>
    </div>

    <div class="card kepala">
        <div class="sub">Resume kondisi sekolah</div>
        <div class="kepala-judul-wrap">
            <h1>{{ nama }}</h1>
            {% if ke_online %}
            <span class="badge-kategori-asal asal-online" title="Kategori Asal: Sekolah Online">
                <span class="bka-dot"></span> Sekolah Online
            </span>
            {% else %}
            <span class="badge-kategori-asal asal-offline" title="Kategori Asal: Sekolah Offline">
                <span class="bka-dot"></span> Sekolah Offline
            </span>
            {% endif %}
        </div>
        <div class="meta">
            <span class="badge-kategori-chip {{ 'k-ok' if ke_online else 'k-putus' }}">
                <b>Kategori Asal:</b> {{ 'Sekolah Online' if ke_online else 'Sekolah Offline' }}
            </span>
            <span title="Sumber wilayah: {{ wilayah_sumber }}"><b>Kabupaten/Kota:</b> {{ wilayah_tampil }}</span>
            <span><b>Laporan dibuat:</b> {{ dibuat_awam }}</span>
        </div>
    </div>

    {{ tab_nav|safe }}

    <div class="card">

        <div class="angka">
            <div class="kotak">
                <span class="lbl">{{ kartu.label_mulai }}</span>
                <b>{{ kartu.mulai }}</b>
                <span class="cat">Waktu ini perkiraan dari data PRTG</span>
            </div>
            <div class="kotak">
                <span class="lbl">{{ kartu.label_lama }}</span>
                <b>{{ kartu.lama }}</b>
                <span class="cat">{{ kartu.ket_lama }}</span>
            </div>
            <div class="kotak">
                <span class="lbl">Berapa kali putus dalam 30 hari</span>
                <b>{{ stat.n30 }} kali</b>
                <span class="cat">Total waktu putus: {{ stat.jam30 }}</span>
            </div>
            <div class="kotak">
                <span class="lbl">Seberapa stabil (30 hari)</span>
                <b>{% if stat.stabil == '-' %}-{% else %}{{ stat.stabil }} menyala{% endif %}</b>
                <span class="cat st-{{ stat.stabil_kelas }}">{{ stat.stabil_label }}</span>
            </div>
        </div>
    </div>

    <div class="card">
        <div>
            <h2>Riwayat 30 hari terakhir</h2>
            <div class="ket">Setiap kotak adalah satu hari. Dari kiri (30 hari lalu) sampai kanan (hari ini).</div>
        </div>

        <div class="strip">
            {% for h in hari %}<div title="{{ h.tip }}" style="background: {{ h.warna }}"></div>{% endfor %}
        </div>

        <div class="legenda">
            <span><i style="background: #2e9d57"></i>Normal seharian</span>
            <span><i style="background: #e8a600"></i>Putus sebentar (kurang dari 1 jam)</span>
            <span><i style="background: #d64545"></i>Putus cukup lama</span>
            {% if ada_belum %}<span><i style="background: #c9cfe0"></i>Belum dicatat</span>{% endif %}
        </div>

        <div class="mini">
            <div><span class="l">7 hari terakhir</span><b>{{ stat.n7 }} kali putus</b><span class="l">total {{ stat.jam7 }}</span></div>
            <div><span class="l">Putus paling lama</span><b>{{ stat.terpanjang }}</b><span class="l">dalam 30 hari</span></div>
            <div><span class="l">Rata-rata sekali putus</span><b>{{ stat.rata }}</b><span class="l">dalam 30 hari</span></div>
            <div><span class="l">Terakhir pulih</span><b>{{ stat.pulih }}</b><span class="l">internet kembali menyala</span></div>
        </div>
    </div>

    <div class="card">
        <h2>Daftar kejadian putus</h2>
        {% if kejadian %}
        <div class="tabel-scroll">
        <table>
            <thead>
                <tr><th>Mulai putus</th><th>Menyala lagi</th><th>Berapa lama</th><th>Tingkat</th></tr>
            </thead>
            <tbody>
                {% for k in kejadian %}
                <tr>
                    <td>{{ k.mulai }}</td>
                    <td{% if k.masih %} style="font-weight:700;color:#a31621"{% else %} style="font-weight:600"{% endif %}>{{ k.selesai }}</td>
                    <td>{{ k.durasi }}</td>
                    <td><span class="tk tk-{{ k.kelas }}">{{ k.tingkat }}</span></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        </div>
        {% else %}
        <div class="kosong">Tidak ada kejadian putus tercatat dalam 30 hari terakhir.</div>
        {% endif %}
    </div>

    </main>

</div>
</body>
</html>
"""


CSS_DASAR = CSS_DASAR.replace("/*TOPBAR_CSS*/", TOPBAR_CSS + TOPBAR_ZOOM_CSS + CSS_TAMBAHAN)
TAB_TEMPLATE = TAB_TEMPLATE.replace("/*CSS_DASAR*/", CSS_DASAR)
RESUME_TEMPLATE = RESUME_TEMPLATE.replace("/*CSS_DASAR*/", CSS_DASAR)


def halaman_resume():

    sid = request.args.get("id", "").strip()

    c = _K["bangun_resume"](sid)

    if c is None:
        return "Sekolah tidak ditemukan atau belum punya riwayat.", 404

    return render_template_string(
        RESUME_TEMPLATE,
        shared_css=_K["shared_css"],
        prtg_server=_K["server"],
        tab_nav=nav_html(sid, "resume", request.args.get("dari", ""), request.args.get("w", "").strip()),
        wil_asal=request.args.get("w", "").strip(),
        **c,
    )


LEGENDA = (
    f'<span><i style="background:{WARNA["ok"]}"></i>Tersambung</span>'
    f'<span><i style="background:{WARNA["sebagian"]}"></i>Putus sebentar (kurang dari 1 jam)</span>'
    f'<span><i style="background:{WARNA["putus"]}"></i>Putus cukup lama (1 jam atau lebih)</span>'
    f'<span><i style="background:{WARNA["kosong"]}"></i>Tidak ada data</span>'
)

JUDUL_TAB = {
    "overview": ("Overview", "Ringkasan perangkat sekolah"),
    "alarms": ("Alarms", "Sensor yang sedang bermasalah"),
    "log": ("Log", "Riwayat kejadian"),
}


# ============================================================
# RUTE
# ============================================================

def _kembali(dev, dari, wil=""):
    """Tujuan tombol 'Kembali' ke daftar dashboard."""

    ke_online = (dari == "online") or (not dari and dev["online"] and not dev["down"])

    if ke_online:
        ke_wil = wil or dev["wilayah"] or None
        return url_for("dashboard_online", w=ke_wil), "Kembali ke daftar sekolah online"

    ke_wil = wil or dev["wilayah"] or None
    return url_for("index", w=ke_wil), "Kembali ke daftar gangguan"


def halaman_tab(tab):

    if tab not in JUDUL_TAB:
        return "Halaman tidak dikenal.", 404

    sid = request.args.get("id", "").strip()
    dari = request.args.get("dari", "").strip()
    wil = request.args.get("w", "").strip()
    hal = _int(request.args.get("hal"), 1)

    dev = cari_device(sid)

    if not dev["prtg_id"]:
        return ("Sekolah tidak ditemukan di PRTG. Buka lewat daftar dashboard supaya ID-nya terbaca.", 404)

    did = dev["prtg_id"]
    galat = []
    ctx = {}

    sensors = []

    if tab in ("overview", "alarms", "log"):
        try:
            sensors = sensor_perangkat(did)
        except Exception as e:
            galat.append(f"Daftar sensor gagal diambil dari PRTG ({e}).")

    if tab == "overview":
        ctx["info"] = bangun_info(did, galat)
        ctx["sensor_baris"] = [_baris_sensor(s, i) for i, s in enumerate(sensors, 1)]
        _pil = baca_pilihan(did)
        ctx["terpilih_id"] = _pil
        ctx["maks_pilih"] = MAKS_PILIH
        _bs = {str(b["id"]): b for b in ctx["sensor_baris"]}
        ctx["terpilih"] = [_bs[i] for i in _pil if i in _bs]

    elif tab == "alarms":
        baris = [_baris_sensor(s, i) for i, s in enumerate(sensors, 1)]
        alarm = [b for b in baris if b["alarm"]]
        alarm.sort(key=lambda b: -b["jam"])
        ctx["alarm_baris"] = alarm
        ctx["terlama"] = alarm[0] if alarm else None
        ctx["sensor_ada"] = bool(baris)

    elif tab == "log":
        _pil = baca_pilihan(did)
        _dipilih = [s for s in sensors if str(s.get("objid")) in _pil]
        ctx["log_nama"] = [_bersih(s.get("sensor")) for s in _dipilih]
        ctx["log_nama_ok"] = {n.lower() for n in ctx["log_nama"]} or None
        ctx["log"] = bangun_log(
            did,
            request.args.get("rentang", "7"),
            request.args.get("tgl1", ""),
            request.args.get("tgl2", ""),
            hal,
            galat,
            nama_ok=ctx["log_nama_ok"],
        )

    kembali_url, kembali_teks = _kembali(dev, dari, wil)
    judul_tab, judul_panjang = JUDUL_TAB[tab]

    return render_template_string(
        TAB_TEMPLATE,
        prtg_server=_K["server"],
        tab=tab,
        sid=sid,
        dari=dari,
        wil=wil,
        dev=dev,
        nama=dev["nama"],
        judul_tab=judul_tab,
        judul_tab_panjang=judul_panjang,
        kembali_url=kembali_url,
        kembali_teks=kembali_teks,
        galat=galat,
        dibuat=waktu_id(datetime.now()),
        nav=nav_html(sid, tab, dari, wil),
        legenda=LEGENDA,
        **ctx,
    )


def halaman_sensor(sensor_id):
    """Halaman satu sensor dengan tab ala PRTG:
    Overview, Live Data, 2 Hari, 30 Hari, 365 Hari, Historic Data, Log."""

    sid = request.args.get("id", "").strip()
    dari = request.args.get("dari", "").strip()
    wil = request.args.get("w", "").strip()
    hal = _int(request.args.get("hal"), 1)

    # t = tab; p = parameter lama (2hari/30hari/365hari) tetap dikenali
    sub = request.args.get("t") or request.args.get("p") or "overview"

    if sub not in dict(SUB_SENSOR):
        sub = "overview"

    dev = cari_device(sid)

    if not dev["prtg_id"]:
        return ("Sekolah tidak ditemukan di PRTG. Buka lewat daftar dashboard supaya ID-nya terbaca.", 404)

    did = dev["prtg_id"]

    try:
        semua = sensor_perangkat(did)
    except Exception as e:
        return (f"Daftar sensor gagal diambil dari PRTG ({e}).", 502)

    # hanya sensor milik sekolah ini yang boleh dibuka
    sn = next((s for s in semua if str(s.get("objid")) == str(sensor_id)), None)

    if sn is None:
        return "Sensor tidak ditemukan di sekolah ini.", 404

    nama_sensor = _bersih(sn.get("sensor")) or f"#{sensor_id}"
    _pil = baca_pilihan(did)
    galat, ctx = [], {}

    if sub == "overview":
        ctx["ov"] = bangun_overview_sensor(sensor_id, sn, galat)

    elif sub in PERIODE:
        per = bangun_periode(did, [sn], sub, hal, pakai_device=False)
        per["ringkas_kalimat"] = kalimat_ringkas(
            nama_sensor, per["p"], per["persen"], per["strip"], per["jumlah_data"]
        )
        galat += per["galat"]
        ctx["per"] = per

    elif sub == "historic":
        url_dasar = dict(sensor_id=sensor_id, id=sid, dari=dari or None, w=wil or None, t="historic")
        h = bangun_historic(sensor_id, nama_sensor, request.args, hal, galat, url_dasar)

        if h.get("csv") is not None:
            return h["csv"]

        ctx["h"] = h

    elif sub == "log":
        ctx["log"] = bangun_log(
            sensor_id,
            request.args.get("rentang", "7"),
            request.args.get("tgl1", ""),
            request.args.get("tgl2", ""),
            hal,
            galat,
        )

    judul_sub = dict(SUB_SENSOR)[sub]
    kembali_url, kembali_teks = _kembali(dev, dari, wil)

    return render_template_string(
        TAB_TEMPLATE,
        prtg_server=_K["server"],
        tab="sensor",
        sub=sub,
        sid=sid,
        dari=dari,
        wil=wil,
        dev=dev,
        nama=dev["nama"],
        judul_tab=f"{judul_sub} - {nama_sensor}",
        judul_tab_panjang=f"Sensor: {nama_sensor}",
        kembali_url=kembali_url,
        kembali_teks=kembali_teks,
        galat=galat,
        dibuat=waktu_id(datetime.now()),
        nav=nav_sensor_html(sid, sensor_id, sub, dari, wil),
        legenda=LEGENDA,
        sensor_id=sensor_id,
        sensor_baris=[_baris_sensor(x, i) for i, x in enumerate(semua, 1)],
        terpilih_id=_pil,
        maks_pilih=MAKS_PILIH,
        daftar_sensor=[
            {"id": x.get("objid"), "nama": _bersih(x.get("sensor")) or f"#{x.get('objid')}"}
            for x in semua
            if (not _pil) or str(x.get("objid")) in _pil or str(x.get("objid")) == str(sensor_id)
        ],
        **ctx,
    )


# ============================================================
# SENSOR YANG DIPILIH PER SEKOLAH (disimpan di sensor_terpilih.json)
# ============================================================

MAKS_PILIH = 2
FILE_PILIHAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensor_terpilih.json")
_kunci_pilihan = threading.Lock()


def _baca_semua_pilihan():

    try:
        with open(FILE_PILIHAN, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def baca_pilihan(did):
    """ID sensor yang dipilih untuk satu sekolah (ID perangkat PRTG). Maksimal MAKS_PILIH."""

    with _kunci_pilihan:
        v = _baca_semua_pilihan().get(str(did), [])

    return [str(x) for x in v][:MAKS_PILIH] if isinstance(v, list) else []


def simpan_pilihan(did, ids):

    bersih = []

    for x in ids:
        x = str(x).strip()
        if x.isdigit() and x not in bersih:
            bersih.append(x)

    bersih = bersih[:MAKS_PILIH]

    with _kunci_pilihan:
        d = _baca_semua_pilihan()

        if bersih:
            d[str(did)] = bersih
        else:
            d.pop(str(did), None)

        tmp = FILE_PILIHAN + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

        os.replace(tmp, FILE_PILIHAN)

    return bersih


def simpan_sensor_pilihan():
    """POST dari form di tab Overview: simpan sensor yang dicentang (maksimal 2)."""

    sid = request.form.get("id", "").strip()
    dari = request.form.get("dari", "").strip()
    wil = request.form.get("w", "").strip()

    dev = cari_device(sid)

    if not dev["prtg_id"]:
        return "Sekolah tidak ditemukan di PRTG.", 404

    try:
        sah = {str(s.get("objid")) for s in sensor_perangkat(dev["prtg_id"])}
    except Exception as e:
        return f"Daftar sensor gagal diambil dari PRTG ({e}). Pilihan belum disimpan.", 502

    ids = [i for i in request.form.getlist("sensor") if i in sah]
    simpan = simpan_pilihan(dev["prtg_id"], ids)

    # Disimpan dari halaman sensor: tetap di halaman sensor (yang masih dipilih,
    # atau sensor pertama yang dipilih). Kalau tidak ada yang dipilih -> Overview.
    asal = request.form.get("sensor_asal", "").strip()
    kode = request.form.get("t", "").strip() or None

    if asal and simpan:
        tujuan = asal if asal in simpan else simpan[0]
        return redirect(url_for("sensor_sekolah", sensor_id=tujuan, id=sid, dari=dari or None, w=wil or None, t=kode, buka=1, _anchor="area-pantau"))

    return redirect(url_for("tab_sekolah", tab="overview", id=sid, dari=dari or None, w=wil or None, buka=1, _anchor="area-pantau"))


def pasang(app, *, login_required, ambil_down, ambil_online, bangun_resume, shared_css,
           prtg_server, prtg_auth, prtg_timeout=30, verify_ssl=True):
    """
    Daftarkan rute /sekolah/tab/<tab> dan /sekolah/sensor/<sensor_id> ke aplikasi Flask.

    ambil_down()    -> daftar sekolah down   (isi: id, nama, wilayah, prtg_id)
    ambil_online()  -> daftar sekolah online (isi: id, nama, wilayah, prtg_id)
    prtg_auth()     -> dict login PRTG (username + passhash)
    """

    _K.update(
        ambil_down=ambil_down,
        ambil_online=ambil_online,
        bangun_resume=bangun_resume,
        shared_css=shared_css,
        server=prtg_server,
        auth=prtg_auth,
        timeout=prtg_timeout,
        verify=verify_ssl,
    )

    app.add_url_rule("/sekolah", "detail_sekolah", login_required(halaman_resume))
    app.add_url_rule("/sekolah/tab/<tab>", "tab_sekolah", login_required(halaman_tab))
    app.add_url_rule("/sekolah/sensor/<sensor_id>", "sensor_sekolah", login_required(halaman_sensor))
    app.add_url_rule("/sekolah/pilih-sensor", "pilih_sensor_sekolah",
                     login_required(simpan_sensor_pilihan), methods=["POST"])