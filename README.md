# Cabinet d'orthoptie — Application Flask

## Démarrage rapide

### 1. Installer les dépendances
```bash
pip install flask flask-login flask-sqlalchemy
```

### 2. Lancer l'application
```bash
python app.py
```
→ Ouvrir http://localhost:5000

La base SQLite `orthoptie.db` est créée automatiquement au premier lancement.
Un praticien de test est créé : login `marie.dupont` (sans mot de passe pour le prototype).

---

## Structure du projet à créer

```
orthoptie/
├── app.py                        ← fichier principal (fourni)
├── schema.sql                    ← schéma PostgreSQL (référence)
├── orthoptie.db                  ← base SQLite (créée automatiquement)
│
└── templates/
    ├── base.html                 ← layout commun (à créer)
    ├── login.html                ← formulaire de connexion
    │
    ├── patients/
    │   ├── liste.html            ← liste des patients
    │   ├── detail.html           ← fiche patient + historique
    │   ├── formulaire.html       ← création / modification patient
    │   └── recherche.html        ← résultats de recherche
    │
    └── consultations/
        ├── formulaire.html       ← saisie du bilan orthoptique (long)
        └── detail.html           ← lecture du bilan
```

---

## Prochaines étapes

1. **Créer les templates HTML** (base.html + les 6 templates listés ci-dessus)
2. **Ajouter la vérification du mot de passe** (werkzeug.security)
3. **Tester avec un vrai praticien** du cabinet sur le formulaire de bilan
4. **Migrer vers PostgreSQL** avant tout déploiement
5. **Packager pour YunoHost**

---

## Sécurité — à faire AVANT tout déploiement

- [ ] Changer `SECRET_KEY` dans app.py
- [ ] Activer la vérification des mots de passe (werkzeug)
- [ ] Chiffrer `num_secu` avec pgcrypto (PostgreSQL)
- [ ] Passer en HTTPS (géré par YunoHost automatiquement)
- [ ] Vérifier que le journal d'accès fonctionne
