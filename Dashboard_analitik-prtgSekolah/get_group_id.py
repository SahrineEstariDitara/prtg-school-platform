import requests
from app.config import sanitize_text
from prtg_sekolah import (
    GREENAPI_API_URL,
    GREENAPI_ID_INSTANCE,
    GREENAPI_TOKEN,
)


def get_groups():
    url = (
        f"{GREENAPI_API_URL}"
        f"/waInstance{GREENAPI_ID_INSTANCE}"
        f"/getContacts/{GREENAPI_TOKEN}"
    )

    params = {
        "group": "true"
    }

    print("Mengambil daftar grup WhatsApp...")
    print()

    try:
        response = requests.get(
            url,
            params=params,
            timeout=30
        )

        print("HTTP Status:", response.status_code)

        if response.status_code != 200:
            print("Gagal mengambil daftar grup.")
            print(response.text)
            return

        data = response.json()

        if not isinstance(data, list):
            print("Response GreenAPI tidak sesuai.")
            print(data)
            return

        groups = [
            item for item in data
            if item.get("type") == "group"
        ]

        print("=" * 60)
        print("DAFTAR GRUP WHATSAPP")
        print("=" * 60)

        if not groups:
            print("Tidak ada grup yang ditemukan.")
            return

        for nomor, group in enumerate(groups, start=1):
            print()
            print(f"{nomor}. {group.get('name', '(Tanpa Nama)')}")
            print(f"   ID: {group.get('id', '-')}")
        
        print()
        print("=" * 60)
        print(f"Total grup: {len(groups)}")
        print("=" * 60)

    except requests.exceptions.RequestException as e:
        print("Gagal menghubungi GreenAPI:")
        print(sanitize_text(e))

    except Exception as e:
        print("Terjadi error:")
        print(type(e).__name__, sanitize_text(e))


if __name__ == "__main__":
    get_groups()