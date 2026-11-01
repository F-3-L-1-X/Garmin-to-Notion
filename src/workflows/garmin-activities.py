from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv
from garminconnect import Garmin as GarminClient
from notion_client import Client as NotionClient

from src.helpers import get_garmin_client, get_notion_client

DATE_PROPERTY = "Date de la  séance"
TITLE_PROPERTY = "Nom"


def get_all_activities(garmin_client: GarminClient, limit: int = 10) -> list[dict]:
    return garmin_client.get_activities(0, limit)


def discipline_for(type_key: str) -> str | None:
    key = (type_key or "").lower()
    for markers, label in [
        (("run", "treadmill"), "Course à pied"),
        (("cycl", "bike"), "Vélo"),
        (("walk", "hike", "ruck"), "Marche / Rando"),
        (("swim",), "Natation"),
        (("strength", "cardio", "fitness", "rowing"), "Renforcement"),
        (("yoga", "pilates"), "Yoga"),
        (("climb",), "Escalade"),
        (("ski",), "Ski de fond"),
    ]:
        if any(marker in key for marker in markers):
            return label
    return None


def as_number(value, divisor=1, multiplier=1, digits=1):
    if value is None:
        return None
    try:
        return round(float(value) / divisor * multiplier, digits)
    except (TypeError, ValueError):
        return None


def start_date(activity: dict) -> str:
    raw = activity.get("startTimeGMT") or activity.get("startTimeLocal")
    if not raw:
        raise ValueError("Date de début absente")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def activity_day(activity: dict) -> str:
    # Match the planned Notion date using Garmin's local start time when available.
    raw = activity.get("startTimeLocal") or activity.get("startTimeGMT")
    if not raw:
        raise ValueError("Date de début absente")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    return parsed.date().isoformat()


def _page_title(page: dict) -> str:
    title = page.get("properties", {}).get(TITLE_PROPERTY, {}).get("title", [])
    return "".join(item.get("plain_text", "") for item in title).strip()


def find_planned_session(
    notion: NotionClient,
    database_id: str,
    day: str,
    activity_name: str,
    discipline: str | None,
) -> dict | None:
    next_day = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
    result = notion.databases.query(
        database_id=database_id,
        filter={"and": [
            {"property": DATE_PROPERTY, "date": {"on_or_after": day}},
            {"property": DATE_PROPERTY, "date": {"before": next_day}},
        ]},
        page_size=100,
    )
    pages = result.get("results", [])
    normalized_name = activity_name.strip().casefold()

    exact = [
        page for page in pages
        if _page_title(page).casefold() == normalized_name
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError(f"Plusieurs séances Notion portent le nom « {activity_name} » le {day}.")

    if not discipline:
        return None
    same_discipline = [
        page for page in pages
        if discipline in [
            item.get("name")
            for item in page.get("properties", {}).get("Discipline", {}).get("multi_select", [])
        ]
    ]
    if len(same_discipline) == 1:
        return same_discipline[0]
    if len(same_discipline) > 1:
        unfinished = [
            page for page in same_discipline
            if not page.get("properties", {}).get("Terminé", {}).get("checkbox", False)
        ]
        if len(unfinished) == 1:
            return unfinished[0]
    return None


def activity_properties(activity: dict) -> dict:
    elevation_gain = as_number(activity.get("elevationGain"), digits=1)
    elevation_loss = as_number(activity.get("elevationLoss"), digits=1)
    values = {
        "Distance (km)": as_number(activity.get("distance"), divisor=1000, digits=2),
        "Durée (min)": as_number(activity.get("duration"), divisor=60, digits=1),
        "FC moyenne (bmp)": as_number(activity.get("averageHR"), digits=0),
        "FC maximum (pbm)": as_number(activity.get("maxHR"), digits=0),
        "Aérobie": as_number(activity.get("aerobicTrainingEffect"), digits=1),
        "Anéorobie ": as_number(activity.get("anaerobicTrainingEffect"), digits=1),
        "Watts moyenne": as_number(activity.get("avgPower"), digits=0),
        "Vitesse moyenne ": as_number(activity.get("averageSpeed"), multiplier=3.6, digits=2),
        "Vitesse maximum": as_number(activity.get("maxSpeed"), multiplier=3.6, digits=2),
        "Altitude maximale": as_number(activity.get("maxElevation"), digits=1),
        "Altitude minimum": as_number(activity.get("minElevation"), digits=1),
        "Ascension": elevation_gain,
        "Descente totale": elevation_loss,
        "Dénivelé -": elevation_loss,
        "Fréquence respiratoire moyenne": as_number(
            activity.get("averageRespirationRate"), digits=0
        ),
        "Fréquence respiratoire minimale": as_number(
            activity.get("minRespirationRate"), digits=0
        ),
        "Fréquence respiratoire maximale": as_number(
            activity.get("maxRespirationRate"), digits=0
        ),
    }
    properties = {
        name: {"number": value}
        for name, value in values.items()
        if value is not None
    }
    if elevation_gain is not None:
        properties["Dénivelé +"] = {
            "rich_text": [{"text": {"content": str(elevation_gain)}}]
        }
    properties["Source"] = {
        "rich_text": [{"text": {"content": "Garmin Connect"}}]
    }
    properties["Terminé"] = {"checkbox": True}
    return properties


def create_activity(
    notion: NotionClient,
    database_id: str,
    activity: dict,
    activity_name: str,
    discipline: str | None,
) -> None:
    properties = activity_properties(activity)
    properties[TITLE_PROPERTY] = {
        "title": [{"text": {"content": activity_name[:2000]}}]
    }
    properties[DATE_PROPERTY] = {"date": {"start": start_date(activity)}}
    if discipline:
        properties["Discipline"] = {"multi_select": [{"name": discipline}]}
    notion.pages.create(parent={"database_id": database_id}, properties=properties)


def update_planned_session(
    notion: NotionClient,
    page: dict,
    activity: dict,
) -> None:
    # Update only Garmin-provided properties; preserve the planned session's
    # title, date, type, equipment, location, intensity, and personal notes.
    notion.pages.update(
        page_id=page["id"],
        properties=activity_properties(activity),
    )


def main():
    load_dotenv()
    garmin, configuration = get_garmin_client()
    notion, databases = get_notion_client()

    for activity in get_all_activities(garmin, configuration.activity_fetch_limit):
        name = activity.get("activityName") or "Activité Garmin"
        type_key = (activity.get("activityType") or {}).get("typeKey", "")
        discipline = discipline_for(type_key)
        day = activity_day(activity)
        planned = find_planned_session(
            notion,
            databases.activities,
            day,
            name,
            discipline,
        )
        if planned:
            update_planned_session(notion, planned, activity)
            print(f"Séance planifiée mise à jour : {_page_title(planned)}")
        else:
            create_activity(notion, databases.activities, activity, name, discipline)
            print(f"Nouvelle activité ajoutée : {name}")


if __name__ == "__main__":
    main()
