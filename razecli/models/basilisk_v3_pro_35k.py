"""Razer Basilisk V3 Pro 35K (wired + HyperSpeed dongle) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="basilisk-v3-pro-35k",
    name="Razer Basilisk V3 Pro 35K",
    usb_ids=((0x1532, 0x00CC), (0x1532, 0x00CD)),
    name_aliases=("basilisk v3 pro 35k", "b3 pro 35k"),
    dpi_min=100,
    dpi_max=35000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_mirror_product_ids=(0x00CC, 0x00CD),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00CC,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Basilisk V3 Pro 35K",
        ),
        RawHidPidSpec(
            product_id=0x00CD,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Basilisk V3 Pro 35K",
        ),
    ),
    rawhid_transport_priority=(0x00CC, 0x00CD),
)
