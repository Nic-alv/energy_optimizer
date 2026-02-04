# /config/custom_components/energy_optimizer/__init__.py
# VERSION POC - Intégration Versatile Thermostat + Anti-cyclage AC

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.components.climate.const import HVACMode, HVACAction
from datetime import timedelta, datetime
from .const import *

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=1)

PLATFORMS = ["climate", "sensor"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Setup de l'intégration avec support POC Versatile Thermostat."""
    hass.data.setdefault(DOMAIN, {})
    manager = EnergyManager(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = manager
    
    # Setup platforms standards
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    entry.async_on_unload(entry.add_update_listener(update_listener))
    entry.async_on_unload(async_track_time_interval(hass, manager.update_loop, SCAN_INTERVAL))
    
    _LOGGER.info("✅ Energy Optimizer POC setup completed")
    return True

async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class EnergyManager:
    """Gestionnaire principal Energy Optimizer avec support POC Versatile Thermostat."""
    
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
        
        # ===== NOUVEAU POC : Support climate switches =====
        self.switches = {}  # {room_idx: EnergyOptimizerSwitch}
        # ==================================================

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
        """Enregistrer un sensor."""
        self.sensors.append(sensor)

    def register_virtual_climate(self, room_idx, climate_entity):
        """Enregistrer un thermostat virtuel (ancien système)."""
        self.virtual_climates[room_idx] = climate_entity

    # ===== NOUVELLES MÉTHODES POC =====
    
    def register_switch(self, room_idx: int, switch_entity):
        """
        Enregistrer le climate switch pour une pièce (POC Versatile Thermostat).
        
        Ce switch reçoit les ordres de VT et notifie le manager.
        """
        self.switches[room_idx] = switch_entity
        _LOGGER.info(f"✅ Registered VT switch for room {room_idx}")
    
    async def on_vt_mode_change(self, room_idx: int, hvac_mode, target_temp: float):
        """
        Callback appelé quand VT change le mode via le switch.
        
        Déclenche immédiatement un update_loop pour réagir.
        """
        _LOGGER.info(
            f"🔔 VT mode change detected for room {room_idx}: "
            f"mode={hvac_mode}, target={target_temp}°C"
        )
        
        # Déclencher immédiatement une mise à jour
        await self.update_loop()
    
    async def on_vt_temp_change(self, room_idx: int, new_temp: float):
        """
        Callback appelé quand VT change la température cible.
        
        Déclenche un update_loop pour ajuster.
        """
        _LOGGER.info(
            f"🌡️ VT temp change detected for room {room_idx}: {new_temp}°C"
        )
        
        # Déclencher une mise à jour
        await self.update_loop()
    
    # ===== FIN NOUVELLES MÉTHODES POC =====

    def get_room_status(self, room_idx):
        """Récupérer le status d'une pièce."""
        return self.room_statuses.get(room_idx, {})

    def _notify_sensors(self):
        """Notifier tous les sensors d'une mise à jour."""
        for sensor in self.sensors:
            sensor.update_from_manager()

    def _get_entity_value(self, entity_id):
        """Récupérer la valeur numérique d'une entité."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state and state.state not in ["unknown", "unavailable"]:
            try:
                return float(state.state)
            except ValueError:
                return None
        return None

    def _can_stop_ac(self, room_idx, room_config):
        """
        Vérifie si l'AC peut s'arrêter (délai minimum respecté).
        
        Protection anti-cyclage du compresseur.
        """
        start_time = self.ac_start_times.get(room_idx)
        if start_time is None:
            return True  # Pas démarrée, peut rester off
        
        min_runtime = room_config.get(CONF_AC_MIN_RUNTIME, 5)  # Défaut 5 minutes
        elapsed = (datetime.now() - start_time).total_seconds() / 60  # en minutes
        
        return elapsed >= min_runtime

    def _is_summer_mode(self):
        """Vérifie si le switch Été est activé."""
        if not self.summer_mode_id:
            return False
        state = self.hass.states.get(self.summer_mode_id)
        if state and state.state == "on":
            return True
        return False

    def _get_active_tariff_index(self):
        """Détermine l'index tarifaire actif (1=HC, 2=Normal, 3=Pointe)."""
        if self.mode == MODE_SINGLE:
            return 1
        if not self.tariff_sensor:
            return 2
        state = self.hass.states.get(self.tariff_sensor)
        if not state or state.state in ["unknown", "unavailable"]:
            return 2
        val = str(state.state).strip().lower()
        if val in ["1", "1.0", "low", "night", "off_peak", "eco"]:
            return 1
        if val in ["2", "2.0", "normal", "day", "peak"]:
            return 2
        if val in ["3", "3.0", "high", "super_peak"]:
            return 3
        return 2

    def _get_current_prices(self):
        """Récupère les prix électricité actuels (consommation + injection)."""
        idx = self._get_active_tariff_index()
        price_cons = self._get_entity_value(self.config.get(self.map_cons_price.get(idx)))
        price_inj = self._get_entity_value(self.config.get(self.map_inj_price.get(idx)))
        return idx, price_cons, price_inj

    def _interpolate_cop(self, temp_ext, room_config):
        """Calcule le COP de la PAC selon température extérieure (interpolation linéaire)."""
        points = [
            (-15, room_config.get(CONF_COP_M15, 2.0)),
            (-7,  room_config.get(CONF_COP_M7, 2.5)),
            (0,   room_config.get(CONF_COP_0, 3.2)),
            (7,   room_config.get(CONF_COP_7, 4.0)),
            (15,  room_config.get(CONF_COP_15, 5.0))
        ]
        if temp_ext <= -15:
            return points[0][1]
        if temp_ext >= 15:
            return points[4][1]
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i+1]
            if x1 <= temp_ext <= x2:
                return y1 + (temp_ext - x1) * (y2 - y1) / (x2 - x1)
        return 4.0

    async def update_loop(self, now=None):
        """
        Boucle principale d'optimisation - s'exécute toutes les minutes.
        
        Modifiée pour supporter à la fois :
        - Thermostats virtuels (ancien système)
        - Climate switches VT (nouveau POC)
        """
        # Récupération des données globales
        tariff_idx, prix_elec_cons, prix_elec_inj = self._get_current_prices()
        prix_gaz = self._get_entity_value(self.gaz_price_id)
        if not prix_gaz:
            prix_gaz = 0.085
        if prix_elec_cons is None:
            return

        temp_ext = self._get_entity_value(self.outside_temp_id)
        if temp_ext is None:
            temp_ext = 25.0

        is_summer = self._is_summer_mode()

        # Batterie
        soc = 0
        has_battery = False
        battery_forced = False
        thresh_val = self._get_entity_value(self.battery_thresh_id)
        if thresh_val is None:
            thresh_val = 30.0
        if self.battery_id:
            val = self._get_entity_value(self.battery_id)
            if val is not None:
                soc = val
                has_battery = True
                if soc > thresh_val:
                    battery_forced = True
        
        # Solaire (Production excédentaire)
        effective_elec_price = prix_elec_cons
        is_solar_exporting = False
        grid_power = self._get_entity_value(self.grid_power_id)
        if grid_power is not None and grid_power < -500:
            if prix_elec_inj is not None:
                effective_elec_price = prix_elec_inj
                is_solar_exporting = True

        # ===== BOUCLE PIÈCES - MODIFIÉE POUR POC =====
        for idx, room in enumerate(self.rooms):
            clim_gaz = room.get(CONF_CLIMATE_GAZ)
            clim_ac = room.get(CONF_CLIMATE_AC)
            
            # POC : Vérifier si cette pièce a un switch (= contrôlée par VT)
            switch = self.switches.get(idx)
            
            # ===== CAS 1 : ANCIEN SYSTÈME (Thermostat Virtuel) =====
            if not switch:
                v_climate = self.virtual_climates.get(idx)
                if not v_climate:
                    continue
                
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
                
                # Gestion affichage thermostat virtuel (mode forcé selon saison)
                if is_summer and v_climate.hvac_mode != HVACMode.OFF:
                    if v_climate.hvac_mode != HVACMode.COOL:
                        await v_climate.async_set_hvac_mode(HVACMode.COOL)
                elif not is_summer and v_climate.hvac_mode != HVACMode.OFF:
                    if v_climate.hvac_mode != HVACMode.HEAT:
                        await v_climate.async_set_hvac_mode(HVACMode.HEAT)

                is_on = v_climate.hvac_mode in [HVACMode.HEAT, HVACMode.COOL]
                
                active_source = "Off"
                reason = "Off"

                # === SI THERMOSTAT ÉTEINT ===
                if not is_on:
                    if clim_ac:
                        await self._set_climate(clim_ac, "off", None)
                    if clim_gaz:
                        await self._set_climate(clim_gaz, "off", None)
                    v_climate.update_action_from_manager(False)
                    self.ac_start_times[idx] = None

                # === LOGIQUE ÉTÉ ===
                elif is_summer:
                    if clim_gaz:
                        await self._set_climate(clim_gaz, "off", None)
                    
                    if not clim_ac:
                        reason = "Pas d'AC dans cette pièce"
                        v_climate.update_action_from_manager(False)
                    elif current_temp > target_temp:
                        if is_solar_exporting or battery_forced:
                            active_source = "AC (Cooling)"
                            reason = f"Solaire dispo (Export {grid_power}W) ou Bat ({soc}%)"
                            await self._set_climate(clim_ac, "cool", target_temp)
                            v_climate.update_action_from_manager(True)
                            # Enregistrer le démarrage
                            if self.ac_start_times.get(idx) is None:
                                self.ac_start_times[idx] = datetime.now()
                        else:
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

                # === LOGIQUE HIVER ===
                else:
                    if current_temp >= (target_temp + self.hysteresis):
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
                            if clim_ac:
                                await self._set_climate(clim_ac, "off", None)
                            if clim_gaz:
                                await self._set_climate(clim_gaz, "off", None)
                            v_climate.update_action_from_manager(False)
                            active_source = "Off (Temp OK)"
                            reason = f"Cible atteinte (+hystérésis)"
                            self.ac_start_times[idx] = None
                    else:
                        should_heat_ac = False
                        should_heat_gas = False
                        ac_is_cheaper = False
                        cop = 0
                        cout_pac_kwh = 0

                        if clim_ac:
                            cop = self._interpolate_cop(temp_ext, room)
                            safe_cop = cop if cop > 0.1 else 0.1
                            cout_pac_kwh = effective_elec_price / safe_cop
                            
                            if battery_forced:
                                ac_is_cheaper = True
                                reason = f"Batterie pleine ({soc}%)"
                            elif cout_pac_kwh < prix_gaz:
                                ac_is_cheaper = True
                                if is_solar_exporting:
                                    reason = f"Solaire (Export {grid_power}W)"
                                else:
                                    reason = f"PAC moins chère"
                            else:
                                ac_is_cheaper = False
                                reason = f"Gaz moins cher"

                        if clim_ac and clim_gaz:
                            if ac_is_cheaper:
                                should_heat_ac = True
                            else:
                                should_heat_gas = True
                        elif clim_ac and not clim_gaz:
                            should_heat_ac = True
                        elif clim_gaz and not clim_ac:
                            should_heat_gas = True

                        if should_heat_ac:
                            active_source = "AC (Heat)"
                            await self._set_climate(clim_ac, "heat", target_temp)
                            if clim_gaz:
                                await self._set_climate(clim_gaz, "off", None)
                            v_climate.update_action_from_manager(True)
                            # Enregistrer le démarrage
                            if self.ac_start_times.get(idx) is None:
                                self.ac_start_times[idx] = datetime.now()
                        elif should_heat_gas:
                            active_source = "Gaz"
                            await self._set_climate(clim_gaz, "heat", target_temp)
                            if clim_ac:
                                await self._set_climate(clim_ac, "off", None)
                            v_climate.update_action_from_manager(True)
                            self.ac_start_times[idx] = None  # Reset si on passe au gaz
                
                # Sauvegarde status
                self.room_statuses[idx] = {
                    "active_source": active_source,
                    "target_temp": target_temp,
                    "outside_temp": temp_ext,
                    "reason": reason
                }
                continue
            
            # ===== CAS 2 : NOUVEAU SYSTÈME POC (Versatile Thermostat) =====
            _LOGGER.debug(f"🔄 Processing room {idx} with VT control")
            
            # Récupérer l'état du switch (qui reçoit les ordres de VT)
            requested_mode = switch.hvac_mode
            target_temp = switch.target_temperature
            current_temp = self._get_entity_value(room.get(CONF_TEMP_SENSOR))
            
            # Vérification capteur température
            if current_temp is None:
                room_name = room.get(CONF_ROOM_NAME, f"Pièce {idx}")
                _LOGGER.warning(f"⚠️ Room '{room_name}': No temperature sensor data")
                
                if clim_ac:
                    await self._set_climate(clim_ac, "off", None)
                if clim_gaz:
                    await self._set_climate(clim_gaz, "off", None)
                switch.update_from_manager(None, HVACAction.OFF, "Sensor error")
                
                self.room_statuses[idx] = {
                    "active_source": "Error",
                    "target_temp": target_temp,
                    "outside_temp": temp_ext,
                    "reason": "Capteur température indisponible"
                }
                self.ac_start_times[idx] = None
                continue
            
            # === VT DEMANDE OFF ===
            if requested_mode == HVACMode.OFF:
                _LOGGER.debug(f"Room {idx}: VT requested OFF")
                if clim_ac:
                    await self._set_climate(clim_ac, "off", None)
                if clim_gaz:
                    await self._set_climate(clim_gaz, "off", None)
                switch.update_from_manager(current_temp, HVACAction.OFF, "VT OFF")
                
                self.room_statuses[idx] = {
                    "active_source": "Off (VT)",
                    "target_temp": target_temp,
                    "outside_temp": temp_ext,
                    "reason": "Versatile Thermostat: OFF"
                }
                self.ac_start_times[idx] = None
                continue
            
            # === TEMPÉRATURE ATTEINTE (Hystérésis) ===
            if current_temp >= (target_temp + self.hysteresis):
                # Vérifier si AC est active et si délai minimum est respecté
                ac_state = self.hass.states.get(clim_ac) if clim_ac else None
                ac_is_running = ac_state and ac_state.state in ["heat", "cool"]
                
                if ac_is_running and not self._can_stop_ac(idx, room):
                    # AC doit continuer (délai non écoulé)
                    min_runtime = room.get(CONF_AC_MIN_RUNTIME, 5)
                    elapsed = (datetime.now() - self.ac_start_times[idx]).total_seconds() / 60
                    reason = f"Délai minimum AC ({elapsed:.1f}/{min_runtime} min)"
                    switch.update_from_manager(current_temp, HVACAction.HEATING, reason)
                    
                    self.room_statuses[idx] = {
                        "active_source": "AC (Min Runtime)",
                        "target_temp": target_temp,
                        "outside_temp": temp_ext,
                        "reason": reason
                    }
                else:
                    # Peut couper normalement
                    if clim_ac:
                        await self._set_climate(clim_ac, "off", None)
                    if clim_gaz:
                        await self._set_climate(clim_gaz, "off", None)
                    switch.update_from_manager(current_temp, HVACAction.IDLE, "Temp OK")
                    
                    self.room_statuses[idx] = {
                        "active_source": "Off (Temp OK)",
                        "target_temp": target_temp,
                        "outside_temp": temp_ext,
                        "reason": f"Température atteinte ({current_temp}°C >= {target_temp + self.hysteresis}°C)"
                    }
                    self.ac_start_times[idx] = None
                continue
            
            # === VT DEMANDE CHAUFFAGE ===
            if requested_mode in [HVACMode.HEAT, HVACMode.HEAT_COOL] and not is_summer:
                _LOGGER.debug(f"Room {idx}: VT requests heating")
                
                # VOTRE LOGIQUE UNIQUE : PAC vs Gaz
                should_heat_ac = False
                should_heat_gas = False
                reason = ""
                cop = 0
                cout_pac_kwh = 0
                
                # Calcul COP et rentabilité
                if clim_ac:
                    cop = self._interpolate_cop(temp_ext, room)
                    safe_cop = cop if cop > 0.1 else 0.1
                    cout_pac_kwh = effective_elec_price / safe_cop
                    
                    if battery_forced:
                        should_heat_ac = True
                        reason = f"Batterie ({soc}%) → PAC forcée"
                    elif cout_pac_kwh < prix_gaz:
                        should_heat_ac = True
                        if is_solar_exporting:
                            reason = f"Solaire ({grid_power}W) → PAC rentable"
                        else:
                            reason = f"PAC rentable (COP={cop:.1f}, {cout_pac_kwh:.4f}€ < {prix_gaz:.4f}€)"
                    else:
                        should_heat_gas = True
                        reason = f"Gaz rentable ({prix_gaz:.4f}€ < {cout_pac_kwh:.4f}€, COP={cop:.1f})"
                
                # Décision finale selon équipements disponibles
                if clim_ac and clim_gaz:
                    pass  # Déjà décidé
                elif clim_ac and not clim_gaz:
                    should_heat_ac = True
                    reason = "PAC seule disponible"
                elif clim_gaz and not clim_ac:
                    should_heat_gas = True
                    reason = "Gaz seul disponible"
                
                # Exécution
                if should_heat_ac:
                    _LOGGER.info(f"✅ Room {idx}: PAC selected - {reason}")
                    await self._set_climate(clim_ac, "heat", target_temp)
                    if clim_gaz:
                        await self._set_climate(clim_gaz, "off", None)
                    switch.update_from_manager(current_temp, HVACAction.HEATING, reason)
                    
                    # Enregistrer le démarrage
                    if self.ac_start_times.get(idx) is None:
                        self.ac_start_times[idx] = datetime.now()
                    
                    self.room_statuses[idx] = {
                        "active_source": "AC (Heat)",
                        "target_temp": target_temp,
                        "outside_temp": temp_ext,
                        "reason": reason
                    }
                
                elif should_heat_gas:
                    _LOGGER.info(f"✅ Room {idx}: GAZ selected - {reason}")
                    await self._set_climate(clim_gaz, "heat", target_temp)
                    if clim_ac:
                        await self._set_climate(clim_ac, "off", None)
                    switch.update_from_manager(current_temp, HVACAction.HEATING, reason)
                    
                    self.ac_start_times[idx] = None  # Reset si on passe au gaz
                    
                    self.room_statuses[idx] = {
                        "active_source": "Gaz",
                        "target_temp": target_temp,
                        "outside_temp": temp_ext,
                        "reason": reason
                    }
            
            # === VT DEMANDE REFROIDISSEMENT (ÉTÉ) ===
            elif requested_mode in [HVACMode.COOL, HVACMode.HEAT_COOL] and is_summer:
                _LOGGER.debug(f"Room {idx}: VT requests cooling")
                
                if not clim_ac:
                    reason = "Pas d'AC disponible pour refroidir"
                    switch.update_from_manager(current_temp, HVACAction.IDLE, reason)
                    
                    self.room_statuses[idx] = {
                        "active_source": "Off (Pas d'AC)",
                        "target_temp": target_temp,
                        "outside_temp": temp_ext,
                        "reason": reason
                    }
                    self.ac_start_times[idx] = None
                
                elif current_temp > target_temp:
                    # Condition solaire/batterie pour été
                    if is_solar_exporting or battery_forced:
                        reason = f"Solaire/Batterie → AC Cooling"
                        _LOGGER.info(f"✅ Room {idx}: AC COOLING - {reason}")
                        await self._set_climate(clim_ac, "cool", target_temp)
                        switch.update_from_manager(current_temp, HVACAction.COOLING, reason)
                        
                        # Enregistrer le démarrage
                        if self.ac_start_times.get(idx) is None:
                            self.ac_start_times[idx] = datetime.now()
                        
                        self.room_statuses[idx] = {
                            "active_source": "AC (Cooling)",
                            "target_temp": target_temp,
                            "outside_temp": temp_ext,
                            "reason": reason
                        }
                    else:
                        reason = "Attente solaire/batterie pour climatisation"
                        await self._set_climate(clim_ac, "off", None)
                        switch.update_from_manager(current_temp, HVACAction.IDLE, reason)
                        
                        self.room_statuses[idx] = {
                            "active_source": "Off (Attente Solaire)",
                            "target_temp": target_temp,
                            "outside_temp": temp_ext,
                            "reason": reason
                        }
                        self.ac_start_times[idx] = None
                else:
                    # Température cible atteinte - Vérifier délai minimum
                    if not self._can_stop_ac(idx, room):
                        # AC doit continuer (délai non écoulé)
                        min_runtime = room.get(CONF_AC_MIN_RUNTIME, 5)
                        elapsed = (datetime.now() - self.ac_start_times[idx]).total_seconds() / 60
                        reason = f"Délai minimum AC ({elapsed:.1f}/{min_runtime} min)"
                        await self._set_climate(clim_ac, "cool", target_temp)
                        switch.update_from_manager(current_temp, HVACAction.COOLING, reason)
                        
                        self.room_statuses[idx] = {
                            "active_source": "AC (Cooling - Min Runtime)",
                            "target_temp": target_temp,
                            "outside_temp": temp_ext,
                            "reason": reason
                        }
                    else:
                        # Délai respecté, on peut couper
                        await self._set_climate(clim_ac, "off", None)
                        switch.update_from_manager(current_temp, HVACAction.IDLE, "Temp OK")
                        
                        self.room_statuses[idx] = {
                            "active_source": "Off (Temp OK)",
                            "target_temp": target_temp,
                            "outside_temp": temp_ext,
                            "reason": f"Température OK ({current_temp}°C <= {target_temp}°C)"
                        }
                        self.ac_start_times[idx] = None
        
        # Fin de la boucle - Notifier les sensors
        self._notify_sensors()

    async def _set_climate(self, entity_id, mode, temp):
        """
        Envoyer une commande à un climate (AC ou Gaz).
        
        Version améliorée avec gestion d'erreurs et logs.
        """
        state = self.hass.states.get(entity_id)
        if not state:
            _LOGGER.warning(f"⚠️ Entity {entity_id} not found")
            return
        
        # Vérifier disponibilité
        if state.state in ["unavailable", "unknown"]:
            _LOGGER.warning(f"⚠️ Entity {entity_id} is {state.state}, skipping command")
            return
        
        try:
            # Changer le mode si nécessaire
            if state.state != mode:
                await self.hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {"entity_id": entity_id, "hvac_mode": mode}
                )
                _LOGGER.debug(f"🎛️ Set {entity_id} to {mode}")
            
            # Changer la température si mode actif
            if mode in ["heat", "cool"] and temp is not None:
                current_target = state.attributes.get("temperature", 0)
                if float(current_target) != temp:
                    await self.hass.services.async_call(
                        "climate",
                        "set_temperature",
                        {"entity_id": entity_id, "temperature": temp}
                    )
                    _LOGGER.debug(f"🌡️ Set {entity_id} temperature to {temp}°C")
        
        except Exception as e:
            _LOGGER.error(f"❌ Failed to control {entity_id}: {e}")
