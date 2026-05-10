"""
Ajoute les sections 'prescription' et 'courrier' en base.
Lancez avec : python add_sections_doc.py
"""
from app import app, db, SectionDef, ChampDef

SECTIONS = [
    ('prescription', 'Prescription', [
        ('renouvellement',  'Renouvellement correction',  'textarea', False),
        ('bilan_demande',   'Bilan demandé',              'textarea', False),
        ('exercices',       'Exercices prescrits',        'textarea', False),
        ('nombre_seances',  'Nombre de séances',          'text',     False),
        ('rythme',          'Rythme',                     'text',     False),
        ('commentaires',    'Commentaires',               'textarea', False),
    ]),
    ('courrier', 'Courrier / Compte-rendu', [
        ('introduction',    'Introduction',               'textarea', False),
        ('anamnese_courrier','Anamnèse',                  'textarea', False),
        ('resultats',       'Résultats',                  'textarea', False),
        ('conclusion_courrier','Conclusion',              'textarea', False),
    ]),
]

with app.app_context():
    for type_key, label, champs, builtin in [
        (k, l, c, True) for k, l, c in [
            (s[0], s[1], s[2]) for s in SECTIONS
        ]
    ]:
        existing = SectionDef.query.filter_by(type_key=type_key).first()
        if existing:
            print(f'Présent : {label}')
            continue
        # Trouver le dernier ordre
        max_ordre = db.session.query(db.func.max(SectionDef.ordre)).scalar() or 0
        sec = SectionDef(
            type_key=type_key,
            label=label,
            ordre=max_ordre + 1,
            builtin=True,
            actif=True
        )
        db.session.add(sec)
        db.session.flush()
        for i, (name, ch_label, ch_type, _) in enumerate(champs):
            db.session.add(ChampDef(
                section_id=sec.id,
                name=name,
                label=ch_label,
                type=ch_type,
                ordre=i
            ))
        db.session.commit()
        print(f'OK : section {label} ajoutée')

    print('Terminé.')
