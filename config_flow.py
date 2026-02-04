import voluptuous as vol
import copy
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from .const import *

class EnergyOptimizerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EnergyOptimizerOptionsFlow(config_entry)
    
    def __init__(self):
        self.user_info = {}

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self.user_info = user_input
            return await self.async_step_prices()

        schema = vol.Schema({
            vol.Required(CONF_TARIFF_MODE, default=MODE_DUAL): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[MODE_SINGLE, MODE_DUAL, MODE_TRIPLE], mode="dropdown")
            ),
            vol.Required(CONF_OUTSIDE_TEMP_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Optional(CONF_GRID_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            vol.Optional(CONF_TARIFF_SENSOR): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_BATTERY_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(CONF_GAZ_PRICE_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "input_number"])),
            vol.Required(CONF_GAZ_METER_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        })
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_prices(self, user_input=None):
        if user_input is not None:
            final_data = {**self.user_info, **user_input}
            return self.async_create_entry(title="My Energy Brain", data=final_data)
        
        mode = self.user_info.get(CONF_TARIFF_MODE)
        fields = {}
        def add_tariff_block(schema_dict, label, pk, mk, ipk, imk):
            schema_dict[vol.Required(pk)] = selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "input_number"]))
            schema_dict[vol.Optional(mk)] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
            schema_dict[vol.Optional(ipk)] = selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "input_number"]))
            schema_dict[vol.Optional(imk)] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))

        add_tariff_block(fields, "Tarif 1", CONF_PRICE_T1, CONF_METER_T1, CONF_INJ_PRICE_T1, CONF_INJ_METER_T1)
        if mode in [MODE_DUAL, MODE_TRIPLE]:
            add_tariff_block(fields, "Tarif 2", CONF_PRICE_T2, CONF_METER_T2, CONF_INJ_PRICE_T2, CONF_INJ_METER_T2)
        if mode == MODE_TRIPLE:
            add_tariff_block(fields, "Tarif 3", CONF_PRICE_T3, CONF_METER_T3, CONF_INJ_PRICE_T3, CONF_INJ_METER_T3)
        return self.async_show_form(step_id="prices", data_schema=vol.Schema(fields))

class EnergyOptimizerOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._config_entry = config_entry
        self.options = dict(config_entry.options)
        self.rooms = copy.deepcopy(self.options.get(CONF_ROOMS, []))
        self.current_room_id = None

    def _save_changes(self):
        """Sauvegarde immédiate sur le disque (Auto-Save)."""
        new_data = {**self.options, CONF_ROOMS: self.rooms}
        self.hass.config_entries.async_update_entry(
            self._config_entry,
            options=new_data
        )

    async def async_step_init(self, user_input=None):
        return await self.async_step_menu()

    async def async_step_menu(self, user_input=None):
        if user_input is not None:
            selected = user_input.get("menu_selection")
            
            if selected == "add_room":
                return await self.async_step_room_name()
            
            elif selected == "delete_room":  # <--- NOUVELLE ACTION
                return await self.async_step_delete_room()
            
            elif selected == "global_settings":
                return await self.async_step_global_settings()
            
            elif selected.startswith("edit_"):
                self.current_room_id = int(selected.split("_")[1])
                return await self.async_step_room_config()
            
            elif selected == "save":
                return self.async_create_entry(title="", data={**self.options, CONF_ROOMS: self.rooms})

        select_options = [
            {"value": "global_settings", "label": "⚙️ Réglages Globaux"},
            {"value": "add_room", "label": "➕ Ajouter une pièce"}
        ]
        
        # On n'affiche le bouton supprimer que s'il y a des pièces
        if len(self.rooms) > 0:
            select_options.append({"value": "delete_room", "label": "🗑️ Supprimer une pièce"})

        for idx, room in enumerate(self.rooms):
            name = room.get(CONF_ROOM_NAME, f"Pièce {idx+1}")
            select_options.append({"value": f"edit_{idx}", "label": f"✏️ {name}"})
            
        select_options.append({"value": "save", "label": "✅ Fermer (Tout est sauvegardé)"})

        return self.async_show_form(step_id="menu", data_schema=vol.Schema({vol.Required("menu_selection"): selector.SelectSelector(selector.SelectSelectorConfig(options=select_options, mode="list"))}))

    async def async_step_delete_room(self, user_input=None):
        """Suppression d'une pièce."""
        if user_input is not None:
            idx_to_delete = int(user_input["room_to_delete"])
            # On retire la pièce de la liste
            if 0 <= idx_to_delete < len(self.rooms):
                self.rooms.pop(idx_to_delete)
                self._save_changes() # Sauvegarde immédiate
            return await self.async_step_menu()

        # Création de la liste des pièces pour le sélecteur
        room_options = []
        for idx, room in enumerate(self.rooms):
            name = room.get(CONF_ROOM_NAME, f"Pièce {idx+1}")
            room_options.append({"value": str(idx), "label": name})

        return self.async_show_form(
            step_id="delete_room", 
            data_schema=vol.Schema({
                vol.Required("room_to_delete"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=room_options, mode="list")
                )
            })
        )

    async def async_step_global_settings(self, user_input=None):
        if user_input is not None:
            self.options.update(user_input)
            self._save_changes()
            return await self.async_step_menu()

        current_grid = self.options.get(CONF_GRID_POWER_ENTITY, self._config_entry.data.get(CONF_GRID_POWER_ENTITY))
        current_batt_thresh = self.options.get(CONF_BATTERY_THRESH_ENTITY)
        current_hysteresis = self.options.get(CONF_HYSTERESIS, 0.5)
        current_summer_mode = self.options.get(CONF_SUMMER_MODE_ENTITY)

        schema = vol.Schema({
            vol.Optional(CONF_SUMMER_MODE_ENTITY, default=current_summer_mode): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["input_boolean", "switch", "input_select"])
            ),
            vol.Optional(CONF_GRID_POWER_ENTITY, default=current_grid): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            vol.Optional(CONF_BATTERY_THRESH_ENTITY, default=current_batt_thresh): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["input_number", "number"])
            ),
            vol.Required(CONF_HYSTERESIS, default=current_hysteresis): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.1, max=5.0, step=0.1, mode="slider", unit_of_measurement="°C")
            ),
        })
        return self.async_show_form(step_id="global_settings", data_schema=schema)

    async def async_step_room_name(self, user_input=None):
        if user_input is not None:
            self.rooms.append({CONF_ROOM_NAME: user_input[CONF_ROOM_NAME]})
            self.current_room_id = len(self.rooms) - 1
            return await self.async_step_room_config()
        return self.async_show_form(step_id="room_name", data_schema=vol.Schema({vol.Required(CONF_ROOM_NAME): str}))

    async def async_step_room_config(self, user_input=None, errors=None):
        if user_input is not None:
            if not user_input.get(CONF_CLIMATE_GAZ) and not user_input.get(CONF_CLIMATE_AC):
                return await self.async_step_room_config(errors={"base": "no_heater_selected"})
            
            self.rooms[self.current_room_id].update(user_input)
            
            if user_input.get(CONF_CLIMATE_AC):
                return await self.async_step_room_cop()
            
            self._save_changes() 
            return await self.async_step_menu()

        room = self.rooms[self.current_room_id]
        schema_dict = {}
        args_gaz = {'default': room.get(CONF_CLIMATE_GAZ)} if room.get(CONF_CLIMATE_GAZ) else {}
        schema_dict[vol.Optional(CONF_CLIMATE_GAZ, **args_gaz)] = selector.EntitySelector(selector.EntitySelectorConfig(domain="climate"))
        args_ac = {'default': room.get(CONF_CLIMATE_AC)} if room.get(CONF_CLIMATE_AC) else {}
        schema_dict[vol.Optional(CONF_CLIMATE_AC, **args_ac)] = selector.EntitySelector(selector.EntitySelectorConfig(domain="climate"))
        schema_dict[vol.Required(CONF_TEMP_SENSOR, default=room.get(CONF_TEMP_SENSOR))] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="temperature"))
        return self.async_show_form(step_id="room_config", data_schema=vol.Schema(schema_dict), errors=errors)

    async def async_step_room_cop(self, user_input=None):
        if user_input is not None:
            self.rooms[self.current_room_id].update(user_input)
            self._save_changes() 
            return await self.async_step_menu()

        room = self.rooms[self.current_room_id]
        schema = vol.Schema({
            vol.Required(CONF_COP_M15, default=room.get(CONF_COP_M15, 2.0)): selector.NumberSelector(selector.NumberSelectorConfig(min=0.5, max=10, step=0.1, mode="box")),
            vol.Required(CONF_COP_M7,  default=room.get(CONF_COP_M7, 2.5)): selector.NumberSelector(selector.NumberSelectorConfig(min=0.5, max=10, step=0.1, mode="box")),
            vol.Required(CONF_COP_0,   default=room.get(CONF_COP_0, 3.2)): selector.NumberSelector(selector.NumberSelectorConfig(min=0.5, max=10, step=0.1, mode="box")),
            vol.Required(CONF_COP_7,   default=room.get(CONF_COP_7, 4.0)): selector.NumberSelector(selector.NumberSelectorConfig(min=0.5, max=10, step=0.1, mode="box")),
            vol.Required(CONF_COP_15,  default=room.get(CONF_COP_15, 5.0)): selector.NumberSelector(selector.NumberSelectorConfig(min=0.5, max=10, step=0.1, mode="box")),
        })
        return self.async_show_form(step_id="room_cop", data_schema=schema)