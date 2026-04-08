"""Razer Viper V3 Pro (wired + HyperSpeed dongle) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="viper-v3-pro",
    name="Razer Viper V3 Pro",
    usb_ids=((0x1532, 0x00C0), (0x1532, 0x00C1)),
    name_aliases=("viper v3 pro", "razer viper v3 pro"),
    dpi_min=100,
    dpi_max=35000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_mirror_product_ids=(0x00C0, 0x00C1),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00C0,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer Viper V3 Pro",
            experimental=True,
        ),
        RawHidPidSpec(
            product_id=0x00C1,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer Viper V3 Pro",
            experimental=True,
        ),
    ),
    rawhid_transport_priority=(0x00C0, 0x00C1),
)
