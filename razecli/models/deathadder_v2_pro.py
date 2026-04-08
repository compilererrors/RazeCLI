"""DeathAdder V2 Pro model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="deathadder-v2-pro",
    name="Razer DeathAdder V2 Pro",
    usb_ids=((0x1532, 0x007C), (0x1532, 0x007D), (0x1532, 0x008E)),
    name_aliases=("deathadder v2 pro", "razer deathadder", "da v2 pro"),
    dpi_min=100,
    dpi_max=20000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    # BLE RGB: OpenSnek-class 10:83 zone read exposes effect id (e.g. 0x04 = spectrum).
    ble_supported_rgb_modes=("off", "static", "breathing", "breathing-single", "spectrum"),
    ble_endpoint_product_ids=(0x008E,),
    ble_endpoint_experimental=True,
    ble_multi_profile_table_limited=True,
    onboard_profile_bank_switch=True,
    rawhid_mirror_product_ids=(0x007C, 0x007D),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x007C,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer DeathAdder V2 Pro",
        ),
        RawHidPidSpec(
            product_id=0x007D,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer DeathAdder V2 Pro",
        ),
        RawHidPidSpec(
            product_id=0x008E,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer DeathAdder V2 Pro",
            tx_candidates=(0x3F, 0x1F, 0xFF),
            report_id_candidates=(0x00, 0x02),
            experimental=True,
            prefer_vendor_usage_page=True,
        ),
    ),
    rawhid_transport_priority=(0x007C, 0x007D, 0x008E),
    cli_default_target=True,
    ble_button_decode_layouts=("compact-16", "razer-v1", "slot-byte6"),
)
