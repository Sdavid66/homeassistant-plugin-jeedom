# Jeedom Bridge — Custom Component for Home Assistant

Intégration personnalisée Home Assistant (HA 2026.5+) permettant de **découvrir et contrôler** les équipements d'une instance **Jeedom v3.3.x** via son API JSON-RPC 2.0, avec compatibilité complète **Alexa**.

---

## Fonctionnalités

| Feature | Détail |
|---|---|
| 🔌 **Config Flow UI** | Configuration 100% via l'interface HA (pas de YAML) |
| 🔁 **Polling centralisé** | `DataUpdateCoordinator` — 1 requête toutes les 30 s |
| 💡 **Lumières** | On/Off + variation de luminosité (slider Jeedom → `brightness` HA) |
| 🔘 **Interrupteurs** | Tous les équipements on/off sans variateur |
| 🗣️ **Alexa-ready** | `unique_id`, `DeviceInfo`, `ColorMode` conformes |
| ⚡ **Async** | 100 % `async/await`, `aiohttp` via session HA |

---

## Installation

### Via HACS (recommandé)

1. Ajoutez ce dépôt comme **dépôt personnalisé** dans HACS.
2. Installez **Jeedom Bridge**.
3. Redémarrez Home Assistant.

### Manuelle

```bash
cp -r custom_components/jeedom_bridge \
      /config/custom_components/jeedom_bridge
```

Redémarrez Home Assistant.

---

## Configuration

1. **Paramètres → Appareils et services → Ajouter une intégration**
2. Recherchez **Jeedom Bridge**
3. Renseignez :
   - **URL Jeedom** : `http://192.168.1.50` (sans slash final)
   - **Clé API globale** : disponible dans Jeedom → Réglages → Système → Configuration → API

---

## Logique de détection des équipements

### Lumières (`light.*`)

Un équipement est classé comme lumière si **l'une** de ces conditions est vraie :

- Son plugin (`eqType_name`) appartient à : `light`, `zwave`, `zigbee`, `philips_hue`, `ikea`, `hue`
- Sa catégorie contient : `light`, `lumière`, `éclairage`
- Il possède des commandes `on`, `off` **et** un slider (→ variation supportée)

### Interrupteurs (`switch.*`)

Tous les équipements actifs avec commandes `on`/`off` mais ne correspondant pas aux critères lumière.

---

## Format des requêtes Jeedom

```json
POST /core/api/jeeApi.php
{
  "jsonrpc": "2.0",
  "method": "eqLogic::all",
  "params": { "apikey": "VOTRE_CLE_API" },
  "id": 1
}
```

---

## Logs & Débogage

Activez le niveau `debug` dans `configuration.yaml` :

```yaml
logger:
  default: warning
  logs:
    custom_components.jeedom_bridge: debug
```

---

## Architecture

```
custom_components/jeedom_bridge/
├── __init__.py          # Entry point : setup/unload
├── api.py               # Client JSON-RPC async (aiohttp)
├── config_flow.py       # Config Flow UI (URL + API key)
├── const.py             # Constantes & domaine
├── coordinator.py       # DataUpdateCoordinator + parsing eqLogics
├── light.py             # Plateforme lumière (dimming inclus)
├── switch.py            # Plateforme interrupteur
├── strings.json         # Chaînes UI (base)
├── manifest.json        # Métadonnées HA
└── translations/
    ├── en.json
    └── fr.json
```

---

## Compatibilité

| Logiciel | Version testée |
|---|---|
| Home Assistant Core | 2026.5.2 |
| Jeedom | 3.3.60 |
| Python | 3.12+ |
