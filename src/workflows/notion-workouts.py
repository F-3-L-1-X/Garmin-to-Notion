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
SYNC_TAG = re.compile(r"\[NTN:([a-f0-9]{16}):([a-f0-9]{8})(?::(P|S|C|F))?\]$", re.IGNORECASE)

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
    "aikido": "other",
    "wingsuit": "other",
    "skiraquette": "other",
    "skideraquette": "other",
    "raquette": "other",
    "ski": "other",
}
NON_WORKOUT_DISCIPLINES = {"repos", "massage"}

EXERCISE_LINE = re.compile(
    r"^(?P<reps>\d+)\s*[x×]\s*(?P<name>.+?)"
    r"(?:\s*[—–-]\s*(?P<reference>\d+))?$",
    re.IGNORECASE,
)
ATELIER_HEADER = re.compile(r"^(?:atelier(?:\s+\d+)?\b.*|(?:emom|emon)\b.*|(?:échauffement|echauffement|warm[ -]?up|mise en selle|retour au calme|cooldown|séance principale|seance principale|séance|seance)\b.*)$", re.IGNORECASE)
REVERSED_EXERCISE_LINE = re.compile(r"^(?P<name>.+?)\s*[x×]\s*(?P<reps>\d+(?:/\d+)?)$", re.IGNORECASE)
SETS_LINE = re.compile(
    r"^(?:s(?:é|e)ries?\s*[:：]\s*(?P<after>\d+)|[x×]\s*(?P<times>\d+)\s*s(?:é|e)ries?|\b(?P<before>\d+)\s*s(?:é|e)ries?)\s*$",
    re.IGNORECASE,
)
NOTE_LINE = re.compile(r"^(?:notes?|remarques?)\s*[:：]\s*(.*)$", re.IGNORECASE)
EMOM_DURATION = re.compile(r"\bem(?:om|on)\b.*?(\d+)\s*(?:min|minutes|mn)\b", re.IGNORECASE)
TIME_TOKEN_PATTERN = (
    r"""(?P<amount>\d+(?:[,.]\d+)?)\s*"""
    r"""(?P<unit>minutes?|mins?|mn\b|secondes?|secs?|s\b|[’'′]{2}|["”“″]|[’'′])"""
    r"""(?:\s*(?P<second_amount>\d+(?:[,.]\d+)?)\s*"""
    r"""(?P<second_unit>secondes?|secs?|s\b|["”“″]|[’'′]{2}))?"""
)
REST_TIME = re.compile(TIME_TOKEN_PATTERN, re.IGNORECASE)


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
    runs = body.get("rich_text") or body.get("title") or body.get("caption") or []
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
                    block_type = block.get("type")
                    is_list_item = block_type in {
                        "bulleted_list_item",
                        "numbered_list_item",
                        "to_do",
                    }
                    block_lines = [line.strip() for line in text.splitlines() if line.strip()]
                    for index, line in enumerate(block_lines):
                        if is_list_item and index == 0:
                            line = "- " + line
                        lines.append(line)
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
    lines: list[str] = []
    properties = page.get("properties", {})
    for name, value in properties.items():
        if name == TITLE_PROPERTY:
            continue
        runs = value.get("rich_text") or []
        if runs:
            text = "".join(item.get("plain_text", "") for item in runs).strip()
            if text:
                lines.append(f"{name} : {text}")
            continue
        selected = value.get("select") or value.get("status")
        if selected and selected.get("name"):
            lines.append(f"{name} : {selected['name']}")
            continue
        choices = value.get("multi_select") or []
        names = [item.get("name", "").strip() for item in choices if item.get("name")]
        if names:
            lines.append(f"{name} : {', '.join(names)}")
    return lines


def sport_for_disciplines(disciplines: list[str]) -> dict[str, Any] | None:
    normalized = [normalize_name(value) for value in disciplines]
    if any(value in NON_WORKOUT_DISCIPLINES for value in normalized):
        return None
    priority = (
        "courseapied", "velo", "natation", "yoga", "pilates", "hiit", "cardio",
        "mobilite", "etirement", "renforcement", "hautducorps", "basducorps",
        "touslecorps", "gymnastique", "escalade", "skidefond", "marcherando",
        "aikido", "wingsuit", "skiraquette", "skideraquette", "raquette", "ski",
    )
    for discipline in priority:
        if discipline in normalized:
            return dict(SPORT_TYPES[DISCIPLINE_SPORTS[discipline]])
    return None


def _is_list_item(line: str) -> bool:
    return line.lstrip().startswith(("-", "*", "•", "☐", "☑"))


def _training_lines(lines: list[str]) -> list[str]:
    selected: list[str] = []
    active = False
    skip_context_value = False

    for raw_line in lines:
        list_item = _is_list_item(raw_line)
        line = _clean_plan_line(raw_line)
        if not line:
            continue

        numbered = re.match(r"^\d+\s*[.)]\s*(?P<text>.+)$", line)
        numbered_text = numbered.group("text").strip() if numbered else ""
        heading_text = numbered_text if numbered and _is_plan_heading(numbered_text) else line
        normalized = normalize_name(line)
        heading_normalized = normalize_name(heading_text)

        if skip_context_value:
            skip_context_value = False
            objective_cooldown = (
                not active and heading_normalized in {"retouraucalme", "cooldown"}
            )
            if not _is_plan_heading(heading_text) or objective_cooldown:
                continue

        if heading_normalized.startswith("distancetotale"):
            break
        if (
            not numbered
            and re.match(r"^(?:retouraucalme|cooldown|bilandelaseance|bilan)\b", normalized)
        ):
            break

        if _is_source_line(line) or normalized.startswith(
            ("objectif", "discipline", "douleur", "intensite", "localisation")
        ):
            _, separator, value = line.partition(":")
            if not separator:
                _, separator, value = line.partition("：")
            skip_context_value = not separator or not value.strip()
            continue

        if numbered and _is_plan_heading(numbered_text):
            active = True
            selected.append(numbered_text)
            continue
        if _is_plan_heading(line):
            active = True
            selected.append(f"- {line}" if list_item else line)
            continue
        if _is_cycle_marker(line):
            continue
        if active:
            selected.append(f"- {line}" if list_item else line)
        elif (
            list_item
            or DURATION_TOKEN.search(line)
            or DISTANCE_TOKEN.search(line)
            or REPEAT_TOKEN.search(line)
            or re.search(r"^\d+\s*[x×]\s*\d+", line, re.IGNORECASE)
            or re.search(r"\bx\s*\d+\b", line, re.IGNORECASE)
        ):
            active = True
            selected.append(f"- {line}" if list_item else line)

    return selected




def _plan_section_kind(line: str) -> str | None:
    normalized = normalize_name(line.strip().rstrip(":： "))
    if normalized.startswith(("echauffement", "warmup", "miseenselle")):
        return "warmup"
    if normalized.startswith(("retouraucalme", "cooldown")):
        return "cooldown"
    if normalized.startswith(("recuperation", "repos")):
        return "recovery"
    if normalized.startswith(("cardio", "seance", "entrainement", "fractionne", "atelier", "emom", "emon")):
        return "interval"
    return None


def _is_plan_heading(line: str) -> bool:
    normalized = normalize_name(line.strip().rstrip(":： "))
    return (
        _plan_section_kind(line) is not None
        or normalized.startswith(("specifique", "specific", "seanceprincipale"))
    )


def _is_source_line(line: str) -> bool:
    return bool(re.match(r"^sources?\s*[:：]?", line.strip(), re.IGNORECASE))


def _is_cycle_marker(line: str) -> bool:
    return bool(re.match(r"^\(?\d+\s*(?:cycles?|tours?)\)?$", line.strip(), re.IGNORECASE))


def _parse_rest_seconds(line: str) -> int | None:
    match = REST_TIME.search(line)
    return _duration_seconds(match) if match else None


def _section_item_summary(item: dict[str, Any]) -> str:
    if item.get("kind") == "reps":
        return f"{item['reps']} x {item['name']}"
    return item.get("description", "").strip()


def _section_summary(section: dict[str, Any]) -> str:
    items = [
        summary
        for summary in (_section_item_summary(item) for item in section["exercises"])
        if summary
    ]
    details = " · ".join(items)
    return f"{section['title']} — {details}" if details else section["title"]


def parse_sections(lines: list[str]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_exercise: dict[str, Any] | None = None
    collecting_note = False

    def save_section() -> None:
        if current and (current["exercises"] or current["rest_seconds"] is not None):
            duration_match = EMOM_DURATION.search(current["title"])
            rounds = current["sets"]
            if rounds is None:
                rounds = int(duration_match.group(1)) if duration_match else 1
            current["rounds"] = max(1, rounds)
            current["duration_seconds"] = int(duration_match.group(1)) * 60 if duration_match else None
            current["notes"] = " ".join(current["notes"]).strip()
            current["summary"] = _section_summary(current)
            sections.append(current)

    for raw_line in lines:
        line = re.sub(r"^(?:[-*•]\s+|[☐☑]\s*)", "", raw_line).strip()
        recovery_header = bool(
            re.match(r"^(?:récupération|recuperation|repos)\b", line, re.IGNORECASE)
        )
        if ATELIER_HEADER.match(line) or recovery_header:
            save_section()
            current = {
                "title": line.rstrip(":： "),
                "sets": None,
                "rest_seconds": _parse_rest_seconds(line) if recovery_header else None,
                "exercises": [],
                "notes": [],
            }
            current_exercise = None
            collecting_note = False
            continue
        if current is None:
            continue

        if _is_source_line(line):
            current_exercise = None
            collecting_note = False
            continue

        set_match = SETS_LINE.match(line)
        if set_match:
            current["sets"] = int(set_match.group("after") or set_match.group("times") or set_match.group("before"))
            current_exercise = None
            collecting_note = False
            continue

        if re.match(r"^(?:récupération|recuperation|repos)\s*(?:[:：]\s*)?", line, re.IGNORECASE):
            current["rest_seconds"] = _parse_rest_seconds(line)
            current_exercise = None
            collecting_note = False
            continue

        note_match = NOTE_LINE.match(line)
        if note_match:
            if note_match.group(1).strip():
                current["notes"].append(note_match.group(1).strip())
            current_exercise = None
            collecting_note = True
            continue

        exercise_match = EXERCISE_LINE.match(line)
        if exercise_match:
            current_exercise = {
                "kind": "reps",
                "reps": int(exercise_match.group("reps")),
                "name": exercise_match.group("name").strip(),
                "reference": exercise_match.group("reference"),
                "explanation": [],
            }
            current["exercises"].append(current_exercise)
            collecting_note = False
            continue

        reversed_exercise = REVERSED_EXERCISE_LINE.match(line)
        if reversed_exercise:
            reps_text = reversed_exercise.group("reps")
            if "/" in reps_text:
                item = {"kind": "manual", "description": line, "explanation": []}
            else:
                item = {
                    "kind": "reps",
                    "reps": int(reps_text),
                    "name": reversed_exercise.group("name").strip(),
                    "reference": None,
                    "explanation": [],
                }
            current["exercises"].append(item)
            current_exercise = item
            collecting_note = False
            continue

        duration_match = DURATION_TOKEN.search(line)
        if duration_match:
            item = {
                "kind": "time",
                "description": line,
                "duration_seconds": _duration_seconds(duration_match),
                "explanation": [],
            }
            current["exercises"].append(item)
            current_exercise = item
            collecting_note = False
            continue

        if collecting_note:
            current["notes"].append(line)
        elif current_exercise is not None:
            current_exercise["explanation"].append(line)
        elif line and not re.match(r"^(?:objectif|discipline|source)\b", line, re.IGNORECASE):
            item = {"kind": "manual", "description": line, "explanation": []}
            current["exercises"].append(item)
            current_exercise = item
            collecting_note = False

    save_section()
    return sections

DURATION_TOKEN = re.compile(
    TIME_TOKEN_PATTERN + r"""\s*(?P<label>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ_-]*)?""",
    re.IGNORECASE,
)
DISTANCE_TOKEN = re.compile(
    r"""(?P<distance>\d+(?:[,.]\d+)?)\s*(?P<unit>kilomètres?|kilometres?|km|mètres?|metres?|m)\b""",
    re.IGNORECASE,
)
REPEAT_TOKEN = re.compile(r"(?P<count>\d+)\s*[x×]\s*\((?P<body>[^)]*)\)", re.IGNORECASE)
DURATION_RANGE_TOKEN = re.compile(
    r"""(?P<first>\d+(?:[,.]\d+)?)\s*"""
    r"""(?P<first_unit>minutes?|mins?|mn\b|secondes?|secs?|s\b|[’'′]{1,2}|["”“″])?\s*"""
    r"""(?:[-–—]|à|a|to)\s*"""
    r"""(?P<second>\d+(?:[,.]\d+)?)\s*"""
    r"""(?P<second_unit>minutes?|mins?|mn\b|secondes?|secs?|s\b|[’'′]{1,2}|["”“″])?""",
    re.IGNORECASE,
)
SIMPLE_REPEAT_TOKEN = re.compile(
    r"""(?P<count>\d+)\s*[x×]\s*(?P<body>\d+(?:[,.]\d+)?\s*"""
    r"""(?:kilomètres?|kilometres?|km|mètres?|metres?|m|minutes?|mins?|mn\b|"""
    r"""secondes?|secs?|s\b|[’'′]{1,2}|["”“″])"""
    r"""(?:\s+[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ_-]*)?)""",
    re.IGNORECASE,
)
SWIM_LINE = re.compile(
    r"^\s*(?P<distance>\d{2,5})\s*m(?:ètres?)?(?:\s*[—–-]\s*|\s+)?(?P<stroke>.*?)\s*$",
    re.IGNORECASE,
)
SWIM_REPEAT_LINE = re.compile(
    r"^\s*(?P<count>\d+)\s*[x×]\s*(?P<body>.+?)\s*$",
    re.IGNORECASE,
)


def _fold_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _duration_seconds(match: re.Match[str]) -> int:
    raw_amount = match.group("amount").replace(",", ".")
    amount = float(raw_amount)
    unit = match.group("unit").casefold()
    second_amount = match.groupdict().get("second_amount")

    is_minutes = unit.startswith(("min", "mn")) or unit in {"’", "'", "′"}
    is_quote_seconds = unit in {'"', "”", "“", "″", "''", "’’", "′′"}

    if is_minutes:
        if second_amount is not None:
            whole_minutes = int(amount)
            return max(1, round(whole_minutes * 60 + float(second_amount.replace(",", "."))))
        if re.fullmatch(r"\d+[,.]\d{2}", raw_amount):
            whole, seconds = re.split(r"[,.]", raw_amount)
            if int(seconds) < 60:
                return max(1, int(whole) * 60 + int(seconds))
        return max(1, round(amount * 60))

    if is_quote_seconds and re.fullmatch(r"\d+[,.]\d{2}", raw_amount):
        whole, seconds = re.split(r"[,.]", raw_amount)
        if int(seconds) < 60:
            return max(1, int(whole) * 60 + int(seconds))
    if second_amount is not None:
        return max(1, round(amount + float(second_amount.replace(",", "."))))
    return max(1, round(amount))




def _clean_plan_line(value: str) -> str:
    line = value.replace("\\.", ".").strip()
    line = re.sub(r"^(?:[-*•]\s+|[☐☑]\s*)", "", line).strip()
    line = line.replace("**", "").replace("__", "")
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
        "description": description[:200],
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
        "description": description[:200],
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
    default_kind: str = "interval",
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
    left = full_line[:match.start()]
    right = full_line[match.end():]
    left_boundary = max(left.rfind("+"), left.rfind("/"), left.rfind(";"))
    right_boundaries = [position for position in (right.find("+"), right.find("/"), right.find(";")) if position >= 0]
    segment = (
        left[left_boundary + 1:]
        + match.group(0)
        + right[:min(right_boundaries) if right_boundaries else len(right)]
    ).strip(" ()+/;")
    if sport_key == "running" and label.casefold() in {"c", "m"}:
        step_name = "Course" if label.casefold() == "c" else "Marche"
        segment = f"{step_name} · {segment}"
    kind = _step_kind(label, sport_key)
    if default_kind in {"warmup", "cooldown", "recovery"}:
        kind = default_kind
    elif kind == "interval" and default_kind != "interval":
        kind = default_kind
    return {
        "duration_seconds": _duration_seconds(match),
        "kind": kind,
        "description": segment or full_line.strip() or label or "Étape",
    }


def _is_explicit_instruction(line: str) -> bool:
    return bool(
        DURATION_TOKEN.search(line)
        or REPEAT_TOKEN.search(line)
        or re.search(r"\bx\s*\d+\b", line, re.IGNORECASE)
        or EXERCISE_LINE.match(line)
        or SWIM_LINE.match(line)
    )


def _append_step_explanation(step: dict[str, Any], explanation: str) -> None:
    if step.get("type") == "RepeatGroupDTO":
        for child in step.get("workoutSteps", []):
            _append_step_explanation(child, explanation)
        return
    description = step.get("description", "").strip()
    if description:
        step["description"] = f"{description} — {explanation}"[:200]


def _distance_segment(
    match: re.Match[str],
    full_line: str,
    sport_key: str,
    default_kind: str = "interval",
) -> dict[str, Any]:
    unit = normalize_name(match.group("unit"))
    amount = float(match.group("distance").replace(",", "."))
    distance_meters = round(amount * 1000) if unit in {"km", "kilometre", "kilometres"} else round(amount)
    left = full_line[:match.start()]
    right = full_line[match.end():]
    left_boundary = max(left.rfind("+"), left.rfind("/"), left.rfind(";"))
    right_boundaries = [position for position in (right.find("+"), right.find("/"), right.find(";")) if position >= 0]
    segment = (
        left[left_boundary + 1:]
        + match.group(0)
        + right[:min(right_boundaries) if right_boundaries else len(right)]
    ).strip(" ()+/;")
    kind = _step_kind(full_line, sport_key)
    if kind == "interval" and default_kind != "interval":
        kind = default_kind
    return {
        "distance_meters": max(1, distance_meters),
        "kind": kind,
        "description": segment or full_line.strip() or "Distance",
    }


def _duration_range_segment(
    match: re.Match[str],
    full_line: str,
    sport_key: str,
    default_kind: str,
) -> dict[str, Any]:
    first_unit = match.group("first_unit") or match.group("second_unit")
    second_unit = match.group("second_unit") or match.group("first_unit")
    first_match = DURATION_TOKEN.fullmatch(match.group("first") + first_unit)
    second_match = DURATION_TOKEN.fullmatch(match.group("second") + second_unit)
    seconds = 0
    if first_match and second_match:
        seconds = round(
            (_duration_seconds(first_match) + _duration_seconds(second_match)) / 2
        )
    kind = _step_kind(full_line, sport_key)
    if default_kind in {"warmup", "cooldown", "recovery"}:
        kind = default_kind
    elif kind == "interval" and default_kind != "interval":
        kind = default_kind
    return {
        "duration_seconds": max(1, seconds),
        "kind": kind,
        "description": full_line.strip(),
    }


def _cardio_tokens(
    line: str,
    sport_key: str,
    default_kind: str = "interval",
) -> list[dict[str, Any]]:
    groups = list(REPEAT_TOKEN.finditer(line))
    ranges = [
        match
        for match in DURATION_RANGE_TOKEN.finditer(line)
        if (match.group("first_unit") or match.group("second_unit"))
        and not any(group.start() <= match.start() < group.end() for group in groups)
    ]
    simple_groups = [
        group
        for group in SIMPLE_REPEAT_TOKEN.finditer(line)
        if not any(existing.start() <= group.start() < existing.end() for existing in groups)
    ]
    repeat_groups = sorted(groups + simple_groups, key=lambda group: group.start())
    tokens: list[tuple[int, str, Any]] = [
        (group.start(), "repeat", group) for group in repeat_groups
    ]
    tokens.extend(
        (match.start(), "range", (match, line)) for match in ranges
    )

    def add_measures(text: str, offset: int, excluded: list[re.Match[str]]) -> None:
        for match in DURATION_TOKEN.finditer(text):
            if not any(group.start() <= match.start() < group.end() for group in excluded):
                tokens.append((offset + match.start(), "time", (match, text)))
        for match in DISTANCE_TOKEN.finditer(text):
            if not any(group.start() <= match.start() < group.end() for group in excluded):
                tokens.append((offset + match.start(), "distance", (match, text)))

    add_measures(line, 0, repeat_groups + ranges)
    tokens.sort(key=lambda item: item[0])

    parsed: list[dict[str, Any]] = []
    for _, token_type, token in tokens:
        if token_type == "range":
            match, source = token
            parsed.append(
                _duration_range_segment(match, source, sport_key, default_kind)
            )
        elif token_type == "repeat":
            body = token.group("body")
            measures: list[tuple[int, str, Any]] = []
            for match in DURATION_TOKEN.finditer(body):
                measures.append((match.start(), "time", match))
            for match in DISTANCE_TOKEN.finditer(body):
                measures.append((match.start(), "distance", match))
            measures.sort(key=lambda item: item[0])
            children = []
            for _, child_type, child_match in measures:
                if child_type == "time":
                    children.append(_time_segment(child_match, body, sport_key, default_kind))
                else:
                    children.append(_distance_segment(child_match, body, sport_key, default_kind))
            if not children:
                children.append({
                    "kind": default_kind,
                    "description": body.strip(),
                })
            parsed.append({"repeat": int(token.group("count")), "steps": children})
        elif token_type == "time":
            match, source = token
            parsed.append(_time_segment(match, source, sport_key, default_kind))
        else:
            match, source = token
            parsed.append(_distance_segment(match, source, sport_key, default_kind))
    return parsed




def _parse_cardio_steps(lines: list[str], sport_key: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    order = 1
    section_kind = "interval"
    section_title = ""
    last_instruction_steps: list[dict[str, Any]] = []
    for raw_line in lines:
        list_item = _is_list_item(raw_line)
        line = _clean_plan_line(raw_line)
        if not line:
            continue
        header_kind = _plan_section_kind(line)
        if header_kind:
            section_kind = header_kind
            section_title = line.rstrip(":： ")
            last_instruction_steps = []
            if header_kind == "recovery":
                seconds = _parse_rest_seconds(line)
                if seconds:
                    step = _time_or_manual_step(order, "Récupération", seconds, "recovery")
                    steps.append(step)
                    last_instruction_steps = [step]
                    order += 1
            continue
        if _is_plan_heading(line) or _is_cycle_marker(line) or _is_source_line(line):
            continue
        items = _cardio_tokens(line, sport_key, section_kind)
        if not items:
            if list_item or _is_explicit_instruction(line) or not last_instruction_steps:
                description = f"{section_title} · {line}" if section_title else line
                step = _time_or_manual_step(order, description, None, section_kind)
                steps.append(step)
                last_instruction_steps = [step]
                order += 1
            else:
                for step in last_instruction_steps:
                    _append_step_explanation(step, line)
            continue
        current_instruction_steps: list[dict[str, Any]] = []
        for item in items:
            if "repeat" in item:
                children = []
                for index, child in enumerate(item["steps"], start=1):
                    description = (
                        f"{section_title} · {child['description']}"
                        if section_title
                        else child["description"]
                    )
                    if child.get("distance_meters") is not None:
                        children.append(
                            _distance_step(
                                index,
                                child["distance_meters"],
                                description,
                                child["kind"],
                            )
                        )
                    else:
                        children.append(
                            _time_or_manual_step(
                                index,
                                description,
                                child.get("duration_seconds"),
                                child["kind"],
                            )
                        )
                group_note = section_title
                if children:
                    details = " · ".join(
                        child["description"].split(" · ", 1)[-1]
                        for child in children
                    )
                    group_note = f"{section_title} — {details}" if section_title else details
                group = _repeat_group(
                    max(1, item["repeat"]),
                    children,
                    order,
                    skip_last_rest=False,
                    description=group_note or None,
                )
                steps.append(group)
                current_instruction_steps.append(group)
                order += len(children) + 2
            else:
                description = (
                    f"{section_title} · {item['description']}"
                    if section_title
                    else item["description"]
                )
                if item.get("distance_meters") is not None:
                    step = _distance_step(
                        order,
                        item["distance_meters"],
                        description,
                        item["kind"],
                    )
                else:
                    step = _time_or_manual_step(
                        order,
                        description,
                        item.get("duration_seconds"),
                        item["kind"],
                    )
                steps.append(step)
                current_instruction_steps.append(step)
                order += 1
        last_instruction_steps = current_instruction_steps
    return steps
def _parse_swim_steps(lines: list[str]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    order = 1
    section_kind = "interval"
    section_title = ""
    last_instruction_steps: list[dict[str, Any]] = []

    for raw_line in lines:
        list_item = _is_list_item(raw_line)
        line = _clean_plan_line(raw_line)
        if not line:
            continue
        numbered_item = re.match(r"^\d+\s*[.)]\s*(?P<text>.+)$", line)
        if numbered_item:
            candidate = numbered_item.group("text").strip()
            if SWIM_LINE.match(candidate) or re.match(
                r"^(?:repos|récupération|recuperation)\b", candidate, re.IGNORECASE
            ):
                line = candidate
        normalized = normalize_name(line)
        header_kind = _plan_section_kind(line)
        if header_kind:
            section_kind = header_kind
            section_title = line.rstrip(":： ")
            last_instruction_steps = []
            if header_kind == "recovery":
                seconds = _parse_rest_seconds(line)
                if seconds:
                    step = _time_or_manual_step(order, "Récupération", seconds, "recovery")
                    steps.append(step)
                    last_instruction_steps = [step]
                    order += 1
            continue
        if _is_plan_heading(line) or _is_source_line(line) or _is_cycle_marker(line):
            continue

        repeat = SWIM_REPEAT_LINE.match(line)
        repeated_body = SWIM_LINE.match(repeat.group("body")) if repeat else None
        if repeat and repeated_body:
            stroke = repeated_body.group("stroke").strip()
            detail = f"{repeated_body.group('distance')} m"
            if stroke:
                detail += f" · {stroke}"
            description = f"{section_title} — {detail}" if section_title else detail
            child = _distance_step(1, int(repeated_body.group("distance")), description, section_kind)
            group = _repeat_group(
                int(repeat.group("count")),
                [child],
                order,
                skip_last_rest=False,
                description=f"{section_title} — {line}" if section_title else line,
            )
            steps.append(group)
            last_instruction_steps = [group]
            order += 1
            continue

        match = SWIM_LINE.match(line)
        if match:
            stroke = match.group("stroke").strip()
            description = f"{section_title} · {stroke} · {line}" if section_title and stroke else (
                f"{section_title} · {line}" if section_title else line
            )
            step = _distance_step(
                order,
                int(match.group("distance")),
                description,
                section_kind,
            )
            steps.append(step)
            last_instruction_steps = [step]
            order += 1
            continue

        if re.match(r"^(?:repos|récupération|recuperation)\s*[:：]?", line, re.IGNORECASE):
            seconds = _parse_rest_seconds(line)
            if seconds:
                step = _time_or_manual_step(order, "Récupération", seconds, "recovery")
                steps.append(step)
                last_instruction_steps = [step]
                order += 1
            continue

        duration_match = DURATION_TOKEN.search(line)
        if duration_match:
            description = f"{section_title} · {line}" if section_title else line
            step = _time_or_manual_step(
                order,
                description,
                _duration_seconds(duration_match),
                section_kind,
            )
            steps.append(step)
            last_instruction_steps = [step]
            order += 1
        elif last_instruction_steps and not list_item:
            for step in last_instruction_steps:
                _append_step_explanation(step, line)
        elif list_item:
            description = f"{section_title} · {line}" if section_title else line
            step = _time_or_manual_step(order, description, None, section_kind)
            steps.append(step)
            last_instruction_steps = [step]
            order += 1

    return steps




def _numbered_routine_steps(lines: list[str]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    section: dict[str, Any] | None = None
    current_item: dict[str, str] | None = None
    active = False

    def save_item() -> None:
        nonlocal current_item
        if current_item is not None and section is not None:
            section["items"].append(current_item)
        current_item = None

    def save_section() -> None:
        nonlocal section
        save_item()
        if section is not None and section["items"]:
            sections.append(section)
        section = None

    for raw_line in lines:
        list_item = _is_list_item(raw_line)
        line = _clean_plan_line(raw_line)
        if not line:
            continue
        normalized = normalize_name(line)

        if _is_plan_heading(line):
            save_section()
            section = {
                "title": line.rstrip(":： "),
                "rounds": 1,
                "items": [],
            }
            active = True
            continue
        if re.match(r"^(?:retouraucalme|cooldown|bilandelaseance|bilan)\b", normalized):
            save_section()
            active = False
            continue
        if not active or section is None:
            continue
        if _is_source_line(line) or _is_cycle_marker(line):
            continue

        set_match = SETS_LINE.match(line)
        if set_match:
            save_item()
            section["rounds"] = max(
                1,
                int(set_match.group("after") or set_match.group("times") or set_match.group("before")),
            )
            continue

        match = re.match(r"^\d+\s*[.)]\s*(.+)$", line)
        if match:
            save_item()
            current_item = {"title": match.group(1).strip(), "details": ""}
        elif list_item:
            save_item()
            current_item = {"title": line, "details": ""}
        elif current_item is not None:
            current_item["details"] = (current_item["details"] + " " + line).strip()

    save_section()
    steps: list[dict[str, Any]] = []
    order = 1

    for routine in sections:
        section_title = routine["title"]
        section_kind = _plan_section_kind(section_title) or "interval"
        normalized_title = normalize_name(section_title)
        if section_kind == "recovery":
            seconds = _parse_rest_seconds(section_title)
            if seconds:
                steps.append(_time_or_manual_step(order, "Récupération", seconds, "recovery"))
                order += 1
            continue

        section_steps: list[dict[str, Any]] = []
        for item in routine["items"]:
            description = item["title"]
            if item["details"]:
                description += " — " + item["details"]
            full_description = f"{section_title} · {description}"
            timing_tokens = _cardio_tokens(description, sport_key, section_kind)
            if timing_tokens:
                for token in timing_tokens:
                    step_order = len(section_steps) + 1
                    if "repeat" in token:
                        children: list[dict[str, Any]] = []
                        for child_order, child in enumerate(token["steps"], start=1):
                            child_description = (
                                f"{section_title} · {child['description']}"
                                if section_title
                                else child["description"]
                            )
                            if child.get("distance_meters") is not None:
                                children.append(_distance_step(
                                    child_order,
                                    child["distance_meters"],
                                    child_description,
                                    child["kind"],
                                ))
                            else:
                                children.append(_time_or_manual_step(
                                    child_order,
                                    child_description,
                                    child.get("duration_seconds"),
                                    child["kind"],
                                ))
                        section_steps.append(_repeat_group(
                            token["repeat"],
                            children,
                            step_order,
                            skip_last_rest=False,
                            description=_compact_description(
                                f"{section_title} — {description}"
                            ),
                        ))
                    elif token.get("distance_meters") is not None:
                        section_steps.append(_distance_step(
                            step_order,
                            token["distance_meters"],
                            full_description,
                            token["kind"],
                        ))
                    else:
                        section_steps.append(_time_or_manual_step(
                            step_order,
                            full_description,
                            token.get("duration_seconds"),
                            token["kind"],
                        ))
                continue

            section_steps.append(_time_or_manual_step(
                len(section_steps) + 1,
                full_description,
                None,
                section_kind,
            ))

        if not section_steps:
            continue
        if routine["rounds"] > 1:
            details = " · ".join(item["title"] for item in routine["items"])
            steps.append(_repeat_group(
                routine["rounds"],
                section_steps,
                order,
                skip_last_rest=False,
                description=_compact_description(f"{section_title} — {details}"),
            ))
            order += 1
        else:
            for child in section_steps:
                child["stepOrder"] = order
                steps.append(child)
                order += 1

    return steps




def _compact_description(value: str, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    split_at = text.rfind(" ", 0, limit - 1)
    if split_at < limit // 2:
        split_at = limit - 1
    return text[:split_at].rstrip(" ,;:·-") + "…"


def _brief_explanation(item: dict[str, Any], limit: int = 72) -> str:
    explanation = " ".join(item.get("explanation", [])).strip()
    if not explanation:
        return ""
    first_sentence = re.split(r"(?<=[.!?])\s+", explanation, maxsplit=1)[0]
    return _compact_description(first_sentence, limit)


def _strength_section_description(section: dict[str, Any]) -> str:
    items = [
        _section_item_summary(item)
        for item in section.get("exercises", [])
        if _section_item_summary(item)
    ]
    description = section["title"]
    if items:
        description += " — " + " · ".join(items)
    if section.get("notes"):
        description += " · " + section["notes"]
    return _compact_description(description)


def _emom_description(section: dict[str, Any]) -> str:
    instructions: list[str] = []
    for item in section.get("exercises", []):
        summary = _section_item_summary(item)
        explanation = _brief_explanation(item, 64)
        instructions.append(f"{summary} — {explanation}" if explanation else summary)
    description = f"{section['title']} · un tour au début de chaque minute"
    if instructions:
        description += " · " + " ; ".join(instructions)
    if section.get("notes"):
        description += " · " + section["notes"]
    return _compact_description(description)


def _split_text_chunks(value: str, limit: int = 450) -> list[str]:
    remaining = value
    chunks: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = max(remaining.rfind("\n", 0, limit), remaining.rfind(" ", 0, limit))
        if split_at < limit // 2:
            split_at = limit
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]
        else:
            chunks.append(remaining[:split_at + 1])
            remaining = remaining[split_at + 1:]
    return chunks


def _generic_prompt_steps(
    lines: list[str],
    sport_key: str = "other",
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    last_instruction_steps: list[dict[str, Any]] = []
    section_title = ""
    section_kind = "interval"

    for raw_line in lines:
        list_item = _is_list_item(raw_line)
        line = _clean_plan_line(raw_line)
        normalized = normalize_name(line)
        if _is_plan_heading(line):
            section_title = line.rstrip(":： ")
            section_kind = _plan_section_kind(line) or "interval"
            last_instruction_steps = []
            if section_kind == "recovery":
                seconds = _parse_rest_seconds(line)
                if seconds:
                    steps.append(_time_or_manual_step(
                        len(steps) + 1, "Récupération", seconds, "recovery"
                    ))
            continue
        if (
            not line
            or _is_source_line(line)
            or _is_cycle_marker(line)
            or normalized in {"retouraucalme", "seanceprincipale"}
            or normalized.endswith("retouraucalme")
        ):
            continue
        if line.casefold().startswith(("source:", "distance totale", "consigne deload")):
            continue
        if not list_item and not _is_explicit_instruction(line) and last_instruction_steps:
            for step in last_instruction_steps:
                _append_step_explanation(step, line)
            continue

        items = _cardio_tokens(line, sport_key, section_kind)
        current_instruction_steps: list[dict[str, Any]] = []
        if items:
            for item in items:
                description = (
                    f"{section_title} · {item['description']}"
                    if section_title
                    else item["description"]
                )
                if "repeat" in item:
                    children = []
                    for index, child in enumerate(item["steps"], start=1):
                        child_description = (
                            f"{section_title} · {child['description']}"
                            if section_title
                            else child["description"]
                        )
                        if child.get("distance_meters") is not None:
                            children.append(_distance_step(
                                index, child["distance_meters"], child_description, child["kind"]
                            ))
                        else:
                            children.append(_time_or_manual_step(
                                index, child_description, child.get("duration_seconds"), child["kind"]
                            ))
                    group = _repeat_group(
                        item["repeat"],
                        children,
                        len(steps) + 1,
                        skip_last_rest=False,
                        description=_compact_description(
                            f"{section_title} — {line}" if section_title else line
                        ),
                    )
                    steps.append(group)
                    current_instruction_steps.append(group)
                elif item.get("distance_meters") is not None:
                    step = _distance_step(
                        len(steps) + 1,
                        item["distance_meters"],
                        description,
                        item["kind"],
                    )
                    steps.append(step)
                    current_instruction_steps.append(step)
                else:
                    step = _time_or_manual_step(
                        len(steps) + 1,
                        description,
                        item.get("duration_seconds"),
                        item["kind"],
                    )
                    steps.append(step)
                    current_instruction_steps.append(step)
        else:
            for chunk in _split_text_chunks(line):
                description = f"{section_title} · {chunk}" if section_title else chunk
                step = _time_or_manual_step(
                    len(steps) + 1, description, None, section_kind
                )
                steps.append(step)
                current_instruction_steps.append(step)
        last_instruction_steps = current_instruction_steps

    return steps




def _estimated_seconds(steps: list[dict[str, Any]]) -> int:
    total = 0
    for step in steps:
        if step.get("type") == "RepeatGroupDTO":
            iterations = int(step.get("numberOfIterations", 1))
            children = step.get("workoutSteps", [])
            repeated_seconds = _estimated_seconds(children) * iterations
            if step.get("skipLastRestStep") and children:
                last = children[-1]
                if last.get("stepType", {}).get("stepTypeKey") == "rest":
                    repeated_seconds -= int(last.get("endConditionValue") or 0)
            total += repeated_seconds
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
    page_marker = page_id.replace("-", "").casefold()[:PAGE_MARKER_LENGTH]
    digest = hashlib.sha256(
        ("structured-workout-v5\n" + title + "\n" + discipline_text + "\n" + source_text).encode("utf-8")
    ).hexdigest()[:HASH_LENGTH]
    marker = f"[NTN:{page_marker}:{digest}:P]"
    if not steps:
        raise ValueError("Aucune consigne exploitable dans cette séance.")

    workout = {
        "workoutName": f"{title[:48].rstrip()} {marker}"[:100],
        "sportType": dict(sport_type),
        "estimatedDurationInSecs": _estimated_seconds(steps),
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": dict(sport_type),
            "workoutSteps": steps,
        }],
        "description": "",
    }
    return workout, marker


def build_garmin_workout(
    title: str,
    page_id: str,
    disciplines: list[str],
    lines: list[str],
    source_text: str,
) -> tuple[dict[str, Any], str]:
    sport_type = sport_for_disciplines(disciplines)
    if sport_type is None:
        raise ValueError("Cette discipline n'est pas un entraînement à envoyer sur la montre.")
    sport_key = sport_type["sportTypeKey"]
    discipline_text = ", ".join(disciplines)
    plan_lines = _training_lines(lines)

    if sport_key == "strength_training":
        sections = parse_sections(plan_lines)
        if sections:
            return _build_strength_workout(
                title, page_id, sections, discipline_text + "\n" + source_text
            )
        steps = _generic_prompt_steps(plan_lines, sport_key)
        return _generic_workout(
            title, page_id, discipline_text, source_text, sport_type, steps
        )

    if sport_key in {"running", "cycling"}:
        ambiguous_bike_repeat = sport_key == "cycling" and any(
            re.search(r"\b(?:repeat|repeter|repete)\b", _fold_text(line))
            and not REPEAT_TOKEN.search(line)
            for line in plan_lines
        )
        if ambiguous_bike_repeat:
            # Keep every instruction on Garmin without guessing the repeat count.
            steps = _generic_prompt_steps(plan_lines, sport_key)
        else:
            steps = _parse_cardio_steps(plan_lines, sport_key)
            if not steps:
                steps = _generic_prompt_steps(plan_lines, sport_key)
    elif sport_key == "swimming":
        steps = _parse_swim_steps(plan_lines)
        if not steps:
            steps = _generic_prompt_steps(plan_lines, sport_key)
    else:
        steps = _numbered_routine_steps(plan_lines)
        if not steps:
            steps = _generic_prompt_steps(plan_lines, sport_key)
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
    category, exercise_name, _ = _resolve_exercise(item["name"])
    reference = f" · Réf. {item['reference']}" if item.get("reference") else ""
    explanation = _brief_explanation(item, 140)
    description = f"{item['name']} · {item['reps']} répétitions{reference}"
    if explanation:
        description += f" — {explanation}"
    step_kind = _step_kind(section["title"], "strength_training")
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": step_order,
        "stepType": _step_type(step_kind),
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
        "description": _compact_description(description),
    }


def _section_item_step(
    item: dict[str, Any],
    section: dict[str, Any],
    step_order: int,
) -> dict[str, Any]:
    if item.get("kind") == "reps":
        return _exercise_step(item, section, step_order)

    description = item.get("description", "").strip()
    explanation = _brief_explanation(item, 140)
    if explanation:
        description += f" — {explanation}"
    if section.get("notes"):
        description += f" · {section['notes']}"
    return _time_or_manual_step(
        step_order,
        _compact_description(description),
        item.get("duration_seconds"),
        _step_kind(section["title"], "strength_training"),
    )




def _rest_step(
    seconds: int,
    step_order: int,
    description: str = "Récupération",
) -> dict[str, Any]:
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
        "description": description[:200],
    }


def _repeat_group(
    rounds: int,
    steps: list[dict[str, Any]],
    step_order: int,
    skip_last_rest: bool,
    description: str | None = None,
) -> dict[str, Any]:
    group = {
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
    if description:
        group["description"] = description[:200]
    return group


def _build_strength_workout(
    title: str,
    page_id: str,
    sections: list[dict[str, Any]],
    source_text: str,
) -> tuple[dict[str, Any], str]:
    compact_page_id = page_id.replace("-", "").casefold()
    digest = hashlib.sha256(("structured-workout-v5\n" + title + "\n" + source_text).encode("utf-8")).hexdigest()[:HASH_LENGTH]
    page_marker = compact_page_id[:PAGE_MARKER_LENGTH]
    marker = f"[NTN:{page_marker}:{digest}:P]"
    workout_name = f"Muscu progres · {title[:48].rstrip()} {marker}"[:100]

    steps: list[dict[str, Any]] = []
    step_order = 1
    for section in sections:
        title_text = section["title"]
        rounds = section["rounds"]
        has_rest = section.get("rest_seconds") is not None and section["rest_seconds"] > 0
        normalized_title = normalize_name(title_text)
        is_recovery = normalized_title.startswith(("recuperation", "repos"))
        is_emom = bool(re.search(r"\bem(?:om|on)\b", title_text, re.IGNORECASE))

        if is_emom:
            duration = section.get("duration_seconds") or rounds * 60
            steps.append(_time_or_manual_step(
                step_order,
                _emom_description(section),
                duration,
                "interval",
            ))
            step_order += 1
            continue

        if is_recovery and not section["exercises"]:
            if has_rest:
                steps.append(_rest_step(section["rest_seconds"], step_order, "Récupération"))
                step_order += 1
            continue

        child_steps = [
            _section_item_step(item, section, step_order + index + 1)
            for index, item in enumerate(section["exercises"])
        ]
        if has_rest and rounds > 1:
            child_steps.append(
                _rest_step(
                    section["rest_seconds"],
                    step_order + len(child_steps) + 1,
                    "Récupération",
                )
            )

        if child_steps:
            steps.append(
                _repeat_group(
                    rounds,
                    child_steps,
                    step_order,
                    skip_last_rest=has_rest and rounds > 1,
                    description=_strength_section_description(section),
                )
            )
            step_order += len(child_steps) + 2
        if has_rest and rounds == 1:
            steps.append(_rest_step(section["rest_seconds"], step_order, "Récupération"))
            step_order += 1

    if not steps:
        raise ValueError("Aucune étape d'exercice Garmin n'a pu être créée.")

    workout = {
        "workoutName": workout_name,
        "sportType": {
            "sportTypeId": 5,
            "sportTypeKey": "strength_training",
            "displayOrder": 5,
        },
        "estimatedDurationInSecs": _estimated_seconds(steps),
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
        "description": "",
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


def _remove_legacy_text_overflow(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    cleaned: list[dict[str, Any]] = []
    removed = 0
    for step in steps:
        if step.get("type") == "RepeatGroupDTO":
            children, child_count = _remove_legacy_text_overflow(
                step.get("workoutSteps", [])
            )
            step["workoutSteps"] = children
            removed += child_count
        description = str(step.get("description") or "").strip().casefold()
        if description.startswith("suite du texte notion ("):
            removed += 1
            continue
        cleaned.append(step)
    return cleaned, removed


def clear_synced_workout_notes(
    garmin: Garmin,
    existing_workouts: list[dict[str, Any]],
    upcoming_page_markers: set[str],
) -> None:
    for existing in existing_workouts:
        workout_name = existing.get("workoutName", "")
        tag = SYNC_TAG.search(workout_name)
        if (
            not tag
            or tag.group(3) == "F"
            or tag.group(1).casefold() in upcoming_page_markers
        ):
            continue
        workout_id = existing.get("workoutId")
        if not workout_id:
            continue
        try:
            workout = garmin.get_workout_by_id(workout_id)
            if not workout:
                continue
            description = str(workout.get("description") or "")
            workout["description"] = ""
            removed_steps = 0
            for segment in workout.get("workoutSegments", []):
                cleaned_steps, removed = _remove_legacy_text_overflow(
                    segment.get("workoutSteps", [])
                )
                segment["workoutSteps"] = cleaned_steps
                removed_steps += removed
            clean_name = (
                workout_name[:tag.start()]
                + f"[NTN:{tag.group(1)}:{tag.group(2)}:F]"
            )
            workout["workoutName"] = clean_name
            garmin.update_workout(workout_id, workout)
            if description.strip() or removed_steps:
                garmin.push_workout_to_device(workout_id=workout_id)
            existing["workoutName"] = clean_name
            if description.strip() or removed_steps:
                print(
                    f"Notes Garmin et texte de débordement supprimés : "
                    f"« {workout_name} » ({removed_steps} étape(s) retirée(s))."
                )
        except Exception as error:
            print(f"Impossible de vider les notes de « {workout_name} » : {error}")


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
            title, page["id"], disciplines, lines, source_text
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
    existing_workouts = all_garmin_workouts(garmin)
    upcoming_page_markers = {
        page["id"].replace("-", "").casefold()[:PAGE_MARKER_LENGTH]
        for page in pages
        if page.get("id")
    }
    clear_synced_workout_notes(garmin, existing_workouts, upcoming_page_markers)
    if not pages:
        print(f"Aucune séance prévue dans les {LOOKAHEAD_DAYS} prochains jours.")
        return

    for page in pages:
        planned_date = page_date(page) or "date inconnue"
        try:
            sync_page(notion, garmin, page, existing_workouts)
        except Exception as error:
            print(f"Échec de l'envoi de « {page_title(page)} » ({planned_date}) : {error}")


if __name__ == "__main__":
    main()

