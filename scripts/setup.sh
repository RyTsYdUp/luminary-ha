#!/usr/bin/env bash
# =============================================================================
# HA Zone Motion — Setup Script
# Generates a configured package.yaml for a specific room/zone.
# Run from the project root: bash scripts/setup.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/../package.yaml"

echo ""
echo "=== HA Zone Motion Setup ==="
echo "This script generates a room-specific package.yaml from the template."
echo ""

read -rp "Zone prefix (lowercase, underscores — e.g. hallway, master_bedroom): " ZONE
read -rp "Room display name (e.g. Hallway, Master Bedroom): " ROOM_NAME
read -rp "Z-Wave switch device_id (from HA device registry URL): " SWITCH_DEVICE
read -rp "Light entity_id (e.g. light.hallway_lights): " LIGHT_ENTITY
read -rp "Motion sensor 1 entity_id: " SENSOR_1
read -rp "Motion sensor 2 entity_id (or press Enter to reuse sensor 1): " SENSOR_2
read -rp "Motion sensor 3 entity_id (or press Enter to reuse sensor 1): " SENSOR_3

[[ -z "$SENSOR_2" ]] && SENSOR_2="$SENSOR_1"
[[ -z "$SENSOR_3" ]] && SENSOR_3="$SENSOR_1"

OUTPUT_DIR="$SCRIPT_DIR/../output/${ZONE}"
mkdir -p "$OUTPUT_DIR"
OUTPUT_FILE="$OUTPUT_DIR/package.yaml"

sed \
  -e "s/ZONE/${ZONE}/g" \
  -e "s/ROOM_NAME/${ROOM_NAME}/g" \
  -e "s/SWITCH_DEVICE/${SWITCH_DEVICE}/g" \
  -e "s|LIGHT_ENTITY|${LIGHT_ENTITY}|g" \
  -e "s|SENSOR_1|${SENSOR_1}|g" \
  -e "s|SENSOR_2|${SENSOR_2}|g" \
  -e "s|SENSOR_3|${SENSOR_3}|g" \
  "$TEMPLATE" > "$OUTPUT_FILE"

echo ""
echo "Generated: $OUTPUT_FILE"
echo ""
echo "Next steps:"
echo "  1. Copy $OUTPUT_FILE to your HA config: packages/${ZONE}/package.yaml"
echo "  2. Add to configuration.yaml:"
echo "       homeassistant:"
echo "         packages:"
echo "           ${ZONE}: !include packages/${ZONE}/package.yaml"
echo "  3. Reload HA configuration"
echo "  4. Add lovelace_card.yaml to your dashboard (replace ZONE=${ZONE}, ROOM_NAME=${ROOM_NAME})"
echo ""
