"""DeathAdder V2 Mini model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="deathadder-v2-mini",
    name="Razer DeathAdder V2 Mini",
    usb_ids=((0x1532, 0x008C),),
    name_aliases=("deathadder v2 mini", "razer deathadder v2 mini"),
    dpi_min=100,
    dpi_max=8500,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x008C,
            capabilities=("dpi", "dpi-stages", "poll-rate", "rgb", "button-mapping"),
            name_hint="Razer DeathAdder V2 Mini",
            experimental=True,
        ),
    ),
)
