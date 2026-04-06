"""Razer DeathAdder V2 X HyperSpeed model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="deathadder-v2-x-hyperspeed",
    name="Razer DeathAdder V2 X HyperSpeed",
    usb_ids=((0x1532, 0x009C),),
    name_aliases=("deathadder v2 x hyperspeed", "da v2 x", "razer deathadder v2 x"),
    dpi_min=100,
    dpi_max=14000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x009C,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer DeathAdder V2 X HyperSpeed",
            experimental=True,
        ),
    ),
)
