"""AI assistant of the DSS (Claude via the official Anthropic SDK).

The DSS computes, the model explains (Lecture 6: DSS + generative AI). Every number the
assistant quotes comes from a tool that runs the same engine as the interface: the current
ranking, one apartment step by step, a comparison, a "what if" with other settings, the
sensitivity of the leader, the lecture example. It can also change the settings in the
interface when asked to (apply_settings) - the change arrives in the browser as an event.

The answer streams to the page as server-sent events: thinking summary, tool steps, text.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from dss import topsis as run_topsis, wsm as run_wsm
from dss.examples import lecture_suppliers

from .criteria import BY_KEY, CRITERIA, PRESETS
from .engine import RankRequest, rank

log = logging.getLogger("apartment.agent")
ROOT = Path(__file__).resolve().parent.parent
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_ROUNDS = 8
MAX_TURNS = 40                       # user messages per conversation
MAX_MESSAGE = 2000                   # characters
RATE = (30, 600)                     # messages per client per 10 minutes (the site may be public via a tunnel)


def _load_env() -> None:
    """Minimal .env reader (KEY=VALUE lines); real environment variables win."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("ANTHROPIC_EFFORT", "medium")


def enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


# ---- system prompt: how the application works ----------------------------------------------

def _criteria_text() -> str:
    rows = []
    for c in CRITERIA:
        modes = " и ".join({"sale": "покупка", "rent": "аренда"}[m] for m in c.modes)
        defaults = ", ".join(f"{ {'sale': 'покупка', 'rent': 'аренда'}[m]} {c.importance.get(m, 0)}" for m in c.modes)
        rows.append(f"- `{c.key}` «{c.name}» — {c.kind} ({'больше — лучше' if c.kind == 'benefit' else 'меньше — лучше'}), "
                    f"{c.unit}; как измеряется: {c.how}" + (f"; шкала: {c.scale}" if c.scale else "")
                    + f"; режимы: {modes}; важность по умолчанию: {defaults}.")
    return "\n".join(rows)


def _presets_text() -> str:
    out = []
    for k, p in PRESETS.items():
        if k == "balanced":
            out.append(f"- «{p['title']}» — важности по умолчанию.")
            continue
        out.append(f"- «{p['title']}»: покупка {p['sale']}; аренда {p['rent']}.")
    return "\n".join(out)


SYSTEM = f"""Ты — ИИ-помощник внутри веб-приложения «Подбор квартиры». Это система поддержки принятия решений (СППР) для учебного задания 4 курса «Когнитивные технологии» (лекция 6 — СППР, лекция 7.1 — многокритериальный выбор WSM/TOPSIS). Смотрят на неё студент магистратуры, его преподаватель и одногруппники; многие видят СППР впервые, и интерфейс кажется им сложным. Твоя работа — объяснять простыми словами, что значит каждая настройка, зачем она нужна и как идёт расчёт; считать «что будет, если…»; по просьбе — менять настройки в интерфейсе.

## Как устроено приложение

Данные — объявления Krisha.kz по Астане, режим «Покупка» или «Аренда». Параметры берутся из карточек поиска (комнаты, площадь, этаж, цена, тип дома, год, состояние, потолки, мебель, ЖК). Координаты — по адресу через OpenStreetMap; у каждой квартиры указана точность: точно (со страницы объявления), по дому, по улице, по ЖК, по центру района. Страницы объявлений Krisha закрывает антибот-проверкой, поэтому по умолчанию они не открываются. «Демо-набор» — сохранённый снимок реального поиска, чтобы показывать без сети. В карточках аренды почти не бывает состояния ремонта, года и типа дома, поэтому для аренды по умолчанию включены цена, площадь, расстояние, мебель, этаж.

Панель слева, сверху вниз:
1. «Объявления» — выбор набора данных и «Новый поиск на Krisha.kz» (фильтры сайта: комнаты, цена от/до, площадь от, год от, этаж, сколько страниц поиска по 20 объявлений; галочка «открывать страницы объявлений» — точнее, но медленнее, и Krisha может включить проверку на бота). Фильтры поиска лишь определяют, какие объявления скачать, — это ещё не модель решения.
2. «Точка притяжения» — работа, учёба, родители. От неё считается критерий «Расстояние до точки» (по прямой, км). Выбирается из списка или кликом по карте, домик на карте можно перетащить.
3. «Важность критериев» — у каждого критерия галочка (учитывать или нет) и ползунок важности 0–10. Вес = важность / сумма важностей включённых критериев, поэтому сумма весов всегда 1. Ярлык «↓ меньше — лучше» означает cost-критерий, «↑ больше — лучше» — benefit. Кнопки-пресеты быстро ставят типовые важности. «Веса по AHP» — метод Саати: критерии сравниваются попарно по шкале 1–9, веса — нормированные средние геометрические строк матрицы; согласованность проверяется по CR ≤ 0,10, при CR > 0,10 применить нельзя — сравнения противоречат друг другу.
4. «Ограничения» — жёсткие условия: бюджет до, площадь от, комнаты, год постройки от, расстояние до точки не дальше, состояние не хуже, не первый и не последний этаж. Они применяются ДО ранжирования: квартира, нарушившая условие, не участвует, сколько бы баллов ни набрала (в отличие от веса, который лишь сдвигает баллы). Если значение в объявлении неизвестно, квартира не исключается.
5. «Метод и данные» — по какому методу сортировать список (WSM, TOPSIS, WPM; считаются всегда все три) и что делать, если параметр не указан в объявлении: подставить худшее значение (пессимистично: неизвестное не вознаграждается), медиану подборки или исключить объявление. Валидация матрицы запрещает пропуски, поэтому их надо обработать явно; что подставлено, видно в «Расчёте» (оранжевые клетки) и в карточке квартиры.

Вкладки:
- «Подбор» — сводка (сколько объявлений, сколько прошло ограничения, число критериев, согласие методов ρ), карта (номера — места в рейтинге), карточка лучшего варианта с ответами на три вопроса задания (одинаковый ли победитель, отличаются ли рейтинги, почему методы могут расходиться) и строкой «А WPM?», устойчивость лидера, доминирование, список квартир (место, код A1…An, параметры, плюсы и минусы, последствия выбора, баллы WSM/TOPSIS/WPM) и графики: «Баллы WSM, TOPSIS и WPM», «Из чего сложился балл» (вклад каждого критерия w·r), «Места по двум методам» (синим — TOPSIS ставит выше, оранжевым — ниже), «Нормализованная матрица» (тепловая карта, 1 — лучшее значение), «Чувствительность к весу».
- «Расчёт · 4.2» — 10 шагов: 1 ввод данных (критерии, типы, веса, матрица решений), 2 проверка данных, 3 нормализация, 4 WSM, 5 TOPSIS, 6 сравнение WSM и TOPSIS (с ответами на три вопроса задания), 7 WPM — взвешенное произведение (формула, множители r^w по критериям, где WPM расходится с WSM), 8 сравнение трёх методов (лидеры, ρ для каждой пары, разброс мест), 9 доминирование и чувствительность, 10 визуализация и интерфейс.
- «Формализация · 4.1» — задача, ЛПР, цель, альтернативы, критерии с типом и единицами, ограничения, последствия, тип задачи, тип СППР, модель Саймона, матрица решений, веса, возможные ошибки СППР.

## Критерии (ключ, смысл, шкала)
{_criteria_text()}

## Пресеты важности
{_presets_text()}

## Методы (лекция 7.1)
- Нормализация для WSM и WPM: benefit r = x / max(x), cost r = min(x) / x; лучшее значение в подборке получает 1. Нужна, потому что тенге, м², км и баллы нельзя складывать напрямую.
- WSM (взвешенная сумма): S = Σ w·r. Компенсаторный метод: сильная сторона полностью компенсирует слабую.
- WPM (взвешенное произведение): P = Π r^w — та же нормализация, что у WSM, но значения перемножаются, каждое в степени веса. Множитель r^w = 1 у лучшего значения; чем хуже значение, тем меньше множитель, и он уменьшает ВЕСЬ балл в разы (r = 0,5 при весе 0,25 → 0,84, то есть −16 % ко всему баллу). Поэтому WPM почти не прощает провалов по одному критерию: «сбалансированные» квартиры поднимаются, квартиры с одним сильным минусом опускаются. Не зависит от единиц измерения; ноль обнулил бы произведение. С WSM обычно согласован сильнее, чем TOPSIS (общая нормализация). В explain_listing есть множители wpm_factor_r_pow_w по каждому критерию.
- TOPSIS: векторная нормализация n = x / √Σx², взвешивание v = w·n, идеал A+ (лучшие значения: max для benefit, min для cost) и анти-идеал A−, евклидовы расстояния d+ и d−, коэффициент близости C = d− / (d+ + d−) от 0 до 1. Сильнее наказывает большое отставание хотя бы по одному критерию, любит «сбалансированные» варианты.
- Почему WSM и TOPSIS могут расходиться: разная нормализация (относительно лучшего vs по длине вектора), линейная компенсация vs расстояния до идеала. Согласие рангов показывает коэффициент Спирмена ρ (1 — порядок совпадает).
- Доминирование: альтернатива не лучше другой ни по одному критерию и хуже хотя бы по одному — она не станет первой ни при каких весах.
- Анализ чувствительности: вес одного критерия меняется от 0 до 1, остальные делят остаток пропорционально прежним весам; порог — вес, при котором меняется лидер. Если порог далеко от текущего веса, решение устойчиво.
- Пример из лекции (поставщики A–D, четыре критерия по 0,25): WSM D 0,9035, B 0,8542, A 0,8472, C 0,8295; TOPSIS D 0,7100; лидер сменится D → A при весе надёжности ≈ 0,40. Для разбора на маленьких числах есть инструмент lecture_example.

## Задание 4.1 и лекция 6
Тип задачи — полуструктурированная: цена, площадь, расстояние — числа, но веса субъективны, качественные признаки переводятся в баллы шкалой, данные неполны. Тип СППР — model-driven (ядро — модели WSM/TOPSIS) с data-driven компонентом (сбор объявлений). Модель Саймона: Intelligence — поиск на Krisha, Design — критерии, ограничения, веса, Choice — ранжирование и анализ, Implementation — просмотр квартиры и сделка. Ошибки СППР: плохие данные, неверная модель, смещение весов, автоматическое доверие — и как система с ними справляется. СППР + ИИ: система считает, а ты объясняешь и помогаешь подобрать веса.

## Как отвечать
- Каждое сообщение пользователя начинается с блока <ui_state> — это то, что сейчас на экране: режим, набор, точка, веса, ограничения, лидеры. Опирайся на него, но детали (значения квартиры, нормализованные числа, вклад критериев, «что если») бери из инструментов. Не придумывай чисел — если числа нет в <ui_state> или в результате инструмента, вызови инструмент.
- Квартиры называй кодом и коротко: «A12 (2-к, 46 м², 26 млн ₸)».
- Пиши по-русски, просто, как человеку, который впервые видит СППР. Сначала короткий ответ на вопрос (1–3 предложения), потом пояснение. Формулу давай одной строкой и сразу пример на текущих числах. Не пересказывай весь интерфейс, если спросили про одну вещь.
- «Что будет, если…» — используй what_if: он считает, но ничего не меняет на экране. apply_settings вызывай, только когда пользователь просит поменять, поставить, применить или «сделать расчёт» с какими-то настройками; после изменения коротко скажи, что именно поменял и что получилось.
- Помни, что рейтинг — подсказка: квартиру надо смотреть вживую, данные объявлений бывают неполными и неточными.
- Оформление: короткие абзацы, списки, **жирный** для ключевых слов. Таблица markdown — только для сравнения 2–5 квартир или весов. Без заголовков первого уровня, без эмодзи.
"""


# ---- tools --------------------------------------------------------------------------------

CONSTRAINT_PROPS = {
    "price_max": {"type": ["number", "null"], "description": "бюджет до, ₸ (для аренды — в месяц)"},
    "area_min": {"type": ["number", "null"], "description": "площадь от, м²"},
    "rooms": {"type": ["array", "null"], "items": {"type": "integer"}, "description": "допустимое число комнат, 5 = 5+"},
    "year_min": {"type": ["integer", "null"], "description": "год постройки от"},
    "distance_max": {"type": ["number", "null"], "description": "до точки не дальше, км"},
    "condition_min": {"type": ["number", "null"], "description": "состояние не хуже, балл 2–5"},
    "not_first": {"type": ["boolean", "null"], "description": "не первый этаж"},
    "not_last": {"type": ["boolean", "null"], "description": "не последний этаж"},
}
WEIGHTS_SCHEMA = {"type": "object", "additionalProperties": {"type": "number"},
                  "description": "важность 0–10 по ключам критериев (0 — выключить). Указывай только те, что меняешь; "
                                 "ключи: " + ", ".join(c.key for c in CRITERIA)}
POINT_SCHEMA = {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"},
                                                 "name": {"type": "string"}}, "required": ["lat", "lon"]}

TOOLS: List[Dict[str, Any]] = [
    {"name": "get_ranking",
     "description": "Текущий рейтинг квартир (с настройками на экране): места и баллы по WSM, TOPSIS и WPM, "
                    "основные параметры, пропуски, доминирование.",
     "input_schema": {"type": "object", "properties": {
         "top": {"type": "integer", "description": "сколько квартир вернуть, 1–25 (по умолчанию 10)"},
         "method": {"type": "string", "enum": ["wsm", "topsis", "wpm"], "description": "по какому методу упорядочить"}}}},
    {"name": "explain_listing",
     "description": "Разбор одной квартиры по шагам: исходные значения, нормализованные r, вес, вклад w·r в WSM, "
                    "TOPSIS (векторно нормализованные и взвешенные значения, d+, d−, C), WPM, места, плюсы и минусы, "
                    "подставленные пропуски, последствия выбора, доминирование.",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "код квартиры, например A12"}},
                      "required": ["code"]}},
    {"name": "compare_listings",
     "description": "Сравнить 2–5 квартир по каждому критерию: значения, нормализованные r и вклад в WSM; баллы и места.",
     "input_schema": {"type": "object", "properties": {"codes": {"type": "array", "items": {"type": "string"}}},
                      "required": ["codes"]}},
    {"name": "what_if",
     "description": "Пересчитать рейтинг с другими настройками, НЕ меняя экран: новые важности, ограничения, "
                    "обработка пропусков, точка. Возвращает новых лидеров и что сдвинулось относительно текущего расчёта.",
     "input_schema": {"type": "object", "properties": {
         "weights": WEIGHTS_SCHEMA,
         "constraints": {"type": "object", "properties": CONSTRAINT_PROPS,
                         "description": "только меняемые ограничения; null — снять ограничение"},
         "missing": {"type": "string", "enum": ["worst", "median", "drop"]},
         "point": POINT_SCHEMA}}},
    {"name": "sensitivity",
     "description": "Анализ чувствительности: как меняется лидер, если вес одного критерия идёт от 0 до 1 "
                    "(остальные пропорционально). Пороги смены лидера и баллы лидеров в нескольких точках.",
     "input_schema": {"type": "object", "properties": {
         "criterion": {"type": "string", "description": "ключ критерия"},
         "method": {"type": "string", "enum": ["wsm", "topsis"]}}, "required": ["criterion"]}},
    {"name": "apply_settings",
     "description": "Изменить настройки В ИНТЕРФЕЙСЕ пользователя (ползунки, ограничения, пропуски, сортировку, точку) "
                    "и пересчитать. Только по явной просьбе пользователя. Возвращает новый результат.",
     "input_schema": {"type": "object", "properties": {
         "weights": WEIGHTS_SCHEMA,
         "constraints": {"type": "object", "properties": CONSTRAINT_PROPS,
                         "description": "только меняемые ограничения; null — снять ограничение"},
         "missing": {"type": "string", "enum": ["worst", "median", "drop"]},
         "sort": {"type": "string", "enum": ["wsm", "topsis", "wpm"]},
         "point": POINT_SCHEMA,
         "summary": {"type": "string", "description": "одна строка для пользователя: что меняется"}}}},
    {"name": "lecture_example",
     "description": "Пример из лекции 7.1 (поставщики A–D) на маленьких числах: матрица, нормализация, WSM, "
                    "TOPSIS по шагам — чтобы объяснить метод без 50 квартир.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_formalization",
     "description": "Текущая формализация задания 4.1: задача, ЛПР, цель, число альтернатив, критерии с весами, "
                    "ограничения, последствия для лидеров, тип задачи, тип СППР, модель Саймона.",
     "input_schema": {"type": "object", "properties": {}}},
]
for _t in TOOLS:
    _t["eager_input_streaming"] = True      # inputs stream as generated; validated below before use

TOOL_LABELS = {
    "get_ranking": "Смотрю текущий рейтинг",
    "explain_listing": "Разбираю квартиру {code}",
    "compare_listings": "Сравниваю {codes}",
    "what_if": "Пересчитываю с другими настройками",
    "sensitivity": "Проверяю чувствительность к весу",
    "apply_settings": "Меняю настройки на экране",
    "lecture_example": "Беру пример из лекции",
    "get_formalization": "Смотрю формализацию 4.1",
}


# ---- state of one conversation ------------------------------------------------------------

@dataclass
class Session:
    id: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    req: Dict[str, Any] = field(default_factory=dict)        # the /api/rank body that matches the screen
    turns: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    touched: float = field(default_factory=time.time)


_sessions: "OrderedDict[str, Session]" = OrderedDict()
_hits: Dict[str, deque] = defaultdict(deque)
_glock = threading.Lock()


def session(chat_id: Optional[str]) -> Session:
    with _glock:
        s = _sessions.get(chat_id or "")
        if s is None or time.time() - s.touched > 3 * 3600:
            s = Session(uuid.uuid4().hex[:12])
            _sessions[s.id] = s
        _sessions.move_to_end(s.id)
        while len(_sessions) > 100:
            _sessions.popitem(last=False)
        s.touched = time.time()
        return s


def allow(client: str) -> bool:
    limit, window = RATE
    now = time.time()
    with _glock:
        q = _hits[client]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


# ---- helpers over the engine ----------------------------------------------------------------

def to_request(body: Dict[str, Any]) -> RankRequest:
    p = body.get("point") or None
    return RankRequest(weights=dict(body.get("weights") or {}), point=(p["lat"], p["lon"]) if p else None,
                       point_name=body.get("point_name", ""), constraints=dict(body.get("constraints") or {}),
                       missing=body.get("missing", "worst"), sens_key=body.get("sens_key"),
                       sens_method=body.get("sens_method", "wsm"), top=15)


def _r(v, d=4):
    return round(float(v), d) if isinstance(v, (int, float)) and v is not None else v


def _by_code(records: List[dict]) -> Dict[str, dict]:
    return {r["code"]: r for r in records or []}


def _short(x: dict) -> dict:
    return {"code": x["code"], "label": x["label"], "wsm": x["wsm"], "wsm_rank": x["wsm_rank"],
            "topsis": x["topsis"], "topsis_rank": x["topsis_rank"], "wpm": x["wpm"], "wpm_rank": x["wpm_rank"]}


def ui_state(res: dict, body: Dict[str, Any], tab: str) -> str:
    """What is on the screen right now, compactly."""
    mode = {"sale": "покупка", "rent": "аренда"}[res.get("mode", "sale")]
    ds = res.get("dataset") or {}
    lines = [f"Вкладка: {tab or 'Подбор'}. Режим: {mode}. Набор: {'демо-набор' if ds.get('sample') else 'поиск'} — "
             f"{res.get('total', 0)} объявлений; прошли ограничения: {len(res.get('listings') or [])}; "
             f"исключено ограничениями: {len(res.get('excluded') or [])}."]
    pt = res.get("point") or {}
    lines.append(f"Точка притяжения: {body.get('point_name') or 'точка на карте'} ({_r(pt.get('lat'), 4)}, {_r(pt.get('lon'), 4)}).")
    lines.append(f"Сортировка списка: {str(body.get('sort', 'wsm')).upper()}. Пропуски: {body.get('missing', 'worst')}.")
    if res.get("criteria"):
        lines.append("Критерии (важность → вес): " + "; ".join(
            f"{c['name']} [{c['key']}, {c['kind']}] {c['importance']:g} → {c['weight']:.3f}" for c in res["criteria"]))
    off = [c.name for c in CRITERIA if res.get("mode", "sale") in c.modes
           and c.key not in {x["key"] for x in res.get("criteria") or []}]
    if off:
        lines.append("Выключены: " + ", ".join(off) + ".")
    active = [f"{c['label']} {c['value']} (исключено {c['excluded']})" for c in res.get("constraints") or []]
    lines.append("Ограничения: " + ("; ".join(active) if active else "не заданы") + ".")
    m = res.get("missing") or {}
    if m.get("by_criterion"):
        lines.append("Пропуски в данных: " + ", ".join(f"{k} — {n}" for k, n in m["by_criterion"].items())
                     + f"; заполнено клеток {len(m.get('filled', []))}, исключено объявлений {len(m.get('dropped', []))}.")
    v = res.get("validation") or {}
    if v.get("errors"):
        lines.append("ОШИБКИ валидации: " + " ".join(v["errors"]))
    if res.get("listings"):
        top = sorted(res["listings"], key=lambda x: x["wsm_rank"])[:5]
        lines.append("Топ-5 по WSM: " + "; ".join(
            f"{x['wsm_rank']}. {x['label']} — WSM {x['wsm']:.4f}, TOPSIS #{x['topsis_rank']} ({x['topsis']:.4f})" for x in top))
        t_lead = min(res["listings"], key=lambda x: x["topsis_rank"])
        c = res["comparison"]
        p_lead = min(res["listings"], key=lambda x: x["wpm_rank"])
        rho = res["methods"]["spearman"]
        lines.append(f"Лидер TOPSIS: {t_lead['label']}. Лидер WPM: {p_lead['label']}. Одинаковый победитель WSM/TOPSIS: "
                     f"{'да' if c['same_winner'] else 'нет'}; все три метода сошлись: "
                     f"{'да' if res['methods']['same_leader'] else 'нет'}. Спирмен ρ: WSM–TOPSIS {rho['wsm~topsis']:.2f}, "
                     f"WSM–WPM {rho['wsm~wpm']:.2f}, TOPSIS–WPM {rho['topsis~wpm']:.2f}. "
                     f"Доминируемых: {len(res.get('dominance') or [])}.")
        if res["methods"]["wpm_notes"]:
            lines.append("Где WPM расходится с WSM: " + " ".join(res["methods"]["wpm_notes"][:2]))
        s = res["sensitivity"]
        th = "; ".join(f"при весе {t['weight']:.3f}: {t['from']} → {t['to']}" for t in s["thresholds"]) or "лидер не меняется"
        lines.append(f"Чувствительность ({s['method'].upper()}, «{s['name']}», текущий вес {s['current']:.3f}): {th}.")
    return "<ui_state>\n" + "\n".join(lines) + "\n</ui_state>"


def _merge(body: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    new = json.loads(json.dumps(body))
    if args.get("weights"):
        w = dict(new.get("weights") or {})
        for k, v in args["weights"].items():
            w[k] = max(0.0, min(10.0, float(v)))
        new["weights"] = w
    if args.get("constraints"):
        c = dict(new.get("constraints") or {})
        for k, v in args["constraints"].items():
            if v is None or v is False or v == []:
                c.pop(k, None)
            else:
                c[k] = v
        new["constraints"] = c
    if args.get("missing"):
        new["missing"] = args["missing"]
    if args.get("sort"):
        new["sort"] = args["sort"]
    if args.get("point"):
        new["point"] = {"lat": float(args["point"]["lat"]), "lon": float(args["point"]["lon"])}
        new["point_name"] = args["point"].get("name") or "точка от ИИ-помощника"
    return new


def _validate(name: str, args: Any) -> Optional[str]:
    """Inputs stream eagerly, so the server did not check them against the schema - do it here."""
    if not isinstance(args, dict):
        return "аргументы должны быть объектом"
    if name in ("explain_listing",) and not isinstance(args.get("code"), str):
        return "нужен code — строка, например A12"
    if name == "compare_listings":
        codes = args.get("codes")
        if not (isinstance(codes, list) and 2 <= len(codes) <= 5 and all(isinstance(c, str) for c in codes)):
            return "нужен codes — список из 2–5 кодов"
    if name == "sensitivity" and args.get("criterion") not in BY_KEY:
        return "criterion должен быть ключом критерия: " + ", ".join(BY_KEY)
    w = args.get("weights")
    if w is not None:
        if not isinstance(w, dict) or any(k not in BY_KEY or not isinstance(v, (int, float)) for k, v in w.items()):
            return "weights — объект {ключ критерия: число 0–10}; ключи: " + ", ".join(BY_KEY)
    c = args.get("constraints")
    if c is not None and (not isinstance(c, dict) or any(k not in CONSTRAINT_PROPS for k in c)):
        return "constraints — ключи: " + ", ".join(CONSTRAINT_PROPS)
    p = args.get("point")
    if p is not None and not (isinstance(p, dict) and isinstance(p.get("lat"), (int, float))
                              and isinstance(p.get("lon"), (int, float))):
        return "point — {lat, lon, name}"
    return None


def _leaders(res: dict, n: int = 5) -> Dict[str, List[dict]]:
    ls = res.get("listings") or []
    return {"wsm": [_short(x) for x in sorted(ls, key=lambda x: x["wsm_rank"])[:n]],
            "topsis": [_short(x) for x in sorted(ls, key=lambda x: x["topsis_rank"])[:n]],
            "wpm": [_short(x) for x in sorted(ls, key=lambda x: x["wpm_rank"])[:n]]}


def _diff(old: dict, new: dict) -> dict:
    o = {x["code"]: x["wsm_rank"] for x in old.get("listings") or []}
    moves = []
    for x in new.get("listings") or []:
        if x["code"] in o and o[x["code"]] != x["wsm_rank"]:
            moves.append({"code": x["code"], "label": x["label"], "was": o[x["code"]], "now": x["wsm_rank"]})
    moves.sort(key=lambda m: -abs(m["was"] - m["now"]))
    lead = lambda r: min(r["listings"], key=lambda x: x["wsm_rank"])["code"] if r.get("listings") else None
    return {"wsm_leader_before": lead(old), "wsm_leader_after": lead(new),
            "leader_changed": lead(old) != lead(new), "biggest_moves": moves[:6],
            "left_out_of_ranking": [c for c in o if c not in {x["code"] for x in new.get("listings") or []}][:15]}


# ---- the tools themselves -----------------------------------------------------------------

class Tools:
    def __init__(self, dataset: dict, s: Session, emit: Callable[[dict], None]):
        self.dataset, self.s, self.emit = dataset, s, emit
        self._res: Optional[dict] = None

    @property
    def res(self) -> dict:
        if self._res is None:
            self._res = rank(self.dataset, to_request(self.s.req))
        return self._res

    def run(self, name: str, args: Dict[str, Any]) -> Any:
        return getattr(self, name)(**args)

    def get_ranking(self, top: int = 10, method: str = "wsm") -> dict:
        res = self.res
        if not (res.get("validation") or {}).get("ok"):
            return {"validation": res.get("validation")}
        key = method + "_rank" if method in ("wsm", "topsis", "wpm") else "wsm_rank"
        rows = sorted(res["listings"], key=lambda x: x[key])[: max(1, min(int(top or 10), 25))]
        names = {c["key"]: c["name"] for c in res["criteria"]}
        return {"method": method, "weights": {names[k]: round(v, 4) for k, v in res["weights"].items()},
                "total_alternatives": len(res["listings"]),
                "rows": [{**_short(x), "wpm": x["wpm"], "address": x["address"],
                          "values": {names[k]: v for k, v in x["values"].items()},
                          "imputed": [names.get(k, k) for k in x["imputed"]], "dominated_by": x["dominated_by"],
                          "geo_precision": x.get("geo")} for x in rows]}

    def explain_listing(self, code: str) -> dict:
        res = self.res
        x = next((l for l in res.get("listings") or [] if l["code"].lower() == code.strip().lower()), None)
        if x is None:
            ex = next((e for e in res.get("excluded") or [] if e["code"].lower() == code.strip().lower()), None)
            if ex:
                return {"code": ex["code"], "excluded_by_constraints": ex["reasons"]}
            return {"error": f"Квартиры {code} нет в текущем расчёте"}
        c = x["code"]
        norm, wsm_rec = _by_code(res["normalized"]).get(c, {}), _by_code(res["wsm"]).get(c, {})
        tn, tw = _by_code(res["topsis"]["normalized"]).get(c, {}), _by_code(res["topsis"]["weighted"]).get(c, {})
        ideal = _by_code(res["topsis"]["ideal"])
        tr = _by_code(res["topsis"]["result"]).get(c, {})
        wpm_rec = _by_code(res["wpm"]).get(c, {})
        crit = []
        for cr in res["criteria"]:
            k = cr["key"]
            crit.append({"criterion": cr["name"], "type": cr["kind"], "unit": cr["unit"], "weight": cr["weight"],
                         "value": x["values"].get(k), "imputed": k in x["imputed"],
                         "normalized_r": norm.get(k), "wsm_contribution": wsm_rec.get(k),
                         "wpm_factor_r_pow_w": wpm_rec.get(k),
                         "topsis_vector_n": tn.get(k), "topsis_weighted_v": tw.get(k),
                         "topsis_ideal_best": ideal.get("ideal_best", {}).get(k),
                         "topsis_ideal_worst": ideal.get("ideal_worst", {}).get(k)})
        return {"code": c, "label": x["label"], "title": x["title"], "address": x["address"], "url": x["url"],
                "geo_precision": x.get("geo"), "criteria": crit,
                "wsm": {"score": x["wsm"], "rank": x["wsm_rank"]},
                "topsis": {"d_plus": tr.get("d_plus"), "d_minus": tr.get("d_minus"), "C": x["topsis"],
                           "rank": x["topsis_rank"]},
                "wpm": {"score": x["wpm"], "rank": x["wpm_rank"]}, "of": len(res["listings"]),
                "strengths": x["strengths"], "weaknesses": x["weaknesses"], "consequences": x["consequences"],
                "dominated_by": x["dominated_by"]}

    def compare_listings(self, codes: List[str]) -> dict:
        out = [self.explain_listing(c) for c in codes]
        return {"listings": [{k: o.get(k) for k in ("code", "label", "wsm", "topsis", "wpm", "criteria",
                                                      "excluded_by_constraints", "error")} for o in out]}

    def what_if(self, **args) -> dict:
        body = _merge(self.s.req, args)
        new = rank(self.dataset, to_request(body))
        if not (new.get("validation") or {}).get("ok"):
            return {"validation": new.get("validation")}
        return {"weights_now": {c["name"]: c["weight"] for c in self.res.get("criteria") or []},
                "weights_what_if": {c["name"]: c["weight"] for c in new["criteria"]},
                "alternatives_after_constraints": len(new["listings"]),
                "leaders": _leaders(new), "change_vs_screen": _diff(self.res, new),
                "same_winner_wsm_topsis": new["comparison"]["same_winner"], "spearman": new["comparison"]["spearman"]}

    def sensitivity(self, criterion: str, method: str = "wsm") -> dict:
        body = dict(self.s.req, sens_key=criterion, sens_method=method)
        res = rank(self.dataset, to_request(body))
        if not (res.get("validation") or {}).get("ok"):
            return {"validation": res.get("validation")}
        if criterion not in res["weights"]:
            return {"error": f"Критерий {criterion} сейчас выключен — включите его или выберите другой."}
        s = res["sensitivity"]
        return {"criterion": s["name"], "method": s["method"], "current_weight": s["current"],
                "thresholds": s["thresholds"], "scores_at_weights": s["at"],
                "note": "остальные веса делят остаток пропорционально текущим"}

    def apply_settings(self, summary: str = "", **args) -> dict:
        body = _merge(self.s.req, args)
        new = rank(self.dataset, to_request(body))
        before = self.res
        self.s.req = body
        self._res = new
        self.emit({"type": "apply", "settings": {k: args[k] for k in ("weights", "constraints", "missing", "sort", "point")
                                                 if args.get(k) is not None},
                   "summary": summary})
        if not (new.get("validation") or {}).get("ok"):
            return {"applied": True, "validation": new.get("validation")}
        return {"applied": True, "weights": {c["name"]: c["weight"] for c in new["criteria"]},
                "alternatives_after_constraints": len(new["listings"]), "leaders": _leaders(new),
                "change": _diff(before, new)}

    def lecture_example(self) -> dict:
        p = lecture_suppliers()
        w, t = run_wsm(p), run_topsis(p)
        rnd = lambda df, d=4: {i: {k: round(float(v), d) for k, v in row.items()} for i, row in df.iterrows()}
        return {"criteria": [{"key": c.key, "name": c.name, "type": c.kind, "unit": c.unit, "weight": 0.25} for c in p.criteria],
                "matrix": rnd(p.matrix, 2), "normalized_wsm": rnd(w["normalized"]), "wsm": rnd(w["result"]),
                "topsis_vector_normalized": rnd(t["normalized"]), "topsis_weighted": rnd(t["weighted"], 5),
                "topsis_ideal": rnd(t["ideal"], 5), "topsis": rnd(t["result"], 5),
                "sensitivity": "лидер WSM меняется D → A при весе надёжности ≈ 0,4016 (остальные по (1−t)/3)"}

    def get_formalization(self) -> dict:
        f = (self.res or {}).get("formalization")
        if not f:
            return {"validation": self.res.get("validation")}
        return {k: v for k, v in f.items() if k not in ("matrix", "alternatives")} | {
            "alternatives_count": len(f["alternatives"]),
            "alternatives_first": [a["code"] + " " + (a["title"] or "") for a in f["alternatives"][:8]]}


# ---- the conversation loop ------------------------------------------------------------------

_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(timeout=120.0, max_retries=2)
    return _client


def chat(dataset: dict, s: Session, body: Dict[str, Any], message: str, tab: str) -> Iterator[dict]:
    """Yields events: chat, thinking, step, text, apply, done, error."""
    import anthropic

    yield {"type": "chat", "chat_id": s.id}
    if not enabled():
        yield {"type": "error", "message": "ИИ-помощник не настроен: добавьте ANTHROPIC_API_KEY в файл .env проекта."}
        return
    if s.turns >= MAX_TURNS:
        yield {"type": "error", "message": "Диалог получился длинным — начните новый (кнопка «Новый диалог»)."}
        return
    s.turns += 1
    s.req = body
    events: List[dict] = []
    tools = Tools(dataset, s, events.append)
    current = rank(dataset, to_request(body))
    current["dataset"] = {"sample": dataset.get("sample", False)}
    tools._res = current
    s.messages.append({"role": "user", "content": [{"type": "text", "text": ui_state(current, body, tab)},
                                                   {"type": "text", "text": message}]})
    system = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            # history is append-only; the top-level cache marker moves to the newest block by itself
            with client().beta.messages.stream(
                    model=MODEL, max_tokens=16000, system=system, messages=s.messages, tools=TOOLS,
                    cache_control={"type": "ephemeral"},
                    thinking={"type": "adaptive", "display": "summarized"}, output_config={"effort": EFFORT},
                    betas=[FALLBACK_BETA], fallbacks="default") as stream:
                for ev in stream:
                    if ev.type == "content_block_start" and ev.content_block.type == "tool_use":
                        yield {"type": "step", "id": ev.content_block.id, "name": ev.content_block.name,
                               "label": TOOL_LABELS.get(ev.content_block.name, ev.content_block.name).split(" {")[0],
                               "status": "start"}
                    elif ev.type == "content_block_delta":
                        if ev.delta.type == "text_delta":
                            yield {"type": "text", "text": ev.delta.text}
                        elif ev.delta.type == "thinking_delta" and ev.delta.thinking:
                            yield {"type": "thinking", "text": ev.delta.thinking}
                final = stream.get_final_message()
        except ValueError:
            # tool input JSON the SDK could not parse at all - nothing to answer; ask the model to retry
            continue
        except anthropic.RateLimitError:
            yield {"type": "error", "message": "Слишком много запросов к модели — попробуйте через минуту."}
            return
        except anthropic.APIStatusError as e:
            log.warning("LLM error %s: %s", e.status_code, e)
            yield {"type": "error", "message": f"Ошибка модели (HTTP {e.status_code}). Попробуйте ещё раз."}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "message": "Нет связи с API Anthropic. Проверьте интернет."}
            return

        s.messages.append({"role": "assistant", "content": final.content})
        if final.stop_reason == "refusal":
            yield {"type": "text", "text": "\n\nНа этот запрос я ответить не могу."}
            break
        uses = [b for b in final.content if b.type == "tool_use"]
        if final.stop_reason == "max_tokens" and uses:
            s.messages.pop()                    # a truncated tool call must not be run or kept
            yield {"type": "error", "message": "Ответ оборвался — переформулируйте вопрос короче."}
            return
        if not uses:
            break
        results = []
        for b in uses:
            args = b.input if isinstance(b.input, dict) else {}
            problem = _validate(b.name, b.input)
            label = TOOL_LABELS.get(b.name, b.name)
            try:
                label = label.format(code=args.get("code", ""), codes=", ".join(args.get("codes") or []))
            except (KeyError, IndexError):
                pass
            if problem or b.name not in TOOL_LABELS:
                out, err = {"error": problem or "неизвестный инструмент"}, True
            else:
                try:
                    out, err = tools.run(b.name, args), False
                except Exception as e:                    # a tool bug must not end the conversation
                    log.exception("tool %s failed", b.name)
                    out, err = {"error": f"{type(e).__name__}: {e}"}, True
            for e in events:
                yield e
            events.clear()
            yield {"type": "step", "id": b.id, "name": b.name, "label": label, "status": "error" if err else "done"}
            results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": err,
                            "content": json.dumps(out, ensure_ascii=False, default=str)})
        s.messages.append({"role": "user", "content": results})
    yield {"type": "done"}
