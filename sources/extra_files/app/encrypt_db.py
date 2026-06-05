"""
Chiffrement de la base SQLite vers SQLCipher AES-256.
Logique :
- enc.db présente et non vide → rien à faire (idempotent)
- enc.db absente/vide + db standard présente → chiffrer (sans supprimer la standard)
- nouvelle installation → créer enc.db vide (db.create_all() la remplira)

La base standard n'est JAMAIS supprimée — elle sert de source pour les restaurations.
"""
import os, sys, sqlite3

install_dir = os.path.dirname(os.path.abspath(__file__))
key_file    = os.path.join(install_dir, '.db_key')
uploads_dir = os.path.join(install_dir, 'uploads')
data_dir    = os.path.dirname(os.path.realpath(uploads_dir))
db_std      = os.path.join(data_dir, 'orthoptie_v2.db')
db_enc      = os.path.join(data_dir, 'orthoptie_v2.enc.db')

# Générer la clé si absente
if not os.path.exists(key_file):
    import secrets
    key = secrets.token_hex(32)
    with open(key_file, 'w') as f: f.write(key)
    os.chmod(key_file, 0o600)
    print("INFO encrypt_db: clé SQLCipher générée")

with open(key_file) as f:
    key = f.read().strip()

# Base chiffrée déjà présente et non vide → rien à faire
if os.path.exists(db_enc) and os.path.getsize(db_enc) > 0:
    print("INFO encrypt_db: base chiffrée déjà présente, rien à faire")
    sys.exit(0)

try:
    import sqlcipher3
except ImportError:
    print("INFO encrypt_db: sqlcipher3 non disponible, abandon")
    sys.exit(0)

SKIP = {'sqlite_sequence','sqlite_stat1','sqlite_stat2','sqlite_stat3','sqlite_stat4'}

def fix_permissions(path):
    import pwd, grp
    try:
        uid = pwd.getpwnam('orthoptie').pw_uid
        gid = grp.getgrnam('orthoptie').gr_gid
        os.chown(path, uid, gid)
        os.chmod(path, 0o660)
    except Exception as e:
        print(f"INFO encrypt_db: permissions non corrigées — {e}")

# Supprimer enc.db vide si elle existe
if os.path.exists(db_enc):
    os.remove(db_enc)

try:
    if os.path.exists(db_std) and os.path.getsize(db_std) > 0:
        # Chiffrer depuis la base standard (sans la supprimer)
        src = sqlite3.connect(db_std)
        dst = sqlcipher3.connect(db_enc)
        dst.executescript(f"""
            PRAGMA key='{key}';
            PRAGMA cipher_page_size=4096;
            PRAGMA kdf_iter=64000;
            PRAGMA cipher_hmac_algorithm=HMAC_SHA512;
            PRAGMA cipher_kdf_algorithm=PBKDF2_HMAC_SHA512;
        """)
        tables = src.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (table,) in tables:
            if table in SKIP: continue
            schema = src.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if schema and schema[0]:
                dst.execute(schema[0])
            rows = src.execute(f"SELECT * FROM {table}").fetchall()
            if rows:
                placeholders = ','.join(['?'] * len(rows[0]))
                dst.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        for (idx_sql,) in src.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        ).fetchall():
            try: dst.execute(idx_sql)
            except Exception: pass
        dst.commit()
        dst.close()
        src.close()
        # Vérifier
        conn = sqlcipher3.connect(db_enc)
        conn.executescript(f"PRAGMA key='{key}'; PRAGMA cipher_page_size=4096; PRAGMA kdf_iter=64000;")
        nb = conn.execute("SELECT COUNT(*) FROM patient").fetchone()[0]
        conn.close()
        fix_permissions(db_enc)
        print(f"INFO encrypt_db: base chiffrée créée — {nb} patients ✓")
    else:
        # Nouvelle installation — enc.db vide, db.create_all() la remplira
        dst = sqlcipher3.connect(db_enc)
        dst.executescript(f"""
            PRAGMA key='{key}';
            PRAGMA cipher_page_size=4096;
            PRAGMA kdf_iter=64000;
            PRAGMA cipher_hmac_algorithm=HMAC_SHA512;
            PRAGMA cipher_kdf_algorithm=PBKDF2_HMAC_SHA512;
        """)
        dst.commit()
        dst.close()
        fix_permissions(db_enc)
        print("INFO encrypt_db: base chiffrée vide créée (nouvelle installation)")

except Exception as e:
    print(f"ERREUR encrypt_db: {e}")
    if os.path.exists(db_enc):
        os.remove(db_enc)
    sys.exit(1)
