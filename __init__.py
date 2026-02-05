# /config/custom_components/energy_optimizer/__init__.py

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.components.climate.const import HVACMode, HVACAction
from datetime import timedelta
from .const import *

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=1)

PLATFORMS = ["climate", "sensor"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    hass.data.setdefault(DOMAIN, {})
    manager = EnergyManager(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    entry.async_on_unload(async_track_time_interval(hass, manager.update_loop, SCAN_INTERVAL))
    return True

async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class EnergyManager:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.config = entry.data
        self.options = entry.options
        self.sensors = []
        self.room_statuses = {}
        
        # Stockage des switchs (un par pièce)
        self.switches = {} 

        self.mode = self.config.get(CONF_TARIFF_MODE, MODE_SINGLE)
        self.tariff_sensor = self.config.get(CONF_TARIFF_SENSOR)
        self.gaz_price_id = self.config[CONF_GAZ_PRICE_ENTITY]
        self.battery_id = self.config.get(CONF_BATTERY_ENTITY)
        self.outside_temp_id = self.config.get(CONF_OUTSIDE_TEMP_ENTITY)
        
        self.grid_power_id = self.options.get(CONF_GRID_POWER_ENTITY, self.config.get(CONF_GRID_POWER_ENTITY))
        self.battery_thresh_id = self.options.get(CONF_BATTERY_THRESH_ENTITY)
        self.hysteresis = self.options.get(CONF_HYSTERESIS, 0.5)
        self.summer_mode_id = self.options.get(CONF_SUMMER_MODE_ENTITY)

        self.rooms = self.options.get(CONF_ROOMS, [])
        self.map_cons_price = {1: CONF_PRICE_T1, 2: CONF_PRICE_T2, 3: CONF_PRICE_T3}
        self.map_inj_price = {1: CONF_INJ_PRICE_T1, 2: CONF_INJ_PRICE_T2, 3: CONF_INJ_PRICE_T3}
        
        _LOGGER.info(f"📊 EnergyManager initialized with {len(self.rooms)} rooms")

    def register_sensor(self, sensor):
        self.sensors.append(sensor)

    def register_switch(self, room_idx: int, switch_entity):
        """Enregistre le climate switch pour une pièce."""
        self.switches[room_idx] = switch_entity
    
    async def on_vt_mode_change(self, room_idx: int, hvac_mode, target_temp: float):
        """Callback immédiat quand VT change de mode."""
        await self.update_loop()
    
    async def on_vt_temp_change(self, room_idx: int, new_temp: float):
        """Callback immédiat quand VT change de température."""
        await self.update_loop()

    def get_room_status(self, room_idx):
        return self.room_statuses.get(room_idx, {})

    def _notify_sensors(self):
        for sensor in self.sensors:
            sensor.update_from_manager()

    def _get_entity_value(self, entity_id):
        if not entity_id: return None
        state = self.hass.states.get(entity_id)
        if state and state.state not in ["unknown", "unavailable"]:
            try: return float(state.state)
            except ValueError: return None
        return None

    def _is_summer_mode(self):
        if not self.summer_mode_id: return False
        state = self.hass.states.get(self.summer_mode_id)
        if state and state.state == "on": return True
        return False

    def _get_active_tariff_index(self):
        if self.mode == MODE_SINGLE: return 1
        if not self.tariff_sensor: return 2
        state = self.hass.states.get(self.tariff_sensor)
        if not state or state.state in ["unknown", "unavailable"]: return 2
        val = str(state.state).strip().lower()
        if val in ["1", "1.0", "low", "night", "off_peak", "eco"]: return 1
        if val in ["2", "2.0", "normal", "day", "peak"]: return 2
        if val in ["3", "3.0", "high", "super_peak"]: return 3
        return 2

    def _get_current_prices(self):
        idx = self._get_active_tariff_index()
        price_cons = self._get_entity_value(self.config.get(self.map_cons_price.get(idx)))
        price_inj = self._get_entity_value(self.config.get(self.map_inj_price.get(idx)))
        return idx, price_cons, price_inj

    def _interpolate_cop(self, temp_ext, room_config):
        points = [
            (-15, room_config.get(CONF_COP_M15, 2.0)),
            (-7,  room_config.get(CONF_COP_M7, 2.5)),
            (0,   room_config.get(CONF_COP_0, 3.2)),
            (7,   room_config.get(CONF_COP_7, 4.0)),
            (15,  room_config.get(CONF_COP_15, 5.0))
        ]
        if temp_ext <= -15: return points[0][1]
        if temp_ext >= 15: return points[4][1]
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i+1]
            if x1 <= temp_ext <= x2: return y1 + (temp_ext - x1) * (y2 - y1) / (x2 - x1)
        return 4.0

    async def update_loop(self, now=None):
        """Boucle principale d'optimisation."""
        tariff_idx, prix_elec_cons, prix_elec_inj = self._get_current_prices()
        prix_gaz = self._get_entity_value(self.gaz_price_id)
        if not prix_gaz: prix_gaz = 0.085
        if prix_elec_cons is None: return

        temp_ext = self._get_entity_value(self.outside_temp_id)
        if temp_ext is None: temp_ext = 25.0

        is_summer = self._is_summer_mode()

        # Batterie
        soc = 0; has_battery = False; battery_forced = False
        thresh_val = self._get_entity_value(self.battery_thresh_id)
        if thresh_val is None: thresh_val = 30.0
        if self.battery_id:
            val = self._get_entity_value(self.battery_id)
            if val is not None:
                soc = val; has_battery = True
                if soc > thresh_val: battery_forced = True
        
        # Solaire
        effective_elec_price = prix_elec_cons
        is_solar_exporting = False
        grid_power = self._get_entity_value(self.grid_power_id)
        if grid_power is not None and grid_power < -500:
            if prix_elec_inj is not None:
                effective_elec_price = prix_elec_inj
                is_solar_exporting = True

        # ===== BOUCLE PIÈCES =====
        for idx, room in enumerate(self.rooms):
            clim_gaz = room.get(CONF_CLIMATE_GAZ)
            clim_ac = room.get(CONF_CLIMATE_AC)
            switch = self.switches.get(idx)
            
            # Si pas de switch (erreur init), on passe
            if not switch: continue

            requested_mode = switch.hvac_mode
            target_temp = switch.target_temperature
            current_temp = self._get_entity_value(room.get(CONF_TEMP_SENSOR))
            
            # Sécurité capteur
            if current_temp is None:
                if clim_ac: await self._set_climate(clim_ac, "off", None)
                if clim_gaz: await self._set_climate(clim_gaz, "off", None)
                switch.update_from_manager(None, HVACAction.OFF, "Sensor error")
                self.room_statuses[idx] = {"active_source": "Error", "reason": "Capteur HS"}
                continue
            
            # === VT DEMANDE OFF ===
            if requested_mode == HVACMode.OFF:
                if clim_ac: await self._set_climate(clim_ac, "off", None)
                if clim_gaz: await self._set_climate(clim_gaz, "off", None)
                switch.update_from_manager(current_temp, HVACAction.OFF, "VT OFF")
                self.room_statuses[idx] = {"active_source": "Off (VT)", "reason": "Versatile Thermostat: OFF"}
                continue
            
            # === TEMPÉRATURE ATTEINTE (Hystérésis gérée par EO en sécurité, mais VT le gère aussi) ===
            # On garde cette sécurité au cas où VT envoie Heat alors qu'il fait chaud
            if not is_summer and current_temp >= (target_temp + self.hysteresis):
                if clim_ac: await self._set_climate(clim_ac, "off", None)
                if clim_gaz: await self._set_climate(clim_gaz, "off", None)
                switch.update_from_manager(current_temp, HVACAction.IDLE, "Temp OK")
                self.room_statuses[idx] = {"active_source": "Off (Temp OK)", "reason": "Température atteinte"}
                continue
            
            # === VT DEMANDE CHAUFFAGE ===
            if requested_mode in [HVACMode.HEAT, HVACMode.HEAT_COOL] and not is_summer:
                should_heat_ac = False
                should_heat_gas = False
                reason = ""
                cop = 0
                cout_pac_kwh = 0
                
                # Calcul Rentabilité
                if clim_ac:
                    cop = self._interpolate_cop(temp_ext, room)
                    safe_cop = cop if cop > 0.1 else 0.1
                    cout_pac_kwh = effective_elec_price / safe_cop
                    
                    if battery_forced:
                        should_heat_ac = True; reason = f"Batterie ({soc}%)"
                    elif cout_pac_kwh < prix_gaz:
                        should_heat_ac = True
                        if is_solar_exporting: reason = f"Solaire ({grid_power}W)"
                        else: reason = f"PAC moins chère"
                    else:
                        should_heat_gas = True
                        reason = f"Gaz moins cher"
                
                # Disponibilité équipements
                if clim_ac and clim_gaz: pass
                elif clim_ac and not clim_gaz: should_heat_ac = True; reason = "PAC seule"
                elif clim_gaz and not clim_ac: should_heat_gas = True; reason = "Gaz seul"
                
                # Action
                if should_heat_ac:
                    await self._set_climate(clim_ac, "heat", target_temp)
                    if clim_gaz: await self._set_climate(clim_gaz, "off", None)
                    switch.update_from_manager(current_temp, HVACAction.HEATING, reason)
                    self.room_statuses[idx] = {"active_source": "AC (Heat)", "reason": reason, "cost_ac": cout_pac_kwh, "cost_gas": prix_gaz}
                
                elif should_heat_gas:
                    await self._set_climate(clim_gaz, "heat", target_temp)
                    if clim_ac: await self._set_climate(clim_ac, "off", None)
                    switch.update_from_manager(current_temp, HVACAction.HEATING, reason)
                    self.room_statuses[idx] = {"active_source": "Gaz", "reason": reason, "cost_ac": cout_pac_kwh, "cost_gas": prix_gaz}
            
            # === VT DEMANDE REFROIDISSEMENT (ÉTÉ) ===
            elif requested_mode in [HVACMode.COOL, HVACMode.HEAT_COOL] and is_summer:
                if not clim_ac:
                    switch.update_from_manager(current_temp, HVACAction.IDLE, "Pas d'AC")
                    self.room_statuses[idx] = {"active_source": "Off", "reason": "Pas d'AC"}
                
                elif current_temp > target_temp:
                    if is_solar_exporting or battery_forced:
                        reason = f"Solaire/Batterie"
                        await self._set_climate(clim_ac, "cool", target_temp)
                        switch.update_from_manager(current_temp, HVACAction.COOLING, reason)
                        self.room_statuses[idx] = {"active_source": "AC (Cooling)", "reason": reason}
                    else:
                        await self._set_climate(clim_ac, "off", None)
                        switch.update_from_manager(current_temp, HVACAction.IDLE, "Attente Solaire")
                        self.room_statuses[idx] = {"active_source": "Off", "reason": "Attente Solaire"}
                else:
                    await self._set_climate(clim_ac, "off", None)
                    switch.update_from_manager(current_temp, HVACAction.IDLE, "Temp OK")
                    self.room_statuses[idx] = {"active_source": "Off", "reason": "Temp OK"}

        self._notify_sensors()

    async def _set_climate(self, entity_id, mode, temp):
        state = self.hass.states.get(entity_id)
        if not state or state.state in ["unavailable", "unknown"]: return
        
        try:
            if state.state != mode:
                await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": mode})
            if mode in ["heat", "cool"] and temp is not None:
                current_target = state.attributes.get("temperature", 0)
                if float(current_target) != temp:
                    await self.hass.services.async_call("climate", "set_temperature", {"entity_id": entity_id, "temperature": temp})
        except Exception as e:
            _LOGGER.error(f"❌ Failed to control {entity_id}: {e}")