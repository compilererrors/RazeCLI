"""Razer DeathAdder V3 HyperSpeed (wired + HyperSpeed dongle) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="deathadder-v3-hyperspeed",
    name="Razer DeathAdder V3 HyperSpeed",
    usb_ids=((0x1532, 0x00C4), (0x1532, 0x00C5)),
    name_aliases=("deathadder v3 hyperspeed", "da v3 hyperspeed"),
    dpi_min=100,
    dpi_max=28000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_mirror_product_ids=(0x00C4, 0x00C5),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00C4,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer DeathAdder V3 HyperSpeed",
            experimental=True,
        ),
        RawHidPidSpec(
            product_id=0x00C5,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery", "rgb", "button-mapping"),
            name_hint="Razer DeathAdder V3 HyperSpeed",
            experimental=True,
        ),
    ),
    rawhid_transport_priority=(0x00C4, 0x00C5),
)
