"""Razer Viper 8K Hz model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="viper-8k",
    name="Razer Viper 8K Hz",
    usb_ids=((0x1532, 0x0091),),
    name_aliases=("viper 8k", "razer viper 8k", "viper 8khz"),
    dpi_min=100,
    dpi_max=20000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x0091,
            capabilities=("dpi", "dpi-stages", "poll-rate", "rgb", "button-mapping"),
            name_hint="Razer Viper 8K Hz",
            experimental=True,
        ),
    ),
)
