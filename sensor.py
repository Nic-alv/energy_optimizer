import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, CONF_ROOM_NAME

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Crée les sensors pour chaque pièce configurée."""
    manager = hass.data[DOMAIN][entry.entry_id]
    
    sensors = []
    for room_idx, room in enumerate(manager.rooms):
        sensors.append(EnergyOptimizerRoomSensor(manager, room, room_idx))
    
    async_add_entities(sensors, True)

class EnergyOptimizerRoomSensor(SensorEntity):
    """Sensor qui expose les calculs pour une pièce."""

    def __init__(self, manager, room_config, room_idx):
        self._manager = manager
        self._room_config = room_config
        self._room_idx = room_idx
        self._attr_name = f"Optimizer {room_config.get(CONF_ROOM_NAME)}"
        self._attr_unique_id = f"energy_optimizer_room_{entry_id}_{room_idx}" if hasattr(manager, 'entry') and (entry_id := manager.entry.entry_id) else f"energy_optimizer_room_{room_idx}"
        self._attr_icon = "mdi:home-thermometer"
        # On s'abonne aux mises à jour du manager
        self._manager.register_sensor(self)

    @property
    def unique_id(self):
        return f"{self._manager.entry.entry_id}_room_{self._room_idx}"

    @property
    def state(self):
        """L'état principal est la source de chauffage active."""
        # On récupère les données calculées depuis le manager
        data = self._manager.get_room_status(self._room_idx)
        return data.get("active_source", "Unknown")

    @property
    def extra_state_attributes(self):
        """Tous les détails techniques (COP, Prix...)."""
        data = self._manager.get_room_status(self._room_idx)
        return {
            "target_temp": data.get("target_temp"),
            "current_cop": data.get("cop"),
            "cost_ac_kwh": data.get("cost_ac"),  # Coût pour 1kWh de chaleur via AC
            "cost_gas_kwh": data.get("cost_gas"), # Coût pour 1kWh de chaleur via Gaz
            "is_profitable": data.get("profitable"),
            "outside_temp": data.get("outside_temp"),
            "reason": data.get("reason")
        }

    def update_from_manager(self):
        """Appelé par le manager quand le calcul est fini."""
        self.async_write_ha_state()