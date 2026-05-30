from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Any

import requests


RAIDERIO_CHARACTER_URL = "https://raider.io/api/v1/characters/profile"

FIELDS = ",".join(
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


DEFAULT_RULES = {
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


def fetch_character(region: str, realm: str, name: str) -> dict[str, Any]:
    params = {
        "region": region,
        "realm": realm,
        "name": name,
        "fields": FIELDS,
    }

    access_key = os.getenv("RAIDERIO_ACCESS_KEY")
    if access_key:
        params["access_key"] = access_key

    response = requests.get(
        RAIDERIO_CHARACTER_URL,
        params=params,
        timeout=20,
        headers={"User-Agent": "RaidCandidateAnalyzer/1.0"},
    )

    if response.status_code == 404:
        raise RuntimeError("Персонаж не найден. Проверь регион, сервер и ник.")

    if response.status_code == 429:
        raise RuntimeError("Raider.IO временно ограничил запросы. Слишком много обращений к API.")

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(f"Ошибка Raider.IO API: {response.status_code} — {response.text}") from exc

    return response.json()


def get_nested_number(data: dict[str, Any], *keys: str, default: float = 0) -> float:
    current: Any = data

    for key in keys:
        if not isinstance(current, dict):
            return default

        current = current.get(key)

    if isinstance(current, int | float):
        return float(current)

    return default


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

    role_scores = [
        scores.get("dps", 0),
        scores.get("healer", 0),
        scores.get("tank", 0),
    ]

    role_scores = [float(value) for value in role_scores if isinstance(value, int | float)]

    return max(role_scores, default=0.0)


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
    """
    Пример summary может быть похож на:
    8/8 H
    3/8 M
    8/8 N
    Поэтому парсим максимально осторожно.
    """
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
    """
    Raider.IO может вернуть прогресс в разных структурах.
    Поэтому сначала ищем явные поля, потом fallback через summary.
    """
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
    best_kills = max(best_kills, parse_summary_progress(summary, wanted_difficulty))

    return best_kills


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

    result = []

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


def make_verdict(total_score: int, ilvl: float, rules: Rules) -> str:
    hard_ilvl_fail = ilvl < rules.min_ilvl - 15

    if hard_ilvl_fail:
        return "ОТКАЗАТЬ"

    if total_score >= 80:
        return "ПРИНЯТЬ"

    if total_score >= 60:
        return "ТЕСТОВЫЙ РЕЙД"

    if total_score >= 45:
        return "РУЧНАЯ ПРОВЕРКА"

    return "ОТКАЗАТЬ"


def analyze_candidate(data: dict[str, Any], rules: Rules) -> dict[str, Any]:
    ilvl = extract_ilvl(data)
    mplus_score = extract_mplus_score(data)
    max_weekly_key = extract_max_weekly_key(data)
    recent_runs_count = extract_recent_runs_count(data)
    raid_kills, raid_name = extract_raid_progress(data, rules.raid_difficulty)
    best_runs = extract_best_runs(data)

    checks = [
        score_ilvl(ilvl, rules),
        score_mplus(mplus_score, rules),
        score_raid(raid_kills, raid_name, rules),
        score_weekly_key(max_weekly_key, rules),
        score_recent_activity(recent_runs_count),
    ]

    total_score = sum(points for points, _reason in checks)
    verdict = make_verdict(total_score, ilvl, rules)

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
        "ilvl": ilvl,
        "mplus_score": mplus_score,
        "max_weekly_key": max_weekly_key,
        "recent_runs_count": recent_runs_count,
        "raid_kills": raid_kills,
        "raid_name": raid_name,
        "total_score": total_score,
        "verdict": verdict,
        "checks": checks,
        "best_runs": best_runs,
    }


def print_report(report: dict[str, Any], rules: Rules) -> None:
    print()
    print("=" * 60)
    print("АНАЛИЗАТОР КАНДИДАТА В РЕЙД")
    print("=" * 60)
    print()
    print(f"Персонаж: {report['name']} - {report['realm']} [{report['region']}]")
    print(f"Класс/спек: {report['class']} / {report['active_spec_name']} / {report['active_spec_role']}")
    print(f"Раса: {report['race']}")
    print(f"Гильдия: {report['guild']}")

    if report["profile_url"]:
        print(f"Raider.IO: {report['profile_url']}")

    print()
    print(f"Цель проверки: {rules.target.upper()}")
    print(f"Требования: ilvl {rules.min_ilvl}, score {rules.min_score}, "
          f"ключ недели +{rules.min_weekly_key}, рейд {rules.raid_difficulty} "
          f"{rules.min_raid_bosses}+ босс(ов)")
    print()
    print("-" * 60)
    print("ОЦЕНКА")
    print("-" * 60)

    for points, reason in report["checks"]:
        print(f"{points:>2} балл(ов) — {reason}")

    print()
    print("-" * 60)
    print(f"ИТОГО: {report['total_score']} / 100")
    print(f"ВЕРДИКТ: {report['verdict']}")
    print("-" * 60)

    if report["best_runs"]:
        print()
        print("Лучшие M+ ключи:")
        for run in report["best_runs"]:
            print(f"- {run}")

    print()
    print("Пояснение:")
    if report["verdict"] == "ПРИНЯТЬ":
        print("Кандидат выглядит достаточно сильным по базовым Raider.IO метрикам.")
    elif report["verdict"] == "ТЕСТОВЫЙ РЕЙД":
        print("Кандидата можно брать на тестовый рейд, но лучше проверить логи и механику.")
    elif report["verdict"] == "РУЧНАЯ ПРОВЕРКА":
        print("Данные неоднозначные. Нужны WarcraftLogs, разговор и, возможно, пробный ключ/рейд.")
    else:
        print("По текущим требованиям кандидат слабоват или данных недостаточно.")

    print()


def build_rules(args: argparse.Namespace) -> Rules:
    rules = DEFAULT_RULES[args.target]

    return Rules(
        target=rules.target,
        min_ilvl=args.min_ilvl if args.min_ilvl is not None else rules.min_ilvl,
        min_score=args.min_score if args.min_score is not None else rules.min_score,
        min_weekly_key=args.min_weekly_key if args.min_weekly_key is not None else rules.min_weekly_key,
        raid_difficulty=args.raid_difficulty if args.raid_difficulty is not None else rules.raid_difficulty,
        min_raid_bosses=args.min_raid_bosses if args.min_raid_bosses is not None else rules.min_raid_bosses,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Анализатор кандидата в рейд по данным Raider.IO"
    )

    parser.add_argument("region", help="Регион: eu, us, kr, tw, cn")
    parser.add_argument("realm", help="Сервер персонажа, например: howling-fjord")
    parser.add_argument("name", help="Ник персонажа")

    parser.add_argument(
        "--target",
        choices=["normal", "heroic", "mythic"],
        default="heroic",
        help="Цель проверки. По умолчанию: heroic",
    )

    parser.add_argument("--min-ilvl", type=float, help="Минимальный ilvl")
    parser.add_argument("--min-score", type=float, help="Минимальный M+ score")
    parser.add_argument("--min-weekly-key", type=int, help="Минимальный ключ недели")
    parser.add_argument(
        "--raid-difficulty",
        choices=["normal", "heroic", "mythic"],
        help="Сложность рейда для проверки",
    )
    parser.add_argument("--min-raid-bosses", type=int, help="Минимум убитых боссов")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rules = build_rules(args)

    try:
        data = fetch_character(args.region, args.realm, args.name)
        report = analyze_candidate(data, rules)
        print_report(report, rules)

    except RuntimeError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        sys.exit(1)

    except requests.RequestException as error:
        print(f"Ошибка сети: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()