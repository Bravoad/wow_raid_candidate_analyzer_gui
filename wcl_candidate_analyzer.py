from __future__ import annotations

import argparse
import statistics
from typing import Any

from wcl_client import WarcraftLogsClient


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
    """
    zoneRankings в WarcraftLogs возвращается как JSON-подобная структура.
    У разных зон/рейдов структура может отличаться, поэтому парсим осторожно.
    """

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

    clean_percentiles = [
        value for value in percentiles
        if 0 <= value <= 100
    ]

    clean_performances = [
        value for value in performances
        if value >= 0
    ]

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


def verdict(points: int) -> str:
    if points >= 70:
        return "СИЛЬНЫЙ КАНДИДАТ ПО ЛОГАМ"
    if points >= 50:
        return "НОРМАЛЬНЫЙ КАНДИДАТ, МОЖНО НА ТЕСТ"
    if points >= 30:
        return "СОМНИТЕЛЬНО, НУЖНА РУЧНАЯ ПРОВЕРКА"
    return "СЛАБЫЕ ИЛИ ПУСТЫЕ ЛОГИ"


def analyze_character(name: str, server_slug: str, region: str) -> None:
    client = WarcraftLogsClient()

    data = client.graphql(
        CHARACTER_RANKINGS_QUERY,
        {
            "name": name,
            "serverSlug": server_slug,
            "serverRegion": region.upper(),
        },
    )

    character = data["characterData"]["character"]

    if not character:
        print("Персонаж не найден в WarcraftLogs.")
        return

    summary = extract_wcl_summary(character.get("zoneRankings"))
    points, reasons = score_wcl(summary)

    print()
    print("=" * 60)
    print("WARCRAFTLOGS АНАЛИЗ КАНДИДАТА")
    print("=" * 60)
    print(f"Персонаж: {character['name']}")
    print(f"Сервер: {character['server']['name']}")
    print()
    print(f"Средний percentile: {summary['avg_percentile']}")
    print(f"Медианный percentile: {summary['median_percentile']}")
    print(f"Лучший percentile: {summary['best_percentile']}")
    print(f"Найдено значений: {summary['logs_count']}")
    print()
    print("Оценка:")
    for reason in reasons:
        print(f"- {reason}")

    print()
    print(f"Баллы WarcraftLogs: {points} / 80")
    print(f"Вердикт: {verdict(points)}")
    print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Анализатор кандидата через WarcraftLogs")
    parser.add_argument("name", help="Ник персонажа")
    parser.add_argument("server_slug", help="Slug сервера, например howling-fjord")
    parser.add_argument("region", help="Регион: EU, US, KR, TW")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    analyze_character(args.name, args.server_slug, args.region)