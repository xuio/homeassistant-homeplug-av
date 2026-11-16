DOMAIN = "homeplug_av"

PLATFORMS = ["sensor", "binary_sensor", "button"]

DEFAULT_SCAN_INTERVAL = 30  # seconds
DEFAULT_ADAPTER_RETENTION_SECONDS = 24 * 60 * 60
DEFAULT_LINK_RETENTION_SECONDS = 5 * 60
DEFAULT_QCA_DIAGNOSTIC_INTERVAL_SECONDS = 5 * 60

# Guards against phantom adapters created from garbage station lists.
# A HomePlug AV logical network has at most a handful of stations in practice
# (the spec allows 254 TEIs), so a sudden flood of new MACs is never real.
MAX_NEW_ADAPTERS_PER_POLL = 8
# A station only reported by another adapter's stats (never answered our own
# discovery) must appear in this many consecutive polls before it is added.
PEER_CONFIRMATION_POLLS = 2

CONF_ADAPTER_RETENTION_SECONDS = "adapter_retention_seconds"
CONF_LINK_RETENTION_SECONDS = "link_retention_seconds"
CONF_QCA_DIAGNOSTIC_INTERVAL_SECONDS = "qca_diagnostic_interval_seconds"
CONF_SCAN_INTERVAL = "scan_interval"

ATTR_ADAPTER_MAC = "adapter_mac"
ATTR_ENTRY_ID = "entry_id"
ATTR_INCLUDE_LIVE_QCA = "include_live_qca"

SERVICE_DUMP_ADAPTER_DIAGNOSTICS = "dump_adapter_diagnostics"
SERVICE_REFRESH_DISCOVERY = "refresh_discovery"
SERVICE_REFRESH_STATS = "refresh_stats"
