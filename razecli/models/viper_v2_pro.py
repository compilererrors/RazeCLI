"""Razer Viper V2 Pro (wired + HyperSpeed dongle) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="viper-v2-pro",
    name="Razer Viper V2 Pro",
    usb_ids=((0x1532, 0x00A5), (0x1532, 0x00A6)),
    name_aliases=("viper v2 pro", "razer viper v2 pro", "v2 pro viper"),
    dpi_min=100,
    dpi_max=30000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_mirror_product_ids=(0x00A5, 0x00A6),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00A5,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer Viper V2 Pro",
        ),
        RawHidPidSpec(
            product_id=0x00A6,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer Viper V2 Pro",
        ),
    ),
    rawhid_transport_priority=(0x00A5, 0x00A6),
)
