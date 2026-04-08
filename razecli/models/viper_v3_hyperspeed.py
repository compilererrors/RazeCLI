"""Razer Viper V3 HyperSpeed model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="viper-v3-hyperspeed",
    name="Razer Viper V3 HyperSpeed",
    usb_ids=((0x1532, 0x00B8),),
    name_aliases=("viper v3 hyperspeed", "razer viper v3 hyperspeed"),
    dpi_min=100,
    dpi_max=30000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00B8,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer Viper V3 HyperSpeed",
            experimental=True,
        ),
    ),
)
