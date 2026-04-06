"""Razer Basilisk V3 X HyperSpeed (USB receiver + Bluetooth) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="basilisk-v3-x-hyperspeed",
    name="Razer Basilisk V3 X HyperSpeed",
    usb_ids=((0x1532, 0x00B9), (0x1532, 0x00BA)),
    name_aliases=(
        "basilisk v3 x hyperspeed",
        "razer basilisk v3 x",
        "basilisk v3 x",
        "b3 x hyperspeed",
    ),
    dpi_min=100,
    dpi_max=18000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static", "breathing", "breathing-single", "spectrum"),
    ble_endpoint_product_ids=(0x00BA,),
    ble_endpoint_experimental=True,
    ble_multi_profile_table_limited=True,
    rawhid_mirror_product_ids=(0x00B9, 0x00BA),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00B9,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Basilisk V3 X HyperSpeed",
        ),
        RawHidPidSpec(
            product_id=0x00BA,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Basilisk V3 X HyperSpeed",
            tx_candidates=(0x3F, 0x1F, 0xFF),
            report_id_candidates=(0x00, 0x02),
            experimental=True,
            prefer_vendor_usage_page=True,
        ),
    ),
    rawhid_transport_priority=(0x00B9, 0x00BA),
    ble_button_decode_layouts=("compact-16", "razer-v1", "slot-byte6"),
)
