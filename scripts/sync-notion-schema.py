"""Outils en ligne de commande pour synchroniser les schémas Notion."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from src.helpers import get_notion_client
from src.notion_schema import retrieve_database_schemas


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Récupère les métadonnées structurelles Notion.")
    parser.add_argument("--output", default="notion-schema.json", help="Fichier JSON de sortie")
    args = parser.parse_args()

    notion, databases = get_notion_client()
    ids = [database_id for database_id in (
        databases.activities,
        databases.personal_records,
        databases.sleep,
        databases.daily_steps,
    ) if database_id]
    if not ids:
        raise ValueError("Aucun identifiant de base Notion configuré.")

    schema = retrieve_database_schemas(notion, ids)
    path = Path(args.output)
    path.write_text(json.dumps(schema.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Schéma Notion écrit dans {path} ({len(schema.databases)} bases).")


if __name__ == "__main__":
    main()
