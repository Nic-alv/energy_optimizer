# /config/custom_components/energy_optimizer/const.py

DOMAIN = "energy_optimizer"

# --- CONFIGURATION GLOBALE (Install) ---
CONF_TARIFF_MODE = "tariff_mode"
CONF_TARIFF_SENSOR = "tariff_sensor"
CONF_OUTSIDE_TEMP_ENTITY = "outside_temp_entity" 
CONF_BATTERY_ENTITY = "battery_entity"

CONF_GAZ_PRICE_ENTITY = "gaz_price_entity"
CONF_GAZ_METER_ENTITY = "gaz_meter_entity"

# Modes
MODE_SINGLE = "Mono-horaire"
MODE_DUAL = "Bi-horaire (Jour/Nuit)"
MODE_TRIPLE = "Tri-horaire (Eco/Normal/Pointe)"

# --- PRIX & INDEX ---
CONF_PRICE_T1 = "price_t1"; CONF_METER_T1 = "meter_t1"
CONF_PRICE_T2 = "price_t2"; CONF_METER_T2 = "meter_t2"
CONF_PRICE_T3 = "price_t3"; CONF_METER_T3 = "meter_t3"

CONF_INJ_PRICE_T1 = "inj_price_t1"; CONF_INJ_METER_T1 = "inj_meter_t1"
CONF_INJ_PRICE_T2 = "inj_price_t2"; CONF_INJ_METER_T2 = "inj_meter_t2"
CONF_INJ_PRICE_T3 = "inj_price_t3"; CONF_INJ_METER_T3 = "inj_meter_t3"

# --- CONFIGURATION DES PIÈCES (IMPORTANT) ---
CONF_ROOMS = "rooms" 
CONF_ROOM_NAME = "room_name"

# Appareils
CONF_CLIMATE_GAZ = "climate_gaz"
CONF_CLIMATE_AC = "climate_ac"
CONF_TEMP_SENSOR = "temp_sensor"

# Courbe COP (AC)
CONF_COP_M15 = "cop_m15" # -15°C
CONF_COP_M7  = "cop_m7"  # -7°C
CONF_COP_0   = "cop_0"   # 0°C
CONF_COP_7   = "cop_7"   # 7°C
CONF_COP_15  = "cop_15"  # 15°C

# Planning Simple
CONF_COMFORT_TEMP = "comfort_temp"
CONF_ECO_TEMP = "eco_temp"
CONF_START_TIME = "start_time"
CONF_END_TIME = "end_time"