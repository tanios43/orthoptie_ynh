#!/bin/bash
# Script de restauration post-upload : arrêt service, re-chiffrement, redémarrage
# Arguments: $1 = chemin data_dir, $2 = chemin install_dir

DATA_DIR="${1:-/home/yunohost.app/orthoptie}"
INSTALL_DIR="${2:-/var/www/orthoptie}"
PYTHON="$INSTALL_DIR/venv/bin/python3"
ENCRYPT_SCRIPT="$INSTALL_DIR/encrypt_db.py"

sleep 1

# 1. Arrêter le service
systemctl stop orthoptie 2>/dev/null || true
sleep 1

# 2. Supprimer l'ancienne base chiffrée
rm -f "$DATA_DIR/orthoptie_v2.enc.db"

# 3. Re-chiffrer depuis la base standard restaurée
if [ -f "$ENCRYPT_SCRIPT" ]; then
    "$PYTHON" "$ENCRYPT_SCRIPT"
fi

# 4. Corriger les permissions
chown -R orthoptie:orthoptie "$DATA_DIR" 2>/dev/null || true
chmod 600 "$INSTALL_DIR/.db_key" 2>/dev/null || true

# 5. Redémarrer
systemctl start orthoptie 2>/dev/null || true
