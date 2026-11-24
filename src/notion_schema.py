"""Récupération et validation des structures de bases Notion.

Ce module n'interroge que les métadonnées des sources de données.
Il ne récupère ni ne modifie les pages existantes pendant la synchronisation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping


class SchemaError(ValueError):
    """Erreur explicite de schéma ou de données Notion."""


@dataclass(frozen=True)
class PropertySchema:
    id: str
    name: str
    type: str
    options: tuple[str, ...] = ()
    relation_database_ids: tuple[str, ...] = ()
    formula: dict[str, Any] | None = None
    aggregation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "options": list(self.options),
            "relation_database_ids": list(self.relation_database_ids),
            "formula": self.formula,
            "aggregation": self.aggregation,
        }


@dataclass(frozen=True)
class DatabaseSchema:
    id: str
    name: str
    properties: dict[str, PropertySchema] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "properties": {
                prop.id: prop.to_dict() for prop in self.properties.values()
            },
        }


@dataclass(frozen=True)
class NotionSchema:
    databases: dict[str, DatabaseSchema] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "databases": {
                database.id: database.to_dict()
                for database in self.databases.values()
            }
        }


def parse_database_metadata(database_id: str, metadata: Mapping[str, Any]) -> DatabaseSchema:
    """Convertit les métadonnées Notion en schéma typé, sans lire de page."""
    name = str(metadata.get("title", [{}])[0].get("plain_text", "") if isinstance(metadata.get("title"), list) else metadata.get("name", "")).strip()
    properties: dict[str, PropertySchema] = {}
    raw_properties = metadata.get("properties") or {}
    if not isinstance(raw_properties, Mapping):
        raise SchemaError(f"La base {database_id} ne contient pas de propriétés valides.")

    for prop_name, raw in raw_properties.items():
        if not isinstance(raw, Mapping):
            raise SchemaError(f"Propriété invalide « {prop_name} » dans la base {database_id}.")
        prop_type = str(raw.get("type") or "")
        prop_id = str(raw.get("id") or "")
        if not prop_id:
            raise SchemaError(f"Identifiant absent pour la propriété « {prop_name} ».")

        options: tuple[str, ...] = ()
        if prop_type in {"select", "multi_select", "status"}:
            option_list = raw.get(prop_type, {}).get("options", [])
            options = tuple(str(option.get("name")) for option in option_list if option.get("name") is not None)

        relation_ids: tuple[str, ...] = ()
        if prop_type == "relation":
            relation = raw.get("relation") or {}
            database_id_target = relation.get("database_id")
            if database_id_target:
                relation_ids = (str(database_id_target),)

        formula = raw.get("formula") if prop_type == "formula" else None
        aggregation = raw.get(prop_type) if prop_type in {"rollup", "number"} else None
        properties[prop_name] = PropertySchema(
            id=prop_id,
            name=prop_name,
            type=prop_type,
            options=options,
            relation_database_ids=relation_ids,
            formula=formula,
            aggregation=aggregation,
        )

    return DatabaseSchema(id=str(database_id), name=name or str(database_id), properties=properties)


def retrieve_database_schemas(notion: Any, database_ids: Iterable[str]) -> NotionSchema:
    """Récupère uniquement les métadonnées des bases demandées."""
    databases: dict[str, DatabaseSchema] = {}
    seen: set[str] = set()
    for database_id in database_ids:
        database_id = str(database_id).strip()
        if not database_id:
            raise SchemaError("Un identifiant de base Notion est vide.")
        if database_id in seen:
            continue
        seen.add(database_id)
        try:
            metadata = notion.databases.retrieve(database_id=database_id)
        except Exception as error:
            raise SchemaError(
                f"Impossible de récupérer la base {database_id}. Vérifie le NOTION_TOKEN, "
                f"le partage de cette base avec l'intégration et l'identifiant: {error}"
            ) from error

        schema = parse_database_metadata(database_id, metadata)
        databases[database_id] = schema

        for prop in schema.properties.values():
            for related_id in prop.relation_database_ids:
                if related_id not in seen:
                    seen.add(related_id)
                    try:
                        related = notion.databases.retrieve(database_id=related_id)
                    except Exception as error:
                        raise SchemaError(
                            f"Impossible de récupérer la base liée {related_id}: {error}"
                        ) from error
                    databases[related_id] = parse_database_metadata(related_id, related)
    return NotionSchema(databases=databases)


def get_data_source_metadata(notion: Any, database_id: str) -> dict[str, Any]:
    """Retourne les métadonnées d'une source sans interroger les pages."""
    try:
        return notion.databases.retrieve(database_id=database_id)
    except Exception as error:
        raise SchemaError(
            f"Connexion Notion impossible pour la base {database_id}: permissions, partage ou ID invalide: {error}"
        ) from error


def validate_session_properties(schema: DatabaseSchema, values: Mapping[str, Any]) -> dict[str, Any]:
    """Construit des propriétés Notion validées et refuse toute inconnue."""
    properties: dict[str, Any] = {}
    for name, value in values.items():
        prop = schema.properties.get(name)
        if prop is None:
            raise SchemaError(f"Propriété inexistante: « {name} ».")

        if prop.type == "title":
            if not isinstance(value, str) or not value:
                raise SchemaError(f"« {name} » doit être un titre texte non vide.")
            properties[prop.id] = {"title": [{"text": {"content": value[:2000]}}]}
        elif prop.type == "rich_text":
            if not isinstance(value, str):
                raise SchemaError(f"« {name} » doit être du texte riche.")
            properties[prop.id] = {"rich_text": [{"text": {"content": value[:2000]}}]}
        elif prop.type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SchemaError(f"« {name} » doit être un nombre.")
            properties[prop.id] = {"number": value}
        elif prop.type == "checkbox":
            if not isinstance(value, bool):
                raise SchemaError(f"« {name} » doit être un booléen.")
            properties[prop.id] = {"checkbox": value}
        elif prop.type == "date":
            if isinstance(value, datetime):
                start = value.isoformat()
            elif isinstance(value, date):
                start = value.isoformat()
            elif isinstance(value, str):
                start = value
            else:
                raise SchemaError(f"« {name} » doit être une date valide.")
            properties[prop.id] = {"date": {"start": start}}
        elif prop.type in {"select", "status"}:
            if not isinstance(value, str):
                raise SchemaError(f"« {name} » doit être une sélection textuelle.")
            if prop.options and value not in prop.options:
                raise SchemaError(f"Option invalide « {value} » pour « {name} ». Options: {', '.join(prop.options)}")
            properties[prop.id] = {prop.type: {"name": value}}
        elif prop.type == "multi_select":
            if not isinstance(value, (list, tuple)):
                raise SchemaError(f"« {name} » doit être une liste de sélections.")
            names = list(value)
            for option in names:
                if not isinstance(option, str):
                    raise SchemaError(f"Une option de « {name} » doit être textuelle.")
                if prop.options and option not in prop.options:
                    raise SchemaError(f"Option invalide « {option} » pour « {name} ».")
            properties[prop.id] = {"multi_select": [{"name": option} for option in names]}
        elif prop.type == "relation":
            if not isinstance(value, (list, tuple)):
                raise SchemaError(f"« {name} » doit être une liste d'identifiants de relation.")
            ids = [str(item) for item in value]
            if not ids:
                raise SchemaError(f"Une relation « {name} » ne peut pas être vide.")
            properties[prop.id] = {"relation": [{"id": item} for item in ids]}
        else:
            raise SchemaError(f"Type de propriété non créable « {prop.type} » pour « {name} ».")

    missing = [
        prop.name for prop in schema.properties.values()
        if prop.type == "title" and prop.name not in values
    ]
    if missing:
        raise SchemaError(f"Données indispensables absentes: {', '.join(missing)}")
    return properties


def generate_session_page_properties(
    schema: DatabaseSchema,
    title_property: str,
    title: str,
    additional_values: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Génère des propriétés compatibles avec le schéma réel."""
    if title_property not in schema.properties:
        raise SchemaError(f"La propriété titre « {title_property} » est absente de la base {schema.id}.")
    values: dict[str, Any] = {title_property: title}
    values.update(additional_values or {})
    return validate_session_properties(schema, values)


def create_session_page(notion: Any, schema: DatabaseSchema, properties: Mapping[str, Any]) -> dict[str, Any]:
    """Crée une page seulement après validation complète des propriétés."""
    if not properties:
        raise SchemaError("Création refusée: aucune propriété validée.")
    try:
        return notion.pages.create(
            parent={"type": "data_source_id", "data_source_id": schema.id},
            properties=dict(properties),
        )
    except Exception as error:
        raise SchemaError(f"Création Notion refusée pour la base {schema.id}: {error}") from error
