# WoW Raid Candidate Analyzer

Графическое приложение на Python для первичной оценки кандидатов в рейд World of Warcraft.

Приложение использует данные Raider.IO и WarcraftLogs, чтобы быстро проверить персонажа: экипировку, Mythic+ рейтинг, рейдовый прогресс, активность, WarcraftLogs-перцентили, смерти и базовые данные из конкретного лога боя.

## Способы поддержать автора:

Отправить Донат вне стримов: https://www.donationalerts.com/r/bravoad
Номер карты для перевода:2202 2083 0793 4340 МИР Кирилл Евгеньевич Р.
Эксклюзивный контент: https://boosty.to/bravoad

## Возможности

### Raider.IO анализ кандидата

Приложение получает профиль персонажа через Raider.IO и показывает:

* имя персонажа;
* регион и сервер;
* класс, расу, активный спек и роль;
* гильдию;
* Raider.IO profile URL;
* аватарку персонажа;
* текущий item level;
* Mythic+ score;
* лучший ключ недели;
* количество последних Mythic+ забегов;
* рейдовый прогресс;
* лучшие Mythic+ ключи;
* итоговую оценку кандидата;
* вердикт для рейда.

Возможные вердикты:

```text
ПРИНЯТЬ
ТЕСТОВЫЙ РЕЙД
РУЧНАЯ ПРОВЕРКА
ОТКАЗАТЬ
```

### WarcraftLogs профиль

Быстрый анализ персонажа через WarcraftLogs:

* средний percentile;
* медианный percentile;
* лучший percentile;
* количество найденных значений;
* итоговый WarcraftLogs-вердикт.

Возможные вердикты:

```text
СИЛЬНЫЙ КАНДИДАТ ПО ЛОГАМ
НОРМАЛЬНЫЙ КАНДИДАТ, МОЖНО НА ТЕСТ
СОМНИТЕЛЬНО, НУЖНА РУЧНАЯ ПРОВЕРКА
СЛАБЫЕ ИЛИ ПУСТЫЕ ЛОГИ
```

### WarcraftLogs лог боя

Глубокий анализ конкретного WarcraftLogs-отчета по `report code`:

* название отчета;
* код отчета;
* выбранный игрок;
* выбранный бой;
* был ли убит босс;
* количество смертей;
* причины/события смерти;
* полученный урон;
* количество найденных кастов/прожатий;
* базовый вердикт по логу.

Важно: полноценный анализ механик и личных сейвов требует отдельной настройки правил под конкретного босса.

## Требования

Рекомендуется использовать Python 3.11 или Python 3.12.

Проверка версии Python:

```bash
python --version
```

Зависимости:

```text
requests
python-dotenv
pillow
```

## Установка

Склонируй или скачай проект.

Создай виртуальное окружение:

```bash
python -m venv .venv
```

Активируй окружение.

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Windows CMD:

```cmd
.venv\Scripts\activate
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Установи зависимости:

```bash
pip install -r requirements.txt
```

Если файла `requirements.txt` еще нет, создай его:

```txt
requests
python-dotenv
pillow
```

И установи зависимости:

```bash
pip install requests python-dotenv pillow
```

## Настройка `.env`

Рядом с файлом приложения создай файл `.env`.

Пример:

```env
WCL_CLIENT_ID=your_warcraftlogs_client_id_here
WCL_CLIENT_SECRET=your_warcraftlogs_client_secret_here
RAIDERIO_ACCESS_KEY=
```

`WCL_CLIENT_ID` и `WCL_CLIENT_SECRET` нужны для WarcraftLogs API.

`RAIDERIO_ACCESS_KEY` необязателен для базового использования Raider.IO, но может быть полезен для нормального приложения.

## `.env.example`

Создай файл `.env.example`, чтобы в репозитории было понятно, какие переменные окружения нужны.

```env
WCL_CLIENT_ID=your_warcraftlogs_client_id_here
WCL_CLIENT_SECRET=your_warcraftlogs_client_secret_here
RAIDERIO_ACCESS_KEY=your_raiderio_access_key_here_optional
```

## `.gitignore`

Обязательно добавь `.env` в `.gitignore`, чтобы случайно не залить секреты в GitHub.

```gitignore
.env
.venv/
__pycache__/
*.pyc
```

Важно: настоящий `.env` нельзя публиковать. В GitHub можно заливать только `.env.example`.

## Получение WarcraftLogs API Client

1. Зайди в WarcraftLogs.
2. Открой раздел API Clients.
3. Создай новый client.
4. В поле имени можно указать:

```text
WoW Raid Candidate Analyzer
```

5. Redirect URL можно указать локальный:

```text
http://localhost:8000/callback
```

6. Галочку `Public Client` для этого приложения ставить не нужно.
7. После создания скопируй `Client ID` и `Client Secret`.
8. Вставь их в `.env`.

Если `Client Secret` был отправлен в чат, опубликован или попал в GitHub, его лучше сразу перевыпустить.

## Запуск приложения

Основной файл приложения:

```bash
python wow_raid_candidate_gui.py
```

После запуска откроется графическое окно.

## Как пользоваться

### Вкладка Raider.IO кандидат

Заполни поля:

```text
Регион: eu
Сервер/realm slug: howling-fjord
Ник: Templar
```

Выбери цель проверки:

```text
normal
heroic
mythic
```

Можно вручную настроить требования:

```text
Мин. ilvl
Мин. M+ score
Мин. ключ недели
Сложность рейда
Мин. боссов
```

Нажми кнопку:

```text
Проверить через Raider.IO
```

После успешного поиска появится текстовый отчет и аватарка персонажа.

### Вкладка WCL профиль

Заполни:

```text
Ник
Сервер slug
Регион
```

Пример:

```text
Templar
howling-fjord
EU
```

Нажми:

```text
Проверить WCL профиль
```

Приложение покажет базовую оценку персонажа по WarcraftLogs.

### Вкладка WCL лог боя

Вставь `report code` из ссылки WarcraftLogs.

Пример ссылки:

```text
https://www.warcraftlogs.com/reports/ABC123Example
```

В этом случае `report code`:

```text
ABC123Example
```

Заполни ник игрока.

Если нужно разобрать конкретный бой, укажи `Fight ID`. Если поле оставить пустым, приложение возьмет последний найденный бой с боссом.

Нажми:

```text
Разобрать лог
```

## Логика Raider.IO оценки

По умолчанию используются три набора требований.

### Normal

```text
min_ilvl = 600
min_score = 800
min_weekly_key = 2
raid_difficulty = normal
min_raid_bosses = 3
```

### Heroic

```text
min_ilvl = 620
min_score = 1800
min_weekly_key = 6
raid_difficulty = heroic
min_raid_bosses = 4
```

### Mythic

```text
min_ilvl = 635
min_score = 2500
min_weekly_key = 10
raid_difficulty = mythic
min_raid_bosses = 1
```

Оценка складывается из нескольких блоков:

```text
ilvl — до 35 баллов
M+ score — до 25 баллов
рейдовый опыт — до 25 баллов
ключ недели — до 10 баллов
последняя активность — до 5 баллов
```

Максимум:

```text
100 баллов
```

## Логика вердикта Raider.IO

```text
80+ баллов: ПРИНЯТЬ
60–79 баллов: ТЕСТОВЫЙ РЕЙД
45–59 баллов: РУЧНАЯ ПРОВЕРКА
меньше 45 баллов: ОТКАЗАТЬ
```

Если item level сильно ниже требования, приложение может сразу поставить:

```text
ОТКАЗАТЬ
```

## Логика WarcraftLogs оценки

Приложение ищет числовые значения percentile/rank в `zoneRankings` и считает:

```text
средний percentile
медианный percentile
лучший percentile
количество найденных значений
```

Это быстрый профильный анализ, а не полноценный разбор каждого боя.

Для смертей, сейвов, механик и полученного урона нужен конкретный WarcraftLogs report code.

## Типовые ошибки

### Не найдены WCL_CLIENT_ID и WCL_CLIENT_SECRET

Причина: нет `.env` или переменные названы неправильно.

Проверь файл `.env`:

```env
WCL_CLIENT_ID=your_client_id
WCL_CLIENT_SECRET=your_client_secret
```

### Для отображения аватарок установи Pillow

Причина: не установлен пакет `pillow`.

Решение:

```bash
pip install pillow
```

### Персонаж не найден в Raider.IO

Проверь:

* регион;
* сервер;
* ник;
* правильный realm slug.

Пример:

```text
eu
howling-fjord
Templar
```

### Raider.IO ограничил запросы

Причина: слишком много запросов за короткое время.

Решение: подождать и повторить позже. Для публичного или активного приложения лучше использовать Raider.IO access key.

### Ошибка GraphQL WarcraftLogs

Возможные причины:

* неверный `Client ID`;
* неверный `Client Secret`;
* истекший/удаленный client;
* неправильный `serverSlug`;
* неправильный регион;
* персонажа нет в WarcraftLogs;
* изменилась структура ответа API.

### AttributeError: module 'pkgutil' has no attribute 'ImpImporter'

Обычно это проблема старого `setuptools`/`pip` на новой версии Python.

Решение:

```bash
python -m pip install --upgrade pip setuptools wheel
```

Если не помогло, лучше создать новое виртуальное окружение.

## Безопасность

Никогда не публикуй:

```text
WCL_CLIENT_SECRET
.env
```

Не вставляй секреты прямо в Python-код.

Если секрет попал в чат, GitHub, Discord или скриншот, лучше удалить старый client в WarcraftLogs и создать новый.

## Ограничения

Приложение не заменяет рейд-лидера и ручной анализ логов.

Raider.IO показывает, что персонаж закрыл и какой у него прогресс, но не показывает качество игры в конкретной попытке.

WarcraftLogs позволяет глубже смотреть бой, но для нормального анализа механик нужны правила под конкретных боссов и способности.

Текущий анализ WarcraftLogs является базовым:

* считает перцентили;
* ищет смерти;
* показывает базовые события смерти;
* суммирует найденный полученный урон;
* считает найденные cast/count значения.


## Пример итогового отчета

```text
RAIDER.IO — АНАЛИЗ КАНДИДАТА

Персонаж: Templar - howling-fjord [eu]
Класс/спек: Hunter / Beast Mastery / dps
Гильдия: Авантюрное Агентство

Цель проверки: HEROIC
Требования: ilvl 620, score 1800, ключ недели +6, рейд heroic 4+ боссов

Оценка:
35 балл(ов) — ilvl нормальный
25 балл(ов) — M+ score нормальный
25 балл(ов) — рейдовый опыт подходит
10 балл(ов) — активность за неделю есть
5 балл(ов) — активный игрок

ИТОГО: 100 / 100
ВЕРДИКТ: ПРИНЯТЬ
```

## Авторство и дисклеймер

Проект предназначен для помощи офицерам и рейд-лидерам World of Warcraft при первичной оценке кандидатов.
Проект не является официальным приложением Blizzard, Raider.IO или WarcraftLogs.
При публичном использовании данных Raider.IO и WarcraftLogs соблюдай правила их API и указывай соответствующую атрибуцию.
