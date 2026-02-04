import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from datetime import timedelta
from .const import *

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=1)

PLATFORMS = ["sensor"] 

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

        self.mode = self.config.get(CONF_TARIFF_MODE, MODE_SINGLE)
        self.tariff_sensor = self.config.get(CONF_TARIFF_SENSOR)
        self.gaz_price_id = self.config[CONF_GAZ_PRICE_ENTITY]
        self.battery_id = self.config.get(CONF_BATTERY_ENTITY)
        self.outside_temp_id = self.config.get(CONF_OUTSIDE_TEMP_ENTITY)
        
        self.grid_power_id = self.options.get(CONF_GRID_POWER_ENTITY, self.config.get(CONF_GRID_POWER_ENTITY))
        self.battery_thresh_id = self.options.get(CONF_BATTERY_THRESH_ENTITY)

        self.rooms = self.options.get(CONF_ROOMS, [])
        self.map_cons_price = {1: CONF_PRICE_T1, 2: CONF_PRICE_T2, 3: CONF_PRICE_T3}
        self.map_inj_price = {1: CONF_INJ_PRICE_T1, 2: CONF_INJ_PRICE_T2, 3: CONF_INJ_PRICE_T3}

    def register_sensor(self, sensor):
        self.sensors.append(sensor)

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

    def _get_active_tariff_index(self):
        if self.mode == MODE_SINGLE: return 1
        if not self.tariff_sensor:
            now = dt_util.now()
            if now.weekday() >= 5 or now.hour < 7 or now.hour >= 22: return 1
            return 2
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

    def _get_target_temp_for_room(self, room):
        target_entity = room.get(CONF_TARGET_TEMP_ENTITY)
        val = self._get_entity_value(target_entity)
        if val is None: return 20.0
        return val

    async def update_loop(self, now=None):
        tariff_idx, prix_elec_cons, prix_elec_inj = self._get_current_prices()
        prix_gaz = self._get_entity_value(self.gaz_price_id)
        if not prix_gaz: prix_gaz = 0.085
        if prix_elec_cons is None: return

        temp_ext = self._get_entity_value(self.outside_temp_id)
        if temp_ext is None: temp_ext = 7.0

        # --- GESTION BATTERIE & SOLAIRE ---
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
        
        effective_elec_price = prix_elec_cons
        is_solar_exporting = False
        grid_power = self._get_entity_value(self.grid_power_id)
        if grid_power is not None and grid_power < -500:
             if prix_elec_inj is not None:
                 effective_elec_price = prix_elec_inj
                 is_solar_exporting = True

        # --- BOUCLE PIÈCES ---
        for idx, room in enumerate(self.rooms):
            clim_gaz = room.get(CONF_CLIMATE_GAZ)
            clim_ac = room.get(CONF_CLIMATE_AC)
            target_temp = self._get_target_temp_for_room(room)
            
            # État initial
            cop = 0
            cout_pac_kwh = 0
            active_source = "Off"
            reason = "Aucun chauffage"
            
            # Variables de décision
            should_heat_ac = False
            should_heat_gas = False
            ac_is_cheaper = False # Est-ce que la PAC est moins chère que le gaz ?

            # 1. ANALYSE PAC (Si elle existe)
            if clim_ac:
                cop = self._interpolate_cop(temp_ext, room)
                safe_cop = cop if cop > 0.1 else 0.1
                cout_pac_kwh = effective_elec_price / safe_cop
                
                # Est-ce que la PAC est financièrement intéressante ?
                if battery_forced:
                    ac_is_cheaper = True
                    reason = f"Batterie pleine ({soc}%)"
                elif cout_pac_kwh < prix_gaz:
                    ac_is_cheaper = True
                    if is_solar_exporting: reason = f"Solaire (Export {grid_power}W)"
                    else: reason = f"PAC moins chère ({cout_pac_kwh:.3f}€ < {prix_gaz}€)"
                else:
                    ac_is_cheaper = False
                    reason = f"Gaz moins cher ({prix_gaz}€ < {cout_pac_kwh:.3f}€)"

            # 2. PRISE DE DÉCISION (Logique à 3 cas)
            
            # CAS A : Les deux chauffages existent
            if clim_ac and clim_gaz:
                if ac_is_cheaper:
                    should_heat_ac = True
                else:
                    should_heat_gas = True
            
            # CAS B : Seulement l'AC existe
            elif clim_ac and not clim_gaz:
                should_heat_ac = True
                if not ac_is_cheaper:
                    reason = f"Seule source dispo (AC) - Coût: {cout_pac_kwh:.3f}€"
            
            # CAS C : Seulement le Gaz existe
            elif clim_gaz and not clim_ac:
                should_heat_gas = True
                reason = "Seule source dispo (Gaz)"

            # 3. APPLICATION
            if should_heat_ac:
                active_source = "AC (Toshiba)"
                await self._set_climate(clim_ac, "heat", target_temp)
                if clim_gaz: await self._set_climate(clim_gaz, "off", None)
            
            elif should_heat_gas:
                active_source = "Gaz"
                await self._set_climate(clim_gaz, "heat", target_temp)
                if clim_ac: await self._set_climate(clim_ac, "off", None)
            
            # Sauvegarde Sensor
            self.room_statuses[idx] = {
                "active_source": active_source,
                "target_temp": target_temp,
                "cop": round(cop, 2) if clim_ac else 0,
                "cost_ac": round(cout_pac_kwh, 4) if clim_ac else 0,
                "cost_gas": round(prix_gaz, 4),
                "profitable": ac_is_cheaper, # Note: ceci indique juste si AC est plus rentable que Gaz
                "outside_temp": temp_ext,
                "reason": reason
            }

        self._notify_sensors()

    async def _set_climate(self, entity_id, mode, temp):
        state = self.hass.states.get(entity_id)
        if not state: return
        if state.state != mode:
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": mode})
        if mode == "heat" and temp is not None:
            try:
                if float(state.attributes.get("temperature", 0)) != temp:
                    await self.hass.services.async_call("climate", "set_temperature", {"entity_id": entity_id, "temperature": temp})
            except: pass