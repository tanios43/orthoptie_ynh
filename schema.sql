-- ============================================================
-- Schéma base de données — Cabinet d'orthoptie multi-praticiens
-- Compatible PostgreSQL (recommandé en production)
-- et SQLite (pour le prototype local, remplacer SERIAL par INTEGER)
-- ============================================================

-- Extensions PostgreSQL utiles
-- CREATE EXTENSION IF NOT EXISTS pgcrypto; -- pour chiffrement futur

-- ------------------------------------------------------------
-- PRATICIENS
-- ------------------------------------------------------------
CREATE TABLE praticien (
    id              SERIAL PRIMARY KEY,
    nom             VARCHAR(100) NOT NULL,
    prenom          VARCHAR(100) NOT NULL,
    titre           VARCHAR(50) DEFAULT 'Orthoptiste',  -- Orthoptiste, Dr, etc.
    email           VARCHAR(200) UNIQUE,
    login_yunohost  VARCHAR(100) UNIQUE,                -- lié au SSO YunoHost
    actif           BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- PATIENTS
-- ------------------------------------------------------------
CREATE TABLE patient (
    id                  SERIAL PRIMARY KEY,
    nom                 VARCHAR(100) NOT NULL,
    prenom              VARCHAR(100) NOT NULL,
    date_naissance      DATE,
    sexe                VARCHAR(10),                    -- M / F / Autre
    adresse             TEXT,
    telephone           VARCHAR(20),
    email               VARCHAR(200),
    -- Médecin adresseur
    medecin_referent    VARCHAR(200),
    -- Sécurité sociale (à chiffrer en production avec pgcrypto)
    num_secu            VARCHAR(15),
    -- Praticien référent dans le cabinet
    praticien_id        INTEGER REFERENCES praticien(id),
    notes_admin         TEXT,                           -- notes administratives libres
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- CONSULTATIONS  (en-tête de chaque séance)
-- ------------------------------------------------------------
CREATE TABLE consultation (
    id              SERIAL PRIMARY KEY,
    patient_id      INTEGER NOT NULL REFERENCES patient(id) ON DELETE CASCADE,
    praticien_id    INTEGER NOT NULL REFERENCES praticien(id),
    date_consult    DATE NOT NULL DEFAULT CURRENT_DATE,
    motif           TEXT,                               -- motif de consultation
    -- Anamnèse
    entretien       TEXT,                               -- texte libre entretien
    plan_general    TEXT,                               -- texte libre plan général
    antecedents     TEXT,                               -- antécédents (libre)
    -- Conclusion générale
    conclusions     TEXT,
    observations    TEXT,                               -- observations libres globales
    -- Traçabilité (conformité RGPD)
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- JOURNAL D'ACCÈS (traçabilité RGPD obligatoire)
-- ------------------------------------------------------------
CREATE TABLE journal_acces (
    id              SERIAL PRIMARY KEY,
    praticien_id    INTEGER REFERENCES praticien(id),
    patient_id      INTEGER REFERENCES patient(id),
    consultation_id INTEGER REFERENCES consultation(id),
    action          VARCHAR(50) NOT NULL,               -- 'lecture', 'creation', 'modification', 'suppression'
    ip_address      VARCHAR(45),
    user_agent      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- RÉFRACTION
-- ------------------------------------------------------------
CREATE TABLE refraction (
    id                  SERIAL PRIMARY KEY,
    consultation_id     INTEGER NOT NULL REFERENCES consultation(id) ON DELETE CASCADE,
    -- Correction portée
    correction_od_sph   NUMERIC(5,2),                  -- sphère OD
    correction_od_cyl   NUMERIC(5,2),                  -- cylindre OD
    correction_od_axe   INTEGER,                        -- axe OD (0-180°)
    correction_og_sph   NUMERIC(5,2),
    correction_og_cyl   NUMERIC(5,2),
    correction_og_axe   INTEGER,
    -- Réfractométrie objective
    refracto_od_sph     NUMERIC(5,2),
    refracto_od_cyl     NUMERIC(5,2),
    refracto_od_axe     INTEGER,
    refracto_og_sph     NUMERIC(5,2),
    refracto_og_cyl     NUMERIC(5,2),
    refracto_og_axe     INTEGER,
    observations        TEXT
);

-- ------------------------------------------------------------
-- ACUITÉ VISUELLE
-- ------------------------------------------------------------
CREATE TABLE acuite_visuelle (
    id                  SERIAL PRIMARY KEY,
    consultation_id     INTEGER NOT NULL REFERENCES consultation(id) ON DELETE CASCADE,
    -- Binoculaire
    av_bino             VARCHAR(20),                    -- ex: 10/10
    -- OD
    av_od_loin          VARCHAR(20),
    av_od_pres          VARCHAR(20),                    -- ex: P2
    -- OG
    av_og_loin          VARCHAR(20),
    av_og_pres          VARCHAR(20),
    -- Conditions de mesure
    correction          VARCHAR(50),                    -- 'sans correction', 'avec correction habituelle', etc.
    distance_loin       VARCHAR(20) DEFAULT '5m',
    distance_pres       VARCHAR(20) DEFAULT '40cm',
    observations        TEXT
);

-- ------------------------------------------------------------
-- SWAINE INVERSE
-- ------------------------------------------------------------
CREATE TABLE swaine (
    id                  SERIAL PRIMARY KEY,
    consultation_id     INTEGER NOT NULL REFERENCES consultation(id) ON DELETE CASCADE,
    swaine_od           NUMERIC(4,1),                   -- valeur /10
    swaine_og           NUMERIC(4,1),
    observations        TEXT
);

-- ------------------------------------------------------------
-- VISION STÉRÉOSCOPIQUE
-- ------------------------------------------------------------
CREATE TABLE stereoscopie (
    id                  SERIAL PRIMARY KEY,
    consultation_id     INTEGER NOT NULL REFERENCES consultation(id) ON DELETE CASCADE,
    tno                 VARCHAR(20),                    -- ex: '60"', '30"', etc.
    lang                VARCHAR(20),                    -- 'positif', 'négatif', 'non réalisable'
    observations        TEXT
);

-- ------------------------------------------------------------
-- EXAMEN SOUS ÉCRAN (cover test + maddox + motilité)
-- ------------------------------------------------------------
CREATE TABLE examen_sous_ecran (
    id                      SERIAL PRIMARY KEY,
    consultation_id         INTEGER NOT NULL REFERENCES consultation(id) ON DELETE CASCADE,
    -- Cover test
    cover_loin              VARCHAR(50),                -- 'orthophorie', 'ésophorie', etc.
    cover_pres              VARCHAR(50),
    -- DIP et AC/A
    dip_mm                  NUMERIC(4,1),               -- distance inter-pupillaire en mm
    ac_a                    NUMERIC(4,2),               -- rapport AC/A
    -- Maddox
    maddox_loin             VARCHAR(50),
    maddox_pres             VARCHAR(50),
    -- Angle objectif
    angle_obj_loin          VARCHAR(100),
    angle_obj_pres          VARCHAR(100),
    -- Motilité
    motilite                VARCHAR(100),               -- 'normale', ou description
    -- PPC
    ppc_cm                  VARCHAR(20),                -- ex: '5 cm', '>10 cm'
    observations            TEXT
);

-- ------------------------------------------------------------
-- PRISMES — amplitudes de fusion
-- ------------------------------------------------------------
CREATE TABLE prismes (
    id                      SERIAL PRIMARY KEY,
    consultation_id         INTEGER NOT NULL REFERENCES consultation(id) ON DELETE CASCADE,
    -- Convergence
    convergence_loin        VARCHAR(20),                -- ex: '30-40Δ'
    convergence_pres        VARCHAR(20),
    -- Divergence
    divergence_loin         VARCHAR(20),
    divergence_pres         VARCHAR(20),
    observations            TEXT
);

-- ------------------------------------------------------------
-- FACILITÉS D'ACCOMMODATION
-- ------------------------------------------------------------
CREATE TABLE facilites_accommodation (
    id                      SERIAL PRIMARY KEY,
    consultation_id         INTEGER NOT NULL REFERENCES consultation(id) ON DELETE CASCADE,
    resultat_cpm            VARCHAR(20),                -- ex: '8-10 cpm'
    lenteur_en              VARCHAR(10),                -- '+', '-', '± égal'
    observations            TEXT
);

-- ------------------------------------------------------------
-- FACILITÉS DE VERGENCES
-- ------------------------------------------------------------
CREATE TABLE facilites_vergences (
    id                      SERIAL PRIMARY KEY,
    consultation_id         INTEGER NOT NULL REFERENCES consultation(id) ON DELETE CASCADE,
    resultat_cpm            VARCHAR(20),                -- ex: '12-15 cpm'
    observations            TEXT
);

-- ------------------------------------------------------------
-- INDEX pour les recherches fréquentes
-- ------------------------------------------------------------
CREATE INDEX idx_consultation_patient   ON consultation(patient_id);
CREATE INDEX idx_consultation_praticien ON consultation(praticien_id);
CREATE INDEX idx_consultation_date      ON consultation(date_consult);
CREATE INDEX idx_journal_praticien      ON journal_acces(praticien_id);
CREATE INDEX idx_journal_patient        ON journal_acces(patient_id);

-- ------------------------------------------------------------
-- TRIGGER : mise à jour automatique de updated_at
-- (PostgreSQL uniquement)
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_patient_updated
    BEFORE UPDATE ON patient
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_consultation_updated
    BEFORE UPDATE ON consultation
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
