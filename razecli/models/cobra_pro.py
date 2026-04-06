"""Razer Cobra Pro (wired + HyperSpeed dongle) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="cobra-pro",
    name="Razer Cobra Pro",
    usb_ids=((0x1532, 0x00AF), (0x1532, 0x00B0)),
    name_aliases=("cobra pro", "razer cobra pro"),
    dpi_min=100,
    dpi_max=30000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_mirror_product_ids=(0x00AF, 0x00B0),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x00AF,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Cobra Pro",
        ),
        RawHidPidSpec(
            product_id=0x00B0,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Cobra Pro",
        ),
    ),
    rawhid_transport_priority=(0x00AF, 0x00B0),
)
