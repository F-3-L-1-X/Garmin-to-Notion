# Garmin vers Notion — Journal d'entraînement

Ce dépôt reprend le projet [garmin-to-notion de chloevoyer](https://github.com/chloevoyer/garmin-to-notion) sous licence MIT et adapte la synchronisation des activités à ta base Notion « Journal d'entraînement ».

## Ce qui est synchronisé

Une fois configuré, GitHub Actions vérifie les nouvelles activités Garmin toutes les 15 minutes et peut aussi être lancé manuellement juste après une séance. Pour chaque activité terminée, le script cherche la séance prévue à la même date : d'abord avec le même nom, puis avec la même discipline si une seule séance correspond. Il met à jour cette ligne avec les mesures fournies par Garmin et coche « Terminé », en conservant les informations prévues et les notes personnelles. Si aucune séance correspondante n'est trouvée, il crée une nouvelle ligne. Les mesures importées peuvent inclure la distance, la durée, la fréquence cardiaque, la charge d'exercice, les effets d'entraînement, la vitesse, les watts, la respiration et le dénivelé.

Pour les activités où Garmin fournit des données détaillées, notamment la course à pied, le programme crée aussi dans la page Notion un bloc repliable « Bilan de la séance » avec les courbes disponibles (allure, fréquence cardiaque, cadence, course/marche) et les zones Garmin de fréquence cardiaque et de puissance. Le bilan est une image graphique générée à partir des données détaillées Garmin, pas une capture d'écran de Garmin Connect. Les courbes ou zones absentes de l'activité ne sont pas affichées. Une activité déjà accompagnée de son bilan n'est pas recopiée à chaque passage.

Quand Garmin fournit un parcours GPS, le fichier `.gpx` original est également ajouté dans la propriété fichier « Parcours » de la même séance. Les parcours déjà joints ne sont pas ajoutés une seconde fois.

Les autres synchronisations du projet source (records personnels, pas et sommeil) ne sont pas lancées car elles nécessitent des bases Notion séparées.

## Préparer un entraînement Notion sur la montre Garmin

Notion reste l'endroit où tu écris le programme. Toutes les 15 minutes, GitHub Actions cherche dans « Journal d'entraînement » les séances prévues aujourd'hui ou dans les 14 jours, qui ne sont pas cochées « Terminé ». Le programme lit le champ « Discipline » et prépare un entraînement Garmin dès qu'il trouve du texte dans la page. Le titre devient le nom de l'entraînement. Le texte du contenu de la page et des propriétés textuelles ou à choix (dont « Source ») est conservé dans la description Garmin jusqu'à 1 000 caractères ; si le texte dépasse cette limite, la suite est ajoutée comme des étapes de note manuelles à la fin. Les lignes reconnues créent aussi les étapes propres au sport. L'affichage des notes dépend du modèle de montre.

Les disciplines de ta base sont prises en compte : course à pied, vélo, natation, renforcement, haut/bas/tout le corps, yoga, mobilité, étirements, gymnastique, marche/randonnée, ski de fond et escalade. Les séances de repos et de massage sont ignorées. Garmin Connect ne proposant pas de type d'entraînement personnalisé dédié à la marche/randonnée, au ski de fond et à l'escalade dans cette intégration, ces trois disciplines sont envoyées comme séances génériques « Autre ».

Pour la course et le vélo, écris des étapes avec une durée, par exemple « 5 min échauffement » ou « 3x (1 min course / 1 min marche) ». Indique toujours le nombre de répétitions ; si un programme vélo demande de répéter un bloc sans préciser combien de fois, l'envoi est volontairement arrêté pour éviter de créer une séance incorrecte. Pour la natation, utilise des lignes comme « 100 m — Brasse — Facile » et « Repos : 15 s ». Le yoga, les étirements, la mobilité et les routines numérotées apparaissent étape par étape sur la montre. Une durée simple est chronométrée ; pour une consigne sans durée précise, la montre attend que tu appuies sur le bouton de passage à l'étape suivante. Les zones d'allure, de puissance et de fréquence cardiaque ne sont pas encore réglées automatiquement.

Pour les séances de renforcement, conserve une structure avec des titres d'atelier, puis une ligne par exercice. Exemple : « ATELIER 1 », « 5 x REGULAR PUSH UP — 19 », « Séries : 2 » et « Récupération : 1,30 min ». Le programme lit le nombre avant « x » comme le nombre de répétitions, « Séries » comme le nombre de tours et « Récupération » comme le repos entre les tours. Le nombre après le tiret est conservé comme référence dans la note de l'étape. Vérifie cette convention avec ton programme d'origine avant de te fier aux nombres sur la montre. Si Garmin ne connaît pas le nom d'un exercice, le nom original reste dans la description de l'étape. Si le texte ne respecte pas cette structure, les consignes restent disponibles comme étapes manuelles, mais les répétitions et les séries ne sont alors pas programmées automatiquement.

Le nom de chaque entraînement contient un identifiant de la page Notion pour éviter les doublons. Si tu modifies ensuite le texte, l'entraînement Garmin correspondant est mis à jour et renvoyé à la montre. La montre doit ensuite se synchroniser avec Garmin Connect. Ouvre l'activité qui correspond au sport et sélectionne l'entraînement prévu. Les menus et la compatibilité des entraînements dépendent du modèle de montre ; pour la marche, la randonnée, le ski de fond et l'escalade, il faudra lancer le profil « Autre ».

## Configuration rapide

1. Créer une intégration dans [Notion My integrations](https://www.notion.so/profile/integrations), copier son secret API, puis connecter cette intégration à la base « Journal d'entraînement » via le menu **••• → Connexions** de la base.
2. Dans ce dépôt GitHub, ouvrir **Settings → Secrets and variables → Actions → New repository secret** et ajouter :
   - `NOTION_TOKEN` : le secret de l'intégration Notion, sans le préfixe `Bearer ` ;
   - `NOTION_DB_ID` : `2da53733bea98160bdcae88a07aea5eb` ;
   - `GARMIN_AUTH_TOKEN` : à créer à l'étape ci-dessous.
3. Le programme sélectionne automatiquement la source de données nommée « Journal d'entraînement ». Si un lancement GitHub demande explicitement `NOTION_DATA_SOURCE_ID`, ouvrir la base dans Notion, puis **••• → Gérer les sources de données → Copier l'ID de la source** et enregistrer cet ID comme secret GitHub.
4. Installer `uv` sur Windows avec PowerShell : `winget install --id=astral-sh.uv -e`, puis fermer et rouvrir PowerShell.
5. Dans PowerShell, lancer :
   ```powershell
   uv run https://raw.githubusercontent.com/F-3-L-1-X/Garmin-to-Notion/main/scripts/generate-garmin-token.py
   ```
   Saisir les identifiants Garmin et le code de double authentification uniquement dans cette fenêtre locale. Copier tout le JSON affiché et le sauvegarder comme secret GitHub `GARMIN_AUTH_TOKEN`. Ne jamais publier ce JSON.
6. Pour tester seulement l'envoi d'un entraînement, ouvrir **Actions → Sync Garmin to Notion → Run workflow**, décocher **SYNC_ACTIVITIES**, puis lancer le workflow. Dans le journal, vérifier l'étape « Envoyer les entraînements prévus sur la montre ». Pour tester aussi l'import d'activités, laisser **SYNC_ACTIVITIES** coché et choisir une limite de `1` activité. Ensuite, le contrôle automatique a lieu toutes les 15 minutes ; tu peux aussi lancer le workflow manuellement après une séance.

Les champs subjectifs (qualité, douleur, charge mentale, énergie après 24 h), le matériel, le lieu et la météo restent à renseigner dans Notion. Les champs que Garmin ne fournit pas pour une activité sont laissés tels quels.

## Lancer depuis son ordinateur

Après avoir cloné ce dépôt et installé uv : `uv sync`, puis définir les variables d'environnement listées ci-dessus et lancer `uv run python -m src.workflows.garmin-activities`.

Licence : MIT. Le code source original conserve son attribution dans [LICENSE](LICENSE).
