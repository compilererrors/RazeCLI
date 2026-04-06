"""Razer Naga X model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="naga-x",
    name="Razer Naga X",
    usb_ids=((0x1532, 0x0096),),
    name_aliases=("naga x", "razer naga x"),
    dpi_min=100,
    dpi_max=16000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x0096,
            capabilities=("dpi", "dpi-stages", "poll-rate"),
            name_hint="Razer Naga X",
            experimental=True,
        ),
    ),
)
