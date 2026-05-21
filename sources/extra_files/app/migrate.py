"""
Script de migration — exécutez après chaque mise à jour de app.py.
Lance avec : python migrate.py
"""
import warnings
warnings.filterwarnings('ignore', category=UserWarning, module='flask_sqlalchemy')
warnings.filterwarnings('ignore', message='.*already contains a class.*')

# Créer les tables et colonnes critiques AVANT l'import de app.py
# pour éviter que SQLAlchemy plante si le schéma est incomplet
import sqlite3, os, glob

def _find_db():
    candidates = [
        '/home/yunohost.app/orthoptie/orthoptie_v2.db',
        os.path.join(os.path.dirname(__file__), 'instance', 'orthoptie_v2.db'),
        os.path.join(os.path.dirname(__file__), 'orthoptie_v2.db'),
    ]
    for p in candidates:
        real = os.path.realpath(p)
        if os.path.exists(real):
            return real
    return None

_db_path = _find_db()
if _db_path:
    _conn = sqlite3.connect(_db_path)
    _cur = _conn.cursor()
    # Toutes les colonnes/tables critiques créées AVANT l'import de app.py
    _pre_migrations = [
        "ALTER TABLE section_def ADD COLUMN categorie VARCHAR(50) DEFAULT ''",
        "ALTER TABLE section_def ADD COLUMN avec_observations BOOLEAN DEFAULT 1",
        "ALTER TABLE section_def ADD COLUMN obs_defaut TEXT DEFAULT ''",
        """CREATE TABLE IF NOT EXISTS message (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expediteur_id INTEGER NOT NULL REFERENCES praticien(id),
            destinataire_id INTEGER NOT NULL REFERENCES praticien(id),
            contenu TEXT NOT NULL,
            lu BOOLEAN DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS note_patient (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL REFERENCES patient(id),
            praticien_id INTEGER NOT NULL REFERENCES praticien(id),
            contenu TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS suivi_vb (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notes TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS seance_vb (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suivi_id INTEGER NOT NULL REFERENCES suivi_vb(id),
            numero INTEGER NOT NULL,
            date_seance DATE,
            praticien_id INTEGER REFERENCES praticien(id),
            fusion TEXT DEFAULT '',
            accommodation TEXT DEFAULT '',
            stereogrammes TEXT DEFAULT '',
            notes TEXT DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS suivi_amblyopie (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lunettes_od VARCHAR(50) DEFAULT '',
            lunettes_og VARCHAR(50) DEFAULT '',
            av_od_init VARCHAR(20) DEFAULT '',
            av_og_init VARCHAR(20) DEFAULT '',
            ophthalmo VARCHAR(100) DEFAULT '',
            stereo VARCHAR(50) DEFAULT '',
            ese VARCHAR(50) DEFAULT '',
            versions VARCHAR(50) DEFAULT '',
            date_cs DATE,
            traitement TEXT DEFAULT '',
            prochain_rdv VARCHAR(100) DEFAULT '',
            notes TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS seance_amblyopie (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suivi_id INTEGER NOT NULL REFERENCES suivi_amblyopie(id),
            numero INTEGER NOT NULL,
            date_seance DATE,
            occlusion VARCHAR(100) DEFAULT '',
            av_od VARCHAR(20) DEFAULT '',
            av_og VARCHAR(20) DEFAULT '',
            ese VARCHAR(50) DEFAULT '',
            notes TEXT DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS categorie_section (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key VARCHAR(50) UNIQUE NOT NULL,
            label VARCHAR(100) NOT NULL,
            bg VARCHAR(20) DEFAULT '#F1EFE8',
            color VARCHAR(20) DEFAULT '#444441',
            icon VARCHAR(50) DEFAULT 'ti-layout-grid',
            ordre INTEGER DEFAULT 99
        )""",
        """CREATE TABLE IF NOT EXISTS journal_acces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            praticien_id INTEGER NOT NULL,
            patient_id INTEGER,
            consultation_id INTEGER,
            action VARCHAR(100) NOT NULL,
            detail VARCHAR(500) DEFAULT '',
            ip_address VARCHAR(50) DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "ALTER TABLE journal_acces ADD COLUMN detail VARCHAR(500) DEFAULT ''",
        "ALTER TABLE journal_acces ADD COLUMN ip_address VARCHAR(50) DEFAULT ''",
        "ALTER TABLE fichier_section ADD COLUMN section_type VARCHAR(50) DEFAULT ''",
        "ALTER TABLE praticien ADD COLUMN signature VARCHAR(500)",
        "ALTER TABLE seance_amblyopie ADD COLUMN av_notes TEXT DEFAULT ''",
        "ALTER TABLE seance_amblyopie ADD COLUMN praticien_id INTEGER REFERENCES praticien(id)",
        "ALTER TABLE wopi_session ADD COLUMN section_ordre INTEGER DEFAULT 0",
    ]
    for sql in _pre_migrations:
        try:
            _cur.execute(sql)
        except sqlite3.OperationalError:
            pass  # colonne/table déjà existante
    _conn.commit()
    _conn.close()
    print("Pré-migration SQLite OK")

from app import db, app, SectionDef, ChampDef

with app.app_context():

    # categorie EN TOUT PREMIER (requis avant tout import SQLAlchemy du modèle)
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("ALTER TABLE section_def ADD COLUMN categorie VARCHAR(50) DEFAULT ''"))
            conn.commit()
        print("OK      : categorie sur section_def")
    except Exception as e:
        msg = str(e).lower()
        if 'duplicate column' in msg or 'already exists' in msg:
            print("Present : categorie sur section_def")
        else:
            print(f"ERREUR  : categorie — {e}")

    # avec_observations EN SECOND (requis par les autres migrations)
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

    # Table categorie_section
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("""
                CREATE TABLE IF NOT EXISTS categorie_section (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key VARCHAR(50) UNIQUE NOT NULL,
                    label VARCHAR(100) NOT NULL,
                    bg VARCHAR(20) DEFAULT '#F1EFE8',
                    color VARCHAR(20) DEFAULT '#444441',
                    icon VARCHAR(50) DEFAULT 'ti-layout-grid',
                    ordre INTEGER DEFAULT 99
                )
            """))
            conn.commit()
        print("OK      : table categorie_section")
    except Exception as e:
        print(f"Present/ERREUR categorie_section : {e}")

    # Table journal_acces
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("""
                CREATE TABLE IF NOT EXISTS journal_acces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    praticien_id INTEGER NOT NULL REFERENCES praticien(id),
                    patient_id INTEGER REFERENCES patient(id),
                    consultation_id INTEGER REFERENCES consultation(id),
                    action VARCHAR(100) NOT NULL,
                    detail VARCHAR(500) DEFAULT '',
                    ip_address VARCHAR(50) DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
        print("OK      : table journal_acces")
    except Exception as e:
        print(f"Present/ERREUR journal_acces : {e}")

    # Colonnes manquantes sur journal_acces
    for col, typedef in [('detail', "VARCHAR(500) DEFAULT ''"),
                         ('ip_address', "VARCHAR(50) DEFAULT ''"),
                         ('consultation_id', 'INTEGER')]:
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text(f"ALTER TABLE journal_acces ADD COLUMN {col} {typedef}"))
                conn.commit()
            print(f"OK      : journal_acces.{col} ajouté")
        except Exception as e:
            msg = str(e).lower()
            if 'duplicate column' in msg or 'already exists' in msg:
                print(f"Present : journal_acces.{col}")
            else:
                print(f"ERREUR  : journal_acces.{col} — {e}")

    # Initialiser les catégories des sections builtin
    try:
        from app import BUILTIN_CATEGORIES
        for type_key, cat in BUILTIN_CATEGORIES.items():
            s = SectionDef.query.filter_by(type_key=type_key).first()
            if s and not s.categorie:
                s.categorie = cat
        db.session.commit()
        print("OK      : catégories builtin initialisées")
    except Exception as e:
        print(f"ERREUR  : catégories builtin — {e}")

    # Nettoyer les \r\n dans section_bilan.donnees
    try:
        import json
        with db.engine.connect() as conn:
            rows = conn.execute(db.text(
                "SELECT id, donnees FROM section_bilan WHERE donnees LIKE '%\\r%'"
            )).fetchall()
            for row in rows:
                try:
                    d = json.loads(row[1])
                    cleaned = {k: v.replace('\r\n', '\n').replace('\r', '\n') if isinstance(v, str) else v
                               for k, v in d.items()}
                    conn.execute(db.text(
                        "UPDATE section_bilan SET donnees=:d WHERE id=:id"
                    ), {'d': json.dumps(cleaned, ensure_ascii=False), 'id': row[0]})
                except Exception:
                    pass
            conn.commit()
        print(f"OK      : nettoyé {len(rows)} section_bilan avec \\r")
    except Exception as e:
        print(f"ERREUR  : nettoyage \\r : {e}")

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

    # section_type sur fichier_section
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("ALTER TABLE fichier_section ADD COLUMN section_type VARCHAR(50) DEFAULT ''"))
            conn.commit()
        print("OK      : section_type sur fichier_section")
    except Exception as e:
        msg = str(e).lower()
        if 'duplicate column' in msg or 'already exists' in msg:
            print("Present : section_type sur fichier_section")
        else:
            print(f"ERREUR  : section_type — {e}")

    # Section ordonnance
    try:
        existing = SectionDef.query.filter_by(type_key='ordonnance').first()
        if not existing:
            s = SectionDef(type_key='ordonnance', label='Ordonnances',
                           ordre=20, builtin=True, actif=True, avec_observations=False)
            db.session.add(s)
            db.session.flush()
            champs = [
                ('orto_oeil',      'Œil à occlure',       'select', 0),
                ('orto_heures',    'Heures par jour',      'text',   1),
                ('orto_duree',     'Durée du traitement',  'text',   2),
                ('orto_notes',     'Notes',                'text',   3),
                ('prisme_od_diop', 'OD dioptries',         'text',   4),
                ('prisme_od_base', 'OD base',              'select', 5),
                ('prisme_og_diop', 'OG dioptries',         'text',   6),
                ('prisme_og_base', 'OG base',              'select', 7),
                ('ryser_od_num',   'OD Ryser N°',          'text',   8),
                ('ryser_od_av',    'OD AV laissée',        'text',   9),
                ('ryser_og_num',   'OG Ryser N°',          'text',   10),
                ('ryser_og_av',    'OG AV laissée',        'text',   11),
            ]
            for name, label, type_, ordre in champs:
                db.session.add(ChampDef(section_id=s.id, name=name,
                                        label=label, type=type_, ordre=ordre))
            db.session.commit()
            print("OK      : section ordonnance créée")
        else:
            print("Present : section ordonnance")
    except Exception as e:
        print(f"ERREUR  : section ordonnance — {e}")

    # Correction section ordonnance_lunettes : remplacer lun_ep_vl/vp par lun_dip/renouvelable
    try:
        s_lun = SectionDef.query.filter_by(type_key='ordonnance_lunettes').first()
        if s_lun:
            existing = ChampDef.query.filter_by(section_id=s_lun.id).all()
            existing_names = [c.name for c in existing]
            # Supprimer anciens champs
            for c in existing:
                if c.name in ('lun_ep_vl', 'lun_ep_vp'):
                    db.session.delete(c)
                    print(f"OK      : supprimé {c.name}")
            # Ajouter nouveaux champs
            if 'lun_dip' not in existing_names:
                db.session.add(ChampDef(section_id=s_lun.id, name='lun_dip',
                                        label='DIP (mm)', type='text', ordre=8))
                print("OK      : lun_dip ajouté")
            if 'lun_renouvelable' not in existing_names:
                db.session.add(ChampDef(section_id=s_lun.id, name='lun_renouvelable',
                                        label='Renouvelable', type='select', ordre=9))
                print("OK      : lun_renouvelable ajouté")
            db.session.commit()
    except Exception as e:
        print(f"ERREUR  : fix ordonnance_lunettes champs — {e}")

    # Correction section ordonnance_lunettes : ajouter lun_dip si absent
    try:
        s_lun = SectionDef.query.filter_by(type_key='ordonnance_lunettes').first()
        if s_lun:
            existing_names = [c.name for c in ChampDef.query.filter_by(section_id=s_lun.id).all()]
            if 'lun_dip' not in existing_names:
                db.session.add(ChampDef(section_id=s_lun.id, name='lun_dip',
                                        label='DIP (mm)', type='text', ordre=8))
                db.session.commit()
                print("OK      : lun_dip ajouté à ordonnance_lunettes")
            if 'lun_renouvelable' not in existing_names:
                db.session.add(ChampDef(section_id=s_lun.id, name='lun_renouvelable',
                                        label='Renouvelable', type='select', ordre=9))
                db.session.commit()
                print("OK      : lun_renouvelable ajouté à ordonnance_lunettes")
    except Exception as e:
        print(f"ERREUR  : fix ordonnance_lunettes champs — {e}")

    # Section ordonnance_lunettes
    try:
        existing = SectionDef.query.filter_by(type_key='ordonnance_lunettes').first()
        if not existing:
            s = SectionDef(type_key='ordonnance_lunettes', label='Ordonnance de lunettes',
                           ordre=21, builtin=True, actif=True, avec_observations=False)
            db.session.add(s)
            db.session.flush()
            champs = [
                ('lun_vl_od_sph',    'VL OD — Sphère',    'text',     0),
                ('lun_vl_od_cyl',    'VL OD — Cylindre',  'text',     1),
                ('lun_vl_od_axe',    'VL OD — Axe',       'text',     2),
                ('lun_vl_og_sph',    'VL OG — Sphère',    'text',     3),
                ('lun_vl_og_cyl',    'VL OG — Cylindre',  'text',     4),
                ('lun_vl_og_axe',    'VL OG — Axe',       'text',     5),
                ('lun_vp_od_add',    'VP OD — Addition',  'text',     6),
                ('lun_vp_og_add',    'VP OG — Addition',  'text',     7),
                ('lun_dip',          'DIP (mm)',           'text',     8),
                ('lun_renouvelable', 'Renouvelable',       'select',   9),
                ('lun_remarques',    'Remarques',          'textarea', 10),
            ]
            for name, label, type_, ordre in champs:
                db.session.add(ChampDef(section_id=s.id, name=name,
                                        label=label, type=type_, ordre=ordre))
            db.session.commit()
            print("OK      : section ordonnance_lunettes créée")
        else:
            print("Present : section ordonnance_lunettes")
    except Exception as e:
        print(f"ERREUR  : section ordonnance_lunettes — {e}")

    print("\nMigration terminee.")
