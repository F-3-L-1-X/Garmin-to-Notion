from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timedelta
from typing import Any
import unicodedata
from zoneinfo import ZoneInfo

from garminconnect import Garmin
from garminconnect import exercises as garmin_exercises
from notion_client import Client as NotionClient

from src.helpers import get_garmin_client, get_notion_client

DATE_PROPERTY = "Date de la  séance"
TITLE_PROPERTY = "Nom"
DISCIPLINE_PROPERTY = "Discipline"
DONE_PROPERTY = "Terminé"
LOOKAHEAD_DAYS = max(1, min(int(os.getenv("GARMIN_WORKOUTS_LOOKAHEAD_DAYS", "14")), 90))
PAGE_MARKER_LENGTH = 16
HASH_LENGTH = 8
SYNC_TAG = re.compile(r"\[NTN:([a-f0-9]{16}):([a-f0-9]{8})(?::(P|S))?\]$", re.IGNORECASE)

SPORT_TYPES = {
    "running": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
    "cycling": {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2},
    "other": {"sportTypeId": 3, "sportTypeKey": "other", "displayOrder": 4},
    "swimming": {"sportTypeId": 4, "sportTypeKey": "swimming", "displayOrder": 3},
    "strength_training": {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5},
    "cardio_training": {"sportTypeId": 6, "sportTypeKey": "cardio_training", "displayOrder": 6},
    "yoga": {"sportTypeId": 7, "sportTypeKey": "yoga", "displayOrder": 7},
    "pilates": {"sportTypeId": 8, "sportTypeKey": "pilates", "displayOrder": 8},
    "hiit": {"sportTypeId": 9, "sportTypeKey": "hiit", "displayOrder": 9},
    "mobility": {"sportTypeId": 11, "sportTypeKey": "mobility", "displayOrder": 11},
}
DISCIPLINE_SPORTS = {
    "courseapied": "running",
    "velo": "cycling",
    "natation": "swimming",
    "renforcement": "strength_training",
    "hautducorps": "strength_training",
    "basducorps": "strength_training",
    "touslecorps": "strength_training",
    "yoga": "yoga",
    "mobilite": "mobility",
    "etirement": "mobility",
    "cardio": "cardio_training",
    "pilates": "pilates",
    "hiit": "hiit",
    "gymnastique": "other",
    "escalade": "other",
    "skidefond": "other",
    "marcherando": "other",
}
NON_WORKOUT_DISCIPLINES = {"repos", "massage"}

EXERCISE_LINE = re.compile(
    r"^(?P<reps>\d+)\s*[x×]\s*(?P<name>.+?)"
    r"(?:\s*[—–-]\s*(?P<reference>\d+))?$",
    re.IGNORECASE,
)
ATELIER_HEADER = re.compile(r"^(?:atelier\s+\d+\b|emom\b.*)", re.IGNORECASE)
SETS_LINE = re.compile(r"^s(?:é|e)ries?\s*[:：]\s*(\d+)\b", re.IGNORECASE)
NOTE_LINE = re.compile(r"^(?:notes?|remarques?)\s*[:：]\s*(.*)$", re.IGNORECASE)
EMOM_DURATION = re.compile(r"\bemom\b.*?(\d+)\s*(?:min|minutes|mn)\b", re.IGNORECASE)
REST_TIME = re.compile(
    r"(?P<amount>\d+(?:[,.]\d+)?)\s*(?P<unit>minutes?|mins?|mn|secondes?|secs?|s)\b",
    re.IGNORECASE,
)


def normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    unaccented = "".join(character for character in decomposed if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", "", unaccented)


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


def page_disciplines(page: dict[str, Any]) -> list[str]:
    value = page.get("properties", {}).get(DISCIPLINE_PROPERTY, {})
    selections = value.get("multi_select") or []
    if not selections and value.get("select"):
        selections = [value["select"]]
    return [item.get("name", "").strip() for item in selections if item.get("name")]


def page_source_lines(page: dict[str, Any]) -> list[str]:
    value = page.get("properties", {}).get("Source", {})
    runs = value.get("rich_text") or value.get("title") or []
    source = "".join(item.get("plain_text", "") for item in runs)
    return [line.strip() for line in source.splitlines() if line.strip()]


def sport_for_disciplines(disciplines: list[str]) -> dict[str, Any] | None:
    normalized = [normalize_name(value) for value in disciplines]
    if any(value in NON_WORKOUT_DISCIPLINES for value in normalized):
        return None
    priority = (
        "courseapied", "velo", "natation", "yoga", "pilates", "hiit", "cardio",
        "mobilite", "etirement", "renforcement", "hautducorps", "basducorps",
        "touslecorps", "gymnastique", "escalade", "skidefond", "marcherando",
    )
    for discipline in priority:
        if discipline in normalized:
            return dict(SPORT_TYPES[DISCIPLINE_SPORTS[discipline]])
    return None


def _training_lines(lines: list[str]) -> list[str]:
    selected: list[str] = []
    active = False
    for raw_line in lines:
        line = raw_line.replace("\\.", ".").strip()
        normalized = normalize_name(line)
        if re.match(r"^(?:echauffement|warmup|seance|entrainement)", normalized):
            active = True
            selected.append(line)
            continue
        if re.match(r"^(?:retouraucalme|bilandelaseance|bilan)\b", normalized):
            active = False
            continue
        if active and line:
            selected.append(line)
    return selected


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


DURATION_TOKEN = re.compile(
    r"(?P<amount>\d+(?:[,.]\d+)?)\s*(?P<unit>minutes?|mins?|mn|secondes?|secs?|s\b|[’'′])\s*"
    r"(?P<label>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ_-]*)?",
    re.IGNORECASE,
)
REPEAT_TOKEN = re.compile(r"(?P<count>\d+)\s*[x×]\s*\((?P<body>[^)]*)\)", re.IGNORECASE)
SWIM_LINE = re.compile(
    r"^\s*(?P<distance>\d{2,5})\s*m(?:ètres?)?\s*[—–-]\s*(?P<stroke>.+?)\s*$",
    re.IGNORECASE,
)


def _fold_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _duration_seconds(match: re.Match[str]) -> int:
    amount = float(match.group("amount").replace(",", "."))
    unit = match.group("unit").casefold()
    if unit.startswith(("min", "mn", "’", "'", "′")):
        return max(1, round(amount * 60))
    return max(1, round(amount))


def _clean_plan_line(value: str) -> str:
    line = value.replace("\\.", ".").strip()
    line = re.sub(r"^(?:[-*•]\s+|[☐☑]\s*)", "", line).strip()
    return re.sub(r"\s+", " ", line)


def _step_type(kind: str) -> dict[str, Any]:
    values = {
        "warmup": (1, "warmup", 1),
        "cooldown": (2, "cooldown", 2),
        "interval": (3, "interval", 3),
        "recovery": (4, "recovery", 4),
        "rest": (5, "rest", 5),
    }
    step_id, key, display_order = values.get(kind, values["interval"])
    return {"stepTypeId": step_id, "stepTypeKey": key, "displayOrder": display_order}


def _time_or_manual_step(
    step_order: int,
    description: str,
    duration_seconds: int | None = None,
    kind: str = "interval",
) -> dict[str, Any]:
    step: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": step_order,
        "stepType": _step_type(kind),
        "targetType": {
            "workoutTargetTypeId": 1,
            "workoutTargetTypeKey": "no.target",
            "displayOrder": 1,
        },
        "description": description[:500],
    }
    if duration_seconds is None:
        step["endCondition"] = {
            "conditionTypeId": 1,
            "conditionTypeKey": "lap.button",
            "displayOrder": 1,
            "displayable": True,
        }
    else:
        step["endCondition"] = {
            "conditionTypeId": 2,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        }
        step["endConditionValue"] = float(duration_seconds)
    return step


def _distance_step(
    step_order: int,
    distance_meters: int,
    description: str,
    kind: str,
) -> dict[str, Any]:
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": step_order,
        "stepType": _step_type(kind),
        "endCondition": {
            "conditionTypeId": 3,
            "conditionTypeKey": "distance",
            "displayOrder": 3,
            "displayable": True,
        },
        "endConditionValue": float(distance_meters),
        "targetType": {
            "workoutTargetTypeId": 1,
            "workoutTargetTypeKey": "no.target",
            "displayOrder": 1,
        },
        "description": description[:500],
    }


def _step_kind(label: str, sport_key: str) -> str:
    normalized = normalize_name(label)
    if any(word in normalized for word in ("echauffement", "warmup", "miseenselle")):
        return "warmup"
    if any(word in normalized for word in ("retouraucalme", "cooldown")):
        return "cooldown"
    if any(word in normalized for word in ("recuperation", "repos", "marche")):
        return "recovery"
    if sport_key == "cycling" and normalized in {"l", "facile", "low"}:
        return "recovery"
    if sport_key == "running" and normalized in {"m", "walk"}:
        return "recovery"
    return "interval"


def _time_segment(
    match: re.Match[str],
    full_line: str,
    sport_key: str,
) -> dict[str, Any]:
    label = (match.group("label") or "").strip()
    if label.casefold() in {"de", "du", "la", "le", "pour"}:
        suffix = full_line[match.end():].strip(" ,;:+-")
        label = suffix.split()[0] if suffix else ""
    context = _fold_text(full_line)
    if not label and "echauffement" in context:
        label = "Échauffement"
    elif not label and any(word in context for word in ("retour au calme", "cooldown")):
        label = "Retour au calme"
    elif not label:
        prefix = re.split(r"[+;/]", full_line[:match.start()])[-1].strip(" ():")
        label = prefix.split()[-1] if prefix else "Effort"
    return {
        "duration_seconds": _duration_seconds(match),
        "kind": _step_kind(label, sport_key),
        "description": label or "Étape",
    }


def _cardio_tokens(line: str, sport_key: str) -> list[dict[str, Any]]:
    groups = list(REPEAT_TOKEN.finditer(line))
    tokens: list[tuple[int, str, Any]] = [
        (group.start(), "repeat", group) for group in groups
    ]
    for match in DURATION_TOKEN.finditer(line):
        if not any(group.start() <= match.start() < group.end() for group in groups):
            tokens.append((match.start(), "time", match))
    tokens.sort(key=lambda item: item[0])

    parsed: list[dict[str, Any]] = []
    for _, token_type, token in tokens:
        if token_type == "repeat":
            inner = [
                _time_segment(match, token.group("body"), sport_key)
                for match in DURATION_TOKEN.finditer(token.group("body"))
            ]
            if inner:
                parsed.append({"repeat": int(token.group("count")), "steps": inner})
        else:
            parsed.append(_time_segment(token, line, sport_key))
    return parsed


def _parse_cardio_steps(lines: list[str], sport_key: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    order = 1
    for raw_line in lines:
        line = _clean_plan_line(raw_line)
        if not line or not DURATION_TOKEN.search(line):
            continue
        search_line = _fold_text(line)
        if sport_key == "running":
            relevant = re.search(
                r"\b(?:m|c|course|marche|footing|echauffement|warm.?up|cooldown|recuperation|fractionne)\b",
                search_line,
                re.IGNORECASE,
            )
        else:
            relevant = re.search(
                r"\b(?:v|l|warm.?up|echauffement|cooldown|recuperation|facile|tempo|intervalle|effort)\b",
                line,
                re.IGNORECASE,
            )
        if not relevant:
            continue
        for item in _cardio_tokens(line, sport_key):
            if "repeat" in item:
                children = [
                    _time_or_manual_step(
                        index,
                        child["description"],
                        child["duration_seconds"],
                        child["kind"],
                    )
                    for index, child in enumerate(item["steps"], start=1)
                ]
                steps.append(_repeat_group(
                    max(1, item["repeat"]), children, order, skip_last_rest=False
                ))
                order += len(children) + 2
            else:
                steps.append(_time_or_manual_step(
                    order,
                    item["description"],
                    item["duration_seconds"],
                    item["kind"],
                ))
                order += 1
    return steps


def _parse_swim_steps(lines: list[str]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    order = 1
    section_kind = "interval"
    for raw_line in lines:
        line = _clean_plan_line(raw_line)
        normalized = normalize_name(line)
        if "echauffement" in normalized:
            section_kind = "warmup"
        elif "retouraucalme" in normalized:
            section_kind = "cooldown"
        elif "seanceprincipale" in normalized or normalized == "seance":
            section_kind = "interval"

        match = SWIM_LINE.match(line)
        if match:
            steps.append(_distance_step(
                order,
                int(match.group("distance")),
                f"{match.group('stroke').strip()} · {line}",
                section_kind,
            ))
            order += 1
        elif re.match(r"^repos\s*[:：]", line, re.IGNORECASE) and steps:
            seconds = _parse_rest_seconds(line)
            if seconds:
                steps.append(_time_or_manual_step(order, line, seconds, "recovery"))
                order += 1
    return steps


def _numbered_routine_steps(lines: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    active = False
    for raw_line in lines:
        line = _clean_plan_line(raw_line)
        normalized = normalize_name(line)
        if re.match(r"^(?:echauffement|warmup|seance|entrainement)\b", normalized):
            active = True
            continue
        if re.match(r"^(?:retouraucalme|bilandelaseance|bilan)\b", normalized):
            if current:
                items.append(current)
                current = None
            active = False
            continue
        if not active:
            continue
        match = re.match(r"^\d+\s*[.)]\s*(.+)$", line)
        if match:
            if current:
                items.append(current)
            title = match.group(1).strip()
            if normalize_name(title) in {"echauffement", "seanceprincipale", "retouraucalme"}:
                current = None
                continue
            current = {"title": title, "details": ""}
        elif current and line:
            current["details"] = (current["details"] + " " + line).strip()
    if current:
        items.append(current)

    steps: list[dict[str, Any]] = []
    for order, item in enumerate(items, start=1):
        description = item["title"]
        if item["details"]:
            description += " — " + item["details"]
        durations = list(DURATION_TOKEN.finditer(description))
        duration = None
        lower = description.casefold()
        complex_timing = any(word in lower for word in (
            "chaque côté", "chaque cote", "chaque jambe", "série", "series",
            "recommencer", "pause",
        ))
        if len(durations) == 1 and not complex_timing:
            duration = _duration_seconds(durations[0])
        steps.append(_time_or_manual_step(order, description, duration))
    return steps


def _generic_prompt_steps(lines: list[str]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for raw_line in lines:
        line = _clean_plan_line(raw_line)
        normalized = normalize_name(line)
        if not line or normalized in {
            "echauffement", "seance", "seanceprincipale", "retouraucalme"
        } or normalized.endswith("retouraucalme"):
            continue
        if line.casefold().startswith(("source:", "distance totale", "consigne deload")):
            continue
        match = DURATION_TOKEN.search(line)
        duration = _duration_seconds(match) if match else None
        steps.append(_time_or_manual_step(len(steps) + 1, line, duration))
    return steps


def _estimated_seconds(steps: list[dict[str, Any]]) -> int:
    total = 0
    for step in steps:
        if step.get("type") == "RepeatGroupDTO":
            total += _estimated_seconds(step.get("workoutSteps", [])) * int(step.get("numberOfIterations", 1))
        elif step.get("endCondition", {}).get("conditionTypeKey") == "time":
            total += int(step.get("endConditionValue") or 0)
    return total


def _generic_workout(
    title: str,
    page_id: str,
    discipline_text: str,
    source_text: str,
    sport_type: dict[str, Any],
    steps: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    if not steps:
        raise ValueError("Aucune consigne exploitable dans cette séance.")
    page_marker = page_id.replace("-", "").casefold()[:PAGE_MARKER_LENGTH]
    digest = hashlib.sha256(
        (title + "\n" + discipline_text + "\n" + source_text).encode("utf-8")
    ).hexdigest()[:HASH_LENGTH]
    marker = f"[NTN:{page_marker}:{digest}:P]"
    workout = {
        "workoutName": f"{title[:48].rstrip()} {marker}"[:100],
        "sportType": dict(sport_type),
        "estimatedDurationInSecs": _estimated_seconds(steps),
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": dict(sport_type),
            "workoutSteps": steps,
        }],
        "description": (
            f"Créé automatiquement depuis la séance Notion « {title} » "
            f"(discipline : {discipline_text}). Les étapes sans durée attendent une validation manuelle."
        )[:1000],
    }
    return workout, marker


def build_garmin_workout(
    title: str,
    page_id: str,
    disciplines: list[str],
    lines: list[str],
    source_lines: list[str],
    source_text: str,
) -> tuple[dict[str, Any], str]:
    sport_type = sport_for_disciplines(disciplines)
    if sport_type is None:
        raise ValueError("Cette discipline n'est pas un entraînement à envoyer sur la montre.")
    sport_key = sport_type["sportTypeKey"]
    discipline_text = ", ".join(disciplines)
    plan_lines = _training_lines(lines) + source_lines

    if sport_key == "strength_training":
        sections = parse_sections(lines + source_lines)
        if sections:
            return _build_strength_workout(
                title, page_id, sections, discipline_text + "\n" + source_text
            )
        raise ValueError("Aucun atelier de renforcement exploitable n'a été trouvé.")

    if sport_key in {"running", "cycling"}:
        if sport_key == "cycling" and any(
            re.search(r"\b(?:repeat|repeter|repete)\b", _fold_text(line))
            and not REPEAT_TOKEN.search(line)
            for line in plan_lines
        ):
            raise ValueError(
                "Le programme vélo demande une répétition sans indiquer combien de fois. "
                "Précise le nombre, par exemple 4x (5 min effort / 2 min récupération)."
            )
        steps = _parse_cardio_steps(plan_lines, sport_key)
    elif sport_key == "swimming":
        steps = _parse_swim_steps(plan_lines)
    else:
        steps = _numbered_routine_steps(lines)
        if not steps:
            steps = _generic_prompt_steps(plan_lines)
    return _generic_workout(
        title, page_id, discipline_text, source_text, sport_type, steps
    )


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


def _build_strength_workout(
    title: str,
    page_id: str,
    sections: list[dict[str, Any]],
    source_text: str,
) -> tuple[dict[str, Any], str]:
    compact_page_id = page_id.replace("-", "").casefold()
    digest = hashlib.sha256((title + "\n" + source_text).encode("utf-8")).hexdigest()[:HASH_LENGTH]
    page_marker = compact_page_id[:PAGE_MARKER_LENGTH]
    marker = f"[NTN:{page_marker}:{digest}:P]"
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
    title = page_title(page) or "Séance"
    disciplines = page_disciplines(page)
    if sport_for_disciplines(disciplines) is None:
        print(f"Séance ignorée sur la montre (repos, massage ou discipline inconnue) : « {title} ».")
        return False
    lines = page_content_lines(notion, page["id"])
    source_lines = page_source_lines(page)
    source_text = "\n".join(lines + source_lines)
    try:
        workout, marker = build_garmin_workout(
            title, page["id"], disciplines, lines, source_lines, source_text
        )
    except ValueError as error:
        print(f"« {title} » non envoyée : {error}")
        return False
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
        workout_id = existing.get("workoutId")
        if not workout_id:
            raise ValueError(f"Identifiant Garmin absent pour « {title} ».")
        existing_tag = SYNC_TAG.search(existing.get("workoutName", ""))
        current_tag = SYNC_TAG.search(workout["workoutName"])
        if (
            existing_tag
            and current_tag
            and existing_tag.group(2) == current_tag.group(2)
            and existing_tag.group(3) == "S"
        ):
            print(f"Entraînement Garmin déjà à jour : « {title} ».")
            return False

        garmin.update_workout(workout_id, workout)
        garmin.push_workout_to_device(workout_id=workout_id)
        sent_workout = dict(workout)
        sent_workout["workoutName"] = workout["workoutName"].replace(":P]", ":S]")
        garmin.update_workout(workout_id, sent_workout)
        existing["workoutName"] = sent_workout["workoutName"]
        print(f"Entraînement Garmin mis à jour et envoyé à la montre : « {title} » ({marker}).")
        return True

    created = garmin.upload_workout(workout)
    workout_id = created.get("workoutId") if isinstance(created, dict) else None
    if not workout_id:
        raise ValueError(f"Garmin n'a pas renvoyé l'identifiant de « {title} ».")
    garmin.push_workout_to_device(workout_id=workout_id)
    sent_workout = dict(workout)
    sent_workout["workoutName"] = workout["workoutName"].replace(":P]", ":S]")
    garmin.update_workout(workout_id, sent_workout)
    existing_workouts.append(
        {"workoutId": workout_id, "workoutName": sent_workout["workoutName"]}
    )
    print(f"Entraînement Garmin créé et envoyé à la montre : « {title} » ({marker}).")
    return True


def main() -> None:
    garmin, _ = get_garmin_client()
    notion, databases = get_notion_client()
    data_source_id = get_activity_data_source_id(notion, databases.activities)
    today = datetime.now(ZoneInfo("Europe/Paris")).date()
    pages = upcoming_pages(notion, data_source_id, today)
    if not pages:
        print(f"Aucune séance prévue dans les {LOOKAHEAD_DAYS} prochains jours.")
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
