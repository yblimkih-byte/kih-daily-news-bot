"""Kakao OAuth token manager."""
import os
import requests


KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"


def refresh_kakao_access_token():
    """Refresh Kakao access token using stored refresh token.

    Returns:
        dict: {
            "access_token": str,
            "refresh_token": str or None (None if not rotated),
            "expires_in": int,
            "refresh_token_expires_in": int or None,
        }
    """
    rest_api_key = os.environ["KAKAO_REST_API_KEY"]
    refresh_token = os.environ["KAKAO_REFRESH_TOKEN"]

    response = requests.post(
        KAKAO_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": rest_api_key,
            "refresh_token": refresh_token,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    result = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
        "refresh_token_expires_in": data.get("refresh_token_expires_in"),
    }

    # Kakao rotates refresh_token only when current one is within ~1 month of expiry.
    if result["refresh_token"]:
        days_left = (result["refresh_token_expires_in"] or 0) / 86400
        print(
            f"[WARNING] Kakao issued a new refresh_token. "
            f"New token expires in ~{days_left:.0f} days. "
            f"Update GitHub Secret KAKAO_REFRESH_TOKEN with the new value.",
            flush=True,
        )
        # Note: For full automation, use GitHub API to update the secret here.
        # That requires an additional Personal Access Token. For now, print warning.

    return result
