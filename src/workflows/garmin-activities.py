import os
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv
from garminconnect import Garmin as GarminClient, parse_activity_detail_metrics
from notion_client import Client as NotionClient

from src.helpers import get_garmin_client, get_notion_client
from src.helpers.garmin_report import (
    add_or_update_activity_report,
    find_activity_report_page,
    render_activity_report,
)

DATE_PROPERTY = "Date de la  séance"
TITLE_PROPERTY = "Nom"


def get_all_activities(garmin_client: GarminClient, limit: int = 10) -> list[dict]:
    return garmin_client.get_activities(0, limit)


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

    preferred_sources = [
        source for source in data_sources
        if source.get("name", "").strip().casefold() == "journal d'entraînement"
    ]
    if len(preferred_sources) == 1:
        return preferred_sources[0]["id"]
    if len(preferred_sources) > 1:
        raise ValueError(
            "Plusieurs sources de données Notion portent le nom « Journal d'entraînement ». "
            "Ajoute la bonne valeur comme secret GitHub NOTION_DATA_SOURCE_ID."
        )

    if len(data_sources) == 1:
        return data_sources[0]["id"]

    if not data_sources:
        raise ValueError(
            "Aucune source de données trouvée dans la base Notion. "
            "Vérifie NOTION_DB_ID et l'accès de la connexion à cette base."
        )

    names = ", ".join(source.get("name", "(sans nom)") for source in data_sources)
    raise ValueError(
        "Cette base Notion contient plusieurs sources de données. "
        "Dans Notion, copie l'ID de la source « Journal d'entraînement », "
        "puis ajoute-le comme secret GitHub NOTION_DATA_SOURCE_ID. "
        f"Sources trouvées : {names}."
    )


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
    data_source_id: str,
    day: str,
    activity_name: str,
    discipline: str | None,
) -> dict | None:
    next_day = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
    result = notion.data_sources.query(
        data_source_id=data_source_id,
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
        "Charge d'exercice": as_number(
            activity.get("activityTrainingLoad") or activity.get("trainingLoad"), digits=1
        ),
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
    properties["Terminé"] = {"checkbox": True}
    return properties


def create_activity(
    notion: NotionClient,
    data_source_id: str,
    activity: dict,
    activity_name: str,
    discipline: str | None,
) -> dict:
    properties = activity_properties(activity)
    properties["Source"] = {
        "rich_text": [{"text": {"content": "Garmin Connect"}}]
    }
    properties[TITLE_PROPERTY] = {
        "title": [{"text": {"content": activity_name[:2000]}}]
    }
    properties[DATE_PROPERTY] = {"date": {"start": start_date(activity)}}
    if discipline:
        properties["Discipline"] = {"multi_select": [{"name": discipline}]}
    return notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": data_source_id},
        properties=properties,
    )


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


def _optional_activity_data(garmin: GarminClient, method_name: str, activity_id: str):
    method = getattr(garmin, method_name, None)
    if method is None:
        return []
    try:
        return method(activity_id)
    except Exception as error:
        print(f"Donnée Garmin facultative indisponible ({method_name}) : {error}")
        return []


def main():
    load_dotenv()
    garmin, configuration = get_garmin_client()
    notion, databases = get_notion_client()
    activities_data_source_id = get_activity_data_source_id(
        notion,
        databases.activities,
    )

    for activity in get_all_activities(garmin, configuration.activity_fetch_limit):
        name = activity.get("activityName") or "Activité Garmin"
        type_key = (activity.get("activityType") or {}).get("typeKey", "")
        discipline = discipline_for(type_key)
        day = activity_day(activity)
        activity_id = str(activity.get("activityId") or "")

        already_reported = None
        if activity_id:
            already_reported = find_activity_report_page(
                notion,
                activities_data_source_id,
                day,
                activity_id,
            )
        if already_reported:
            print(f"Bilan Garmin déjà présent : {_page_title(already_reported)}")
            continue

        planned = find_planned_session(
            notion,
            activities_data_source_id,
            day,
            name,
            discipline,
        )
        if planned:
            page = planned
            update_planned_session(notion, planned, activity)
            print(f"Séance planifiée mise à jour : {_page_title(planned)}")
        else:
            page = create_activity(notion, activities_data_source_id, activity, name, discipline)
            print(f"Nouvelle activité ajoutée : {name}")

        if not activity_id:
            print("Bilan graphique ignoré : Garmin n'a pas fourni l'identifiant de l'activité.")
            continue

        try:
            details = garmin.get_activity_details(activity_id)
            samples = parse_activity_detail_metrics(details) or []
        except Exception as error:
            print(f"Impossible de lire les courbes Garmin de « {name} » : {error}")
            continue

        heart_rate_zones = _optional_activity_data(
            garmin,
            "get_activity_hr_in_timezones",
            activity_id,
        )
        power_zones = _optional_activity_data(
            garmin,
            "get_activity_power_in_timezones",
            activity_id,
        )
        report = render_activity_report(
            name,
            activity,
            samples,
            heart_rate_zones,
            power_zones,
            discipline,
        )
        if report is None:
            print(f"Pas de courbe Garmin exploitable pour « {name} » ; bilan non ajouté.")
            continue

        add_or_update_activity_report(notion, page["id"], activity_id, report)
        print(f"Bilan graphique ajouté dans Notion : {name}")


if __name__ == "__main__":
    main()
