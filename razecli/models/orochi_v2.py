"""Razer Orochi V2 (USB receiver + Bluetooth) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="orochi-v2",
    name="Razer Orochi V2",
    usb_ids=((0x1532, 0x0094), (0x1532, 0x0095)),
    name_aliases=("orochi v2", "razer orochi v2"),
    dpi_min=100,
    dpi_max=18000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static", "breathing", "breathing-single", "spectrum"),
    ble_endpoint_product_ids=(0x0095,),
    ble_endpoint_experimental=True,
    ble_multi_profile_table_limited=True,
    rawhid_mirror_product_ids=(0x0094, 0x0095),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x0094,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer Orochi V2",
        ),
        RawHidPidSpec(
            product_id=0x0095,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer Orochi V2",
            tx_candidates=(0x3F, 0x1F, 0xFF),
            report_id_candidates=(0x00, 0x02),
            experimental=True,
            prefer_vendor_usage_page=True,
        ),
    ),
    rawhid_transport_priority=(0x0094, 0x0095),
    ble_button_decode_layouts=("compact-16", "razer-v1", "slot-byte6"),
)
