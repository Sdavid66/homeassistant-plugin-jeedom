"""Constants for the Jeedom Bridge integration."""

DOMAIN = "jeedom_bridge"

# ── Configuration keys ────────────────────────────────────────────────────────
CONF_JEEDOM_URL = "jeedom_url"
CONF_API_KEY = "api_key"

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_SCAN_INTERVAL = 30  # seconds

# ── Jeedom API ────────────────────────────────────────────────────────────────
JEEDOM_API_PATH = "/core/api/jeeApi.php"
JEEDOM_API_VERSION = "2.0"

# Methods
METHOD_EQLOGIC_ALL = "eqLogic::all"
METHOD_CMD_EXECUTE = "cmd::execute"
METHOD_CMD_GETVALUE = "cmd::byId"

# ── Platform list ─────────────────────────────────────────────────────────────
PLATFORMS = ["light", "switch"]

# ── Command type identifiers (Jeedom) ─────────────────────────────────────────
CMD_TYPE_ACTION = "action"
CMD_TYPE_INFO = "info"

CMD_SUBTYPE_SLIDER = "slider"
CMD_SUBTYPE_OTHER = "other"

# ── Category / plugin tags considered as lights ───────────────────────────────
LIGHT_PLUGINS = {"light", "zwave", "zigbee", "philips_hue", "ikea", "hue"}
LIGHT_CATEGORIES = {"light", "lights", "lumière", "lumières", "éclairage", "eclairage"}

# ── Internal data keys stored in config entry ─────────────────────────────────
DATA_COORDINATOR = "coordinator"
DATA_API = "api"
