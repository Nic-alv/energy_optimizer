# /config/custom_components/energy_optimizer/climate.py

import logging
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACMode,
    HVACAction
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature, PRECISION_TENTHS
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN, CONF_ROOM_NAME

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Setup climate entities - Uniquement les switchs pour VT."""
    manager = hass.data[DOMAIN][entry.entry_id]
    climates = []
    
    # On boucle sur TOUTES les pièces configurées
    for room_idx, room in enumerate(manager.rooms):
        # Création automatique du Switch pour chaque pièce
        switch = EnergyOptimizerSwitch(manager, room_idx, entry.entry_id)
        climates.append(switch)
        
        # Enregistrement dans le manager pour qu'il puisse le piloter
        manager.register_switch(room_idx, switch)
        
        room_name = room.get(CONF_ROOM_NAME, f"Room {room_idx}")
        _LOGGER.info(f"✅ EO Switch created for: {room_name}")
    
    async_add_entities(climates, True)


class EnergyOptimizerSwitch(ClimateEntity):
    """
    Climate Switch - Interface unique pour Versatile Thermostat.
    Il reçoit les ordres de VT (Heat/Off/Cool) et notifie le Manager.
    """
    
    def __init__(self, manager, room_idx: int, entry_id: str):
        self._manager = manager
        self._room_idx = room_idx
        self._entry_id = entry_id
        
        room_name = manager.rooms[room_idx].get(CONF_ROOM_NAME, f"Room {room_idx}")
        
        # ID unique et Nom standardisé
        # Ex: climate.eo_switch_sejour
        self._attr_name = f"EO Switch {room_name}"
        self._attr_unique_id = f"energy_optimizer_switch_{entry_id}_{room_idx}"
        
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_precision = PRECISION_TENTHS
        self._attr_target_temperature_step = 0.5
        self._attr_min_temp = 10
        self._attr_max_temp = 30
        
        # Modes supportés par ce switch (pour VT)
        self._attr_hvac_modes = [
            HVACMode.OFF,
            HVACMode.HEAT,
            HVACMode.COOL,
            HVACMode.HEAT_COOL,
        ]
        
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE 
            | ClimateEntityFeature.TURN_OFF 
            | ClimateEntityFeature.TURN_ON
        )
        
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = 20.0
        self._current_temperature = None
        self._attr_hvac_action = HVACAction.OFF
    
    @property
    def current_temperature(self):
        return self._current_temperature
    
    @property
    def hvac_action(self):
        return self._attr_hvac_action
    
    async def async_set_hvac_mode(self, hvac_mode: HVACMode):
        """Ordre reçu de Versatile Thermostat."""
        _LOGGER.info(
            f"🔔 VT Order for room {self._room_idx}: Mode {self._attr_hvac_mode} -> {hvac_mode}"
        )
        old_mode = self._attr_hvac_mode
        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()
        
        if old_mode != hvac_mode:
            await self._manager.on_vt_mode_change(
                self._room_idx, 
                hvac_mode,
                self._attr_target_temperature
            )
    
    async def async_set_temperature(self, **kwargs):
        """Changement de consigne reçu de Versatile Thermostat."""
        if ATTR_TEMPERATURE in kwargs:
            new_temp = kwargs[ATTR_TEMPERATURE]
            
            _LOGGER.info(
                f"🌡️ VT Order for room {self._room_idx}: Temp {self._attr_target_temperature} -> {new_temp}"
            )
            old_temp = self._attr_target_temperature
            self._attr_target_temperature = new_temp
            self.async_write_ha_state()
            
            if old_temp != new_temp:
                await self._manager.on_vt_temp_change(self._room_idx, new_temp)
    
    def update_from_manager(self, current_temp, hvac_action, reason=None):
        """Retour d'état du Manager vers le Switch (et donc vers VT)."""
        self._current_temperature = current_temp
        self._attr_hvac_action = hvac_action
        self.async_write_ha_state()
    
    @property
    def extra_state_attributes(self):
        return {
            "room_index": self._room_idx,
            "managed_by": "Energy Optimizer",
            "integration": "Versatile Thermostat Ready",
        }