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
from .const import DOMAIN, CONF_ROOM_NAME, CONF_TEMP_SENSOR

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    manager = hass.data[DOMAIN][entry.entry_id]
    climates = []
    
    for room_idx, room in enumerate(manager.rooms):
        v_climate = EnergyOptimizerVirtualThermostat(manager, room, room_idx, entry.entry_id)
        climates.append(v_climate)
        manager.register_virtual_climate(room_idx, v_climate)
    
    async_add_entities(climates, True)

class EnergyOptimizerVirtualThermostat(ClimateEntity):
    def __init__(self, manager, room_config, room_idx, entry_id):
        self._manager = manager
        self._room_config = room_config
        self._room_idx = room_idx
        
        room_name = room_config.get(CONF_ROOM_NAME, f"Room {room_idx}")
        self._attr_name = f"Optimizer {room_name}"
        self._attr_unique_id = f"energy_optimizer_climate_{entry_id}_{room_idx}"
        
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        # NOUVEAU : On supporte HEAT (Hiver) et COOL (Eté)
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
        self._attr_target_temperature_step = 0.5
        self._attr_precision = PRECISION_TENTHS
        self._attr_min_temp = 10
        self._attr_max_temp = 30
        
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = 20.0
        self._current_action = HVACAction.IDLE

    @property
    def current_temperature(self):
        sensor_id = self._room_config.get(CONF_TEMP_SENSOR)
        val = self._manager._get_entity_value(sensor_id)
        return val

    @property
    def hvac_action(self):
        if self._attr_hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return self._current_action

    async def async_set_hvac_mode(self, hvac_mode):
        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()
        await self._manager.update_loop()

    async def async_set_temperature(self, **kwargs):
        if ATTR_TEMPERATURE in kwargs:
            self._attr_target_temperature = kwargs[ATTR_TEMPERATURE]
            self.async_write_ha_state()
            await self._manager.update_loop()

    def update_action_from_manager(self, is_active):
        """Met à jour l'action (Chauffe ou Refroidit)"""
        if not is_active:
            self._current_action = HVACAction.IDLE
        else:
            if self._attr_hvac_mode == HVACMode.HEAT:
                self._current_action = HVACAction.HEATING
            elif self._attr_hvac_mode == HVACMode.COOL:
                self._current_action = HVACAction.COOLING
            else:
                self._current_action = HVACAction.IDLE
        self.async_write_ha_state()