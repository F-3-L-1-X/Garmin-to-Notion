from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timedelta
from typing import Any

from garminconnect import Garmin
from garminconnect import exercises as garmin_exercises
from notion_client import Client as NotionClient

from src.helpers import get_garmin_client, get_notion_client

DATE_PROPERTY = "Date de la  séance"
TITLE_PROPERTY = "Nom"
DISCIPLINE_PROPERTY = "Discipline"
DONE_PROPERTY = "Terminé"
STRENGTH_DISCIPLINE = "Renforcement"
LOOKAHEAD_DAYS = max(1, min(int(os.getenv("GARMIN_WORKOUTS_LOOKAHEAD_DAYS", "14")), 90))
PAGE_MARKER_LENGTH = 16
HASH_LENGTH = 8

EXERCISE_LINE = re.compile(
    r"^(?P<reps>\d+)\s*[x×]\s*(?P<name>.+?)"
    r"(?:\s*[—–-]\s*(?P<reference>\d+))?$",
    re.IGNORECASE,
)
ATELIER_HEADER = re.compile(r"^(?:atelier\s+\d+\b|.*\bemom\b.*)", re.IGNORECASE)
SETS_LINE = re.compile(r"^s(?:é|e)ries?\s*[:：]\s*(\d+)\b", re.IGNORECASE)
NOTE_LINE = re.compile(r"^(?:notes?|remarques?)\s*[:：]\s*(.*)$", re.IGNORECASE)
EMOM_DURATION = re.compile(r"\bemom\b.*?(\d+)\s*(?:min|minutes|mn)\b", re.IGNORECASE)
REST_TIME = re.compile(
    r"(?P<amount>\d+(?:[,.]\d+)?)\s*(?P<unit>minutes?|mins?|mn|secondes?|secs?|s)\b",
    re.IGNORECASE,
)


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def page_title(page: dict[str, Any]) -> str:
    title = page.get("properties", {}).get(TITLE_PROPERTY, {}).get("title", [])
    return "".join(item.get("plain_text", "") for item in title).strip()


def page_date(page: dict[str, Any]) -> str | None:
    value = page.get("properties", {}).get(DATE_PROPERTY, {}).get("date")
    start = value.get("start") if value else None
    return start[:10] if start else None


def get_activity_data_source_id(notion: NotionClient, database_id: str) -> str:
    database = notion.databases.retrieve(database_id=database_id)
    data_sources = database.get("data_sources", [])
    requested_id = (os.getenv("NOTION_DATA_SOURCE_ID") or "").strip()

    if requested_id:
        if any(source.get("id") == requested_id for source in data_sources):
            return requested_id
        raise ValueError(
            "NOTION_DATA_SOURCE_ID ne correspond à aucune source de données de NOTION_DB_ID."
        )

    preferred = [
        source
        for source in data_sources
        if source.get("name", "").strip().casefold() == "journal d'entraînement"
    ]
    if len(preferred) == 1:
        return preferred[0]["id"]
    if len(data_sources) == 1:
        return data_sources[0]["id"]
    if not data_sources:
        raise ValueError(
            "Aucune source de données trouvée dans la base Notion. "
            "Vérifie NOTION_DB_ID et l'accès de la connexion."
        )

    names = ", ".join(source.get("name", "(sans nom)") for source in data_sources)
    raise ValueError(
        "Plusieurs sources de données existent. Ajoute l'ID de « Journal d'entraînement » "
        f"dans le secret NOTION_DATA_SOURCE_ID. Sources trouvées : {names}."
    )


def upcoming_pages(
    notion: NotionClient,
    data_source_id: str,
    today: date,
) -> list[dict[str, Any]]:
    end = today + timedelta(days=LOOKAHEAD_DAYS + 1)
    filters = {
        "and": [
            {"property": DATE_PROPERTY, "date": {"on_or_after": today.isoformat()}},
            {"property": DATE_PROPERTY, "date": {"before": end.isoformat()}},
            {"property": DISCIPLINE_PROPERTY, "multi_select": {"contains": STRENGTH_DISCIPLINE}},
            {"property": DONE_PROPERTY, "checkbox": {"equals": False}},
        ]
    }
    pages: list[dict[str, Any]] = []
    cursor = None
    while True:
        arguments: dict[str, Any] = {
            "data_source_id": data_source_id,
            "filter": filters,
            "page_size": 100,
        }
        if cursor:
            arguments["start_cursor"] = cursor
        result = notion.data_sources.query(**arguments)
        pages.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
        if not cursor:
            break
    return pages


def _text_from_block(block: dict[str, Any]) -> str:
    block_type = block.get("type")
    body = block.get(block_type, {}) if block_type else {}
    runs = body.get("rich_text") or body.get("title") or []
    if runs:
        return "".join(run.get("plain_text", "") for run in runs)
    cells = body.get("cells")
    if cells:
        return " | ".join(
            "".join(run.get("plain_text", "") for run in cell)
            for cell in cells
        )
    return ""


def page_content_lines(notion: NotionClient, page_id: str) -> list[str]:
    lines: list[str] = []
    visited: set[str] = set()

    def read_children(block_id: str, depth: int = 0) -> None:
        if depth > 8 or block_id in visited:
            return
        visited.add(block_id)
        cursor = None
        while True:
            arguments: dict[str, Any] = {"block_id": block_id, "page_size": 100}
            if cursor:
                arguments["start_cursor"] = cursor
            result = notion.blocks.children.list(**arguments)
            for block in result.get("results", []):
                text = _text_from_block(block)
                if text.strip():
                    lines.extend(text.splitlines())
                if block.get("has_children") and block.get("id"):
                    read_children(block["id"], depth + 1)
            if not result.get("has_more"):
                break
            cursor = result.get("next_cursor")
            if not cursor:
                break

    read_children(page_id)
    return [line.strip() for line in lines if line.strip()]


def _parse_rest_seconds(line: str) -> int | None:
    match = REST_TIME.search(line)
    if not match:
        return None
    raw = match.group("amount").replace(",", ".")
    unit = match.group("unit").casefold()
    if unit.startswith(("min", "mn")):
        if "." in raw:
            whole, fraction = raw.split(".", 1)
            if len(fraction) == 2:
                return int(whole) * 60 + int(fraction)
        return round(float(raw) * 60)
    return round(float(raw))


def parse_sections(lines: list[str]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    collecting_note = False

    def save_section() -> None:
        if current and current["exercises"]:
            duration_match = EMOM_DURATION.search(current["title"])
            rounds = current["sets"]
            if rounds is None:
                rounds = int(duration_match.group(1)) if duration_match else 1
            current["rounds"] = max(1, rounds)
            current["notes"] = " ".join(current["notes"]).strip()
            sections.append(current)

    for raw_line in lines:
        line = re.sub(r"^(?:[-*•]\s+|[☐☑]\s*)", "", raw_line).strip()
        if ATELIER_HEADER.match(line):
            save_section()
            current = {
                "title": line,
                "sets": None,
                "rest_seconds": None,
                "exercises": [],
                "notes": [],
            }
            collecting_note = False
            continue
        if current is None:
            continue

        set_match = SETS_LINE.match(line)
        if set_match:
            current["sets"] = int(set_match.group(1))
            collecting_note = False
            continue

        if re.match(r"^récupération\s*[:：]", line, re.IGNORECASE):
            current["rest_seconds"] = _parse_rest_seconds(line)
            collecting_note = False
            continue

        note_match = NOTE_LINE.match(line)
        if note_match:
            if note_match.group(1).strip():
                current["notes"].append(note_match.group(1).strip())
            collecting_note = True
            continue

        exercise_match = EXERCISE_LINE.match(line)
        if exercise_match:
            current["exercises"].append(
                {
                    "reps": int(exercise_match.group("reps")),
                    "name": exercise_match.group("name").strip(),
                    "reference": exercise_match.group("reference"),
                }
            )
            collecting_note = False
            continue

        if collecting_note:
            current["notes"].append(line)

    save_section()
    return sections


def _resolve_exercise(name: str) -> tuple[str, str, bool]:
    normalized = normalize_name(name)
    aliases = {
        "regularpushup": "Push-up",
        "pushup": "Push-up",
        "burpee": "Burpee",
    }
    display_name = aliases.get(normalized, name.strip())
    catalog = {
        normalize_name(item["name"]): item
        for item in garmin_exercises.EXERCISES
    }
    match = catalog.get(normalize_name(display_name))
    if match:
        return match["category"], match.get("exercise", ""), True

    # Garmin requires a valid category. Keep the exact Notion exercise name in
    # the step description whenever the catalog has no exact match.
    if "push" in normalized:
        category = "PUSH_UP"
    elif "triceps" in normalized:
        category = "TRICEPS_EXTENSION"
    elif "shoulder" in normalized or "snowangel" in normalized:
        category = "SHOULDER_STABILITY"
    else:
        category = "TOTAL_BODY"
    return category, "", False


def _exercise_step(
    item: dict[str, Any],
    section: dict[str, Any],
    step_order: int,
) -> dict[str, Any]:
    category, exercise_name, exact_match = _resolve_exercise(item["name"])
    reference = f" · Réf. {item['reference']}" if item.get("reference") else ""
    note = f" · {section['notes']}" if section.get("notes") else ""
    emom = " · EMOM : un tour au début de chaque minute" if "emom" in section["title"].casefold() else ""
    match_note = "" if exact_match else " · nom d'exercice Garmin générique"
    description = (
        f"{section['title']} · {item['name']}{reference}{note}{emom}{match_note}"
    )
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": step_order,
        "stepType": {
            "stepTypeId": 3,
            "stepTypeKey": "interval",
            "displayOrder": 3,
        },
        "endCondition": {
            "conditionTypeId": 10,
            "conditionTypeKey": "reps",
            "displayOrder": 10,
            "displayable": True,
        },
        "endConditionValue": float(item["reps"]),
        "targetType": {
            "workoutTargetTypeId": 1,
            "workoutTargetTypeKey": "no.target",
            "displayOrder": 1,
        },
        "category": category,
        "exerciseName": exercise_name,
        "description": description[:500],
    }


def _rest_step(seconds: int, step_order: int) -> dict[str, Any]:
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": step_order,
        "stepType": {"stepTypeId": 5, "stepTypeKey": "rest", "displayOrder": 5},
        "endCondition": {
            "conditionTypeId": 2,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        },
        "endConditionValue": float(seconds),
        "targetType": {
            "workoutTargetTypeId": 1,
            "workoutTargetTypeKey": "no.target",
            "displayOrder": 1,
        },
    }


def _repeat_group(
    rounds: int,
    steps: list[dict[str, Any]],
    step_order: int,
    skip_last_rest: bool,
) -> dict[str, Any]:
    return {
        "type": "RepeatGroupDTO",
        "stepOrder": step_order,
        "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
        "numberOfIterations": rounds,
        "workoutSteps": steps,
        "endCondition": {
            "conditionTypeId": 7,
            "conditionTypeKey": "iterations",
            "displayOrder": 7,
            "displayable": False,
        },
        "endConditionValue": float(rounds),
        "skipLastRestStep": skip_last_rest,
    }


def build_garmin_workout(
    title: str,
    page_id: str,
    sections: list[dict[str, Any]],
    source_text: str,
) -> tuple[dict[str, Any], str]:
    compact_page_id = page_id.replace("-", "").casefold()
    digest = hashlib.sha256((title + "\n" + source_text).encode("utf-8")).hexdigest()[:HASH_LENGTH]
    page_marker = compact_page_id[:PAGE_MARKER_LENGTH]
    marker = f"[NTN:{page_marker}:{digest}]"
    workout_name = f"{title[:48].rstrip()} {marker}"[:100]

    steps: list[dict[str, Any]] = []
    step_order = 1
    for section in sections:
        rounds = section["rounds"]
        child_steps = [
            _exercise_step(item, section, step_order + index + 1)
            for index, item in enumerate(section["exercises"])
        ]
        has_rest = section.get("rest_seconds") is not None and section["rest_seconds"] > 0
        if has_rest and rounds > 1:
            child_steps.append(
                _rest_step(section["rest_seconds"], step_order + len(child_steps) + 1)
            )

        if rounds > 1:
            steps.append(
                _repeat_group(rounds, child_steps, step_order, skip_last_rest=has_rest)
            )
            step_order += len(child_steps) + 2
        else:
            for item in section["exercises"]:
                steps.append(_exercise_step(item, section, step_order))
                step_order += 1

    if not steps:
        raise ValueError("Aucune étape d'exercice Garmin n'a pu être créée.")

    note_parts = [
        f"Créé automatiquement depuis la page Notion {page_id}.",
        "Dans une ligne « N x exercice — référence », N est interprété comme le nombre de répétitions; "
        "le nombre après le tiret est conservé comme référence de la source.",
    ]
    for section in sections:
        if "emom" in section["title"].casefold():
            note_parts.append(
                f"{section['title']}: la montre compte les tours, mais ne déclenche pas "
                "un départ automatique toutes les minutes."
            )
    workout = {
        "workoutName": workout_name,
        "sportType": {
            "sportTypeId": 5,
            "sportTypeKey": "strength_training",
            "displayOrder": 5,
        },
        "estimatedDurationInSecs": 0,
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": {
                    "sportTypeId": 5,
                    "sportTypeKey": "strength_training",
                    "displayOrder": 5,
                },
                "workoutSteps": steps,
            }
        ],
        "description": " ".join(note_parts)[:1000],
    }
    return workout, marker


def all_garmin_workouts(garmin: Garmin) -> list[dict[str, Any]]:
    workouts: list[dict[str, Any]] = []
    start = 0
    page_size = 100
    while True:
        page = garmin.get_workouts(
            start=start,
            limit=page_size,
            my_workouts_only=True,
        )
        workouts.extend(page or [])
        if not page or len(page) < page_size:
            break
        start += page_size
    return workouts


def sync_page(
    notion: NotionClient,
    garmin: Garmin,
    page: dict[str, Any],
    existing_workouts: list[dict[str, Any]],
) -> bool:
    title = page_title(page) or "Renforcement"
    lines = page_content_lines(notion, page["id"])
    source_text = "\n".join(lines)
    sections = parse_sections(lines)
    if not sections:
        print(f"Pas de texte d'atelier exploitable dans « {title} » ; aucun envoi.")
        return False

    workout, marker = build_garmin_workout(title, page["id"], sections, source_text)
    page_marker = f"[NTN:{page['id'].replace('-', '').casefold()[:PAGE_MARKER_LENGTH]}:"
    existing = next(
        (
            item
            for item in existing_workouts
            if page_marker in item.get("workoutName", "")
        ),
        None,
    )

    if existing:
        if existing.get("workoutName") == workout["workoutName"]:
            print(f"Entraînement Garmin déjà à jour : « {title} ».")
            return False
        workout_id = existing.get("workoutId")
        if not workout_id:
            raise ValueError(f"Identifiant Garmin absent pour « {title} ».")
        garmin.update_workout(workout_id, workout)
        garmin.push_workout_to_device(workout_id=workout_id)
        existing["workoutName"] = workout["workoutName"]
        print(f"Entraînement Garmin mis à jour et envoyé à la montre : « {title} » ({marker}).")
        return True

    created = garmin.upload_workout(workout)
    workout_id = created.get("workoutId") if isinstance(created, dict) else None
    if not workout_id:
        raise ValueError(f"Garmin n'a pas renvoyé l'identifiant de « {title} ».")
    garmin.push_workout_to_device(workout_id=workout_id)
    existing_workouts.append(
        {"workoutId": workout_id, "workoutName": workout["workoutName"]}
    )
    print(f"Entraînement Garmin créé et envoyé à la montre : « {title} » ({marker}).")
    return True


def main() -> None:
    garmin, _ = get_garmin_client()
    notion, databases = get_notion_client()
    data_source_id = get_activity_data_source_id(notion, databases.activities)
    today = datetime.now().date()
    pages = upcoming_pages(notion, data_source_id, today)
    if not pages:
        print(f"Aucun entraînement de renforcement prévu dans les {LOOKAHEAD_DAYS} prochains jours.")
        return

    existing_workouts = all_garmin_workouts(garmin)
    for page in pages:
        planned_date = page_date(page) or "date inconnue"
        try:
            sync_page(notion, garmin, page, existing_workouts)
        except Exception as error:
            print(f"Échec de l'envoi de « {page_title(page)} » ({planned_date}) : {error}")


if __name__ == "__main__":
    main()
