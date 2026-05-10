"""
Script dédié : ajoute les champs Add OD / Add OG
dans les sections correction_portee et refraction_subj.
Lancez avec : python add_addition.py
"""
from app import app, db, SectionDef, ChampDef

with app.app_context():
    for type_key in ['correction_portee', 'refraction_subj']:
        sec = SectionDef.query.filter_by(type_key=type_key).first()
        if not sec:
            print(f"Section {type_key} introuvable")
            continue
        existing = [c.name for c in sec.champs]
        max_ordre = max((c.ordre for c in sec.champs), default=0)
        for name, label in [('od_add', 'Add OD'), ('og_add', 'Add OG')]:
            if name not in existing:
                max_ordre += 1
                db.session.add(ChampDef(
                    section_id=sec.id,
                    name=name,
                    label=label,
                    type='number',
                    ordre=max_ordre
                ))
                print(f"OK : {label} ajouté dans {type_key}")
            else:
                print(f"Déjà présent : {label} dans {type_key}")
    db.session.commit()
    print("Terminé.")
