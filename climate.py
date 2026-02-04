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
from .const import DOMAIN, CONF_ROOM_NAME, CONF_TEMP_SENSOR, CONF_CLIMATE_AC

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Setup climate entities - Thermostats virtuels + Switch POC."""
    manager = hass.data[DOMAIN][entry.entry_id]
    climates = []
    
    # Créer les thermostats virtuels (ancien système)
    for room_idx, room in enumerate(manager.rooms):
        v_climate = EnergyOptimizerVirtualThermostat(manager, room, room_idx, entry.entry_id)
        climates.append(v_climate)
        manager.register_virtual_climate(room_idx, v_climate)
    
    # ===== POC : Créer UN switch pour la première pièce (Séjour = room 0) =====
    if len(manager.rooms) > 0:
        switch = EnergyOptimizerSwitch(manager, 0, entry.entry_id)
        climates.append(switch)
        manager.register_switch(0, switch)
        _LOGGER.info("✅ POC: Created EO Switch for room 0")
    # ==========================================================================
    
    async_add_entities(climates, True)


class EnergyOptimizerVirtualThermostat(ClimateEntity):
    """Thermostat virtuel (ancien système)."""
    
    def __init__(self, manager, room_config, room_idx, entry_id):
        self._manager = manager
        self._room_config = room_config
        self._room_idx = room_idx
        
        room_name = room_config.get(CONF_ROOM_NAME, f"Room {room_idx}")
        self._attr_name = f"Optimizer {room_name}"
        self._attr_unique_id = f"energy_optimizer_climate_{entry_id}_{room_idx}"
        
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        
        # --- DÉTECTION DYNAMIQUE DES MODES ---
        modes = [HVACMode.OFF, HVACMode.HEAT]
        
        if room_config.get(CONF_CLIMATE_AC):
            modes.append(HVACMode.COOL)
            
        self._attr_hvac_modes = modes
        
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


# ===== POC : CLIMATE SWITCH POUR VERSATILE THERMOSTAT =====

class EnergyOptimizerSwitch(ClimateEntity):
    """
    Climate Switch POC - Fait le pont entre Versatile Thermostat et Energy Optimizer.
    
    Ce climate reçoit les demandes de VT (température, mode)
    et notifie le Manager qui décide PAC ou Gaz.
    """
    
    def __init__(self, manager, room_idx: int, entry_id: str):
        """Initialiser le switch."""
        self._manager = manager
        self._room_idx = room_idx
        self._entry_id = entry_id
        
        room_name = manager.rooms[room_idx].get(CONF_ROOM_NAME, f"Room {room_idx}")
        
        self._attr_name = f"EO Switch {room_name}"
        self._attr_unique_id = f"energy_optimizer_switch_{entry_id}_{room_idx}"
        
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_precision = PRECISION_TENTHS
        self._attr_target_temperature_step = 0.5
        self._attr_min_temp = 10
        self._attr_max_temp = 30
        
        # Modes disponibles
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
        
        # État initial
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = 20.0
        self._current_temperature = None
        self._attr_hvac_action = HVACAction.OFF
        
        _LOGGER.info(f"✅ EO Switch created for room {room_idx}: {self._attr_name}")
    
    @property
    def current_temperature(self):
        """Température actuelle."""
        return self._current_temperature
    
    @property
    def hvac_action(self):
        """Action actuelle (heating/cooling/idle/off)."""
        return self._attr_hvac_action
    
    async def async_set_hvac_mode(self, hvac_mode: HVACMode):
        """
        VT demande un changement de mode.
        
        Cette méthode est appelée par Versatile Thermostat quand :
        - Il décide d'activer le chauffage
        - Fenêtre ouverte → force OFF
        - Présence partie → force OFF (selon config VT)
        """
        _LOGGER.info(
            f"🔔 VT requested mode change for room {self._room_idx}: "
            f"{self._attr_hvac_mode} → {hvac_mode}"
        )
        
        old_mode = self._attr_hvac_mode
        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()
        
        # Notifier le manager que VT a changé de mode
        if old_mode != hvac_mode:
            await self._manager.on_vt_mode_change(
                self._room_idx, 
                hvac_mode,
                self._attr_target_temperature
            )
    
    async def async_set_temperature(self, **kwargs):
        """
        VT demande un changement de température.
        
        Appelé quand :
        - Utilisateur change la consigne
        - VT passe en préréglage (Confort/Eco/Boost)
        - Planning horaire change la température
        """
        if ATTR_TEMPERATURE in kwargs:
            new_temp = kwargs[ATTR_TEMPERATURE]
            
            _LOGGER.info(
                f"🌡️ VT requested temp change for room {self._room_idx}: "
                f"{self._attr_target_temperature} → {new_temp}°C"
            )
            
            old_temp = self._attr_target_temperature
            self._attr_target_temperature = new_temp
            self.async_write_ha_state()
            
            # Notifier le manager
            if old_temp != new_temp:
                await self._manager.on_vt_temp_change(
                    self._room_idx,
                    new_temp
                )
    
    def update_from_manager(
        self, 
        current_temp: float, 
        hvac_action: HVACAction,
        reason: str = None
    ):
        """
        Le Manager met à jour l'état du switch après avoir pris sa décision.
        
        Args:
            current_temp: Température actuelle mesurée
            hvac_action: Action réelle (HEATING/COOLING/IDLE/OFF)
            reason: Raison de la décision (pour debug)
        """
        self._current_temperature = current_temp
        self._attr_hvac_action = hvac_action
        
        if reason:
            _LOGGER.debug(
                f"📊 Manager updated switch room {self._room_idx}: "
                f"temp={current_temp}°C, action={hvac_action}, reason={reason}"
            )
        
        self.async_write_ha_state()
    
    @property
    def extra_state_attributes(self):
        """Attributs supplémentaires pour debug."""
        return {
            "room_index": self._room_idx,
            "managed_by": "Energy Optimizer",
            "vt_controlled": True,
        }
