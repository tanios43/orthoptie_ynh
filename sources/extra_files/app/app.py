"""
Application Flask — Cabinet d'orthoptie multi-praticiens
Architecture v3 : sections de bilan pilotées par la base de données.

Installation :
    pip install flask flask-login flask-sqlalchemy

Lancement :
    python app.py  →  http://localhost:5000
"""

from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, jsonify, session, after_this_request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime, date
from werkzeug.utils import secure_filename
import json, os, re, unicodedata

app = Flask(__name__)

@app.after_request
def add_ngrok_header(response):
    """Désactive l'interstitiel ngrok et gère la session permanente."""
    response.headers['ngrok-skip-browser-warning'] = 'true'
    if current_user.is_authenticated:
        session.permanent = True
        session.modified = True
    return response


def log_acces(action, patient_id=None, consultation_id=None, detail=''):
    """Enregistre un accès dans le journal RGPD."""
    try:
        ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
        db.session.add(JournalAcces(
            praticien_id=current_user.id,
            patient_id=patient_id,
            consultation_id=consultation_id,
            action=action,
            detail=str(detail)[:500],
            ip_address=str(ip)[:50],
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()


@app.template_global()
def now():
    """Date/heure locale courante pour les templates Jinja."""
    from datetime import datetime as _dt
    return _dt.now()


@app.template_global()
def age_a_la_date(date_naissance, date_ref=None):
    """Retourne l'âge sous la forme 'X ans Y mois' pour les templates Jinja."""
    if not date_naissance:
        return ''
    from datetime import date
    ref = date_ref or date.today()
    years = ref.year - date_naissance.year
    months = ref.month - date_naissance.month
    if ref.day < date_naissance.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    if years == 0:
        return f'{months} mois'
    if months == 0:
        return f'{years} ans'
    return f'{years} ans {months} mois'


CATEGORIES_BUILTIN = {
    'refraction':  {'label': 'Réfraction',          'bg': '#EEEDFE', 'color': '#3C3489', 'icon': 'ti-eye'},
    'acuite':      {'label': 'Acuité visuelle',      'bg': '#EAF3DE', 'color': '#27500A', 'icon': 'ti-focus-2'},
    'motilite':    {'label': 'Motilité / Vergences', 'bg': '#FAECE7', 'color': '#712B13', 'icon': 'ti-arrows-move'},
    'stereoscopie':{'label': 'Stéréoscopie',         'bg': '#FAEEDA', 'color': '#633806', 'icon': 'ti-3d-cube-sphere'},
    'anamnese':    {'label': 'Anamnèse',             'bg': '#F1EFE8', 'color': '#444441', 'icon': 'ti-notes'},
    'conclusions': {'label': 'Conclusions',          'bg': '#E1F5EE', 'color': '#085041', 'icon': 'ti-clipboard-text'},
    'ordonnance':  {'label': 'Ordonnance',           'bg': '#FBEAF0', 'color': '#72243E', 'icon': 'ti-prescription'},
    'courrier':    {'label': 'Courrier',             'bg': '#E6F1FB', 'color': '#0C447C', 'icon': 'ti-mail'},
    '':            {'label': 'Sans catégorie',       'bg': '#F1EFE8', 'color': '#444441', 'icon': 'ti-layout-grid'},
}

def get_categories():
    """Retourne le dict des catégories (builtin + DB)."""
    cats = dict(CATEGORIES_BUILTIN)
    try:
        for c in CategorieSection.query.order_by(CategorieSection.ordre).all():
            cats[c.key] = {'label': c.label, 'bg': c.bg, 'color': c.color, 'icon': c.icon}
    except Exception:
        pass
    return cats


@app.after_request
def add_security_headers(response):
    """Ajoute les headers de sécurité HTTP à chaque réponse."""
    response.headers['X-Frame-Options']        = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection']       = '1; mode=block'
    response.headers['Referrer-Policy']         = 'strict-origin-when-cross-origin'
    # Ne pas cacher les pages authentifiées
    if current_user.is_authenticated:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return response


# Rate limiting manuel sur le login
_login_attempts = {}  # {ip: [timestamps]}

def _check_rate_limit(ip):
    """Retourne True si l'IP est bloquée (trop de tentatives)."""
    import time
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Garder uniquement les tentatives des 15 dernières minutes
    attempts = [t for t in attempts if now - t < 900]
    _login_attempts[ip] = attempts
    return len(attempts) >= 10  # max 10 tentatives / 15 min

def _record_attempt(ip):
    import time
    _login_attempts.setdefault(ip, []).append(time.time())


@app.before_request
def force_setup_si_admin_defaut():
    """Bloque toute navigation si connecté avec le compte admin par défaut."""
    if not current_user.is_authenticated:
        return
    is_default = getattr(current_user, 'is_default', False) or \
                 (current_user.login == 'admin' and current_user.nom == '')
    if is_default:
        allowed = {'setup_premier_compte', 'logout', 'static'}
        if request.endpoint not in allowed:
            return redirect(url_for('setup_premier_compte'))


def get_collabora_url():
    """Retourne l'URL Collabora depuis la config DB ou la valeur par défaut."""
    try:
        cfg = ConfigApp.query.first()
        if cfg and cfg.collabora_url:
            return cfg.collabora_url.rstrip('/')
    except Exception:
        pass
    return COLLABORA_URL


def get_wopi_base_url():
    """Retourne l'URL de base WOPI depuis la config DB ou la valeur par défaut."""
    try:
        cfg = ConfigApp.query.first()
        if cfg and cfg.wopi_base_url:
            return cfg.wopi_base_url.rstrip('/')
    except Exception:
        pass
    return WOPI_BASE_URL


@app.context_processor
def inject_categories():
    return {'CATEGORIES': get_categories(), 'get_categories': get_categories}


@app.context_processor
def inject_globals_count():
    if current_user.is_authenticated:
        try:
            # Messages non lus dans les conversations
            msgs = db.session.query(Message.id).join(
                Conversation, Message.conversation_id == Conversation.id
            ).join(
                ConversationParticipant,
                ConversationParticipant.conversation_id == Conversation.id
            ).filter(
                ConversationParticipant.praticien_id == current_user.id,
                Message.expediteur_id != current_user.id
            ).outerjoin(
                MessageLu,
                db.and_(MessageLu.message_id == Message.id,
                        MessageLu.praticien_id == current_user.id)
            ).filter(MessageLu.id == None).count()
            # Tâches actives
            taches = Tache.query.filter(
                db.or_(Tache.praticien_id == current_user.id,
                       Tache.assigne_a == current_user.id),
                Tache.statut != 'termine'
            ).count()
            return {'messages_non_lus': msgs, 'nb_taches_actives': taches}
        except Exception:
            pass
    return {'messages_non_lus': 0, 'nb_taches_actives': 0}


@app.template_filter('mdhtml')
def md_to_contenteditable(text):
    """Convertit le Markdown en HTML compatible contenteditable (format natif Chrome)."""
    import re
    if not text: return ''
    t = str(text).replace('\r\n', '\n').replace('\r', '\n')
    t = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', t, flags=re.DOTALL)
    t = re.sub(r'\*\*(.+?)\*\*',     r'<strong>\1</strong>', t, flags=re.DOTALL)
    t = re.sub(r'\*(.+?)\*',         r'<em>\1</em>', t, flags=re.DOTALL)
    t = re.sub(r'<u>(.+?)</u>',      r'<u>\1</u>', t, flags=re.DOTALL)
    # Format natif contenteditable Chrome : première ligne brute, suivantes en <div>
    lines = t.split('\n')
    if len(lines) == 1:
        return t
    return lines[0] + ''.join(f'<div>{l if l else "<br>"}</div>' for l in lines[1:])


@app.template_filter('localtime')
def localtime_filter(dt):
    """Convertit un datetime UTC en heure locale (Europe/Paris)."""
    if not dt: return ''
    try:
        from datetime import timezone, timedelta
        import time
        # Utiliser le décalage local du serveur
        local_offset = timedelta(seconds=-time.timezone if time.daylight == 0 else -time.altzone)
        local_dt = dt.replace(tzinfo=timezone.utc) + local_offset
        return local_dt.strftime('%d/%m/%Y %H:%M')
    except Exception:
        return dt.strftime('%d/%m/%Y %H:%M')


@app.template_filter('localhour')
def localhour_filter(dt):
    """Convertit en heure locale, format court HH:MM."""
    if not dt: return ''
    try:
        from datetime import timezone, timedelta
        import time
        local_offset = timedelta(seconds=-time.timezone if time.daylight == 0 else -time.altzone)
        local_dt = dt.replace(tzinfo=timezone.utc) + local_offset
        return local_dt.strftime('%d/%m %H:%M')
    except Exception:
        return dt.strftime('%d/%m %H:%M')


@app.template_filter('md')
def md_to_html(text):
    """Convertit le Markdown simple en HTML pour l'affichage dans contenteditable."""
    import re
    if not text: return ''
    t = str(text)
    t = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', t, flags=re.DOTALL)
    t = re.sub(r'\*\*(.+?)\*\*',     r'<strong>\1</strong>', t, flags=re.DOTALL)
    t = re.sub(r'\*(.+?)\*',         r'<em>\1</em>', t, flags=re.DOTALL)
    t = re.sub(r'<u>(.+?)</u>',      r'<u>\1</u>', t, flags=re.DOTALL)
    # Utiliser <div> pour les sauts de ligne — format natif contenteditable Chrome
    lines = t.split('\n')
    if len(lines) == 1:
        return t
    return lines[0] + ''.join(f'<div>{l if l else "<br>"}</div>' for l in lines[1:])


def _md_runs(text, font='Verdana', size=20):
    """Convertit le Markdown en runs Word XML avec tokenisation."""
    import re
    if not text: return ''

    def make_run(t, bold=False, italic=False, underline=False):
        if not t: return ''
        b = '<w:b/><w:bCs/>' if bold else ''
        i = '<w:i/><w:iCs/>' if italic else ''
        u = '<w:u w:val="single"/>' if underline else ''
        t_esc = t.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        lines = t_esc.split('\n')
        content = ''
        for j, line in enumerate(lines):
            br = '<w:br/>' if j < len(lines)-1 else ''
            content += f'<w:t xml:space="preserve">{line}</w:t>{br}'
        return (f'<w:r><w:rPr>{b}{i}{u}'
                f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}"/>'
                f'<w:sz w:val="{size}"/></w:rPr>{content}</w:r>')

    # Tokeniser en liste de (texte, bold, italic, underline)
    # Approche : remplacer les marqueurs par des tokens, puis parser
    TOK = [
        ('***', 'BI_OPEN'),  ('***', 'BI_CLOSE'),
        ('**',  'B_OPEN'),   ('**',  'B_CLOSE'),
        ('*',   'I_OPEN'),   ('*',   'I_CLOSE'),
        ('<u>', 'U_OPEN'),   ('</u>','U_CLOSE'),
    ]

    # Approche récursive avec état
    def parse(t, bold=False, italic=False, underline=False):
        if not t: return ''
        # Trouver le premier marqueur
        first_pos = len(t)
        first_pat = None
        for pat, name in [
            (r'\*\*\*',  'BI'), (r'\*\*', 'B'), (r'\*', 'I'),
            (r'<u>',     'UO'), (r'</u>', 'UC'),
        ]:
            m = re.search(pat, t)
            if m and m.start() < first_pos:
                first_pos = m.start()
                first_pat = (m, name, pat)

        if first_pat is None:
            return make_run(t, bold, italic, underline)

        m, name, pat = first_pat
        result = make_run(t[:m.start()], bold, italic, underline)

        if name == 'BI':
            # Trouver la fermeture ***
            end = re.search(r'\*\*\*', t[m.end():])
            if end:
                inner = t[m.end():m.end()+end.start()]
                result += parse(inner, True, True, underline)
                result += parse(t[m.end()+end.end():], bold, italic, underline)
            else:
                result += make_run(t[m.start():], bold, italic, underline)
        elif name == 'B':
            end = re.search(r'\*\*', t[m.end():])
            if end:
                inner = t[m.end():m.end()+end.start()]
                result += parse(inner, True, italic, underline)
                result += parse(t[m.end()+end.end():], bold, italic, underline)
            else:
                result += make_run(t[m.start():], bold, italic, underline)
        elif name == 'I':
            end = re.search(r'\*', t[m.end():])
            if end:
                inner = t[m.end():m.end()+end.start()]
                result += parse(inner, bold, True, underline)
                result += parse(t[m.end()+end.end():], bold, italic, underline)
            else:
                result += make_run(t[m.start():], bold, italic, underline)
        elif name == 'UO':
            end = re.search(r'</u>', t[m.end():], re.IGNORECASE)
            if end:
                inner = t[m.end():m.end()+end.start()]
                result += parse(inner, bold, italic, True)
                result += parse(t[m.end()+end.end():], bold, italic, underline)
            else:
                result += make_run(t[m.start():], bold, italic, underline)
        else:
            result += make_run(t[m.start():], bold, italic, underline)

        return result

    return parse(text)



    """Convertit le Markdown simple (gras, italique, souligné) en HTML."""
    import re
    if not text: return ''
    t = str(text)
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'\*(.+?)\*',     r'<em>\1</em>', t)
    t = re.sub(r'<u>(.+?)</u>',  r'<u>\1</u>', t)
    t = t.replace('\n', '<br>')
    return t


@app.template_filter('notes_patient_list')
def notes_patient_list_filter(patient_id):
    try:
        return NotePatient.query.filter_by(patient_id=patient_id)\
            .order_by(NotePatient.created_at.desc()).all()
    except Exception:
        return []


@app.template_filter('suivi_bv_list')
def suivi_bv_list_filter(patient_id):
    try:
        return SuiviBV.query.filter_by(patient_id=patient_id)\
            .order_by(SuiviBV.date_debut.desc()).all()
    except Exception:
        return []


@app.template_filter('suivi_nv_list')
def suivi_nv_list_filter(patient_id):
    try:
        return SuiviNV.query.filter_by(patient_id=patient_id)\
            .order_by(SuiviNV.date_debut.desc()).all()
    except Exception:
        return []


@app.template_filter('suivi_vb_list')
def suivi_vb_list_filter(patient_id):
    try:
        return SuiviVB.query.filter_by(patient_id=patient_id)\
            .order_by(SuiviVB.date_debut.desc()).all()
    except Exception:
        return []


@app.template_filter('suivi_amblyopie_list')
def suivi_amblyopie_list_filter(patient_id):
    try:
        return SuiviAmblyopie.query.filter_by(patient_id=patient_id)\
            .order_by(SuiviAmblyopie.date_bilan.desc()).all()
    except Exception:
        return []


BUILTIN_CATEGORIES = {
    'anam': 'anamnese', 'correction_portee': 'refraction',
    'frontofocometrie': 'refraction',
    'refraction_obj': 'refraction', 'refraction_subj': 'refraction',
    'acuite': 'acuite', 'swaine': 'acuite',
    'stereoscopie': 'stereoscopie',
    'cover': 'motilite', 'motilite': 'motilite', 'ppc': 'motilite',
    'facilites_accom': 'motilite', 'facilites_verg': 'motilite',
    'conclusions': 'conclusions',
    'ordonnance': 'ordonnance', 'ordonnance_lunettes': 'ordonnance',
    'courrier': 'courrier',
}


@app.template_global()
def section_style(section_type, categorie=''):
    """Retourne bg/color/icon pour une section selon sa catégorie."""
    cat = categorie or BUILTIN_CATEGORIES.get(section_type, '')
    return get_categories().get(cat, CATEGORIES_BUILTIN[''])
import os as _os
_secret_key = _os.environ.get('ORTHOPTIE_SECRET_KEY') or _os.environ.get('SECRET_KEY')
if not _secret_key:
    # Générer et persister une clé si elle n'existe pas
    _key_file = _os.path.join(_os.path.dirname(__file__), '.secret_key')
    if _os.path.exists(_key_file):
        with open(_key_file) as _f: _secret_key = _f.read().strip()
    else:
        import secrets as _sec
        _secret_key = _sec.token_hex(32)
        with open(_key_file, 'w') as _f: _f.write(_secret_key)
        _os.chmod(_key_file, 0o600)

app.config['SECRET_KEY']                = _secret_key
app.config['SESSION_COOKIE_SECURE']     = True
app.config['SESSION_COOKIE_HTTPONLY']   = True
app.config['SESSION_COOKIE_SAMESITE']   = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = __import__('datetime').timedelta(hours=24)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True

# ── Configuration WOPI / Collabora ──────────────────────────────────────────
# URL publique de CE serveur Flask (accessible par Collabora)
# Exemples :
#   ngrok      : 'https://abc123.ngrok.io'
#   YunoHost   : 'https://dossiers.orthoptistes-yssingeaux.fr'
#   local test : 'http://host.docker.internal:5000'
WOPI_BASE_URL = 'https://dossiers.cyps.ynh.fr'   # URL publique de ce serveur Flask

# URL de votre serveur Collabora Online (modifiable depuis Admin → Configuration)
COLLABORA_URL = 'https://collabora.orthoptistes-yssingeaux.fr'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', 'doc', 'docx'}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
# DATA_FOLDER : résoudre le symlink uploads pour trouver le vrai répertoire de données
_uploads_real = os.path.realpath(app.config['UPLOAD_FOLDER'])
app.config['DATA_FOLDER'] = os.path.dirname(_uploads_real)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# ── Base de données — SQLCipher si disponible, sinon SQLite standard ──────────
_db_key_file = _os.path.join(_os.path.dirname(__file__), '.db_key')
_db_key = None
if _os.path.exists(_db_key_file):
    with open(_db_key_file) as _f:
        _db_key = _f.read().strip()

_data_folder  = app.config['DATA_FOLDER']
_db_enc_path  = _os.path.join(_data_folder, 'orthoptie_v2.enc.db')
_use_encrypted = bool(_db_key and _os.path.exists(_db_enc_path) and _os.path.getsize(_db_enc_path) > 0)

if _use_encrypted:
    try:
        import sqlcipher3 as _sqlcipher3
        _enc_path = _db_enc_path
        _enc_key  = _db_key
        def _sqlcipher_creator():
            conn = _sqlcipher3.connect(_enc_path)
            conn.executescript(f"""
                PRAGMA key='{_enc_key}';
                PRAGMA cipher_page_size=4096;
                PRAGMA kdf_iter=64000;
                PRAGMA cipher_hmac_algorithm=HMAC_SHA512;
                PRAGMA cipher_kdf_algorithm=PBKDF2_HMAC_SHA512;
            """)
            return conn
        app.config['SQLALCHEMY_DATABASE_URI']   = 'sqlite+pysqlite:///:memory:'
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'creator': _sqlcipher_creator,
            'connect_args': {}
        }
        print("INFO: Base de données chiffrée (SQLCipher AES-256) activée")
    except ImportError:
        _use_encrypted = False
        print("WARNING: sqlcipher3 non disponible, SQLite standard utilisé")

if not _use_encrypted:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///orthoptie_v2.db'

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Veuillez vous connecter.'

# ============================================================
# MODÈLES
# ============================================================

class Praticien(UserMixin, db.Model):
    __tablename__ = 'praticien'
    id            = db.Column(db.Integer, primary_key=True)
    nom           = db.Column(db.String(100), nullable=False)
    prenom        = db.Column(db.String(100), nullable=False)
    titre         = db.Column(db.String(50), default='Orthoptiste')
    email         = db.Column(db.String(200), unique=True)
    login         = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200))
    is_default    = db.Column(db.Boolean, default=False)
    rpps          = db.Column(db.String(11))   # N° RPPS national fixe
    couleur       = db.Column(db.String(7), default='#2E7D6B')  # couleur hex
    actif         = db.Column(db.Boolean, default=True)
    role          = db.Column(db.String(20), default='praticien')
    signature     = db.Column(db.String(500))  # chemin vers l'image de signature
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_admin(self):
        return self.role == 'admin'

    def set_password(self, password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        from werkzeug.security import check_password_hash
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def __repr__(self): return f'{self.titre} {self.prenom} {self.nom}'


class Patient(db.Model):
    __tablename__ = 'patient'
    id               = db.Column(db.Integer, primary_key=True)
    nom              = db.Column(db.String(100), nullable=False)
    prenom           = db.Column(db.String(100), nullable=False)
    date_naissance   = db.Column(db.Date)
    sexe             = db.Column(db.String(10))
    rue              = db.Column(db.String(200))
    code_postal      = db.Column(db.String(10))
    commune          = db.Column(db.String(100))
    telephone        = db.Column(db.String(20))
    email            = db.Column(db.String(200))
    medecin_referent = db.Column(db.String(200))
    num_secu         = db.Column(db.String(15))
    praticien_id     = db.Column(db.Integer, db.ForeignKey('praticien.id'))
    notes_admin      = db.Column(db.Text)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    consultations    = db.relationship('Consultation', backref='patient', lazy=True,
                                       order_by='Consultation.date_consult.desc()')
    @property
    def age(self):
        if self.date_naissance:
            today = date.today()
            years = today.year - self.date_naissance.year
            months = today.month - self.date_naissance.month
            if today.day < self.date_naissance.day:
                months -= 1
            if months < 0:
                years -= 1
                months += 12
            if years == 0:
                return f'{months} mois'
            if months == 0:
                return f'{years} ans'
            return f'{years} ans {months} mois'
        return None
    def __repr__(self): return f'{self.nom} {self.prenom}'


class Consultation(db.Model):
    __tablename__ = 'consultation'
    id           = db.Column(db.Integer, primary_key=True)
    patient_id   = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    praticien_id = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    date_consult = db.Column(db.Date, nullable=False, default=date.today)
    motif                 = db.Column(db.Text)
    medecin_prescripteur  = db.Column(db.String(200))
    classe_profession     = db.Column(db.String(200))
    cabinet_id            = db.Column(db.Integer, db.ForeignKey('cabinet.id'))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    praticien    = db.relationship('Praticien', backref='consultations')
    cabinet      = db.relationship('Cabinet')
    sections     = db.relationship('SectionBilan', backref='consultation', lazy=True,
                                   order_by='SectionBilan.ordre', cascade='all, delete-orphan')
    fichiers     = db.relationship('FichierBilan', backref='consultation', lazy=True,
                                   order_by='FichierBilan.created_at', cascade='all, delete-orphan')
    fichiers_sec = db.relationship('FichierSection', backref='consultation', lazy=True,
                                   order_by='FichierSection.section_ordre',
                                   cascade='all, delete-orphan')


class SectionBilan(db.Model):
    __tablename__ = 'section_bilan'
    id              = db.Column(db.Integer, primary_key=True)
    consultation_id = db.Column(db.Integer, db.ForeignKey('consultation.id'), nullable=False)
    type            = db.Column(db.String(50), nullable=False)
    ordre           = db.Column(db.Integer, nullable=False)
    titre           = db.Column(db.String(200))
    observations    = db.Column(db.Text)
    donnees         = db.Column(db.Text, default='{}')
    def get_donnees(self):
        try: return json.loads(self.donnees or '{}')
        except: return {}
    @property
    def label(self):
        sd = SectionDef.query.filter_by(type_key=self.type).first()
        return sd.label if sd else self.type

    @property
    def categorie(self):
        sd = SectionDef.query.filter_by(type_key=self.type).first()
        return (sd.categorie or '') if sd else ''


class FichierBilan(db.Model):
    __tablename__ = 'fichier_bilan'
    id              = db.Column(db.Integer, primary_key=True)
    consultation_id = db.Column(db.Integer, db.ForeignKey('consultation.id'), nullable=False)
    nom_original    = db.Column(db.String(255), nullable=False)
    nom_stocke      = db.Column(db.String(255), nullable=False)
    type_fichier    = db.Column(db.String(10))
    titre           = db.Column(db.String(255), default='')
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)


class FichierSection(db.Model):
    """Fichier attaché à une section précise d'un bilan."""
    __tablename__ = 'fichier_section'
    id              = db.Column(db.Integer, primary_key=True)
    consultation_id = db.Column(db.Integer, db.ForeignKey('consultation.id'), nullable=False)
    section_ordre   = db.Column(db.Integer, nullable=False)
    section_type    = db.Column(db.String(50), default='')  # type de section pour recalcul ordre
    champ_name      = db.Column(db.String(50), nullable=False)
    nom_original    = db.Column(db.String(255), nullable=False)
    nom_stocke      = db.Column(db.String(255), nullable=False)
    type_fichier    = db.Column(db.String(10))
    titre           = db.Column(db.String(255), default='')
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)


class SuiviBV(db.Model):
    """Suivi de rééducation basse vision."""
    __tablename__ = 'suivi_bv'
    id            = db.Column(db.Integer, primary_key=True)
    patient_id    = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    praticien_id  = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    cabinet_id    = db.Column(db.Integer, db.ForeignKey('cabinet.id'), nullable=True)
    date_debut    = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    notes         = db.Column(db.Text, default='')
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient   = db.relationship('Patient',   foreign_keys=[patient_id])
    praticien = db.relationship('Praticien', foreign_keys=[praticien_id])
    cabinet   = db.relationship('Cabinet',   foreign_keys=[cabinet_id])
    seances   = db.relationship('SeanceBV', backref='suivi',
                                order_by='SeanceBV.numero',
                                cascade='all, delete-orphan')

    @property
    def derniere_seance_date(self):
        dates = [s.date_seance for s in self.seances if s.date_seance]
        return max(dates) if dates else self.date_debut

    @property
    def date_tri(self): return self.derniere_seance_date


class SeanceBV(db.Model):
    """Séance individuelle de rééducation basse vision."""
    __tablename__ = 'seance_bv'
    id           = db.Column(db.Integer, primary_key=True)
    suivi_id     = db.Column(db.Integer, db.ForeignKey('suivi_bv.id'), nullable=False)
    numero       = db.Column(db.Integer, nullable=False)
    date_seance  = db.Column(db.Date, nullable=True)
    praticien_id = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=True)
    acuite       = db.Column(db.Text, default='')
    av_od        = db.Column(db.String(20), default='')
    av_og        = db.Column(db.String(20), default='')
    av_notes     = db.Column(db.Text, default='')
    exercices    = db.Column(db.Text, default='')
    notes        = db.Column(db.Text, default='')

    praticien = db.relationship('Praticien', foreign_keys=[praticien_id])


class SuiviNV(db.Model):
    """Suivi de rééducation neurovisuelle."""
    __tablename__ = 'suivi_nv'
    id            = db.Column(db.Integer, primary_key=True)
    patient_id    = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    praticien_id  = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    cabinet_id    = db.Column(db.Integer, db.ForeignKey('cabinet.id'), nullable=True)
    date_debut    = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    notes         = db.Column(db.Text, default='')
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient   = db.relationship('Patient',   foreign_keys=[patient_id])
    praticien = db.relationship('Praticien', foreign_keys=[praticien_id])
    cabinet   = db.relationship('Cabinet',   foreign_keys=[cabinet_id])
    seances   = db.relationship('SeanceNV', backref='suivi',
                                order_by='SeanceNV.numero',
                                cascade='all, delete-orphan')

    @property
    def derniere_seance_date(self):
        dates = [s.date_seance for s in self.seances if s.date_seance]
        return max(dates) if dates else self.date_debut

    @property
    def date_tri(self):
        return self.derniere_seance_date


class SeanceNV(db.Model):
    """Séance individuelle de rééducation neurovisuelle."""
    __tablename__ = 'seance_nv'
    id           = db.Column(db.Integer, primary_key=True)
    suivi_id     = db.Column(db.Integer, db.ForeignKey('suivi_nv.id'), nullable=False)
    numero       = db.Column(db.Integer, nullable=False)
    date_seance  = db.Column(db.Date, nullable=True)
    praticien_id = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=True)
    vb_acco_omot = db.Column(db.Text, default='')
    neurovisuel  = db.Column(db.Text, default='')
    notes        = db.Column(db.Text, default='')

    praticien = db.relationship('Praticien', foreign_keys=[praticien_id])


class SuiviVB(db.Model):
    """Suivi de rééducation vision binoculaire."""
    __tablename__ = 'suivi_vb'
    id            = db.Column(db.Integer, primary_key=True)
    patient_id    = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    praticien_id  = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    cabinet_id    = db.Column(db.Integer, db.ForeignKey('cabinet.id'), nullable=True)
    date_debut    = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    notes         = db.Column(db.Text, default='')
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient   = db.relationship('Patient',   foreign_keys=[patient_id])
    praticien = db.relationship('Praticien', foreign_keys=[praticien_id])
    cabinet   = db.relationship('Cabinet',   foreign_keys=[cabinet_id])
    seances   = db.relationship('SeanceVB', backref='suivi',
                                order_by='SeanceVB.numero',
                                cascade='all, delete-orphan')

    @property
    def derniere_seance_date(self):
        dates = [s.date_seance for s in self.seances if s.date_seance]
        return max(dates) if dates else self.date_debut

    @property
    def date_tri(self):
        return self.derniere_seance_date


class SeanceVB(db.Model):
    """Séance individuelle de rééducation VB."""
    __tablename__ = 'seance_vb'
    id           = db.Column(db.Integer, primary_key=True)
    suivi_id     = db.Column(db.Integer, db.ForeignKey('suivi_vb.id'), nullable=False)
    numero       = db.Column(db.Integer, nullable=False)
    date_seance  = db.Column(db.Date, nullable=True)
    praticien_id = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=True)
    fusion       = db.Column(db.Text, default='')
    accommodation= db.Column(db.Text, default='')
    stereogrammes= db.Column(db.Text, default='')
    notes        = db.Column(db.Text, default='')

    praticien = db.relationship('Praticien', foreign_keys=[praticien_id])


class SuiviAmblyopie(db.Model):
    """Suivi de rééducation amblyopie."""
    __tablename__ = 'suivi_amblyopie'
    id            = db.Column(db.Integer, primary_key=True)
    patient_id    = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    praticien_id  = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    cabinet_id    = db.Column(db.Integer, db.ForeignKey('cabinet.id'), nullable=True)
    date_bilan    = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    # En-tête
    lunettes_od   = db.Column(db.String(50), default='')
    lunettes_og   = db.Column(db.String(50), default='')
    av_od_init    = db.Column(db.String(20), default='')
    av_og_init    = db.Column(db.String(20), default='')
    ophthalmo     = db.Column(db.String(100), default='')
    stereo        = db.Column(db.String(50), default='')
    ese           = db.Column(db.String(50), default='')
    versions      = db.Column(db.String(50), default='')
    date_cs       = db.Column(db.Date, nullable=True)
    traitement    = db.Column(db.Text, default='')
    prochain_rdv  = db.Column(db.String(100), default='')
    notes         = db.Column(db.Text, default='')
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient   = db.relationship('Patient',   foreign_keys=[patient_id])
    praticien = db.relationship('Praticien', foreign_keys=[praticien_id])
    cabinet   = db.relationship('Cabinet',  foreign_keys=[cabinet_id])
    seances   = db.relationship('SeanceAmblyopie', backref='suivi',
                                order_by='SeanceAmblyopie.numero',
                                cascade='all, delete-orphan')

    def __str__(self):
        return f'Suivi amblyopie du {self.date_bilan.strftime("%d/%m/%Y")}'

    @property
    def derniere_seance_date(self):
        """Date de la dernière séance renseignée, ou date du bilan."""
        dates = [s.date_seance for s in self.seances if s.date_seance]
        return max(dates) if dates else self.date_bilan

    @property
    def date_tri(self):
        return self.derniere_seance_date


class SeanceAmblyopie(db.Model):
    """Séance individuelle dans un suivi amblyopie."""
    __tablename__ = 'seance_amblyopie'
    id          = db.Column(db.Integer, primary_key=True)
    suivi_id    = db.Column(db.Integer, db.ForeignKey('suivi_amblyopie.id'), nullable=False)
    numero      = db.Column(db.Integer, nullable=False)
    date_seance = db.Column(db.Date, nullable=True)
    praticien_id= db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=True)
    occlusion   = db.Column(db.String(100), default='')
    av_od       = db.Column(db.String(20), default='')
    av_og       = db.Column(db.String(20), default='')
    av_notes    = db.Column(db.Text, default='')
    ese         = db.Column(db.String(50), default='')
    notes       = db.Column(db.Text, default='')

    praticien = db.relationship('Praticien', foreign_keys=[praticien_id])


class Conversation(db.Model):
    """Conversation entre praticiens (2 ou plus)."""
    __tablename__ = 'conversation'
    id         = db.Column(db.Integer, primary_key=True)
    titre      = db.Column(db.String(200), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    participants = db.relationship('ConversationParticipant', backref='conversation',
                                   cascade='all, delete-orphan')
    messages     = db.relationship('Message', backref='conversation',
                                   order_by='Message.created_at',
                                   cascade='all, delete-orphan')

    def get_autres_participants(self, praticien_id):
        return [p.praticien for p in self.participants if p.praticien_id != praticien_id]

    def dernier_message(self):
        return self.messages[-1] if self.messages else None

    def non_lus_pour(self, praticien_id):
        return sum(1 for m in self.messages
                   if not m.lu_par(praticien_id) and m.expediteur_id != praticien_id)


class ConversationParticipant(db.Model):
    """Participant à une conversation."""
    __tablename__ = 'conversation_participant'
    id              = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=False)
    praticien_id    = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    praticien       = db.relationship('Praticien', foreign_keys=[praticien_id])


class MessageLu(db.Model):
    """Statut de lecture d'un message par participant."""
    __tablename__ = 'message_lu'
    id           = db.Column(db.Integer, primary_key=True)
    message_id   = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)
    praticien_id = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)


class Message(db.Model):
    """Message dans une conversation."""
    __tablename__ = 'message'
    id              = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=True)
    expediteur_id   = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    # Champs legacy (messages 1-to-1 anciens)
    destinataire_id = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=True)
    lu              = db.Column(db.Boolean, default=False)
    contenu         = db.Column(db.Text, nullable=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    expediteur   = db.relationship('Praticien', foreign_keys=[expediteur_id])
    destinataire = db.relationship('Praticien', foreign_keys=[destinataire_id])
    lectures     = db.relationship('MessageLu', backref='message',
                                   cascade='all, delete-orphan')

    def lu_par(self, praticien_id):
        """Vérifie si le message a été lu par ce praticien."""
        if self.conversation_id:
            return any(l.praticien_id == praticien_id for l in self.lectures)
        # Legacy
        return self.lu if self.destinataire_id == praticien_id else True

    def marquer_lu(self, praticien_id):
        """Marque le message comme lu par ce praticien."""
        if self.conversation_id:
            if not any(l.praticien_id == praticien_id for l in self.lectures):
                db.session.add(MessageLu(message_id=self.id, praticien_id=praticien_id))
        else:
            if self.destinataire_id == praticien_id:
                self.lu = True



class Note(db.Model):
    """Note personnelle d'un praticien."""
    __tablename__ = 'note'
    id           = db.Column(db.Integer, primary_key=True)
    praticien_id = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    titre        = db.Column(db.String(200), default='')
    contenu      = db.Column(db.Text, default='')
    couleur      = db.Column(db.String(20), default='#FEFCE8')
    epingle      = db.Column(db.Boolean, default=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow)
    praticien    = db.relationship('Praticien', foreign_keys=[praticien_id])


class Favori(db.Model):
    __tablename__ = 'favori'
    id           = db.Column(db.Integer, primary_key=True)
    praticien_id = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    nom          = db.Column(db.String(100), nullable=False)
    url          = db.Column(db.String(500), nullable=False)
    categorie    = db.Column(db.String(100), default='')
    couleur      = db.Column(db.String(7), default='#f0f4ff')
    favicon_url  = db.Column(db.String(500), default='')
    ordre        = db.Column(db.Integer, default=0)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    praticien    = db.relationship('Praticien', foreign_keys=[praticien_id])


class Tache(db.Model):
    """Tâche personnelle ou partagée."""
    __tablename__ = 'tache'
    id           = db.Column(db.Integer, primary_key=True)
    praticien_id = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    titre        = db.Column(db.String(300), nullable=False)
    description  = db.Column(db.Text, default='')
    echeance     = db.Column(db.Date, nullable=True)
    priorite     = db.Column(db.String(10), default='normale')  # basse, normale, haute
    statut       = db.Column(db.String(20), default='a_faire')  # a_faire, en_cours, termine
    assigne_a    = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=True)
    patient_id   = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow)
    praticien    = db.relationship('Praticien', foreign_keys=[praticien_id])
    assigne      = db.relationship('Praticien', foreign_keys=[assigne_a])
    patient      = db.relationship('Patient', foreign_keys=[patient_id])


class NotePatient(db.Model):
    """Note partagée sur un patient, visible par tous les praticiens."""
    __tablename__ = 'note_patient'
    id           = db.Column(db.Integer, primary_key=True)
    patient_id   = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    praticien_id = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    contenu      = db.Column(db.Text, nullable=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    patient   = db.relationship('Patient',   foreign_keys=[patient_id])
    praticien = db.relationship('Praticien', foreign_keys=[praticien_id])


class ConfigApp(db.Model):
    """Configuration générale de l'application."""
    __tablename__ = 'config_app'
    id              = db.Column(db.Integer, primary_key=True)
    collabora_url   = db.Column(db.String(500), default='')
    wopi_base_url   = db.Column(db.String(500), default='')
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow)


class ConfigSauvegarde(db.Model):
    """Configuration de la sauvegarde distante."""
    __tablename__ = 'config_sauvegarde'
    id            = db.Column(db.Integer, primary_key=True)
    sftp_host     = db.Column(db.String(255), default='')
    sftp_port     = db.Column(db.Integer, default=22)
    sftp_user     = db.Column(db.String(100), default='')
    sftp_path     = db.Column(db.String(500), default='/backups/orthoptie')
    sftp_actif    = db.Column(db.Boolean, default=False)
    cle_publique  = db.Column(db.Text, default='')
    cle_privee    = db.Column(db.Text, default='')
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow)


class CategorieSection(db.Model):
    """Catégories personnalisables pour les sections de bilan."""
    __tablename__ = 'categorie_section'
    id     = db.Column(db.Integer, primary_key=True)
    key    = db.Column(db.String(50), unique=True, nullable=False)
    label  = db.Column(db.String(100), nullable=False)
    bg     = db.Column(db.String(20), default='#F1EFE8')
    color  = db.Column(db.String(20), default='#444441')
    icon   = db.Column(db.String(50), default='ti-layout-grid')
    ordre  = db.Column(db.Integer, default=99)


class JournalAcces(db.Model):
    __tablename__ = 'journal_acces'
    id              = db.Column(db.Integer, primary_key=True)
    praticien_id    = db.Column(db.Integer, db.ForeignKey('praticien.id'))
    patient_id      = db.Column(db.Integer, db.ForeignKey('patient.id'))
    consultation_id = db.Column(db.Integer, db.ForeignKey('consultation.id'))
    action          = db.Column(db.String(50), nullable=False)
    ip_address      = db.Column(db.String(45))
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)


class Cabinet(db.Model):
    """Cabinet médical — coordonnées."""
    __tablename__ = 'cabinet'
    id          = db.Column(db.Integer, primary_key=True)
    nom         = db.Column(db.String(100), nullable=False)
    rue         = db.Column(db.String(200))
    code_postal = db.Column(db.String(10))
    commune     = db.Column(db.String(100))
    telephone   = db.Column(db.String(20))
    fax         = db.Column(db.String(20))
    email       = db.Column(db.String(200))
    couleur     = db.Column(db.String(7), default='#1C2B3A')
    actif       = db.Column(db.Boolean, default=True)
    repondeur            = db.Column(db.Text, default='')
    repondeur_updated_by = db.Column(db.String(100), default='')
    repondeur_updated_at = db.Column(db.DateTime, nullable=True)

    praticiens  = db.relationship('PraticienCabinet', backref='cabinet',
                                  cascade='all, delete-orphan')

    @property
    def adresse_complete(self):
        parts = [self.rue, f'{self.code_postal} {self.commune}'.strip()]
        return ', '.join(p for p in parts if p and p.strip())

    def __repr__(self): return self.nom


class PraticienCabinet(db.Model):
    """Liaison praticien ↔ cabinet avec données spécifiques."""
    __tablename__ = 'praticien_cabinet'
    id            = db.Column(db.Integer, primary_key=True)
    praticien_id  = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    cabinet_id    = db.Column(db.Integer, db.ForeignKey('cabinet.id'), nullable=False)
    adeli         = db.Column(db.String(9))    # N° ADELI propre à ce cabinet
    forme_juridique = db.Column(db.String(50)) # EI, SELARL, etc.
    __table_args__ = (db.UniqueConstraint('praticien_id', 'cabinet_id'),)

    praticien = db.relationship('Praticien', backref='cabinets')


class DocumentModele(db.Model):
    """Modèle de document (courrier ou ordonnance)."""
    __tablename__ = 'document_modele'
    id      = db.Column(db.Integer, primary_key=True)
    nom     = db.Column(db.String(100), nullable=False)
    type    = db.Column(db.String(20), nullable=False)
    actif   = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    blocs   = db.relationship('DocumentBloc', backref='modele',
                               order_by='DocumentBloc.ordre',
                               cascade='all, delete-orphan')


class DocumentBloc(db.Model):
    """Bloc dans un modèle de document."""
    __tablename__ = 'document_bloc'
    id                = db.Column(db.Integer, primary_key=True)
    modele_id         = db.Column(db.Integer, db.ForeignKey('document_modele.id'), nullable=False)
    type              = db.Column(db.String(20), nullable=False)  # 'texte' ou 'section_bilan'
    contenu           = db.Column(db.Text, default='')
    ordre             = db.Column(db.Integer, default=99)
    label             = db.Column(db.String(100), default='')      # titre affiché à la génération
    filtre_categories = db.Column(db.Text, default=None)           # JSON ['refraction'] ou None
    sections_predef   = db.Column(db.Text, default=None)           # JSON ['correction_portee'] ou None

    @property
    def filtre_cats(self):
        import json
        return json.loads(self.filtre_categories) if self.filtre_categories else None

    @property
    def sections_predefinies(self):
        import json
        return json.loads(self.sections_predef) if self.sections_predef else None



class JournalAcces(db.Model):
    """Journal RGPD des accès aux dossiers patients."""
    __tablename__ = 'journal_acces'
    __table_args__ = {'extend_existing': True}
    id             = db.Column(db.Integer, primary_key=True)
    praticien_id   = db.Column(db.Integer, db.ForeignKey('praticien.id'), nullable=False)
    patient_id     = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=True)
    consultation_id= db.Column(db.Integer, db.ForeignKey('consultation.id'), nullable=True)
    action         = db.Column(db.String(100), nullable=False)  # 'consultation_detail', 'patient_detail', etc.
    detail         = db.Column(db.String(500), default='')
    ip_address     = db.Column(db.String(50), default='')
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    praticien = db.relationship('Praticien', foreign_keys=[praticien_id])
    patient   = db.relationship('Patient',   foreign_keys=[patient_id])


class WopiSession(db.Model):
    """Session WOPI temporaire pour l'édition Collabora."""
    __tablename__ = 'wopi_session'
    id              = db.Column(db.Integer, primary_key=True)
    token           = db.Column(db.String(64), unique=True, nullable=False)
    consultation_id = db.Column(db.Integer, db.ForeignKey('consultation.id'), nullable=False)
    section_type    = db.Column(db.String(50))
    section_ordre   = db.Column(db.Integer, default=0)
    nom_fichier     = db.Column(db.String(255), nullable=False)
    chemin_fichier  = db.Column(db.String(500), nullable=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at      = db.Column(db.DateTime)


class SectionDef(db.Model):
    __tablename__ = 'section_def'
    id       = db.Column(db.Integer, primary_key=True)
    type_key = db.Column(db.String(50), unique=True, nullable=False)
    label    = db.Column(db.String(100), nullable=False)
    ordre    = db.Column(db.Integer, default=99)
    builtin  = db.Column(db.Boolean, default=False)
    actif            = db.Column(db.Boolean, default=True)
    obs_defaut       = db.Column(db.Text, default='')
    avec_observations = db.Column(db.Boolean, default=True)
    categorie        = db.Column(db.String(50), default='')  # catégorie visuelle
    nb_colonnes      = db.Column(db.Integer, default=2)      # nb colonnes dans la grille
    champs     = db.relationship('ChampDef', backref='section',
                               order_by='ChampDef.ordre', cascade='all, delete-orphan')
    def to_dict(self):
        return {'label': self.label,
                'obs_defaut': self.obs_defaut or '',
                'avec_observations': self.avec_observations if self.avec_observations is not None else True,
                'categorie': self.categorie or '',
                'nb_colonnes': self.nb_colonnes or 2,
                'champs': [c.to_dict() for c in self.champs if c.actif]}


class ChampDef(db.Model):
    __tablename__ = 'champ_def'
    id         = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey('section_def.id'), nullable=False)
    name       = db.Column(db.String(50), nullable=False)
    label      = db.Column(db.String(100), nullable=False)
    type       = db.Column(db.String(20), default='text')
    ordre      = db.Column(db.Integer, default=99)
    actif      = db.Column(db.Boolean, default=True)
    options    = db.relationship('OptionDef', backref='champ',
                                 order_by='OptionDef.ordre', cascade='all, delete-orphan')
    def to_dict(self):
        d = {'name': self.name, 'label': self.label, 'type': self.type}
        if self.type == 'select':
            d['options'] = [o.valeur for o in self.options if o.actif]
        return d


class OptionDef(db.Model):
    __tablename__ = 'option_def'
    id       = db.Column(db.Integer, primary_key=True)
    champ_id = db.Column(db.Integer, db.ForeignKey('champ_def.id'), nullable=False)
    valeur   = db.Column(db.String(100), nullable=False)
    ordre    = db.Column(db.Integer, default=99)
    actif    = db.Column(db.Boolean, default=True)


class ModeleBilan(db.Model):
    """Modèle de bilan réutilisable."""
    __tablename__ = 'modele_bilan'
    id      = db.Column(db.Integer, primary_key=True)
    nom     = db.Column(db.String(100), nullable=False)
    motif   = db.Column(db.String(200), default='')
    actif   = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sections = db.relationship('ModeleBilanSection', backref='modele',
                               order_by='ModeleBilanSection.ordre',
                               cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'nom': self.nom,
            'motif': self.motif or '',
            'sections': [s.type_key for s in self.sections]
        }


class ModeleBilanSection(db.Model):
    __tablename__ = 'modele_bilan_section'
    id        = db.Column(db.Integer, primary_key=True)
    modele_id = db.Column(db.Integer, db.ForeignKey('modele_bilan.id'), nullable=False)
    type_key  = db.Column(db.String(50), nullable=False)
    ordre     = db.Column(db.Integer, default=99)


# ============================================================
# HELPERS
# ============================================================

def admin_required(f):
    """Décorateur : réserve la route aux admins."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Accès réservé aux administrateurs.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def get_current_cabinet():
    """Retourne le cabinet sélectionné en session, ou None."""
    cab_id = session.get('cabinet_id')
    if cab_id:
        return Cabinet.query.get(cab_id)
    return None


def get_cabinets_praticien():
    """Cabinets où le praticien courant est rattaché."""
    if not current_user.is_authenticated:
        return []
    pcs = PraticienCabinet.query.filter_by(
        praticien_id=current_user.id).join(Cabinet).filter(Cabinet.actif==True).all()
    return [pc.cabinet for pc in pcs]


def get_sections():
    secs = SectionDef.query.filter_by(actif=True).order_by(SectionDef.ordre).all()
    return {s.type_key: s.to_dict() for s in secs}, [s.type_key for s in secs]


def slugify(text):
    text = unicodedata.normalize('NFD', text.lower())
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]+', '_', text).strip('_')[:40]


def _parse_date(val):
    if not val: return None
    try: return datetime.strptime(val, '%Y-%m-%d').date()
    except ValueError: return None


# ============================================================
# AUTH
# ============================================================

@login_manager.user_loader
def load_user(user_id): return Praticien.query.get(int(user_id))


def log_action(action, patient_id=None, consultation_id=None):
    db.session.add(JournalAcces(
        praticien_id=current_user.id if current_user.is_authenticated else None,
        patient_id=patient_id, consultation_id=consultation_id,
        action=action, ip_address=request.remote_addr))
    db.session.commit()


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
        if _check_rate_limit(ip):
            flash('Trop de tentatives de connexion. Réessayez dans 15 minutes.', 'danger')
            return render_template('login.html')
        p = Praticien.query.filter_by(login=request.form.get('login'), actif=True).first()
        if p and p.check_password(request.form.get('password', '')):
            login_user(p)
            if getattr(p, 'is_default', False) or p.login == 'admin' and p.nom == '':
                return redirect(url_for('setup_premier_compte'))
            return redirect(url_for('index'))
        _record_attempt(ip)
        flash('Identifiants ou mot de passe incorrects.', 'danger')
    return render_template('login.html')


@app.route('/setup/premier-compte', methods=['GET', 'POST'])
@login_required
def setup_premier_compte():
    """Création du premier vrai compte — remplace le compte admin par défaut."""
    if not current_user.is_default:
        return redirect(url_for('index'))
    if request.method == 'POST':
        prenom = request.form.get('prenom', '').strip()
        nom    = request.form.get('nom', '').strip()
        login_ = request.form.get('login', '').strip()
        mdp    = request.form.get('password', '').strip()
        mdp2   = request.form.get('password2', '').strip()
        if not all([prenom, nom, login_, mdp]):
            flash('Tous les champs sont obligatoires.', 'danger')
        elif mdp != mdp2:
            flash('Les mots de passe ne correspondent pas.', 'danger')
        elif len(mdp) < 6:
            flash('Le mot de passe doit contenir au moins 6 caractères.', 'danger')
        elif Praticien.query.filter_by(login=login_).first():
            flash('Cet identifiant est déjà utilisé.', 'danger')
        else:
            # Créer le vrai compte admin
            nouveau = Praticien(
                prenom    = prenom,
                nom       = nom,
                login     = login_,
                role      = 'admin',
                actif     = True,
                is_default= False,
            )
            nouveau.set_password(mdp)
            db.session.add(nouveau)
            db.session.flush()
            # Supprimer le compte admin par défaut
            admin_default = Praticien.query.filter_by(is_default=True).first()
            if admin_default:
                logout_user()
                db.session.delete(admin_default)
            db.session.commit()
            flash('Compte créé. Connectez-vous avec vos nouveaux identifiants.', 'success')
            return redirect(url_for('login'))
    return render_template('setup/premier_compte.html')


@app.route('/logout')
@login_required
def logout():
    session.pop('cabinet_id', None)
    logout_user()
    return redirect(url_for('login'))


@app.route('/changer-cabinet', methods=['POST'])
@login_required
def changer_cabinet():
    cab_id = request.form.get('cabinet_id', type=int)
    if cab_id:
        session['cabinet_id'] = cab_id
    else:
        session.pop('cabinet_id', None)
    return redirect(request.referrer or url_for('index'))


@app.route('/api/historique_section')
@login_required
def api_historique_section():
    """Retourne l'historique d'un type de section pour un patient donné."""
    patient_id   = request.args.get('patient_id', type=int)
    type_key     = request.args.get('type_key', '')
    exclude_id   = request.args.get('exclude_id', type=int)  # consultation courante à exclure

    if not patient_id or not type_key:
        return jsonify([])

    # Récupérer toutes les consultations du patient sauf la courante
    query = Consultation.query.filter(Consultation.patient_id == patient_id)
    if exclude_id:
        query = query.filter(Consultation.id != exclude_id)
    consultations = query.order_by(Consultation.date_consult.desc()).all()

    results = []
    for c in consultations:
        sections = [s for s in c.sections if s.type == type_key]
        for sec in sections:
            results.append({
                'consultation_id': c.id,
                'date': c.date_consult.strftime('%d/%m/%Y'),
                'motif': c.motif or '',
                'observations': sec.observations or '',
                'donnees': sec.get_donnees(),
            })

    return jsonify(results)


@app.route('/api/communes')
@login_required
def api_communes():
    """Retourne les communes pour un code postal via l'API geo.api.gouv.fr."""
    import urllib.request, urllib.parse
    cp = request.args.get('cp', '').strip()
    if len(cp) < 2:
        return jsonify([])
    try:
        url = f"https://geo.api.gouv.fr/communes?codePostal={urllib.parse.quote(cp)}&fields=nom&format=json&geometry=centre"
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.loads(r.read().decode())
        communes = sorted(set(c['nom'] for c in data))
        return jsonify(communes)
    except Exception:
        return jsonify([])


# ============================================================
# PATIENTS
# ============================================================

def _derniere_activite(patient):
    """Retourne la date la plus récente parmi bilans et tous les suivis."""
    from datetime import date as date_type
    dates = []
    for c in patient.consultations:
        if c.date_consult: dates.append(c.date_consult)
    for s in SuiviAmblyopie.query.filter_by(patient_id=patient.id).all():
        dates.append(s.derniere_seance_date)
    for s in SuiviVB.query.filter_by(patient_id=patient.id).all():
        dates.append(s.derniere_seance_date)
    for s in SuiviNV.query.filter_by(patient_id=patient.id).all():
        dates.append(s.derniere_seance_date)
    for s in SuiviBV.query.filter_by(patient_id=patient.id).all():
        dates.append(s.derniere_seance_date)
    return max(dates) if dates else None


@app.route('/')
@login_required
def index():
    patients = Patient.query.order_by(Patient.nom).all()
    activites = {p.id: _derniere_activite(p) for p in patients}
    return render_template('patients/liste.html', patients=patients, activites=activites)


@app.route('/patient/nouveau', methods=['GET', 'POST'])
@login_required
def patient_nouveau():
    if request.method == 'POST':
        nom    = request.form['nom'].strip().upper()
        prenom = request.form['prenom'].strip().capitalize()
        ddn    = _parse_date(request.form.get('date_naissance'))

        # Détecter les doublons (même nom, prénom, DDN)
        # Sauf si l'utilisateur a confirmé vouloir créer quand même
        forcer_creation = request.form.get('forcer_creation') == '1'
        if not forcer_creation and ddn:
            doublons = Patient.query.filter_by(nom=nom, prenom=prenom,
                                               date_naissance=ddn).all()
            if doublons:
                # Repasser les données du formulaire pour pré-remplir si l'user revient
                form_data = request.form.to_dict()
                return render_template('patients/edition.html',
                                       patient=None,
                                       doublons=doublons,
                                       form_data=form_data)

        p = Patient(nom=nom, prenom=prenom,
                    date_naissance=ddn,
                    sexe=request.form.get('sexe'),
                    rue=request.form.get('rue'),
                    code_postal=request.form.get('code_postal'),
                    commune=request.form.get('commune'),
                    telephone=request.form.get('telephone'),
                    email=request.form.get('email'),
                    medecin_referent=request.form.get('medecin_referent'),
                    num_secu=request.form.get('num_secu'),
                    notes_admin=request.form.get('notes_admin'),
                    praticien_id=current_user.id)
        db.session.add(p); db.session.commit()
        log_action('creation_patient', patient_id=p.id)
        flash(f'Patient {p} créé.', 'success')
        return redirect(url_for('patient_detail', patient_id=p.id))
    return render_template('patients/edition.html', patient=None)


@app.route('/patient/<int:patient_id>')
@login_required
def patient_detail(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    sections, _ = get_sections()
    log_acces('patient_detail', patient_id=patient_id,
              detail=f'{patient.prenom} {patient.nom}')
    # Mélanger bilans et suivis dans l'ordre chronologique inversé
    suivis_amb = SuiviAmblyopie.query.filter_by(patient_id=patient_id).all()
    suivis_vb  = SuiviVB.query.filter_by(patient_id=patient_id).all()
    suivis_bv  = SuiviBV.query.filter_by(patient_id=patient_id).all()
    suivis_nv  = SuiviNV.query.filter_by(patient_id=patient_id).all()
    timeline = []
    for c in patient.consultations:
        timeline.append({'type': 'bilan', 'date': c.date_consult, 'obj': c})
    for s in suivis_amb:
        timeline.append({'type': 'suivi', 'date': s.derniere_seance_date, 'obj': s})
    for s in suivis_vb:
        timeline.append({'type': 'suivi_vb', 'date': s.derniere_seance_date, 'obj': s})
    for s in suivis_nv:
        timeline.append({'type': 'suivi_nv', 'date': s.derniere_seance_date, 'obj': s})
    for s in suivis_bv:
        timeline.append({'type': 'suivi_bv_reeds', 'date': s.derniere_seance_date, 'obj': s})
    timeline.sort(key=lambda x: x['date'], reverse=True)
    praticiens = Praticien.query.filter_by(actif=True).order_by(Praticien.nom).all()
    return render_template('patients/fiche.html', patient=patient,
                           sections_def=sections, timeline=timeline,
                           praticiens=praticiens)


@app.route('/patient/<int:patient_id>/dossier')
@login_required
def patient_dossier(patient_id):
    """Page d'impression du dossier patient complet."""
    patient = Patient.query.get_or_404(patient_id)
    sections, _ = get_sections()
    log_acces('impression_dossier', patient_id=patient_id,
              detail=f'{patient.prenom} {patient.nom}')
    cabinet = get_current_cabinet()
    pc = None
    if cabinet:
        pc = PraticienCabinet.query.filter_by(
            praticien_id=current_user.id, cabinet_id=cabinet.id).first()
    consultations = Consultation.query.filter_by(patient_id=patient_id)\
        .order_by(Consultation.date_consult.desc()).all()
    from datetime import datetime
    return render_template('patients/dossier_print.html',
                           patient=patient,
                           consultations=consultations,
                           sections=sections,
                           cabinet=cabinet,
                           pc=pc,
                           praticien=current_user,
                           now=datetime.today())



    patient = Patient.query.get_or_404(patient_id)
    sections, _ = get_sections()
    log_action('lecture_patient', patient_id=patient_id)
    return render_template('patients/fiche.html', patient=patient, sections_def=sections)


@app.route('/patient/<int:patient_id>/modifier', methods=['GET', 'POST'])
@login_required
def patient_modifier(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        patient.nom=request.form['nom'].strip().upper()
        patient.prenom=request.form['prenom'].strip().capitalize()
        patient.date_naissance=_parse_date(request.form.get('date_naissance'))
        patient.sexe=request.form.get('sexe')
        patient.rue=request.form.get('rue')
        patient.code_postal=request.form.get('code_postal')
        patient.commune=request.form.get('commune')
        patient.telephone=request.form.get('telephone')
        patient.email=request.form.get('email')
        patient.medecin_referent=request.form.get('medecin_referent')
        patient.num_secu=request.form.get('num_secu'); patient.notes_admin=request.form.get('notes_admin')
        db.session.commit(); log_action('modification_patient', patient_id=patient_id)
        flash('Fiche patient mise à jour.', 'success')
        return redirect(url_for('patient_detail', patient_id=patient_id))
    return render_template('patients/edition.html', patient=patient)


@app.route('/patient/<int:patient_id>/supprimer', methods=['POST'])
@login_required
def patient_supprimer(patient_id):
    """Supprime un patient et toutes ses données."""
    patient = Patient.query.get_or_404(patient_id)
    confirmation = request.form.get('confirmation', '').strip().lower()
    if confirmation != 'oui':
        flash('Suppression annulée — vous devez taper "oui" pour confirmer.', 'warning')
        return redirect(url_for('patient_modifier', patient_id=patient_id))

    nom = str(patient)
    # Supprimer les fichiers liés puis les consultations
    for c in patient.consultations:
        for f in FichierSection.query.filter_by(consultation_id=c.id).all():
            chemin = os.path.join(app.config['UPLOAD_FOLDER'], 'sections',
                                  str(c.id), f.nom_stocke)
            if os.path.exists(chemin):
                try: os.remove(chemin)
                except: pass
            db.session.delete(f)
        for w in WopiSession.query.filter_by(consultation_id=c.id).all():
            db.session.delete(w)
        db.session.delete(c)
    db.session.flush()
    db.session.delete(patient)
    db.session.commit()
    flash(f'Patient {nom} supprimé.', 'success')
    return redirect(url_for('index'))


@app.route('/patient/<int:patient_id>/suivi-amblyopie/nouveau', methods=['GET', 'POST'])
@login_required
def suivi_amblyopie_nouveau(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    cabinet = get_current_cabinet()
    if request.method == 'POST':
        s = SuiviAmblyopie(
            patient_id   = patient_id,
            praticien_id = current_user.id,
            cabinet_id   = cabinet.id if cabinet else None,
            date_bilan   = _parse_date(request.form.get('date_bilan')) or datetime.utcnow().date(),
            lunettes_od  = request.form.get('lunettes_od','').strip(),
            lunettes_og  = request.form.get('lunettes_og','').strip(),
            av_od_init   = request.form.get('av_od_init','').strip(),
            av_og_init   = request.form.get('av_og_init','').strip(),
            ophthalmo    = request.form.get('ophthalmo','').strip(),
            stereo       = request.form.get('stereo','').strip(),
            ese          = request.form.get('ese','').strip(),
            versions     = request.form.get('versions','').strip(),
            date_cs      = _parse_date(request.form.get('date_cs')),
            traitement   = request.form.get('traitement','').strip(),
            prochain_rdv = request.form.get('prochain_rdv','').strip(),
            notes        = request.form.get('notes','').strip(),
        )
        db.session.add(s)
        db.session.flush()
        # Créer 10 séances vides
        for i in range(1, 11):
            db.session.add(SeanceAmblyopie(suivi_id=s.id, numero=i))
        db.session.commit()
        log_acces('creation_suivi_amblyopie', patient_id=patient_id)
        flash('Suivi amblyopie créé.', 'success')
        return redirect(url_for('suivi_amblyopie_detail', suivi_id=s.id))
    # Récupérer le dernier bilan pour pré-remplissage
    dernier_bilan = Consultation.query\
        .filter_by(patient_id=patient_id)\
        .order_by(Consultation.date_consult.desc()).first()
    prefill = {}
    if dernier_bilan:
        sections, _ = get_sections()
        for sec in dernier_bilan.sections:
            d = sec.get_donnees()
            if sec.type == 'correction_portee':
                od = f"{d.get('od_sph','')} {d.get('od_cyl','')} {d.get('od_axe','')}°".strip()
                og = f"{d.get('og_sph','')} {d.get('og_cyl','')} {d.get('og_axe','')}°".strip()
                if d.get('od_add'): od += f"\nAdd: {d['od_add']}"
                if d.get('og_add'): og += f"\nAdd: {d['og_add']}"
                prefill['lunettes_od'] = od.strip()
                prefill['lunettes_og'] = og.strip()
            elif sec.type == 'acuite':
                parts_od, parts_og = [], []
                if d.get('av_correction'): 
                    parts_od.append(d['av_correction'])
                    parts_og.append(d['av_correction'])
                if d.get('av_od_loin'): parts_od.append(f"Loin: {d['av_od_loin']}")
                if d.get('av_od_pres'): parts_od.append(f"Près: {d['av_od_pres']}")
                if d.get('av_og_loin'): parts_og.append(f"Loin: {d['av_og_loin']}")
                if d.get('av_og_pres'): parts_og.append(f"Près: {d['av_og_pres']}")
                if d.get('av_bino'): parts_od.append(f"Bino: {d['av_bino']}")
                prefill['av_od_init'] = '\n'.join(parts_od)
                prefill['av_og_init'] = '\n'.join(parts_og)
            elif sec.type == 'stereoscopie':
                parts = []
                if d.get('lang'): parts.append(f"Lang: {d['lang']}")
                if d.get('tno'): parts.append(f"TNO: {d['tno']}")
                prefill['stereo'] = '\n'.join(parts)
            elif sec.type == 'cover':
                parts = []
                if d.get('cover_loin'): parts.append(f"Loin: {d['cover_loin']}")
                if d.get('cover_pres'): parts.append(f"Près: {d['cover_pres']}")
                if d.get('dip_mm'): parts.append(f"DIP: {d['dip_mm']}mm")
                if d.get('ac_a'): parts.append(f"AC/A: {d['ac_a']}")
                prefill['ese'] = '\n'.join(parts)
            elif sec.type == 'motilite':
                prefill['versions'] = d.get('motilite', '')
            elif sec.type == 'anam':
                if d.get('medecin_prescripteur'):
                    prefill['ophthalmo'] = d.get('medecin_prescripteur', '')
        prefill['date_bilan'] = dernier_bilan.date_consult.strftime('%Y-%m-%d')
    return render_template('amblyopie/nouveau.html', patient=patient,
                           cabinet=cabinet, today=datetime.utcnow().date(),
                           prefill=prefill, dernier_bilan=dernier_bilan)


@app.route('/suivi-amblyopie/<int:suivi_id>', methods=['GET', 'POST'])
@login_required
def suivi_amblyopie_detail(suivi_id):
    s = SuiviAmblyopie.query.get_or_404(suivi_id)
    if request.method == 'POST':
        action = request.form.get('action', 'save')
        # Mise à jour en-tête
        s.date_bilan   = _parse_date(request.form.get('date_bilan')) or s.date_bilan
        s.lunettes_od  = request.form.get('lunettes_od','').strip()
        s.lunettes_og  = request.form.get('lunettes_og','').strip()
        s.av_od_init   = request.form.get('av_od_init','').strip()
        s.av_og_init   = request.form.get('av_og_init','').strip()
        s.ophthalmo    = request.form.get('ophthalmo','').strip()
        s.stereo       = request.form.get('stereo','').strip()
        s.ese          = request.form.get('ese','').strip()
        s.versions     = request.form.get('versions','').strip()
        s.date_cs      = _parse_date(request.form.get('date_cs'))
        s.traitement   = request.form.get('traitement','').strip()
        s.prochain_rdv = request.form.get('prochain_rdv','').strip()
        s.notes        = request.form.get('notes','').strip()
        s.updated_at   = datetime.utcnow()
        # Mise à jour séances
        for seance in s.seances:
            pfx = f'seance_{seance.id}_'
            seance.date_seance  = _parse_date(request.form.get(pfx+'date'))
            seance.occlusion    = request.form.get(pfx+'occlusion','').strip()
            seance.av_od        = request.form.get(pfx+'av_od','').strip()
            seance.av_og        = request.form.get(pfx+'av_og','').strip()
            seance.av_notes     = request.form.get(pfx+'av_notes','').strip()
            seance.ese          = request.form.get(pfx+'ese','').strip()
            seance.notes        = request.form.get(pfx+'notes','').strip()
            prat_id = request.form.get(pfx+'praticien_id','').strip()
            seance.praticien_id = int(prat_id) if prat_id else None
        # Ajouter une séance
        if action == 'ajouter_seance':
            next_num = max((se.numero for se in s.seances), default=0) + 1
            db.session.add(SeanceAmblyopie(suivi_id=s.id, numero=next_num))
        db.session.commit()
        flash('Suivi enregistré.', 'success')
        if action == 'generer':
            return redirect(url_for('suivi_amblyopie_generer', suivi_id=suivi_id))
        return redirect(url_for('suivi_amblyopie_detail', suivi_id=suivi_id))
    log_acces('lecture_suivi_amblyopie', patient_id=s.patient_id)
    praticiens = Praticien.query.filter_by(actif=True).order_by(Praticien.nom).all()
    return render_template('amblyopie/detail.html', suivi=s, praticiens=praticiens)


@app.route('/suivi-amblyopie/<int:suivi_id>/supprimer', methods=['POST'])
@login_required
def suivi_amblyopie_supprimer(suivi_id):
    s = SuiviAmblyopie.query.get_or_404(suivi_id)
    patient_id = s.patient_id
    db.session.delete(s)
    db.session.commit()
    flash('Suivi supprimé.', 'success')
    return redirect(url_for('patient_detail', patient_id=patient_id))


@app.route('/suivi-amblyopie/<int:suivi_id>/generer')
@login_required
def suivi_amblyopie_generer(suivi_id):
    """Génère le document Word du suivi amblyopie."""
    import zipfile, re, tempfile, os, shutil, uuid, urllib.parse
    s = SuiviAmblyopie.query.get_or_404(suivi_id)
    p = s.patient
    praticien = s.praticien
    cabinet   = s.cabinet
    pc = None
    if cabinet:
        pc = PraticienCabinet.query.filter_by(
            praticien_id=praticien.id, cabinet_id=cabinet.id).first()

    esc = lambda x: (x or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

    entete_path = os.path.join(app.root_path, 'entete.docx')
    tmpdir = tempfile.mkdtemp()
    with zipfile.ZipFile(entete_path, 'r') as z:
        doc_xml = z.read('word/document.xml').decode('utf-8')

    # Substitutions entête cabinet
    cab_rue     = (cabinet.rue or '') if cabinet else ''
    cab_cp_comm = f"{(cabinet.code_postal or '')} {(cabinet.commune or '')}".strip() if cabinet else ''
    cab_commune = (cabinet.commune or 'Yssingeaux') if cabinet else 'Yssingeaux'
    cab_tel     = (cabinet.telephone or '') if cabinet else ''
    cab_email   = (cabinet.email or '') if cabinet else ''
    adeli       = (pc.adeli if pc else '') or ''
    prat_nom    = f"{praticien.prenom} {praticien.nom}"
    prat_rpps   = praticien.rpps or ''
    prat_titre  = praticien.titre or 'Orthoptiste'

    def sub(xml, old, new):
        return xml.replace(old, esc(new)) if old in xml else xml

    doc_xml = sub(doc_xml, '130, Boulevard de la Paix', cab_rue)
    doc_xml = sub(doc_xml, '43200 Yssingeaux', cab_cp_comm)
    doc_xml = sub(doc_xml, '04 71 59 01 38', cab_tel)
    doc_xml = sub(doc_xml, 'orthoptistes-yssingeaux@outlook.fr', cab_email)
    doc_xml = sub(doc_xml, 'ADELI\xa0: 439287145', f'ADELI : {adeli}' if adeli else '')
    doc_xml = sub(doc_xml, 'RPPS\xa0: 10010253291', f'RPPS : {prat_rpps}' if prat_rpps else '')
    doc_xml = sub(doc_xml, ' Cyprien Nesme', f' {prat_nom}')
    doc_xml = sub(doc_xml, 'ORTHOPTISTE', prat_titre)
    doc_xml = sub(doc_xml, 'Prise de rendez-vous sur Doctolib', '')
    doc_xml = doc_xml.replace(
        f'A\xa0Yssingeaux, le </w:t></w:r><w:bookmarkEnd w:id="0"/>',
        f'A\xa0{esc(cab_commune)}, le {s.date_bilan.strftime("%d/%m/%Y")}</w:t></w:r><w:bookmarkEnd w:id="0"/>'
    )
    # Patient
    pat_nom = f'{p.prenom} {p.nom}'
    pat_ddn = p.date_naissance.strftime('%d/%m/%Y') if p.date_naissance else ''
    doc_xml = re.sub(
        r'<w:sdt><w:sdtPr><w:alias w:val="Nom"/>.*?<w:sdtContent>.*?</w:sdtContent></w:sdt>',
        f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="20"/></w:rPr><w:t>{esc(pat_nom)}</w:t></w:r>',
        doc_xml, flags=re.DOTALL)
    doc_xml = re.sub(r'<w:sdt><w:sdtPr><w:alias w:val="Pr[eé]nom"/>.*?</w:sdt>', '', doc_xml, flags=re.DOTALL)
    doc_xml = doc_xml.replace('DDN : </w:t></w:r>', f'DDN : {esc(pat_ddn)}</w:t></w:r>')
    doc_xml = re.sub(
        r'<w:sdt><w:sdtPr><w:alias w:val="Commentaires ".*?<w:sdtContent>.*?</w:sdtContent></w:sdt>',
        '<w:r><w:t></w:t></w:r>', doc_xml, flags=re.DOTALL)
    doc_xml = re.sub(r'<w:tab/><w:t xml:space="preserve">Âge\s*:\s*</w:t>.*?<w:t xml:space="preserve">Classe\s*:\s*</w:t>', '', doc_xml, flags=re.DOTALL)
    doc_xml = re.sub(r'<w:p[^>]*>(?:(?!</w:p>).)*?[Mm]édecin(?:(?!</w:p>).)*?</w:p>', '', doc_xml, flags=re.DOTALL)
    doc_xml = doc_xml.replace('<w:t>BILAN ORTHOPTIQUE</w:t>', '<w:t>SUIVI AMBLYOPIE</w:t>')

    def para(txt, bold=False, center=False, size=20, before=0, after=80):
        b = '<w:b/>' if bold else ''
        jc = f'<w:jc w:val="center"/>' if center else ''
        return (f'<w:p><w:pPr>{jc}<w:spacing w:before="{before}" w:after="{after}"/></w:pPr>'
                f'<w:r><w:rPr>{b}<w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                f'<w:sz w:val="{size}"/></w:rPr>'
                f'<w:t xml:space="preserve">{esc(txt)}</w:t></w:r></w:p>')

    def tbl_cell(txt, bold=False, bg=None, w=1000):
        b = '<w:b/>' if bold else ''
        shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{bg}"/>' if bg else ''
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shd}'
                f'<w:tcMar><w:top w:w="60"/><w:bottom w:w="60"/><w:left w:w="80"/><w:right w:w="80"/></w:tcMar>'
                f'</w:tcPr>'
                f'<w:p><w:r><w:rPr>{b}<w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                f'<w:sz w:val="18"/></w:rPr>'
                f'<w:t xml:space="preserve">{esc(txt)}</w:t></w:r></w:p></w:tc>')

    body = []
    # En-tête infos
    body.append(para(f'BO réalisé le : {s.date_bilan.strftime("%d/%m/%Y")}', bold=True, before=240))
    body.append(para(f'Lunettes — OD : {s.lunettes_od or "—"}   OG : {s.lunettes_og or "—"}'))
    body.append(para(f'AV initiale — OD : {s.av_od_init or "—"}   OG : {s.av_og_init or "—"}'))
    if s.ophthalmo: body.append(para(f'Ophtalmologiste : {s.ophthalmo}'))
    if s.traitement: body.append(para(f'Traitement : {s.traitement}'))
    if s.prochain_rdv: body.append(para(f'À revoir dans : {s.prochain_rdv}'))

    # Tableau séances
    HDR_BG = 'DAE9F7'
    col_w = [400, 900, 1400, 800, 800, 900, 1600]
    hdr_cells = ''.join([
        tbl_cell('#', bold=True, bg=HDR_BG, w=col_w[0]),
        tbl_cell('Date', bold=True, bg=HDR_BG, w=col_w[1]),
        tbl_cell('Occlusion', bold=True, bg=HDR_BG, w=col_w[2]),
        tbl_cell('AV OD', bold=True, bg=HDR_BG, w=col_w[3]),
        tbl_cell('AV OG', bold=True, bg=HDR_BG, w=col_w[4]),
        tbl_cell('ESE', bold=True, bg=HDR_BG, w=col_w[5]),
        tbl_cell('Notes', bold=True, bg=HDR_BG, w=col_w[6]),
    ])
    rows = f'<w:tr>{"".join(hdr_cells)}</w:tr>'
    for seance in s.seances:
        date_str = seance.date_seance.strftime('%d/%m/%Y') if seance.date_seance else ''
        row_bg = 'F4F8FD' if seance.numero % 2 == 0 else None
        cells = ''.join([
            tbl_cell(str(seance.numero), bold=True, bg=row_bg, w=col_w[0]),
            tbl_cell(date_str, bg=row_bg, w=col_w[1]),
            tbl_cell(seance.occlusion or '', bg=row_bg, w=col_w[2]),
            tbl_cell(seance.av_od or '', bg=row_bg, w=col_w[3]),
            tbl_cell(seance.av_og or '', bg=row_bg, w=col_w[4]),
            tbl_cell(seance.ese or '', bg=row_bg, w=col_w[5]),
            tbl_cell(seance.notes or '', bg=row_bg, w=col_w[6]),
        ])
        rows += f'<w:tr>{cells}</w:tr>'

    total_w = sum(col_w)
    gridcols = ''.join(f'<w:gridCol w:w="{w}"/>' for w in col_w)
    table = (f'<w:tbl>'
             f'<w:tblPr><w:tblW w:w="{total_w}" w:type="dxa"/>'
             f'<w:tblBorders>'
             f'<w:top w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:left w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:bottom w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:right w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:insideH w:val="single" w:sz="4" w:color="DAE9F7"/>'
             f'<w:insideV w:val="single" w:sz="4" w:color="DAE9F7"/>'
             f'</w:tblBorders></w:tblPr>'
             f'<w:tblGrid>{gridcols}</w:tblGrid>'
             f'{rows}</w:tbl>')
    body.append(f'<w:p><w:pPr><w:spacing w:before="240"/></w:pPr></w:p>')
    body.append(table)
    if s.notes:
        body.append(para(f'Notes : {s.notes}', before=160))

    doc_xml = doc_xml.replace('</w:body>', '\n'.join(body) + '</w:body>')

    new_out = os.path.join(tmpdir, 'suivi_amblyopie.docx')
    with zipfile.ZipFile(entete_path, 'r') as zin:
        with zipfile.ZipFile(new_out, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == 'word/document.xml':
                    zout.writestr(item, doc_xml.encode('utf-8'))
                else:
                    zout.writestr(item, zin.read(item.filename))

    nom = f'{p.nom}_{p.prenom}_SuiviAmblyopie_{s.date_bilan.strftime("%Y%m%d")}.docx'
    from flask import send_file
    return send_file(new_out, as_attachment=True, download_name=nom,
                     mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


@app.route('/patient/<int:patient_id>/suivi-bv/nouveau', methods=['GET', 'POST'])
@login_required
def suivi_bv_nouveau(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    cabinet = get_current_cabinet()
    if request.method == 'POST':
        s = SuiviBV(
            patient_id  = patient_id,
            praticien_id= current_user.id,
            cabinet_id  = cabinet.id if cabinet else None,
            date_debut  = _parse_date(request.form.get('date_debut')) or datetime.utcnow().date(),
            notes       = request.form.get('notes','').strip(),
        )
        db.session.add(s); db.session.flush()
        db.session.add(SeanceBV(suivi_id=s.id, numero=1))
        db.session.commit()
        flash('Suivi basse vision créé.', 'success')
        return redirect(url_for('suivi_bv_detail', suivi_id=s.id))
    dernier_bilan = Consultation.query.filter_by(patient_id=patient_id)\
        .order_by(Consultation.date_consult.desc()).first()
    sections, _ = get_sections()
    return render_template('bv/nouveau.html', patient=patient, cabinet=cabinet,
                           today=datetime.utcnow().date(),
                           dernier_bilan=dernier_bilan, sections_def=sections)


@app.route('/suivi-bv/<int:suivi_id>', methods=['GET', 'POST'])
@login_required
def suivi_bv_detail(suivi_id):
    s = SuiviBV.query.get_or_404(suivi_id)
    if request.method == 'POST':
        action = request.form.get('action', 'save')
        s.date_debut = _parse_date(request.form.get('date_debut')) or s.date_debut
        s.notes      = request.form.get('notes','').strip()
        s.updated_at = datetime.utcnow()
        for seance in s.seances:
            pfx = f'seance_{seance.id}_'
            seance.date_seance  = _parse_date(request.form.get(pfx+'date'))
            seance.av_od        = request.form.get(pfx+'av_od','').strip()
            seance.av_og        = request.form.get(pfx+'av_og','').strip()
            seance.av_notes     = request.form.get(pfx+'av_notes','').strip()
            seance.exercices    = request.form.get(pfx+'exercices','').strip()
            seance.notes        = request.form.get(pfx+'notes','').strip()
            prat_id = request.form.get(pfx+'praticien_id','').strip()
            seance.praticien_id = int(prat_id) if prat_id else None
        if action == 'ajouter_seance':
            next_num = max((se.numero for se in s.seances), default=0) + 1
            db.session.add(SeanceBV(suivi_id=s.id, numero=next_num))
        db.session.commit()
        flash('Suivi enregistré.', 'success')
        if action == 'generer':
            return redirect(url_for('suivi_bv_generer', suivi_id=suivi_id))
        return redirect(url_for('suivi_bv_detail', suivi_id=suivi_id))
    log_acces('lecture_suivi_bv', patient_id=s.patient_id)
    praticiens = Praticien.query.filter_by(actif=True).order_by(Praticien.nom).all()
    dernier_bilan = Consultation.query.filter_by(patient_id=s.patient_id)\
        .order_by(Consultation.date_consult.desc()).first()
    sections, _ = get_sections()
    return render_template('bv/detail.html', suivi=s, praticiens=praticiens,
                           dernier_bilan=dernier_bilan, sections_def=sections)


@app.route('/suivi-bv/<int:suivi_id>/supprimer', methods=['POST'])
@login_required
def suivi_bv_supprimer(suivi_id):
    s = SuiviBV.query.get_or_404(suivi_id)
    patient_id = s.patient_id
    db.session.delete(s); db.session.commit()
    flash('Suivi basse vision supprimé.', 'success')
    return redirect(url_for('patient_detail', patient_id=patient_id))


@app.route('/suivi-bv/<int:suivi_id>/generer')
@login_required
def suivi_bv_generer(suivi_id):
    """Génère le document Word du suivi basse vision."""
    import zipfile, re as _re, tempfile, os as _os
    s   = SuiviBV.query.get_or_404(suivi_id)
    p   = s.patient; prat = s.praticien; cab = s.cabinet; pc = None
    if cab:
        pc = PraticienCabinet.query.filter_by(praticien_id=prat.id, cabinet_id=cab.id).first()
    esc = lambda x: (x or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    entete_path = _os.path.join(app.root_path, 'entete.docx')
    tmpdir = tempfile.mkdtemp()
    with zipfile.ZipFile(entete_path, 'r') as z:
        doc_xml = z.read('word/document.xml').decode('utf-8')
    cab_rue     = (cab.rue or '') if cab else ''
    cab_cp_comm = f"{(cab.code_postal or '')} {(cab.commune or '')}".strip() if cab else ''
    cab_commune = (cab.commune or 'Yssingeaux') if cab else 'Yssingeaux'
    cab_tel     = (cab.telephone or '') if cab else ''
    cab_email   = (cab.email or '') if cab else ''
    adeli       = (pc.adeli if pc else '') or ''
    prat_nom    = f"{prat.prenom} {prat.nom}"
    def sub(xml, old, new): return xml.replace(old, esc(new)) if old in xml else xml
    doc_xml = sub(doc_xml, '130, Boulevard de la Paix', cab_rue)
    doc_xml = sub(doc_xml, '43200 Yssingeaux', cab_cp_comm)
    doc_xml = sub(doc_xml, '04 71 59 01 38', cab_tel)
    doc_xml = sub(doc_xml, 'orthoptistes-yssingeaux@outlook.fr', cab_email)
    doc_xml = sub(doc_xml, 'ADELI\xa0: 439287145', f'ADELI : {adeli}' if adeli else '')
    doc_xml = sub(doc_xml, 'RPPS\xa0: 10010253291', f'RPPS : {prat.rpps or ""}' if prat.rpps else '')
    doc_xml = sub(doc_xml, ' Cyprien Nesme', f' {prat_nom}')
    doc_xml = sub(doc_xml, 'ORTHOPTISTE', prat.titre or 'Orthoptiste')
    doc_xml = sub(doc_xml, 'Prise de rendez-vous sur Doctolib', '')
    doc_xml = doc_xml.replace(
        'A\xa0Yssingeaux, le </w:t></w:r><w:bookmarkEnd w:id="0"/>',
        f'A\xa0{esc(cab_commune)}, le {s.date_debut.strftime("%d/%m/%Y")}</w:t></w:r><w:bookmarkEnd w:id="0"/>'
    )
    pat_nom = f'{p.prenom} {p.nom}'
    pat_ddn = p.date_naissance.strftime('%d/%m/%Y') if p.date_naissance else ''
    doc_xml = _re.sub(r'<w:sdt><w:sdtPr><w:alias w:val="Nom"/>.*?<w:sdtContent>.*?</w:sdtContent></w:sdt>',
        f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="20"/></w:rPr><w:t>{esc(pat_nom)}</w:t></w:r>',
        doc_xml, flags=_re.DOTALL)
    doc_xml = _re.sub(r'<w:sdt><w:sdtPr><w:alias w:val="Pr[eé]nom"/>.*?</w:sdt>', '', doc_xml, flags=_re.DOTALL)
    doc_xml = doc_xml.replace('DDN : </w:t></w:r>', f'DDN : {esc(pat_ddn)}</w:t></w:r>')
    doc_xml = _re.sub(r'<w:sdt><w:sdtPr><w:alias w:val="Commentaires ".*?<w:sdtContent>.*?</w:sdtContent></w:sdt>', '<w:r><w:t></w:t></w:r>', doc_xml, flags=_re.DOTALL)
    doc_xml = _re.sub(r'<w:tab/><w:t xml:space="preserve">Âge\s*:\s*</w:t>.*?<w:t xml:space="preserve">Classe\s*:\s*</w:t>', '', doc_xml, flags=_re.DOTALL)
    doc_xml = _re.sub(r'<w:p[^>]*>(?:(?!</w:p>).)*?[Mm]édecin(?:(?!</w:p>).)*?</w:p>', '', doc_xml, flags=_re.DOTALL)
    doc_xml = doc_xml.replace('<w:t>BILAN ORTHOPTIQUE</w:t>', '<w:t>RÉÉDUCATION BASSE VISION</w:t>')

    def para(txt, bold=False, size=20, before=0, after=80):
        b = '<w:b/>' if bold else ''
        return (f'<w:p><w:pPr><w:spacing w:before="{before}" w:after="{after}"/></w:pPr>'
                f'<w:r><w:rPr>{b}<w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                f'<w:sz w:val="{size}"/></w:rPr>'
                f'<w:t xml:space="preserve">{esc(txt)}</w:t></w:r></w:p>')

    def tbl_cell(txt, bold=False, bg=None, w=1000):
        b = '<w:b/>' if bold else ''
        shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{bg}"/>' if bg else ''
        lines = str(txt).split('\n')
        paras = ''
        for i, line in enumerate(lines):
            br = '<w:br/>' if i < len(lines)-1 else ''
            paras += (f'<w:r><w:rPr>{b}<w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                      f'<w:sz w:val="18"/></w:rPr>'
                      f'<w:t xml:space="preserve">{esc(line)}</w:t>{br}</w:r>')
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shd}'
                f'<w:tcMar><w:top w:w="60"/><w:bottom w:w="60"/><w:left w:w="80"/><w:right w:w="80"/></w:tcMar>'
                f'</w:tcPr><w:p>{paras}</w:p></w:tc>')

    body = [para(f'Début rééducation : {s.date_debut.strftime("%d/%m/%Y")}', bold=True, before=240)]
    if s.notes: body.append(para(f'Notes : {s.notes}'))

    HDR_BG = 'DAE9F7'
    col_w = [400, 900, 1700, 1700, 1100]
    hdr = ''.join([
        tbl_cell('#',                   bold=True, bg=HDR_BG, w=col_w[0]),
        tbl_cell('Date',                bold=True, bg=HDR_BG, w=col_w[1]),
        tbl_cell('Acuité OD/OG',        bold=True, bg=HDR_BG, w=col_w[2]),
        tbl_cell('Exercices',           bold=True, bg=HDR_BG, w=col_w[3]),
        tbl_cell('Notes',               bold=True, bg=HDR_BG, w=col_w[4]),
    ])
    rows = f'<w:tr>{hdr}</w:tr>'
    for seance in s.seances:
        date_str = seance.date_seance.strftime('%d/%m/%Y') if seance.date_seance else ''
        if seance.praticien:
            date_str += f'\n{seance.praticien.prenom} {seance.praticien.nom}'
        row_bg = 'F4F8FD' if seance.numero % 2 == 0 else None
        cells = ''.join([
            tbl_cell(str(seance.numero), bold=True, bg=row_bg, w=col_w[0]),
            tbl_cell(date_str,           bg=row_bg, w=col_w[1]),
            tbl_cell(seance.acuite    or '', bg=row_bg, w=col_w[2]),
            tbl_cell(seance.exercices or '', bg=row_bg, w=col_w[3]),
            tbl_cell(seance.notes     or '', bg=row_bg, w=col_w[4]),
        ])
        rows += f'<w:tr>{cells}</w:tr>'

    total_w = sum(col_w)
    gridcols = ''.join(f'<w:gridCol w:w="{w}"/>' for w in col_w)
    table = (f'<w:tbl><w:tblPr><w:tblW w:w="{total_w}" w:type="dxa"/>'
             f'<w:tblBorders>'
             f'<w:top w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:left w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:bottom w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:right w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:insideH w:val="single" w:sz="4" w:color="DAE9F7"/>'
             f'<w:insideV w:val="single" w:sz="4" w:color="DAE9F7"/>'
             f'</w:tblBorders></w:tblPr>'
             f'<w:tblGrid>{gridcols}</w:tblGrid>{rows}</w:tbl>')
    body.append(f'<w:p><w:pPr><w:spacing w:before="240"/></w:pPr></w:p>')
    body.append(table)
    doc_xml = doc_xml.replace('</w:body>', '\n'.join(body) + '</w:body>')
    new_out = _os.path.join(tmpdir, 'suivi_bv.docx')
    with zipfile.ZipFile(entete_path, 'r') as zin:
        with zipfile.ZipFile(new_out, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                zout.writestr(item, doc_xml.encode('utf-8') if item.filename == 'word/document.xml' else zin.read(item.filename))
    nom = f'{p.nom}_{p.prenom}_SuiviBV_{s.date_debut.strftime("%Y%m%d")}.docx'
    from flask import send_file
    return send_file(new_out, as_attachment=True, download_name=nom,
                     mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


@app.route('/patient/<int:patient_id>/suivi-nv/nouveau', methods=['GET', 'POST'])
@login_required
def suivi_nv_nouveau(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    cabinet = get_current_cabinet()
    if request.method == 'POST':
        s = SuiviNV(
            patient_id  = patient_id,
            praticien_id= current_user.id,
            cabinet_id  = cabinet.id if cabinet else None,
            date_debut  = _parse_date(request.form.get('date_debut')) or datetime.utcnow().date(),
            notes       = request.form.get('notes','').strip(),
        )
        db.session.add(s); db.session.flush()
        db.session.add(SeanceNV(suivi_id=s.id, numero=1))
        db.session.commit()
        flash('Suivi neurovisuel créé.', 'success')
        return redirect(url_for('suivi_nv_detail', suivi_id=s.id))
    dernier_bilan = Consultation.query\
        .filter_by(patient_id=patient_id)\
        .order_by(Consultation.date_consult.desc()).first()
    sections, _ = get_sections()
    return render_template('nv/nouveau.html', patient=patient, cabinet=cabinet,
                           today=datetime.utcnow().date(),
                           dernier_bilan=dernier_bilan, sections_def=sections)


@app.route('/suivi-nv/<int:suivi_id>', methods=['GET', 'POST'])
@login_required
def suivi_nv_detail(suivi_id):
    s = SuiviNV.query.get_or_404(suivi_id)
    if request.method == 'POST':
        action = request.form.get('action', 'save')
        s.date_debut = _parse_date(request.form.get('date_debut')) or s.date_debut
        s.notes      = request.form.get('notes','').strip()
        s.updated_at = datetime.utcnow()
        for seance in s.seances:
            pfx = f'seance_{seance.id}_'
            seance.date_seance  = _parse_date(request.form.get(pfx+'date'))
            seance.vb_acco_omot = request.form.get(pfx+'vb_acco_omot','').strip()
            seance.neurovisuel  = request.form.get(pfx+'neurovisuel','').strip()
            seance.notes        = request.form.get(pfx+'notes','').strip()
            prat_id = request.form.get(pfx+'praticien_id','').strip()
            seance.praticien_id = int(prat_id) if prat_id else None
        if action == 'ajouter_seance':
            next_num = max((se.numero for se in s.seances), default=0) + 1
            db.session.add(SeanceNV(suivi_id=s.id, numero=next_num))
        db.session.commit()
        flash('Suivi enregistré.', 'success')
        if action == 'generer':
            return redirect(url_for('suivi_nv_generer', suivi_id=suivi_id))
        return redirect(url_for('suivi_nv_detail', suivi_id=suivi_id))
    log_acces('lecture_suivi_nv', patient_id=s.patient_id)
    praticiens = Praticien.query.filter_by(actif=True).order_by(Praticien.nom).all()
    dernier_bilan = Consultation.query\
        .filter_by(patient_id=s.patient_id)\
        .order_by(Consultation.date_consult.desc()).first()
    sections, _ = get_sections()
    return render_template('nv/detail.html', suivi=s, praticiens=praticiens,
                           dernier_bilan=dernier_bilan, sections_def=sections)


@app.route('/suivi-nv/<int:suivi_id>/supprimer', methods=['POST'])
@login_required
def suivi_nv_supprimer(suivi_id):
    s = SuiviNV.query.get_or_404(suivi_id)
    patient_id = s.patient_id
    db.session.delete(s); db.session.commit()
    flash('Suivi neurovisuel supprimé.', 'success')
    return redirect(url_for('patient_detail', patient_id=patient_id))


@app.route('/suivi-nv/<int:suivi_id>/generer')
@login_required
def suivi_nv_generer(suivi_id):
    """Génère le document Word du suivi neurovisuel."""
    import zipfile, re as _re, tempfile, os as _os
    s   = SuiviNV.query.get_or_404(suivi_id)
    p   = s.patient
    prat= s.praticien
    cab = s.cabinet
    pc  = None
    if cab:
        pc = PraticienCabinet.query.filter_by(praticien_id=prat.id, cabinet_id=cab.id).first()

    esc = lambda x: (x or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    entete_path = _os.path.join(app.root_path, 'entete.docx')
    tmpdir = tempfile.mkdtemp()
    with zipfile.ZipFile(entete_path, 'r') as z:
        doc_xml = z.read('word/document.xml').decode('utf-8')

    cab_rue     = (cab.rue or '') if cab else ''
    cab_cp_comm = f"{(cab.code_postal or '')} {(cab.commune or '')}".strip() if cab else ''
    cab_commune = (cab.commune or 'Yssingeaux') if cab else 'Yssingeaux'
    cab_tel     = (cab.telephone or '') if cab else ''
    cab_email   = (cab.email or '') if cab else ''
    adeli       = (pc.adeli if pc else '') or ''
    prat_nom    = f"{prat.prenom} {prat.nom}"
    prat_rpps   = prat.rpps or ''
    prat_titre  = prat.titre or 'Orthoptiste'

    def sub(xml, old, new): return xml.replace(old, esc(new)) if old in xml else xml
    doc_xml = sub(doc_xml, '130, Boulevard de la Paix', cab_rue)
    doc_xml = sub(doc_xml, '43200 Yssingeaux', cab_cp_comm)
    doc_xml = sub(doc_xml, '04 71 59 01 38', cab_tel)
    doc_xml = sub(doc_xml, 'orthoptistes-yssingeaux@outlook.fr', cab_email)
    doc_xml = sub(doc_xml, 'ADELI\xa0: 439287145', f'ADELI : {adeli}' if adeli else '')
    doc_xml = sub(doc_xml, 'RPPS\xa0: 10010253291', f'RPPS : {prat_rpps}' if prat_rpps else '')
    doc_xml = sub(doc_xml, ' Cyprien Nesme', f' {prat_nom}')
    doc_xml = sub(doc_xml, 'ORTHOPTISTE', prat_titre)
    doc_xml = sub(doc_xml, 'Prise de rendez-vous sur Doctolib', '')
    doc_xml = doc_xml.replace(
        'A\xa0Yssingeaux, le </w:t></w:r><w:bookmarkEnd w:id="0"/>',
        f'A\xa0{esc(cab_commune)}, le {s.date_debut.strftime("%d/%m/%Y")}</w:t></w:r><w:bookmarkEnd w:id="0"/>'
    )
    pat_nom = f'{p.prenom} {p.nom}'
    pat_ddn = p.date_naissance.strftime('%d/%m/%Y') if p.date_naissance else ''
    doc_xml = _re.sub(
        r'<w:sdt><w:sdtPr><w:alias w:val="Nom"/>.*?<w:sdtContent>.*?</w:sdtContent></w:sdt>',
        f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="20"/></w:rPr><w:t>{esc(pat_nom)}</w:t></w:r>',
        doc_xml, flags=_re.DOTALL)
    doc_xml = _re.sub(r'<w:sdt><w:sdtPr><w:alias w:val="Pr[eé]nom"/>.*?</w:sdt>', '', doc_xml, flags=_re.DOTALL)
    doc_xml = doc_xml.replace('DDN : </w:t></w:r>', f'DDN : {esc(pat_ddn)}</w:t></w:r>')
    doc_xml = _re.sub(r'<w:sdt><w:sdtPr><w:alias w:val="Commentaires ".*?<w:sdtContent>.*?</w:sdtContent></w:sdt>', '<w:r><w:t></w:t></w:r>', doc_xml, flags=_re.DOTALL)
    doc_xml = _re.sub(r'<w:tab/><w:t xml:space="preserve">Âge\s*:\s*</w:t>.*?<w:t xml:space="preserve">Classe\s*:\s*</w:t>', '', doc_xml, flags=_re.DOTALL)
    doc_xml = _re.sub(r'<w:p[^>]*>(?:(?!</w:p>).)*?[Mm]édecin(?:(?!</w:p>).)*?</w:p>', '', doc_xml, flags=_re.DOTALL)
    doc_xml = doc_xml.replace('<w:t>BILAN ORTHOPTIQUE</w:t>', '<w:t>RÉÉDUCATION NEUROVISUELLE</w:t>')

    def para(txt, bold=False, size=20, before=0, after=80):
        b = '<w:b/>' if bold else ''
        return (f'<w:p><w:pPr><w:spacing w:before="{before}" w:after="{after}"/></w:pPr>'
                f'<w:r><w:rPr>{b}<w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                f'<w:sz w:val="{size}"/></w:rPr>'
                f'<w:t xml:space="preserve">{esc(txt)}</w:t></w:r></w:p>')

    def tbl_cell(txt, bold=False, bg=None, w=1000):
        b = '<w:b/>' if bold else ''
        shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{bg}"/>' if bg else ''
        lines = str(txt).split('\n')
        paras = ''
        for i, line in enumerate(lines):
            br = '<w:br/>' if i < len(lines)-1 else ''
            paras += (f'<w:r><w:rPr>{b}<w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                      f'<w:sz w:val="18"/></w:rPr>'
                      f'<w:t xml:space="preserve">{esc(line)}</w:t>{br}</w:r>')
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shd}'
                f'<w:tcMar><w:top w:w="60"/><w:bottom w:w="60"/><w:left w:w="80"/><w:right w:w="80"/></w:tcMar>'
                f'</w:tcPr><w:p>{paras}</w:p></w:tc>')

    body = []
    body.append(para(f'Début rééducation : {s.date_debut.strftime("%d/%m/%Y")}', bold=True, before=240))
    if s.notes:
        body.append(para(f'Notes : {s.notes}'))

    HDR_BG = 'DAE9F7'
    col_w = [400, 900, 1600, 1600, 1300]
    hdr = ''.join([
        tbl_cell('#',              bold=True, bg=HDR_BG, w=col_w[0]),
        tbl_cell('Date',           bold=True, bg=HDR_BG, w=col_w[1]),
        tbl_cell('VB-ACCO-OMOT',  bold=True, bg=HDR_BG, w=col_w[2]),
        tbl_cell('Neurovisuel',    bold=True, bg=HDR_BG, w=col_w[3]),
        tbl_cell('Notes',          bold=True, bg=HDR_BG, w=col_w[4]),
    ])
    rows = f'<w:tr>{hdr}</w:tr>'
    for seance in s.seances:
        date_str = seance.date_seance.strftime('%d/%m/%Y') if seance.date_seance else ''
        if seance.praticien:
            date_str += f'\n{seance.praticien.prenom} {seance.praticien.nom}'
        row_bg = 'F4F8FD' if seance.numero % 2 == 0 else None
        cells = ''.join([
            tbl_cell(str(seance.numero), bold=True, bg=row_bg, w=col_w[0]),
            tbl_cell(date_str,           bg=row_bg, w=col_w[1]),
            tbl_cell(seance.vb_acco_omot or '', bg=row_bg, w=col_w[2]),
            tbl_cell(seance.neurovisuel  or '', bg=row_bg, w=col_w[3]),
            tbl_cell(seance.notes        or '', bg=row_bg, w=col_w[4]),
        ])
        rows += f'<w:tr>{cells}</w:tr>'

    total_w = sum(col_w)
    gridcols = ''.join(f'<w:gridCol w:w="{w}"/>' for w in col_w)
    table = (f'<w:tbl>'
             f'<w:tblPr><w:tblW w:w="{total_w}" w:type="dxa"/>'
             f'<w:tblBorders>'
             f'<w:top w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:left w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:bottom w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:right w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:insideH w:val="single" w:sz="4" w:color="DAE9F7"/>'
             f'<w:insideV w:val="single" w:sz="4" w:color="DAE9F7"/>'
             f'</w:tblBorders></w:tblPr>'
             f'<w:tblGrid>{gridcols}</w:tblGrid>'
             f'{rows}</w:tbl>')
    body.append(f'<w:p><w:pPr><w:spacing w:before="240"/></w:pPr></w:p>')
    body.append(table)

    doc_xml = doc_xml.replace('</w:body>', '\n'.join(body) + '</w:body>')
    new_out = _os.path.join(tmpdir, 'suivi_nv.docx')
    with zipfile.ZipFile(entete_path, 'r') as zin:
        with zipfile.ZipFile(new_out, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == 'word/document.xml':
                    zout.writestr(item, doc_xml.encode('utf-8'))
                else:
                    zout.writestr(item, zin.read(item.filename))

    nom = f'{p.nom}_{p.prenom}_SuiviNV_{s.date_debut.strftime("%Y%m%d")}.docx'
    from flask import send_file
    return send_file(new_out, as_attachment=True, download_name=nom,
                     mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


@app.route('/patient/<int:patient_id>/suivi-vb/nouveau', methods=['GET', 'POST'])
@login_required
def suivi_vb_nouveau(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    cabinet = get_current_cabinet()
    if request.method == 'POST':
        s = SuiviVB(
            patient_id  = patient_id,
            praticien_id= current_user.id,
            cabinet_id  = cabinet.id if cabinet else None,
            date_debut  = _parse_date(request.form.get('date_debut')) or datetime.utcnow().date(),
            notes       = request.form.get('notes','').strip(),
        )
        db.session.add(s); db.session.flush()
        db.session.add(SeanceVB(suivi_id=s.id, numero=1))
        db.session.commit()
        flash('Suivi VB créé.', 'success')
        return redirect(url_for('suivi_vb_detail', suivi_id=s.id))
    # Dernier bilan pour affichage
    dernier_bilan = Consultation.query\
        .filter_by(patient_id=patient_id)\
        .order_by(Consultation.date_consult.desc()).first()
    sections, _ = get_sections()
    return render_template('vb/nouveau.html', patient=patient, cabinet=cabinet,
                           today=datetime.utcnow().date(),
                           dernier_bilan=dernier_bilan, sections_def=sections)


@app.route('/suivi-vb/<int:suivi_id>', methods=['GET', 'POST'])
@login_required
def suivi_vb_detail(suivi_id):
    s = SuiviVB.query.get_or_404(suivi_id)
    if request.method == 'POST':
        action = request.form.get('action', 'save')
        s.date_debut = _parse_date(request.form.get('date_debut')) or s.date_debut
        s.notes      = request.form.get('notes','').strip()
        s.updated_at = datetime.utcnow()
        for seance in s.seances:
            pfx = f'seance_{seance.id}_'
            seance.date_seance   = _parse_date(request.form.get(pfx+'date'))
            seance.fusion        = request.form.get(pfx+'fusion','').strip()
            seance.accommodation = request.form.get(pfx+'accommodation','').strip()
            seance.stereogrammes = request.form.get(pfx+'stereogrammes','').strip()
            seance.notes         = request.form.get(pfx+'notes','').strip()
            prat_id = request.form.get(pfx+'praticien_id','').strip()
            seance.praticien_id  = int(prat_id) if prat_id else None
        if action == 'ajouter_seance':
            next_num = max((se.numero for se in s.seances), default=0) + 1
            db.session.add(SeanceVB(suivi_id=s.id, numero=next_num))
        db.session.commit()
        flash('Suivi enregistré.', 'success')
        if action == 'generer':
            return redirect(url_for('suivi_vb_generer', suivi_id=suivi_id))
        return redirect(url_for('suivi_vb_detail', suivi_id=suivi_id))
    log_acces('lecture_suivi_vb', patient_id=s.patient_id)
    praticiens = Praticien.query.filter_by(actif=True).order_by(Praticien.nom).all()
    dernier_bilan = Consultation.query\
        .filter_by(patient_id=s.patient_id)\
        .order_by(Consultation.date_consult.desc()).first()
    sections, _ = get_sections()
    return render_template('vb/detail.html', suivi=s, praticiens=praticiens,
                           dernier_bilan=dernier_bilan, sections_def=sections)


@app.route('/suivi-vb/<int:suivi_id>/supprimer', methods=['POST'])
@login_required
def suivi_vb_supprimer(suivi_id):
    s = SuiviVB.query.get_or_404(suivi_id)
    patient_id = s.patient_id
    db.session.delete(s); db.session.commit()
    flash('Suivi VB supprimé.', 'success')
    return redirect(url_for('patient_detail', patient_id=patient_id))


@app.route('/suivi-vb/<int:suivi_id>/generer')
@login_required
def suivi_vb_generer(suivi_id):
    """Génère le document Word du suivi VB."""
    import zipfile, re as _re, tempfile, os as _os
    s   = SuiviVB.query.get_or_404(suivi_id)
    p   = s.patient
    prat= s.praticien
    cab = s.cabinet
    pc  = None
    if cab:
        pc = PraticienCabinet.query.filter_by(praticien_id=prat.id, cabinet_id=cab.id).first()

    esc = lambda x: (x or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    entete_path = _os.path.join(app.root_path, 'entete.docx')
    tmpdir = tempfile.mkdtemp()
    with zipfile.ZipFile(entete_path, 'r') as z:
        doc_xml = z.read('word/document.xml').decode('utf-8')

    cab_rue     = (cab.rue or '') if cab else ''
    cab_cp_comm = f"{(cab.code_postal or '')} {(cab.commune or '')}".strip() if cab else ''
    cab_commune = (cab.commune or 'Yssingeaux') if cab else 'Yssingeaux'
    cab_tel     = (cab.telephone or '') if cab else ''
    cab_email   = (cab.email or '') if cab else ''
    adeli       = (pc.adeli if pc else '') or ''
    prat_nom    = f"{prat.prenom} {prat.nom}"
    prat_rpps   = prat.rpps or ''
    prat_titre  = prat.titre or 'Orthoptiste'

    def sub(xml, old, new): return xml.replace(old, esc(new)) if old in xml else xml
    doc_xml = sub(doc_xml, '130, Boulevard de la Paix', cab_rue)
    doc_xml = sub(doc_xml, '43200 Yssingeaux', cab_cp_comm)
    doc_xml = sub(doc_xml, '04 71 59 01 38', cab_tel)
    doc_xml = sub(doc_xml, 'orthoptistes-yssingeaux@outlook.fr', cab_email)
    doc_xml = sub(doc_xml, 'ADELI\xa0: 439287145', f'ADELI : {adeli}' if adeli else '')
    doc_xml = sub(doc_xml, 'RPPS\xa0: 10010253291', f'RPPS : {prat_rpps}' if prat_rpps else '')
    doc_xml = sub(doc_xml, ' Cyprien Nesme', f' {prat_nom}')
    doc_xml = sub(doc_xml, 'ORTHOPTISTE', prat_titre)
    doc_xml = sub(doc_xml, 'Prise de rendez-vous sur Doctolib', '')
    doc_xml = doc_xml.replace(
        'A\xa0Yssingeaux, le </w:t></w:r><w:bookmarkEnd w:id="0"/>',
        f'A\xa0{esc(cab_commune)}, le {s.date_debut.strftime("%d/%m/%Y")}</w:t></w:r><w:bookmarkEnd w:id="0"/>'
    )
    pat_nom = f'{p.prenom} {p.nom}'
    pat_ddn = p.date_naissance.strftime('%d/%m/%Y') if p.date_naissance else ''
    doc_xml = _re.sub(
        r'<w:sdt><w:sdtPr><w:alias w:val="Nom"/>.*?<w:sdtContent>.*?</w:sdtContent></w:sdt>',
        f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="20"/></w:rPr><w:t>{esc(pat_nom)}</w:t></w:r>',
        doc_xml, flags=_re.DOTALL)
    doc_xml = _re.sub(r'<w:sdt><w:sdtPr><w:alias w:val="Pr[eé]nom"/>.*?</w:sdt>', '', doc_xml, flags=_re.DOTALL)
    doc_xml = doc_xml.replace('DDN : </w:t></w:r>', f'DDN : {esc(pat_ddn)}</w:t></w:r>')
    doc_xml = _re.sub(r'<w:sdt><w:sdtPr><w:alias w:val="Commentaires ".*?<w:sdtContent>.*?</w:sdtContent></w:sdt>', '<w:r><w:t></w:t></w:r>', doc_xml, flags=_re.DOTALL)
    doc_xml = _re.sub(r'<w:tab/><w:t xml:space="preserve">Âge\s*:\s*</w:t>.*?<w:t xml:space="preserve">Classe\s*:\s*</w:t>', '', doc_xml, flags=_re.DOTALL)
    doc_xml = _re.sub(r'<w:p[^>]*>(?:(?!</w:p>).)*?[Mm]édecin(?:(?!</w:p>).)*?</w:p>', '', doc_xml, flags=_re.DOTALL)
    doc_xml = doc_xml.replace('<w:t>BILAN ORTHOPTIQUE</w:t>', '<w:t>RÉÉDUCATION VISION BINOCULAIRE</w:t>')

    def para(txt, bold=False, size=20, before=0, after=80):
        b = '<w:b/>' if bold else ''
        return (f'<w:p><w:pPr><w:spacing w:before="{before}" w:after="{after}"/></w:pPr>'
                f'<w:r><w:rPr>{b}<w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                f'<w:sz w:val="{size}"/></w:rPr>'
                f'<w:t xml:space="preserve">{esc(txt)}</w:t></w:r></w:p>')

    def tbl_cell(txt, bold=False, bg=None, w=1000, wrap=True):
        b = '<w:b/>' if bold else ''
        shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{bg}"/>' if bg else ''
        wrap_xml = '<w:wordWrap/>' if wrap else ''
        lines = str(txt).split('\n')
        paras = ''
        for i, line in enumerate(lines):
            br = '<w:br/>' if i < len(lines)-1 else ''
            paras += (f'<w:r><w:rPr>{b}<w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                      f'<w:sz w:val="18"/></w:rPr>'
                      f'<w:t xml:space="preserve">{esc(line)}</w:t>{br}</w:r>')
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shd}{wrap_xml}'
                f'<w:tcMar><w:top w:w="60"/><w:bottom w:w="60"/><w:left w:w="80"/><w:right w:w="80"/></w:tcMar>'
                f'</w:tcPr><w:p>{paras}</w:p></w:tc>')

    body = []
    body.append(para(f'Début rééducation : {s.date_debut.strftime("%d/%m/%Y")}', bold=True, before=240))
    if s.notes:
        body.append(para(f'Notes : {s.notes}'))

    HDR_BG = 'DAE9F7'
    col_w = [400, 900, 1400, 1400, 1400, 1300]
    hdr = ''.join([
        tbl_cell('#', bold=True, bg=HDR_BG, w=col_w[0]),
        tbl_cell('Date', bold=True, bg=HDR_BG, w=col_w[1]),
        tbl_cell('Fusion', bold=True, bg=HDR_BG, w=col_w[2]),
        tbl_cell('Accommodation', bold=True, bg=HDR_BG, w=col_w[3]),
        tbl_cell('Stéréogrammes', bold=True, bg=HDR_BG, w=col_w[4]),
        tbl_cell('Notes', bold=True, bg=HDR_BG, w=col_w[5]),
    ])
    rows = f'<w:tr>{hdr}</w:tr>'
    for seance in s.seances:
        date_str = seance.date_seance.strftime('%d/%m/%Y') if seance.date_seance else ''
        if seance.praticien:
            date_str += f'\n{seance.praticien.prenom} {seance.praticien.nom}'
        row_bg = 'F4F8FD' if seance.numero % 2 == 0 else None
        cells = ''.join([
            tbl_cell(str(seance.numero), bold=True, bg=row_bg, w=col_w[0]),
            tbl_cell(date_str, bg=row_bg, w=col_w[1]),
            tbl_cell(seance.fusion or '', bg=row_bg, w=col_w[2]),
            tbl_cell(seance.accommodation or '', bg=row_bg, w=col_w[3]),
            tbl_cell(seance.stereogrammes or '', bg=row_bg, w=col_w[4]),
            tbl_cell(seance.notes or '', bg=row_bg, w=col_w[5]),
        ])
        rows += f'<w:tr>{cells}</w:tr>'

    total_w = sum(col_w)
    gridcols = ''.join(f'<w:gridCol w:w="{w}"/>' for w in col_w)
    table = (f'<w:tbl>'
             f'<w:tblPr><w:tblW w:w="{total_w}" w:type="dxa"/>'
             f'<w:tblBorders>'
             f'<w:top w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:left w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:bottom w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:right w:val="single" w:sz="4" w:color="4472C4"/>'
             f'<w:insideH w:val="single" w:sz="4" w:color="DAE9F7"/>'
             f'<w:insideV w:val="single" w:sz="4" w:color="DAE9F7"/>'
             f'</w:tblBorders></w:tblPr>'
             f'<w:tblGrid>{gridcols}</w:tblGrid>'
             f'{rows}</w:tbl>')
    body.append(f'<w:p><w:pPr><w:spacing w:before="240"/></w:pPr></w:p>')
    body.append(table)

    doc_xml = doc_xml.replace('</w:body>', '\n'.join(body) + '</w:body>')
    new_out = _os.path.join(tmpdir, 'suivi_vb.docx')
    with zipfile.ZipFile(entete_path, 'r') as zin:
        with zipfile.ZipFile(new_out, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == 'word/document.xml':
                    zout.writestr(item, doc_xml.encode('utf-8'))
                else:
                    zout.writestr(item, zin.read(item.filename))

    nom = f'{p.nom}_{p.prenom}_SuiviVB_{s.date_debut.strftime("%Y%m%d")}.docx'
    from flask import send_file
    return send_file(new_out, as_attachment=True, download_name=nom,
                     mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


@app.route('/api/messages/count')
@login_required
def api_messages_count():
    count = db.session.query(Message.id).join(
        Conversation, Message.conversation_id == Conversation.id
    ).join(
        ConversationParticipant,
        ConversationParticipant.conversation_id == Conversation.id
    ).filter(
        ConversationParticipant.praticien_id == current_user.id,
        Message.expediteur_id != current_user.id
    ).outerjoin(
        MessageLu,
        db.and_(MessageLu.message_id == Message.id,
                MessageLu.praticien_id == current_user.id)
    ).filter(MessageLu.id == None).count()
    return {'count': count}


@app.route('/messages')
@login_required
def messages_liste():
    """Liste des conversations."""
    praticiens_all = Praticien.query.filter(
        Praticien.id != current_user.id, Praticien.actif == True
    ).order_by(Praticien.nom).all()

    convs_multi = Conversation.query.join(
        ConversationParticipant,
        ConversationParticipant.conversation_id == Conversation.id
    ).filter(
        ConversationParticipant.praticien_id == current_user.id
    ).order_by(Conversation.updated_at.desc()).all()

    return render_template('messages/liste.html',
                           convs_multi=convs_multi,
                           legacy_convs=[],
                           praticiens_all=praticiens_all)


@app.route('/messages/nouvelle-conversation', methods=['POST'])
@login_required
def messages_nouvelle_conversation():
    """Créer une nouvelle conversation (2+ participants)."""
    dest_ids = request.form.getlist('destinataires[]')
    contenu  = request.form.get('contenu', '').strip()
    titre    = request.form.get('titre', '').strip()
    if not contenu or not dest_ids:
        flash('Destinataire(s) et message requis.', 'danger')
        return redirect(url_for('messages_liste'))
    # Créer la conversation
    conv = Conversation(titre=titre, updated_at=datetime.utcnow())
    db.session.add(conv)
    db.session.flush()
    # Ajouter les participants (expéditeur + destinataires)
    participants = {current_user.id}
    for did in dest_ids:
        try: participants.add(int(did))
        except (ValueError, TypeError): pass
    for pid in participants:
        db.session.add(ConversationParticipant(
            conversation_id=conv.id, praticien_id=pid))
    # Ajouter le premier message
    db.session.add(Message(
        conversation_id=conv.id,
        expediteur_id=current_user.id,
        contenu=contenu
    ))
    db.session.commit()
    return redirect(url_for('messages_conversation_multi', conv_id=conv.id))


@app.route('/messages/conv/<int:conv_id>', methods=['GET', 'POST'])
@login_required
def messages_conversation_multi(conv_id):
    """Conversation multi-participants."""
    conv = Conversation.query.get_or_404(conv_id)
    # Vérifier que l'utilisateur est participant
    if not any(p.praticien_id == current_user.id for p in conv.participants):
        abort(403)
    if request.method == 'POST':
        action = request.form.get('action', 'send')
        if action == 'send':
            contenu = request.form.get('contenu', '').strip()
            if contenu:
                db.session.add(Message(
                    conversation_id=conv.id,
                    expediteur_id=current_user.id,
                    contenu=contenu
                ))
                conv.updated_at = datetime.utcnow()
                db.session.commit()
        elif action == 'ajouter_participant':
            pid = request.form.get('praticien_id', type=int)
            if pid and not any(p.praticien_id == pid for p in conv.participants):
                db.session.add(ConversationParticipant(
                    conversation_id=conv.id, praticien_id=pid))
                db.session.commit()
                flash('Participant ajouté.', 'success')
        elif action == 'quitter':
            part = ConversationParticipant.query.filter_by(
                conversation_id=conv.id, praticien_id=current_user.id).first()
            if part:
                db.session.delete(part)
                db.session.commit()
                flash('Vous avez quitté la conversation.', 'success')
                return redirect(url_for('messages_liste'))
        return redirect(url_for('messages_conversation_multi', conv_id=conv_id))
    # Marquer les messages comme lus
    for msg in conv.messages:
        if msg.expediteur_id != current_user.id:
            msg.marquer_lu(current_user.id)
    db.session.commit()
    praticiens_all = Praticien.query.filter(
        Praticien.id != current_user.id, Praticien.actif == True
    ).order_by(Praticien.nom).all()
    # Exclure ceux déjà dans la conversation
    participant_ids = {p.praticien_id for p in conv.participants}
    praticiens_ajoutables = [p for p in praticiens_all if p.id not in participant_ids]
    return render_template('messages/conversation_multi.html',
                           conv=conv,
                           praticiens_ajoutables=praticiens_ajoutables)


@app.route('/messages/<int:praticien_id>', methods=['GET', 'POST'])
@login_required
def messages_conversation(praticien_id):
    """Conversation legacy 1-to-1."""
    autre = Praticien.query.get_or_404(praticien_id)
    if request.method == 'POST':
        contenu = request.form.get('contenu', '').strip()
        if contenu:
            db.session.add(Message(
                expediteur_id=current_user.id,
                destinataire_id=praticien_id,
                contenu=contenu,
                conversation_id=None
            ))
            db.session.commit()
        return redirect(url_for('messages_conversation', praticien_id=praticien_id))
    Message.query.filter_by(
        expediteur_id=praticien_id, destinataire_id=current_user.id,
        lu=False, conversation_id=None
    ).update({'lu': True})
    db.session.commit()
    msgs = Message.query.filter(
        Message.conversation_id == None,
        db.or_(
            db.and_(Message.expediteur_id==current_user.id, Message.destinataire_id==praticien_id),
            db.and_(Message.expediteur_id==praticien_id, Message.destinataire_id==current_user.id)
        )
    ).order_by(Message.created_at.asc()).all()
    praticiens_all = Praticien.query.filter(
        Praticien.id != current_user.id, Praticien.actif == True
    ).order_by(Praticien.nom).all()
    return render_template('messages/conversation.html', msgs=msgs, autre=autre,
                           praticiens_all=praticiens_all, convs=[])


def _get_convs():
    return []


@app.route('/message/nouveau', methods=['POST'])
@login_required
def message_nouveau():
    dest_ids = request.form.getlist('destinataires[]')
    contenu  = request.form.get('contenu', '').strip()
    titre    = request.form.get('titre', '').strip()
    if not contenu or not dest_ids:
        return redirect(url_for('messages_liste'))
    # Créer via nouvelle conversation
    from flask import redirect as _redirect
    return _redirect(url_for('messages_nouvelle_conversation'))



@app.route('/favoris')
@login_required
def favoris():
    favs = Favori.query.filter_by(praticien_id=current_user.id).order_by(Favori.categorie, Favori.ordre).all()
    # Grouper par catégorie
    groupes = {}
    for f in favs:
        cat = f.categorie or 'Sans catégorie'
        groupes.setdefault(cat, []).append(f)
    return render_template('favoris/index.html', groupes=groupes)


@app.route('/favoris/nouveau', methods=['POST'])
@login_required
def favori_nouveau():
    url = request.form.get('url', '').strip()
    if url and not url.startswith('http'):
        url = 'https://' + url
    nom = request.form.get('nom', '').strip() or url
    # Favicon auto via Google S2
    from urllib.parse import urlparse
    try:
        domain = urlparse(url).netloc
        favicon_url = f'https://www.google.com/s2/favicons?domain={domain}&sz=64'
    except Exception:
        favicon_url = ''
    f = Favori(
        praticien_id=current_user.id,
        nom=nom,
        url=url,
        categorie=request.form.get('categorie', '').strip(),
        couleur=request.form.get('couleur', '#f0f4ff'),
        favicon_url=favicon_url,
        ordre=Favori.query.filter_by(praticien_id=current_user.id).count()
    )
    db.session.add(f)
    db.session.commit()
    return redirect(url_for('favoris'))


@app.route('/favoris/<int:favori_id>/modifier', methods=['POST'])
@login_required
def favori_modifier(favori_id):
    f = Favori.query.get_or_404(favori_id)
    if f.praticien_id != current_user.id: abort(403)
    url = request.form.get('url', '').strip()
    if url and not url.startswith('http'):
        url = 'https://' + url
    f.nom       = request.form.get('nom', '').strip() or url
    f.url       = url
    f.categorie = request.form.get('categorie', '').strip()
    f.couleur   = request.form.get('couleur', '#f0f4ff')
    # Rafraîchir le favicon si l'URL a changé
    from urllib.parse import urlparse
    try:
        domain = urlparse(url).netloc
        f.favicon_url = f'https://www.google.com/s2/favicons?domain={domain}&sz=64'
    except Exception:
        pass
    db.session.commit()
    return redirect(url_for('favoris'))


@app.route('/favoris/<int:favori_id>/supprimer', methods=['POST'])
@login_required
def favori_supprimer(favori_id):
    f = Favori.query.get_or_404(favori_id)
    if f.praticien_id != current_user.id: abort(403)
    db.session.delete(f)
    db.session.commit()
    return redirect(url_for('favoris'))


@app.route('/favoris/reordonner', methods=['POST'])
@login_required
def favoris_reordonner():
    ids = request.json.get('ids', [])
    for i, fid in enumerate(ids):
        f = Favori.query.get(fid)
        if f and f.praticien_id == current_user.id:
            f.ordre = i
    db.session.commit()
    return '', 204


@app.route('/repondeur', methods=['GET', 'POST'])
@login_required
def repondeur():
    """Bloc-notes répondeur partagé par cabinet."""
    # Tous les cabinets du praticien connecté
    pcs = PraticienCabinet.query.filter_by(praticien_id=current_user.id).all()
    cabinets = [Cabinet.query.get(pc.cabinet_id) for pc in pcs if Cabinet.query.get(pc.cabinet_id)]
    if not cabinets:
        cabinets = Cabinet.query.filter_by(actif=True).all()

    # Cabinet sélectionné (par défaut le premier)
    cabinet_id = request.args.get('cabinet_id', type=int) or \
                 request.form.get('cabinet_id', type=int) or \
                 (cabinets[0].id if cabinets else None)
    cabinet = next((c for c in cabinets if c.id == cabinet_id), cabinets[0] if cabinets else None)

    if request.method == 'POST':
        if cabinet:
            cabinet.repondeur = request.form.get('repondeur', '')
            cabinet.repondeur_updated_by = current_user.prenom + ' ' + current_user.nom
            cabinet.repondeur_updated_at = datetime.utcnow()
            db.session.commit()
            flash('✅ Répondeur mis à jour.', 'success')
        return redirect(url_for('repondeur', cabinet_id=cabinet_id))
    return render_template('repondeur/index.html', cabinet=cabinet,
                           cabinets=cabinets, cabinet_id=cabinet_id)


@app.route('/notes', methods=['GET'])
@login_required
def notes_liste():
    onglet = request.args.get('onglet', 'notes')
    notes = Note.query.filter_by(praticien_id=current_user.id)\
        .order_by(Note.epingle.desc(), Note.updated_at.desc()).all()
    taches = Tache.query.filter(
        db.or_(Tache.praticien_id == current_user.id,
               Tache.assigne_a == current_user.id)
    ).order_by(Tache.statut, Tache.echeance.asc().nullslast(),
               Tache.priorite).all()
    praticiens = Praticien.query.filter_by(actif=True)\
        .order_by(Praticien.nom).all()
    nb_taches = Tache.query.filter(
        db.or_(Tache.praticien_id == current_user.id,
               Tache.assigne_a == current_user.id),
        Tache.statut != 'termine'
    ).count()
    return render_template('notes/index.html', notes=notes, taches=taches,
                           onglet=onglet,
                           praticiens=praticiens, nb_taches=nb_taches,
                           today=datetime.utcnow().date())


@app.route('/notes/nouvelle', methods=['POST'])
@login_required
def note_nouvelle():
    n = Note(praticien_id=current_user.id,
             titre=request.form.get('titre', '').strip(),
             contenu=request.form.get('contenu', '').strip(),
             couleur=request.form.get('couleur', '#FEFCE8'))
    db.session.add(n); db.session.commit()
    return redirect(url_for('notes_liste', onglet='notes'))


@app.route('/notes/<int:note_id>/modifier', methods=['POST'])
@login_required
def note_modifier(note_id):
    n = Note.query.get_or_404(note_id)
    if n.praticien_id != current_user.id: abort(403)
    n.titre   = request.form.get('titre', '').strip()
    n.contenu = request.form.get('contenu', '').strip()
    n.couleur = request.form.get('couleur', n.couleur)
    n.updated_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('notes_liste', onglet='notes'))


@app.route('/notes/<int:note_id>/epingler', methods=['POST'])
@login_required
def note_epingler(note_id):
    n = Note.query.get_or_404(note_id)
    if n.praticien_id != current_user.id: abort(403)
    n.epingle = not n.epingle
    db.session.commit()
    return redirect(url_for('notes_liste', onglet='notes'))


@app.route('/notes/<int:note_id>/supprimer', methods=['POST'])
@login_required
def note_supprimer(note_id):
    n = Note.query.get_or_404(note_id)
    if n.praticien_id != current_user.id: abort(403)
    db.session.delete(n); db.session.commit()
    return redirect(url_for('notes_liste', onglet='notes'))


@app.route('/taches/nouvelle', methods=['POST'])
@login_required
def tache_nouvelle():
    echeance = None
    if request.form.get('echeance'):
        try: echeance = datetime.strptime(request.form['echeance'], '%Y-%m-%d').date()
        except ValueError: pass
    assigne_a = request.form.get('assigne_a', type=int) or None
    patient_id = request.form.get('patient_id', type=int) or None
    t = Tache(praticien_id=current_user.id,
              titre      =request.form.get('titre', '').strip(),
              description=request.form.get('description', '').strip(),
              echeance   =echeance,
              priorite   =request.form.get('priorite', 'normale'),
              assigne_a  =assigne_a,
              patient_id =patient_id)
    db.session.add(t); db.session.commit()
    # Rediriger vers fiche patient si vient de là
    if patient_id and request.form.get('from_patient'):
        return redirect(url_for('patient_detail', patient_id=patient_id))
    return redirect(url_for('notes_liste', onglet='taches'))


@app.route('/taches/<int:tache_id>/statut', methods=['POST'])
@login_required
def tache_statut(tache_id):
    t = Tache.query.get_or_404(tache_id)
    statuts = ['a_faire', 'en_cours', 'termine']
    idx = statuts.index(t.statut) if t.statut in statuts else 0
    t.statut = statuts[(idx + 1) % len(statuts)]
    t.updated_at = datetime.utcnow()
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify({'statut': t.statut})
    return redirect(url_for('notes_liste', onglet='taches'))


@app.route('/taches/<int:tache_id>/modifier', methods=['POST'])
@login_required
def tache_modifier(tache_id):
    t = Tache.query.get_or_404(tache_id)
    if t.praticien_id != current_user.id and t.assigne_a != current_user.id: abort(403)
    echeance = None
    if request.form.get('echeance'):
        try: echeance = datetime.strptime(request.form['echeance'], '%Y-%m-%d').date()
        except ValueError: pass
    t.titre       = request.form.get('titre', '').strip()
    t.description = request.form.get('description', '').strip()
    t.echeance    = echeance
    t.priorite    = request.form.get('priorite', 'normale')
    t.assigne_a   = request.form.get('assigne_a', type=int) or None
    t.updated_at  = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('notes_liste', onglet='taches'))


@app.route('/taches/<int:tache_id>/supprimer', methods=['POST'])
@login_required
def tache_supprimer(tache_id):
    t = Tache.query.get_or_404(tache_id)
    if t.praticien_id != current_user.id and t.assigne_a != current_user.id: abort(403)
    db.session.delete(t); db.session.commit()
    return redirect(url_for('notes_liste', onglet='taches'))


@app.route('/notes-patient/<int:patient_id>', methods=['POST'])
@login_required
def note_patient_ajouter(patient_id):
    """Ajouter une note partagée sur un patient."""
    contenu = request.form.get('contenu', '').strip()
    if contenu:
        db.session.add(NotePatient(
            patient_id=patient_id,
            praticien_id=current_user.id,
            contenu=contenu
        ))
        db.session.commit()
    redirect_to = request.form.get('redirect', url_for('patient_detail', patient_id=patient_id))
    return redirect(redirect_to)


@app.route('/notes-patient/<int:note_id>/supprimer', methods=['POST'])
@login_required
def note_patient_supprimer(note_id):
    note = NotePatient.query.get_or_404(note_id)
    patient_id = note.patient_id
    if note.praticien_id == current_user.id or current_user.role == 'admin':
        db.session.delete(note)
        db.session.commit()
    return redirect(url_for('patient_detail', patient_id=patient_id))


@app.route('/admin/sauvegarde/config-distante', methods=['POST'])
@login_required
@admin_required
def admin_sauvegarde_config_distante():
    """Configure la sauvegarde SFTP distante."""
    cfg = ConfigSauvegarde.query.first()
    if not cfg:
        cfg = ConfigSauvegarde()
        db.session.add(cfg)
    cfg.sftp_host  = request.form.get('sftp_host', '').strip()
    cfg.sftp_port  = int(request.form.get('sftp_port', 22) or 22)
    cfg.sftp_user  = request.form.get('sftp_user', '').strip()
    cfg.sftp_path  = request.form.get('sftp_path', '/backups/orthoptie').strip()
    cfg.sftp_actif = request.form.get('sftp_actif') == '1'
    cfg.updated_at = datetime.utcnow()
    db.session.commit()
    # Générer le fichier de config shell pour le cron
    import os
    data_dir = app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie')
    config_path = os.path.join(data_dir, 'sftp_config.sh')
    with open(config_path, 'w') as f:
        f.write(f'SFTP_ACTIF="{1 if cfg.sftp_actif else 0}"\n')
        f.write(f'SFTP_HOST="{cfg.sftp_host}"\n')
        f.write(f'SFTP_PORT="{cfg.sftp_port}"\n')
        f.write(f'SFTP_USER="{cfg.sftp_user}"\n')
        f.write(f'SFTP_PATH="{cfg.sftp_path}"\n')
    flash('Configuration sauvegarde distante enregistrée.', 'success')
    return redirect(url_for('admin_sauvegarde'))


@app.route('/admin/sauvegarde/generer-cle', methods=['POST'])
@login_required
@admin_required
def admin_generer_cle_ssh():
    """Génère une paire de clés SSH RSA 4096 bits."""
    import subprocess, os
    cfg = ConfigSauvegarde.query.first()
    if not cfg:
        cfg = ConfigSauvegarde()
        db.session.add(cfg)
    key_dir = os.path.join(app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie'), 'ssh')
    os.makedirs(key_dir, exist_ok=True)
    key_path = os.path.join(key_dir, 'backup_key')
    if os.path.exists(key_path):
        os.remove(key_path)
    if os.path.exists(key_path + '.pub'):
        os.remove(key_path + '.pub')
    try:
        subprocess.run([
            'ssh-keygen', '-t', 'rsa', '-b', '4096',
            '-f', key_path, '-N', '', '-C', 'orthoptie-backup'
        ], check=True, capture_output=True)
        with open(key_path) as f:
            cfg.cle_privee = f.read()
        with open(key_path + '.pub') as f:
            cfg.cle_publique = f.read()
        db.session.commit()
        flash('Paire de clés SSH générée avec succès.', 'success')
    except Exception as e:
        flash(f'Erreur lors de la génération des clés : {e}', 'danger')
    return redirect(url_for('admin_sauvegarde'))


@app.route('/admin/sauvegarde/test-connexion', methods=['POST'])
@login_required
@admin_required
def admin_test_connexion_sftp():
    """Teste la connexion SFTP."""
    import subprocess, os, tempfile
    cfg = ConfigSauvegarde.query.first()
    if not cfg or not cfg.sftp_host or not cfg.cle_privee:
        flash('Configuration incomplète — renseignez l\'hôte et générez une clé SSH.', 'danger')
        return redirect(url_for('admin_sauvegarde'))
    key_dir = os.path.join(app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie'), 'ssh')
    key_path = os.path.join(key_dir, 'backup_key')
    if not os.path.exists(key_path):
        os.makedirs(key_dir, exist_ok=True)
        with open(key_path, 'w') as f:
            f.write(cfg.cle_privee)
        os.chmod(key_path, 0o600)
    try:
        result = subprocess.run([
            'ssh', '-i', key_path,
            '-p', str(cfg.sftp_port),
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ConnectTimeout=10',
            '-o', 'BatchMode=yes',
            f'{cfg.sftp_user}@{cfg.sftp_host}',
            f'mkdir -p {cfg.sftp_path} && echo OK'
        ], capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            flash(f'✅ Connexion réussie à {cfg.sftp_host} !', 'success')
        else:
            flash(f'❌ Échec de connexion : {result.stderr.strip() or result.stdout.strip()}', 'danger')
    except subprocess.TimeoutExpired:
        flash('❌ Timeout — vérifiez l\'adresse et le port.', 'danger')
    except Exception as e:
        flash(f'❌ Erreur : {e}', 'danger')
    return redirect(url_for('admin_sauvegarde'))


@app.route('/admin/sauvegarde/envoyer-distant', methods=['POST'])
@login_required
@admin_required
def admin_envoyer_sauvegarde_distante():
    """Envoie la dernière sauvegarde locale vers le SFTP distant."""
    import subprocess, os, glob
    cfg = ConfigSauvegarde.query.first()
    if not cfg or not cfg.sftp_host or not cfg.cle_privee:
        flash('Configuration SFTP incomplète.', 'danger')
        return redirect(url_for('admin_sauvegarde'))
    backup_dir = os.path.join(app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie'), 'backups')
    fichiers = sorted(glob.glob(os.path.join(backup_dir, '*.tar.gz')), key=os.path.getmtime, reverse=True)
    if not fichiers:
        flash('Aucune sauvegarde locale trouvée.', 'danger')
        return redirect(url_for('admin_sauvegarde'))
    dernier = fichiers[0]
    key_dir  = os.path.join(app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie'), 'ssh')
    key_path = os.path.join(key_dir, 'backup_key')
    if not os.path.exists(key_path):
        os.makedirs(key_dir, exist_ok=True)
        with open(key_path, 'w') as f: f.write(cfg.cle_privee)
        os.chmod(key_path, 0o600)
    try:
        ssh_opts = f"ssh -i {key_path} -p {cfg.sftp_port} -o StrictHostKeyChecking=no -o BatchMode=yes"
        data_dir = app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie')
        errors = []

        # Base de données
        r = subprocess.run([
            'rsync', '-az', '--timeout=120',
            '-e', ssh_opts,
            os.path.join(data_dir, 'orthoptie_v2.db'),
            f'{cfg.sftp_user}@{cfg.sftp_host}:{cfg.sftp_path}/db/'
        ], capture_output=True, text=True, timeout=120)
        if r.returncode != 0: errors.append(f'db: {r.stderr.strip()}')

        # Clés SSH et config SFTP
        ssh_dir = os.path.join(data_dir, 'ssh')
        if os.path.isdir(ssh_dir):
            r = subprocess.run([
                'rsync', '-az', '--timeout=30', '-e', ssh_opts,
                ssh_dir + '/',
                f'{cfg.sftp_user}@{cfg.sftp_host}:{cfg.sftp_path}/ssh/'
            ], capture_output=True, text=True, timeout=30)
        sftp_conf = os.path.join(data_dir, 'sftp_config.sh')
        if os.path.exists(sftp_conf):
            r = subprocess.run([
                'rsync', '-az', '--timeout=30', '-e', ssh_opts,
                sftp_conf,
                f'{cfg.sftp_user}@{cfg.sftp_host}:{cfg.sftp_path}/'
            ], capture_output=True, text=True, timeout=30)

        # Uploads incrémental
        r = subprocess.run([
            'rsync', '-az', '--checksum', '--delete', '--chmod=D755,F644', '--timeout=300',
            '-e', ssh_opts,
            os.path.join(data_dir, 'uploads') + '/',
            f'{cfg.sftp_user}@{cfg.sftp_host}:{cfg.sftp_path}/uploads/'
        ], capture_output=True, text=True, timeout=300)
        if r.returncode != 0: errors.append(f'uploads: {r.stderr.strip()}')

        if not errors:
            flash(f'✅ Sync incrémental terminé vers {cfg.sftp_host} (db + uploads)', 'success')
        else:
            flash(f'⚠️ Sync partiel : {" | ".join(errors)}', 'warning')
    except subprocess.TimeoutExpired:
        flash('❌ Timeout — connexion trop lente ou volume trop important.', 'danger')
    except FileNotFoundError:
        flash('❌ rsync non installé sur le serveur.', 'danger')
    except Exception as e:
        flash(f'❌ Erreur : {e}', 'danger')
    return redirect(url_for('admin_sauvegarde'))



@app.route('/admin/sauvegarde/do-restart', methods=['POST'])
@login_required
def admin_sauvegarde_do_restart():
    """Déclenche le redémarrage du service après restauration."""
    import subprocess
    subprocess.Popen(
        ['sudo', '/bin/systemctl', 'restart', 'orthoptie'],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    return '', 204


@app.route('/admin/sauvegarde/attente')
@login_required
def admin_sauvegarde_attente():
    return render_template('admin/restauration_attente.html',
                           restart=request.args.get('restart') == '1')


@app.route('/admin/sauvegarde/trigger-restart', methods=['POST'])
@login_required
def admin_trigger_restart():
    """Déclenche le redémarrage depuis JS après que la page d'attente est affichée."""
    try:
        if os.path.exists('/usr/local/bin/orthoptie-fix-perms'):
            subprocess.Popen(['sudo', '/usr/local/bin/orthoptie-fix-perms'])
        else:
            subprocess.Popen(['bash', '-c', 'sleep 2 && systemctl restart orthoptie 2>/dev/null || true'])
    except Exception:
        pass
    return '', 200


@app.route('/admin/sauvegarde/infos-nas')
@login_required
@admin_required
def admin_sauvegarde_infos_nas():
    """Retourne les infos de la dernière sauvegarde NAS en JSON."""
    import subprocess, os
    cfg = ConfigSauvegarde.query.first()
    if not cfg or not cfg.sftp_host or not cfg.cle_privee:
        return {'error': 'NAS non configuré'}, 400
    key_dir  = os.path.join(app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie'), 'ssh')
    key_path = os.path.join(key_dir, 'backup_key')
    if not os.path.exists(key_path):
        os.makedirs(key_dir, exist_ok=True)
        with open(key_path, 'w') as f: f.write(cfg.cle_privee)
        os.chmod(key_path, 0o600)
    try:
        r = subprocess.run([
            'ssh', '-i', key_path, '-p', str(cfg.sftp_port),
            '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=10',
            f'{cfg.sftp_user}@{cfg.sftp_host}',
            f'stat -c "%Y" {cfg.sftp_path}/db/orthoptie_v2.enc.db 2>/dev/null || '
            f'stat -c "%Y" {cfg.sftp_path}/db/orthoptie_v2.db 2>/dev/null; '
            f'du -sh {cfg.sftp_path}/uploads 2>/dev/null | cut -f1'
        ], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            lines = r.stdout.strip().split('\n')
            ts = int(lines[0]) if lines and lines[0].isdigit() else None
            size = lines[1] if len(lines) > 1 else '?'
            date_str = datetime.fromtimestamp(ts).strftime('%d/%m/%Y à %H:%M') if ts else 'inconnue'
            return {'date': date_str, 'size': size, 'ok': True}
        return {'error': r.stderr.strip() or 'Connexion échouée'}, 400
    except Exception as e:
        return {'error': str(e)}, 400


@app.route('/admin/sauvegarde/restaurer-nas', methods=['POST'])
@login_required
@admin_required
def admin_restaurer_nas():
    """Restaure depuis le NAS (db + uploads)."""
    import subprocess, os, shutil
    cfg = ConfigSauvegarde.query.first()
    if not cfg or not cfg.sftp_host or not cfg.cle_privee:
        flash('NAS non configuré.', 'danger')
        return redirect(url_for('admin_sauvegarde'))
    key_dir  = os.path.join(app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie'), 'ssh')
    key_path = os.path.join(key_dir, 'backup_key')
    if not os.path.exists(key_path):
        os.makedirs(key_dir, exist_ok=True)
        with open(key_path, 'w') as f: f.write(cfg.cle_privee)
        os.chmod(key_path, 0o600)
    data_dir = app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie')
    ssh_opts = f"ssh -i {key_path} -p {cfg.sftp_port} -o StrictHostKeyChecking=no -o BatchMode=yes"
    errors = []
    try:
        # 1. Restaurer la base de données — enc.db en priorité, sinon db standard
        r_enc = subprocess.run([
            'rsync', '-az', '--no-perms', '--no-owner', '--no-group',
            '--timeout=120', '-e', ssh_opts,
            f'{cfg.sftp_user}@{cfg.sftp_host}:{cfg.sftp_path}/db/orthoptie_v2.enc.db',
            os.path.join(data_dir, 'orthoptie_v2.enc.db')
        ], capture_output=True, text=True, timeout=120)

        if r_enc.returncode != 0:
            # Fallback : télécharger db standard
            r = subprocess.run([
                'rsync', '-az', '--no-perms', '--no-owner', '--no-group',
                '--timeout=120', '-e', ssh_opts,
                f'{cfg.sftp_user}@{cfg.sftp_host}:{cfg.sftp_path}/db/orthoptie_v2.db',
                os.path.join(data_dir, 'orthoptie_v2.db')
            ], capture_output=True, text=True, timeout=120)
            if r.returncode != 0: errors.append(f'db: {r.stderr.strip()}')

        # 2. Restaurer les clés SSH si présentes sur le NAS
        subprocess.run([
            'rsync', '-az', '--no-perms', '--no-owner', '--no-group',
            '--timeout=30', '-e', ssh_opts,
            f'{cfg.sftp_user}@{cfg.sftp_host}:{cfg.sftp_path}/ssh/',
            os.path.join(data_dir, 'ssh') + '/'
        ], capture_output=True, text=True, timeout=30)
        subprocess.run([
            'rsync', '-az', '--no-perms', '--no-owner', '--no-group',
            '--timeout=30', '-e', ssh_opts,
            f'{cfg.sftp_user}@{cfg.sftp_host}:{cfg.sftp_path}/sftp_config.sh',
            os.path.join(data_dir, 'sftp_config.sh')
        ], capture_output=True, text=True, timeout=30)

        # 2. Restaurer les uploads
        r = subprocess.run([
            'rsync', '-az', '--delete', '--no-perms', '--no-owner', '--no-group',
            '--timeout=600', '-e', ssh_opts,
            f'{cfg.sftp_user}@{cfg.sftp_host}:{cfg.sftp_path}/uploads/',
            os.path.join(data_dir, 'uploads') + '/'
        ], capture_output=True, text=True, timeout=600)
        if r.returncode != 0: errors.append(f'uploads: {r.stderr.strip()}')

        if not errors:
            db_std_path = os.path.join(data_dir, 'orthoptie_v2.db')
            db_enc_path = os.path.join(data_dir, 'orthoptie_v2.enc.db')
            install_dir_nas = os.path.dirname(__file__)
            key_file_nas = os.path.join(install_dir_nas, '.db_key')
            fix_perms_nas = '/usr/local/bin/orthoptie-fix-perms'

            @after_this_request
            def _do_nas_restore(response):
                try:
                    import shutil as _sh, pwd, grp
                    # Si enc.db a été téléchargée directement — juste permissions
                    if os.path.exists(db_enc_path) and os.path.getsize(db_enc_path) > 0:
                        try:
                            uid = pwd.getpwnam('orthoptie').pw_uid
                            gid = grp.getgrnam('orthoptie').gr_gid
                            os.chown(db_enc_path, uid, gid)
                            os.chmod(db_enc_path, 0o660)
                        except Exception: pass
                    # Sinon rechiffrer depuis db standard
                    elif os.path.exists(db_std_path) and os.path.getsize(db_std_path) > 0 and os.path.exists(key_file_nas):
                        import sqlcipher3 as _sc, sqlite3 as _sq
                        with open(key_file_nas) as kf: key = kf.read().strip()
                        SKIP = {'sqlite_sequence','sqlite_stat1','sqlite_stat2','sqlite_stat3','sqlite_stat4'}
                        if os.path.exists(db_enc_path): os.remove(db_enc_path)
                        src = _sq.connect(db_std_path)
                        dst = _sc.connect(db_enc_path)
                        dst.executescript(f"PRAGMA key='{key}'; PRAGMA cipher_page_size=4096; PRAGMA kdf_iter=64000; PRAGMA cipher_hmac_algorithm=HMAC_SHA512; PRAGMA cipher_kdf_algorithm=PBKDF2_HMAC_SHA512;")
                        for (table,) in src.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                            if table in SKIP: continue
                            s = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                            if s and s[0]: dst.execute(s[0])
                            rows = src.execute(f"SELECT * FROM {table}").fetchall()
                            if rows: dst.executemany(f"INSERT INTO {table} VALUES ({','.join(['?']*len(rows[0]))})", rows)
                        for (i,) in src.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchall():
                            try: dst.execute(i)
                            except: pass
                        dst.commit(); dst.close(); src.close()
                        os.remove(db_std_path)
                        try:
                            uid = pwd.getpwnam('orthoptie').pw_uid
                            gid = grp.getgrnam('orthoptie').gr_gid
                            os.chown(db_enc_path, uid, gid)
                            os.chmod(db_enc_path, 0o660)
                        except Exception: pass
                    # Redémarrer
                    import subprocess as _sub
                    if os.path.exists(fix_perms_nas):
                        _sub.Popen(['bash', '-c', f'sleep 5 && sudo {fix_perms_nas}'])
                    else:
                        _sub.Popen(['bash', '-c', 'sleep 5 && systemctl restart orthoptie 2>/dev/null || true'])
                except Exception:
                    pass
                return response

            flash('✅ Restauration depuis le NAS réussie.', 'success')
        else:
            flash(f'⚠️ Restauration partielle : {" | ".join(errors)}', 'warning')
    except subprocess.TimeoutExpired:
        flash('❌ Timeout — connexion trop lente.', 'danger')
    except Exception as e:
        flash(f'❌ Erreur : {e}', 'danger')

    _page_attente = '''<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="20;url=/">
<title>Restauration effectuée</title>
<style>body{font-family:sans-serif;display:flex;align-items:center;justify-content:center;
height:100vh;margin:0;background:#f0f4ff;}
.box{text-align:center;padding:40px;background:white;border-radius:16px;
box-shadow:0 4px 24px rgba(0,0,0,.1);}
.spinner{width:40px;height:40px;border:4px solid #e0e0e0;border-top-color:#4a7bd4;
border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 20px;}
@keyframes spin{to{transform:rotate(360deg)}}
</style></head><body><div class="box">
<div class="spinner"></div>
<h2>✅ Restauration effectuée</h2>
<p>L\'application redémarre, veuillez patienter…</p>
<p style="color:#888;font-size:13px;">Redirection automatique dans quelques secondes.</p>
</div>
<script>
setTimeout(function check() {
  fetch(\'/\').then(function(r) {
    if (r.ok || r.status === 302) { window.location.href = \'/\'; }
    else { setTimeout(check, 2000); }
  }).catch(function() { setTimeout(check, 2000); });
}, 5000);
</script>
</body></html>'''
    return _page_attente, 200


@app.route('/admin/sauvegarde/lancer', methods=['POST'])
@login_required
@admin_required
def admin_sauvegarde_lancer():
    """Lance manuellement le script de sauvegarde automatique."""
    import subprocess
    try:
        result = subprocess.run(
            ['/etc/cron.daily/orthoptie-backup'],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            flash('✅ Sauvegarde lancée avec succès. ' + (result.stdout.split('\n')[0] if result.stdout else ''), 'success')
        else:
            flash(f'❌ Erreur : {result.stderr.strip() or result.stdout.strip()}', 'danger')
    except subprocess.TimeoutExpired:
        flash('❌ Timeout — la sauvegarde prend trop de temps.', 'danger')
    except FileNotFoundError:
        flash('❌ Script de sauvegarde introuvable.', 'danger')
    except Exception as e:
        flash(f'❌ Erreur : {e}', 'danger')
    return redirect(url_for('admin_sauvegarde'))


@app.route('/admin/nettoyage-fichiers', methods=['POST'])
@login_required
@admin_required
def admin_nettoyage_fichiers():
    """Supprime les fichiers WOPI temporaires et les fichiers sections orphelins."""
    import os, glob
    stats = {'wopi': 0, 'orphelins': 0}

    # 1. Nettoyer les fichiers WOPI temporaires de plus de 24h
    wopi_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'wopi')
    if os.path.exists(wopi_dir):
        for f in glob.glob(os.path.join(wopi_dir, '*.docx')):
            try:
                age = datetime.utcnow().timestamp() - os.path.getmtime(f)
                if age > 86400:  # > 24h
                    os.remove(f)
                    stats['wopi'] += 1
            except Exception:
                pass

    # 2. Nettoyer les fichiers sections orphelins (pas en base)
    sections_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'sections')
    if os.path.exists(sections_dir):
        in_db = {r[0] for r in db.session.query(FichierSection.nom_stocke).all()}
        for f in glob.glob(os.path.join(sections_dir, '**', '*.docx'), recursive=True):
            if os.path.basename(f) not in in_db:
                try:
                    os.remove(f)
                    stats['orphelins'] += 1
                except Exception:
                    pass

    flash(f"Nettoyage : {stats['wopi']} fichiers WOPI + {stats['orphelins']} orphelins supprimés.", 'success')
    return redirect(url_for('admin_sauvegarde'))


@app.route('/admin/categories')
@login_required
@admin_required
def admin_categories():
    cats_builtin = {k: v for k, v in CATEGORIES_BUILTIN.items() if k}
    cats_custom = CategorieSection.query.order_by(CategorieSection.ordre).all()
    return render_template('admin/categories.html',
                           cats_builtin=cats_builtin,
                           cats_custom=cats_custom)


@app.route('/admin/categories/creer', methods=['POST'])
@login_required
@admin_required
def admin_categorie_creer():
    key   = request.form.get('key', '').strip().lower().replace(' ', '_')
    label = request.form.get('label', '').strip()
    bg    = request.form.get('bg', '#F1EFE8').strip()
    color = request.form.get('color', '#444441').strip()
    icon  = request.form.get('icon', 'ti-layout-grid').strip()
    if not key or not label:
        flash('Clé et libellé requis.', 'danger')
        return redirect(url_for('admin_categories'))
    if CategorieSection.query.filter_by(key=key).first() or key in CATEGORIES_BUILTIN:
        flash(f'La clé « {key} » existe déjà.', 'danger')
        return redirect(url_for('admin_categories'))
    c = CategorieSection(key=key, label=label, bg=bg, color=color, icon=icon)
    db.session.add(c); db.session.commit()
    flash(f'Catégorie « {label} » créée.', 'success')
    return redirect(url_for('admin_categories'))


@app.route('/admin/categories/<int:cat_id>/modifier', methods=['POST'])
@login_required
@admin_required
def admin_categorie_modifier(cat_id):
    c = CategorieSection.query.get_or_404(cat_id)
    c.label = request.form.get('label', c.label).strip()
    c.bg    = request.form.get('bg', c.bg).strip()
    c.color = request.form.get('color', c.color).strip()
    c.icon  = request.form.get('icon', c.icon).strip()
    db.session.commit()
    flash('Catégorie mise à jour.', 'success')
    return redirect(url_for('admin_categories'))


@app.route('/admin/categories/<int:cat_id>/supprimer', methods=['POST'])
@login_required
@admin_required
def admin_categorie_supprimer(cat_id):
    c = CategorieSection.query.get_or_404(cat_id)
    label = c.label
    # Remettre à vide les sections qui utilisaient cette catégorie
    SectionDef.query.filter_by(categorie=c.key).update({'categorie': ''})
    db.session.delete(c); db.session.commit()
    flash(f'Catégorie « {label} » supprimée.', 'success')
    return redirect(url_for('admin_categories'))


@app.route('/admin/configuration', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_configuration():
    """Configuration générale : URLs Collabora et WOPI."""
    cfg = ConfigApp.query.first()
    if not cfg:
        cfg = ConfigApp()
        db.session.add(cfg)
        db.session.commit()
    if request.method == 'POST':
        cfg.collabora_url = request.form.get('collabora_url', '').strip().rstrip('/')
        cfg.wopi_base_url = request.form.get('wopi_base_url', '').strip().rstrip('/')
        cfg.updated_at    = datetime.utcnow()
        db.session.commit()
        flash('Configuration enregistrée.', 'success')
        return redirect(url_for('admin_configuration'))
    return render_template('admin/configuration.html', cfg=cfg,
                           collabora_url_default=COLLABORA_URL,
                           wopi_base_url_default=WOPI_BASE_URL)


@app.route('/admin/sauvegarde', methods=['GET'])
@login_required
def admin_sauvegarde():
    """Page de gestion des sauvegardes."""
    if current_user.role != 'admin':
        flash('Accès réservé aux administrateurs.', 'danger')
        return redirect(url_for('index'))
    import glob
    data_dir   = app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie')
    backup_dir = os.path.join(data_dir, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    backups = sorted(
        glob.glob(os.path.join(backup_dir, 'orthoptie_backup_*.zip')) +
        glob.glob(os.path.join(backup_dir, 'orthoptie_backup_*.tar.gz')),
        reverse=True)
    backups_info = []
    for b in backups:
        stat = os.stat(b)
        backups_info.append({
            'nom': os.path.basename(b),
            'taille': f"{stat.st_size / 1024 / 1024:.1f} Mo",
            'date': datetime.fromtimestamp(stat.st_mtime).strftime('%d/%m/%Y %H:%M'),
        })
    return render_template('admin/sauvegarde.html', backups=backups_info,
                           config_sauvegarde=ConfigSauvegarde.query.first())


@app.route('/admin/sauvegarde/telecharger/<nom>')
@login_required
@admin_required
def admin_sauvegarde_telecharger(nom):
    """Télécharge une archive de sauvegarde locale."""
    import re
    if not re.match(r'^orthoptie_backup_[\w\-]+\.(tar\.gz|zip)$', nom):
        abort(400)
    data_dir   = app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie')
    backup_dir = os.path.join(data_dir, 'backups')
    chemin     = os.path.join(backup_dir, nom)
    if not os.path.exists(chemin):
        abort(404)
    from flask import send_file
    return send_file(chemin, as_attachment=True, download_name=nom)


@app.route('/admin/sauvegarde/supprimer-local/<nom>', methods=['POST'])
@login_required
@admin_required
def admin_sauvegarde_supprimer_local(nom):
    """Supprime une archive de sauvegarde locale."""
    import re
    if not re.match(r'^orthoptie_backup_[\w\-]+\.(tar\.gz|zip)$', nom):
        abort(400)
    data_dir   = app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie')
    backup_dir = os.path.join(data_dir, 'backups')
    chemin     = os.path.join(backup_dir, nom)
    if os.path.exists(chemin):
        os.remove(chemin)
        flash(f'Archive {nom} supprimée.', 'success')
    return redirect(url_for('admin_sauvegarde'))



@app.route('/admin/sauvegarde/exporter-config')
@login_required
@admin_required
def admin_exporter_config():
    """Exporte la configuration (Collabora + SFTP) en JSON."""
    import json
    cfg_app  = ConfigApp.query.first()
    cfg_sftp = ConfigSauvegarde.query.first()
    data_dir = app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie')
    # Lire la clé privée SSH si elle existe
    key_path = os.path.join(data_dir, 'ssh', 'backup_key')
    cle_privee = ''
    if os.path.exists(key_path):
        with open(key_path) as f:
            cle_privee = f.read()
    config = {
        'collabora_url':  cfg_app.collabora_url  if cfg_app  else '',
        'wopi_base_url':  cfg_app.wopi_base_url   if cfg_app  else '',
        'sftp_host':      cfg_sftp.sftp_host      if cfg_sftp else '',
        'sftp_port':      cfg_sftp.sftp_port      if cfg_sftp else 22,
        'sftp_user':      cfg_sftp.sftp_user      if cfg_sftp else '',
        'sftp_path':      cfg_sftp.sftp_path      if cfg_sftp else '',
        'sftp_actif':     cfg_sftp.sftp_actif     if cfg_sftp else False,
        'cle_publique':   cfg_sftp.cle_publique   if cfg_sftp else '',
        'cle_privee':     cle_privee,
    }
    from flask import Response
    return Response(
        json.dumps(config, indent=2, ensure_ascii=False),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=orthoptie_config.json'}
    )


@app.route('/admin/sauvegarde/importer-config', methods=['POST'])
@login_required
@admin_required
def admin_importer_config():
    """Importe la configuration (Collabora + SFTP) depuis un JSON."""
    import json
    f = request.files.get('fichier_config')
    if not f or not f.filename.endswith('.json'):
        flash('Fichier invalide — JSON requis.', 'danger')
        return redirect(url_for('admin_sauvegarde'))
    try:
        data     = json.load(f)
        data_dir = app.config.get('DATA_FOLDER', '/home/yunohost.app/orthoptie')
        cfg_app = ConfigApp.query.first()
        if not cfg_app:
            cfg_app = ConfigApp(); db.session.add(cfg_app)
        # Config Collabora — restaurer seulement si explicitement demandé
        if request.form.get('importer_collabora') == '1':
            cfg_app.collabora_url = data.get('collabora_url', '')
            cfg_app.wopi_base_url = data.get('wopi_base_url', '')
        # Config SFTP
        cfg_sftp = ConfigSauvegarde.query.first()
        if not cfg_sftp:
            cfg_sftp = ConfigSauvegarde(); db.session.add(cfg_sftp)
        cfg_sftp.sftp_host    = data.get('sftp_host', '')
        cfg_sftp.sftp_port    = int(data.get('sftp_port', 22))
        cfg_sftp.sftp_user    = data.get('sftp_user', '')
        cfg_sftp.sftp_path    = data.get('sftp_path', '')
        cfg_sftp.sftp_actif   = data.get('sftp_actif', False)
        cfg_sftp.cle_publique = data.get('cle_publique', '')
        cfg_sftp.cle_privee   = data.get('cle_privee', '')
        db.session.commit()
        # Restaurer la clé SSH sur le disque
        if data.get('cle_privee'):
            key_dir = os.path.join(data_dir, 'ssh')
            os.makedirs(key_dir, exist_ok=True)
            key_path = os.path.join(key_dir, 'backup_key')
            with open(key_path, 'w') as kf:
                kf.write(data['cle_privee'])
            os.chmod(key_path, 0o600)
        if data.get('cle_publique'):
            key_dir = os.path.join(data_dir, 'ssh')
            os.makedirs(key_dir, exist_ok=True)
            with open(os.path.join(key_dir, 'backup_key.pub'), 'w') as kf:
                kf.write(data['cle_publique'])
        # Regénérer sftp_config.sh
        if cfg_sftp.sftp_host:
            config_path = os.path.join(data_dir, 'sftp_config.sh')
            with open(config_path, 'w') as cf:
                cf.write(f'SFTP_ACTIF="{1 if cfg_sftp.sftp_actif else 0}"\n')
                cf.write(f'SFTP_HOST="{cfg_sftp.sftp_host}"\n')
                cf.write(f'SFTP_PORT="{cfg_sftp.sftp_port}"\n')
                cf.write(f'SFTP_USER="{cfg_sftp.sftp_user}"\n')
                cf.write(f'SFTP_PATH="{cfg_sftp.sftp_path}"\n')
        flash('✅ Configuration importée avec succès.', 'success')
    except Exception as e:
        flash(f'❌ Erreur lors de l\'import : {e}', 'danger')
    return redirect(url_for('admin_sauvegarde'))


@app.route('/admin/sauvegarde/exporter')
@login_required
def admin_sauvegarde_exporter():
    """Génère et télécharge un zip de sauvegarde."""
    if current_user.role != 'admin':
        return 'Accès refusé', 403
    import zipfile as zf, tempfile
    tmpdir = tempfile.mkdtemp()
    nom = f"orthoptie_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    zip_path = os.path.join(tmpdir, nom)

    data_dir    = app.config['DATA_FOLDER']
    uploads_dir = app.config['UPLOAD_FOLDER']
    db_enc      = os.path.join(data_dir, 'orthoptie_v2.enc.db')
    db_std      = os.path.join(app.instance_path, 'orthoptie_v2.db')

    with zf.ZipFile(zip_path, 'w', zf.ZIP_DEFLATED) as z:
        # Base de données — préférer enc.db, fallback sur standard
        if os.path.exists(db_enc) and os.path.getsize(db_enc) > 0:
            z.write(db_enc, 'orthoptie_v2.enc.db')
        elif os.path.exists(db_std) and os.path.getsize(db_std) > 0:
            z.write(db_std, 'orthoptie_v2.db')
        # Uploads
        for root, dirs, files in os.walk(uploads_dir):
            for file in files:
                full = os.path.join(root, file)
                arcname = os.path.join('uploads', os.path.relpath(full, uploads_dir))
                z.write(full, arcname)

    from flask import send_file
    return send_file(zip_path, as_attachment=True, download_name=nom,
                     mimetype='application/zip')


@app.route('/admin/sauvegarde/importer', methods=['POST'])
@login_required
def admin_sauvegarde_importer():
    """Restaure depuis un zip de sauvegarde — chiffrement en arrière-plan."""
    if current_user.role != 'admin':
        return 'Accès refusé', 403
    import tempfile, shutil, subprocess

    f = request.files.get('fichier') or request.files.get('backup_file')
    if not f or not (f.filename.endswith('.zip') or f.filename.endswith('.tar.gz')):
        flash('Fichier invalide — zip ou tar.gz requis.', 'danger')
        return redirect(url_for('admin_sauvegarde'))

    confirmation = request.form.get('confirmation', 'oui').strip().lower()
    if confirmation not in ('oui', 'yes', ''):
        flash('Vous devez taper "oui" pour confirmer la restauration.', 'warning')
        return redirect(url_for('admin_sauvegarde'))

    tmpdir      = tempfile.mkdtemp()
    backup_path = os.path.join(tmpdir, 'restore_file')
    f.save(backup_path)

    uploads_dir = app.config['UPLOAD_FOLDER']
    data_dir    = app.config['DATA_FOLDER']
    install_dir = os.path.dirname(__file__)
    db_tmp      = os.path.join(tmpdir, 'orthoptie_v2.db')

    # Extraire la DB et les uploads
    try:
        if f.filename.endswith('.tar.gz'):
            import tarfile
            with tarfile.open(backup_path, 'r:gz') as tar:
                members = tar.getnames()
                # Chercher enc.db en priorité, puis db standard
                enc_member = next((m for m in members if m.endswith('orthoptie_v2.enc.db')), None)
                db_member  = next((m for m in members if m.endswith('orthoptie_v2.db') and not m.endswith('.enc.db')), None)
                if enc_member:
                    tar.extract(enc_member, tmpdir)
                    shutil.move(os.path.join(tmpdir, enc_member), db_tmp.replace('.db', '.enc.db'))
                    db_tmp = db_tmp.replace('.db', '.enc.db')
                elif db_member:
                    tar.extract(db_member, tmpdir)
                    extracted = os.path.join(tmpdir, db_member)
                    if extracted != db_tmp:
                        shutil.move(extracted, db_tmp)
                for member in tar.getmembers():
                    if 'uploads/' in member.name and member.isfile():
                        tar.extract(member, tmpdir)
                        rel  = member.name.split('uploads/', 1)[1]
                        dest = os.path.join(uploads_dir, rel)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        shutil.copy2(os.path.join(tmpdir, member.name), dest)
        else:
            import zipfile as zf
            with zf.ZipFile(backup_path, 'r') as z:
                names = z.namelist()
                if 'orthoptie_v2.enc.db' in names:
                    z.extract('orthoptie_v2.enc.db', tmpdir)
                    db_tmp = os.path.join(tmpdir, 'orthoptie_v2.enc.db')
                elif 'orthoptie_v2.db' in names:
                    z.extract('orthoptie_v2.db', tmpdir)
                for name in names:
                    if name.startswith('uploads/'):
                        z.extract(name, tmpdir)
                        dest = os.path.join(uploads_dir, os.path.relpath(name, 'uploads'))
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        src = os.path.join(tmpdir, name)
                        if os.path.isfile(src):
                            shutil.copy2(src, dest)
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        flash(f'❌ Erreur extraction : {e}', 'danger')
        return redirect(url_for('admin_sauvegarde'))

    # Copier enc.db puis redémarrer avec délai (même approche qu'avant le chiffrement)
    db_enc_path = os.path.join(data_dir, 'orthoptie_v2.enc.db')
    is_already_encrypted = db_tmp.endswith('.enc.db')

    if is_already_encrypted and os.path.exists(db_tmp) and os.path.getsize(db_tmp) > 0:
        shutil.copy2(db_tmp, db_enc_path)
        try:
            import pwd, grp
            uid = pwd.getpwnam('orthoptie').pw_uid
            gid = grp.getgrnam('orthoptie').gr_gid
            os.chown(db_enc_path, uid, gid)
            os.chmod(db_enc_path, 0o660)
        except Exception:
            pass

    shutil.rmtree(tmpdir, ignore_errors=True)

    # Planifier la copie APRÈS que la réponse est envoyée
    enc_src  = db_tmp
    enc_dst  = os.path.join(data_dir, 'orthoptie_v2.enc.db')
    fix_perms_script = '/usr/local/bin/orthoptie-fix-perms'
    tmpdir_to_clean  = tmpdir

    @after_this_request
    def _do_restore(response):
        try:
            import shutil as _shutil, subprocess as _sub, pwd, grp
            _shutil.copy2(enc_src, enc_dst)
            try:
                uid = pwd.getpwnam('orthoptie').pw_uid
                gid = grp.getgrnam('orthoptie').gr_gid
                os.chown(enc_dst, uid, gid)
                os.chmod(enc_dst, 0o660)
            except Exception:
                pass
            _shutil.rmtree(tmpdir_to_clean, ignore_errors=True)
            if os.path.exists(fix_perms_script):
                _sub.Popen(['bash', '-c', f'sleep 5 && sudo {fix_perms_script}'])
            else:
                _sub.Popen(['bash', '-c', 'sleep 5 && systemctl restart orthoptie 2>/dev/null || true'])
        except Exception:
            pass
        return response

    flash('✅ Restauration effectuée.', 'success')
    return redirect(url_for('admin_sauvegarde_attente', restart='1'))


@app.route('/session/ping', methods=['POST'])
@login_required
def session_ping():
    """Maintient la session active (appelé périodiquement par le JS)."""
    session.modified = True
    return jsonify({'ok': True})


@app.route('/admin/journal')
@login_required
def admin_journal():
    """Journal RGPD des accès."""
    if current_user.role != 'admin':
        flash('Accès réservé aux administrateurs.', 'danger')
        return redirect(url_for('index'))
    page = request.args.get('page', 1, type=int)
    praticien_filter = request.args.get('praticien_id', type=int)
    q = JournalAcces.query.order_by(JournalAcces.created_at.desc())
    if praticien_filter:
        q = q.filter_by(praticien_id=praticien_filter)
    entrees = q.limit(200).all()
    praticiens = Praticien.query.order_by(Praticien.nom).all()
    return render_template('admin/journal.html', entrees=entrees,
                           praticiens=praticiens, praticien_filter=praticien_filter)


@app.route('/aide')
@login_required
def aide():
    return render_template('aide/index.html')


@app.route('/mon-historique')
@login_required
def mon_historique():
    """Historique des dossiers patients consultés par le praticien connecté."""
    # Derniers accès uniques par patient, triés par date décroissante
    sous_q = db.session.query(
        JournalAcces.patient_id,
        db.func.max(JournalAcces.created_at).label('derniere_visite')
    ).filter(
        JournalAcces.praticien_id == current_user.id,
        JournalAcces.patient_id.isnot(None),
        JournalAcces.action.in_(['patient_detail','consultation_detail',
                                  'impression_dossier','lecture_suivi_amblyopie','lecture_suivi_vb'])
    ).group_by(JournalAcces.patient_id).subquery()

    entrees = db.session.query(Patient, sous_q.c.derniere_visite)\
        .join(sous_q, Patient.id == sous_q.c.patient_id)\
        .order_by(sous_q.c.derniere_visite.desc())\
        .limit(20).all()

    return render_template('historique_praticien.html', entrees=entrees)


@app.route('/recherche')
@login_required
def recherche():
    q = request.args.get('q', '').strip(); patients = []
    if q:
        dn = None
        for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d'):
            try: dn = datetime.strptime(q, fmt).date(); break
            except ValueError: pass
        if dn:
            patients = Patient.query.filter(Patient.date_naissance == dn).order_by(Patient.nom).all()
        else:
            # Normaliser le numéro de téléphone (supprimer espaces/tirets/points)
            q_tel = re.sub(r'[\s\.\-]', '', q)
            # Si la requête ressemble à un numéro (que des chiffres après normalisation)
            if q_tel.isdigit() and len(q_tel) >= 4:
                patients = Patient.query.filter(
                    db.func.replace(db.func.replace(db.func.replace(
                        Patient.telephone, ' ', ''), '.', ''), '-', ''
                    ).ilike(f'%{q_tel}%')
                ).order_by(Patient.nom).all()
            else:
                conds = [db.or_(
                    Patient.nom.ilike(f'%{m}%'),
                    Patient.prenom.ilike(f'%{m}%'),
                    Patient.telephone.ilike(f'%{m}%')
                ) for m in q.split()]
                patients = Patient.query.filter(db.and_(*conds)).order_by(Patient.nom).all()
    activites = {p.id: _derniere_activite(p) for p in patients}
    return render_template('patients/recherche.html', patients=patients, q=q,
                           activites=activites, today=datetime.utcnow().date())


# ============================================================
# CONSULTATIONS
# ============================================================

@app.route('/patient/<int:patient_id>/consultation/nouvelle', methods=['GET', 'POST'])
@login_required
def consultation_nouvelle(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    sections, ordre = get_sections()
    if request.method == 'POST':
        cab = get_current_cabinet()
        c = Consultation(patient_id=patient_id, praticien_id=current_user.id,
                         date_consult=_parse_date(request.form.get('date_consult')) or date.today(),
                         motif=request.form.get('motif'),
                         medecin_prescripteur=request.form.get('medecin_prescripteur','').strip() or None,
                         classe_profession=request.form.get('classe_profession','').strip() or None,
                         cabinet_id=cab.id if cab else None)
        db.session.add(c); db.session.flush()
        _save_sections(c.id, request.form, sections, request.files)
        _save_fichiers(c.id, request.files, request.form)
        db.session.commit()
        log_action('creation_consultation', patient_id=patient_id, consultation_id=c.id)
        flash('Bilan enregistré.', 'success')
        redirect_after = request.form.get('redirect_after', '').strip()
        if redirect_after:
            # redirect_after peut être relatif ex: 'modifier#section-courrier'
            if redirect_after.startswith('modifier'):
                return redirect(url_for('consultation_modifier', consultation_id=c.id) +
                                redirect_after.replace('modifier', ''))
            return redirect(redirect_after)
        return redirect(url_for('consultation_detail', consultation_id=c.id))
    autres = Consultation.query.filter(Consultation.patient_id == patient_id)\
                               .order_by(Consultation.date_consult.asc()).all()
    modeles = ModeleBilan.query.filter_by(actif=True).order_by(ModeleBilan.nom).all()
    if not get_current_cabinet():
        flash('⚠️ Veuillez sélectionner un cabinet avant de créer un bilan.', 'warning')
        return redirect(url_for('patient_detail', patient_id=patient_id))
    return render_template('consultations/saisie.html', patient=patient, consultation=None,
                           sections_dispo=sections, sections_ordre=ordre,
                           autres_consultations=autres, sections_def=sections,
                           modeles=modeles, modeles_json=[m.to_dict() for m in modeles])


@app.route('/consultation/<int:consultation_id>')
@login_required
def consultation_detail(consultation_id):
    c = Consultation.query.get_or_404(consultation_id)
    sections, _ = get_sections()
    log_action('lecture_consultation', patient_id=c.patient_id, consultation_id=consultation_id)
    log_acces('consultation_detail', patient_id=c.patient_id, consultation_id=consultation_id,
              detail=f'{c.patient.prenom} {c.patient.nom} — {c.date_consult.strftime("%d/%m/%Y")}')
    return render_template('consultations/bilan.html', consultation=c, sections_def=sections)


@app.route('/consultation/<int:consultation_id>/modifier', methods=['GET', 'POST'])
@login_required
def consultation_modifier(consultation_id):
    c = Consultation.query.get_or_404(consultation_id)
    sections, ordre = get_sections()
    if request.method == 'POST':
        c.date_consult = _parse_date(request.form.get('date_consult')) or c.date_consult
        c.motif = request.form.get('motif')
        c.medecin_prescripteur = request.form.get('medecin_prescripteur','').strip() or None
        c.classe_profession    = request.form.get('classe_profession','').strip() or None
        for s in list(c.sections): db.session.delete(s)
        db.session.flush()
        _save_sections(c.id, request.form, sections, request.files)
        _save_fichiers(c.id, request.files, request.form)
        db.session.commit()

        # Mettre à jour section_ordre des FichierSection WOPI selon le type actuel
        # et supprimer les fichiers orphelins (section supprimée du bilan)
        types_actuels = {s.type for s in c.sections}
        for fic in FichierSection.query.filter_by(consultation_id=c.id, champ_name='wopi_doc').all():
            if fic.section_type and fic.section_type not in types_actuels:
                # La section a été supprimée — supprimer le fichier
                chemin = os.path.join(app.config['UPLOAD_FOLDER'], 'wopi', fic.nom_stocke)
                if os.path.exists(chemin):
                    try: os.remove(chemin)
                    except: pass
                db.session.delete(fic)
            elif fic.section_type:
                section = next((s for s in c.sections if s.type == fic.section_type), None)
                if section:
                    fic.section_ordre = section.ordre
        db.session.commit()
        log_action('modification_consultation', patient_id=c.patient_id, consultation_id=c.id)
        flash('Bilan mis à jour.', 'success')
        redirect_after = request.form.get('redirect_after', '').strip()
        if redirect_after:
            return redirect(redirect_after)
        return redirect(url_for('consultation_detail', consultation_id=c.id))
    autres = Consultation.query.filter(Consultation.patient_id == c.patient_id,
                                       Consultation.id != c.id)\
                               .order_by(Consultation.date_consult.asc()).all()
    return render_template('consultations/saisie.html', patient=c.patient, consultation=c,
                           sections_dispo=sections, sections_ordre=ordre,
                           autres_consultations=autres, sections_def=sections,
                           modeles=[], modeles_json=[])


@app.route('/consultation/<int:consultation_id>/supprimer', methods=['POST'])
@login_required
def consultation_supprimer(consultation_id):
    c = Consultation.query.get_or_404(consultation_id)
    pid = c.patient_id; pnom = f'{c.patient.prenom} {c.patient.nom}'
    dstr = c.date_consult.strftime('%d/%m/%Y')
    log_action('suppression_consultation', patient_id=pid, consultation_id=consultation_id)
    db.session.delete(c); db.session.commit()
    flash(f'Bilan du {dstr} pour {pnom} supprimé définitivement.', 'danger')
    return redirect(url_for('patient_detail', patient_id=pid))


# ============================================================
# FICHIERS
# ============================================================

def allowed_file(fn):
    return '.' in fn and fn.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def check_file_mime(file_storage):
    """Vérifie les magic bytes du fichier uploadé."""
    header = file_storage.read(16)
    file_storage.seek(0)
    ext = file_storage.filename.rsplit('.', 1)[-1].lower() if '.' in file_storage.filename else ''
    # Signatures magic bytes connues
    if header.startswith(b'\x89PNG') and ext == 'png':           return True
    if header.startswith(b'\xff\xd8\xff') and ext in ('jpg','jpeg'): return True
    if header[:6] in (b'GIF87a', b'GIF89a') and ext == 'gif':   return True
    if header.startswith(b'%PDF') and ext == 'pdf':              return True
    if header.startswith(b'PK\x03\x04') and ext in ('docx','doc'): return True
    if header.startswith(b'RIFF') and ext == 'webp':             return True
    # Format non reconnu — on vérifie juste qu'il n'y a pas de code PHP/script
    try:
        sample = file_storage.read(512).decode('utf-8', errors='ignore')
        file_storage.seek(0)
        if any(sig in sample for sig in ['<?php', '<script', '#!/']):
            return False
    except Exception:
        pass
    return True


@app.route('/uploads/<int:consultation_id>/<filename>')
@login_required
def uploaded_file(consultation_id, filename):
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], str(consultation_id)), filename)


@app.route('/uploads/section/<int:consultation_id>/<filename>')
@login_required
def uploaded_file_section(consultation_id, filename):
    folder = os.path.join(app.config['UPLOAD_FOLDER'], 'sections', str(consultation_id))
    return send_from_directory(folder, filename)


@app.route('/fichier_section/<int:fichier_id>/supprimer', methods=['POST'])
@login_required
def fichier_section_supprimer(fichier_id):
    f = FichierSection.query.get_or_404(fichier_id)
    cid = f.consultation_id
    chemin = os.path.join(app.config['UPLOAD_FOLDER'], 'sections', str(cid), f.nom_stocke)
    if os.path.exists(chemin): os.remove(chemin)
    db.session.delete(f); db.session.commit()
    return jsonify({'ok': True})


@app.route('/fichier/<int:fichier_id>/titre', methods=['POST'])
@login_required
def fichier_titre(fichier_id):
    f = FichierBilan.query.get_or_404(fichier_id)
    f.titre = request.form.get('titre', '').strip(); db.session.commit()
    return jsonify({'ok': True})


@app.route('/fichier-bilan/<int:fichier_id>/voir')
@login_required
def consultation_fichier_voir(fichier_id):
    """Affiche ou télécharge un fichier joint à un bilan."""
    from flask import send_file as _sf
    f = FichierBilan.query.get_or_404(fichier_id)
    chemin = os.path.join(app.config['UPLOAD_FOLDER'],
                          str(f.consultation_id), f.nom_stocke)
    if not os.path.exists(chemin):
        abort(404)
    # Afficher inline si image ou PDF, sinon télécharger
    inline_types = {'pdf', 'jpg', 'jpeg', 'png', 'gif', 'webp'}
    ext = f.type_fichier or ''
    as_attachment = ext.lower() not in inline_types
    return _sf(chemin, as_attachment=as_attachment,
               download_name=f.nom_original)



@app.route('/fichier/<int:fichier_id>/supprimer', methods=['POST'])
@login_required
def fichier_supprimer(fichier_id):
    f = FichierBilan.query.get_or_404(fichier_id)
    cid = f.consultation_id
    chemin = os.path.join(app.config['UPLOAD_FOLDER'], str(cid), f.nom_stocke)
    if os.path.exists(chemin): os.remove(chemin)
    db.session.delete(f); db.session.commit()
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify({'ok': True})
    flash(f'Fichier « {f.nom_original} » supprimé.', 'success')
    return redirect(url_for('consultation_modifier', consultation_id=cid))


def _save_fichiers(consultation_id, files, form=None):
    import uuid
    uploaded = files.getlist('fichiers[]')
    titres = form.getlist('fichiers_titres[]') if form else []
    if not uploaded: return
    folder = os.path.join(app.config['UPLOAD_FOLDER'], str(consultation_id))
    os.makedirs(folder, exist_ok=True)
    ti = 0
    for f in uploaded:
        if f and f.filename and allowed_file(f.filename):
            ext = f.filename.rsplit('.', 1)[1].lower()
            ns = f"{uuid.uuid4().hex}.{ext}"
            tf = 'pdf' if ext == 'pdf' else ('word' if ext in ('doc','docx') else 'image')
            f.save(os.path.join(folder, ns))
            db.session.add(FichierBilan(consultation_id=consultation_id,
                nom_original=secure_filename(f.filename), nom_stocke=ns, type_fichier=tf,
                titre=(titres[ti] if ti < len(titres) else '').strip()))
            ti += 1


def _save_sections(consultation_id, form, sections, files=None):
    import uuid
    types = form.getlist('sections_types[]')
    obs_list = form.getlist('sections_obs[]')

    # Construire un mapping idx -> type depuis les clés du formulaire
    # Un idx peut correspondre à un type donné
    idx_type_map = {}
    for key in form.keys():
        if key.startswith('champ__'):
            parts = key.split('__')
            if len(parts) >= 3:
                try:
                    idx = int(parts[1])
                    champ_name = parts[2]
                    for stype, sdef in sections.items():
                        if any(c['name'] == champ_name for c in sdef['champs']):
                            idx_type_map[idx] = stype
                            break
                except ValueError:
                    pass

    # Pour chaque type, construire la liste ordonnée des idx disponibles
    # (pour gérer plusieurs sections du même type)
    from collections import defaultdict
    type_idx_list = defaultdict(list)
    for idx in sorted(idx_type_map.keys()):
        type_idx_list[idx_type_map[idx]].append(idx)

    # Compteur d'occurrence par type pour prendre le bon idx
    type_occurrence = defaultdict(int)

    for ordre, (stype, obs) in enumerate(zip(types, obs_list)):
        if stype not in sections: continue
        donnees = {}

        # Prendre le idx correspondant à cette occurrence du type
        occurrence = type_occurrence[stype]
        idx_list = type_idx_list.get(stype, [])
        if occurrence < len(idx_list):
            matching_idx = idx_list[occurrence]
        else:
            matching_idx = ordre
        type_occurrence[stype] += 1

        for champ in sections[stype]['champs']:
            if champ['type'] == 'fichier':
                continue
            val = form.get(f"champ__{matching_idx}__{champ['name']}", '').strip()
            if val: donnees[champ['name']] = val
        db.session.add(SectionBilan(consultation_id=consultation_id, type=stype,
            ordre=ordre, titre='', observations=obs.strip() if obs else '',
            donnees=json.dumps(donnees, ensure_ascii=False)))
    if files:
        _save_fichiers_section(consultation_id, form, files, sections)


def _save_fichiers_section(consultation_id, form, files, sections):
    import uuid
    from collections import defaultdict
    types = form.getlist('sections_types[]')

    idx_type_map = {}
    for key in form.keys():
        if key.startswith('champ__'):
            parts = key.split('__')
            if len(parts) >= 3:
                try:
                    idx = int(parts[1])
                    champ_name = parts[2]
                    for stype, sdef in sections.items():
                        if any(c['name'] == champ_name for c in sdef['champs']):
                            idx_type_map[idx] = stype
                            break
                except ValueError:
                    pass

    type_idx_list = defaultdict(list)
    for idx in sorted(idx_type_map.keys()):
        type_idx_list[idx_type_map[idx]].append(idx)

    type_occurrence = defaultdict(int)

    for ordre, stype in enumerate(types):
        if stype not in sections: continue
        occurrence = type_occurrence[stype]
        idx_list = type_idx_list.get(stype, [])
        matching_idx = idx_list[occurrence] if occurrence < len(idx_list) else ordre
        type_occurrence[stype] += 1

        for champ in sections[stype]['champs']:
            if champ['type'] != 'fichier': continue
            key = f"champ__{matching_idx}__{champ['name']}"
            uploaded = files.getlist(key)
            for f in uploaded:
                if not f or not f.filename or not allowed_file(f.filename): continue
                ext = f.filename.rsplit('.', 1)[1].lower()
                ns = f"{uuid.uuid4().hex}.{ext}"
                tf = 'pdf' if ext=='pdf' else ('word' if ext in ('doc','docx') else 'image')
                folder = os.path.join(app.config['UPLOAD_FOLDER'], 'sections', str(consultation_id))
                os.makedirs(folder, exist_ok=True)
                f.save(os.path.join(folder, ns))
                db.session.add(FichierSection(
                    consultation_id=consultation_id,
                    section_ordre=ordre,
                    champ_name=champ['name'],
                    nom_original=secure_filename(f.filename),
                    nom_stocke=ns,
                    type_fichier=tf,
                    titre='',
                ))


# ============================================================
# ADMIN — SECTIONS
# ============================================================

# ============================================================
# ADMIN — CABINETS
# ============================================================

@app.route('/admin/cabinets')
@login_required
@admin_required
def admin_cabinets():
    cabinets = Cabinet.query.order_by(Cabinet.nom).all()
    praticiens = Praticien.query.filter_by(actif=True).order_by(Praticien.nom).all()
    return render_template('admin/cabinets.html', cabinets=cabinets, praticiens=praticiens)


@app.route('/admin/cabinet/nouveau', methods=['POST'])
@login_required
@admin_required
def admin_cabinet_nouveau():
    nom = request.form.get('nom', '').strip()
    if not nom: flash('Le nom est requis.', 'danger'); return redirect(url_for('admin_cabinets'))
    c = Cabinet(nom=nom, rue=request.form.get('rue','').strip(),
                code_postal=request.form.get('code_postal','').strip(),
                commune=request.form.get('commune','').strip(),
                telephone=request.form.get('telephone','').strip(),
                fax=request.form.get('fax','').strip(),
                email=request.form.get('email','').strip(),
                couleur=request.form.get('couleur','#1C2B3A').strip())
    db.session.add(c); db.session.commit()
    flash(f'Cabinet « {nom} » créé.', 'success')
    return redirect(url_for('admin_cabinets'))


@app.route('/admin/cabinet/<int:cabinet_id>/modifier', methods=['POST'])
@login_required
@admin_required
def admin_cabinet_modifier(cabinet_id):
    c = Cabinet.query.get_or_404(cabinet_id)
    c.nom         = request.form.get('nom', c.nom).strip()
    c.rue         = request.form.get('rue', '').strip()
    c.code_postal = request.form.get('code_postal', '').strip()
    c.commune     = request.form.get('commune', '').strip()
    c.telephone   = request.form.get('telephone', '').strip()
    c.fax         = request.form.get('fax', '').strip()
    c.email       = request.form.get('email', '').strip()
    c.couleur     = request.form.get('couleur', c.couleur or '#1C2B3A').strip()
    c.actif       = request.form.get('actif') == '1'
    db.session.commit(); flash('Cabinet mis à jour.', 'success')
    return redirect(url_for('admin_cabinets'))


@app.route('/admin/cabinet/<int:cabinet_id>/supprimer', methods=['POST'])
@login_required
@admin_required
def admin_cabinet_supprimer(cabinet_id):
    c = Cabinet.query.get_or_404(cabinet_id)
    nom = c.nom; db.session.delete(c); db.session.commit()
    flash(f'Cabinet « {nom} » supprimé.', 'success')
    return redirect(url_for('admin_cabinets'))


@app.route('/admin/praticien-cabinet/lier', methods=['POST'])
@login_required
@admin_required
def admin_praticien_cabinet_lier():
    """Lie un praticien à un cabinet avec son ADELI et forme juridique."""
    p_id  = request.form.get('praticien_id', type=int)
    c_id  = request.form.get('cabinet_id', type=int)
    adeli = request.form.get('adeli', '').strip()
    forme = request.form.get('forme_juridique', '').strip()
    existing = PraticienCabinet.query.filter_by(praticien_id=p_id, cabinet_id=c_id).first()
    if existing:
        existing.adeli = adeli; existing.forme_juridique = forme
    else:
        db.session.add(PraticienCabinet(praticien_id=p_id, cabinet_id=c_id,
                                        adeli=adeli, forme_juridique=forme))
    db.session.commit(); flash('Liaison mise à jour.', 'success')
    return redirect(url_for('admin_cabinets'))


@app.route('/admin/praticien-cabinet/<int:pc_id>/supprimer', methods=['POST'])
@login_required
@admin_required
def admin_praticien_cabinet_supprimer(pc_id):
    pc = PraticienCabinet.query.get_or_404(pc_id)
    db.session.delete(pc); db.session.commit()
    flash('Liaison supprimée.', 'success')
    return redirect(url_for('admin_cabinets'))


# ============================================================
# ADMIN — PRATICIENS
# ============================================================

@app.route('/admin/praticiens')
@login_required
@admin_required
def admin_praticiens():
    praticiens = Praticien.query.order_by(Praticien.nom).all()
    return render_template('admin/praticiens.html', praticiens=praticiens)


@app.route('/admin/praticien/nouveau', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_praticien_nouveau():
    if request.method == 'POST':
        login = request.form.get('login', '').strip().lower()
        if Praticien.query.filter_by(login=login).first():
            flash('Ce login est déjà utilisé.', 'danger')
            return redirect(url_for('admin_praticien_nouveau'))
        p = Praticien(
            nom    = request.form.get('nom', '').strip().upper(),
            prenom = request.form.get('prenom', '').strip().capitalize(),
            titre  = request.form.get('titre', 'Orthoptiste').strip(),
            email  = request.form.get('email', '').strip() or None,
            login  = login,
            rpps    = request.form.get('rpps', '').strip() or None,
            couleur = request.form.get('couleur', '#2E7D6B').strip(),
            role   = request.form.get('role', 'praticien'),
            actif  = True,
        )
        pwd = request.form.get('password', '').strip()
        if pwd:
            p.set_password(pwd)
        db.session.add(p); db.session.commit()
        flash(f'Praticien {p} créé.', 'success')
        return redirect(url_for('admin_praticiens'))
    return render_template('admin/praticien_form.html', praticien=None)


@app.route('/admin/praticien/<int:praticien_id>/modifier', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_praticien_modifier(praticien_id):
    p = Praticien.query.get_or_404(praticien_id)
    if request.method == 'POST':
        login = request.form.get('login', '').strip().lower()
        existing = Praticien.query.filter_by(login=login).first()
        if existing and existing.id != p.id:
            flash('Ce login est déjà utilisé.', 'danger')
            return redirect(url_for('admin_praticien_modifier', praticien_id=praticien_id))
        p.nom    = request.form.get('nom', '').strip().upper()
        p.prenom = request.form.get('prenom', '').strip().capitalize()
        p.titre  = request.form.get('titre', 'Orthoptiste').strip()
        p.email  = request.form.get('email', '').strip() or None
        p.login  = login
        p.rpps    = request.form.get('rpps', '').strip() or None
        p.couleur = request.form.get('couleur', '#2E7D6B').strip()
        p.role   = request.form.get('role', 'praticien')
        p.actif  = request.form.get('actif') == '1'
        pwd  = request.form.get('password', '').strip()
        pwd2 = request.form.get('password2', '').strip()
        if pwd:
            if pwd != pwd2:
                flash('Les mots de passe ne correspondent pas.', 'danger')
                return redirect(url_for('admin_praticien_modifier', praticien_id=praticien_id))
            p.set_password(pwd)
        db.session.commit()
        flash(f'Praticien {p} mis à jour.', 'success')
        return redirect(url_for('admin_praticiens'))
    return render_template('admin/praticien_form.html', praticien=p)


@app.route('/admin/praticien/<int:praticien_id>/supprimer', methods=['POST'])
@login_required
@admin_required
def admin_praticien_supprimer(praticien_id):
    p = Praticien.query.get_or_404(praticien_id)
    if p.id == current_user.id:
        flash('Vous ne pouvez pas supprimer votre propre compte.', 'danger')
        return redirect(url_for('admin_praticiens'))
    if p.role == 'admin':
        nb_admins = Praticien.query.filter_by(role='admin', actif=True).count()
        if nb_admins <= 1:
            flash('Impossible de supprimer le dernier administrateur.', 'danger')
            return redirect(url_for('admin_praticiens'))
    # Bloquer si le praticien a des consultations
    nb_consultations = Consultation.query.filter_by(praticien_id=p.id).count()
    if nb_consultations > 0:
        flash(
            f'{p} a {nb_consultations} consultation(s) enregistrée(s) et ne peut pas être supprimé. '
            f'Désactivez-le à la place.',
            'danger'
        )
        return redirect(url_for('admin_praticien_modifier', praticien_id=praticien_id))
    nom = str(p)
    db.session.delete(p)
    db.session.commit()
    flash(f'Praticien {nom} supprimé.', 'success')
    return redirect(url_for('admin_praticiens'))



    p = Praticien.query.get_or_404(praticien_id)
    if request.method == 'POST':
        login = request.form.get('login', '').strip().lower()
        existing = Praticien.query.filter_by(login=login).first()
        if existing and existing.id != p.id:
            flash('Ce login est déjà utilisé.', 'danger')
            return redirect(url_for('admin_praticien_modifier', praticien_id=praticien_id))
        p.nom    = request.form.get('nom', '').strip().upper()
        p.prenom = request.form.get('prenom', '').strip().capitalize()
        p.titre  = request.form.get('titre', 'Orthoptiste').strip()
        p.email  = request.form.get('email', '').strip() or None
        p.login  = login
        p.rpps    = request.form.get('rpps', '').strip() or None
        p.couleur = request.form.get('couleur', '#2E7D6B').strip()
        p.role   = request.form.get('role', 'praticien')
        p.actif  = request.form.get('actif') == '1'
        pwd  = request.form.get('password', '').strip()
        pwd2 = request.form.get('password2', '').strip()
        if pwd:
            if pwd != pwd2:
                flash('Les mots de passe ne correspondent pas.', 'danger')
                return redirect(url_for('admin_praticien_modifier', praticien_id=praticien_id))
            p.set_password(pwd)
        db.session.commit()
        flash(f'Praticien {p} mis à jour.', 'success')
        return redirect(url_for('admin_praticiens'))
    return render_template('admin/praticien_form.html', praticien=p)


@app.route('/praticien/<int:praticien_id>/signature')
@login_required
def signature_image(praticien_id):
    """Sert l'image de signature d'un praticien."""
    p = Praticien.query.get_or_404(praticien_id)
    if not p.signature or not os.path.exists(p.signature):
        return '', 404
    from flask import send_file
    return send_file(p.signature)


@app.route('/profil', methods=['GET', 'POST'])
@login_required
def profil():
    """Chaque praticien peut modifier son propre profil."""
    p = current_user
    if request.method == 'POST':
        p.prenom  = request.form.get('prenom', '').strip().capitalize()
        p.nom     = request.form.get('nom', '').strip().upper()
        p.titre   = request.form.get('titre', 'Orthoptiste').strip()
        p.email   = request.form.get('email', '').strip() or None
        p.couleur = request.form.get('couleur', p.couleur or '#2E7D6B').strip()
        pwd = request.form.get('password', '').strip()
        pwd2 = request.form.get('password2', '').strip()
        if pwd:
            if pwd != pwd2:
                flash('Les mots de passe ne correspondent pas.', 'danger')
                return redirect(url_for('profil'))
            p.set_password(pwd)

        # Upload signature
        sig_file = request.files.get('signature')
        if sig_file and sig_file.filename:
            import uuid
            ext = sig_file.filename.rsplit('.', 1)[-1].lower()
            if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
                sig_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'signatures')
                os.makedirs(sig_dir, exist_ok=True)
                nom_sig = f"sig_{p.id}_{uuid.uuid4().hex[:8]}.{ext}"
                sig_path = os.path.join(sig_dir, nom_sig)
                # Supprimer l'ancienne signature
                if p.signature and os.path.exists(p.signature):
                    os.remove(p.signature)
                sig_file.save(sig_path)
                p.signature = sig_path
            else:
                flash('Format image non supporté (png, jpg, gif, webp).', 'danger')
                return redirect(url_for('profil'))

        # Supprimer signature
        if request.form.get('supprimer_signature') == '1':
            if p.signature and os.path.exists(p.signature):
                os.remove(p.signature)
            p.signature = None

        db.session.commit()
        flash('Profil mis à jour.', 'success')
        return redirect(url_for('profil'))
    return render_template('admin/profil.html', praticien=p)


@app.route('/admin/sections/exporter')
@login_required
def admin_sections_exporter():
    """Exporte toutes les sections (natives et personnalisées) en JSON."""
    import json
    from flask import Response
    sections = SectionDef.query.order_by(SectionDef.ordre).all()
    data = {
        'version': 1,
        'type': 'sections_def',
        'sections': [{
            'type_key':          s.type_key,
            'label':             s.label,
            'ordre':             s.ordre,
            'builtin':           s.builtin,
            'actif':             s.actif,
            'obs_defaut':        s.obs_defaut or '',
            'avec_observations': s.avec_observations,
            'categorie':         s.categorie or '',
            'nb_colonnes':       s.nb_colonnes or 2,
            'champs': [{
                'name':    c.name,
                'label':   c.label,
                'type':    c.type,
                'ordre':   c.ordre,
                'options': [o.valeur for o in c.options if o.actif]
            } for c in s.champs if c.actif]
        } for s in sections]
    }
    return Response(
        json.dumps(data, indent=2, ensure_ascii=False),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=sections_def.json'}
    )


@app.route('/admin/sections/importer', methods=['POST'])
@login_required
def admin_sections_importer():
    """Importe des sections depuis un JSON."""
    import json
    f = request.files.get('fichier_sections')
    if not f or not f.filename.endswith('.json'):
        flash('Fichier invalide — JSON requis.', 'danger')
        return redirect(url_for('admin_sections'))
    try:
        data = json.load(f)
        if data.get('type') != 'sections_def':
            flash('Fichier non reconnu — ce n\'est pas un export de sections.', 'danger')
            return redirect(url_for('admin_sections'))
        mode       = request.form.get('mode', 'ajouter')
        importees  = 0
        mises_a_j  = 0
        ignorees   = 0

        for s_data in data.get('sections', []):
            type_key = s_data.get('type_key', '').strip()
            if not type_key:
                continue

            existing = SectionDef.query.filter_by(type_key=type_key).first()

            if existing:
                if s_data.get('builtin') and existing.builtin:
                    # Section native → mettre à jour seulement les propriétés éditables
                    existing.label             = s_data.get('label', existing.label)
                    existing.obs_defaut        = s_data.get('obs_defaut', existing.obs_defaut)
                    existing.avec_observations = s_data.get('avec_observations', existing.avec_observations)
                    existing.categorie         = s_data.get('categorie', existing.categorie)
                    existing.nb_colonnes       = s_data.get('nb_colonnes', existing.nb_colonnes or 2)
                    # Ajouter les champs manquants (sans supprimer les existants)
                    existing_names = {c.name for c in existing.champs}
                    for i, c_data in enumerate(s_data.get('champs', [])):
                        if c_data['name'] not in existing_names:
                            nc = ChampDef(
                                section_id=existing.id,
                                name=c_data['name'], label=c_data['label'],
                                type=c_data.get('type', 'text'),
                                ordre=c_data.get('ordre', 99)
                            )
                            db.session.add(nc)
                    mises_a_j += 1
                elif mode == 'ajouter':
                    ignorees += 1
                    continue
                else:  # remplacer section personnalisée
                    existing.label             = s_data.get('label', existing.label)
                    existing.obs_defaut        = s_data.get('obs_defaut', '')
                    existing.avec_observations = s_data.get('avec_observations', True)
                    existing.categorie         = s_data.get('categorie', '')
                    existing.nb_colonnes       = s_data.get('nb_colonnes', 2)
                    existing.actif             = s_data.get('actif', True)
                    # Remplacer les champs
                    for c in list(existing.champs):
                        db.session.delete(c)
                    db.session.flush()
                    for i, c_data in enumerate(s_data.get('champs', [])):
                        nc = ChampDef(
                            section_id=existing.id,
                            name=c_data['name'], label=c_data['label'],
                            type=c_data.get('type', 'text'),
                            ordre=c_data.get('ordre', i)
                        )
                        db.session.add(nc)
                        db.session.flush()
                        for val in c_data.get('options', []):
                            db.session.add(OptionDef(champ_id=nc.id, valeur=val, ordre=0))
                    mises_a_j += 1
            else:
                # Nouvelle section personnalisée
                if s_data.get('builtin'):
                    # Native non trouvée localement — créer comme personnalisée
                    pass
                ns = SectionDef(
                    type_key         = type_key,
                    label            = s_data.get('label', type_key),
                    ordre            = s_data.get('ordre', 99),
                    builtin          = False,
                    actif            = s_data.get('actif', True),
                    obs_defaut       = s_data.get('obs_defaut', ''),
                    avec_observations= s_data.get('avec_observations', True),
                    categorie        = s_data.get('categorie', ''),
                    nb_colonnes      = s_data.get('nb_colonnes', 2),
                )
                db.session.add(ns)
                db.session.flush()
                for i, c_data in enumerate(s_data.get('champs', [])):
                    nc = ChampDef(
                        section_id=ns.id,
                        name=c_data['name'], label=c_data['label'],
                        type=c_data.get('type', 'text'),
                        ordre=c_data.get('ordre', i)
                    )
                    db.session.add(nc)
                    db.session.flush()
                    for val in c_data.get('options', []):
                        db.session.add(OptionDef(champ_id=nc.id, valeur=val, ordre=0))
                importees += 1

        db.session.commit()
        msg = f'✅ {importees} section(s) importée(s), {mises_a_j} mise(s) à jour.'
        if ignorees:
            msg += f' {ignorees} ignorée(s) (type_key déjà existant).'
        flash(msg, 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Erreur lors de l\'import : {e}', 'danger')
    return redirect(url_for('admin_sections'))


@app.route('/admin/sections')
@login_required
def admin_sections():
    sections = SectionDef.query.order_by(SectionDef.ordre).all()
    return render_template('admin/sections.html', sections=sections)


@app.route('/admin/section/nouvelle', methods=['POST'])
@login_required
def admin_section_nouvelle():
    label = request.form.get('label', '').strip()
    if not label: flash('Le nom est requis.', 'danger'); return redirect(url_for('admin_sections'))
    key = slugify(label); i = 2
    while SectionDef.query.filter_by(type_key=key).first():
        key = f'{slugify(label)}_{i}'; i += 1
    max_o = db.session.query(db.func.max(SectionDef.ordre)).scalar() or 0
    s = SectionDef(type_key=key, label=label, ordre=max_o+1, builtin=False)
    db.session.add(s); db.session.commit()
    flash(f'Section « {label} » créée.', 'success')
    return redirect(url_for('admin_section_detail', section_id=s.id))


@app.route('/admin/section/<int:section_id>')
@login_required
def admin_section_detail(section_id):
    section = SectionDef.query.get_or_404(section_id)
    all_sections = SectionDef.query.order_by(SectionDef.ordre).all()
    return render_template('admin/section_detail.html', section=section, all_sections=all_sections)


@app.route('/admin/section/<int:section_id>/modifier', methods=['POST'])
@login_required
def admin_section_modifier(section_id):
    s = SectionDef.query.get_or_404(section_id)
    s.label             = request.form.get('label', s.label).strip()
    s.actif             = request.form.get('actif') == '1'
    s.obs_defaut        = request.form.get('obs_defaut', '').strip()
    s.avec_observations = request.form.get('avec_observations') == '1'
    s.categorie         = request.form.get('categorie', '').strip()
    s.nb_colonnes       = int(request.form.get('nb_colonnes', 2) or 2)
    db.session.commit(); flash('Section mise à jour.', 'success')
    return redirect(url_for('admin_section_detail', section_id=section_id))


@app.route('/admin/section/<int:section_id>/supprimer', methods=['POST'])
@login_required
def admin_section_supprimer(section_id):
    s = SectionDef.query.get_or_404(section_id)
    if s.builtin:
        flash('Section native : désactivez-la plutôt que la supprimer.', 'danger')
        return redirect(url_for('admin_section_detail', section_id=section_id))
    label = s.label; db.session.delete(s); db.session.commit()
    flash(f'Section « {label} » supprimée.', 'success')
    return redirect(url_for('admin_sections'))


@app.route('/admin/sections/reordonner', methods=['POST'])
@login_required
def admin_sections_reordonner():
    for i, sid in enumerate(request.json.get('ordre', [])):
        s = SectionDef.query.get(sid)
        if s: s.ordre = i
    db.session.commit(); return jsonify({'ok': True})


# ── Champs ──

@app.route('/admin/section/<int:section_id>/champ/nouveau', methods=['POST'])
@login_required
def admin_champ_nouveau(section_id):
    s = SectionDef.query.get_or_404(section_id)
    label = request.form.get('label', '').strip()
    type_ = request.form.get('type', 'text')
    # Label vide autorisé — on laisse vide ou on met un défaut pour fichier
    if not label and type_ == 'fichier':
        label = 'Pièces jointes'
    name = slugify(label) if label else 'pieces_jointes'; existing = [c.name for c in s.champs]; i = 2; orig = name
    while name in existing: name = f'{orig}_{i}'; i += 1
    max_o = max((c.ordre for c in s.champs), default=0)
    db.session.add(ChampDef(section_id=section_id, name=name, label=label,
                             type=type_, ordre=max_o+1))
    db.session.commit(); flash(f'Champ « {label} » ajouté.', 'success')
    return redirect(url_for('admin_section_detail', section_id=section_id))


@app.route('/admin/champ/<int:champ_id>/modifier', methods=['POST'])
@login_required
def admin_champ_modifier(champ_id):
    c = ChampDef.query.get_or_404(champ_id)
    c.label = request.form.get('label', c.label).strip()
    c.type  = request.form.get('type', c.type)
    c.actif = request.form.get('actif') == '1'
    db.session.commit(); flash('Champ mis à jour.', 'success')
    return redirect(url_for('admin_section_detail', section_id=c.section_id))


@app.route('/admin/champ/<int:champ_id>/supprimer', methods=['POST'])
@login_required
def admin_champ_supprimer(champ_id):
    c = ChampDef.query.get_or_404(champ_id); sid = c.section_id
    db.session.delete(c); db.session.commit(); flash('Champ supprimé.', 'success')
    return redirect(url_for('admin_section_detail', section_id=sid))


@app.route('/admin/champs/reordonner', methods=['POST'])
@login_required
def admin_champs_reordonner():
    for i, cid in enumerate(request.json.get('ordre', [])):
        c = ChampDef.query.get(cid)
        if c: c.ordre = i
    db.session.commit(); return jsonify({'ok': True})


# ── Options ──

@app.route('/admin/champ/<int:champ_id>/options_json')
@login_required
def admin_options_json(champ_id):
    c = ChampDef.query.get_or_404(champ_id)
    return jsonify({'options': [
        {'id': o.id, 'valeur': o.valeur}
        for o in c.options if o.actif
    ]})


@app.route('/admin/champ/<int:champ_id>/option/nouvelle', methods=['POST'])
@login_required
def admin_option_nouvelle(champ_id):
    c = ChampDef.query.get_or_404(champ_id)
    valeur = request.form.get('valeur', '').strip()
    if not valeur: flash('La valeur est requise.', 'danger'); return redirect(url_for('admin_section_detail', section_id=c.section_id))
    max_o = max((o.ordre for o in c.options), default=0)
    db.session.add(OptionDef(champ_id=champ_id, valeur=valeur, ordre=max_o+1))
    db.session.commit(); flash(f'Valeur « {valeur} » ajoutée.', 'success')
    return redirect(url_for('admin_section_detail', section_id=c.section_id))


@app.route('/admin/option/<int:option_id>/supprimer', methods=['POST'])
@login_required
def admin_option_supprimer(option_id):
    o = OptionDef.query.get_or_404(option_id); sid = o.champ.section_id; v = o.valeur
    db.session.delete(o); db.session.commit(); flash(f'Valeur « {v} » supprimée.', 'success')
    return redirect(url_for('admin_section_detail', section_id=sid))


@app.route('/admin/options/reordonner', methods=['POST'])
@login_required
def admin_options_reordonner():
    for i, oid in enumerate(request.json.get('ordre', [])):
        o = OptionDef.query.get(oid)
        if o: o.ordre = i
    db.session.commit(); return jsonify({'ok': True})


# ============================================================
# ADMIN — MODÈLES DE BILAN
# ============================================================

@app.route('/admin/modeles/exporter')
@login_required
def admin_modeles_exporter():
    """Exporte tous les modèles de bilan en JSON."""
    import json
    from flask import Response
    modeles = ModeleBilan.query.order_by(ModeleBilan.nom).all()
    data = {
        'version': 1,
        'type': 'modeles_bilan',
        'modeles': [m.to_dict() for m in modeles]
    }
    return Response(
        json.dumps(data, indent=2, ensure_ascii=False),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=modeles_bilan.json'}
    )


@app.route('/admin/modeles/exporter/<int:modele_id>')
@login_required
def admin_modele_exporter_un(modele_id):
    """Exporte un seul modèle de bilan en JSON."""
    import json
    from flask import Response
    m = ModeleBilan.query.get_or_404(modele_id)
    data = {'version': 1, 'type': 'modeles_bilan', 'modeles': [m.to_dict()]}
    nom = m.nom.replace(' ', '_').lower()
    return Response(
        json.dumps(data, indent=2, ensure_ascii=False),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename=modele_{nom}.json'}
    )


@app.route('/admin/modeles/importer', methods=['POST'])
@login_required
def admin_modeles_importer():
    """Importe des modèles de bilan depuis un JSON."""
    import json
    f = request.files.get('fichier_modeles')
    if not f or not f.filename.endswith('.json'):
        flash('Fichier invalide — JSON requis.', 'danger')
        return redirect(url_for('admin_modeles'))
    try:
        data = json.load(f)
        if data.get('type') != 'modeles_bilan':
            flash('Fichier non reconnu — ce n\'est pas un export de modèles de bilan.', 'danger')
            return redirect(url_for('admin_modeles'))
        mode      = request.form.get('mode', 'ajouter')  # ajouter ou remplacer
        importes  = 0
        ignores   = 0
        for m_data in data.get('modeles', []):
            nom = m_data.get('nom', '').strip()
            if not nom:
                continue
            # En mode "ajouter" : ignorer si un modèle du même nom existe déjà
            if mode == 'ajouter':
                existing = ModeleBilan.query.filter_by(nom=nom).first()
                if existing:
                    ignores += 1
                    continue
            # Créer le modèle
            m = ModeleBilan(nom=nom, motif=m_data.get('motif', ''))
            db.session.add(m)
            db.session.flush()
            for i, type_key in enumerate(m_data.get('sections', [])):
                db.session.add(ModeleBilanSection(
                    modele_id=m.id, type_key=type_key, ordre=i
                ))
            importes += 1
        db.session.commit()
        msg = f'✅ {importes} modèle(s) importé(s).'
        if ignores:
            msg += f' {ignores} ignoré(s) (nom déjà existant).'
        flash(msg, 'success')
    except Exception as e:
        flash(f'❌ Erreur lors de l\'import : {e}', 'danger')
    return redirect(url_for('admin_modeles'))


@app.route('/admin/modeles')
@login_required
def admin_modeles():
    modeles = ModeleBilan.query.order_by(ModeleBilan.nom).all()
    sections, ordre = get_sections()
    return render_template('admin/modeles.html', modeles=modeles,
                           sections_dispo=sections, sections_ordre=ordre)


@app.route('/admin/modele/nouveau', methods=['POST'])
@login_required
def admin_modele_nouveau():
    nom = request.form.get('nom', '').strip()
    if not nom:
        flash('Le nom du modèle est requis.', 'danger')
        return redirect(url_for('admin_modeles'))
    m = ModeleBilan(nom=nom, motif=request.form.get('motif', '').strip())
    db.session.add(m); db.session.flush()
    types = request.form.getlist('sections[]')
    sections, ordre = get_sections()
    # Trier selon l'ordre canonique
    types_sorted = [t for t in ordre if t in types]
    for i, t in enumerate(types_sorted):
        db.session.add(ModeleBilanSection(modele_id=m.id, type_key=t, ordre=i))
    db.session.commit()
    flash(f'Modèle « {nom} » créé.', 'success')
    return redirect(url_for('admin_modeles'))


@app.route('/admin/modele/<int:modele_id>/modifier', methods=['POST'])
@login_required
def admin_modele_modifier(modele_id):
    m = ModeleBilan.query.get_or_404(modele_id)
    m.nom   = request.form.get('nom', m.nom).strip()
    m.motif = request.form.get('motif', '').strip()
    m.actif = request.form.get('actif') == '1'
    for s in list(m.sections): db.session.delete(s)
    db.session.flush()
    types = request.form.getlist('sections[]')
    sections, ordre = get_sections()
    types_sorted = [t for t in ordre if t in types]
    for i, t in enumerate(types_sorted):
        db.session.add(ModeleBilanSection(modele_id=m.id, type_key=t, ordre=i))
    db.session.commit()
    flash('Modèle mis à jour.', 'success')
    return redirect(url_for('admin_modeles'))


@app.route('/admin/modele/<int:modele_id>/supprimer', methods=['POST'])
@login_required
def admin_modele_supprimer(modele_id):
    m = ModeleBilan.query.get_or_404(modele_id)
    nom = m.nom; db.session.delete(m); db.session.commit()
    flash(f'Modèle « {nom} » supprimé.', 'success')
    return redirect(url_for('admin_modeles'))


# ============================================================
# DONNÉES INITIALES
# ============================================================

BUILTIN_SECTIONS = [
    ('anam','Anamnèse',[('entretien','Entretien','textarea',[]),('plan_general','Plan général','textarea',[]),('antecedents','Antécédents','textarea',[])]),
    ('correction_portee','Correction portée',[('od_sph','OD sphère','sph',[]),('od_cyl','OD cylindre','text',[]),('od_axe','OD axe','number',[]),('od_add','Add OD','number',[]),('og_sph','OG sphère','sph',[]),('og_cyl','OG cylindre','text',[]),('og_axe','OG axe','number',[]),('og_add','Add OG','number',[]),('prisme_od','Prisme OD','text',[]),('prisme_og','Prisme OG','text',[])]),
    ('frontofocometrie','Frontofocométrie',[('od_sph','OD sphère','sph',[]),('od_cyl','OD cylindre','text',[]),('od_axe','OD axe','number',[]),('od_add','Add OD','number',[]),('og_sph','OG sphère','sph',[]),('og_cyl','OG cylindre','text',[]),('og_axe','OG axe','number',[]),('og_add','Add OG','number',[]),('prisme_od','Prisme OD','text',[]),('prisme_og','Prisme OG','text',[])]),
    ('acuite','Acuité visuelle',[
        ('av_bino','Binoculaire','select',['1/10','2/10','3/10','4/10','5/10','6/10','7/10','8/10','9/10','10/10']),
        ('av_correction','Correction','select',['sans correction','avec correction habituelle','avec correction optimale','avec addition']),
        ('av_od_loin','OD de loin','select',['1/10','2/10','3/10','4/10','5/10','6/10','7/10','8/10','9/10','10/10']),
        ('av_od_pres','OD de près','select',['P14','P10','P8','P6','P5','P4','P3','P2','P1.5','P1']),
        ('av_og_loin','OG de loin','select',['1/10','2/10','3/10','4/10','5/10','6/10','7/10','8/10','9/10','10/10']),
        ('av_og_pres','OG de près','select',['P14','P10','P8','P6','P5','P4','P3','P2','P1.5','P1'])]),
    ('refraction_obj','Réfraction objective',[('od_sph','OD sphère','sph',[]),('od_cyl','OD cylindre','text',[]),('od_axe','OD axe','number',[]),('og_sph','OG sphère','sph',[]),('og_cyl','OG cylindre','text',[]),('og_axe','OG axe','number',[])]),
    ('refraction_subj','Réfraction subjective',[('od_sph','OD sphère','sph',[]),('od_cyl','OD cylindre','text',[]),('od_axe','OD axe','number',[]),('od_add','Add OD','number',[]),('og_sph','OG sphère','sph',[]),('og_cyl','OG cylindre','text',[]),('og_axe','OG axe','number',[]),('og_add','Add OG','number',[]),
        ('od_av_loin','AV OD de loin','select',['1/10','2/10','3/10','4/10','5/10','6/10','7/10','8/10','9/10','10/10']),
        ('od_av_pres','AV OD de près','select',['P14','P10','P8','P6','P5','P4','P3','P2','P1.5','P1']),
        ('og_av_loin','AV OG de loin','select',['1/10','2/10','3/10','4/10','5/10','6/10','7/10','8/10','9/10','10/10']),
        ('og_av_pres','AV OG de près','select',['P14','P10','P8','P6','P5','P4','P3','P2','P1.5','P1'])]),
    ('swaine','Swaine inverse',[('swaine_od','OD /10','number',[]),('swaine_og','OG /10','number',[])]),
    ('stereoscopie','Vision stéréoscopique',[('tno','TNO (norme ≤60")','select',['480"','240"','120"','60"','30"','15"','non réalisable']),('lang','Lang','select',['positif','négatif','non réalisable'])]),
    ('cover','Examen sous écran',[('cover_loin','Cover de loin','select',['orthophorie','ésophorie','exophorie','hypophorie OD','hyperphorie OD','ésotropie','exotropie','hypertropie OD','hypertropie OG']),('cover_pres','Cover de près','select',['orthophorie','ésophorie','exophorie','hypophorie OD','hyperphorie OD','ésotropie','exotropie','hypertropie OD','hypertropie OG']),('dip_mm','DIP (mm)','number',[]),('ac_a','AC/A (norme 4±2)','number',[])]),
    ('motilite','Motilité',[('motilite','Résultat','textarea',[])]),
    ('ppc','PPC',[('ppc_cm',"Suit jusqu'à (norme ≤5cm)",'select',['2 cm','3 cm','4 cm','5 cm','6 cm','7 cm','8 cm','10 cm','>10 cm','non réalisé'])]),
    ('maddox','Maddox',[('maddox_loin','De loin','select',['orthophorie','ésophorie','exophorie','hypophorie','hyperphorie','cyclophorie']),('maddox_pres','De près','select',['orthophorie','ésophorie','exophorie','hypophorie','hyperphorie','cyclophorie'])]),
    ('angle','Angle objectif',[('angle_loin','De loin','select',['orthotropie','ésotropie <10Δ','ésotropie 10-20Δ','ésotropie >20Δ','exotropie <10Δ','exotropie 10-20Δ','exotropie >20Δ','hypertropie']),('angle_pres','De près','select',['orthotropie','ésotropie <10Δ','ésotropie 10-20Δ','ésotropie >20Δ','exotropie <10Δ','exotropie 10-20Δ','exotropie >20Δ','hypertropie'])]),
    ('prismes','Prismes — amplitudes de fusion',[('conv_loin','Convergence de loin (norme 30Δ)','select',['<10Δ','10-20Δ','20-30Δ','30-40Δ','>40Δ']),('conv_pres','Convergence de près (norme 40Δ)','select',['<10Δ','10-20Δ','20-40Δ','40-50Δ','>50Δ']),('div_loin','Divergence de loin (norme 8Δ)','select',['<4Δ','4-8Δ','8-12Δ','>12Δ']),('div_pres','Divergence de près (norme 12Δ)','select',['<6Δ','6-12Δ','12-16Δ','>16Δ'])]),
    ('facilites_accom',"Facilités d'accommodation",[('accom_cpm','Résultat (cpm)','select',['<3 cpm','3-5 cpm','5-8 cpm','8-10 cpm','>10 cpm','non réalisé']),('accom_lenteur','Lenteur en','select',['+','−','± égal','non précisé'])]),
    ('facilites_verg','Facilités de vergences',[('verg_cpm','Résultat (cpm) (norme 15±3)','select',['<6 cpm','6-9 cpm','9-12 cpm','12-15 cpm','15-18 cpm','>18 cpm','non réalisé'])]),
    ('ordonnance_lunettes','Ordonnance de lunettes',[
        # VL
        ('lun_vl_od_sph',  'VL OD — Sphère',    'text', []),
        ('lun_vl_od_cyl',  'VL OD — Cylindre',  'text', []),
        ('lun_vl_od_axe',  'VL OD — Axe',       'text', []),
        ('lun_vl_og_sph',  'VL OG — Sphère',    'text', []),
        ('lun_vl_og_cyl',  'VL OG — Cylindre',  'text', []),
        ('lun_vl_og_axe',  'VL OG — Axe',       'text', []),
        # VP
        ('lun_vp_od_add',  'VP OD — Addition',  'text', []),
        ('lun_vp_og_add',  'VP OG — Addition',  'text', []),
        # EP
        ('lun_ep_vl',      'Écart pupillaire VL (mm)', 'text', []),
        ('lun_ep_vp',      'Écart pupillaire VP (mm)', 'text', []),
        # Remarques
        ('lun_remarques',  'Remarques',          'textarea', []),
    ]),
    ('conclusions','Conclusions',[('conclusions','Conclusions','textarea',[]),('recommandations','Recommandations','textarea',[])]),
    ('ordonnance','Ordonnances',[
        # Ortopad / Opticlude
        ('orto_actif','Ortopad / Opticlude','select',['','Oui']),
        ('orto_oeil','Œil à occlure','select',['','OD','OG']),
        ('orto_heures','Heures par jour','text',[]),
        ('orto_duree','Durée du traitement','text',[]),
        ('orto_notes','Notes','text',[]),
        # Prisme Press-On
        ('prisme_actif','Prisme Press-On','select',['','Oui']),
        ('prisme_od_diop','OD dioptries','text',[]),
        ('prisme_od_base','OD base','select',['','nasale','temporale','inférieure','supérieure']),
        ('prisme_og_diop','OG dioptries','text',[]),
        ('prisme_og_base','OG base','select',['','nasale','temporale','inférieure','supérieure']),
        # Filtre Ryser
        ('ryser_actif','Filtre Ryser','select',['','Oui']),
        ('ryser_od_num','OD Ryser N°','text',[]),
        ('ryser_od_av','OD AV laissée (/10)','text',[]),
        ('ryser_og_num','OG Ryser N°','text',[]),
        ('ryser_og_av','OG AV laissée (/10)','text',[]),
    ]),
]


@app.context_processor
def inject_cabinet():
    """Rend cabinet_courant et cabinets_dispo disponibles dans tous les templates."""
    if current_user.is_authenticated:
        return {
            'cabinet_courant': get_current_cabinet(),
            'cabinets_dispo':  get_cabinets_praticien(),
        }
    return {'cabinet_courant': None, 'cabinets_dispo': []}


def init_db():
    with app.app_context():
        db.create_all()
        if not Praticien.query.first():
            p = Praticien(nom='Dupont', prenom='Marie', login='marie.dupont',
                          email='marie@cabinet.fr', role='admin')
            p.set_password('admin')
            db.session.add(p); db.session.commit()
            print('Praticien admin créé : login=marie.dupont  mot de passe=admin')
            print('⚠  Changez ce mot de passe dès la première connexion !')
        if not SectionDef.query.first():
            for ordre, (key, label, champs) in enumerate(BUILTIN_SECTIONS):
                sd = SectionDef(type_key=key, label=label, ordre=ordre, builtin=True)
                db.session.add(sd); db.session.flush()
                for co, (name, cl, ct, opts) in enumerate(champs):
                    cd = ChampDef(section_id=sd.id, name=name, label=cl, type=ct, ordre=co)
                    db.session.add(cd); db.session.flush()
                    for oo, v in enumerate(opts):
                        db.session.add(OptionDef(champ_id=cd.id, valeur=v, ordre=oo))
            db.session.commit(); print('Sections built-in injectées.')




# ============================================================
# ADMIN — MODÈLES DE DOCUMENTS
# ============================================================

@app.route('/admin/document-modeles/exporter')
@login_required
def admin_document_modeles_exporter():
    """Exporte tous les modèles de documents en JSON."""
    import json
    from flask import Response
    modeles = DocumentModele.query.order_by(DocumentModele.type, DocumentModele.nom).all()
    data = {
        'version': 1,
        'type': 'modeles_documents',
        'modeles': [{
            'nom':   m.nom,
            'type':  m.type,
            'actif': m.actif,
            'blocs': [{'type': b.type, 'contenu': b.contenu, 'ordre': b.ordre}
                      for b in m.blocs]
        } for m in modeles]
    }
    return Response(
        json.dumps(data, indent=2, ensure_ascii=False),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=modeles_documents.json'}
    )


@app.route('/admin/document-modele/<int:modele_id>/exporter')
@login_required
def admin_document_modele_exporter_un(modele_id):
    """Exporte un seul modèle de document en JSON."""
    import json
    from flask import Response
    m = DocumentModele.query.get_or_404(modele_id)
    data = {
        'version': 1,
        'type': 'modeles_documents',
        'modeles': [{'nom': m.nom, 'type': m.type, 'actif': m.actif,
                     'blocs': [{'type': b.type, 'contenu': b.contenu, 'ordre': b.ordre}
                               for b in m.blocs]}]
    }
    nom = m.nom.replace(' ', '_').lower()
    return Response(
        json.dumps(data, indent=2, ensure_ascii=False),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename=modele_doc_{nom}.json'}
    )


@app.route('/admin/document-modeles/importer', methods=['POST'])
@login_required
def admin_document_modeles_importer():
    """Importe des modèles de documents depuis un JSON."""
    import json
    f = request.files.get('fichier_modeles')
    if not f or not f.filename.endswith('.json'):
        flash('Fichier invalide — JSON requis.', 'danger')
        return redirect(url_for('admin_document_modeles'))
    try:
        data = json.load(f)
        if data.get('type') != 'modeles_documents':
            flash('Fichier non reconnu — ce n\'est pas un export de modèles de documents.', 'danger')
            return redirect(url_for('admin_document_modeles'))
        mode     = request.form.get('mode', 'ajouter')
        importes = 0
        ignores  = 0
        for m_data in data.get('modeles', []):
            nom  = m_data.get('nom', '').strip()
            type_ = m_data.get('type', '').strip()
            if not nom or not type_:
                continue
            if mode == 'ajouter':
                existing = DocumentModele.query.filter_by(nom=nom, type=type_).first()
                if existing:
                    ignores += 1
                    continue
            m = DocumentModele(nom=nom, type=type_, actif=m_data.get('actif', True))
            db.session.add(m)
            db.session.flush()
            for b_data in m_data.get('blocs', []):
                db.session.add(DocumentBloc(
                    modele_id=m.id,
                    type=b_data.get('type', 'texte'),
                    contenu=b_data.get('contenu', ''),
                    ordre=b_data.get('ordre', 99)
                ))
            importes += 1
        db.session.commit()
        msg = f'✅ {importes} modèle(s) importé(s).'
        if ignores:
            msg += f' {ignores} ignoré(s) (nom+type déjà existant).'
        flash(msg, 'success')
    except Exception as e:
        flash(f'❌ Erreur lors de l\'import : {e}', 'danger')
    return redirect(url_for('admin_document_modeles'))


@app.route('/admin/document-modeles')
@login_required
def admin_document_modeles():
    modeles = DocumentModele.query.order_by(DocumentModele.type, DocumentModele.nom).all()
    sections, ordre = get_sections()
    return render_template('admin/document_modeles.html',
                           modeles=modeles, sections_dispo=sections, sections_ordre=ordre)


@app.route('/admin/document-modele/nouveau', methods=['POST'])
@login_required
def admin_document_modele_nouveau():
    nom   = request.form.get('nom', '').strip()
    type_ = request.form.get('type', 'courrier')
    if not nom:
        flash('Le nom est requis.', 'danger')
        return redirect(url_for('admin_document_modeles'))
    m = DocumentModele(nom=nom, type=type_)
    db.session.add(m); db.session.commit()
    flash(f'Modèle « {nom} » créé.', 'success')
    return redirect(url_for('admin_document_modele_detail', modele_id=m.id))


@app.route('/admin/document-modele/<int:modele_id>')
@login_required
def admin_document_modele_detail(modele_id):
    m = DocumentModele.query.get_or_404(modele_id)
    modeles = DocumentModele.query.order_by(DocumentModele.type, DocumentModele.nom).all()
    sections, ordre = get_sections()
    categories = CategorieSection.query.order_by(CategorieSection.ordre).all()
    return render_template('admin/document_modele_detail.html',
                           modele=m, modeles=modeles,
                           sections_dispo=sections, sections_ordre=ordre,
                           categories=categories)


@app.route('/admin/document-modele/<int:modele_id>/modifier', methods=['POST'])
@login_required
def admin_document_modele_modifier(modele_id):
    m = DocumentModele.query.get_or_404(modele_id)
    m.nom   = request.form.get('nom', m.nom).strip()
    m.type  = request.form.get('type', m.type)
    m.actif = request.form.get('actif') == '1'
    db.session.commit(); flash('Modèle mis à jour.', 'success')
    return redirect(url_for('admin_document_modele_detail', modele_id=modele_id))


@app.route('/admin/document-modele/<int:modele_id>/supprimer', methods=['POST'])
@login_required
def admin_document_modele_supprimer(modele_id):
    m = DocumentModele.query.get_or_404(modele_id)
    nom = m.nom; db.session.delete(m); db.session.commit()
    flash(f'Modèle « {nom} » supprimé.', 'success')
    return redirect(url_for('admin_document_modeles'))


@app.route('/admin/document-modele/<int:modele_id>/bloc/nouveau', methods=['POST'])
@login_required
def admin_document_bloc_nouveau(modele_id):
    m = DocumentModele.query.get_or_404(modele_id)
    max_o = max((b.ordre for b in m.blocs), default=0)
    b = DocumentBloc(modele_id=modele_id,
                     type=request.form.get('type', 'texte'),
                     contenu=request.form.get('contenu', '').strip(),
                     ordre=max_o + 1)
    db.session.add(b); db.session.commit()
    flash('Bloc ajouté.', 'success')
    return redirect(url_for('admin_document_modele_detail', modele_id=modele_id))


@app.route('/admin/document-bloc/<int:bloc_id>/modifier', methods=['POST'])
@login_required
def admin_document_bloc_modifier(bloc_id):
    b = DocumentBloc.query.get_or_404(bloc_id)
    b.contenu = request.form.get('contenu', '').strip()
    if 'label' in request.form:
        b.label = request.form.get('label', '').strip()
    if 'filtre_categories' in request.form:
        import json
        cats = [c.strip() for c in request.form.get('filtre_categories', '').split(',') if c.strip()]
        b.filtre_categories = json.dumps(cats) if cats else None
    if 'sections_predef' in request.form:
        import json
        secs = [s.strip() for s in request.form.get('sections_predef', '').split(',') if s.strip()]
        b.sections_predef = json.dumps(secs) if secs else None
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/admin/document-bloc/<int:bloc_id>/supprimer', methods=['POST'])
@login_required
def admin_document_bloc_supprimer(bloc_id):
    b = DocumentBloc.query.get_or_404(bloc_id)
    mid = b.modele_id; db.session.delete(b); db.session.commit()
    flash('Bloc supprimé.', 'success')
    return redirect(url_for('admin_document_modele_detail', modele_id=mid))


@app.route('/admin/document-blocs/reordonner', methods=['POST'])
@login_required
def admin_document_blocs_reordonner():
    for i, bid in enumerate(request.json.get('ordre', [])):
        b = DocumentBloc.query.get(bid)
        if b: b.ordre = i
    db.session.commit(); return jsonify({'ok': True})


# ============================================================
# GÉNÉRATION DE DOCUMENTS
# ============================================================

@app.route('/consultation/<int:consultation_id>/ordonnance/lunettes/editer-collabora')
@login_required
def editer_ordonnance_lunettes_collabora(consultation_id):
    """Génère une ordonnance de lunettes et l'ouvre dans Collabora."""
    import os, shutil, uuid, urllib.parse
    c = Consultation.query.get_or_404(consultation_id)
    praticien = c.praticien
    cabinet   = c.cabinet
    pc = None
    if cabinet:
        pc = PraticienCabinet.query.filter_by(
            praticien_id=praticien.id, cabinet_id=cabinet.id).first()

    sec = next((s for s in c.sections if s.type == 'ordonnance_lunettes'), None)
    d = sec.get_donnees() if sec else {}

    nom_doc = f"{c.patient.nom}_{c.patient.prenom}_{c.date_consult.strftime('%Y%m%d')}_Lunettes.docx"
    docx_path = _generer_ordonnance_lunettes_docx(c, praticien, cabinet, pc, d)

    wopi_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'wopi')
    os.makedirs(wopi_dir, exist_ok=True)
    permanent_path = os.path.join(wopi_dir, f"{uuid.uuid4().hex}.docx")
    shutil.copy2(docx_path, permanent_path)

    section_ordre = sec.ordre if sec else 0
    token = _wopi_token_for(consultation_id, 'ordonnance_lunettes',
                            permanent_path, nom_doc, section_ordre=section_ordre)

    wopi_src = f"{get_wopi_base_url()}/wopi/files/{token}"
    collabora_action_url = _get_collabora_url(nom_doc)
    editor_url = f"{collabora_action_url}WOPISrc={urllib.parse.quote(wopi_src, safe='')}&access_token={token}&darkTheme=false&ignoreSysTheme=1"
    editor_url = editor_url.replace('?&', '?').replace('&&', '&')

    return render_template('consultations/collabora_editor.html',
                           consultation=c,
                           editor_url=editor_url,
                           nom_fichier=nom_doc,
                           token=token,
                           section_type='ordonnance_lunettes',
                           collabora_url=get_collabora_url())


def _generer_ordonnance_lunettes_docx(consultation, praticien, cabinet, pc, d):
    """Génère un docx d'ordonnance de lunettes."""
    import zipfile, re, tempfile, os
    p = consultation.patient
    esc = lambda s: (s or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

    entete_path = os.path.join(app.root_path, 'entete.docx')
    tmpdir = tempfile.mkdtemp()

    with zipfile.ZipFile(entete_path, 'r') as z:
        doc_xml = z.read('word/document.xml').decode('utf-8')

    # Substitutions cabinet/praticien
    cab_rue     = (cabinet.rue or '') if cabinet else ''
    cab_cp_comm = f"{(cabinet.code_postal or '')} {(cabinet.commune or '')}".strip() if cabinet else ''
    cab_commune = (cabinet.commune or 'Yssingeaux') if cabinet else 'Yssingeaux'
    cab_tel     = (cabinet.telephone or '') if cabinet else ''
    cab_email   = (cabinet.email or '') if cabinet else ''
    adeli       = (pc.adeli if pc else '') or ''
    forme       = (pc.forme_juridique if pc else '') or ''
    prat_nom    = f"{praticien.prenom} {praticien.nom}"
    prat_rpps   = praticien.rpps or ''
    prat_titre  = praticien.titre or 'Orthoptiste'
    date_str    = consultation.date_consult.strftime('%d/%m/%Y')
    pat_nom     = f'{p.prenom} {p.nom}'
    pat_ddn     = p.date_naissance.strftime('%d/%m/%Y') if p.date_naissance else ''

    def sub(xml, old, new):
        return xml.replace(old, esc(new)) if old in xml else xml

    doc_xml = sub(doc_xml, '130, Boulevard de la Paix', cab_rue)
    doc_xml = doc_xml.replace(
        '<w:p><w:pPr><w:pStyle w:val="Header"/><w:rPr><w:color w:themeColor="text1" w:val="000000"/></w:rPr></w:pPr><w:r><w:rPr><w:color w:themeColor="text1" w:val="000000"/></w:rPr><w:t>Résidence les jardinières</w:t></w:r></w:p>', '')
    doc_xml = sub(doc_xml, '43200 Yssingeaux', cab_cp_comm)
    doc_xml = sub(doc_xml, '04 71 59 01 38', cab_tel)
    doc_xml = sub(doc_xml, 'orthoptistes-yssingeaux@outlook.fr', cab_email)
    doc_xml = sub(doc_xml, 'ADELI\xa0: 439287145', f'ADELI : {adeli}' if adeli else '')
    doc_xml = sub(doc_xml, 'RPPS\xa0: 10010253291', f'RPPS : {prat_rpps}' if prat_rpps else '')
    doc_xml = sub(doc_xml, 'SELARL', forme)
    doc_xml = sub(doc_xml, ' Cyprien Nesme', f' {prat_nom}')
    doc_xml = sub(doc_xml, 'ORTHOPTISTE', prat_titre)
    doc_xml = sub(doc_xml, 'Prise de rendez-vous sur Doctolib', '')
    doc_xml = doc_xml.replace(
        f'A\xa0Yssingeaux, le </w:t></w:r><w:bookmarkEnd w:id="0"/>',
        f'A\xa0{esc(cab_commune)}, le {date_str}</w:t></w:r><w:bookmarkEnd w:id="0"/>'
    )

    # SDT Nom + DDN
    doc_xml = re.sub(
        r'<w:sdt><w:sdtPr><w:alias w:val="Nom"/>.*?<w:sdtContent>.*?</w:sdtContent></w:sdt>',
        f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="20"/></w:rPr><w:t>{esc(pat_nom)}</w:t></w:r>',
        doc_xml, flags=re.DOTALL)
    doc_xml = re.sub(
        r'<w:sdt><w:sdtPr><w:alias w:val="Pr[eé]nom"/>.*?</w:sdt>', '', doc_xml, flags=re.DOTALL)
    doc_xml = doc_xml.replace('DDN : </w:t></w:r>',
                              f'DDN : {esc(pat_ddn)}</w:t></w:r>')
    doc_xml = re.sub(
        r'<w:sdt><w:sdtPr><w:alias w:val="Commentaires ".*?<w:sdtContent>.*?</w:sdtContent></w:sdt>',
        '<w:r><w:t></w:t></w:r>', doc_xml, flags=re.DOTALL)

    # Supprimer âge, classe, médecin
    doc_xml = re.sub(r'<w:tab/><w:t xml:space="preserve">Âge\s*:\s*</w:t>.*?<w:t xml:space="preserve">Classe\s*:\s*</w:t>', '', doc_xml, flags=re.DOTALL)
    doc_xml = re.sub(r'<w:p[^>]*>(?:(?!</w:p>).)*?[Mm]édecin(?:(?!</w:p>).)*?</w:p>', '', doc_xml, flags=re.DOTALL)

    # Titre
    doc_xml = doc_xml.replace('<w:t>BILAN ORTHOPTIQUE</w:t>',
                              '<w:t>ORDONNANCE DE LUNETTES</w:t>')

    # Corps de l'ordonnance
    def val(k): return esc(d.get(k, '') or '')

    def format_correction(sph, cyl, axe):
        """Formate la correction sous forme sphère(cylindre)axe°"""
        if not sph: return ''
        result = sph
        if cyl: result += f'({cyl})'
        if axe: result += f'{axe}°'
        return result

    body_paras = []

    # Mention VERRES CORRECTEURS + MONTURE
    body_paras.append(
        f'<w:p><w:pPr><w:jc w:val="center"/><w:spacing w:before="240" w:after="120"/></w:pPr>'
        f'<w:r><w:rPr><w:b/><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
        f'<w:sz w:val="22"/><w:u w:val="single"/></w:rPr>'
        f'<w:t>VERRES CORRECTEURS + MONTURE</w:t></w:r></w:p>'
    )
    # 2 sauts de ligne
    body_paras.append(f'<w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p>')
    body_paras.append(f'<w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p>')

    # Correction OD
    od_vl = format_correction(val('lun_vl_od_sph'), val('lun_vl_od_cyl'), val('lun_vl_od_axe'))
    og_vl = format_correction(val('lun_vl_og_sph'), val('lun_vl_og_cyl'), val('lun_vl_og_axe'))
    od_add = val('lun_vp_od_add')
    og_add = val('lun_vp_og_add')

    if od_vl:
        body_paras.append(
            f'<w:p><w:pPr><w:spacing w:after="80"/></w:pPr>'
            f'<w:r><w:rPr><w:b/><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="22"/></w:rPr>'
            f'<w:t xml:space="preserve">OD : </w:t></w:r>'
            f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="22"/></w:rPr>'
            f'<w:t>{od_vl}{(" Add. " + od_add) if od_add else ""}</w:t></w:r></w:p>'
        )
    if og_vl:
        body_paras.append(
            f'<w:p><w:pPr><w:spacing w:after="80"/></w:pPr>'
            f'<w:r><w:rPr><w:b/><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="22"/></w:rPr>'
            f'<w:t xml:space="preserve">OG : </w:t></w:r>'
            f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="22"/></w:rPr>'
            f'<w:t>{og_vl}{(" Add. " + og_add) if og_add else ""}</w:t></w:r></w:p>'
        )

    # DIP
    dip = val('lun_dip')
    if dip:
        body_paras.append(
            f'<w:p><w:pPr><w:spacing w:after="80"/></w:pPr>'
            f'<w:r><w:rPr><w:b/><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="22"/></w:rPr>'
            f'<w:t xml:space="preserve">DIP : </w:t></w:r>'
            f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="22"/></w:rPr>'
            f'<w:t>{dip} mm</w:t></w:r></w:p>'
        )

    # Remarques
    remarques = d.get('lun_remarques', '') or ''
    if remarques:
        for ligne in remarques.split('\n'):
            body_paras.append(
                f'<w:p><w:pPr><w:spacing w:after="60"/></w:pPr>'
                f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="20"/></w:rPr>'
                f'<w:t xml:space="preserve">{esc(ligne)}</w:t></w:r></w:p>'
            )

    # Renouvelable — avant la mention légale
    renouvelable = val('lun_renouvelable')
    body_paras.append(
        f'<w:p><w:pPr><w:spacing w:before="200" w:after="80"/></w:pPr>'
        f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="20"/></w:rPr>'
        f'<w:t>{"Renouvelable pendant 2 ans" if renouvelable == "oui" else "Non renouvelable"}</w:t></w:r></w:p>'
    )

    # Mention légale
    body_paras.append(f'<w:p><w:pPr><w:spacing w:before="320"/></w:pPr></w:p>')
    for ligne_legale in [
        "L'examen ne revêt pas un caractère médical.",
        "La prochaine prescription de lunettes devra impérativement être réalisée par un ophtalmologiste."
    ]:
        body_paras.append(
            f'<w:p><w:pPr><w:spacing w:after="60"/></w:pPr>'
            f'<w:r><w:rPr><w:i/><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="18"/></w:rPr>'
            f'<w:t xml:space="preserve">{esc(ligne_legale)}</w:t></w:r></w:p>'
        )

    # Signature
    body_paras.append(
        f'<w:p><w:pPr><w:jc w:val="right"/><w:spacing w:before="480"/></w:pPr>'
        f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="20"/></w:rPr>'
        f'<w:t>{esc(prat_nom)}</w:t></w:r></w:p>'
    )

    doc_xml = doc_xml.replace('</w:body>', '\n'.join(body_paras) + '</w:body>')

    # Signature image
    sig_path = praticien.signature if praticien.signature else None
    sig_img_data = None
    sig_ext = None
    sig_rel_id = None
    if sig_path and os.path.exists(sig_path):
        with open(sig_path, 'rb') as sf:
            sig_img_data = sf.read()
        sig_ext = sig_path.rsplit('.', 1)[-1].lower()
        sig_rel_id = 'rIdSig1'
        try:
            from PIL import Image as PILImage
            import io as _io
            img = PILImage.open(_io.BytesIO(sig_img_data))
            img_w, img_h = img.size
            cx = 1800000
            cy = int(cx * img_h / img_w)
        except Exception:
            cx, cy = 1800000, 900000
        sig_para = (
            f'<w:p><w:pPr><w:jc w:val="right"/><w:spacing w:before="120"/></w:pPr>'
            f'<w:r><w:rPr/><w:drawing>'
            f'<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
            f'<wp:docPr id="1" name="signature"/>'
            f'<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="1" name="signature"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill>'
            f'<a:blip r:embed="{sig_rel_id}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
            f'<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            f'</pic:pic></a:graphicData></a:graphic>'
            f'</wp:inline></w:drawing></w:r></w:p>'
        )
        doc_xml = doc_xml.replace('</w:body>', sig_para + '</w:body>')

    new_out = os.path.join(tmpdir, 'final_lunettes.docx')
    with zipfile.ZipFile(entete_path, 'r') as zin:
        with zipfile.ZipFile(new_out, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == 'word/document.xml':
                    zout.writestr(item, doc_xml.encode('utf-8'))
                elif item.filename == 'word/_rels/document.xml.rels' and sig_img_data:
                    rels = zin.read(item.filename).decode('utf-8')
                    rels = rels.replace('</Relationships>',
                        f'<Relationship Id="{sig_rel_id}" '
                        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                        f'Target="media/signature.{sig_ext}"/></Relationships>')
                    zout.writestr(item, rels.encode('utf-8'))
                else:
                    zout.writestr(item, zin.read(item.filename))
            if sig_img_data:
                zout.writestr(f'word/media/signature.{sig_ext}', sig_img_data)

    return new_out


@app.route('/consultation/<int:consultation_id>/ordonnance/<type_ordo>/editer-collabora')
@login_required
def editer_ordonnance_collabora(consultation_id, type_ordo):
    """Génère une ordonnance et l'ouvre dans Collabora."""
    import os, shutil, uuid, urllib.parse
    c = Consultation.query.get_or_404(consultation_id)
    p = c.patient
    praticien = c.praticien
    cabinet   = c.cabinet
    pc = None
    if cabinet:
        pc = PraticienCabinet.query.filter_by(
            praticien_id=praticien.id, cabinet_id=cabinet.id).first()

    # Récupérer les données de la section ordonnance
    sec_ordo = next((s for s in c.sections if s.type == 'ordonnance'), None)
    d = sec_ordo.get_donnees() if sec_ordo else {}

    # Générer le contenu selon le type
    date_str   = c.date_consult.strftime('%d/%m/%Y')
    cab_commune = (cabinet.commune or 'Yssingeaux') if cabinet else 'Yssingeaux'
    esc = lambda s: (s or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

    if type_ordo == 'ortopad':
        oeil   = d.get('orto_oeil', '')
        heures = d.get('orto_heures', '')
        duree  = d.get('orto_duree', '')
        notes  = d.get('orto_notes', '')
        titre_ordo = 'ORDONNANCE — OCCLUSION'
        lignes_ordo = [
            f'Occlusion de l\'{oeil} par Ortopad / Opticlude',
            f'{heures} heures par jour' if heures else '',
            f'Durée : {duree}' if duree else '',
            notes if notes else '',
        ]
        nom_doc = f'{p.nom}_{p.prenom}_{c.date_consult.strftime("%Y%m%d")}_Ortopad.docx'

    elif type_ordo == 'prisme':
        od_d = d.get('prisme_od_diop', '')
        od_b = d.get('prisme_od_base', '')
        og_d = d.get('prisme_og_diop', '')
        og_b = d.get('prisme_og_base', '')
        titre_ordo = 'ORDONNANCE — PRISME PRESS-ON'
        lignes_ordo = []
        if od_d: lignes_ordo.append(f'OD : {od_d} dioptries, base {od_b}')
        if og_d: lignes_ordo.append(f'OG : {og_d} dioptries, base {og_b}')
        nom_doc = f'{p.nom}_{p.prenom}_{c.date_consult.strftime("%Y%m%d")}_Prisme.docx'

    elif type_ordo == 'ryser':
        od_n = d.get('ryser_od_num', '')
        od_a = d.get('ryser_od_av', '')
        og_n = d.get('ryser_og_num', '')
        og_a = d.get('ryser_og_av', '')
        titre_ordo = 'ORDONNANCE — FILTRE RYSER'
        lignes_ordo = []
        if od_n: lignes_ordo.append(f'OD : Ryser N°{od_n}, laissant une AV de {od_a}/10')
        if og_n: lignes_ordo.append(f'OG : Ryser N°{og_n}, laissant une AV de {og_a}/10')
        nom_doc = f'{p.nom}_{p.prenom}_{c.date_consult.strftime("%Y%m%d")}_Ryser.docx'
    else:
        return 'Type inconnu', 404

    # Générer le docx depuis entete.docx
    docx_path = _generer_ordonnance_docx(c, praticien, cabinet, pc,
                                          titre_ordo, lignes_ordo)

    # Copier dans le dossier WOPI
    wopi_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'wopi')
    os.makedirs(wopi_dir, exist_ok=True)
    permanent_path = os.path.join(wopi_dir, f"{uuid.uuid4().hex}.docx")
    shutil.copy2(docx_path, permanent_path)

    # Session WOPI avec section_ordre de la section ordonnance
    section_ordre = sec_ordo.ordre if sec_ordo else 0
    token = _wopi_token_for(consultation_id, 'ordonnance', permanent_path,
                            nom_doc, section_ordre=section_ordre)

    wopi_src     = f"{get_wopi_base_url()}/wopi/files/{token}"
    collabora_action_url = _get_collabora_url(nom_doc)
    editor_url = f"{collabora_action_url}WOPISrc={urllib.parse.quote(wopi_src, safe='')}&access_token={token}&darkTheme=false&ignoreSysTheme=1"
    editor_url = editor_url.replace('?&', '?').replace('&&', '&')

    return render_template('consultations/collabora_editor.html',
                           consultation=c,
                           editor_url=editor_url,
                           nom_fichier=nom_doc,
                           token=token,
                           section_type='ordonnance',
                           collabora_url=get_collabora_url())


@app.route('/consultation/<int:consultation_id>/document', methods=['GET', 'POST'])
@login_required
def generer_document(consultation_id):
    c = Consultation.query.get_or_404(consultation_id)
    modeles = DocumentModele.query.filter_by(actif=True)\
                                  .order_by(DocumentModele.type, DocumentModele.nom).all()
    sections, _ = get_sections()

    if request.method == 'POST':
        modele_id      = request.form.get('modele_id', type=int)
        sections_sel   = request.form.getlist('sections_incluses[]')
        images_ids     = [int(i) for i in request.form.getlist('images_incluses[]') if i.isdigit()]
        modele = DocumentModele.query.get_or_404(modele_id)
        docx_path = _generer_docx(c, modele, sections_sel, images_ids=images_ids)
        from flask import send_file
        nom_fichier = (f"{c.patient.nom}_{c.patient.prenom}_"
                       f"{c.date_consult.strftime('%Y%m%d')}_{modele.nom}.docx")
        return send_file(docx_path, as_attachment=True, download_name=nom_fichier,
                         mimetype='application/vnd.openxmlformats-officedocument'
                                  '.wordprocessingml.document')

    section_origine = request.args.get('section', '')
    # Pré-filtrer les modèles selon la section origine
    type_doc = None
    if section_origine == 'prescription':
        type_doc = 'ordonnance'
    elif section_origine == 'courrier':
        type_doc = 'courrier'
    modeles_filtres = [m for m in modeles if not type_doc or m.type == type_doc]

    return render_template('consultations/generer_document.html',
                           consultation=c, modeles=modeles_filtres,
                           sections_def=sections,
                           section_origine=section_origine,
                           type_doc=type_doc)


def _resoudre_variables(texte, consultation, praticien, cabinet, pc):
    p = consultation.patient
    age = ''
    if p.date_naissance:
        d = consultation.date_consult
        age = _age_str(p.date_naissance, d)
    replacements = {
        '{{patient.nom}}':      f'{p.nom} {p.prenom}',
        '{{patient.prenom}}':   p.prenom or '',
        '{{patient.nom_seul}}': p.nom or '',
        '{{patient.ddn}}':      p.date_naissance.strftime('%d/%m/%Y') if p.date_naissance else '',
        '{{patient.age}}':      age,
        '{{patient.medecin}}':  p.medecin_referent or '',
        '{{consultation.classe}}': consultation.classe_profession or '',
        '{{date}}':             consultation.date_consult.strftime('%d/%m/%Y'),
        '{{praticien.nom}}':    f'{praticien.prenom} {praticien.nom}',
        '{{praticien.titre}}':  praticien.titre or '',
        '{{praticien.rpps}}':   praticien.rpps or '',
        '{{praticien.adeli}}':  pc.adeli if pc else '',
        '{{praticien.forme}}':  pc.forme_juridique if pc else '',
        '{{cabinet.nom}}':      cabinet.nom if cabinet else '',
        '{{cabinet.adresse}}':  cabinet.adresse_complete if cabinet else '',
        '{{cabinet.tel}}':      cabinet.telephone if cabinet else '',
        '{{cabinet.email}}':    cabinet.email if cabinet else '',
        '{{cabinet.commune}}':  cabinet.commune if cabinet else '',
    }
    for k, v in replacements.items():
        texte = texte.replace(k, v or '')
    return texte


def _generer_ordonnance_docx(consultation, praticien, cabinet, pc, titre_ordo, lignes_ordo):
    """Génère un docx d'ordonnance simplifié depuis entete.docx."""
    import zipfile, re, tempfile, os
    p = consultation.patient
    esc = lambda s: (s or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

    entete_path = os.path.join(app.root_path, 'entete.docx')
    tmpdir = tempfile.mkdtemp()
    out_path = os.path.join(tmpdir, 'ordo_base.docx')

    with zipfile.ZipFile(entete_path, 'r') as z:
        z.extractall(tmpdir)
        doc_xml = z.read('word/document.xml').decode('utf-8')

    # Substitutions entête cabinet
    pc_data = pc or type('PC', (), {'adeli': '', 'rpps': '', 'forme_juridique': '', 'commune': ''})()
    cab_rue     = (cabinet.rue or '') if cabinet else ''
    cab_cp_comm = f"{(cabinet.code_postal or '')} {(cabinet.commune or '')}".strip() if cabinet else ''
    cab_commune = (cabinet.commune or 'Yssingeaux') if cabinet else 'Yssingeaux'
    cab_tel     = (cabinet.telephone or '') if cabinet else ''
    cab_email   = (cabinet.email or '') if cabinet else ''
    adeli       = (pc.adeli if pc else '') or ''
    forme       = (pc.forme_juridique if pc else '') or ''
    prat_nom    = f"{praticien.prenom} {praticien.nom}"
    prat_rpps   = praticien.rpps or ''
    prat_titre  = praticien.titre or 'Orthoptiste'
    date_str    = consultation.date_consult.strftime('%d/%m/%Y')

    def sub(xml, old, new):
        return xml.replace(old, esc(new)) if old in xml else xml

    doc_xml = sub(doc_xml, '130, Boulevard de la Paix', cab_rue)
    doc_xml = doc_xml.replace(
        '<w:p><w:pPr><w:pStyle w:val="Header"/><w:rPr><w:color w:themeColor="text1" w:val="000000"/></w:rPr></w:pPr><w:r><w:rPr><w:color w:themeColor="text1" w:val="000000"/></w:rPr><w:t>Résidence les jardinières</w:t></w:r></w:p>',
        '')
    doc_xml = sub(doc_xml, '43200 Yssingeaux', cab_cp_comm)
    doc_xml = sub(doc_xml, '04 71 59 01 38', cab_tel)
    doc_xml = sub(doc_xml, 'orthoptistes-yssingeaux@outlook.fr', cab_email)
    doc_xml = sub(doc_xml, 'ADELI\xa0: 439287145', f'ADELI : {adeli}' if adeli else '')
    doc_xml = sub(doc_xml, 'RPPS\xa0: 10010253291', f'RPPS : {prat_rpps}' if prat_rpps else '')
    doc_xml = sub(doc_xml, 'SELARL', forme)
    doc_xml = sub(doc_xml, ' Cyprien Nesme', f' {prat_nom}')
    doc_xml = sub(doc_xml, 'ORTHOPTISTE', prat_titre)
    doc_xml = sub(doc_xml, 'Prise de rendez-vous sur Doctolib', '')

    # Commune et date
    doc_xml = doc_xml.replace(
        f'A\xa0Yssingeaux, le </w:t></w:r><w:bookmarkEnd w:id="0"/>',
        f'A\xa0{esc(cab_commune)}, le {date_str}</w:t></w:r><w:bookmarkEnd w:id="0"/>'
    )

    # Patient — seulement Nom Prénom + DDN (pas âge, pas classe, pas médecin)
    pat_nom = f'{p.prenom} {p.nom}'
    pat_ddn = p.date_naissance.strftime('%d/%m/%Y') if p.date_naissance else ''

    # SDT Nom → Nom Prénom complet
    doc_xml = re.sub(
        r'<w:sdt><w:sdtPr><w:alias w:val="Nom"/>.*?<w:sdtContent>.*?</w:sdtContent></w:sdt>',
        f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="20"/></w:rPr><w:t>{esc(pat_nom)}</w:t></w:r>',
        doc_xml, flags=re.DOTALL)
    # SDT Prénom → vide (déjà dans Nom)
    doc_xml = re.sub(
        r'<w:sdt><w:sdtPr><w:alias w:val="Pr[eé]nom"/>.*?</w:sdt>',
        '',
        doc_xml, flags=re.DOTALL)

    # DDN — remplacer AVANT de supprimer les paragraphes
    doc_xml = doc_xml.replace(
        'DDN : </w:t></w:r>',
        f'DDN : {esc(pat_ddn)}</w:t></w:r>'
    )
    # Vider le SDT Commentaires placeholder qui suit DDN
    doc_xml = re.sub(
        r'(<w:sdt><w:sdtPr><w:alias w:val="Commentaires ".*?<w:sdtContent>)(.*?)(</w:sdtContent></w:sdt>)',
        lambda m: m.group(1) + '<w:r><w:t></w:t></w:r>' + m.group(3),
        doc_xml, flags=re.DOTALL
    )

    # Supprimer seulement les parties Âge et Classe dans le paragraphe (même para que DDN)
    # Supprimer : <w:tab/><w:t xml:space="preserve">Âge : </w:t><w:tab/><w:tab/><w:t xml:space="preserve">Classe : </w:t>
    doc_xml = re.sub(
        r'<w:tab/><w:t xml:space="preserve">Âge\s*:\s*</w:t>.*?<w:t xml:space="preserve">Classe\s*:\s*</w:t>',
        '',
        doc_xml, flags=re.DOTALL
    )
    # Supprimer les paragraphes médecin séparés
    doc_xml = re.sub(r'<w:p[^>]*>(?:(?!</w:p>).)*?[Mm]édecin(?:(?!</w:p>).)*?</w:p>', '', doc_xml, flags=re.DOTALL)

    # Remplacer le titre "BILAN ORTHOPTIQUE" — un seul run
    doc_xml = doc_xml.replace(
        '<w:t>BILAN ORTHOPTIQUE</w:t>',
        f'<w:t>{esc(titre_ordo)}</w:t>'
    )

    body_paras = []
    for ligne in lignes_ordo:
        if ligne:
            body_paras.append(
                f'<w:p><w:pPr><w:spacing w:after="120"/></w:pPr>'
                f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                f'<w:sz w:val="22"/></w:rPr>'
                f'<w:t>{esc(ligne)}</w:t></w:r></w:p>'
            )

    # Signature
    body_paras.append(
        f'<w:p><w:pPr><w:jc w:val="right"/><w:spacing w:before="720"/></w:pPr>'
        f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
        f'<w:sz w:val="20"/></w:rPr>'
        f'<w:t>{esc(prat_nom)}</w:t></w:r></w:p>'
    )

    doc_xml = doc_xml.replace('</w:body>', '\n'.join(body_paras) + '</w:body>')

    # Signature image
    sig_path = praticien.signature if praticien.signature else None
    sig_img_data = None
    sig_ext = None
    sig_rel_id = None
    if sig_path and os.path.exists(sig_path):
        with open(sig_path, 'rb') as sf:
            sig_img_data = sf.read()
        sig_ext = sig_path.rsplit('.', 1)[-1].lower()
        sig_rel_id = 'rIdSig1'
        try:
            from PIL import Image as PILImage
            import io as _io
            img = PILImage.open(_io.BytesIO(sig_img_data))
            img_w, img_h = img.size
            cx = 1800000
            cy = int(cx * img_h / img_w)
        except Exception:
            cx, cy = 1800000, 900000
        sig_para = (
            f'<w:p><w:pPr><w:jc w:val="right"/><w:spacing w:before="120"/></w:pPr>'
            f'<w:r><w:rPr/><w:drawing>'
            f'<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
            f'<wp:docPr id="1" name="signature"/>'
            f'<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="1" name="signature"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill>'
            f'<a:blip r:embed="{sig_rel_id}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
            f'<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            f'</pic:pic></a:graphicData></a:graphic>'
            f'</wp:inline></w:drawing></w:r></w:p>'
        )
        doc_xml = doc_xml.replace('</w:body>', sig_para + '</w:body>')

    # Écrire le docx final
    new_out = os.path.join(tmpdir, 'final_ordo.docx')
    with zipfile.ZipFile(entete_path, 'r') as zin:
        with zipfile.ZipFile(new_out, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == 'word/document.xml':
                    zout.writestr(item, doc_xml.encode('utf-8'))
                elif item.filename == 'word/_rels/document.xml.rels' and sig_img_data:
                    rels = zin.read(item.filename).decode('utf-8')
                    rels = rels.replace('</Relationships>',
                        f'<Relationship Id="{sig_rel_id}" '
                        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                        f'Target="media/signature.{sig_ext}"/></Relationships>')
                    zout.writestr(item, rels.encode('utf-8'))
                else:
                    zout.writestr(item, zin.read(item.filename))
            if sig_img_data:
                zout.writestr(f'word/media/signature.{sig_ext}', sig_img_data)

    return new_out


def _html_to_docx_paras(html_content, esc_fn):
    """Convertit le HTML riche de l'éditeur de blocs en paragraphes XML Word."""
    from html.parser import HTMLParser

    # Map taille font HTML (1-7) vers demi-points Word
    FONT_SIZE_MAP = {'1': 16, '2': 20, '3': 24, '4': 28, '5': 36, '6': 48, '7': 64}
    # Alignement
    ALIGN_MAP = {'left': 'left', 'center': 'center', 'right': 'right', 'justify': 'both'}

    class HtmlToDocx(HTMLParser):
        def __init__(self):
            super().__init__()
            self.paras = []
            self._runs = []
            self._bold = False
            self._italic = False
            self._underline = False
            self._size = 20
            self._align = 'left'
            self._indent = 0
            self._last_was_empty = False  # évite les paras vides consécutifs

        def _flush_para(self):
            indent_xml = f'<w:ind w:left="{self._indent}"/>' if self._indent else ''
            pPr = (f'<w:pPr><w:jc w:val="{self._align}"/>'
                   f'<w:spacing w:after="0"/>{indent_xml}</w:pPr>')
            if self._runs:
                runs_xml = ''
                for r in self._runs:
                    rPr = '<w:rPr>'
                    rPr += '<w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                    rPr += f'<w:sz w:val="{r["size"]}"/>'
                    if r['bold']: rPr += '<w:b/>'
                    if r['italic']: rPr += '<w:i/>'
                    if r['underline']: rPr += '<w:u w:val="single"/>'
                    rPr += '</w:rPr>'
                    text = esc_fn(r['text'])
                    space = ' xml:space="preserve"' if ' ' in r['text'] or r['text'].startswith(' ') or r['text'].endswith(' ') else ''
                    runs_xml += f'<w:r>{rPr}<w:t{space}>{text}</w:t></w:r>'
                self.paras.append(f'<w:p>{pPr}{runs_xml}</w:p>')
                self._last_was_empty = False
            self._runs = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            style = attrs.get('style', '')
            if tag == 'div':
                if self._runs:
                    self._flush_para()
                # Alignement
                for s in style.split(';'):
                    s = s.strip()
                    if s.startswith('text-align:'):
                        self._align = ALIGN_MAP.get(s.split(':')[1].strip(), 'left')
            elif tag == 'blockquote':
                self._flush_para()
                self._indent += 720
            elif tag == 'b': self._bold = True
            elif tag == 'i': self._italic = True
            elif tag == 'u': self._underline = True
            elif tag == 'font':
                sz = attrs.get('size', '')
                if sz in FONT_SIZE_MAP:
                    self._size = FONT_SIZE_MAP[sz]
            elif tag == 'br':
                pass  # géré via div vide

        def handle_endtag(self, tag):
            if tag == 'div':
                if self._runs:
                    self._flush_para()
                elif not self._last_was_empty:
                    # Para vide — saut de ligne
                    indent_xml = f'<w:ind w:left="{self._indent}"/>' if self._indent else ''
                    pPr = (f'<w:pPr><w:jc w:val="{self._align}"/>'
                           f'<w:spacing w:after="0"/>{indent_xml}</w:pPr>')
                    self.paras.append(f'<w:p>{pPr}</w:p>')
                    self._last_was_empty = True
                self._align = 'left'
            elif tag == 'blockquote':
                self._flush_para()
                self._indent = max(0, self._indent - 720)
            elif tag == 'b': self._bold = False
            elif tag == 'i': self._italic = False
            elif tag == 'u': self._underline = False
            elif tag == 'font': self._size = 20

        def handle_data(self, data):
            if data:
                self._runs.append({
                    'text': data,
                    'bold': self._bold,
                    'italic': self._italic,
                    'underline': self._underline,
                    'size': self._size,
                })

        def handle_entityref(self, name):
            if name == 'nbsp': self._runs.append({'text': '\u00a0', 'bold': self._bold,
                'italic': self._italic, 'underline': self._underline, 'size': self._size})

        def get_paras(self):
            if self._runs:
                self._flush_para()
            return self.paras if self.paras else [
                f'<w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p>'
            ]
            if self._runs:
                self._flush_para()
            return self.paras if self.paras else [
                f'<w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>'
            ]

    parser = HtmlToDocx()
    parser.feed(html_content)
    return parser.get_paras()


def _generer_docx(consultation, modele, sections_incluses, images_ids=None, sections_par_bloc=None):
    """
    Génère un .docx en clonant l'en-tête depuis le template fourni
    et en ajoutant le corps via Node.js/docx.
    Retourne le chemin du fichier généré.
    """
    import tempfile, os, shutil, zipfile, re

    praticien = consultation.praticien
    cabinet   = consultation.cabinet
    pc = None
    if cabinet:
        pc = PraticienCabinet.query.filter_by(
            praticien_id=praticien.id, cabinet_id=cabinet.id).first()

    p = consultation.patient

    # ── Préparer les valeurs de substitution ──────────────────────────
    adeli = (pc.adeli if pc else '') or ''
    forme = (pc.forme_juridique if pc else '') or ''

    # Lignes adresse cabinet
    cab_rue     = (cabinet.rue         if cabinet else '') or ''
    cab_cp_comm = ''
    if cabinet:
        if cabinet.code_postal and cabinet.commune:
            cab_cp_comm = f'{cabinet.code_postal} {cabinet.commune}'
        elif cabinet.commune:
            cab_cp_comm = cabinet.commune
    cab_tel     = (cabinet.telephone   if cabinet else '') or ''
    cab_email   = (cabinet.email       if cabinet else '') or ''
    cab_commune = (cabinet.commune     if cabinet else '') or ''

    prat_nom    = f'{praticien.prenom} {praticien.nom}'
    prat_titre  = (praticien.titre or 'Orthoptiste').upper()
    prat_rpps   = praticien.rpps or ''

    pat_nom     = f'{p.nom} {p.prenom}'
    pat_ddn     = p.date_naissance.strftime('%d/%m/%Y') if p.date_naissance else ''
    pat_medecin = consultation.medecin_prescripteur or p.medecin_referent or ''
    date_str    = consultation.date_consult.strftime('%d/%m/%Y')

    type_label  = 'ORDONNANCE' if modele.type == 'ordonnance' else 'COURRIER'
    motif       = consultation.motif or ''
    # Titre du document : motif du bilan ou type par défaut
    titre_doc = motif.upper() if motif else type_label
    # Fonction d'échappement XML (définie tôt pour usage dans les remplacements)
    def esc_early(s):
        return (s or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

    # ── Charger le template docx ──────────────────────────────────────
    template_path = os.path.join(os.path.dirname(__file__), 'entete.docx')
    if not os.path.exists(template_path):
        raise FileNotFoundError(
            "Fichier entete.docx introuvable. "
            "Placez-le dans le même dossier que app.py.")

    tmpdir  = tempfile.mkdtemp()
    out_path = os.path.join(tmpdir, 'document.docx')
    shutil.copy2(template_path, out_path)

    # ── Lire le document.xml du template ─────────────────────────────
    with zipfile.ZipFile(out_path, 'r') as z:
        doc_xml = z.read('word/document.xml').decode('utf-8')
        all_files = {name: z.read(name) for name in z.namelist()}

    # ── Substitutions dans l'en-tête ─────────────────────────────────
    def sub(xml, old, new):
        """Remplace old par new dans le XML, en gérant l'espace insécable."""
        new_escaped = new.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return xml.replace(old, new_escaped)

    # Zone adresse (Rectangle 1)
    doc_xml = sub(doc_xml, '130, Boulevard de la Paix', cab_rue)
    # Supprimer le paragraphe "Résidence les jardinières" — deux formats possibles
    doc_xml = doc_xml.replace(
        '<w:p w14:paraId="28548EE2" w14:textId="77777777" w:rsidR="00E16D2D" w:rsidRPr="00204C39" w:rsidRDefault="00E16D2D" w:rsidP="00095667"><w:pPr><w:pStyle w:val="En-tte"/><w:rPr><w:color w:val="000000" w:themeColor="text1"/></w:rPr></w:pPr><w:r w:rsidRPr="00204C39"><w:rPr><w:color w:val="000000" w:themeColor="text1"/></w:rPr><w:t>Résidence les jardinières</w:t></w:r></w:p>',
        '')
    # Format YunoHost (sans paraId, style Header)
    doc_xml = doc_xml.replace(
        '<w:p><w:pPr><w:pStyle w:val="Header"/><w:rPr><w:color w:themeColor="text1" w:val="000000"/></w:rPr></w:pPr><w:r><w:rPr><w:color w:themeColor="text1" w:val="000000"/></w:rPr><w:t>Résidence les jardinières</w:t></w:r></w:p>',
        '')
    doc_xml = sub(doc_xml, '43200 Yssingeaux', cab_cp_comm)
    doc_xml = sub(doc_xml, '04 71 59 01 38', cab_tel)
    doc_xml = sub(doc_xml, 'orthoptistes-yssingeaux@outlook.fr', cab_email)

    # Zone ADELI/RPPS
    doc_xml = sub(doc_xml, 'ADELI\xa0: 439287145', f'ADELI : {adeli}' if adeli else '')
    doc_xml = sub(doc_xml, 'RPPS\xa0: 10010253291', f'RPPS : {prat_rpps}' if prat_rpps else '')

    # Zone praticien
    doc_xml = sub(doc_xml, 'SELARL', forme)
    doc_xml = sub(doc_xml, ' Cyprien Nesme', f' {prat_nom}')
    doc_xml = sub(doc_xml, 'ORTHOPTISTE', prat_titre)

    # Titre du document dans le cadre bleu
    doc_xml = doc_xml.replace('<w:t>BILAN ORTHOPTIQUE</w:t>',
                              f'<w:t>{esc_early(titre_doc)}</w:t>')
    # Fallback ancien format en 2 runs
    doc_xml = doc_xml.replace('BILAN ORTHOPTIQU</w:t></w:r><w:r><w:rPr><w:b/><w:bCs/><w:sz w:val="32"/><w:szCs w:val="32"/><w:lang w:val="it-IT"/></w:rPr><w:t>E',
                              esc_early(titre_doc) + '</w:t></w:r><w:r><w:rPr><w:b/><w:bCs/><w:sz w:val="32"/><w:szCs w:val="32"/><w:lang w:val="it-IT"/></w:rPr><w:t>')

    # Patient (SDT Nom)
    doc_xml = re.sub(
        r'(<w:sdt>.*?<w:alias w:val="Nom".*?<w:sdtContent>)(.*?)(</w:sdtContent></w:sdt>)',
        lambda m: m.group(1) + f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="20"/></w:rPr><w:t>{pat_nom}</w:t></w:r>' + m.group(3),
        doc_xml, flags=re.DOTALL
    )
    # Prénom (SDT Prénom) - on l'efface car nom complet déjà dans Nom
    doc_xml = re.sub(
        r'(<w:sdt>.*?<w:alias w:val="Prénom".*?<w:sdtContent>)(.*?)(</w:sdtContent></w:sdt>)',
        lambda m: m.group(1) + '<w:r><w:t></w:t></w:r>' + m.group(3),
        doc_xml, flags=re.DOTALL
    )
    # DDN
    doc_xml = re.sub(
        r'(<w:sdt>.*?<w:alias w:val="Commentaires.*?<w:sdtContent>)(.*?)(</w:sdtContent></w:sdt>)',
        lambda m: m.group(1) + f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/><w:sz w:val="20"/></w:rPr><w:t>{pat_ddn}</w:t></w:r>' + m.group(3),
        doc_xml, flags=re.DOTALL
    )

    # Calcul de l'âge à la date du bilan
    age_str = ''
    if p.date_naissance:
        d = consultation.date_consult
        age_str = _age_str(p.date_naissance, d)

    # Médecin prescripteur — depuis le champ de la consultation ou du patient
    medecin_str = pat_medecin  # déjà défini = p.medecin_referent
    esc = lambda s: s.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    doc_xml = doc_xml.replace(
        'Médecin prescripteur : Dr </w:t>',
        f'Médecin prescripteur : {esc(medecin_str)}</w:t>'
    )

    # Classe / profession
    classe_str = (consultation.classe_profession or '')
    doc_xml = doc_xml.replace(
        'Classe : </w:t></w:r></w:p>',
        f'Classe : {esc(classe_str)}</w:t></w:r></w:p>'
    )
    # Fallback avec tab
    doc_xml = doc_xml.replace(
        'Classe : </w:t>',
        f'Classe : {esc(classe_str)}</w:t>'
    )

    # Âge — pattern adapté au template YunoHost
    doc_xml = doc_xml.replace(
        'Âge : </w:t><w:tab/><w:tab/>',
        f'Âge : {esc(age_str)}</w:t><w:tab/><w:tab/>'
    )

    # Commune et date — pattern adapté
    # "A Yssingeaux, le " → "A [commune], le [date]"
    doc_xml = doc_xml.replace(
        f'A\xa0Yssingeaux, le </w:t></w:r><w:bookmarkEnd w:id="0"/>',
        f'A\xa0{esc(cab_commune)}, le {date_str}</w:t></w:r><w:bookmarkEnd w:id="0"/>'
    )
    # Fallback ancienne version
    doc_xml = doc_xml.replace('>Yssingeaux<', f'>{cab_commune.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")}<')
    doc_xml = doc_xml.replace(
        '<w:t xml:space="preserve">le </w:t></w:r><w:bookmarkEnd w:id="0"/>',
        f'<w:t xml:space="preserve">le {date_str}</w:t></w:r><w:bookmarkEnd w:id="0"/>'
    )

    # ── Construire le corps du document ──────────────────────────────
    sections_db, _ = get_sections()
    sections_data = []
    # Collecter toutes les sections nécessaires (filtre par bloc fait plus tard)
    _all_sections_types = set(sections_incluses)
    if sections_par_bloc:
        for _secs in sections_par_bloc.values():
            _all_sections_types.update(_secs)

    def fmt_sph(v):
        """Formate une valeur sphère/cylindre avec signe et 2 décimales."""
        if not v: return ''
        try:
            f = float(str(v).replace(',', '.'))
            return f'+{f:.2f}' if f >= 0 else f'{f:.2f}'
        except ValueError:
            return v

    def fmt_refraction(d, prefix):
        """Construit la chaîne sph(cyl)axe° pour un œil."""
        sph = fmt_sph(d.get(f'{prefix}_sph', ''))
        cyl = fmt_sph(d.get(f'{prefix}_cyl', ''))
        axe = d.get(f'{prefix}_axe', '')
        parts = sph
        if cyl: parts += f'({cyl})'
        if axe:  parts += f'{axe}°'
        return parts

    for sec in consultation.sections:
        if sec.type not in _all_sections_types:
            continue
        d = sec.get_donnees()
        lignes = []

        if sec.type in ('refraction_obj', 'correction_portee', 'frontofocometrie'):
            od = fmt_refraction(d, 'od')
            og = fmt_refraction(d, 'og')
            od_add = fmt_sph(d.get('od_add', ''))
            og_add = fmt_sph(d.get('og_add', ''))
            od_line = od
            og_line = og
            if od_add: od_line += f'   Add {od_add}'
            if og_add: og_line += f'   Add {og_add}'
            if od_line or og_line:
                lignes.append(('', f'OD : {od_line}  /  OG : {og_line}'))
            if sec.type in ('correction_portee', 'frontofocometrie'):
                prisme_od = d.get('prisme_od', '')
                prisme_og = d.get('prisme_og', '')
                if prisme_od or prisme_og:
                    lignes.append(('Prisme', f'OD : {prisme_od or "—"}  /  OG : {prisme_og or "—"}'))

        elif sec.type == 'refraction_subj':
            # Format OD : sph(cyl)axe° = AV loin   Add = AV près
            # puis OG : sph(cyl)axe° = AV loin   Add = AV près
            od_ref = fmt_refraction(d, 'od')
            og_ref = fmt_refraction(d, 'og')
            od_add  = fmt_sph(d.get('od_add', ''))
            og_add  = fmt_sph(d.get('og_add', ''))
            od_loin = d.get('od_av_loin', '')
            od_pres = d.get('od_av_pres', '')
            og_loin = d.get('og_av_loin', '')
            og_pres = d.get('og_av_pres', '')
            od_line = f'OD : {od_ref}'
            if od_loin: od_line += f' = {od_loin}'
            if od_add:  od_line += f'   Add {od_add}'
            if od_pres: od_line += f' = {od_pres}'
            og_line = f'OG : {og_ref}'
            if og_loin: og_line += f' = {og_loin}'
            if og_add:  og_line += f'   Add {og_add}'
            if og_pres: og_line += f' = {og_pres}'
            if od_line.strip() != 'OD :': lignes.append(('', od_line))
            if og_line.strip() != 'OG :': lignes.append(('', og_line))

        elif sec.type == 'acuite':
            # Même format que l'historique patient
            corr   = d.get('av_correction', '')
            bino   = d.get('av_bino', '')
            od_l   = d.get('av_od_loin', '')
            od_p   = d.get('av_od_pres', '')
            og_l   = d.get('av_og_loin', '')
            og_p   = d.get('av_og_pres', '')
            if corr:  lignes.append(('Correction', corr))
            if bino:  lignes.append(('Binoculaire', bino))
            od_str = ''
            if od_l: od_str += f'loin : {od_l}'
            if od_p: od_str += f'  près : {od_p}'
            if od_str: lignes.append(('OD', od_str.strip()))
            og_str = ''
            if og_l: og_str += f'loin : {og_l}'
            if og_p: og_str += f'  près : {og_p}'
            if og_str: lignes.append(('OG', og_str.strip()))

        elif sec.type == 'ordonnance_lunettes':
            def fmt_lun(sph, cyl, axe):
                if not sph: return ''
                r = fmt_sph(sph)
                if cyl: r += f'({fmt_sph(cyl)})'
                if axe: r += f'{axe}°'
                return r
            od_vl = fmt_lun(d.get('lun_vl_od_sph',''), d.get('lun_vl_od_cyl',''), d.get('lun_vl_od_axe',''))
            og_vl = fmt_lun(d.get('lun_vl_og_sph',''), d.get('lun_vl_og_cyl',''), d.get('lun_vl_og_axe',''))
            od_add = fmt_sph(d.get('lun_vp_od_add',''))
            og_add = fmt_sph(d.get('lun_vp_og_add',''))
            dip = d.get('lun_dip','')
            renouv = d.get('lun_renouvelable','')
            if od_vl:
                od_line = f'OD : {od_vl}'
                if od_add: od_line += f'   Add {od_add}'
                lignes.append(('', od_line))
            if og_vl:
                og_line = f'OG : {og_vl}'
                if og_add: og_line += f'   Add {og_add}'
                lignes.append(('', og_line))
            if dip: lignes.append(('DIP', f'{dip} mm'))
            if renouv == 'oui': lignes.append(('', 'Renouvelable 2 ans'))
            elif renouv: lignes.append(('', 'Non renouvelable'))

        else:
            # Cas général — on récupère aussi nb_colonnes et les types
            sec_def  = sections_db.get(sec.type, {})
            champs   = sec_def.get('champs', [])
            nb_cols  = sec_def.get('nb_colonnes', 2) or 2
            lignes   = []
            # Collecter les cellules non-mise-en-page
            pending_cells = []  # liste de (label, valeur) à regrouper

            def flush_pending():
                """Regroupe les cellules en rangées pleines, sans trous."""
                if not pending_cells:
                    return
                # Filtrer les cellules vides
                non_vides = [(lbl, val) for lbl, val in pending_cells if val and str(val).strip()]
                if not non_vides:
                    pending_cells.clear()
                    return
                # Regrouper en rangées de nb_cols
                for i in range(0, len(non_vides), nb_cols):
                    chunk = non_vides[i:i+nb_cols]
                    # Compléter la dernière rangée si nécessaire
                    while len(chunk) < nb_cols:
                        chunk.append(('', ''))
                    lignes.append(('__row__', chunk))
                pending_cells.clear()

            for ch in champs:
                if ch['type'] == 'fichier':
                    continue
                if ch['type'] == 'spacer':
                    # Spacer = cellule vide intentionnelle — on la garde
                    pending_cells.append(('', ''))
                    if len(pending_cells) >= nb_cols:
                        flush_pending()
                    continue
                if ch['type'] == 'separator':
                    flush_pending()
                    lignes.append(('__sep__', ch['label'] or ''))
                    continue
                if ch['type'] == 'subtitle':
                    flush_pending()
                    lignes.append(('__subtitle__', ch['label'] or ''))
                    continue
                val = d.get(ch['name'], '')
                pending_cells.append((ch['label'], str(val) if val else ''))

            flush_pending()

        sections_data.append({
            'type': sec.type,
            'label': sec.label,
            'observations': sec.observations or '',
            'lignes': lignes,
            'ordre': sec.ordre,
        })

    blocs_resolus = []
    for bloc in modele.blocs:
        if bloc.type == 'texte':
            blocs_resolus.append({
                'type': 'texte',
                'contenu': _resoudre_variables(bloc.contenu, consultation, praticien, cabinet, pc)
            })
        elif bloc.type == 'section_bilan':
            # Sections pour ce bloc spécifique
            if sections_par_bloc and bloc.id in sections_par_bloc:
                bloc_sections_types = sections_par_bloc[bloc.id]
            else:
                bloc_sections_types = sections_incluses

            # Filtrer sections_data selon les types sélectionnés pour ce bloc
            bloc_sections_data = [s for s in sections_data if s['type'] in bloc_sections_types]
            if bloc_sections_data:
                blocs_resolus.append({'type': 'sections', 'sections': bloc_sections_data,
                                      'label': bloc.label or ''})

    # ── Générer le XML des paragraphes du corps ───────────────────────
    def esc(t):
        return t.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

    # Préparer le dict images par section_ordre
    images_by_ordre = {}
    img_rels_to_add = []
    img_files_to_add = []
    img_rel_counter = [1]

    def _make_img_para(fid):
        """Génère le XML d'une image et enregistre la relation."""
        fic = FichierSection.query.get(fid)
        if not fic: return None
        img_path = os.path.join(
            app.config['UPLOAD_FOLDER'], 'sections',
            str(fic.consultation_id), fic.nom_stocke)
        if not os.path.exists(img_path): return None
        try:
            from PIL import Image as PILImage
            with PILImage.open(img_path) as im:
                w_px, h_px = im.size
            # Max 14cm large, 18cm haut
            max_cx = 5040000  # 14cm en EMU
            max_cy = 6480000  # 18cm en EMU
            cx = min(max_cx, int(w_px * 9525))
            cy = int(cx * h_px / w_px)
            if cy > max_cy:
                cy = max_cy
                cx = int(cy * w_px / h_px)
            rel_id  = f'rIdImg{img_rel_counter[0]}'
            draw_id = img_rel_counter[0]
            img_rel_counter[0] += 1
            ext  = fic.nom_stocke.rsplit('.', 1)[-1].lower()
            mime = {'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg',
                    'gif':'image/gif','webp':'image/webp'}.get(ext,'image/png')
            img_name = f'img_{fid}.{ext}'
            with open(img_path, 'rb') as f:
                img_data = f.read()
            img_rels_to_add.append(
                f'<Relationship Id="{rel_id}" '
                f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                f'Target="media/{img_name}"/>'
            )
            img_files_to_add.append((f'word/media/{img_name}', img_data))
            titre = fic.titre or fic.nom_original
            return (
                f'<w:p><w:pPr><w:jc w:val="left"/><w:spacing w:before="60" w:after="40"/></w:pPr>'
                f'<w:r><w:rPr><w:noProof/></w:rPr><w:drawing>'
                f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
                f'<wp:extent cx="{cx}" cy="{cy}"/>'
                f'<wp:effectExtent l="0" t="0" r="0" b="0"/>'
                f'<wp:docPr id="{draw_id}" name="{esc(titre)}"/>'
                f'<wp:cNvGraphicFramePr/>'
                f'<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                f'<pic:nvPicPr>'
                f'<pic:cNvPr id="{draw_id}" name="{esc(titre)}"/>'
                f'<pic:cNvPicPr/>'
                f'</pic:nvPicPr>'
                f'<pic:blipFill>'
                f'<a:blip r:embed="{rel_id}"/>'
                f'<a:stretch><a:fillRect/></a:stretch>'
                f'</pic:blipFill>'
                f'<pic:spPr>'
                f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
                f'</pic:spPr>'
                f'</pic:pic></a:graphicData></a:graphic>'
                f'</wp:inline></w:drawing></w:r></w:p>'
            )
        except Exception as e:
            app.logger.warning(f'Image {fid} non insérée: {e}')
            return None

    if images_ids:
        # Indexer les images par ordre de section dans CETTE consultation
        # On cherche quelle section a le même ordre que section_ordre du fichier
        # Si pas trouvé, on essaie de matcher par type via la section du bilan
        for fid in images_ids:
            fic = FichierSection.query.get(fid)
            if not fic: continue
            # Chercher la section correspondante dans cette consultation
            sec_match = next(
                (s for s in consultation.sections if s.ordre == fic.section_ordre),
                None
            )
            if sec_match:
                images_by_ordre.setdefault(sec_match.ordre, []).append(fid)
            else:
                # L'ordre a changé - chercher par champ_name ou mettre en fin de doc
                # Mettre l'image à la fin (ordre -1)
                images_by_ordre.setdefault(-1, []).append(fid)

    body_paras = []
    for bloc in blocs_resolus:
        if bloc['type'] == 'texte' and bloc['contenu']:
            # Ligne de séparation après les sections
            if body_paras and bloc != blocs_resolus[0]:
                body_paras.append('<w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p>')
            body_paras.extend(_html_to_docx_paras(bloc['contenu'], esc))
        elif bloc['type'] == 'sections':
            for sec in bloc['sections']:
                # Titre section
                body_paras.append(
                    f'<w:p><w:pPr><w:spacing w:before="240" w:after="80"/>'
                    f'<w:pBdr><w:bottom w:val="single" w:sz="2" w:space="1" w:color="CCCCCC"/></w:pBdr>'
                    f'</w:pPr>'
                    f'<w:r><w:rPr><w:b/><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                    f'<w:color w:val="2E7D6B"/><w:sz w:val="20"/></w:rPr>'
                    f'<w:t>{esc(sec["label"])}</w:t></w:r></w:p>'
                )
                if sec['observations']:
                    for obs_line in sec['observations'].split('\n'):
                        if not obs_line.strip():
                            continue
                        body_paras.append(
                            f'<w:p><w:pPr><w:spacing w:after="40"/></w:pPr>'
                            f'<w:r><w:rPr>'
                            f'<w:i/><w:iCs/>'
                            f'<w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                            f'<w:sz w:val="16"/><w:szCs w:val="16"/>'
                            f'<w:color w:val="555555"/>'
                            f'</w:rPr><w:t xml:space="preserve">{esc(obs_line)}</w:t></w:r>'
                            f'</w:p>'
                        )
                for label, valeur in sec['lignes']:
                    if label == '__sep__':
                        # Séparateur — ligne grise avec titre optionnel
                        body_paras.append(
                            f'<w:p><w:pPr><w:spacing w:before="120" w:after="40"/>'
                            f'<w:pBdr><w:bottom w:val="single" w:sz="2" w:space="1" w:color="DDDDDD"/></w:pBdr>'
                            f'</w:pPr>'
                            + (f'<w:r><w:rPr><w:b/><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                               f'<w:color w:val="888888"/><w:sz w:val="16"/></w:rPr>'
                               f'<w:t>{esc(valeur)}</w:t></w:r>' if valeur else '')
                            + '</w:p>'
                        )
                    elif label == '__subtitle__':
                        # Sous-titre — texte gras sans ligne
                        body_paras.append(
                            f'<w:p><w:pPr><w:spacing w:before="120" w:after="40"/></w:pPr>'
                            f'<w:r><w:rPr><w:b/><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                            f'<w:sz w:val="18"/><w:color w:val="333333"/></w:rPr>'
                            f'<w:t>{esc(valeur)}</w:t></w:r></w:p>'
                        )
                    elif label == '__row__':
                        # Rangée de cellules — tableau Word
                        cells = valeur  # liste de (label, val)
                        nb    = len(cells)
                        col_w = max(1, 9000 // nb)  # largeur en twentieths of a point

                        def cell_xml(lbl, val):
                            content = ''
                            if lbl and val:
                                content = (
                                    f'<w:r><w:rPr><w:b/><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                                    f'<w:sz w:val="18"/></w:rPr><w:t xml:space="preserve">{esc(lbl)} : </w:t></w:r>'
                                    + _md_runs(str(val), size=18)
                                )
                            elif val:
                                content = _md_runs(str(val), size=18)
                            return (
                                f'<w:tc>'
                                f'<w:tcPr><w:tcW w:w="{col_w}" w:type="dxa"/>'
                                f'<w:tcBorders><w:top w:val="none"/><w:left w:val="none"/>'
                                f'<w:bottom w:val="none"/><w:right w:val="none"/></w:tcBorders>'
                                f'</w:tcPr>'
                                f'<w:p><w:pPr><w:spacing w:after="40"/></w:pPr>{content}</w:p>'
                                f'</w:tc>'
                            )

                        row_xml = ''.join(cell_xml(lbl, val) for lbl, val in cells)
                        # Générer uniquement si au moins une cellule non vide
                        if any(val for _, val in cells):
                            body_paras.append(
                                f'<w:tbl>'
                                f'<w:tblPr><w:tblW w:w="9000" w:type="dxa"/>'
                                f'<w:tblBorders>'
                                f'<w:top w:val="none"/><w:left w:val="none"/>'
                                f'<w:bottom w:val="none"/><w:right w:val="none"/>'
                                f'<w:insideH w:val="none"/><w:insideV w:val="none"/>'
                                f'</w:tblBorders></w:tblPr>'
                                f'<w:tr>{row_xml}</w:tr>'
                                f'</w:tbl>'
                            )
                    else:
                        valeur_lines = str(valeur).split('\n') if valeur else ['']
                        for i, vline in enumerate(valeur_lines):
                            if i == 0:
                                body_paras.append(
                                    f'<w:p><w:pPr><w:spacing w:after="60"/></w:pPr>'
                                    f'<w:r><w:rPr><w:b/><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
                                    f'<w:sz w:val="20"/></w:rPr><w:t xml:space="preserve">{esc(label) + " : " if label.strip() else ""}</w:t></w:r>'
                                    + _md_runs(vline) + '</w:p>'
                                )
                            else:
                                body_paras.append(
                                    f'<w:p><w:pPr><w:spacing w:after="60"/></w:pPr>'
                                    + _md_runs(vline) + '</w:p>'
                                )

                # Insérer les images liées à cette section
                sec_ordre = sec.get('ordre')
                if sec_ordre is not None and sec_ordre in images_by_ordre:
                    for fid in images_by_ordre[sec_ordre]:
                        img_para = _make_img_para(fid)
                        if img_para:
                            body_paras.append(img_para)

    # Signature — prénom nom uniquement (sans titre)
    body_paras.append(
        f'<w:p><w:pPr><w:jc w:val="right"/><w:spacing w:before="720"/></w:pPr>'
        f'<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
        f'<w:sz w:val="20"/></w:rPr>'
        f'<w:t>{esc(prat_nom)}</w:t></w:r></w:p>'
    )

    # ── Insérer le corps avant </w:body> ─────────────────────────────
    # Ajouter les images orphelines (section_ordre ne correspond plus)
    if -1 in images_by_ordre:
        for fid in images_by_ordre[-1]:
            img_para = _make_img_para(fid)
            if img_para:
                body_paras.append(img_para)

    body_xml = '\n'.join(body_paras)
    doc_xml = doc_xml.replace('</w:body>', body_xml + '</w:body>')

    # ── Signature praticien ───────────────────────────────────────────
    sig_path = praticien.signature if praticien.signature else None
    sig_rel_id = None
    sig_img_data = None
    sig_ext = None
    if sig_path and os.path.exists(sig_path):
        with open(sig_path, 'rb') as sf:
            sig_img_data = sf.read()
        sig_ext = sig_path.rsplit('.', 1)[-1].lower()
        sig_rel_id = 'rIdSig1'
        # Calculer les dimensions en conservant le ratio - max 5cm de large
        try:
            from PIL import Image as PILImage
            import io as _io
            img = PILImage.open(_io.BytesIO(sig_img_data))
            img_w, img_h = img.size
            max_cx = 1800000  # 5cm en EMU
            cx = max_cx
            cy = int(max_cx * img_h / img_w)
        except Exception:
            cx = 1800000
            cy = 900000
        sig_para = (
            f'<w:p><w:pPr><w:jc w:val="right"/><w:spacing w:before="120"/></w:pPr>'
            f'<w:r><w:rPr/><w:drawing>'
            f'<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
            f'<wp:extent cx="{cx}" cy="{cy}"/>'
            f'<wp:effectExtent l="0" t="0" r="0" b="0"/>'
            f'<wp:docPr id="1" name="signature"/>'
            f'<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="1" name="signature"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill>'
            f'<a:blip r:embed="{sig_rel_id}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
            f'<a:stretch><a:fillRect/></a:stretch>'
            f'</pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            f'</pic:pic></a:graphicData></a:graphic>'
            f'</wp:inline></w:drawing></w:r></w:p>'
        )
        doc_xml = doc_xml.replace('</w:body>', sig_para + '</w:body>')

    # ── Pied de page impair : flèche ▶ bas droite (recto-verso) ─────
    footer_odd_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:p><w:pPr><w:jc w:val="right"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/>'
        '<w:sz w:val="24"/><w:color w:val="CCCCCC"/></w:rPr>'
        '<w:t>&#x25BA;</w:t></w:r>'
        '</w:p>'
        '</w:ftr>'
    )
    # Footer vide pour pages paires et défaut
    footer_empty_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:p><w:pPr><w:jc w:val="right"/></w:pPr></w:p>'
        '</w:ftr>'
    )
    footer1_xml = footer_odd_xml  # gardé pour compatibilité avec le code zip
    # Référencer les footers dans le sectPr principal (dernier du document)
    footer_odd_ref     = '<w:footerReference w:type="odd" r:id="rIdFooter1"/>'
    footer_default_ref = '<w:footerReference w:type="default" r:id="rIdFooter2"/>'
    footer_even_ref    = '<w:footerReference w:type="even" r:id="rIdFooter3"/>'
    if footer_odd_ref not in doc_xml:
        last_sectp = doc_xml.rfind('</w:sectPr>')
        if last_sectp >= 0:
            insert = footer_odd_ref + footer_default_ref + footer_even_ref
            doc_xml = doc_xml[:last_sectp] + insert + doc_xml[last_sectp:]
        else:
            doc_xml = doc_xml.replace('</w:body>',
                f'<w:sectPr>{footer_odd_ref}{footer_default_ref}{footer_even_ref}</w:sectPr></w:body>')

    # Corriger la marge footer=0 qui empêche l'affichage
    doc_xml = doc_xml.replace('w:footer="0"', 'w:footer="567"')

    # ── Réécrire le docx ──────────────────────────────────────────────
    new_out = os.path.join(tmpdir, 'final.docx')
    mime_map = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                'gif': 'image/gif', 'webp': 'image/webp'}
    with zipfile.ZipFile(out_path, 'r') as zin:
        with zipfile.ZipFile(new_out, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                fname = item.filename
                if fname == 'word/document.xml':
                    zout.writestr(item, doc_xml.encode('utf-8'))
                elif fname == 'word/settings.xml':
                    s = zin.read(fname).decode('utf-8')
                    if 'evenAndOddHeaders' not in s:
                        s = s.replace('</w:settings>', '<w:evenAndOddHeaders/></w:settings>')
                    zout.writestr(item, s.encode('utf-8'))
                elif fname == 'word/_rels/document.xml.rels':
                    rels = zin.read(fname).decode('utf-8')
                    if sig_img_data and sig_rel_id and sig_rel_id not in rels:
                        rels = rels.replace(
                            '</Relationships>',
                            f'<Relationship Id="{sig_rel_id}" '
                            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                            f'Target="media/signature.{sig_ext}"/>'
                            '</Relationships>'
                        )
                    if 'rIdFooter1' not in rels:
                        rels = rels.replace(
                            '</Relationships>',
                            '<Relationship Id="rIdFooter1" '
                            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" '
                            'Target="footer1.xml"/>'
                            '<Relationship Id="rIdFooter2" '
                            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" '
                            'Target="footer2.xml"/>'
                            '<Relationship Id="rIdFooter3" '
                            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" '
                            'Target="footer3.xml"/>'
                            '</Relationships>'
                        )
                    # Ajouter les relations images
                    for img_rel in img_rels_to_add:
                        if img_rel.split('Id="')[1].split('"')[0] not in rels:
                            rels = rels.replace('</Relationships>', img_rel + '</Relationships>')
                    zout.writestr(item, rels.encode('utf-8'))
                elif fname == '[Content_Types].xml':
                    ct = zin.read(fname).decode('utf-8')
                    if 'footer1.xml' not in ct:
                        ct = ct.replace(
                            '</Types>',
                            '<Override PartName="/word/footer1.xml" '
                            'ContentType="application/vnd.openxmlformats-officedocument'
                            '.wordprocessingml.footer+xml"/>'
                            '<Override PartName="/word/footer2.xml" '
                            'ContentType="application/vnd.openxmlformats-officedocument'
                            '.wordprocessingml.footer+xml"/>'
                            '<Override PartName="/word/footer3.xml" '
                            'ContentType="application/vnd.openxmlformats-officedocument'
                            '.wordprocessingml.footer+xml"/>'
                            '</Types>'
                        )
                    zout.writestr(item, ct.encode('utf-8'))
                else:
                    zout.writestr(item, zin.read(fname))
            # Ajouter les fichiers footer
            zout.writestr('word/footer1.xml', footer_odd_xml.encode('utf-8'))   # impair : flèche
            zout.writestr('word/footer2.xml', footer_empty_xml.encode('utf-8')) # défaut : vide
            zout.writestr('word/footer3.xml', footer_empty_xml.encode('utf-8')) # pair : vide
            # Ajouter les images sélectionnées
            for img_fname, img_data in img_files_to_add:
                zout.writestr(img_fname, img_data)
            # Ajouter signature
            if sig_img_data:
                zout.writestr(f'word/media/signature.{sig_ext}', sig_img_data)

    return new_out


def _build_docx_js(consultation, praticien, cabinet, pc, blocs, out_path, modele):
    import json as _json

    commune  = (cabinet.commune if cabinet else '') or ''
    date_str = consultation.date_consult.strftime('%d/%m/%Y')
    adeli    = (pc.adeli if pc else '') or ''
    forme    = (pc.forme_juridique if pc else '') or ''
    p        = consultation.patient

    # Colonne gauche : coordonnées cabinet + identifiants praticien
    left_lines = []
    if cabinet:
        if cabinet.rue:        left_lines.append(cabinet.rue)
        if cabinet.code_postal and cabinet.commune:
            left_lines.append(f'{cabinet.code_postal} {cabinet.commune}')
        elif cabinet.commune:
            left_lines.append(cabinet.commune)
        if cabinet.telephone:  left_lines.append(cabinet.telephone)
        if cabinet.email:      left_lines.append(cabinet.email)
    if adeli:                  left_lines.append(f'ADELI : {adeli}')
    if praticien.rpps:         left_lines.append(f'RPPS : {praticien.rpps}')
    if forme:                  left_lines.append(forme)
    left_lines.append(f'{praticien.prenom} {praticien.nom}')
    left_lines.append((praticien.titre or 'Orthoptiste').upper())

    type_label  = 'ORDONNANCE' if modele.type == 'ordonnance' else 'COURRIER'
    patient_nom = f'{p.nom} {p.prenom}'
    ddn_str     = p.date_naissance.strftime('%d/%m/%Y') if p.date_naissance else ''

    # Lignes colonne droite
    right_lines = []
    right_lines.append({'text': type_label, 'bold': True, 'size': 28, 'center': True, 'underline': True})
    right_lines.append({'text': f'Patient : {patient_nom}', 'bold': False, 'size': 20, 'center': False})
    if ddn_str:
        right_lines.append({'text': f'DDN : {ddn_str}', 'bold': False, 'size': 20, 'center': False})
    if p.medecin_referent:
        right_lines.append({'text': f'Médecin prescripteur : {p.medecin_referent}', 'bold': False, 'size': 20, 'center': False})
    lieu = f'À {commune}, le {date_str}' if commune else f'Le {date_str}'
    right_lines.append({'text': lieu, 'bold': False, 'size': 20, 'center': False})

    data = {
        'leftLines':    left_lines,
        'rightLines':   right_lines,
        'blocs':        blocs,
        'praticienNom': f'{praticien.titre or ""} {praticien.prenom} {praticien.nom}'.strip(),
        'outPath':      out_path,
    }

    js_data = _json.dumps(data, ensure_ascii=False)

    return r"""
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        AlignmentType, WidthType, BorderStyle, ShadingType, UnderlineType } = require('docx');
const fs = require('fs');

const D = """ + js_data + r""";

// ── Bordures ──────────────────────────────────────────────────────
const noBorder   = { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' };
// Ligne verte en bas uniquement (séparateur en-tête)
const sepBorder  = {
    top: noBorder, left: noBorder, right: noBorder,
    bottom: { style: BorderStyle.SINGLE, size: 6, color: '2E7D6B' }
};

// ── Colonne gauche ────────────────────────────────────────────────
// Adresse + coordonnées + ADELI/RPPS + forme + praticien + titre
const leftParas = D.leftLines.map((line, i) => {
    // Dernières lignes (praticien + titre) en taille normale
    const isIdent = i >= D.leftLines.length - 2;
    return new Paragraph({
        children: [new TextRun({
            text: line,
            size: isIdent ? 20 : 16,
            font: 'Verdana',
            bold: isIdent,
        })],
        spacing: { after: isIdent ? 20 : 30 }
    });
});

// ── Colonne droite ────────────────────────────────────────────────
const rightParas = D.rightLines.map((l, i) => new Paragraph({
    alignment: l.center ? AlignmentType.CENTER : AlignmentType.LEFT,
    children: [new TextRun({
        text: l.text,
        bold: l.bold,
        size: l.size,
        font: 'Verdana',
        underline: l.underline ? { type: UnderlineType.SINGLE } : undefined,
    })],
    spacing: { after: i === 0 ? 160 : 60 }
}));

// ── Table en-tête 2 colonnes ──────────────────────────────────────
// Page A4 : 11906 DXA, marges 1417 DXA → contenu = 9072 DXA
// Colonne gauche : 38% = ~3447 DXA | Droite : 62% = ~5625 DXA
const headerTable = new Table({
    width: { size: 9072, type: WidthType.DXA },
    columnWidths: [3447, 5625],
    rows: [new TableRow({
        children: [
            new TableCell({
                borders: sepBorder,
                width: { size: 3447, type: WidthType.DXA },
                margins: { top: 0, bottom: 120, left: 0, right: 280 },
                children: leftParas,
            }),
            new TableCell({
                borders: sepBorder,
                width: { size: 5625, type: WidthType.DXA },
                margins: { top: 0, bottom: 120, left: 280, right: 0 },
                children: rightParas,
            }),
        ]
    })]
});

// ── Corps du document ─────────────────────────────────────────────
const bodyChildren = [];

D.blocs.forEach(bloc => {
    if (bloc.type === 'texte' && bloc.contenu) {
        bloc.contenu.split('\n').forEach(line => {
            bodyChildren.push(new Paragraph({
                children: [new TextRun({ text: line, size: 22, font: 'Arial' })],
                spacing: { after: 100 }
            }));
        });
    } else if (bloc.type === 'sections') {
        bloc.sections.forEach(sec => {
            // Titre section
            bodyChildren.push(new Paragraph({
                children: [new TextRun({ text: sec.label, bold: true, size: 22,
                                         font: 'Arial', color: '2E7D6B' })],
                spacing: { before: 240, after: 80 },
                border: {
                    bottom: { style: BorderStyle.SINGLE, size: 2, color: 'CCCCCC' },
                    top: noBorder, left: noBorder, right: noBorder
                }
            }));
            // Observations
            if (sec.observations) {
                bodyChildren.push(new Paragraph({
                    children: [new TextRun({ text: sec.observations, size: 20,
                                             font: 'Arial', italics: true })],
                    spacing: { after: 80 }
                }));
            }
            // Champs structurés
            sec.lignes.forEach(([label, valeur]) => {
                bodyChildren.push(new Paragraph({
                    children: [
                        new TextRun({ text: label + ' : ', size: 20, font: 'Arial', bold: true }),
                        new TextRun({ text: valeur, size: 20, font: 'Arial' })
                    ],
                    spacing: { after: 60 }
                }));
            });
        });
    }
});

// ── Signature ─────────────────────────────────────────────────────
bodyChildren.push(new Paragraph({
    alignment: AlignmentType.RIGHT,
    spacing: { before: 720 },
    children: [new TextRun({ text: D.praticienNom, size: 20, font: 'Arial' })]
}));

// ── Document final ────────────────────────────────────────────────
const doc = new Document({
    sections: [{
        properties: {
            page: {
                size: { width: 11906, height: 16838 },
                margin: { top: 1417, right: 1417, bottom: 1417, left: 1417 }
            }
        },
        children: [
            headerTable,
            new Paragraph({ children: [], spacing: { after: 300 } }),
            ...bodyChildren,
        ]
    }]
});

Packer.toBuffer(doc).then(buf => { fs.writeFileSync(D.outPath, buf); });
"""





# ============================================================
# WOPI — Protocole d'intégration Collabora Online
# ============================================================

import secrets as _secrets
from datetime import timedelta

def _age_str(date_naissance, date_ref=None):
    """Retourne l'âge sous la forme 'X ans Y mois'."""
    if not date_naissance:
        return ''
    from datetime import date
    ref = date_ref or date.today()
    years = ref.year - date_naissance.year
    months = ref.month - date_naissance.month
    if ref.day < date_naissance.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    if years == 0:
        return f'{months} mois'
    if months == 0:
        return f'{years} ans'
    return f'{years} ans {months} mois'


def _wopi_token_for(consultation_id, section_type, docx_path, nom_fichier, section_ordre=0):
    """Crée une session WOPI et retourne le token."""
    token = _secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(hours=4)
    sess = WopiSession(
        token=token,
        consultation_id=consultation_id,
        section_type=section_type,
        section_ordre=section_ordre,
        nom_fichier=nom_fichier,
        chemin_fichier=docx_path,
        expires_at=expires,
    )
    db.session.add(sess); db.session.commit()
    return token


def _get_collabora_url(filename):
    """Retourne l'URL Collabora pour ouvrir un fichier .docx en édition."""
    import urllib.request, re
    discovery_url = f"{get_collabora_url()}/hosting/discovery"
    try:
        req = urllib.request.Request(
            discovery_url,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            discovery_xml = r.read().decode('utf-8')
        # Chercher l'action edit pour les docx
        m = re.search(
            r'<action name="edit" urlsrc="([^"]+)"[^/]*/>\s*</app>',
            discovery_xml)
        if not m:
            m = re.search(r'urlsrc="([^"]+lool[^"]+)"', discovery_xml)
        if not m:
            m = re.search(r'urlsrc="([^"]+)"', discovery_xml)
        if m:
            url = m.group(1)
            # S'assurer qu'elle se termine par ? ou &
            if '?' in url:
                return url if url.endswith('&') else url + '&'
            return url + '?'
    except Exception as e:
        app.logger.warning(f"Collabora discovery échoué ({e}), utilisation URL directe")

    # Fallback : URL directe standard Collabora/CODE
    # Format : https://collabora.domain.fr/browser/dist/cool.html?
    return f"{get_collabora_url()}/browser/dist/cool.html?"


@app.route('/wopi/files/<token>')
def wopi_check_file_info(token):
    """WOPI CheckFileInfo — retourne les métadonnées du fichier."""
    # Accepter aussi le token depuis access_token en query string
    if token == 'access_token':
        token = request.args.get('access_token', token)
    sess = WopiSession.query.filter_by(token=token).first()
    if not sess:
        # Chercher via access_token dans query string
        access_token = request.args.get('access_token', '')
        sess = WopiSession.query.filter_by(token=access_token).first()
    if not sess:
        return jsonify({'error': 'Session introuvable'}), 404
    if sess.expires_at and sess.expires_at < datetime.utcnow():
        return jsonify({'error': 'Token expiré'}), 401
    import os
    size = os.path.getsize(sess.chemin_fichier) if os.path.exists(sess.chemin_fichier) else 0
    resp = jsonify({
        'BaseFileName':          sess.nom_fichier,
        'Size':                  size,
        'OwnerId':               str(sess.consultation_id),
        'UserId':                'praticien',
        'UserFriendlyName':      'Praticien',
        'UserCanWrite':          True,
        'SupportsUpdate':        True,
        'SupportsLocks':         True,
        'SupportsGetLock':       True,
        'UserCanNotWriteRelative': True,
        'SupportsExtendedLockLength': True,
        'PostMessageOrigin':     get_collabora_url(),
        'DisableExport':         False,
        'DisablePrint':          False,
        'EnableOwnerTermination': True,
        'UserExtraInfo': {
            'Theme': 'Light',
            'DarkTheme': False,
        },
        'EnableDarkTheme': False,
        'Theme': 'Light',
    })
    resp.headers['ngrok-skip-browser-warning'] = 'true'
    return resp


@app.route('/wopi/files/<token>/contents', methods=['GET', 'POST'])
def wopi_file_contents(token):
    """WOPI GetFile / PutFile — lit ou écrit le fichier."""
    sess = WopiSession.query.filter_by(token=token).first_or_404()
    if sess.expires_at and sess.expires_at < datetime.utcnow():
        return jsonify({'error': 'Token expiré'}), 401

    if request.method == 'GET':
        from flask import send_file
        resp = send_file(sess.chemin_fichier,
                         mimetype='application/vnd.openxmlformats-officedocument'
                                  '.wordprocessingml.document')
        resp.headers['ngrok-skip-browser-warning'] = 'true'
        return resp

    elif request.method == 'POST':
        # Collabora envoie le fichier modifié
        import os, uuid, shutil
        data = request.get_data()
        if not data:
            return '', 200

        # Écrire directement sur le fichier original si possible
        folder = os.path.join(app.config['UPLOAD_FOLDER'],
                              'sections', str(sess.consultation_id))
        os.makedirs(folder, exist_ok=True)

        c = Consultation.query.get(sess.consultation_id)
        if c:
            # Chercher le FichierSection existant lié à cette session
            # 1. Par nom de fichier original (chemin de la session)
            orig_basename = os.path.basename(sess.chemin_fichier)
            existing = FichierSection.query.filter_by(
                consultation_id=sess.consultation_id,
                nom_stocke=orig_basename
            ).first()

            # 2. Si pas trouvé, chercher par titre + section_ordre
            if not existing:
                section = next((s for s in c.sections
                                if s.type == sess.section_type), None)
                section_ordre = section.ordre if section else (sess.section_ordre or 0)
                existing = FichierSection.query.filter_by(
                    consultation_id=sess.consultation_id,
                    section_ordre=section_ordre,
                    champ_name='wopi_doc',
                    titre=sess.nom_fichier
                ).first()
            else:
                section = next((s for s in c.sections
                                if s.type == (existing.section_type or sess.section_type)), None)
                section_ordre = section.ordre if section else (existing.section_ordre or sess.section_ordre or 0)

            if existing:
                # Mettre à jour le fichier existant en place
                dest = os.path.join(folder, existing.nom_stocke)
                with open(dest, 'wb') as fh:
                    fh.write(data)
                existing.section_ordre = section_ordre
                existing.created_at    = datetime.utcnow()
                # Mettre à jour le chemin dans la session pour les prochaines sauvegardes
                sess.chemin_fichier = dest
                db.session.commit()
                app.logger.info(f"WOPI: fichier existant mis à jour {existing.nom_stocke}")
            else:
                # Nouveau fichier
                nom_stocke = f"{uuid.uuid4().hex}.docx"
                dest = os.path.join(folder, nom_stocke)
                with open(dest, 'wb') as fh:
                    fh.write(data)
                # Résoudre section_ordre
                section = next((s for s in c.sections
                                if s.type == sess.section_type), None)
                section_ordre = section.ordre if section else (sess.section_ordre or 0)
                db.session.add(FichierSection(
                    consultation_id = sess.consultation_id,
                    section_ordre   = section_ordre,
                    section_type    = sess.section_type or '',
                    champ_name      = 'wopi_doc',
                    nom_original    = sess.nom_fichier,
                    nom_stocke      = nom_stocke,
                    type_fichier    = 'word',
                    titre           = sess.nom_fichier,
                ))
                sess.chemin_fichier = dest
                db.session.commit()
                app.logger.info(f"WOPI: nouveau fichier sauvegardé {nom_stocke}")

        return '', 200


@app.route('/wopi/files/<token>', methods=['POST'])
def wopi_file_lock(token):
    """WOPI Lock/Unlock/RefreshLock — nécessaire pour Collabora."""
    override = request.headers.get('X-WOPI-Override', '')
    app.logger.info(f"WOPI Lock operation: {override} for token {token[:20]}")
    if override in ('LOCK', 'REFRESH_LOCK', 'UNLOCK', 'GET_LOCK', 'PUT_RELATIVE'):
        return '', 200
    return '', 200


@app.route('/consultation/<int:consultation_id>/fichier-section/<int:fichier_id>/editer-collabora')
@login_required
def editer_fichier_collabora(consultation_id, fichier_id):
    """Ouvre un fichier de section existant dans Collabora pour édition."""
    import os, urllib.parse
    c  = Consultation.query.get_or_404(consultation_id)
    f  = FichierSection.query.get_or_404(fichier_id)

    # Chemin réel du fichier
    folder    = os.path.join(app.config['UPLOAD_FOLDER'], 'sections', str(consultation_id))
    file_path = os.path.join(folder, f.nom_stocke)

    if not os.path.exists(file_path):
        flash('Fichier introuvable.', 'danger')
        return redirect(url_for('consultation_modifier', consultation_id=consultation_id))

    # Créer une session WOPI pointant vers ce fichier existant
    token = _wopi_token_for(consultation_id, f.champ_name, file_path, f.nom_original,
                            section_ordre=f.section_ordre)

    wopi_src           = f"{get_wopi_base_url()}/wopi/files/{token}"
    collabora_action_url = _get_collabora_url(f.nom_original)
    editor_url = f"{collabora_action_url}WOPISrc={urllib.parse.quote(wopi_src, safe='')}&access_token={token}&darkTheme=false&ignoreSysTheme=1"
    editor_url = editor_url.replace('?&', '?').replace('&&', '&')

    return render_template('consultations/collabora_editor.html',
                           consultation=c,
                           editor_url=editor_url,
                           nom_fichier=f.nom_original,
                           token=token,
                           section_type=f.champ_name,
                           collabora_url=get_collabora_url())


@app.route('/consultation/<int:consultation_id>/editer-collabora', methods=['GET'])
@login_required
def editer_collabora(consultation_id):
    """Génère le .docx et ouvre l'éditeur Collabora."""
    c = Consultation.query.get_or_404(consultation_id)
    modele_id    = request.args.get('modele_id', type=int)
    section_type = request.args.get('section', 'courrier')

    if not modele_id:
        flash('Aucun modèle sélectionné.', 'danger')
        return redirect(url_for('generer_document', consultation_id=consultation_id,
                                section=section_type))

    modele = DocumentModele.query.get_or_404(modele_id)

    # Construire sections_par_bloc : dict {bloc_id: [section_types]}
    # Compatibilité : si sections_incluses[] global → affecter à tous les blocs sections
    sections_incluses_global = request.args.getlist('sections_incluses[]')
    sections_par_bloc = {}
    for key in request.args.keys():
        if key.startswith('bloc_') and key.endswith('_sections[]'):
            bloc_id = int(key.split('_')[1])
            sections_par_bloc[bloc_id] = request.args.getlist(key)

    # Fallback global
    if not sections_par_bloc and sections_incluses_global:
        for bloc in modele.blocs:
            if bloc.type == 'section_bilan':
                sections_par_bloc[bloc.id] = sections_incluses_global

    # sections_sel = union de toutes les sections (pour compatibilité)
    sections_sel = list({s for secs in sections_par_bloc.values() for s in secs}) or sections_incluses_global

    # Générer le .docx dans un dossier permanent (pas tmpdir)
    import os, tempfile, urllib.parse, shutil, uuid
    wopi_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'wopi')
    os.makedirs(wopi_dir, exist_ok=True)

    nom_custom = request.args.get('nom_document', '').strip()
    nom_base   = nom_custom if nom_custom else modele.nom
    nom_fichier = (f"{c.patient.nom}_{c.patient.prenom}_"
                   f"{c.date_consult.strftime('%Y%m%d')}_{nom_base}.docx")

    # Vérifier si un document avec le même nom existe déjà
    doublon = FichierSection.query.filter_by(
        consultation_id=consultation_id,
        champ_name='wopi_doc',
        titre=nom_fichier
    ).first()
    forcer = request.args.get('forcer', '')
    if doublon and not forcer:
        # Récupérer les sections sélectionnées pour les repasser
        sections_args = '&'.join(f'sections_incluses[]={s}' for s in sections_sel)
        base_url = url_for('generer_document',
                           consultation_id=consultation_id,
                           section=section_type,
                           doublon=nom_fichier,
                           modele_id=modele_id,
                           nom_document=nom_custom)
        return redirect(f"{base_url}&{sections_args}")

    images_ids  = [int(i) for i in request.args.getlist('images_incluses[]') if i.isdigit()]
    docx_path = _generer_docx(c, modele, sections_sel, images_ids=images_ids,
                               sections_par_bloc=sections_par_bloc)
    permanent_path = os.path.join(wopi_dir, f"{uuid.uuid4().hex}.docx")
    shutil.copy2(docx_path, permanent_path)

    # Créer la session WOPI
    token = _wopi_token_for(consultation_id, section_type, permanent_path, nom_fichier)

    # URL WOPI
    wopi_src = f"{get_wopi_base_url()}/wopi/files/{token}"

    # URL Collabora
    collabora_action_url = _get_collabora_url(nom_fichier)
    print(f"[WOPI] src: {wopi_src}")
    print(f"[WOPI] Collabora action URL: {collabora_action_url}")
    editor_url = f"{collabora_action_url}WOPISrc={urllib.parse.quote(wopi_src, safe='')}&access_token={token}&darkTheme=false&ignoreSysTheme=1"
    # Nettoyer les doubles ? ou & parasites
    editor_url = editor_url.replace('?&', '?').replace('&&', '&')
    print(f"[WOPI] Editor URL finale: {editor_url}")

    return render_template('consultations/collabora_editor.html',
                           consultation=c,
                           editor_url=editor_url,
                           nom_fichier=nom_fichier,
                           token=token,
                           section_type=section_type,
                           collabora_url=get_collabora_url(),
                           open_in_tab=True)

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    init_db()
    app.run(debug=False, port=5000)
