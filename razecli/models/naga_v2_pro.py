"""Razer Naga V2 Pro (wired + HyperSpeed dongle) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="naga-v2-pro",
    name="Razer Naga V2 Pro",
    usb_ids=((0x1532, 0x00A7), (0x1532, 0x00A8)),
    name_aliases=("naga v2 pro", "razer naga v2 pro"),
    dpi_min=100,
    dpi_max=30000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_mirror_product_ids=(0x00A7, 0x00A8),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00A7,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Naga V2 Pro",
            experimental=True,
        ),
        RawHidPidSpec(
            product_id=0x00A8,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Naga V2 Pro",
            experimental=True,
        ),
    ),
    rawhid_transport_priority=(0x00A7, 0x00A8),
)
