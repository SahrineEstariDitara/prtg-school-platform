"""Unit tests for existing school detection, region inference, and downtime logic."""

from __future__ import annotations

import json

import dashboard_sekolah as ds
import prtg_sekolah as ps


def test_is_sekolah_filters_schools_and_ignores_core_devices(isolated_env):
    assert ds.is_sekolah("SMA N 3 BANTUL (10.123.0.154)") is True
    assert ds.is_sekolah("SIM063 SMA Negeri 01 Gamping") is True
    assert ds.is_sekolah("SMK NEGERI 1 GEDANGSARI") is True
    assert ds.is_sekolah("SLBN 2 GUNUNGKIDUL") is True
    assert ds.is_sekolah("CORE-ROUTER-DC-01") is False
    assert ds.is_sekolah("SWITCH-BACKBONE-DIY") is False


def test_is_sekolah_honors_manual_pengaturan_overrides(isolated_env):
    (isolated_env / "pengaturan_sekolah.json").write_text(
        json.dumps(
            {
                "wilayah": {},
                "kata_sekolah": ["pkbm"],
                "kata_bukan_sekolah": ["gw"],
            }
        ),
        encoding="utf-8",
    )
    ds._cache_pengaturan.update(mtime=None, data=None)

    assert ds.is_sekolah("PKBM Harapan Bangsa") is True
    assert ds.is_sekolah("GW Sekolah") is False


def test_school_name_normalization_and_id_extraction():
    assert ds.nama_bersih("140. SIM063 SMA N 1 Bantul (10.20.5.17)") == "SIM063 SMA N 1 Bantul"
    assert ds.nama_tampil("SIM063 SMA N 1 Bantul (10.20.5.17)") == "SMA N 1 Bantul"
    assert ds.normalisasi_nama_sekolah("SMKN 1 Gedangsari") == "SMK NEGERI 1 GEDANGSARI"
    assert ds.normalisasi_nama_sekolah("SMAN 1 Depok") == "SMA NEGERI 1 DEPOK"
    assert ds.tentukan_id("SIM063 SMA Negeri 01 Gamping", "", "25254") == "SIM063"
    assert ds.tentukan_id("SMA N 3 BANTUL (10.123.0.154)", "", "21755") == "#21755"


def test_region_detection_covers_all_diy_regions(isolated_env):
    cases = [
        ("DIY / Bantul", "SMA N 1 Sewon", "Bantul"),
        ("DIY / Gunung Kidul", "SMK N 2 Wonosari", "Gunung Kidul"),
        ("DIY / Kulon Progo", "SMK N 2 Pengasih", "Kulon Progo"),
        ("DIY / Sleman", "SMA N 1 Depok", "Sleman"),
        ("DIY / Kota Jogja", "SMA N 4 Yogyakarta", "Kota Jogja"),
        ("DIY / Khusus", "SLB Negeri Pembina", "Khusus"),
        ("", "SMA N 1 Jetis", "Bantul"),
        ("", "Sekolah Tanpa Lokasi", ds.BELUM),
    ]
    for group_name, device_name, expected in cases:
        wilayah, _sumber = ds.tentukan_wilayah_info(group_name, device_name)
        assert wilayah == expected


def test_duration_parsing_and_down_grouping(sample_down_sensors):
    assert ps.parse_duration_to_hours("3 h 30 m") == 3.5
    assert ps.parse_duration_to_hours("1 d 6 h") == 30.0
    assert ps.parse_duration_to_hours("") is None

    school_only = ds.filter_sekolah(sample_down_sensors)
    assert len(school_only) == 3

    details = ps.build_down_device_details(school_only)
    categories = {item["category"] for item in details.values()}
    assert categories == {"< 24 jam", "24 - 48 jam", "> 48 jam"}


def test_prepare_details_and_catat_gangguan_persist_to_isolated_json(
    mock_prtg,
    sample_down_sensors,
):
    school_down = ds.filter_sekolah(sample_down_sensors)
    details = ds.prepare_details(school_down, online_schools=[])

    assert len(details) == 3
    regions = {d["wilayah"] for d in details}
    assert regions == {"Bantul", "Sleman", "Gunung Kidul"}

    ds.catat_gangguan(details)
    saved = ds.baca_gangguan()
    assert len(saved) == 3
    assert all(item["selesai"] is None for item in saved)
