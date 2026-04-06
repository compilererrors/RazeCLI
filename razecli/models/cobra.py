"""Razer Cobra model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="cobra",
    name="Razer Cobra",
    usb_ids=((0x1532, 0x00A3),),
    name_aliases=("razer cobra",),
    dpi_min=100,
    dpi_max=8500,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00A3,
            capabilities=("dpi", "dpi-stages", "poll-rate"),
            name_hint="Razer Cobra",
            experimental=True,
        ),
    ),
)
