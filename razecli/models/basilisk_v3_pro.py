"""Razer Basilisk V3 Pro (wired + HyperSpeed dongle + Bluetooth) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="basilisk-v3-pro",
    name="Razer Basilisk V3 Pro",
    usb_ids=((0x1532, 0x00AA), (0x1532, 0x00AB), (0x1532, 0x00AC)),
    name_aliases=("basilisk v3 pro", "razer basilisk v3 pro", "b3 pro"),
    dpi_min=100,
    dpi_max=30000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static", "breathing", "breathing-single", "spectrum"),
    ble_endpoint_product_ids=(0x00AC,),
    ble_endpoint_experimental=True,
    ble_multi_profile_table_limited=True,
    onboard_profile_bank_switch=True,
    rawhid_mirror_product_ids=(0x00AA, 0x00AB),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00AA,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer Basilisk V3 Pro",
        ),
        RawHidPidSpec(
            product_id=0x00AB,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer Basilisk V3 Pro",
        ),
        RawHidPidSpec(
            product_id=0x00AC,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer Basilisk V3 Pro",
            tx_candidates=(0x3F, 0x1F, 0xFF),
            report_id_candidates=(0x00, 0x02),
            experimental=True,
            prefer_vendor_usage_page=True,
        ),
    ),
    rawhid_transport_priority=(0x00AA, 0x00AB, 0x00AC),
    ble_button_decode_layouts=("compact-16", "razer-v1", "slot-byte6"),
)
