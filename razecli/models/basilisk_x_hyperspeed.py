"""Basilisk X HyperSpeed model definition."""

from razecli.models.base import ModelSpec


MODEL = ModelSpec(
    slug="basilisk-x-hyperspeed",
    name="Razer Basilisk X HyperSpeed",
    usb_ids=((0x1532, 0x0083),),
    name_aliases=("basilisk x hyperspeed", "razer basilisk x"),
    dpi_min=100,
    dpi_max=16000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
)
