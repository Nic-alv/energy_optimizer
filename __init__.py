# /config/custom_components/energy_optimizer/__init__.py

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from datetime import timedelta, time
from .const import *

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=1)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    hass.data.setdefault(DOMAIN, {})
    manager = EnergyManager(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = manager
    entry.async_on_unload(entry.add_update_listener(update_listener))
    entry.async_on_unload(async_track_time_interval(hass, manager.update_loop, SCAN_INTERVAL))
    return True

async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    hass.data[DOMAIN].pop(entry.entry_id)
    return True

class EnergyManager:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.config = entry.data
        self.options = entry.options

        # Global Config
        self.mode = self.config.get(CONF_TARIFF_MODE, MODE_SINGLE)
        self.tariff_sensor = self.config.get(CONF_TARIFF_SENSOR)
        self.gaz_price_id = self.config[CONF_GAZ_PRICE_ENTITY]
        self.battery_id = self.config.get(CONF_BATTERY_ENTITY)
        self.outside_temp_id = self.config.get(CONF_OUTSIDE_TEMP_ENTITY)
        
        # Liste des Pièces
        self.rooms = self.options.get(CONF_ROOMS, [])

        self.map_cons_price = {1: CONF_PRICE_T1, 2: CONF_PRICE_T2, 3: CONF_PRICE_T3}
        self.map_inj_price = {1: CONF_INJ_PRICE_T1, 2: CONF_INJ_PRICE_T2, 3: CONF_INJ_PRICE_T3}

    # ... (Garde ici les méthodes _get_active_tariff_index, _get_entity_value, _get_current_prices des messages précédents) ...
    # Je ne les remets pas pour raccourcir, mais elles sont indispensables !
    
    def _interpolate_cop(self, temp_ext, room_config):
        """Calcule le COP exact par interpolation linéaire."""
        # Points définis par l'utilisateur
        points = [
            (-15, room_config.get(CONF_COP_M15, 2.0)),
            (-7,  room_config.get(CONF_COP_M7, 2.5)),
            (0,   room_config.get(CONF_COP_0, 3.2)),
            (7,   room_config.get(CONF_COP_7, 4.0)),
            (15,  room_config.get(CONF_COP_15, 5.0))
        ]
        
        # Si T < -15, on prend la valeur de -15
        if temp_ext <= -15: return points[0][1]
        # Si T > 15, on prend la valeur de 15
        if temp_ext >= 15: return points[4][1]

        # Recherche de l'intervalle
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i+1]
            
            if x1 <= temp_ext <= x2:
                # Formule : y = y1 + (x - x1) * (y2 - y1) / (x2 - x1)
                return y1 + (temp_ext - x1) * (y2 - y1) / (x2 - x1)
        
        return 4.0 # Fallback

    def _get_target_temp_for_room(self, room):
        """Vérifie le planning de la pièce."""
        now = dt_util.now().time()
        
        # Récupération des heures (format string "HH:MM:SS" stocké dans options)
        start_str = room.get(CONF_START_TIME, "07:00:00")
        end_str = room.get(CONF_END_TIME, "22:00:00")
        
        # Conversion string -> time object (un peu tricky en Python)
        try:
            start_t = time.fromisoformat(start_str)
            end_t = time.fromisoformat(end_str)
        except:
            start_t = time(7,0)
            end_t = time(22,0)

        # Vérification plage
        in_comfort = False
        if start_t <= end_t:
            in_comfort = start_t <= now <= end_t
        else: # Cas ou on passe minuit (ex: 18h -> 06h)
            in_comfort = start_t <= now or now <= end_t

        return room.get(CONF_COMFORT_TEMP, 21.0) if in_comfort else room.get(CONF_ECO_TEMP, 18.0)

    async def update_loop(self, now=None):
        """Boucle Principale Multi-Pièces."""
        
        # 1. Données Globales
        tariff_idx, prix_elec_cons, prix_elec_inj = self._get_current_prices()
        prix_gaz = self._get_entity_value(self.gaz_price_id)
        if not prix_gaz: prix_gaz = 0.085
        if prix_elec_cons is None: return

        temp_ext = self._get_entity_value(self.outside_temp_id)
        if temp_ext is None: temp_ext = 7.0 # Default

        # Batterie
        soc = 0
        has_battery = False
        if self.battery_id:
            val = self._get_entity_value(self.battery_id)
            if val is not None: soc, has_battery = val, True

        # 2. Boucle sur les Pièces
        for room in self.rooms:
            room_name = room.get(CONF_ROOM_NAME, "Inconnue")
            clim_gaz = room.get(CONF_CLIMATE_GAZ)
            clim_ac = room.get(CONF_CLIMATE_AC)
            
            # Si aucun thermostat configuré, on passe
            if not clim_gaz and not clim_ac: continue

            # A. Calcul Consigne (Scheduling)
            target_temp = self._get_target_temp_for_room(room)

            # B. Calcul Rentabilité (Seulement si AC existe)
            use_ac = False
            cop = 4.0
            
            if clim_ac:
                cop = self._interpolate_cop(temp_ext, room)
                cout_pac = prix_elec_cons / cop
                
                # Critères : Rentable OU Batterie
                if cout_pac < prix_gaz:
                    use_ac = True
                elif has_battery and soc > 30:
                    use_ac = True
            
            # C. Application des Commandes
            
            # Cas 1 : On doit chauffer à l'AC
            if use_ac and clim_ac:
                # AC ON
                await self._set_climate(clim_ac, "heat", target_temp)
                # Gaz OFF
                if clim_gaz: await self._set_climate(clim_gaz, "off", None)
                _LOGGER.debug(f"[{room_name}] Mode AC (COP {cop:.2f}) | Cible {target_temp}°C")

            # Cas 2 : On doit chauffer au Gaz (ou AC pas dispo)
            elif clim_gaz:
                # Gaz ON (Mode Heat)
                await self._set_climate(clim_gaz, "heat", target_temp)
                # AC OFF
                if clim_ac: await self._set_climate(clim_ac, "off", None)
                _LOGGER.debug(f"[{room_name}] Mode Gaz | Cible {target_temp}°C")

            # Cas 3 : Ni AC ni Gaz (Pas possible si config OK, mais sécurité)
            else:
                pass 

    async def _set_climate(self, entity_id, mode, temp):
        """Helper pour éviter de spammer les appels."""
        state = self.hass.states.get(entity_id)
        if not state: return

        # Check mode
        if state.state != mode:
            await self.hass.services.async_call(
                "climate", "set_hvac_mode",
                {"entity_id": entity_id, "hvac_mode": mode}
            )
        
        # Check temp (seulement si mode heat)
        if mode == "heat" and temp is not None:
            try:
                current_setpoint = float(state.attributes.get("temperature", 0))
                if current_setpoint != temp:
                    await self.hass.services.async_call(
                        "climate", "set_temperature",
                        {"entity_id": entity_id, "temperature": temp}
                    )
            except: pass