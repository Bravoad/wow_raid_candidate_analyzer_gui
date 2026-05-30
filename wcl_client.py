from __future__ import annotations

import os
import time
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()

class WarcraftLogsError(RuntimeError):
    pass


class WarcraftLogsClient:
    TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
    GRAPHQL_URL = "https://www.warcraftlogs.com/api/v2/client"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id or os.getenv("WCL_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("WCL_CLIENT_SECRET")

        if not self.client_id or not self.client_secret:
            raise WarcraftLogsError(
                "Не найдены WCL_CLIENT_ID и WCL_CLIENT_SECRET. "
                "Добавь их в переменные окружения."
            )

        self._access_token: str | None = None
        self._token_expires_at = 0.0

    def get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        response = requests.post(
            self.TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=20,
        )

        if response.status_code == 401:
            response = requests.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=20,
            )

        if response.status_code >= 400:
            raise WarcraftLogsError(
                f"Ошибка получения токена WarcraftLogs: "
                f"{response.status_code} — {response.text}"
            )

        data = response.json()

        access_token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)

        if not access_token:
            raise WarcraftLogsError(f"WarcraftLogs не вернул access_token: {data}")

        self._access_token = access_token
        self._token_expires_at = time.time() + int(expires_in) - 60

        return access_token

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self.get_access_token()

        response = requests.post(
            self.GRAPHQL_URL,
            json={
                "query": query,
                "variables": variables or {},
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

        if response.status_code >= 400:
            raise WarcraftLogsError(
                f"Ошибка GraphQL WarcraftLogs: {response.status_code} — {response.text}"
            )

        data = response.json()

        if data.get("errors"):
            raise WarcraftLogsError(f"GraphQL errors: {data['errors']}")

        return data["data"]