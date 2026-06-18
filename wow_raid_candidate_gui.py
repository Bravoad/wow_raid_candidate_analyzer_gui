from __future__ import annotations

import json
import os
import re
import statistics
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import requests

try:
    from PIL import Image, ImageTk
except ImportError:  # Pillow нужен для отображения JPG/WebP аватарок
    Image = None
    ImageTk = None

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv необязателен, но желателен
    load_dotenv = None


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


if load_dotenv:
    load_dotenv(get_app_dir() / ".env")
    load_dotenv()


# ============================================================
# НАСТРОЙКИ
# ============================================================

RAIDERIO_CHARACTER_URL = "https://raider.io/api/v1/characters/profile"

RAIDERIO_FIELDS = ",".join(
    [
        "gear",
        "guild",
        "raid_progression:current-tier",
        "mythic_plus_scores_by_season:current",
        "mythic_plus_best_runs",
        "mythic_plus_recent_runs",
        "mythic_plus_weekly_highest_level_runs",
    ]
)


@dataclass
class Rules:
    target: str
    min_ilvl: float
    min_score: float
    min_weekly_key: int
    raid_difficulty: str
    min_raid_bosses: int
    check_mplus: bool = True


DEFAULT_RULES: dict[str, Rules] = {
    "normal": Rules(
        target="normal",
        min_ilvl=240,
        min_score=800,
        min_weekly_key=2,
        raid_difficulty="normal",
        min_raid_bosses=3,
    ),
    "heroic": Rules(
        target="heroic",
        min_ilvl=250,
        min_score=1800,
        min_weekly_key=6,
        raid_difficulty="heroic",
        min_raid_bosses=4,
    ),
    "mythic": Rules(
        target="mythic",
        min_ilvl=270,
        min_score=2500,
        min_weekly_key=10,
        raid_difficulty="mythic",
        min_raid_bosses=1,
    ),
}

DIFFICULTY_RANK = {
    "normal": 1,
    "heroic": 2,
    "mythic": 3,
}

DIFFICULTY_LETTER = {
    "normal": "N",
    "heroic": "H",
    "mythic": "M",
}


# ============================================================
# ОБЩИЕ УТИЛИТЫ
# ============================================================


def safe_float(value: str, default: float) -> float:
    value = value.strip().replace(",", ".")
    if not value:
        return default
    return float(value)



def safe_int(value: str, default: int) -> int:
    value = value.strip()
    if not value:
        return default
    return int(value)



def pretty_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)



def extract_avatar_url(data: dict[str, Any]) -> str | None:
    """
    Raider.IO обычно возвращает thumbnail_url в базовом профиле персонажа.
    Оставляем несколько fallback-ключей, чтобы GUI не ломался, если формат слегка изменится.
    """
    for key in ("thumbnail_url", "avatar_url", "profile_image_url", "character_thumbnail_url"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None



def download_image_bytes(url: str) -> bytes:
    response = requests.get(
        url,
        timeout=25,
        headers={"User-Agent": "WowRaidCandidateGUI/1.0"},
    )
    response.raise_for_status()
    return response.content



def normalize_region(region: str) -> str:
    return region.strip().lower()



def normalize_wcl_region(region: str) -> str:
    return region.strip().upper()


# ============================================================
# RAIDER.IO ЛОГИКА
# ============================================================


def fetch_raiderio_character(region: str, realm: str, name: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "region": normalize_region(region),
        "realm": realm.strip(),
        "name": name.strip(),
        "fields": RAIDERIO_FIELDS,
    }

    access_key = os.getenv("RAIDERIO_ACCESS_KEY")
    if access_key:
        params["access_key"] = access_key

    response = requests.get(
        RAIDERIO_CHARACTER_URL,
        params=params,
        timeout=25,
        headers={"User-Agent": "WowRaidCandidateGUI/1.0"},
    )

    if response.status_code == 404:
        raise RuntimeError("Персонаж не найден в Raider.IO. Проверь регион, сервер и ник.")

    if response.status_code == 429:
        raise RuntimeError("Raider.IO ограничил запросы. Подожди немного и повтори.")

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(f"Ошибка Raider.IO API: {response.status_code}\n{response.text}") from exc

    return response.json()



def extract_ilvl(data: dict[str, Any]) -> float:
    gear = data.get("gear") or {}
    for key in ("item_level_equipped", "item_level_total"):
        value = gear.get(key)
        if isinstance(value, int | float):
            return float(value)
    return 0.0



def extract_mplus_score(data: dict[str, Any]) -> float:
    seasons = data.get("mythic_plus_scores_by_season") or []
    if not seasons:
        return 0.0

    current_season = seasons[0] or {}
    scores = current_season.get("scores") or {}
    score = scores.get("all")

    if isinstance(score, int | float):
        return float(score)

    role_scores = [scores.get("dps", 0), scores.get("healer", 0), scores.get("tank", 0)]
    clean_scores = [float(value) for value in role_scores if isinstance(value, int | float)]
    return max(clean_scores, default=0.0)



def extract_run_level(run: dict[str, Any]) -> int:
    for key in ("mythic_level", "keystone_level", "level"):
        value = run.get(key)
        if isinstance(value, int):
            return value
    return 0



def extract_max_weekly_key(data: dict[str, Any]) -> int:
    runs = data.get("mythic_plus_weekly_highest_level_runs") or []
    if not isinstance(runs, list):
        return 0
    return max((extract_run_level(run) for run in runs if isinstance(run, dict)), default=0)



def extract_recent_runs_count(data: dict[str, Any]) -> int:
    runs = data.get("mythic_plus_recent_runs") or []
    if isinstance(runs, list):
        return len(runs)
    return 0



def parse_summary_progress(summary: str, wanted_difficulty: str) -> int:
    if not summary:
        return 0

    wanted_rank = DIFFICULTY_RANK[wanted_difficulty]
    best_kills = 0
    matches = re.findall(r"(\d+)\s*/\s*(\d+)\s*([NHM])", summary.upper())

    for killed_raw, _total_raw, difficulty_letter in matches:
        killed = int(killed_raw)
        current_difficulty = None

        for diff, letter in DIFFICULTY_LETTER.items():
            if letter == difficulty_letter:
                current_difficulty = diff
                break

        if current_difficulty is None:
            continue

        current_rank = DIFFICULTY_RANK[current_difficulty]
        if current_rank >= wanted_rank:
            best_kills = max(best_kills, killed)

    return best_kills



def get_raid_kills_from_raid_info(raid_info: dict[str, Any], wanted_difficulty: str) -> int:
    difficulty_order = {
        "normal": ["normal", "heroic", "mythic"],
        "heroic": ["heroic", "mythic"],
        "mythic": ["mythic"],
    }

    best_kills = 0

    for difficulty in difficulty_order[wanted_difficulty]:
        possible_keys = [
            f"{difficulty}_bosses_killed",
            f"{difficulty}_kills",
            f"{difficulty}_progress",
        ]

        for key in possible_keys:
            value = raid_info.get(key)
            if isinstance(value, int):
                best_kills = max(best_kills, value)
            if isinstance(value, str) and value.isdigit():
                best_kills = max(best_kills, int(value))

    summary = str(raid_info.get("summary") or "")
    return max(best_kills, parse_summary_progress(summary, wanted_difficulty))



def extract_raid_progress(data: dict[str, Any], wanted_difficulty: str) -> tuple[int, str]:
    progression = data.get("raid_progression") or {}
    if not isinstance(progression, dict) or not progression:
        return 0, "нет данных"

    best_kills = 0
    best_raid_name = "нет данных"

    for raid_name, raid_info in progression.items():
        if not isinstance(raid_info, dict):
            continue

        kills = get_raid_kills_from_raid_info(raid_info, wanted_difficulty)
        if kills > best_kills:
            best_kills = kills
            best_raid_name = str(raid_name)

    return best_kills, best_raid_name



def extract_best_runs(data: dict[str, Any], limit: int = 5) -> list[str]:
    runs = data.get("mythic_plus_best_runs") or []
    if not isinstance(runs, list):
        return []

    result: list[str] = []
    for run in runs[:limit]:
        if not isinstance(run, dict):
            continue

        dungeon = run.get("dungeon") or run.get("short_name") or "неизвестный данж"
        level = extract_run_level(run)
        score = run.get("score")
        completed_at = run.get("completed_at")

        line = f"+{level} {dungeon}"
        if isinstance(score, int | float):
            line += f" — {round(score)} score"
        if completed_at:
            line += f" — {completed_at}"

        result.append(line)

    return result



def score_ilvl(ilvl: float, rules: Rules) -> tuple[int, str]:
    if ilvl >= rules.min_ilvl:
        return 35, f"ilvl нормальный: {ilvl:.1f} / нужно {rules.min_ilvl:.1f}"
    if ilvl >= rules.min_ilvl - 5:
        return 25, f"ilvl почти подходит: {ilvl:.1f} / нужно {rules.min_ilvl:.1f}"
    if ilvl >= rules.min_ilvl - 15:
        return 15, f"ilvl слабоват: {ilvl:.1f} / нужно {rules.min_ilvl:.1f}"
    return 0, f"ilvl слишком низкий: {ilvl:.1f} / нужно {rules.min_ilvl:.1f}"



def score_mplus(mplus_score: float, rules: Rules) -> tuple[int, str]:
    if mplus_score >= rules.min_score:
        return 25, f"M+ score нормальный: {mplus_score:.0f} / нужно {rules.min_score:.0f}"
    if mplus_score >= rules.min_score * 0.8:
        return 18, f"M+ score почти подходит: {mplus_score:.0f} / нужно {rules.min_score:.0f}"
    if mplus_score >= rules.min_score * 0.6:
        return 10, f"M+ score слабоват: {mplus_score:.0f} / нужно {rules.min_score:.0f}"
    return 0, f"M+ score низкий: {mplus_score:.0f} / нужно {rules.min_score:.0f}"



def score_raid(raid_kills: int, raid_name: str, rules: Rules) -> tuple[int, str]:
    if raid_kills >= rules.min_raid_bosses:
        return 25, (
            f"рейдовый опыт подходит: {raid_kills} босс(ов) "
            f"на {rules.raid_difficulty}, рейд: {raid_name}"
        )
    if raid_kills > 0:
        return 12, (
            f"рейдовый опыт есть, но мало: {raid_kills} босс(ов) "
            f"на {rules.raid_difficulty}, нужно {rules.min_raid_bosses}"
        )
    return 0, f"рейдового опыта на {rules.raid_difficulty} не найдено"



def score_weekly_key(max_weekly_key: int, rules: Rules) -> tuple[int, str]:
    if max_weekly_key >= rules.min_weekly_key:
        return 10, f"активность за неделю есть: лучший ключ +{max_weekly_key}"
    if max_weekly_key > 0:
        return 5, f"ключ на неделе есть, но низкий: +{max_weekly_key} / нужно +{rules.min_weekly_key}"
    return 0, "на этой неделе ключей не найдено"



def score_recent_activity(recent_runs_count: int) -> tuple[int, str]:
    if recent_runs_count >= 5:
        return 5, f"активный игрок: последних ключей найдено {recent_runs_count}"
    if recent_runs_count > 0:
        return 3, f"активность есть, но небольшая: последних ключей найдено {recent_runs_count}"
    return 0, "последних M+ ключей не найдено"



def make_raiderio_verdict(total_score: int, ilvl: float, rules: Rules) -> str:
    if ilvl < rules.min_ilvl - 15:
        return "ОТКАЗАТЬ"
    if total_score >= 80:
        return "ПРИНЯТЬ"
    if total_score >= 60:
        return "ТЕСТОВЫЙ РЕЙД"
    if total_score >= 45:
        return "РУЧНАЯ ПРОВЕРКА"
    return "ОТКАЗАТЬ"



def analyze_raiderio_candidate(data: dict[str, Any], rules: Rules) -> dict[str, Any]:
    ilvl = extract_ilvl(data)
    mplus_score = extract_mplus_score(data)
    max_weekly_key = extract_max_weekly_key(data)
    recent_runs_count = extract_recent_runs_count(data)
    raid_kills, raid_name = extract_raid_progress(data, rules.raid_difficulty)
    best_runs = extract_best_runs(data)

    checks = [score_ilvl(ilvl, rules)]
    max_possible_score = 35

    if rules.check_mplus:
        checks.append(score_mplus(mplus_score, rules))
        max_possible_score += 25

    checks.append(score_raid(raid_kills, raid_name, rules))
    max_possible_score += 25

    if rules.check_mplus:
        checks.append(score_weekly_key(max_weekly_key, rules))
        checks.append(score_recent_activity(recent_runs_count))
        max_possible_score += 15
    else:
        checks.append((0, "проверка Mythic+ прогресса отключена: M+ score, недельный ключ и recent runs не влияют на вердикт"))

    raw_score = sum(points for points, _reason in checks)
    total_score = round((raw_score / max_possible_score) * 100) if max_possible_score else 0
    verdict = make_raiderio_verdict(total_score, ilvl, rules)

    return {
        "name": data.get("name", "unknown"),
        "realm": data.get("realm", "unknown"),
        "region": data.get("region", "unknown"),
        "class": data.get("class", "unknown"),
        "race": data.get("race", "unknown"),
        "active_spec_name": data.get("active_spec_name", "unknown"),
        "active_spec_role": data.get("active_spec_role", "unknown"),
        "guild": (data.get("guild") or {}).get("name", "без гильдии"),
        "profile_url": data.get("profile_url"),
        "avatar_url": extract_avatar_url(data),
        "ilvl": ilvl,
        "mplus_score": mplus_score,
        "max_weekly_key": max_weekly_key,
        "recent_runs_count": recent_runs_count,
        "raid_kills": raid_kills,
        "raid_name": raid_name,
        "total_score": total_score,
        "raw_score": raw_score,
        "max_possible_score": max_possible_score,
        "mplus_check_enabled": rules.check_mplus,
        "verdict": verdict,
        "checks": checks,
        "best_runs": best_runs,
    }



def format_raiderio_report(report: dict[str, Any], rules: Rules) -> str:
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("RAIDER.IO — АНАЛИЗ КАНДИДАТА")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Персонаж: {report['name']} - {report['realm']} [{report['region']}]")
    lines.append(f"Класс/спек: {report['class']} / {report['active_spec_name']} / {report['active_spec_role']}")
    lines.append(f"Раса: {report['race']}")
    lines.append(f"Гильдия: {report['guild']}")

    if report["profile_url"]:
        lines.append(f"Raider.IO: {report['profile_url']}")

    if report.get("avatar_url"):
        lines.append(f"Аватарка: {report['avatar_url']}")

    lines.append("")
    lines.append(f"Цель проверки: {rules.target.upper()}")
    if rules.check_mplus:
        lines.append(
            f"Требования: ilvl {rules.min_ilvl}, score {rules.min_score}, "
            f"ключ недели +{rules.min_weekly_key}, рейд {rules.raid_difficulty} "
            f"{rules.min_raid_bosses}+ босс(ов)"
        )
    else:
        lines.append(
            f"Требования: ilvl {rules.min_ilvl}, рейд {rules.raid_difficulty} "
            f"{rules.min_raid_bosses}+ босс(ов)"
        )
        lines.append("Проверка Mythic+ прогресса: отключена")
    lines.append("")
    lines.append("Оценка:")

    for points, reason in report["checks"]:
        lines.append(f"{points:>2} балл(ов) — {reason}")

    lines.append("")
    lines.append(f"ИТОГО: {report['total_score']} / 100")
    if not rules.check_mplus:
        lines.append(
            f"Сырой счет без M+: {report.get('raw_score', 0)} / "
            f"{report.get('max_possible_score', 60)}"
        )
    lines.append(f"ВЕРДИКТ: {report['verdict']}")

    if rules.check_mplus and report["best_runs"]:
        lines.append("")
        lines.append("Лучшие M+ ключи:")
        for run in report["best_runs"]:
            lines.append(f"- {run}")
    elif not rules.check_mplus:
        lines.append("")
        lines.append("Лучшие M+ ключи не выводятся, потому что проверка Mythic+ прогресса отключена.")

    lines.append("")
    lines.append("Пояснение:")
    if report["verdict"] == "ПРИНЯТЬ":
        lines.append("Кандидат выглядит достаточно сильным по базовым Raider.IO метрикам.")
    elif report["verdict"] == "ТЕСТОВЫЙ РЕЙД":
        lines.append("Кандидата можно брать на тестовый рейд, но желательно проверить WarcraftLogs.")
    elif report["verdict"] == "РУЧНАЯ ПРОВЕРКА":
        lines.append("Данные неоднозначные. Нужны логи, разговор и пробный рейд/ключ.")
    else:
        lines.append("По текущим требованиям кандидат слабоват или данных недостаточно.")

    return "\n".join(lines)


# ============================================================
# WARCRAFTLOGS CLIENT + ЛОГИКА
# ============================================================


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
                "Не найдены WCL_CLIENT_ID и WCL_CLIENT_SECRET.\n"
                "Добавь их в .env или переменные окружения."
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
            timeout=25,
        )

        if response.status_code == 401:
            response = requests.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=25,
            )

        if response.status_code >= 400:
            raise WarcraftLogsError(
                f"Ошибка получения токена WarcraftLogs: {response.status_code}\n{response.text}"
            )

        data = response.json()
        access_token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)

        if not access_token:
            raise WarcraftLogsError(f"WarcraftLogs не вернул access_token:\n{pretty_json(data)}")

        self._access_token = access_token
        self._token_expires_at = time.time() + int(expires_in) - 60
        return access_token

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self.get_access_token()

        response = requests.post(
            self.GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "WowRaidCandidateGUI/1.0",
            },
            timeout=35,
        )

        if response.status_code >= 400:
            raise WarcraftLogsError(
                f"Ошибка GraphQL WarcraftLogs: {response.status_code}\n{response.text}"
            )

        data = response.json()
        if data.get("errors"):
            raise WarcraftLogsError(f"GraphQL errors:\n{pretty_json(data['errors'])}")

        return data["data"]


CHARACTER_RANKINGS_QUERY = """
query GetCharacterRankings(
  $name: String!,
  $serverSlug: String!,
  $serverRegion: String!
) {
  characterData {
    character(
      name: $name,
      serverSlug: $serverSlug,
      serverRegion: $serverRegion
    ) {
      id
      name
      canonicalID
      server {
        name
        slug
        region {
          name
          compactName
        }
      }
      zoneRankings
    }
  }
}
"""

REPORT_FIGHTS_QUERY = """
query GetReportFights($code: String!) {
  reportData {
    report(code: $code) {
      code
      title
      startTime
      endTime
      fights {
        id
        encounterID
        name
        difficulty
        kill
        startTime
        endTime
      }
      masterData {
        actors {
          id
          name
          type
          subType
          server
        }
      }
    }
  }
}
"""

REPORT_DEATHS_QUERY = """
query GetDeaths(
  $code: String!,
  $startTime: Float!,
  $endTime: Float!,
  $sourceID: Int
) {
  reportData {
    report(code: $code) {
      events(
        dataType: Deaths,
        startTime: $startTime,
        endTime: $endTime,
        sourceID: $sourceID
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""

REPORT_DAMAGE_TAKEN_QUERY = """
query GetDamageTaken(
  $code: String!,
  $startTime: Float!,
  $endTime: Float!,
  $sourceID: Int
) {
  reportData {
    report(code: $code) {
      table(
        dataType: DamageTaken,
        startTime: $startTime,
        endTime: $endTime,
        sourceID: $sourceID
      )
    }
  }
}
"""

REPORT_CASTS_QUERY = """
query GetCasts(
  $code: String!,
  $startTime: Float!,
  $endTime: Float!,
  $sourceID: Int
) {
  reportData {
    report(code: $code) {
      table(
        dataType: Casts,
        startTime: $startTime,
        endTime: $endTime,
        sourceID: $sourceID
      )
    }
  }
}
"""



def walk_numbers_by_keys(obj: Any, wanted_keys: set[str]) -> list[float]:
    result: list[float] = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            key_lower = str(key).lower()
            if key_lower in wanted_keys and isinstance(value, int | float):
                result.append(float(value))
            result.extend(walk_numbers_by_keys(value, wanted_keys))

    elif isinstance(obj, list):
        for item in obj:
            result.extend(walk_numbers_by_keys(item, wanted_keys))

    return result



def extract_wcl_summary(zone_rankings: Any) -> dict[str, Any]:
    percentile_keys = {
        "rankpercent",
        "percentile",
        "bestpercent",
        "best",
        "medianpercent",
        "median",
    }

    performance_keys = {
        "bestperformanceaverage",
        "medianperformanceaverage",
        "allstarpoints",
    }

    percentiles = walk_numbers_by_keys(zone_rankings, percentile_keys)
    performances = walk_numbers_by_keys(zone_rankings, performance_keys)

    clean_percentiles = [value for value in percentiles if 0 <= value <= 100]
    clean_performances = [value for value in performances if value >= 0]

    if clean_percentiles:
        avg_percentile = statistics.mean(clean_percentiles)
        best_percentile = max(clean_percentiles)
        median_percentile = statistics.median(clean_percentiles)
        logs_count = len(clean_percentiles)
    else:
        avg_percentile = 0
        best_percentile = 0
        median_percentile = 0
        logs_count = 0

    return {
        "avg_percentile": round(avg_percentile, 1),
        "best_percentile": round(best_percentile, 1),
        "median_percentile": round(median_percentile, 1),
        "logs_count": logs_count,
        "performance_values_found": len(clean_performances),
    }



def score_wcl(summary: dict[str, Any]) -> tuple[int, list[str]]:
    points = 0
    reasons: list[str] = []

    avg = summary["avg_percentile"]
    median = summary["median_percentile"]
    best = summary["best_percentile"]
    logs_count = summary["logs_count"]

    if logs_count == 0:
        reasons.append("Логов/перцентилей не найдено — игрок не оценивается по WarcraftLogs.")
        return 0, reasons

    if avg >= 75:
        points += 35
        reasons.append(f"Средний percentile сильный: {avg}")
    elif avg >= 55:
        points += 25
        reasons.append(f"Средний percentile нормальный: {avg}")
    elif avg >= 35:
        points += 12
        reasons.append(f"Средний percentile слабоват: {avg}")
    else:
        reasons.append(f"Средний percentile низкий: {avg}")

    if median >= 60:
        points += 20
        reasons.append(f"Медианный percentile хороший: {median}")
    elif median >= 40:
        points += 10
        reasons.append(f"Медианный percentile средний: {median}")
    else:
        reasons.append(f"Медианный percentile низкий: {median}")

    if best >= 90:
        points += 15
        reasons.append(f"Есть высокий лучший лог: {best}")
    elif best >= 75:
        points += 8
        reasons.append(f"Есть хороший лучший лог: {best}")
    else:
        reasons.append(f"Лучший лог не выглядит сильным: {best}")

    if logs_count >= 8:
        points += 10
        reasons.append(f"Данных достаточно: найдено значений {logs_count}")
    elif logs_count >= 3:
        points += 5
        reasons.append(f"Данных немного, но оценивать можно: найдено значений {logs_count}")
    else:
        reasons.append(f"Данных мало: найдено значений {logs_count}")

    return points, reasons



def wcl_verdict(points: int) -> str:
    if points >= 70:
        return "СИЛЬНЫЙ КАНДИДАТ ПО ЛОГАМ"
    if points >= 50:
        return "НОРМАЛЬНЫЙ КАНДИДАТ, МОЖНО НА ТЕСТ"
    if points >= 30:
        return "СОМНИТЕЛЬНО, НУЖНА РУЧНАЯ ПРОВЕРКА"
    return "СЛАБЫЕ ИЛИ ПУСТЫЕ ЛОГИ"



def analyze_wcl_character(name: str, server_slug: str, region: str) -> str:
    client = WarcraftLogsClient()
    data = client.graphql(
        CHARACTER_RANKINGS_QUERY,
        {
            "name": name.strip(),
            "serverSlug": server_slug.strip(),
            "serverRegion": normalize_wcl_region(region),
        },
    )

    character = data["characterData"]["character"]
    if not character:
        return "Персонаж не найден в WarcraftLogs."

    summary = extract_wcl_summary(character.get("zoneRankings"))
    points, reasons = score_wcl(summary)

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("WARCRAFTLOGS — БЫСТРЫЙ АНАЛИЗ ПЕРСОНАЖА")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Персонаж: {character['name']}")
    lines.append(f"Сервер: {character['server']['name']}")
    lines.append("")
    lines.append(f"Средний percentile: {summary['avg_percentile']}")
    lines.append(f"Медианный percentile: {summary['median_percentile']}")
    lines.append(f"Лучший percentile: {summary['best_percentile']}")
    lines.append(f"Найдено значений: {summary['logs_count']}")
    lines.append("")
    lines.append("Оценка:")
    for reason in reasons:
        lines.append(f"- {reason}")
    lines.append("")
    lines.append(f"Баллы WarcraftLogs: {points} / 80")
    lines.append(f"Вердикт: {wcl_verdict(points)}")
    lines.append("")
    lines.append("Важно: это быстрый профильный анализ. Для смертей, сейвов и механик нужен конкретный report code.")
    return "\n".join(lines)



def find_player_actor(report: dict[str, Any], player_name: str) -> dict[str, Any] | None:
    actors = report["masterData"]["actors"]
    for actor in actors:
        if actor.get("type") == "Player" and actor.get("name", "").lower() == player_name.lower():
            return actor
    return None



def select_fight(report: dict[str, Any], fight_id: int | None = None) -> dict[str, Any]:
    fights = report["fights"]
    boss_fights = [fight for fight in fights if fight.get("encounterID") and fight.get("name")]

    if not boss_fights:
        raise RuntimeError("В отчете не найдено боев с боссами.")

    if fight_id is not None:
        for fight in boss_fights:
            if fight["id"] == fight_id:
                return fight
        raise RuntimeError(f"Бой с id={fight_id} не найден.")

    return boss_fights[-1]



def summarize_deaths(events_data: list[dict[str, Any]]) -> dict[str, Any]:
    if not events_data:
        return {"death_count": 0, "death_reasons": []}

    reasons: list[str] = []
    for event in events_data:
        ability = event.get("ability") or {}
        ability_name = ability.get("name") or event.get("abilityGameID") or "неизвестная причина"
        reasons.append(str(ability_name))

    return {"death_count": len(events_data), "death_reasons": reasons}



def extract_total_from_table(table: Any) -> int:
    totals: list[int] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if str(key).lower() in {"total", "amount"} and isinstance(value, int | float):
                    totals.append(int(value))
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(table)
    return sum(totals)



def count_casts_from_table(table: Any) -> int:
    casts = 0

    def walk(obj: Any) -> None:
        nonlocal casts
        if isinstance(obj, dict):
            for key, value in obj.items():
                if str(key).lower() in {"total", "casts", "count"} and isinstance(value, int | float):
                    casts += int(value)
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(table)
    return casts



def analyze_wcl_report(code: str, player_name: str, fight_id: int | None = None) -> str:
    client = WarcraftLogsClient()

    report_data = client.graphql(REPORT_FIGHTS_QUERY, {"code": code.strip()})
    report = report_data["reportData"]["report"]

    if not report:
        return "Отчет не найден."

    player = find_player_actor(report, player_name.strip())
    if not player:
        names = sorted(
            actor.get("name", "")
            for actor in report["masterData"]["actors"]
            if actor.get("type") == "Player"
        )
        return (
            f"Игрок {player_name} не найден среди участников отчета.\n\n"
            f"Игроки в логе:\n" + "\n".join(f"- {name}" for name in names if name)
        )

    fight = select_fight(report, fight_id)

    variables = {
        "code": code.strip(),
        "startTime": float(fight["startTime"]),
        "endTime": float(fight["endTime"]),
        "sourceID": int(player["id"]),
    }

    deaths_response = client.graphql(REPORT_DEATHS_QUERY, variables)
    deaths_data = deaths_response["reportData"]["report"]["events"]["data"]
    deaths_summary = summarize_deaths(deaths_data)

    damage_response = client.graphql(REPORT_DAMAGE_TAKEN_QUERY, variables)
    damage_table = damage_response["reportData"]["report"]["table"]
    damage_taken_total = extract_total_from_table(damage_table)

    casts_response = client.graphql(REPORT_CASTS_QUERY, variables)
    casts_table = casts_response["reportData"]["report"]["table"]
    casts_count = count_casts_from_table(casts_table)

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("WARCRAFTLOGS — ГЛУБОКИЙ АНАЛИЗ ЛОГА")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Отчет: {report['title']}")
    lines.append(f"Код: {report['code']}")
    lines.append(f"Игрок: {player['name']}")
    lines.append(f"Бой: {fight['name']} / fight id: {fight['id']}")
    lines.append(f"Убийство босса: {'да' if fight.get('kill') else 'нет'}")
    lines.append("")
    lines.append("Смерти:")
    lines.append(f"- Количество смертей: {deaths_summary['death_count']}")

    if deaths_summary["death_reasons"]:
        lines.append("- Причины/события смерти:")
        for reason in deaths_summary["death_reasons"]:
            lines.append(f"  - {reason}")

    lines.append("")
    lines.append("Полученный урон:")
    lines.append(f"- Найденный total damage taken: {damage_taken_total}")
    lines.append("")
    lines.append("Касты/прожатия:")
    lines.append(f"- Найдено cast/count значений: {casts_count}")
    lines.append("")
    lines.append("Вердикт по логу:")

    death_count = deaths_summary["death_count"]
    if death_count >= 2:
        lines.append("Плохо: несколько смертей в одном бою. Нужен ручной разбор причин.")
    elif death_count == 1:
        lines.append("Средне: была смерть. Нужно проверить, личная ошибка или вайп/механика рейда.")
    else:
        lines.append("Хорошо: смертей в выбранном бою не найдено.")

    lines.append("")
    lines.append("Важно: механики и сейвы лучше оценивать правилами под конкретного босса.")
    return "\n".join(lines)


# ============================================================
# GUI
# ============================================================


class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("WoW Raid Candidate Analyzer")
        self.geometry("1120x760")
        self.minsize(980, 650)

        self._build_style()
        self._build_layout()
        self._set_default_values()

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10), padding=6)
        style.configure("TEntry", font=("Segoe UI", 10))
        style.configure("TCombobox", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Small.TLabel", font=("Segoe UI", 9))

    def _build_layout(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)

        title = ttk.Label(container, text="WoW Raid Candidate Analyzer", style="Header.TLabel")
        title.pack(anchor="w")

        subtitle = ttk.Label(
            container,
            text="Raider.IO + WarcraftLogs: одетость, прогресс, M+, перцентили, смерти и базовый разбор лога.",
            style="Small.TLabel",
        )
        subtitle.pack(anchor="w", pady=(2, 10))

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True)

        self.raider_tab = ttk.Frame(self.notebook, padding=10)
        self.raid_today_tab = ttk.Frame(self.notebook, padding=10)
        self.wcl_profile_tab = ttk.Frame(self.notebook, padding=10)
        self.wcl_report_tab = ttk.Frame(self.notebook, padding=10)
        self.settings_tab = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(self.raider_tab, text="Raider.IO кандидат")
        self.notebook.add(self.raid_today_tab, text="Кого брать сегодня")
        self.notebook.add(self.wcl_profile_tab, text="WCL профиль")
        self.notebook.add(self.wcl_report_tab, text="WCL лог боя")
        self.notebook.add(self.settings_tab, text="Настройки")

        self.raider_avatar_photo = None
        self.raider_avatar_bytes = None

        self._build_raider_tab()
        self._build_raid_today_tab()
        self._build_wcl_profile_tab()
        self._build_wcl_report_tab()
        self._build_settings_tab()

    def _make_output(self, parent: ttk.Frame) -> tk.Text:
        output = tk.Text(parent, wrap="word", font=("Consolas", 10), height=20, undo=False)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=output.yview)
        output.configure(yscrollcommand=scroll.set)
        output.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        scroll.grid(row=1, column=1, sticky="ns", pady=(10, 0))
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        return output

    def _set_output(self, text_widget: tk.Text, text: str) -> None:
        text_widget.configure(state="normal")
        text_widget.delete("1.0", "end")
        text_widget.insert("1.0", text)
        text_widget.configure(state="normal")

    def _append_output(self, text_widget: tk.Text, text: str) -> None:
        text_widget.configure(state="normal")
        text_widget.insert("end", text)
        text_widget.see("end")
        text_widget.configure(state="normal")

    def _build_raider_tab(self) -> None:
        form = ttk.LabelFrame(self.raider_tab, text="Данные персонажа и требования", padding=10)
        form.grid(row=0, column=0, sticky="ew")
        self.raider_tab.columnconfigure(0, weight=1)

        self.region_var = tk.StringVar()
        self.realm_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.min_ilvl_var = tk.StringVar()
        self.min_score_var = tk.StringVar()
        self.min_weekly_key_var = tk.StringVar()
        self.raid_difficulty_var = tk.StringVar()
        self.min_raid_bosses_var = tk.StringVar()
        self.check_mplus_var = tk.BooleanVar(value=True)

        fields = [
            ("Регион", self.region_var, 0, 0),
            ("Сервер/realm slug", self.realm_var, 0, 2),
            ("Ник", self.name_var, 0, 4),
        ]

        for label, var, row, col in fields:
            ttk.Label(form, text=label).grid(row=row, column=col, sticky="w", padx=(0, 6), pady=4)
            ttk.Entry(form, textvariable=var, width=22).grid(row=row, column=col + 1, sticky="ew", padx=(0, 16), pady=4)

        ttk.Label(form, text="Цель").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=4)
        self.target_combo = ttk.Combobox(
            form,
            textvariable=self.target_var,
            values=["normal", "heroic", "mythic"],
            state="readonly",
            width=20,
        )
        self.target_combo.grid(row=1, column=1, sticky="ew", padx=(0, 16), pady=4)
        self.target_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_target_defaults())

        ttk.Label(form, text="Мин. ilvl").grid(row=1, column=2, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=self.min_ilvl_var, width=12).grid(row=1, column=3, sticky="ew", padx=(0, 16), pady=4)

        ttk.Label(form, text="Мин. M+ score").grid(row=1, column=4, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=self.min_score_var, width=12).grid(row=1, column=5, sticky="ew", padx=(0, 16), pady=4)

        ttk.Label(form, text="Мин. ключ недели").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=self.min_weekly_key_var, width=12).grid(row=2, column=1, sticky="ew", padx=(0, 16), pady=4)

        ttk.Label(form, text="Сложность рейда").grid(row=2, column=2, sticky="w", padx=(0, 6), pady=4)
        ttk.Combobox(
            form,
            textvariable=self.raid_difficulty_var,
            values=["normal", "heroic", "mythic"],
            state="readonly",
            width=20,
        ).grid(row=2, column=3, sticky="ew", padx=(0, 16), pady=4)

        ttk.Label(form, text="Мин. боссов").grid(row=2, column=4, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=self.min_raid_bosses_var, width=12).grid(row=2, column=5, sticky="ew",
                                                                              padx=(0, 16), pady=4)

        ttk.Checkbutton(
            form,
            text="Проверять Mythic+ прогресс: M+ score, недельный ключ и recent runs",
            variable=self.check_mplus_var,
        ).grid(row=3, column=0, columnspan=6, sticky="w", pady=(8, 0))

        button_row = ttk.Frame(form)
        button_row.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(10, 0))

        self.raider_button = ttk.Button(button_row, text="Проверить через Raider.IO", command=self.run_raiderio)
        self.raider_button.pack(side="left")

        ttk.Button(button_row, text="Очистить вывод", command=lambda: self._set_output(self.raider_output, "")).pack(side="left", padx=(8, 0))

        for col in range(6):
            form.columnconfigure(col, weight=1)

        result_area = ttk.Frame(self.raider_tab)
        result_area.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        result_area.columnconfigure(0, weight=0)
        result_area.columnconfigure(1, weight=1)
        result_area.rowconfigure(0, weight=1)
        self.raider_tab.rowconfigure(1, weight=1)

        avatar_box = ttk.LabelFrame(result_area, text="Персонаж", padding=10)
        avatar_box.grid(row=0, column=0, sticky="ns", padx=(0, 10))

        self.raider_avatar_label = ttk.Label(
            avatar_box,
            text="Аватарка появится после поиска",
            anchor="center",
            justify="center",
            width=20,
        )
        self.raider_avatar_label.pack(pady=(0, 8))

        self.raider_avatar_info_label = ttk.Label(
            avatar_box,
            text="Нет данных",
            anchor="center",
            justify="center",
            style="Small.TLabel",
        )
        self.raider_avatar_info_label.pack(fill="x")

        output_frame = ttk.Frame(result_area)
        output_frame.grid(row=0, column=1, sticky="nsew")
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(1, weight=1)

        self.raider_output = self._make_output(output_frame)

    def _build_raid_today_tab(self) -> None:
        self.raid_today_tab.columnconfigure(0, weight=1)
        self.raid_today_tab.rowconfigure(1, weight=1)

        form = ttk.LabelFrame(self.raid_today_tab, text="Ростер на сегодняшний рейд", padding=10)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)

        self.raid_today_default_region_var = tk.StringVar(value="eu")
        self.raid_today_default_realm_var = tk.StringVar(value="howling-fjord")

        defaults_frame = ttk.Frame(form)
        defaults_frame.grid(row=0, column=0, columnspan=2, sticky="ew")

        ttk.Label(defaults_frame, text="Регион по умолчанию").pack(side="left", padx=(0, 6))
        ttk.Entry(defaults_frame, textvariable=self.raid_today_default_region_var, width=10).pack(side="left", padx=(0, 16))

        ttk.Label(defaults_frame, text="Сервер по умолчанию").pack(side="left", padx=(0, 6))
        ttk.Entry(defaults_frame, textvariable=self.raid_today_default_realm_var, width=24).pack(side="left", padx=(0, 16))

        hint = ttk.Label(
            form,
            text=(
                "Формат: ник;сервер;регион;роль. Можно короче: ник — тогда сервер и регион возьмутся из полей выше. "
                "Роль необязательна: tank, healer, dps. Требования берутся из вкладки Raider.IO кандидат."
            ),
            style="Small.TLabel",
            wraplength=1000,
        )
        hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 6))

        input_frame = ttk.LabelFrame(form, text="Список игроков", padding=8)
        input_frame.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(4, 0))
        input_frame.columnconfigure(0, weight=1)
        input_frame.rowconfigure(0, weight=1)

        self.raid_today_input = tk.Text(input_frame, wrap="word", font=("Consolas", 10), height=9)
        self.raid_today_input.grid(row=0, column=0, sticky="nsew")

        example_frame = ttk.LabelFrame(form, text="Пример", padding=8)
        example_frame.grid(row=2, column=1, sticky="nsew", padx=(8, 0), pady=(4, 0))
        example_frame.columnconfigure(0, weight=1)
        example_frame.rowconfigure(0, weight=1)

        example_text = tk.Text(example_frame, wrap="word", font=("Consolas", 9), height=9)
        example_text.grid(row=0, column=0, sticky="nsew")
        example_text.insert(
            "1.0",
            "Templar;howling-fjord;eu;dps"
            "Tankone;howling-fjord;eu;tank"
            "Healcat;howling-fjord;eu;healer"
            "Magebro",
        )
        example_text.configure(state="disabled")

        button_row = ttk.Frame(form)
        button_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        self.raid_today_button = ttk.Button(
            button_row,
            text="Собрать состав по Raider.IO",
            command=self.run_raid_today,
        )
        self.raid_today_button.pack(side="left")

        ttk.Button(
            button_row,
            text="Очистить вывод",
            command=lambda: self._set_output(self.raid_today_output, ""),
        ).pack(side="left", padx=(8, 0))

        self.raid_today_output = self._make_output(self.raid_today_tab)

    def _make_avatar_photo(self, image_bytes: bytes, size: tuple[int, int] = (128, 128)):
        if Image is None or ImageTk is None:
            raise RuntimeError("Для отображения аватарок установи Pillow: pip install pillow")

        image = Image.open(BytesIO(image_bytes)).convert("RGBA")
        image.thumbnail(size, Image.LANCZOS)

        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        x = (size[0] - image.width) // 2
        y = (size[1] - image.height) // 2
        canvas.paste(image, (x, y), image)

        return ImageTk.PhotoImage(canvas)

    def _clear_raider_avatar(self) -> None:
        self.raider_avatar_photo = None
        self.raider_avatar_bytes = None
        self.raider_avatar_label.configure(
            image="",
            text="Аватарка появится после поиска",
        )
        self.raider_avatar_info_label.configure(text="Нет данных")

    def _show_raider_avatar(self, report: dict[str, Any], image_bytes: bytes | None) -> None:
        info = (
            f"{report.get('name', 'unknown')}"
            f"{report.get('realm', 'unknown')} [{report.get('region', 'unknown')}]"
            f"{report.get('class', 'unknown')} / {report.get('active_spec_name', 'unknown')}"
        )
        self.raider_avatar_info_label.configure(text=info)

        if not image_bytes:
            self.raider_avatar_photo = None
            self.raider_avatar_label.configure(image="", text="Аватарка не найдена")
            return

        try:
            self.raider_avatar_bytes = image_bytes
            self.raider_avatar_photo = self._make_avatar_photo(image_bytes)
            self.raider_avatar_label.configure(image=self.raider_avatar_photo, text="")
        except Exception as exc:
            self.raider_avatar_photo = None
            self.raider_avatar_label.configure(
                image="",
                text=f"Не удалось показать аватарку:{exc}",
            )

    def _build_wcl_profile_tab(self) -> None:
        form = ttk.LabelFrame(self.wcl_profile_tab, text="Быстрый анализ WarcraftLogs по персонажу", padding=10)
        form.grid(row=0, column=0, sticky="ew")
        self.wcl_profile_tab.columnconfigure(0, weight=1)

        self.wcl_name_var = tk.StringVar()
        self.wcl_server_slug_var = tk.StringVar()
        self.wcl_region_var = tk.StringVar()

        ttk.Label(form, text="Ник").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=self.wcl_name_var, width=24).grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=4)

        ttk.Label(form, text="Сервер slug").grid(row=0, column=2, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=self.wcl_server_slug_var, width=24).grid(row=0, column=3, sticky="ew", padx=(0, 16), pady=4)

        ttk.Label(form, text="Регион").grid(row=0, column=4, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=self.wcl_region_var, width=10).grid(row=0, column=5, sticky="ew", padx=(0, 16), pady=4)

        button_row = ttk.Frame(form)
        button_row.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(10, 0))

        self.wcl_profile_button = ttk.Button(button_row, text="Проверить WCL профиль", command=self.run_wcl_profile)
        self.wcl_profile_button.pack(side="left")

        ttk.Button(button_row, text="Очистить вывод", command=lambda: self._set_output(self.wcl_profile_output, "")).pack(side="left", padx=(8, 0))

        for col in range(6):
            form.columnconfigure(col, weight=1)

        self.wcl_profile_output = self._make_output(self.wcl_profile_tab)

    def _build_wcl_report_tab(self) -> None:
        form = ttk.LabelFrame(self.wcl_report_tab, text="Глубокий анализ конкретного лога", padding=10)
        form.grid(row=0, column=0, sticky="ew")
        self.wcl_report_tab.columnconfigure(0, weight=1)

        self.report_code_var = tk.StringVar()
        self.report_player_var = tk.StringVar()
        self.report_fight_id_var = tk.StringVar()

        ttk.Label(form, text="Report code").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=self.report_code_var, width=36).grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=4)

        ttk.Label(form, text="Игрок").grid(row=0, column=2, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=self.report_player_var, width=24).grid(row=0, column=3, sticky="ew", padx=(0, 16), pady=4)

        ttk.Label(form, text="Fight ID, если нужен").grid(row=0, column=4, sticky="w", padx=(0, 6), pady=4)
        ttk.Entry(form, textvariable=self.report_fight_id_var, width=12).grid(row=0, column=5, sticky="ew", padx=(0, 16), pady=4)

        button_row = ttk.Frame(form)
        button_row.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(10, 0))

        self.wcl_report_button = ttk.Button(button_row, text="Разобрать лог", command=self.run_wcl_report)
        self.wcl_report_button.pack(side="left")

        ttk.Button(button_row, text="Очистить вывод", command=lambda: self._set_output(self.wcl_report_output, "")).pack(side="left", padx=(8, 0))

        hint = ttk.Label(
            form,
            text="Report code — это часть ссылки после /reports/. Если fight id не указан, берется последний бой с боссом.",
            style="Small.TLabel",
        )
        hint.grid(row=2, column=0, columnspan=6, sticky="w", pady=(8, 0))

        for col in range(6):
            form.columnconfigure(col, weight=1)

        self.wcl_report_output = self._make_output(self.wcl_report_tab)

    def _build_settings_tab(self) -> None:
        content = ttk.Frame(self.settings_tab)
        content.pack(fill="both", expand=True)

        text = tk.Text(content, wrap="word", font=("Consolas", 10), height=20)
        text.pack(fill="both", expand=True)

        settings_text = """
Файлы рядом с программой:

.env
.env.example
.gitignore
requirements.txt

Пример .env:

WCL_CLIENT_ID=сюда_твой_client_id
WCL_CLIENT_SECRET=сюда_твой_client_secret
RAIDERIO_ACCESS_KEY=

Пример .env.example:

WCL_CLIENT_ID=your_warcraftlogs_client_id_here
WCL_CLIENT_SECRET=your_warcraftlogs_client_secret_here
RAIDERIO_ACCESS_KEY=your_raiderio_access_key_here_optional

Пример .gitignore:

.env
.venv/
__pycache__/
*.pyc

Пример requirements.txt:

requests
python-dotenv

Важно:
- .env нельзя заливать в GitHub.
- .env.example можно заливать в GitHub.
- WarcraftLogs client secret, который уже был отправлен в чат или куда-то публично, лучше перевыпустить.
- Raider.IO access key необязателен для базового использования, но полезен для нормального приложения.
""".strip()

        text.insert("1.0", settings_text)
        text.configure(state="disabled")

    def _set_default_values(self) -> None:
        self.region_var.set("eu")
        self.realm_var.set("гордунни")
        self.name_var.set("Кипет")
        self.target_var.set("heroic")
        self._apply_target_defaults()

        self.wcl_name_var.set("Кипет")
        self.wcl_server_slug_var.set("Гордунни")
        self.wcl_region_var.set("EU")

    def _apply_target_defaults(self) -> None:
        target = self.target_var.get() or "heroic"
        rules = DEFAULT_RULES[target]
        self.min_ilvl_var.set(str(rules.min_ilvl))
        self.min_score_var.set(str(rules.min_score))
        self.min_weekly_key_var.set(str(rules.min_weekly_key))
        self.raid_difficulty_var.set(rules.raid_difficulty)
        self.min_raid_bosses_var.set(str(rules.min_raid_bosses))

    def _build_rules_from_form(self) -> Rules:
        target = self.target_var.get() or "heroic"
        defaults = DEFAULT_RULES[target]

        return Rules(
            target=target,
            min_ilvl=safe_float(self.min_ilvl_var.get(), defaults.min_ilvl),
            min_score=safe_float(self.min_score_var.get(), defaults.min_score),
            min_weekly_key=safe_int(self.min_weekly_key_var.get(), defaults.min_weekly_key),
            raid_difficulty=self.raid_difficulty_var.get() or defaults.raid_difficulty,
            min_raid_bosses=safe_int(self.min_raid_bosses_var.get(), defaults.min_raid_bosses),
            check_mplus=self.check_mplus_var.get(),
        )

    def _run_threaded(self, button: ttk.Button, output: tk.Text, worker, on_success=None) -> None:
        button.configure(state="disabled")
        self._set_output(output, "Выполняю запрос...")

        def task() -> None:
            success = True
            try:
                result = worker()
            except Exception as exc:  # GUI должен показать ошибку, а не упасть
                success = False
                result = f"ОШИБКА:{exc}"

            def finish() -> None:
                if success and on_success is not None:
                    text_result = result[0] if isinstance(result, tuple) else str(result)
                    self._set_output(output, text_result)
                    on_success(result)
                else:
                    self._set_output(output, str(result))
                button.configure(state="normal")

            self.after(0, finish)

        threading.Thread(target=task, daemon=True).start()

    def _parse_raid_today_lines(self) -> list[dict[str, str]]:
        raw_text = self.raid_today_input.get("1.0", "end").strip()
        if not raw_text:
            raise ValueError("Вставь список игроков.")

        default_region = self.raid_today_default_region_var.get().strip() or "eu"
        default_realm = self.raid_today_default_realm_var.get().strip()
        if not default_realm:
            raise ValueError("Укажи сервер по умолчанию или сервер в каждой строке.")

        players: list[dict[str, str]] = []

        for line_no, line in enumerate(raw_text.splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if ";" in line:
                parts = [part.strip() for part in line.split(";")]
            elif "," in line:
                parts = [part.strip() for part in line.split(",")]
            else:
                parts = line.split()

            parts = [part for part in parts if part]
            if not parts:
                continue

            name = parts[0]
            realm = parts[1] if len(parts) >= 2 else default_realm
            region = parts[2] if len(parts) >= 3 else default_region
            role = parts[3] if len(parts) >= 4 else ""

            players.append(
                {
                    "name": name,
                    "realm": realm,
                    "region": region,
                    "role": role.lower(),
                    "line_no": str(line_no),
                }
            )

        if not players:
            raise ValueError("Не удалось разобрать список игроков.")

        return players

    def _normalize_role_for_report(self, report: dict[str, Any], manual_role: str = "") -> str:
        role = (manual_role or report.get("active_spec_role") or "unknown").strip().lower()

        aliases = {
            "tank": "Танки",
            "tanks": "Танки",
            "танк": "Танки",
            "танки": "Танки",
            "healer": "Хилы",
            "heal": "Хилы",
            "healing": "Хилы",
            "хил": "Хилы",
            "хилы": "Хилы",
            "лекарь": "Хилы",
            "лекари": "Хилы",
            "dps": "ДД",
            "damage": "ДД",
            "dd": "ДД",
            "дд": "ДД",
        }

        return aliases.get(role, "Роль не определена")

    def _format_raid_today_report(self, rows: list[dict[str, Any]], errors: list[str]) -> str:
        priority = {
            "ПРИНЯТЬ": 0,
            "ТЕСТОВЫЙ РЕЙД": 1,
            "РУЧНАЯ ПРОВЕРКА": 2,
            "ОТКАЗАТЬ": 3,
        }

        rows = sorted(
            rows,
            key=lambda row: (
                priority.get(row["verdict"], 99),
                -row["total_score"],
                -row["ilvl"],
                -row["mplus_score"],
            ),
        )

        groups = {
            "Танки": [],
            "Хилы": [],
            "ДД": [],
            "Роль не определена": [],
        }

        for row in rows:
            groups.setdefault(row["role_group"], []).append(row)

        recommended = [row for row in rows if row["verdict"] in {"ПРИНЯТЬ", "ТЕСТОВЫЙ РЕЙД"}]
        manual = [row for row in rows if row["verdict"] == "РУЧНАЯ ПРОВЕРКА"]
        rejected = [row for row in rows if row["verdict"] == "ОТКАЗАТЬ"]

        lines: list[str] = []
        lines.append("=" * 80)
        lines.append("КОГО БРАТЬ В РЕЙД СЕГОДНЯ — ПЕРВИЧНЫЙ ОТБОР")
        lines.append("=" * 80)
        lines.append("")
        lines.append(f"Проверено игроков: {len(rows)}")
        lines.append(f"Рекомендуются: {len(recommended)}")
        lines.append(f"Ручная проверка: {len(manual)}")
        lines.append(f"Не брать по текущим требованиям: {len(rejected)}")
        lines.append("")

        if recommended:
            lines.append("БРАТЬ / МОЖНО НА ТЕСТ:")
            for row in recommended:
                lines.append(
                    f"✅ {row['name']} - {row['realm']} [{row['region']}] | "
                    f"{row['role_group']} | {row['class']} / {row['spec']} | "
                    f"ilvl {row['ilvl']:.1f} | M+ {row['mplus_score']:.0f} | "
                    f"рейд {row['raid_kills']} | {row['total_score']}/100 | {row['verdict']}"
                )
            lines.append("")

        for group_name in ("Танки", "Хилы", "ДД", "Роль не определена"):
            group_rows = groups.get(group_name) or []
            if not group_rows:
                continue

            lines.append(group_name.upper() + ":")
            for row in group_rows:
                marker = "✅" if row["verdict"] == "ПРИНЯТЬ" else "🟡" if row["verdict"] == "ТЕСТОВЫЙ РЕЙД" else "⚠️" if row["verdict"] == "РУЧНАЯ ПРОВЕРКА" else "❌"
                lines.append(
                    f"{marker} {row['name']} — {row['verdict']} — "
                    f"{row['total_score']}/100, ilvl {row['ilvl']:.1f}, M+ {row['mplus_score']:.0f}, "
                    f"недельный ключ +{row['max_weekly_key']}"
                )
            lines.append("")

        if manual:
            lines.append("РУЧНАЯ ПРОВЕРКА:")
            for row in manual:
                lines.append(
                    f"⚠️ {row['name']} — данных хватает не полностью. "
                    f"Проверь WarcraftLogs, роль, связь и готовность к механикам."
                )
            lines.append("")

        if rejected:
            lines.append("НЕ БРАТЬ ПО ТЕКУЩИМ ТРЕБОВАНИЯМ:")
            for row in rejected:
                lines.append(
                    f"❌ {row['name']} — {row['total_score']}/100, "
                    f"ilvl {row['ilvl']:.1f}, M+ {row['mplus_score']:.0f}"
                )
            lines.append("")

        if errors:
            lines.append("ОШИБКИ ПО ОТДЕЛЬНЫМ ИГРОКАМ:")
            for error in errors:
                lines.append(f"- {error}")
            lines.append("")

        lines.append("Важно: это первичный отбор по Raider.IO. Финальный состав лучше добивать по WarcraftLogs, классовому балансу, баффам, опыту на конкретном боссе и голосовой связи.")
        return "".join(lines)

    def run_raid_today(self) -> None:
        def worker() -> str:
            players = self._parse_raid_today_lines()
            rules = self._build_rules_from_form()
            rows: list[dict[str, Any]] = []
            errors: list[str] = []

            for player in players:
                try:
                    data = fetch_raiderio_character(player["region"], player["realm"], player["name"])
                    report = analyze_raiderio_candidate(data, rules)
                    rows.append(
                        {
                            "name": report["name"],
                            "realm": report["realm"],
                            "region": report["region"],
                            "class": report["class"],
                            "spec": report["active_spec_name"],
                            "role_group": self._normalize_role_for_report(report, player.get("role", "")),
                            "ilvl": report["ilvl"],
                            "mplus_score": report["mplus_score"],
                            "max_weekly_key": report["max_weekly_key"],
                            "raid_kills": report["raid_kills"],
                            "total_score": report["total_score"],
                            "verdict": report["verdict"],
                        }
                    )
                except Exception as exc:
                    errors.append(
                        f"строка {player['line_no']}: {player['name']} - {player['realm']} [{player['region']}] — {exc}"
                    )

            if not rows and errors:
                return "Не удалось проверить ни одного игрока." + "".join(errors)

            return self._format_raid_today_report(rows, errors)

        self._run_threaded(self.raid_today_button, self.raid_today_output, worker)

    def run_raiderio(self) -> None:
        self._clear_raider_avatar()

        def worker() -> tuple[str, dict[str, Any], bytes | None]:
            region = self.region_var.get()
            realm = self.realm_var.get()
            name = self.name_var.get()

            if not region.strip() or not realm.strip() or not name.strip():
                raise ValueError("Заполни регион, сервер и ник.")

            rules = self._build_rules_from_form()
            data = fetch_raiderio_character(region, realm, name)
            report = analyze_raiderio_candidate(data, rules)

            avatar_bytes = None
            avatar_url = report.get("avatar_url")
            if isinstance(avatar_url, str) and avatar_url:
                try:
                    avatar_bytes = download_image_bytes(avatar_url)
                except requests.RequestException:
                    avatar_bytes = None

            return format_raiderio_report(report, rules), report, avatar_bytes

        def on_success(result) -> None:
            _text, report, avatar_bytes = result
            self._show_raider_avatar(report, avatar_bytes)

        self._run_threaded(self.raider_button, self.raider_output, worker, on_success=on_success)

    def run_wcl_profile(self) -> None:
        def worker() -> str:
            name = self.wcl_name_var.get()
            server_slug = self.wcl_server_slug_var.get()
            region = self.wcl_region_var.get()

            if not name.strip() or not server_slug.strip() or not region.strip():
                raise ValueError("Заполни ник, сервер slug и регион.")

            return analyze_wcl_character(name, server_slug, region)

        self._run_threaded(self.wcl_profile_button, self.wcl_profile_output, worker)

    def run_wcl_report(self) -> None:
        def worker() -> str:
            code = self.report_code_var.get()
            player_name = self.report_player_var.get()
            fight_id_raw = self.report_fight_id_var.get().strip()

            if not code.strip() or not player_name.strip():
                raise ValueError("Заполни report code и ник игрока.")

            fight_id = int(fight_id_raw) if fight_id_raw else None
            return analyze_wcl_report(code, player_name, fight_id)

        self._run_threaded(self.wcl_report_button, self.wcl_report_output, worker)


if __name__ == "__main__":
    try:
        app = App()
        app.mainloop()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        messagebox.showerror("Ошибка запуска", str(exc))
