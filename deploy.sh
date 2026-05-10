#!/bin/bash
# deploy.sh — Script de déploiement sur YunoHost
# Usage : bash deploy.sh

set -e

APP_DIR="/var/www/orthoptie"
APP_USER="orthoptie"
REPO_URL="https://github.com/VOTRE_USER/VOTRE_REPO.git"  # ← À MODIFIER
DOMAIN="dossiers.orthoptistes-yssingeaux.fr"              # ← À MODIFIER
PYTHON="python3"

echo "=== Déploiement application Orthoptie ==="

# 1. Créer l'utilisateur système si nécessaire
if ! id "$APP_USER" &>/dev/null; then
    sudo useradd --system --home "$APP_DIR" --shell /bin/false "$APP_USER"
    echo "✓ Utilisateur $APP_USER créé"
fi

# 2. Créer les dossiers
sudo mkdir -p "$APP_DIR"
sudo mkdir -p "$APP_DIR/uploads"
sudo mkdir -p "$APP_DIR/uploads/wopi"

# 3. Cloner ou mettre à jour le repo
if [ -d "$APP_DIR/.git" ]; then
    echo "→ Mise à jour du code..."
    cd "$APP_DIR"
    sudo git pull origin main
else
    echo "→ Clonage du repo..."
    sudo git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

# 4. Installer les dépendances Python
echo "→ Installation des dépendances..."
sudo pip3 install -r "$APP_DIR/requirements.txt" --break-system-packages -q

# 5. Copier entete.docx si présent localement
if [ -f "./entete.docx" ]; then
    sudo cp ./entete.docx "$APP_DIR/entete.docx"
    echo "✓ entete.docx copié"
fi

# 6. Permissions
sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"
sudo chmod -R 750 "$APP_DIR"
sudo chmod -R 770 "$APP_DIR/uploads"

# 7. Créer/mettre à jour le service systemd
sudo tee /etc/systemd/system/orthoptie.service > /dev/null << EOF
[Unit]
Description=Application Orthoptie Flask
After=network.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment="PATH=/usr/bin"
ExecStart=/usr/bin/gunicorn --workers 2 --bind 127.0.0.1:5001 --timeout 120 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable orthoptie
sudo systemctl restart orthoptie
echo "✓ Service orthoptie démarré"

# 8. Configurer nginx
sudo tee /etc/nginx/conf.d/$DOMAIN.d/orthoptie.conf > /dev/null << EOF
location / {
    proxy_pass http://127.0.0.1:5001;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_read_timeout 120;
    proxy_connect_timeout 120;
    include proxy_params_no_auth;
}
EOF

sudo nginx -t && sudo systemctl reload nginx
echo "✓ Nginx configuré"

# 9. Migration base de données
echo "→ Migration base de données..."
cd "$APP_DIR"
sudo -u "$APP_USER" python3 migrate.py

echo ""
echo "=== Déploiement terminé ==="
echo "Application disponible sur : https://$DOMAIN"
