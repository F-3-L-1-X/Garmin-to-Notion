# Schéma Notion et séances

Les outils de schéma travaillent uniquement sur les métadonnées des bases Notion : ils n'interrogent pas les pages ni leurs contenus et ne modifient aucune donnée Notion.

## Synchroniser le schéma

Configurez `NOTION_TOKEN` et `NOTION_DB_ID` comme pour la synchronisation habituelle, puis lancez :

```bash
uv run python scripts/sync-notion-schema.py --output notion-schema.json
```

Les bases configurées (`NOTION_DB_ID`, `NOTION_PR_DB_ID`, `NOTION_SLEEP_DB_ID`, `NOTION_STEPS_DB_ID`) et les bases liées par relation accessibles à l'intégration sont décrites avec leurs identifiants réels, leurs propriétés, types, options, formules, agrégations et relations. Le fichier est un instantané : relancez la commande après une modification dans Notion. Ne réutilisez pas un instantané ancien pour créer des pages ; le générateur reprend le schéma auprès de l'API avant chaque création.

Si la requête échoue, vérifiez que l'identifiant est celui d'une base/source de données, que `NOTION_TOKEN` est valide, et que chaque base, y compris les bases liées, est partagée avec l'intégration. L'erreur indique la base concernée et invite à vérifier l'ID, le partage et les permissions.

## Générer une page de séance

`src.notion_schema` expose `retrieve_database_schemas`, `generate_session_page_properties` et `create_session_page`. Il faut transmettre le schéma récupéré, le nom exact de la propriété titre, et toutes les valeurs requises. Le validateur refuse les propriétés inconnues, valeurs incompatibles, options de sélection absentes du schéma, propriétés non inscriptibles et titres manquants. Les relations sont vérifiées par rapport aux bases cibles déclarées, mais l'API Notion demeure l'autorité pour confirmer l'existence et l'accès aux identifiants de pages liés.

L'appel à `create_session_page` est le seul qui écrit dans Notion ; il n'est exécuté qu'après construction réussie des propriétés validées. Les séances Garmin existantes dans `src/workflows/notion-workouts.py` ne créent pas de pages Notion : elles envoient les entraînements vers Garmin. Ce nouveau module n'altère donc pas ce flux.

## Tests

```bash
uv run --with pytest pytest -q
```

Les tests utilisent une fausse API Notion et ne modifient pas de vraies pages. Aucun schéma de votre espace Notion n'a été récupéré lors de la mise en place, faute d'accès à son jeton d'intégration.
