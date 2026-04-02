"""DeathAdder V2 model definition."""

from razecli.models.base import ModelSpec


MODEL = ModelSpec(
    slug="deathadder-v2",
    name="Razer DeathAdder V2",
    usb_ids=((0x1532, 0x0084),),
    name_aliases=("deathadder v2", "razer deathadder v2"),
    dpi_min=100,
    dpi_max=20000,
    supported_poll_rates=(125, 500, 1000),
)

