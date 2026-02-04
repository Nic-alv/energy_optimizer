# ========================================
# PHASE 1 - PROOF OF CONCEPT
# Climate Switch Intermédiaire
# ========================================

"""
Climate Switch - Fait le pont entre Versatile Thermostat et Energy Optimizer

Architecture :
climate.vt_sejour (Versatile Thermostat)
  ↓ demande chaleur
climate.eo_switch_sejour (ce fichier)
  ↓ notifie
Energy Optimizer Manager
  ↓ décide PAC ou Gaz
climate.sejour (Toshiba) OU climate.chaudiere (Gaz)
"""

import logging
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

# Pour le POC, on va juste créer UNE entité switch pour le Séjour

async def async_setup_entry(
    hass: HomeAssistant, 
    entry: ConfigEntry, 
    async_add_entities: AddEntitiesCallback
):
    """Setup climate switches (POC : juste 1 pour test)."""
    from .const import DOMAIN
    
    manager = hass.data[DOMAIN][entry.entry_id]
    
    # POC : Créer UN SEUL switch pour la première pièce (Séjour)
    if len(manager.rooms) > 0:
        switch = EnergyOptimizerSwitch(manager, 0, entry.entry_id)
        manager.register_switch(0, switch)
        async_add_entities([switch], True)
        _LOGGER.info("✅ Created EO Switch for room 0 (POC)")


class EnergyOptimizerSwitch(ClimateEntity):
    """
    Climate virtuel qui fait le lien entre VT et Energy Optimizer.
    
    Ce climate reçoit les demandes de VT (température, mode)
    et notifie le Manager qui décide PAC ou Gaz.
    """
    
    def __init__(self, manager, room_idx: int, entry_id: str):
        """Initialiser le switch."""
        self._manager = manager
        self._room_idx = room_idx
        self._entry_id = entry_id
        
        room_name = manager.rooms[room_idx].get("room_name", f"Room {room_idx}")
        
        self._attr_name = f"EO Switch {room_name}"
        self._attr_unique_id = f"energy_optimizer_switch_{entry_id}_{room_idx}"
        
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_precision = 0.5
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
