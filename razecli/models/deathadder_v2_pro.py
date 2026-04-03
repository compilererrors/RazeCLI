"""DeathAdder V2 Pro model definition."""

from razecli.models.base import ModelSpec


MODEL = ModelSpec(
    slug="deathadder-v2-pro",
    name="Razer DeathAdder V2 Pro",
    usb_ids=((0x1532, 0x007C), (0x1532, 0x007D), (0x1532, 0x008E)),
    name_aliases=("deathadder v2 pro", "razer deathadder", "da v2 pro"),
    dpi_min=100,
    dpi_max=20000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
)
