# /config/custom_components/energy_optimizer/config_flow.py

import voluptuous as vol
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
        """ÉTAPE 1 : Installation."""
        if user_input is not None:
            self.user_info = user_input
            return await self.async_step_prices()

        schema = vol.Schema({
            vol.Required(CONF_TARIFF_MODE, default=MODE_DUAL): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[MODE_SINGLE, MODE_DUAL, MODE_TRIPLE],
                    mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Required(CONF_OUTSIDE_TEMP_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Optional(CONF_TARIFF_SENSOR): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_BATTERY_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(CONF_GAZ_PRICE_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "input_number"])),
            vol.Required(CONF_GAZ_METER_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        })
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_prices(self, user_input=None):
        """ÉTAPE 2 : Prix."""
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
    """Gestionnaire de pièces."""

    def __init__(self, config_entry):
        """Initialisation sécurisée."""
        self.config_entry = config_entry
        self.options = dict(config_entry.options)
        # Utilisation de .get avec valeur par défaut pour éviter le crash si CONF_ROOMS n'existe pas
        self.rooms = self.options.get(CONF_ROOMS, [])
        self.current_room_id = None

    async def async_step_init(self, user_input=None):
        return await self.async_step_menu()

    async def async_step_menu(self, user_input=None):
        """Menu Principal."""
        if user_input is not None:
            selected = user_input.get("menu_selection")
            if selected == "add_room":
                return await self.async_step_room_name()
            elif selected.startswith("edit_"):
                self.current_room_id = int(selected.split("_")[1])
                return await self.async_step_room_config()
            else:
                return self.async_create_entry(title="", data={CONF_ROOMS: self.rooms})

        select_options = [{"value": "add_room", "label": "➕ Ajouter une pièce"}]
        
        for idx, room in enumerate(self.rooms):
            name = room.get(CONF_ROOM_NAME, f"Pièce {idx+1}")
            select_options.append({"value": f"edit_{idx}", "label": f"✏️ {name}"})
            
        select_options.append({"value": "save", "label": "💾 Sauvegarder et Quitter"})

        schema = vol.Schema({
            vol.Required("menu_selection"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=select_options,
                    mode=selector.SelectSelectorMode.LIST
                )
            )
        })

        return self.async_show_form(step_id="menu", data_schema=schema)

    async def async_step_room_name(self, user_input=None):
        if user_input is not None:
            new_room = {CONF_ROOM_NAME: user_input[CONF_ROOM_NAME]}
            self.rooms.append(new_room)
            self.current_room_id = len(self.rooms) - 1
            return await self.async_step_room_config()

        return self.async_show_form(
            step_id="room_name", 
            data_schema=vol.Schema({vol.Required(CONF_ROOM_NAME): str})
        )

    async def async_step_room_config(self, user_input=None):
        if user_input is not None:
            self.rooms[self.current_room_id].update(user_input)
            if user_input.get(CONF_CLIMATE_AC):
                return await self.async_step_room_cop()
            return await self.async_step_menu()

        room = self.rooms[self.current_room_id]

        schema = vol.Schema({
            vol.Optional(CONF_CLIMATE_GAZ, default=room.get(CONF_CLIMATE_GAZ)): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate")),
            vol.Optional(CONF_CLIMATE_AC, default=room.get(CONF_CLIMATE_AC)): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate")),
            vol.Required(CONF_TEMP_SENSOR, default=room.get(CONF_TEMP_SENSOR)): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="temperature")),
            
            vol.Required(CONF_START_TIME, default=room.get(CONF_START_TIME, "07:00:00")): selector.TimeSelector(),
            vol.Required(CONF_END_TIME, default=room.get(CONF_END_TIME, "22:00:00")): selector.TimeSelector(),
            vol.Required(CONF_COMFORT_TEMP, default=room.get(CONF_COMFORT_TEMP, 21.0)): float,
            vol.Required(CONF_ECO_TEMP, default=room.get(CONF_ECO_TEMP, 18.0)): float,
        })

        return self.async_show_form(step_id="room_config", data_schema=schema)

    async def async_step_room_cop(self, user_input=None):
        if user_input is not None:
            self.rooms[self.current_room_id].update(user_input)
            return await self.async_step_menu()

        room = self.rooms[self.current_room_id]
        
        schema = vol.Schema({
            vol.Required(CONF_COP_M15, default=room.get(CONF_COP_M15, 2.0)): float,
            vol.Required(CONF_COP_M7,  default=room.get(CONF_COP_M7, 2.5)): float,
            vol.Required(CONF_COP_0,   default=room.get(CONF_COP_0, 3.2)): float,
            vol.Required(CONF_COP_7,   default=room.get(CONF_COP_7, 4.0)): float,
            vol.Required(CONF_COP_15,  default=room.get(CONF_COP_15, 5.0)): float,
        })

        return self.async_show_form(step_id="room_cop", data_schema=schema)