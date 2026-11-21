"""
Bot Monitoring Gangguan PRTG -> WhatsApp GreenAPI  (versi sekolah)

Versi gabungan:
- Kolom "group" ikut diambil dari PRTG (dibutuhkan dashboard untuk
  membagi wilayah per kabupaten).
- Login PRTG mendukung PASSHASH atau PASSWORD.
- Kirim WhatsApp otomatis sesuai JADWAL_WA, dan tidak mengirim
  pesan yang sama berulang (state notifikasi).

Sumber data:
- Semua data DOWN diambil langsung dari PRTG API.
- Lama down dihitung dari downtimesince (durasi yang dilaporkan PRTG),
  BUKAN dari lastdown.
- Jika satu device punya beberapa sensor DOWN, semua dihitung dan
  kategori memakai downtime TERLAMA.
- Status DOWN: 5 (Down), 13 (Down Acknowledged), 14 (Down Partial).

Kategori: < 24 jam, 24 - 48 jam, > 48 jam

Identitas device (urutan prioritas):
1. Kode LMxxxxxx / JPxxxxxx / XMxxxxxx (jika ada di nama device)
2. ID device PRTG (kolom parentid), contoh #17020
3. Alamat IP di nama device
4. Nama lengkap device
5. OBJID sensor
"""

import re
import json
import sys
import os
import time
import builtins
from datetime import datetime, timedelta

import requests
import urllib3

from app.config import sanitize_text, settings


# ============================================================
# LOGGING KE FILE
# ============================================================

LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "bot_log.txt"
)

_original_print = builtins.print


def _safe_console_text(text):
    """Hilangkan karakter yang tidak bisa ditampilkan di terminal Windows."""
    try:
        encoding = sys.stdout.encoding or "utf-8"
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    except Exception:
        return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def log_print(*args, **kwargs):
    text = sanitize_text(" ".join(str(a) for a in args))

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    line = f"[{timestamp}] {text}"

    # Tetap tampil di terminal, meski terminal Windows tidak
    # mendukung semua Unicode (misalnya emoji).
    try:
        _original_print(line, **kwargs)
    except UnicodeEncodeError:
        _original_print(_safe_console_text(line), **kwargs)

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        _original_print(f"[{timestamp}] GAGAL menulis ke log file:", e)


print = log_print


# ============================================================
# KONFIGURASI PRTG (dimuat dari .env melalui app.config.settings)
# ============================================================

PRTG_SERVER = settings.prtg_server

PRTG_USERNAME = settings.prtg_username

# Isi SALAH SATU di .env: PRTG_PASSHASH (disarankan) atau PRTG_PASSWORD.
PRTG_PASSHASH = settings.prtg_passhash
PRTG_PASSWORD = settings.prtg_password


# ============================================================
# KONFIGURASI GREENAPI (dimuat dari .env melalui app.config.settings)
# ============================================================

GREENAPI_API_URL = settings.greenapi_api_url
GREENAPI_ID_INSTANCE = settings.greenapi_id_instance
GREENAPI_TOKEN = settings.greenapi_token

# Nomor pribadi (628xxx) atau ID grup (xxxxx@g.us)
WA_TARGET = settings.wa_target

# Jadwal pengiriman otomatis WhatsApp (jam:menit, 24 jam)
JADWAL_WA = list(settings.jadwal_wa)

# Interval untuk mode monitoring terus-menerus (run_forever).
CHECK_INTERVAL = settings.check_interval


# ============================================================
# STATE NOTIFIKASI (hindari kirim pesan yang sama berulang)
# ============================================================

NOTIFIKASI_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "notifikasi_prtg_state.json"
)


def baca_state_notifikasi():
    try:
        with open(NOTIFIKASI_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return {}


def simpan_state_notifikasi(state):
    try:
        with open(NOTIFIKASI_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print("Gagal menyimpan state notifikasi:", e)


def signature_groups(groups):
    return {
        "< 24 jam": sorted(groups.get("< 24 jam", [])),
        "24 - 48 jam": sorted(groups.get("24 - 48 jam", [])),
        "> 48 jam": sorted(groups.get("> 48 jam", [])),
    }


# PRTG menggunakan sertifikat self-signed (diatur melalui PRTG_VERIFY_SSL di .env).
VERIFY_SSL = settings.prtg_verify_ssl


# ============================================================
# KONFIGURASI REQUEST
# ============================================================

PRTG_TIMEOUT = settings.prtg_timeout

# Gunakan angka besar agar daftar sensor DOWN tidak terpotong.
PRTG_COUNT = settings.prtg_count

# 5 = Down, 13 = Down (Acknowledged), 14 = Down (Partial)
PRTG_DOWN_STATUS = [5, 13, 14]


if not VERIFY_SSL:
    urllib3.disable_warnings(
        urllib3.exceptions.InsecureRequestWarning
    )


# ============================================================
# AUTENTIKASI & VALIDASI KONFIGURASI
# ============================================================

def _terisi(nilai):
    """True kalau nilai konfigurasi sudah diisi (bukan kosong/placeholder)."""

    teks = str(nilai or "").strip()

    return bool(teks) and not teks.startswith("ISI_")


def get_prtg_auth_params():
    """Parameter autentikasi PRTG: passhash kalau ada, kalau tidak password."""

    params = {"username": str(PRTG_USERNAME).strip()}

    if _terisi(PRTG_PASSHASH):
        params["passhash"] = str(PRTG_PASSHASH).strip()
    elif _terisi(PRTG_PASSWORD):
        params["password"] = str(PRTG_PASSWORD).strip()

    return params


def validate_config():
    """Mengecek konfigurasi dasar sebelum bot dijalankan."""

    errors = []

    if not _terisi(PRTG_SERVER):
        errors.append("PRTG_SERVER belum diisi.")

    if not _terisi(PRTG_USERNAME):
        errors.append("PRTG_USERNAME belum diisi.")

    if not _terisi(PRTG_PASSHASH) and not _terisi(PRTG_PASSWORD):
        errors.append("PRTG_PASSHASH atau PRTG_PASSWORD belum diisi.")

    if not _terisi(GREENAPI_API_URL):
        errors.append("GREENAPI_API_URL belum diisi.")

    if not _terisi(GREENAPI_ID_INSTANCE):
        errors.append("GREENAPI_ID_INSTANCE belum diisi.")

    if not _terisi(GREENAPI_TOKEN):
        errors.append("GREENAPI_TOKEN belum diisi.")

    if not _terisi(WA_TARGET):
        errors.append("WA_TARGET belum diisi.")

    if errors:
        print("=" * 60)
        print("KONFIGURASI BELUM LENGKAP")
        print("=" * 60)

        for error in errors:
            print("-", error)

        print("=" * 60)

        sys.exit(1)


# ============================================================
# DIAGNOSA: kenapa hasilnya 0?  ->  python prtg_sekolah.py cek
# ============================================================

_PREFIX_SEKOLAH = ["SMPN", "SMAN", "SMKN", "SDN", "MTSN", "MAN",
                   "SMP", "SMA", "SMK", "SD", "MTS", "MI", "MA"]


def _mirip_sekolah(nama):

    n = re.sub(r"^\s*\d+\.\s*", "", str(nama or "")).strip()

    for prefix in _PREFIX_SEKOLAH:
        if n.upper().startswith(prefix):
            sisa = n[len(prefix):]
            if not sisa or not sisa[0].islower():
                return True

    return False


def diagnosa_prtg():
    """
    Cek apa yang SEBENARNYA dilihat akun PRTG ini (semua status, tanpa
    filter), supaya jelas penyebabnya kalau hasil monitoring 0.
    Tidak mengirim WhatsApp.
    """

    auth = get_prtg_auth_params()

    mode = (
        "passhash" if "passhash" in auth
        else "password" if "password" in auth
        else "TIDAK ADA (passhash/password kosong)"
    )

    print("=" * 60)
    print("DIAGNOSA PRTG")
    print("=" * 60)
    print("Akun:", auth.get("username"), "| login memakai:", mode)

    try:
        response = requests.get(
            f"{PRTG_SERVER}/api/table.json",
            params={
                "content": "sensors",
                "columns": "objid,parentid,group,device,sensor,status,downtimesince",
                **auth,
                "count": PRTG_COUNT,
            },
            timeout=PRTG_TIMEOUT,
            verify=VERIFY_SSL,
        )
    except requests.exceptions.RequestException as e:
        print("GAGAL menghubungi PRTG:", e)
        return

    print("HTTP status:", response.status_code)

    if response.status_code in (401, 403):
        print("-> LOGIN DITOLAK. Username/password(passhash) bukan akun PRTG yang benar.")
        return

    try:
        data = response.json()
    except ValueError:
        print("-> Jawaban PRTG bukan JSON:", response.text[:300])
        return

    sensors = data.get("sensors", []) if isinstance(data, dict) else []

    print("Total sensor yang TERLIHAT oleh akun ini:", len(sensors))

    if not sensors:
        print("-> Akun ini TIDAK punya akses ke sensor apa pun. Beri hak baca "
              "ke grup sekolah di PRTG (Setup > User Accounts), atau pakai "
              "akun lain.")
        return

    per_status = {}

    for sn in sensors:
        st = str(sn.get("status", "?"))
        per_status[st] = per_status.get(st, 0) + 1

    print("Per status:", per_status)

    down = [s for s in sensors if str(s.get("status", "")).lower().startswith("down")]

    print("Sensor berstatus Down:", len(down))

    kosong = [s for s in down if not str(s.get("downtimesince", "") or "").strip()]

    if kosong:
        print("-> PERINGATAN:", len(kosong), "sensor Down tanpa downtimesince "
              "(durasi tidak terbaca, tidak masuk kategori).")

    nama_down = sorted({s.get("device", "") for s in down})

    sekolah = [n for n in nama_down if _mirip_sekolah(n)]

    print("Device Down:", len(nama_down), "| yang namanya mirip sekolah:", len(sekolah))

    for n in nama_down[:5]:
        print("   contoh:", n)

    grup = sorted({str(s.get("group", "")) for s in sensors})[:8]

    print("Contoh nama grup terbaca:", grup)

    if not down:
        print("-> Memang TIDAK ADA sensor Down untuk akun ini saat ini.")
    elif not sekolah:
        print("-> Ada yang Down, tapi tidak ada yang namanya diawali "
              "SD/SMP/SMA/... (dashboard menyaring berdasarkan awalan itu).")

    print("=" * 60)


# ============================================================
# NORMALISASI NOMOR WA
# ============================================================

def normalize_target(number):
    """Normalisasi nomor WhatsApp."""

    if not number:
        return number

    text = str(number).strip()

    if text.startswith("+"):
        text = text[1:]

    if text.startswith("0"):
        text = "62" + text[1:]

    return text


# ============================================================
# AMBIL DATA PRTG
# ============================================================

def get_down_devices():
    """
    Mengambil SEMUA SENSOR DOWN TERKINI langsung dari PRTG.
    Setiap pemanggilan = request baru ke PRTG.
    """

    url = f"{PRTG_SERVER}/api/table.json"

    params = {
        "content": "sensors",

        # parentid = ID device induk sensor di PRTG (contoh #17020).
        # group    = grup induk device (dipakai dashboard untuk wilayah).
        "columns": (
            "objid,"
            "parentid,"
            "group,"
            "device,"
            "sensor,"
            "status,"
            "downtimesince,"
            "downtimesince_raw,"
            "message"
        ),

        # List dikirim sebagai filter_status berulang.
        "filter_status": PRTG_DOWN_STATUS,

        **get_prtg_auth_params(),

        "count": PRTG_COUNT,
    }

    print("=" * 60)
    print("MENGAMBIL DATA TERKINI DARI PRTG")
    print("=" * 60)
    print("URL:", url)

    response = requests.get(
        url,
        params=params,
        timeout=PRTG_TIMEOUT,
        verify=VERIFY_SSL
    )

    print("HTTP status:", response.status_code)

    response.raise_for_status()

    print("Content-Type:", response.headers.get("Content-Type", ""))

    try:
        data = response.json()
    except ValueError:
        print("Response PRTG bukan JSON.")
        print(response.text[:2000])
        raise

    if not isinstance(data, dict):
        print("Format JSON PRTG tidak sesuai.")
        return []

    sensors = data.get("sensors", [])

    if not isinstance(sensors, list):
        print("Data sensors PRTG tidak berbentuk list.")
        return []

    print("Jumlah sensor DOWN dari PRTG:", len(sensors))

    per_status = {}

    for sensor in sensors:
        status = str(sensor.get("status", "?"))
        per_status[status] = per_status.get(status, 0) + 1

    print("Rincian status:", per_status)

    return sensors


def get_online_devices():
    """
    Mengambil SEMUA SENSOR ONLINE TERKINI langsung dari PRTG.
    Setiap pemanggilan = request baru ke PRTG.
    """

    url = f"{PRTG_SERVER}/api/table.json"

    params = {
        "content": "sensors",
        "columns": (
            "objid,"
            "parentid,"
            "group,"
            "device,"
            "sensor,"
            "status,"
            "lastcheck,"
            "uptimesince,"
            "message"
        ),
        "filter_status": [3],
        **get_prtg_auth_params(),
        "count": PRTG_COUNT,
    }

    try:
        response = requests.get(
            url,
            params=params,
            timeout=PRTG_TIMEOUT,
            verify=VERIFY_SSL
        )
        response.raise_for_status()
        data = response.json()
        return data.get("sensors", []) if isinstance(data, dict) else []
    except Exception as e:
        print("Gagal mengambil data online dari PRTG:", e)
        return []




# ============================================================
# AMBIL ID DARI NAMA DEVICE
# ============================================================

def extract_id(device_name):
    """
    Mengambil kode LM/JP/XM + 6 angka dari nama device.
    Contoh: LM000450, LM000450_ONT, 88.LM000450
    """

    if not device_name:
        return None

    match = re.search(
        r"(?<![A-Za-z])((?:LM|JP|XM)\d{6})(?!\d)",
        str(device_name),
        re.IGNORECASE
    )

    if match:
        return match.group(1).upper()

    return None


def extract_ip(device_name):
    """
    Mengambil alamat IPv4 dari nama device (identitas cadangan).
    Contoh: "SD N Jetisharjo (202.169.234.90)" -> "202.169.234.90"
    """

    if not device_name:
        return None

    match = re.search(
        r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b",
        str(device_name)
    )

    if not match:
        return None

    ip = match.group(1)

    bagian = ip.split(".")

    if len(bagian) != 4:
        return None

    for angka in bagian:
        if not angka.isdigit() or int(angka) > 255:
            return None

    return ip


# ============================================================
# AMBIL SEMUA SENSOR MILIK SATU ID (DEBUG/VERIFIKASI)
# ============================================================

def get_sensors_by_id(device_id):
    """
    Mengambil SEMUA sensor (semua status, tanpa filter status) yang
    cocok dengan satu ID device. Hanya untuk debug/verifikasi manual.
    """

    if not device_id:
        return []

    target = str(device_id).strip()

    if not target:
        return []

    target_upper = target.upper()
    target_lower = target.lower()
    target_id = target.lstrip("#")

    params = {
        "content": "sensors",

        "columns": (
            "objid,"
            "parentid,"
            "group,"
            "device,"
            "sensor,"
            "status,"
            "status_raw,"
            "downtimesince,"
            "downtimesince_raw,"
            "message"
        ),

        **get_prtg_auth_params(),
        "count": PRTG_COUNT,
    }

    print("=" * 60)
    print("DEBUG: cari semua sensor milik ID:", target)
    print("=" * 60)

    response = requests.get(
        f"{PRTG_SERVER}/api/table.json",
        params=params,
        timeout=PRTG_TIMEOUT,
        verify=VERIFY_SSL
    )

    response.raise_for_status()

    data = response.json()

    sensors = data.get("sensors", []) if isinstance(data, dict) else []

    if not isinstance(sensors, list):
        sensors = []

    hasil = []

    for sensor in sensors:

        nama = sensor.get("device", "") or ""

        kode = extract_id(nama)

        ip = extract_ip(nama)

        parent = str(sensor.get("parentid", "") or "").strip()

        cocok = (
            (kode is not None and kode == target_upper)
            or (target_id.isdigit() and parent == target_id)
            or (ip is not None and ip == target)
            or (target_lower in nama.lower())
        )

        if cocok:
            hasil.append(sensor)

    print(f"Ditemukan {len(hasil)} sensor untuk '{target}'.")

    return hasil


# ============================================================
# PARSE DURASI DOWNTIME
# ============================================================

def parse_duration_to_hours(value):
    """
    Mengubah teks durasi PRTG ("5 m", "2 h", "1 d 5 h", "2 d 3 h 10 m")
    menjadi jam.
    """

    if value is None:
        return None

    text = str(value).strip().lower()

    if not text:
        return None

    days = 0
    hours = 0
    minutes = 0
    seconds = 0

    match = re.search(r"(\d+)\s*d", text)
    if match:
        days = int(match.group(1))

    match = re.search(r"(\d+)\s*h", text)
    if match:
        hours = int(match.group(1))

    match = re.search(r"(\d+)\s*m", text)
    if match:
        minutes = int(match.group(1))

    match = re.search(r"(\d+)\s*s", text)
    if match:
        seconds = int(match.group(1))

    return (
        (days * 24)
        + hours
        + (minutes / 60)
        + (seconds / 3600)
    )


# ============================================================
# LAMA DOWN SATU SENSOR
# ============================================================

def get_sensor_down_hours(sensor):
    """
    Lama down satu sensor (jam) dari downtimesince PRTG.

    Teks PRTG jadi acuan. Untuk gangguan sangat lama (teks hanya
    "10 d"), downtimesince_raw dipakai bila salah satu tafsir
    satuannya (hari/jam/detik) cocok dengan teks.
    """

    teks = str(sensor.get("downtimesince", "") or "")

    text_hours = parse_duration_to_hours(teks)

    if text_hours is None:
        return None

    try:
        raw = float(
            str(sensor.get("downtimesince_raw", "")).strip().replace(",", ".")
        )
    except ValueError:
        return text_hours

    if raw <= 0:
        return text_hours

    lower = teks.lower()

    hanya_hari = (
        "h" not in lower
        and "m" not in lower
        and "s" not in lower
    )

    toleransi = 24.5 if hanya_hari else 0.1

    kandidat = (raw * 24, raw, raw / 3600)

    cocok = [
        c for c in kandidat
        if abs(c - text_hours) <= toleransi
    ]

    if not cocok:
        return text_hours

    return min(cocok, key=lambda c: abs(c - text_hours))


# ============================================================
# BUAT DETAIL DEVICE
# ============================================================

def build_down_device_details(sensors, online_sensors=None):
    """
    Menggabungkan sensor DOWN berdasarkan ID device.

    Satu ID = satu baris. Semua sensor DOWN dihitung (down_count).
    Kategori dan "down sejak" memakai sensor dengan downtime TERLAMA.
    Key "lastdown" dipertahankan agar dashboard kompatibel
    (nilainya = sekarang - lama down).

    Jika online_sensors diberikan, device yang aktif di data online PRTG
    akan diabaikan (mengikuti status online).
    """

    online_parent_ids = set()
    online_device_ids = set()
    if online_sensors:
        for s in online_sensors:
            pid = str(s.get("parentid", "") or "").strip()
            if pid:
                online_parent_ids.add(pid)
            did = extract_id(s.get("device"))
            if did:
                online_device_ids.add(did.upper())

    devices = {}

    now = datetime.now()

    for sensor in sensors:

        device_name = sensor.get("device", "")

        device_id = extract_id(device_name)

        parent_id = str(sensor.get("parentid", "") or "").strip()

        # Lewati jika device ini sudah aktif di data online PRTG
        if online_sensors and (
            (parent_id and parent_id in online_parent_ids)
            or (device_id and device_id.upper() in online_device_ids)
        ):
            continue

        if device_id:
            device_key = device_id
            id_source = "kode"

        elif parent_id.isdigit():
            device_key = f"#{parent_id}"
            id_source = "prtg"

        else:
            device_ip = extract_ip(device_name)
            device_key = (
                device_ip
                or device_name.strip()
                or f"OBJID-{sensor.get('objid', '?')}"
            )
            id_source = "ip" if device_ip else "nama"

        hours = get_sensor_down_hours(sensor)

        if hours is None:
            print(
                "DOWNTIME TIDAK TERBACA:",
                device_name,
                "| downtimesince =",
                repr(sensor.get("downtimesince"))
            )

        if device_key not in devices:

            devices[device_key] = {
                "id": device_key,
                "has_short_id": device_id is not None,
                "id_source": id_source,
                "prtg_id": parent_id,
                "group": sensor.get("group", ""),
                "device": device_name,
                "down_count": 0,
                "lastdown": None,
                "hours": None,
                "category": "Tidak diketahui",
                "sensors": []
            }

        item = devices[device_key]

        item["down_count"] += 1

        item["sensors"].append({
            "objid": sensor.get("objid"),
            "sensor": sensor.get("sensor"),
            "status": sensor.get("status"),
            "message": sensor.get("message"),
            "downtimesince": sensor.get("downtimesince", ""),
            "hours": hours
        })

        if hours is not None and (
            item["hours"] is None
            or hours > item["hours"]
        ):
            item["hours"] = hours
            item["lastdown"] = now - timedelta(hours=hours)

    # Kategori
    for item in devices.values():

        hours = item["hours"]

        if hours is None:
            continue

        if hours < 24:
            item["category"] = "< 24 jam"

        elif hours <= 48:
            item["category"] = "24 - 48 jam"

        else:
            item["category"] = "> 48 jam"

    return devices


# ============================================================
# KELOMPOKKAN ID
# ============================================================

def group_down_devices(sensors, online_sensors=None):
    """Kelompok ID berdasarkan DATA PRTG TERKINI."""

    groups = {
        "< 24 jam": [],
        "24 - 48 jam": [],
        "> 48 jam": [],
    }

    details = build_down_device_details(sensors, online_sensors=online_sensors)

    for device_id, item in details.items():

        category = item.get("category")

        if category in groups:
            groups[category].append(device_id)

    for key in groups:
        groups[key] = sorted(groups[key])

    return groups


# ============================================================
# FORMAT PESAN WHATSAPP
# ============================================================

def format_message(groups, tidak_terbaca=0):

    lines = [
        "🔴 *MONITORING GANGGUAN*",
        ""
    ]

    section_map = [
        ("< 24 jam", "*< 24 JAM*"),
        ("24 - 48 jam", "*24 - 48 JAM*"),
    ]

    for key, title in section_map:

        lines.append(title)

        ids = groups.get(key, [])

        if ids:

            for device_id in ids:
                lines.append(device_id)

        else:

            lines.append("-")

        lines.append("")

    old_ids = groups.get("> 48 jam", [])

    lines.append("*REKAP > 48 JAM*")

    lines.append(f"Total: {len(old_ids)} ID")

    if tidak_terbaca:
        lines.append("")
        lines.append(f"Durasi tidak terbaca: {tidak_terbaca} ID")

    return "\n".join(lines)


# ============================================================
# KIRIM WHATSAPP
# ============================================================

def build_chat_id(number):
    """Membuat format chatId GreenAPI."""

    target = str(number).strip()

    if not target:
        return ""

    if target.endswith("@g.us"):
        return target

    if target.endswith("@c.us"):
        return target

    if target.startswith("120") and target.isdigit():
        return f"{target}@g.us"

    return f"{normalize_target(target)}@c.us"


def send_wa(message):

    if not message:
        return False

    url = (
        f"{GREENAPI_API_URL}"
        f"/waInstance{GREENAPI_ID_INSTANCE}"
        f"/sendMessage/{GREENAPI_TOKEN}"
    )

    chat_id = build_chat_id(WA_TARGET)

    payload = {
        "chatId": chat_id,
        "message": message
    }

    print("Mengirim WhatsApp lewat GreenAPI...")

    print("Chat ID tujuan:", chat_id)

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=30
        )

    except requests.exceptions.RequestException as e:

        print("GAGAL menghubungi GreenAPI:", e)

        return False

    print("WA HTTP status:", response.status_code)

    print("WA response mentah:", response.text[:1000])

    try:

        result = response.json()

    except ValueError:

        print("Response GreenAPI bukan JSON.")

        return False

    if response.status_code == 200 and "idMessage" in result:

        print(
            "BERHASIL: pesan WhatsApp terkirim.",
            "idMessage:",
            result.get("idMessage")
        )

        return True

    reason = result.get("message", result)

    print("GAGAL mengirim WhatsApp.", "Detail:", reason)

    reason_text = str(reason).lower()

    if response.status_code == 401 or "unauthorized" in reason_text:

        print("-> Cek GREENAPI_TOKEN dan GREENAPI_ID_INSTANCE.")

    elif "not authorized" in reason_text or "not_authorized" in reason_text:

        print("-> Instance GreenAPI belum/tidak lagi Authorized.")

    elif response.status_code == 429 or "quota" in reason_text:

        print("-> Kemungkinan kuota/limit GreenAPI tercapai.")

    return False


# ============================================================
# RUN SEKALI
# ============================================================

def run_once(force_send=False):
    """
    Cek PRTG, tampilkan hasil, lalu kirim WhatsApp.
    force_send=True: kirim walau kondisi sama dengan pengiriman terakhir.
    """

    print("=" * 60)
    print("CEK PRTG")
    print("=" * 60)

    try:

        sensors = get_down_devices()

        if not sensors:

            print("Tidak ada sensor DOWN.")
            print("Kalau ini tidak wajar, jalankan: python prtg_sekolah.py cek")

            return

        online_sensors = get_online_devices()

        details = build_down_device_details(sensors, online_sensors=online_sensors)

        groups = group_down_devices(sensors, online_sensors=online_sensors)

        print()
        print("=" * 60)
        print("HASIL PENGELOMPOKAN")
        print("=" * 60)

        for label, ids in groups.items():

            print()
            print(label)

            if ids:

                for device_id in ids:

                    item = details.get(device_id, {})

                    lastdown = item.get("lastdown")

                    lastdown_text = (
                        lastdown.strftime("%Y-%m-%d %H:%M")
                        if lastdown
                        else "-"
                    )

                    hours = item.get("hours")

                    hours_text = (
                        f"{hours:.1f} jam"
                        if hours is not None
                        else "-"
                    )

                    print(
                        "-",
                        device_id,
                        "|",
                        f"{item.get('down_count', 0)}x Down",
                        "|",
                        hours_text,
                        "|",
                        "sejak ~" + lastdown_text
                    )

            else:

                print("-")

        tidak_terbaca = [
            k for k, v in details.items()
            if v.get("category") == "Tidak diketahui"
        ]

        if tidak_terbaca:
            print("PERINGATAN: durasi down tidak terbaca untuk",
                  len(tidak_terbaca), "device:", tidak_terbaca[:10])

        message = format_message(groups, len(tidak_terbaca))

        print()
        print("=" * 60)
        print("PESAN WHATSAPP")
        print("=" * 60)
        print(message)
        print("=" * 60)

        state = baca_state_notifikasi()
        signature = signature_groups(groups)

        if force_send or state.get("signature") != signature:

            sent = send_wa(message)

            if sent:
                state["signature"] = signature
                state["last_sent"] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                simpan_state_notifikasi(state)
            else:
                print("Pesan TIDAK terkirim ke WhatsApp.")

        else:

            print("Kondisi belum berubah. WhatsApp tidak dikirim.")

    except requests.exceptions.SSLError as e:

        print("SSL ERROR:")
        print(e)

    except requests.exceptions.HTTPError as e:

        print("HTTP ERROR:")
        print(e)

        if e.response is not None:
            print(e.response.text[:1000])

    except requests.exceptions.RequestException as e:

        print("REQUEST ERROR:")
        print(e)

    except Exception as e:

        print("ERROR:", type(e).__name__, e)


# ============================================================
# MODE BERJALAN TERUS (kirim hanya kalau kondisi berubah)
# ============================================================

def run_forever():

    while True:

        run_once()

        print()
        print(f"Menunggu {CHECK_INTERVAL} detik...")

        time.sleep(CHECK_INTERVAL)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("\n" + ("#" * 60))

    print("RUN BARU DIMULAI")

    print("#" * 60)

    validate_config()

    # Mode diagnosa: python prtg_sekolah.py cek  (tanpa kirim WhatsApp)
    if len(sys.argv) > 1 and sys.argv[1].lower() == "cek":
        diagnosa_prtg()
        sys.exit(0)

    # Diagnosa singkat tiap program dinyalakan (hasil masuk bot_log.txt)
    diagnosa_prtg()

    # Kirim satu kali saat program pertama kali dijalankan.
    run_once(force_send=True)

    # Jadwal otomatis (JADWAL_WA). Program harus tetap hidup.
    # Cek waktu tiap 20 detik, kirim sekali per jadwal per hari.
    last_sent_schedule = None

    while True:
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_date = now.strftime("%Y-%m-%d")
        schedule_key = f"{current_date} {current_time}"

        if current_time in JADWAL_WA and last_sent_schedule != schedule_key:
            print()
            print("=" * 60)
            print(f"JADWAL WA TIBA: {current_time}")
            print("=" * 60)

            run_once(force_send=True)
            last_sent_schedule = schedule_key

        time.sleep(20)