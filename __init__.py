import logging
import asyncio
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta
from .const import DOMAIN, CONF_BATTERY_ENTITY, CONF_PRICE_ENTITY

_LOGGER = logging.getLogger(__name__)

# Intervalle de vérification (ex: 5 minutes)
SCAN_INTERVAL = timedelta(minutes=5)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Configuration via UI."""
    hass.data.setdefault(DOMAIN, {})
    
    # On récupère les choix de l'utilisateur
    battery_entity = entry.data[CONF_BATTERY_ENTITY]
    price_entity = entry.data[CONF_PRICE_ENTITY]

    # On instancie ton Manager
    manager = EnergyManager(hass, battery_entity, price_entity)
    hass.data[DOMAIN][entry.entry_id] = manager

    # On lance la boucle
    entry.async_on_unload(
        async_track_time_interval(hass, manager.update_loop, SCAN_INTERVAL)
    )
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Nettoyage."""
    hass.data[DOMAIN].pop(entry.entry_id)
    return True

class EnergyManager:
    """Ton cerveau."""
    def __init__(self, hass, battery_id, price_id):
        self.hass = hass
        self.battery_id = battery_id
        self.price_id = price_id

    async def update_loop(self, now=None):
        """Ton algo tourne ici."""
        _LOGGER.info("Optimisation en cours...")
        
        # Exemple de lecture d'état
        state_batt = self.hass.states.get(self.battery_id)
        if not state_batt: return

        soc = float(state_batt.state)
        
        # ICI : Insère ta logique (Appel MQTT, Service Climate, etc.)
        # self.hass.services.async_call(...)