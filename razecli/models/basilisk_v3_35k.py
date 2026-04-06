"""Razer Basilisk V3 35K model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="basilisk-v3-35k",
    name="Razer Basilisk V3 35K",
    usb_ids=((0x1532, 0x00CB),),
    name_aliases=("basilisk v3 35k", "razer basilisk v3 35k", "basilisk 35k"),
    dpi_min=100,
    dpi_max=35000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00CB,
            capabilities=("dpi", "dpi-stages", "poll-rate"),
            name_hint="Razer Basilisk V3 35K",
            experimental=True,
        ),
    ),
)
