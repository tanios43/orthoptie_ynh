# Orthoptie — Application de gestion de dossiers patients

Application Flask déployée sur YunoHost pour la gestion des dossiers patients en cabinet d'orthoptie.

---

## Déploiement YunoHost

### Installation
```bash
sudo yunohost app install https://github.com/tanios43/orthoptie_ynh
```

### Mise à jour
```bash
sudo yunohost app upgrade orthoptie -u https://github.com/tanios43/orthoptie_ynh
```

### Serveurs
| Environnement | URL |
|---|---|
| Test | dossiers.famille-nesme.ynh.fr |
| Production | dossiers.cyps.ynh.fr |
| Nouveau VPS Ionos | dossiers.orthoptistes-yssingeaux.fr |

> **Règle** : tester sur le serveur de test, puis mettre à jour la prod quand tout fonctionne. La migration est conçue pour fonctionner sans versions intermédiaires.

---

## Stack technique

| Composant | Technologie |
|---|---|
| Backend | Python Flask + SQLAlchemy |
| Base de données | SQLite |
| Authentification | Flask-Login, sessions 24h |
| Éditeur de documents | Collabora Online via WOPI |
| Export Word | Génération XML .docx depuis entete.docx |
| Frontend | HTML/CSS/JS vanilla + Tabler Icons |

---

## Fonctionnalités

### Patients
- Fiche patient complète (nom, prénom, DDN, téléphone, médecin référent…)
- Recherche par nom, prénom, date de naissance et téléphone
- Historique des bilans avec pièces jointes

### Bilans
- Sections de bilan configurables (types : texte, textarea, nombre, liste, sphère, pièce jointe, espaceur, séparateur)
- Grille configurable : 1, 2, 3 ou 4 colonnes par section
- Sections spéciales : correction portée, réfraction obj/subj, acuité visuelle, ordonnance, courrier…
- Copie depuis l'historique d'une section
- Copie de réfraction depuis correction portée / réfraction objective
- Majuscules automatiques : début de champ, après `.` `!` `?`, après saut de ligne
- Pièces jointes cliquables avant et après sauvegarde
- Avertissement avant de quitter sans sauvegarder

### Édition de courriers (Collabora)
- Sélection des sections à inclure dans le document
- Mise en forme colonnes identique à la page d'édition du bilan
- Insertion optionnelle des images des pièces jointes de sections
- Sections spéciales conservent leur mise en forme dédiée (tableau OD/OG)
- Signature manuscrite du praticien
- Entête cabinet automatique

### Administration des sections
- Création/modification/suppression de sections et champs
- Types de champs : `text`, `textarea`, `number`, `select`, `sph`, `fichier`, `spacer`, `separator`
- Nb colonnes : 1-4 (s'applique dans le bilan ET dans les courriers générés)
- Export/import sections et modèles
- Catégories visuelles

### Messagerie
- Conversations multi-participants
- Badge messages non lus dans la navigation

### Notes & Tâches
- Notes post-its colorés (taille adaptative)
- Tâches avec priorité, échéance, assignation à un praticien
- Tâches liables à un patient (avec lien vers la fiche patient)
- Édition/suppression par le créateur ET le praticien assigné
- Badge tâches actives dans la navigation

### Sauvegarde
- Sauvegarde automatique vers NAS via SFTP/rsync
- Restauration depuis NAS ou archive locale (limite 500 Mo)
- Sauvegarde manuelle téléchargeable

---

## Configuration post-installation

### Timezone
```bash
timedatectl set-timezone Europe/Paris
systemctl restart orthoptie
```

### Dépendances système (incluses automatiquement)
- `rsync` — synchronisation NAS
- `sqlite3` — utilitaires base de données
- `python3`, `python3-pip`, `python3-venv`
- `libreoffice`, `poppler-utils`

---

## Structure des données

```
/home/yunohost.app/orthoptie/
├── orthoptie_v2.db          ← base SQLite
├── uploads/
│   ├── bilans/              ← pièces jointes globales des bilans
│   ├── sections/            ← pièces jointes des sections
│   └── wopi/                ← documents Collabora temporaires
├── ssh/
│   └── backup_key           ← clé SSH pour sauvegarde NAS
└── backups/                 ← sauvegardes locales
```

---

## Logo & Favicon

- **Favicon** (onglet navigateur) : `static/favicon.png`
- **Logo YunoHost portail** : enregistré automatiquement via `_update_app_permission_setting` à l'install/upgrade
- **Logo YunoHost admin** : `doc/LOGO.png`

---

## Développement

### Fichiers clés
| Fichier | Rôle |
|---|---|
| `app.py` | Application Flask complète |
| `migrate.py` | Migrations SQLite (lancé à chaque upgrade) |
| `manifest.toml` | Manifeste YunoHost |
| `conf/nginx.conf` | Config nginx (500M upload, 300s timeout) |
| `scripts/install` | Script d'installation YunoHost |
| `scripts/upgrade` | Script de mise à jour YunoHost |
| `scripts/backup` | Script de sauvegarde YunoHost |
| `entete.docx` | Template Word pour les courriers |
| `doc/LOGO.png` | Logo 200×200px |
| `static/favicon.png` | Favicon 32×32px |

### Pattern migration SQLite
Toutes les migrations sont dans `migrate.py` :
- `CREATE TABLE IF NOT EXISTS` pour les nouvelles tables
- `ALTER TABLE … ADD COLUMN` dans un `try/except` (ignoré si colonne existe déjà)
- Migrations complexes (recréation de table) avec vérification préalable via `PRAGMA table_info`

### Ajout d'une fonctionnalité — checklist
1. Modifier `app.py` (modèle + routes)
2. Ajouter la migration dans `migrate.py`
3. Modifier les templates concernés
4. Valider : `python3 -c "import ast; ast.parse(open('app.py').read())"`
5. Tester sur le serveur de test
6. Mettre à jour la prod

---

## Changelog récent

### Session 6 (mai 2026)
- Tâches : édition/suppression par praticien assigné, lien vers fiche patient
- Notes : post-its adaptatifs en largeur et hauteur
- Sections : types espaceur/séparateur, choix 1-4 colonnes
- Bilan : majuscules automatiques (tous types de champs)
- PJ : ouvrables directement depuis l'éditeur de bilan
- Courriers Collabora : mise en forme colonnes + images PJ de sections
- Migration robuste pour serveurs sans versions intermédiaires
- Restauration NAS/archive : fallback sans `fix-perms`, limite 500M nginx
- Timezone Europe/Paris sur nouveaux serveurs
- `rsync` + `sqlite3` dans les dépendances apt
- Logo YunoHost portail fonctionnel via API permissions
- Favicon dans l'onglet navigateur
