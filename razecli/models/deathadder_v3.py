"""Razer DeathAdder V3 (wired) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="deathadder-v3",
    name="Razer DeathAdder V3",
    usb_ids=((0x1532, 0x00B2),),
    name_aliases=("deathadder v3", "razer deathadder v3", "da v3"),
    dpi_min=100,
    dpi_max=30000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00B2,
            capabilities=("dpi", "dpi-stages", "poll-rate"),
            name_hint="Razer DeathAdder V3",
            experimental=True,
        ),
    ),
)
