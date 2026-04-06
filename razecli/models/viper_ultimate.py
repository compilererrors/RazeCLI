"""Razer Viper Ultimate (wired + HyperSpeed dongle) model definition."""

from razecli.models.base import ModelSpec, RawHidPidSpec


MODEL = ModelSpec(
    slug="viper-ultimate",
    name="Razer Viper Ultimate",
    usb_ids=((0x1532, 0x007A), (0x1532, 0x007B)),
    name_aliases=("viper ultimate", "razer viper ultimate"),
    dpi_min=100,
    dpi_max=20000,
    supported_poll_rates=(125, 500, 1000),
    ble_poll_rate_supported=False,
    ble_supported_poll_rates=(),
    ble_supported_rgb_modes=("off", "static"),
    rawhid_mirror_product_ids=(0x007A, 0x007B),
    rawhid_pid_specs=(
        RawHidPidSpec(
            product_id=0x007A,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Viper Ultimate",
        ),
        RawHidPidSpec(
            product_id=0x007B,
            capabilities=("dpi", "dpi-stages", "poll-rate", "battery"),
            name_hint="Razer Viper Ultimate",
        ),
    ),
    rawhid_transport_priority=(0x007A, 0x007B),
)
