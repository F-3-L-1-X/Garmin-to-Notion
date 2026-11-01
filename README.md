# Garmin vers Notion — Journal d'entraînement

Ce dépôt reprend le projet [garmin-to-notion de chloevoyer](https://github.com/chloevoyer/garmin-to-notion) sous licence MIT et adapte la synchronisation des activités à ta base Notion « Journal d'entraînement ».

## Ce qui est synchronisé

Une fois configuré, GitHub Actions importe les activités Garmin chaque jour et peut aussi être lancé manuellement. Le script inscrit le nom, la date, la discipline, la distance, la durée, la fréquence cardiaque, les effets d'entraînement, la vitesse, les watts et le dénivelé lorsque Garmin fournit ces données. Les doublons sont recherchés avec le nom et la date.

Les autres synchronisations du projet source (records personnels, pas et sommeil) ne sont pas lancées car elles nécessitent des bases Notion séparées.

## Configuration rapide

1. Créer une intégration dans [Notion My integrations](https://www.notion.so/profile/integrations), copier son secret API, puis connecter cette intégration à la base « Journal d'entraînement » via le menu **••• → Connexions** de la base.
2. Dans ce dépôt GitHub, ouvrir **Settings → Secrets and variables → Actions → New repository secret** et ajouter :
   - `NOTION_TOKEN` : le secret de l'intégration Notion ;
   - `NOTION_DB_ID` : `2da53733bea98160bdcae88a07aea5eb` ;
   - `GARMIN_AUTH_TOKEN` : à créer à l'étape ci-dessous.
3. Installer `uv` sur Windows avec PowerShell : `winget install --id=astral-sh.uv -e`, puis fermer et rouvrir PowerShell.
4. Dans PowerShell, lancer :
   ```powershell
   uv run https://raw.githubusercontent.com/F-3-L-1-X/Garmin-to-Notion/main/scripts/generate-garmin-token.py
   ```
   Saisir les identifiants Garmin et le code de double authentification uniquement dans cette fenêtre locale. Copier tout le JSON affiché et le sauvegarder comme secret GitHub `GARMIN_AUTH_TOKEN`. Ne jamais publier ce JSON.
5. Dans GitHub, ouvrir **Actions → Sync Garmin to Notion → Run workflow** pour le premier lancement. Ensuite la synchronisation est quotidienne.

La synchronisation ajoute uniquement les activités qui ne sont pas déjà présentes, afin de préserver les modifications manuelles dans Notion. Les champs manquants dans Garmin sont laissés vides.

## Lancer depuis son ordinateur

Après avoir cloné ce dépôt et installé uv : `uv sync`, puis définir les trois variables d'environnement listées ci-dessus et lancer `uv run python -m src.workflows.garmin-activities`.

Licence : MIT. Le code source original conserve son attribution dans [LICENSE](LICENSE).
