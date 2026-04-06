"""Razer DeathAdder Elite model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="deathadder-elite",
    name="Razer DeathAdder Elite",
    usb_ids=((0x1532, 0x005C),),
    name_aliases=("deathadder elite", "da elite", "razer deathadder elite"),
    dpi_min=100,
    dpi_max=16000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x005C,
            capabilities=("dpi", "poll-rate"),
            name_hint="Razer DeathAdder Elite",
            experimental=True,
        ),
    ),
)
