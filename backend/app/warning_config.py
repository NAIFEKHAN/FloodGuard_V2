"""Versioned thresholds and stage guidance for the decision-support rules."""

WARNING_RULE_VERSION = "1.0.0"

# These are provisional, transparent decision-support boundaries, not calibrated
# event probabilities or operational thresholds. Keep all rule values here.
WARNING_THRESHOLDS = {
    "susceptibility_watch": 60.0,
    "susceptibility_high": 80.0,
    "rainfall_watch_multiplier": 1.5,
    "rainfall_prepare_multiplier": 2.0,
    "soil_moisture_wet_support": 60.0,
    "soil_moisture_very_wet_support": 80.0,
}

WARNING_STAGES = {
    "green": {
        "label": "Normal",
        "color": "#15803d",
        "guidance": "Continue normal monitoring and follow official local guidance.",
    },
    "yellow": {
        "label": "Watch",
        "color": "#ca8a04",
        "guidance": (
            "Monitor weather and official local alerts. Avoid unnecessary exposure "
            "to known unstable slopes during heavy rain."
        ),
    },
    "orange": {
        "label": "Prepare",
        "color": "#ea580c",
        "guidance": (
            "Prepare essential items, identify designated safe locations, and "
            "monitor official instructions."
        ),
    },
    "red": {
        "label": "Warning",
        "color": "#dc2626",
        "guidance": (
            "Seek official emergency information immediately. Be ready to move "
            "to designated safe areas and avoid flooded or unstable routes."
        ),
    },
}

WARNING_DISCLAIMER = (
    "FloodGuard provides modeled decision-support information and does not "
    "replace official alerts from government disaster-management authorities."
)
