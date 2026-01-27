# /config/custom_components/energy_optimizer/config_flow.py

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import uuid
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
        """ÉTAPE 1 : Config Globale (Inchangée)."""
        if user_input is not None:
            self.user_info = user_input
            return await self.async_step_prices()

        schema = vol.Schema({
            vol.Required(CONF_TARIFF_MODE, default=MODE_DUAL): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[MODE_SINGLE, MODE_DUAL, MODE_TRIPLE], mode=selector.SelectSelectorMode.DROPDOWN)
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
        """ÉTAPE 2 : Prix (Code condensé ici, utilise ton code complet précédent)."""
        if user_input is not None:
            final_data = {**self.user_info, **user_input}
            return self.async_create_entry(title="My Energy Brain", data=final_data)
        
        # ... Insère ici ton code de génération de champs prix (add_tariff_block) ...
        # Pour faire court dans la réponse, je mets un schema vide, mais garde ton code !
        return self.async_show_form(step_id="prices", data_schema=vol.Schema({}))


class EnergyOptimizerOptionsFlow(config_entries.OptionsFlow):
    """Gestionnaire de pièces (Menu Principal)."""

    def __init__(self, config_entry):
        self.config_entry = config_entry
        self.options = dict(config_entry.options)
        self.rooms = self.options.get(CONF_ROOMS, [])
        self.current_room_id = None # Pour savoir quelle pièce on édite

    async def async_step_init(self, user_input=None):
        """Menu Principal : Liste des pièces."""
        return await self.async_step_menu()

    async def async_step_menu(self, user_input=None):
        """Affiche la liste des pièces et le bouton Ajouter."""
        if user_input is not None:
            selected = user_input.get("menu_selection")
            if selected == "add_room":
                return await self.async_step_room_name()
            elif selected.startswith("edit_"):
                # On récupère l'index de la pièce
                room_idx = int(selected.split("_")[1])
                self.current_room_id = room_idx
                return await self.async_step_room_config()
            else:
                # Sauvegarder et Quitter
                return self.async_create_entry(title="", data={CONF_ROOMS: self.rooms})

        # Construction du menu
        options_list = ["add_room"]
        options_labels = {"add_room": "➕ Ajouter une pièce"}
        
        for idx, room in enumerate(self.rooms):
            key = f"edit_{idx}"
            options_list.append(key)
            name = room.get(CONF_ROOM_NAME, f"Pièce {idx}")
            options_labels[key] = f"✏️ Modifier : {name}"

        schema = vol.Schema({
            vol.Required("menu_selection"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"value": k, "label": v} for k, v in options_labels.items()],
                    mode=selector.SelectSelectorMode.LIST
                )
            )
        })

        return self.async_show_form(step_id="menu", data_schema=schema, description_placeholders={"count": str(len(self.rooms))})

    async def async_step_room_name(self, user_input=None):
        """Demande le nom de la nouvelle pièce."""
        if user_input is not None:
            # Création d'une nouvelle entrée vide
            new_room = {CONF_ROOM_NAME: user_input[CONF_ROOM_NAME]}
            self.rooms.append(new_room)
            self.current_room_id = len(self.rooms) - 1
            return await self.async_step_room_config()

        schema = vol.Schema({
            vol.Required(CONF_ROOM_NAME): str
        })
        return self.async_show_form(step_id="room_name", data_schema=schema)

    async def async_step_room_config(self, user_input=None):
        """Configure les appareils de la pièce."""
        if user_input is not None:
            # Mise à jour de la pièce en mémoire
            self.rooms[self.current_room_id].update(user_input)
            
            # Si un AC est sélectionné, on va configurer les COP
            if user_input.get(CONF_CLIMATE_AC):
                return await self.async_step_room_cop()
            
            # Sinon retour au menu
            return await self.async_step_menu()

        # Valeurs actuelles
        room = self.rooms[self.current_room_id]

        schema = vol.Schema({
            vol.Optional(CONF_CLIMATE_GAZ, default=room.get(CONF_CLIMATE_GAZ)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="climate")
            ),
            vol.Optional(CONF_CLIMATE_AC, default=room.get(CONF_CLIMATE_AC)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="climate")
            ),
            vol.Required(CONF_TEMP_SENSOR, default=room.get(CONF_TEMP_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            # Planning Simple
            vol.Required(CONF_START_TIME, default=room.get(CONF_START_TIME, "07:00:00")): selector.TimeSelector(),
            vol.Required(CONF_END_TIME, default=room.get(CONF_END_TIME, "22:00:00")): selector.TimeSelector(),
            vol.Required(CONF_COMFORT_TEMP, default=room.get(CONF_COMFORT_TEMP, 21.0)): float,
            vol.Required(CONF_ECO_TEMP, default=room.get(CONF_ECO_TEMP, 18.0)): float,
        })

        return self.async_show_form(step_id="room_config", data_schema=schema, description_placeholders={"room": room[CONF_ROOM_NAME]})

    async def async_step_room_cop(self, user_input=None):
        """Configure les 5 points de COP pour l'AC."""
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

        return self.async_show_form(step_id="room_cop", data_schema=schema, description_placeholders={"room": room[CONF_ROOM_NAME]})