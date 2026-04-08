"""Razer Basilisk V2 model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="basilisk-v2",
    name="Razer Basilisk V2",
    usb_ids=((0x1532, 0x0085),),
    name_aliases=("basilisk v2", "razer basilisk v2"),
    dpi_min=100,
    dpi_max=20000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x0085,
            capabilities=("dpi", "dpi-stages", "poll-rate", "rgb", "button-mapping"),
            name_hint="Razer Basilisk V2",
            experimental=True,
        ),
    ),
)
