# Energy Optimizer - Changelog

## Version 1.1.0 - Protection Anti-Cyclage AC

### 🆕 Nouvelle Fonctionnalité : Délai Minimum de Fonctionnement

**Problème résolu :**
Les pompes à chaleur ne doivent pas démarrer/arrêter trop fréquemment pour éviter :
- L'usure prématurée du compresseur
- La surconsommation due aux cycles courts
- La réduction de l'efficacité énergétique

**Solution :**
Une fois l'AC démarrée (en mode Heat ou Cool), elle reste active pendant au **moins X minutes**, 
même si la température cible est atteinte entre-temps.

### ⚙️ Configuration

**Par pièce** (dans Options > Modifier une pièce > Performance AC) :
- **Durée minimum** : 1 à 60 minutes (slider)
- **Valeur par défaut** : 5 minutes
- S'applique uniquement aux AC (pas au gaz)

### 📊 Comportement

#### Exemple - Mode Été (Refroidissement)
```
14:00 → AC démarre (26°C → cible 24°C)
14:03 → Température atteinte (24°C) mais délai = 5 min
       → AC continue de tourner
       → Raison affichée : "Délai minimum (3.0/5 min)"
14:05 → Délai écoulé → AC s'arrête
```

#### Exemple - Mode Hiver (Chauffage)
```
10:00 → AC (PAC) démarre car plus rentable que gaz
10:02 → Température > cible + hystérésis
       → Normalement devrait s'arrêter
       → MAIS délai minimum pas écoulé
       → Continue jusqu'à 10:05
```

### 🔍 Indicateurs Visuels

Dans le **sensor.optimizer_[pièce]** :
- `active_source` : "AC (Heat - Min Runtime)" ou "AC (Cooling - Min Runtime)"
- `reason` : "Délai minimum (3.2/5 min)" avec temps écoulé en temps réel

### ⚠️ Notes Techniques

1. **Reset du timer :** 
   - Quand l'AC s'arrête complètement
   - Quand on passe d'AC à Gaz (mode hiver)
   - Quand le thermostat virtuel est mis sur OFF

2. **Transitions de mode :**
   - Le délai est **partagé** entre Heat et Cool
   - Si AC tourne 3 min en Heat, puis passe en Cool → reste 2 min minimum

3. **Sécurité :**
   - En cas de capteur HS : AC coupée immédiatement (priorité sécurité)
   - Délai ignoré si utilisateur met manuellement OFF le thermostat virtuel

### 🎯 Recommandations

| Type d'installation | Durée recommandée |
|---------------------|-------------------|
| Split mobile | 3-5 minutes |
| Mono-split fixe | 5-8 minutes |
| Multi-split | 8-12 minutes |
| PAC gainable | 10-15 minutes |

### 📝 Migration depuis v1.0.0

Les pièces existantes auront automatiquement **5 minutes** par défaut.
Vous pouvez ajuster dans : **Options → ✏️ [Nom Pièce] → Performance AC**

---

## Installation des Fichiers Modifiés

Copiez ces fichiers dans `/config/custom_components/energy_optimizer/` :
- `__init__.py` (logique anti-cyclage)
- `const.py` (nouvelle constante)
- `config_flow.py` (interface config)
- `strings.json` + `fr.json` (traductions)

Puis **redémarrez Home Assistant**.
