"""Génération d'une page de séance après relecture du schéma Notion courant."""
from __future__ import annotations

from typing import Any, Mapping

from src.notion_schema import (
    DatabaseSchema,
    SchemaError,
    create_session_page,
    generate_session_page_properties,
    retrieve_database_schemas,
)


def generate_and_create_session(
    notion: Any,
    database_id: str,
    title_property: str,
    title: str,
    values: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh schema, validate all supplied values, then create one Notion page.

    This API intentionally does not read page contents and never writes before
    validation succeeds. Re-fetching prevents using a stale local schema.
    """
    schema_bundle = retrieve_database_schemas(notion, [database_id])
    schema: DatabaseSchema | None = schema_bundle.databases.get(database_id)
    if schema is None:
        raise SchemaError(f"Schéma introuvable pour la base cible {database_id}.")
    properties = generate_session_page_properties(
        schema, title_property, title, values
    )
    return create_session_page(notion, schema, properties)
