from __future__ import annotations

from datetime import date

import pytest

from src.notion_schema import (
    SchemaError,
    create_session_page,
    generate_session_page_properties,
    parse_database_metadata,
    retrieve_database_schemas,
    validate_session_properties,
)
from src.session_generator import generate_and_create_session


DATABASE = {
    "title": [{"plain_text": "Journal d'entraînement"}],
    "properties": {
        "Nom": {"id": "title-id", "name": "Nom", "type": "title", "title": {}},
        "Discipline": {
            "id": "discipline-id", "name": "Discipline", "type": "multi_select",
            "multi_select": {"options": [{"name": "Yoga"}, {"name": "Course à pied"}]},
        },
        "Terminé": {"id": "done-id", "name": "Terminé", "type": "checkbox", "checkbox": {}},
        "Date": {"id": "date-id", "name": "Date", "type": "date", "date": {}},
        "Programme": {
            "id": "relation-id", "name": "Programme", "type": "relation",
            "relation": {"database_id": "linked-db"},
        },
        "Charge": {
            "id": "formula-id", "name": "Charge", "type": "formula",
            "formula": {"expression": "1 + 1"},
        },
    },
}


class FakeNotion:
    def __init__(self, databases):
        self.databases = self
        self.pages = self
        self._databases = databases
        self.created = []
        self.retrieved = []

    def retrieve(self, database_id):
        self.retrieved.append(database_id)
        if database_id not in self._databases:
            raise RuntimeError("base inconnue")
        return self._databases[database_id]

    def create(self, **kwargs):
        self.created.append(kwargs)
        return kwargs


def test_parse_database_metadata_preserves_ids_options_formula_and_relation():
    schema = parse_database_metadata("db-1", DATABASE)
    assert schema.id == "db-1"
    assert schema.properties["Nom"].id == "title-id"
    assert schema.properties["Discipline"].options == ("Yoga", "Course à pied")
    assert schema.properties["Programme"].relation_database_ids == ("linked-db",)
    assert schema.properties["Charge"].formula == {"expression": "1 + 1"}


def test_retrieve_database_schemas_includes_linked_database_metadata_only():
    fake = FakeNotion({
        "db-1": DATABASE,
        "linked-db": {"title": [{"plain_text": "Programmes"}], "properties": {}},
    })
    schema = retrieve_database_schemas(fake, ["db-1"])
    assert set(schema.databases) == {"db-1", "linked-db"}


def test_unknown_property_is_refused():
    schema = parse_database_metadata("db-1", DATABASE)
    with pytest.raises(SchemaError, match="Propriété inexistante"):
        validate_session_properties(schema, {"Inconnu": True})


def test_invalid_types_are_refused():
    schema = parse_database_metadata("db-1", DATABASE)
    with pytest.raises(SchemaError, match="booléen"):
        validate_session_properties(schema, {"Nom": "Séance", "Terminé": "oui"})


def test_invalid_select_option_is_refused():
    schema = parse_database_metadata("db-1", DATABASE)
    with pytest.raises(SchemaError, match="Option invalide"):
        validate_session_properties(schema, {"Nom": "Séance", "Discipline": ["Natation"]})


def test_valid_session_generation_and_relation():
    schema = parse_database_metadata("db-1", DATABASE)
    properties = generate_session_page_properties(
        schema, "Nom", "Séance yoga",
        {"Discipline": ["Yoga"], "Terminé": False, "Date": date(2026, 11, 24), "Programme": ["prog-1"]},
    )
    assert properties["title-id"] == {"title": [{"text": {"content": "Séance yoga"}}]}
    assert properties["discipline-id"]["multi_select"] == [{"name": "Yoga"}]
    assert properties["relation-id"]["relation"] == [{"id": "prog-1"}]
    assert properties["date-id"]["date"] == {"start": "2026-11-24"}


def test_creation_only_after_validation():
    schema = parse_database_metadata("db-1", DATABASE)
    fake = FakeNotion({})
    with pytest.raises(SchemaError, match="inexistante"):
        props = validate_session_properties(schema, {"Bad": "value"})
        create_session_page(fake, schema, props)
    assert fake.created == []


def test_connection_error_is_explicit():
    with pytest.raises(SchemaError, match="base inconnue"):
        retrieve_database_schemas(FakeNotion({}), ["db-1"])


def test_unwritable_formula_is_refused():
    schema = parse_database_metadata("db-1", DATABASE)
    with pytest.raises(SchemaError, match="non créable"):
        validate_session_properties(schema, {"Nom": "Séance", "Charge": 4})


def test_generator_refreshes_schema_before_write():
    fake = FakeNotion({"db-1": DATABASE})
    result = generate_and_create_session(
        fake, "db-1", "Nom", "Séance fraîche",
        {"Discipline": ["Yoga"]},
    )
    assert fake.retrieved == ["db-1"]
    assert len(fake.created) == 1
    assert result["properties"]["title-id"]["title"][0]["text"]["content"] == "Séance fraîche"


def test_generator_refuses_invalid_values_without_calling_writer():
    fake = FakeNotion({"db-1": DATABASE})
    with pytest.raises(SchemaError, match="Option invalide"):
        generate_and_create_session(fake, "db-1", "Nom", "Mauvaise séance", {"Discipline": ["Natation"]})
    assert fake.created == []
