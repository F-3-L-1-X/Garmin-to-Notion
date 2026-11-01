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


def exists(notion: NotionClient, database_id: str, date_iso: str, name: str) -> bool:
    date = datetime.fromisoformat(date_iso)
    result = notion.databases.query(
        database_id=database_id,
        filter={"and": [
            {"property": DATE_PROPERTY, "date": {"on_or_after": (date - timedelta(minutes=5)).isoformat()}},
            {"property": DATE_PROPERTY, "date": {"on_or_before": (date + timedelta(minutes=5)).isoformat()}},
            {"property": TITLE_PROPERTY, "title": {"equals": name}},
        ]},
        page_size=1,
    )
    return bool(result.get("results"))


def create_activity(notion: NotionClient, database_id: str, activity: dict) -> None:
    name = (activity.get("activityName") or "Activité Garmin")[:2000]
    properties = {
        TITLE_PROPERTY: {"title": [{"text": {"content": name}}]},
        DATE_PROPERTY: {"date": {"start": start_date(activity)}},
        "Source": {"rich_text": [{"text": {"content": "Garmin Connect"}}]},
        "Terminé": {"checkbox": True},
    }
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
        "Ascension": as_number(activity.get("elevationGain"), digits=1),
        "Descente totale": as_number(activity.get("elevationLoss"), digits=1),
    }
    for property_name, value in values.items():
        if value is not None:
            properties[property_name] = {"number": value}
    discipline = discipline_for((activity.get("activityType") or {}).get("typeKey", ""))
    if discipline:
        properties["Discipline"] = {"multi_select": [{"name": discipline}]}
    notion.pages.create(parent={"database_id": database_id}, properties=properties)


def main():
    load_dotenv()
    garmin, configuration = get_garmin_client()
    notion, databases = get_notion_client()
    for activity in get_all_activities(garmin, configuration.activity_fetch_limit):
        name = activity.get("activityName") or "Activité Garmin"
        try:
            date_iso = start_date(activity)
            if not exists(notion, databases.activities, date_iso, name):
                create_activity(notion, databases.activities, activity)
                print(f"Ajoutée : {name}")
            else:
                print(f"Déjà présente : {name}")
        except Exception as error:
            print(f"Activité ignorée ({name}) : {error}")


if __name__ == "__main__":
    main()
