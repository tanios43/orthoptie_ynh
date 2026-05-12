"""
Script de migration — exécutez après chaque mise à jour de app.py.
Lance avec : python migrate.py
"""
from app import db, app, SectionDef, ChampDef

with app.app_context():

    # avec_observations EN PREMIER (requis par les autres migrations)
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("ALTER TABLE section_def ADD COLUMN avec_observations BOOLEAN DEFAULT 1"))
            conn.commit()
        print("OK      : avec_observations sur section_def")
    except Exception as e:
        msg = str(e).lower()
        if 'duplicate column' in msg or 'already exists' in msg:
            print("Present : avec_observations sur section_def")
        else:
            print(f"ERREUR  : avec_observations — {e}")

    MIGRATIONS = [
        ("ALTER TABLE section_def ADD COLUMN obs_defaut TEXT DEFAULT ''", "obs_defaut sur section_def"),
        ("""CREATE TABLE IF NOT EXISTS fichier_section (id INTEGER PRIMARY KEY AUTOINCREMENT, consultation_id INTEGER NOT NULL REFERENCES consultation(id), section_ordre INTEGER NOT NULL, champ_name VARCHAR(50) NOT NULL, nom_original VARCHAR(255) NOT NULL, nom_stocke VARCHAR(255) NOT NULL, type_fichier VARCHAR(10), titre VARCHAR(255) DEFAULT '', created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""", "table fichier_section"),
        ("""CREATE TABLE IF NOT EXISTS modele_bilan (id INTEGER PRIMARY KEY AUTOINCREMENT, nom VARCHAR(100) NOT NULL, motif VARCHAR(200) DEFAULT '', actif BOOLEAN DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""", "table modele_bilan"),
        ("""CREATE TABLE IF NOT EXISTS modele_bilan_section (id INTEGER PRIMARY KEY AUTOINCREMENT, modele_id INTEGER NOT NULL REFERENCES modele_bilan(id), type_key VARCHAR(50) NOT NULL, ordre INTEGER DEFAULT 99)""", "table modele_bilan_section"),
        ("ALTER TABLE patient ADD COLUMN rue VARCHAR(200)", "rue sur patient"),
        ("ALTER TABLE patient ADD COLUMN code_postal VARCHAR(10)", "code_postal sur patient"),
        ("ALTER TABLE patient ADD COLUMN commune VARCHAR(100)", "commune sur patient"),
        ("ALTER TABLE praticien ADD COLUMN role VARCHAR(20) DEFAULT 'praticien'", "role sur praticien"),
        ("ALTER TABLE praticien ADD COLUMN rpps VARCHAR(11)", "rpps sur praticien"),
        ("ALTER TABLE praticien ADD COLUMN couleur VARCHAR(7) DEFAULT '#2E7D6B'", "couleur sur praticien"),
        ("""CREATE TABLE IF NOT EXISTS cabinet (id INTEGER PRIMARY KEY AUTOINCREMENT, nom VARCHAR(100) NOT NULL, rue VARCHAR(200), code_postal VARCHAR(10), commune VARCHAR(100), telephone VARCHAR(20), fax VARCHAR(20), email VARCHAR(200), couleur VARCHAR(7) DEFAULT '#1C2B3A', actif BOOLEAN DEFAULT 1)""", "table cabinet"),
        ("""CREATE TABLE IF NOT EXISTS praticien_cabinet (id INTEGER PRIMARY KEY AUTOINCREMENT, praticien_id INTEGER NOT NULL REFERENCES praticien(id), cabinet_id INTEGER NOT NULL REFERENCES cabinet(id), adeli VARCHAR(9), forme_juridique VARCHAR(50), UNIQUE(praticien_id, cabinet_id))""", "table praticien_cabinet"),
        ("ALTER TABLE consultation ADD COLUMN cabinet_id INTEGER REFERENCES cabinet(id)", "cabinet_id sur consultation"),
        ("ALTER TABLE consultation ADD COLUMN medecin_prescripteur VARCHAR(200)", "medecin_prescripteur sur consultation"),
        ("ALTER TABLE cabinet ADD COLUMN couleur VARCHAR(7) DEFAULT '#1C2B3A'", "couleur sur cabinet"),
        ("""CREATE TABLE IF NOT EXISTS document_modele (id INTEGER PRIMARY KEY AUTOINCREMENT, nom VARCHAR(100) NOT NULL, type VARCHAR(20) NOT NULL, actif BOOLEAN DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""", "table document_modele"),
        ("""CREATE TABLE IF NOT EXISTS document_bloc (id INTEGER PRIMARY KEY AUTOINCREMENT, modele_id INTEGER NOT NULL REFERENCES document_modele(id), type VARCHAR(20) NOT NULL, contenu TEXT DEFAULT '', ordre INTEGER DEFAULT 99)""", "table document_bloc"),
    ]

    for sql, label in MIGRATIONS:
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text(sql))
                conn.commit()
            print(f"OK      : {label}")
        except Exception as e:
            msg = str(e).lower()
            if 'duplicate column' in msg or 'already exists' in msg:
                print(f"Present : {label}")
            else:
                print(f"ERREUR  : {label} — {e}")

    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("UPDATE praticien SET role='admin' WHERE id=(SELECT MIN(id) FROM praticien)"))
            conn.commit()
        print("OK      : premier praticien passe admin")
    except Exception as e:
        print(f"ERREUR  : admin — {e}")

    def add_champs(type_key, labels):
        sec = SectionDef.query.filter_by(type_key=type_key).first()
        if not sec:
            print(f"Section {type_key} introuvable")
            return
        existing = [c.name for c in sec.champs]
        max_ordre = max((c.ordre for c in sec.champs), default=0)
        for name, label in labels:
            if name not in existing:
                max_ordre += 1
                db.session.add(ChampDef(section_id=sec.id, name=name, label=label, type='number', ordre=max_ordre))
                print(f"OK      : {label} dans {type_key}")
            else:
                print(f"Present : {label} dans {type_key}")

    add_champs('correction_portee', [('od_add', 'Add OD'), ('og_add', 'Add OG')])
    add_champs('refraction_subj',   [('od_add', 'Add OD'), ('og_add', 'Add OG')])
    db.session.commit()
    # classe_profession sur consultation
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("ALTER TABLE consultation ADD COLUMN classe_profession VARCHAR(200)"))
            conn.commit()
        print("OK      : classe_profession sur consultation")
    except Exception as e:
        msg = str(e).lower()
        if 'duplicate column' in msg or 'already exists' in msg:
            print("Present : classe_profession sur consultation")
        else:
            print(f"ERREUR  : classe_profession — {e}")


    # Table wopi_session
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("""
                CREATE TABLE IF NOT EXISTS wopi_session (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token VARCHAR(64) UNIQUE NOT NULL,
                    consultation_id INTEGER NOT NULL REFERENCES consultation(id),
                    section_type VARCHAR(50),
                    nom_fichier VARCHAR(255) NOT NULL,
                    chemin_fichier VARCHAR(500) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    expires_at DATETIME
                )"""))
            conn.commit()
        print("OK      : table wopi_session")
    except Exception as e:
        msg = str(e).lower()
        if 'already exists' in msg: print("Present : table wopi_session")
        else: print(f"ERREUR  : wopi_session — {e}")

    # section_ordre sur wopi_session
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("ALTER TABLE wopi_session ADD COLUMN section_ordre INTEGER DEFAULT 0"))
            conn.commit()
        print("OK      : section_ordre sur wopi_session")
    except Exception as e:
        msg = str(e).lower()
        if 'duplicate column' in msg or 'already exists' in msg:
            print("Present : section_ordre sur wopi_session")
        else:
            print(f"ERREUR  : section_ordre — {e}")

    # signature sur praticien
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("ALTER TABLE praticien ADD COLUMN signature VARCHAR(500)"))
            conn.commit()
        print("OK      : signature sur praticien")
    except Exception as e:
        msg = str(e).lower()
        if 'duplicate column' in msg or 'already exists' in msg:
            print("Present : signature sur praticien")
        else:
            print(f"ERREUR  : signature — {e}")
    print("\nMigration terminee.")
