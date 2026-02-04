# /config/custom_components/energy_optimizer/__init__.py

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.components.climate.const import HVACMode
from datetime import timedelta, datetime
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
        self.virtual_climates = {} 
        self.room_statuses = {} 
        
        # Tracking du temps de fonctionnement AC (anti-cyclage)
        self.ac_start_times = {}  # {room_idx: datetime ou None} 

        self.mode = self.config.get(CONF_TARIFF_MODE, MODE_SINGLE)
        self.tariff_sensor = self.config.get(CONF_TARIFF_SENSOR)
        self.gaz_price_id = self.config[CONF_GAZ_PRICE_ENTITY]
        self.battery_id = self.config.get(CONF_BATTERY_ENTITY)
        self.outside_temp_id = self.config.get(CONF_OUTSIDE_TEMP_ENTITY)
        
        self.grid_power_id = self.options.get(CONF_GRID_POWER_ENTITY, self.config.get(CONF_GRID_POWER_ENTITY))
        self.battery_thresh_id = self.options.get(CONF_BATTERY_THRESH_ENTITY)
        self.hysteresis = self.options.get(CONF_HYSTERESIS, 0.5)
        
        # NOUVEAU : Switch Mode Été
        self.summer_mode_id = self.options.get(CONF_SUMMER_MODE_ENTITY)

        self.rooms = self.options.get(CONF_ROOMS, [])
        self.map_cons_price = {1: CONF_PRICE_T1, 2: CONF_PRICE_T2, 3: CONF_PRICE_T3}
        self.map_inj_price = {1: CONF_INJ_PRICE_T1, 2: CONF_INJ_PRICE_T2, 3: CONF_INJ_PRICE_T3}

    def register_sensor(self, sensor):
        self.sensors.append(sensor)

    def register_virtual_climate(self, room_idx, climate_entity):
        self.virtual_climates[room_idx] = climate_entity

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

    def _can_stop_ac(self, room_idx, room_config):
        """Vérifie si l'AC peut s'arrêter (délai minimum respecté)."""
        start_time = self.ac_start_times.get(room_idx)
        if start_time is None:
            return True  # Pas démarrée, peut rester off
        
        min_runtime = room_config.get(CONF_AC_MIN_RUNTIME, 5)  # Défaut 5 minutes
        elapsed = (datetime.now() - start_time).total_seconds() / 60  # en minutes
        
        return elapsed >= min_runtime

    def _is_summer_mode(self):
        """Vérifie si le switch Été est activé."""
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
        tariff_idx, prix_elec_cons, prix_elec_inj = self._get_current_prices()
        prix_gaz = self._get_entity_value(self.gaz_price_id)
        if not prix_gaz: prix_gaz = 0.085
        if prix_elec_cons is None: return

        temp_ext = self._get_entity_value(self.outside_temp_id)
        if temp_ext is None: temp_ext = 25.0

        is_summer = self._is_summer_mode()

        # Batterie
        soc = 0; has_battery = False
        battery_forced = False
        thresh_val = self._get_entity_value(self.battery_thresh_id)
        if thresh_val is None: thresh_val = 30.0
        if self.battery_id:
            val = self._get_entity_value(self.battery_id)
            if val is not None:
                soc = val
                has_battery = True
                if soc > thresh_val: battery_forced = True
        
        # Solaire (Production excédentaire)
        effective_elec_price = prix_elec_cons
        is_solar_exporting = False
        grid_power = self._get_entity_value(self.grid_power_id)
        # On considère qu'il y a assez de soleil si on injecte > 500W
        if grid_power is not None and grid_power < -500:
             if prix_elec_inj is not None:
                 effective_elec_price = prix_elec_inj
                 is_solar_exporting = True

        # BOUCLE PIÈCES
        for idx, room in enumerate(self.rooms):
            clim_gaz = room.get(CONF_CLIMATE_GAZ)
            clim_ac = room.get(CONF_CLIMATE_AC)
            
            v_climate = self.virtual_climates.get(idx)
            if not v_climate: continue
            
            target_temp = v_climate.target_temperature
            current_temp = self._get_entity_value(room.get(CONF_TEMP_SENSOR))

            # Vérification capteur température
            if current_temp is None:
                room_name = room.get(CONF_ROOM_NAME, f"Pièce {idx}")
                _LOGGER.warning(
                    f"Room '{room_name}': Capteur de température indisponible ou invalide. "
                    f"Passage en mode sécurité (chauffage désactivé)."
                )
                # Désactive tous les chauffages par sécurité
                if clim_ac:
                    await self._set_climate(clim_ac, "off", None)
                if clim_gaz:
                    await self._set_climate(clim_gaz, "off", None)
                v_climate.update_action_from_manager(False)
                
                # Mise à jour du sensor avec info erreur
                self.room_statuses[idx] = {
                    "active_source": "Error",
                    "target_temp": target_temp,
                    "outside_temp": temp_ext,
                    "reason": "Capteur température indisponible"
                }
                continue  # Passe à la pièce suivante
            
            # Gestion de l'affichage du thermostat virtuel (Mode forcé selon saison)
            if is_summer and v_climate.hvac_mode != HVACMode.OFF:
                 if v_climate.hvac_mode != HVACMode.COOL:
                     await v_climate.async_set_hvac_mode(HVACMode.COOL)
            elif not is_summer and v_climate.hvac_mode != HVACMode.OFF:
                 if v_climate.hvac_mode != HVACMode.HEAT:
                     await v_climate.async_set_hvac_mode(HVACMode.HEAT)

            is_on = v_climate.hvac_mode in [HVACMode.HEAT, HVACMode.COOL]
            
            active_source = "Off"
            reason = "Off"
            profitable = False

            # === SI THERMOSTAT ÉTEINT ===
            if not is_on:
                if clim_ac: await self._set_climate(clim_ac, "off", None)
                if clim_gaz: await self._set_climate(clim_gaz, "off", None)
                v_climate.update_action_from_manager(False)

            # === LOGIQUE ÉTÉ (REFROIDISSEMENT) ===
            elif is_summer:
                # Gaz toujours OFF en été
                if clim_gaz: await self._set_climate(clim_gaz, "off", None)
                
                if not clim_ac:
                    reason = "Pas d'AC dans cette pièce"
                    v_climate.update_action_from_manager(False)
                
                # Besoin de froid ? (Temp > Target)
                elif current_temp is not None and current_temp > target_temp:
                    # CONDITION SOLAIRE / BATTERIE (Strictement demandé)
                    if is_solar_exporting or battery_forced:
                        active_source = "AC (Cooling)"
                        reason = f"Solaire dispo (Export {grid_power}W) ou Bat ({soc}%)"
                        await self._set_climate(clim_ac, "cool", target_temp)
                        v_climate.update_action_from_manager(True)
                        # Enregistrer le démarrage
                        if self.ac_start_times.get(idx) is None:
                            self.ac_start_times[idx] = datetime.now()
                    else:
                        # Pas assez de soleil -> On coupe pour économiser
                        active_source = "Attente Soleil"
                        reason = "Pas assez de production solaire"
                        await self._set_climate(clim_ac, "off", None)
                        v_climate.update_action_from_manager(False)
                        self.ac_start_times[idx] = None
                else:
                    # Température cible atteinte - Vérifier délai minimum
                    if not self._can_stop_ac(idx, room):
                        # AC doit continuer (délai non écoulé)
                        min_runtime = room.get(CONF_AC_MIN_RUNTIME, 5)
                        elapsed = (datetime.now() - self.ac_start_times[idx]).total_seconds() / 60
                        active_source = "AC (Cooling - Min Runtime)"
                        reason = f"Délai minimum ({elapsed:.1f}/{min_runtime} min)"
                        await self._set_climate(clim_ac, "cool", target_temp)
                        v_climate.update_action_from_manager(True)
                    else:
                        # Délai respecté, on peut couper
                        active_source = "Temp OK"
                        reason = f"Cible atteinte ({current_temp} <= {target_temp})"
                        await self._set_climate(clim_ac, "off", None)
                        v_climate.update_action_from_manager(False)
                        self.ac_start_times[idx] = None

            # === LOGIQUE HIVER (CHAUFFAGE) ===
            else:
                # Hystérésis Chauffage
                if current_temp is not None and current_temp >= (target_temp + self.hysteresis):
                    # Vérifier si AC est active et si délai minimum est respecté
                    ac_state = self.hass.states.get(clim_ac) if clim_ac else None
                    ac_is_running = ac_state and ac_state.state in ["heat", "cool"]
                    
                    if ac_is_running and not self._can_stop_ac(idx, room):
                        # AC doit continuer (délai non écoulé)
                        min_runtime = room.get(CONF_AC_MIN_RUNTIME, 5)
                        elapsed = (datetime.now() - self.ac_start_times[idx]).total_seconds() / 60
                        active_source = "AC (Heat - Min Runtime)"
                        reason = f"Délai minimum ({elapsed:.1f}/{min_runtime} min)"
                        v_climate.update_action_from_manager(True)
                    else:
                        # Peut couper normalement
                        if clim_ac: await self._set_climate(clim_ac, "off", None)
                        if clim_gaz: await self._set_climate(clim_gaz, "off", None)
                        v_climate.update_action_from_manager(False)
                        active_source = "Off (Temp OK)"
                        reason = f"Cible atteinte (+hystérésis)"
                        self.ac_start_times[idx] = None
                
                else:
                    # Calcul rentabilité (Code Hiver existant)
                    should_heat_ac = False; should_heat_gas = False; ac_is_cheaper = False
                    cop = 0; cout_pac_kwh = 0

                    if clim_ac:
                        cop = self._interpolate_cop(temp_ext, room)
                        safe_cop = cop if cop > 0.1 else 0.1
                        cout_pac_kwh = effective_elec_price / safe_cop
                        
                        if battery_forced:
                            ac_is_cheaper = True
                            reason = f"Batterie pleine ({soc}%)"
                        elif cout_pac_kwh < prix_gaz:
                            ac_is_cheaper = True
                            if is_solar_exporting: reason = f"Solaire (Export {grid_power}W)"
                            else: reason = f"PAC moins chère"
                        else:
                            ac_is_cheaper = False
                            reason = f"Gaz moins cher"

                    if clim_ac and clim_gaz:
                        if ac_is_cheaper: should_heat_ac = True
                        else: should_heat_gas = True
                    elif clim_ac and not clim_gaz:
                        should_heat_ac = True
                    elif clim_gaz and not clim_ac:
                        should_heat_gas = True

                    if should_heat_ac:
                        active_source = "AC (Heat)"
                        await self._set_climate(clim_ac, "heat", target_temp)
                        if clim_gaz: await self._set_climate(clim_gaz, "off", None)
                        v_climate.update_action_from_manager(True)
                        # Enregistrer le démarrage
                        if self.ac_start_times.get(idx) is None:
                            self.ac_start_times[idx] = datetime.now()
                    elif should_heat_gas:
                        active_source = "Gaz"
                        await self._set_climate(clim_gaz, "heat", target_temp)
                        if clim_ac: await self._set_climate(clim_ac, "off", None)
                        v_climate.update_action_from_manager(True)
                        self.ac_start_times[idx] = None  # Reset si on passe au gaz
            
            # Sauvegarde Sensor
            self.room_statuses[idx] = {
                "active_source": active_source,
                "target_temp": target_temp,
                "outside_temp": temp_ext,
                "reason": reason
            }

        self._notify_sensors()

    async def _set_climate(self, entity_id, mode, temp):
        state = self.hass.states.get(entity_id)
        if not state: return
        if state.state != mode:
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": mode})
        if mode in ["heat", "cool"] and temp is not None:
            try:
                if float(state.attributes.get("temperature", 0)) != temp:
                    await self.hass.services.async_call("climate", "set_temperature", {"entity_id": entity_id, "temperature": temp})
            except: pass