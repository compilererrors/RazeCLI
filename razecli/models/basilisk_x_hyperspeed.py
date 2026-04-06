"""Razer Basilisk X HyperSpeed model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


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
    ble_endpoint_product_ids=(0x0083,),
    ble_endpoint_experimental=True,
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x0083,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Basilisk X HyperSpeed",
            experimental=True,
        ),
    ),
)
