import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from .const import DOMAIN, CONF_BATTERY_ENTITY, CONF_PRICE_ENTITY, CONF_HEATER_ENTITY

class EnergyOptimizerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Gère la configuration via l'UI."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Première étape : on demande les entités."""
        errors = {}

        if user_input is not None:
            # On valide et on crée l'entrée
            return self.async_create_entry(title="My Energy Brain", data=user_input)

        # Le formulaire
        schema = vol.Schema({
            vol.Required(CONF_BATTERY_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_PRICE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
             vol.Required(CONF_HEATER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="climate")
            ),
        })

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)