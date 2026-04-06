"""Razer DeathAdder V3 Pro (wired + HyperSpeed dongle, incl. alt PIDs) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="deathadder-v3-pro",
    name="Razer DeathAdder V3 Pro",
    usb_ids=(
        (0x1532, 0x00B6),
        (0x1532, 0x00B7),
        (0x1532, 0x00C2),
        (0x1532, 0x00C3),
    ),
    name_aliases=("deathadder v3 pro", "da v3 pro", "razer deathadder v3 pro"),
    dpi_min=100,
    dpi_max=30000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_mirror_product_ids=(0x00B6, 0x00B7, 0x00C2, 0x00C3),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00B6,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer DeathAdder V3 Pro",
        ),
        RawHidPidSpec(
            product_id=0x00B7,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer DeathAdder V3 Pro",
        ),
        RawHidPidSpec(
            product_id=0x00C2,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer DeathAdder V3 Pro",
        ),
        RawHidPidSpec(
            product_id=0x00C3,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer DeathAdder V3 Pro",
        ),
    ),
    rawhid_transport_priority=(0x00B6, 0x00B7, 0x00C2, 0x00C3),
)
