"""Razer DeathAdder V4 Pro (wired + HyperSpeed dongle) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="deathadder-v4-pro",
    name="Razer DeathAdder V4 Pro",
    usb_ids=((0x1532, 0x00BE), (0x1532, 0x00BF)),
    name_aliases=("deathadder v4 pro", "da v4 pro", "razer deathadder v4 pro"),
    dpi_min=100,
    dpi_max=35000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_mirror_product_ids=(0x00BE, 0x00BF),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00BE,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer DeathAdder V4 Pro",
            experimental=True,
        ),
        RawHidPidSpec(
            product_id=0x00BF,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer DeathAdder V4 Pro",
            experimental=True,
        ),
    ),
    rawhid_transport_priority=(0x00BE, 0x00BF),
)
