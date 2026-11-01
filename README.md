# Garmin vers Notion — Journal d'entraînement

Ce dépôt reprend le projet [garmin-to-notion de chloevoyer](https://github.com/chloevoyer/garmin-to-notion) sous licence MIT et adapte la synchronisation des activités à ta base Notion « Journal d'entraînement ».

## Ce qui est synchronisé

Une fois configuré, GitHub Actions vérifie les nouvelles activités Garmin toutes les heures et peut aussi être lancé manuellement juste après une séance. Pour chaque activité terminée, le script cherche la séance prévue à la même date : d'abord avec le même nom, puis avec la même discipline si une seule séance correspond. Il met à jour cette ligne avec les mesures fournies par Garmin et coche « Terminé », en conservant les informations prévues et les notes personnelles. Si aucune séance correspondante n'est trouvée, il crée une nouvelle ligne. Les mesures importées peuvent inclure la distance, la durée, la fréquence cardiaque, la charge d'exercice, les effets d'entraînement, la vitesse, les watts, la respiration et le dénivelé.

Pour les activités de course, le programme crée aussi dans la page Notion un bloc repliable « Bilan de la séance » avec les courbes disponibles (allure, fréquence cardiaque, cadence, course/marche) et les zones Garmin de fréquence cardiaque et de puissance. Le bilan est une image graphique générée à partir des données détaillées Garmin, pas une capture d'écran de Garmin Connect. Les courbes ou zones absentes de l'activité ne sont pas affichées. Une activité déjà accompagnée de son bilan n'est pas recopiée à chaque passage.

Les autres synchronisations du projet source (records personnels, pas et sommeil) ne sont pas lancées car elles nécessitent des bases Notion séparées.

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
6. Dans GitHub, ouvrir **Actions → Sync Garmin to Notion → Run workflow** pour le premier lancement. Ensuite la synchronisation est quotidienne.

Les champs subjectifs (qualité, douleur, charge mentale, énergie après 24 h), le matériel, le lieu et la météo restent à renseigner dans Notion. Les champs que Garmin ne fournit pas pour une activité sont laissés tels quels.

## Lancer depuis son ordinateur

Après avoir cloné ce dépôt et installé uv : `uv sync`, puis définir les variables d'environnement listées ci-dessus et lancer `uv run python -m src.workflows.garmin-activities`.

Licence : MIT. Le code source original conserve son attribution dans [LICENSE](LICENSE).
