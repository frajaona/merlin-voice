"""Cas du bench LLM vocal (reconstruction du protocole 14-15/08 : 6 questions de
latence sans outil + 18 cas où un outil DOIT être appelé)."""

# Latence : aucune ne doit déclencher d'outil.
LATENCY = [
    "Quelle est la capitale de l'Australie ?",
    "Donne-moi une idée de dessert rapide avec des pommes.",
    "Combien font dix-sept fois vingt-trois ?",
    "Explique-moi en deux mots ce qu'est la photosynthèse.",
    "Traduis « bonne nuit » en espagnol.",
    "Raconte-moi une blague courte.",
]

WEATHER = {"web_search", "voir_la_meteo_d"}
SEARCH = {"web_search"}

# (question, outils acceptés, type news exigé ?)
TOOL_CASES = [
    ("Quel temps fera-t-il demain à Lyon ?", WEATHER, False),
    ("Il va pleuvoir cet après-midi à Paris ?", WEATHER, False),
    ("Quelle est la météo ce week-end à Bordeaux ?", WEATHER, False),
    ("Quelles sont les actualités du jour ?", SEARCH, True),
    ("Quoi de neuf dans l'actualité politique française ?", SEARCH, True),
    ("Qu'est-ce qui se passe en ce moment en Ukraine ?", SEARCH, True),
    ("Donne-moi les dernières nouvelles sur l'intelligence artificielle.", SEARCH, True),
    ("Quel est le score du dernier match du PSG ?", SEARCH, False),
    ("Qui a gagné le dernier Grand Prix de Formule 1 ?", SEARCH, False),
    ("Contre qui joue l'équipe de France de rugby ce week-end ?", SEARCH, False),
    ("À quelle heure ferme le Carrefour de Montreuil le dimanche ?", SEARCH, False),
    ("Le musée d'Orsay est ouvert le lundi ?", SEARCH, False),
    ("Combien coûte un iPhone 18 en ce moment ?", SEARCH, False),
    ("Quel est le prix du litre de gazole aujourd'hui en France ?", SEARCH, False),
    ("Quel est le cours du bitcoin ?", SEARCH, False),
    ("Quels films sortent au cinéma cette semaine ?", SEARCH, False),
    ("Qui est l'actuel Premier ministre ?", SEARCH, False),
    ("Y a-t-il une grève SNCF prévue cette semaine ?", SEARCH, True),
]
