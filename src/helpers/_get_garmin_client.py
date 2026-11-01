import os
from dataclasses import dataclass

from dotenv import load_dotenv
from garminconnect import Garmin


@dataclass(frozen=True)
class GarminConfiguration:
    activity_fetch_limit: int


def get_garmin_client() -> tuple[Garmin, GarminConfiguration]:
    load_dotenv()

    print("Initializing Garmin client...")

    garmin_client = _get_garmin_client()
    garmin_configuration = _get_garmin_configuration()

    print("Garmin client authenticated successfully.")

    return garmin_client, garmin_configuration


def _get_garmin_client() -> Garmin:
    garmin_auth_token = os.getenv("GARMIN_AUTH_TOKEN")

    if not garmin_auth_token:
        raise ValueError(
            "GARMIN_AUTH_TOKEN is required. "
            "See README_AUTH_SETUP.md for instructions on generating a token."
        )

    # GARMIN_AUTH_TOKEN is passed as an inline JSON string (>512 chars), so the
    # library treats it as token data rather than a file path. This means the
    # access token is refreshed in memory on each run via diauth.garmin.com
    # (standard OAuth2 refresh — separate from the SSO endpoints that are
    # rate-limited), but the refreshed token is never written back anywhere.
    #
    # This is safe as long as Garmin issues non-rotating refresh tokens, which
    # is currently the case. If that ever changes and runs start failing with
    # 401s, we might need to add a workflow step that writes the updated token
    # back to the GARMIN_AUTH_TOKEN secret after each run via `gh secret set`,
    # or otherwise persist the token across runs.
    garmin_client = Garmin()
    garmin_client.login(tokenstore=garmin_auth_token)

    return garmin_client


def _get_garmin_configuration():
    return GarminConfiguration(
        activity_fetch_limit=int(os.getenv("GARMIN_ACTIVITIES_FETCH_LIMIT", "10")),
    )
