"""Constants for the Jeedom Bridge integration."""

DOMAIN = "jeedom_bridge"

# ── Configuration keys ────────────────────────────────────────────────────────
CONF_JEEDOM_URL = "jeedom_url"
CONF_API_KEY = "api_key"

# ── Plugin API keys (optional) ────────────────────────────────────────────────
CONF_PLUGIN_API_KEYS = "plugin_api_keys"       # dict: plugin_id → api_key
CONF_PLUGIN_KEY_EDISIO  = "plugin_key_edisio"
CONF_PLUGIN_KEY_ZWAVE   = "plugin_key_zwave"
CONF_PLUGIN_KEY_VIRTUEL = "plugin_key_virtuel"

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_SCAN_INTERVAL = 30  # seconds

# ── Jeedom API ────────────────────────────────────────────────────────────────
JEEDOM_API_PATH = "/core/api/jeeApi.php"
JEEDOM_API_VERSION = "2.0"

# Methods
METHOD_OBJECT_FULL = "jeeObject::full"
METHOD_CMD_EXECUTE = "cmd::execCmd"
METHOD_CMD_GETVALUE = "cmd::byId"
METHOD_CMD_BY_EQLOGIC = "cmd::byEqLogicId"  # fetch all cmds of a given eqLogic

# ── Platform list ─────────────────────────────────────────────────────────────
PLATFORMS = ["light", "switch", "cover"]

# ── Command type identifiers (Jeedom) ─────────────────────────────────────────
CMD_TYPE_ACTION = "action"
CMD_TYPE_INFO = "info"

CMD_SUBTYPE_SLIDER = "slider"
CMD_SUBTYPE_OTHER = "other"

# ── Category / plugin tags considered as lights ───────────────────────────────
LIGHT_PLUGINS = {"light", "zwave", "zigbee", "philips_hue", "ikea", "hue", "edisio"}
LIGHT_CATEGORIES = {"light", "lights", "lumière", "lumières", "éclairage", "eclairage"}

# ── Internal data keys stored in hass.data ────────────────────────────────────
DATA_COORDINATOR = "coordinator"
DATA_API = "api"
DATA_PLUGIN_CLIENTS = "plugin_clients"  # dict: plugin_id → JeedomApiClient
