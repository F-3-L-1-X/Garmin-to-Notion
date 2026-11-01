from __future__ import annotations

from mimetypes import guess_type
from urllib.request import urlopen

from notion_client import Client as NotionClient

ROUTE_PROPERTY = "Parcours"
MAX_SINGLE_UPLOAD_SIZE = 20 * 1024 * 1024


def route_filename(activity_id: str) -> str:
    return f"garmin-{activity_id}-parcours.gpx"


def page_has_activity_gpx(page: dict, activity_id: str) -> bool:
    filename = route_filename(activity_id)
    route = page.get("properties", {}).get(ROUTE_PROPERTY, {})
    files = route.get("files") or route.get("file") or []
    return any(item.get("name") == filename for item in files)


def _upload_file(notion: NotionClient, filename: str, content: bytes) -> dict:
    if len(content) > MAX_SINGLE_UPLOAD_SIZE:
        raise ValueError(f"Le fichier {filename} dépasse la limite d'envoi en une partie.")

    content_type = (
        "application/xml"
        if filename.casefold().endswith(".gpx")
        else guess_type(filename)[0] or "application/octet-stream"
    )
    upload = notion.file_uploads.create(
        mode="single_part",
        filename=filename,
        content_type=content_type,
    )
    sent = notion.file_uploads.send(
        file_upload_id=upload["id"],
        file=(filename, content, content_type),
    )
    if sent.get("status") != "uploaded":
        raise RuntimeError(f"Notion n'a pas confirmé l'envoi du fichier {filename}.")

    return {
        "name": filename,
        "type": "file_upload",
        "file_upload": {"id": upload["id"]},
    }


def _preserve_existing_file(notion: NotionClient, item: dict) -> dict:
    file_type = item.get("type")
    name = item.get("name") or "fichier-notion"
    if file_type == "external":
        return {
            "name": name,
            "type": "external",
            "external": {"url": item["external"]["url"]},
        }
    if file_type == "file_upload":
        return {
            "name": name,
            "type": "file_upload",
            "file_upload": {"id": item["file_upload"]["id"]},
        }
    if file_type == "file":
        file_url = item.get("file", {}).get("url")
        if not file_url:
            raise ValueError(f"Impossible de conserver le fichier Notion « {name} ».")
        with urlopen(file_url, timeout=30) as response:
            content = response.read()
        return _upload_file(notion, name, content)

    raise ValueError(
        f"Le fichier « {name} » de la propriété « {ROUTE_PROPERTY} » "
        "a un format que le programme ne sait pas conserver."
    )


def add_activity_gpx(
    notion: NotionClient,
    page_id: str,
    activity_id: str,
    gpx_content: bytes,
) -> bool:
    filename = route_filename(activity_id)
    if not gpx_content or b"<gpx" not in gpx_content[:4096].lower():
        raise ValueError("Garmin n'a pas renvoyé un fichier GPX valide.")

    page = notion.pages.retrieve(page_id=page_id)
    route = page.get("properties", {}).get(ROUTE_PROPERTY, {})
    existing_files = route.get("files") or route.get("file") or []
    if any(item.get("name") == filename for item in existing_files):
        return False

    files = [
        _preserve_existing_file(notion, item)
        for item in existing_files
    ]
    files.append(_upload_file(notion, filename, gpx_content))
    notion.pages.update(
        page_id=page_id,
        properties={ROUTE_PROPERTY: {"files": files}},
    )
    return True
