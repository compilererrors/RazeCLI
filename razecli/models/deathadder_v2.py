"""DeathAdder V2 model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="deathadder-v2",
    name="Razer DeathAdder V2",
    usb_ids=((0x1532, 0x0084),),
    name_aliases=("deathadder v2", "razer deathadder v2"),
    dpi_min=100,
    dpi_max=20000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x0084,
            capabilities=("dpi", "poll-rate"),
            name_hint="Razer DeathAdder V2",
            experimental=True,
        ),
    ),
)
