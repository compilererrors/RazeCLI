"""Razer Basilisk Ultimate (wired + HyperSpeed dongle) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="basilisk-ultimate",
    name="Razer Basilisk Ultimate",
    usb_ids=((0x1532, 0x0086), (0x1532, 0x0088)),
    name_aliases=("basilisk ultimate", "razer basilisk ultimate"),
    dpi_min=100,
    dpi_max=20000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_mirror_product_ids=(0x0086, 0x0088),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x0086,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Basilisk Ultimate",
        ),
        RawHidPidSpec(
            product_id=0x0088,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Basilisk Ultimate",
        ),
    ),
    rawhid_transport_priority=(0x0086, 0x0088),
)
