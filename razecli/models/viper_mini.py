"""Razer Viper Mini model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="viper-mini",
    name="Razer Viper Mini",
    usb_ids=((0x1532, 0x008A),),
    name_aliases=("viper mini", "razer viper mini"),
    dpi_min=100,
    dpi_max=8500,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x008A,
            capabilities=("dpi", "dpi-stages", "poll-rate"),
            name_hint="Razer Viper Mini",
            experimental=True,
        ),
    ),
)
