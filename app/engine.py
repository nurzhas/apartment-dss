"""From scraped listings and user preferences to a ranked, explained decision.

Pipeline (Assignment 4.2): constraints -> decision matrix -> missing values -> validation ->
normalization -> WSM / TOPSIS / WPM -> comparison, dominance, sensitivity -> charts.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from dss import (Criterion, DecisionProblem, compare_methods, compare_rankings, dominance, sensitivity, topsis,
                 validate, wpm, wsm)
from dss import charts
from dss.normalization import normalize_linear
from dss.problem import BENEFIT

from .criteria import BY_KEY, CITY_CENTER, CriterionSpec, condition_points, floor_points, specs_for

MIN_ALTERNATIVES = 5          # Assignment 4.1 asks for at least five


@dataclass
class RankRequest:
    weights: Dict[str, float]                       # raw importance or AHP weights; <= 0 means "off"
    point: Optional[Tuple[float, float]] = None
    point_name: str = ""
    constraints: Dict[str, object] = field(default_factory=dict)
    missing: str = "worst"                          # worst | median | drop
    sens_key: Optional[str] = None
    sens_method: str = "wsm"
    top: int = 15


def _fmt_money(v: float) -> str:
    if v >= 1e6:
        return f"{v / 1e6:.1f} млн ₸".replace(".0 ", " ")
    return f"{v:,.0f} ₸".replace(",", " ")


def short_label(x: dict) -> str:
    parts = [x["code"]]
    if x.get("rooms"):
        parts.append(f"{x['rooms']}-к")
    if x.get("area"):
        parts.append(f"{x['area']:g} м²")
    if x.get("price"):
        parts.append(_fmt_money(float(x["price"])))
    return " · ".join(parts)


# ---- constraints ------------------------------------------------------------------------

def apply_constraints(items: List[dict], c: Dict[str, object], point) -> Tuple[List[dict], List[dict], List[dict]]:
    """Hard limits are checked before ranking: an alternative outside them is not a candidate,
    whatever its score. Unknown values are not grounds for exclusion; they are counted instead."""
    rules = []
    if c.get("price_max"):
        rules.append(("Бюджет", f"≤ {_fmt_money(float(c['price_max']))}", lambda x: x.get("price"),
                      lambda v: v <= float(c["price_max"])))
    if c.get("price_min"):
        rules.append(("Цена от", f"≥ {_fmt_money(float(c['price_min']))}", lambda x: x.get("price"),
                      lambda v: v >= float(c["price_min"])))
    if c.get("rooms"):
        rooms = {int(r) for r in c["rooms"]}
        rules.append(("Комнат", ", ".join(("5+" if r >= 5 else str(r)) for r in sorted(rooms)),
                      lambda x: x.get("rooms"), lambda v: (min(int(v), 5) in rooms)))
    if c.get("area_min"):
        rules.append(("Площадь", f"≥ {float(c['area_min']):g} м²", lambda x: x.get("area"),
                      lambda v: v >= float(c["area_min"])))
    if c.get("not_first"):
        rules.append(("Не первый этаж", "да", lambda x: x.get("floor"), lambda v: v != 1))
    if c.get("not_last"):
        rules.append(("Не последний этаж", "да", lambda x: (x.get("floor"), x.get("floors")) if x.get("floors") else None,
                      lambda v: v[0] < v[1]))
    if c.get("year_min"):
        rules.append(("Год постройки", f"≥ {int(c['year_min'])}", lambda x: x.get("year"),
                      lambda v: v >= int(c["year_min"])))
    if c.get("condition_min"):
        rules.append(("Состояние", f"≥ {float(c['condition_min']):g} балла", condition_points,
                      lambda v: v >= float(c["condition_min"])))
    if c.get("distance_max") and point:
        spec = BY_KEY["distance"]
        rules.append(("Расстояние до точки", f"≤ {float(c['distance_max']):g} км", lambda x: spec.get(x, point),
                      lambda v: v <= float(c["distance_max"])))

    kept, excluded = [], []
    stats = [{"label": r[0], "value": r[1], "excluded": 0, "unknown": 0} for r in rules]
    for x in items:
        reasons = []
        for i, (label, value, get, ok) in enumerate(rules):
            v = get(x)
            if v is None:
                stats[i]["unknown"] += 1
            elif not ok(v):
                stats[i]["excluded"] += 1
                reasons.append(f"{label} {value}")
        if reasons:
            excluded.append({"code": x["code"], "title": x.get("title"), "reasons": reasons})
        else:
            kept.append(x)
    return kept, excluded, stats


# ---- the matrix ---------------------------------------------------------------------------

def build_matrix(items: List[dict], specs: List[CriterionSpec], point) -> pd.DataFrame:
    rows = {x["code"]: {s.key: s.get(x, point) for s in specs} for x in items}
    return pd.DataFrame.from_dict(rows, orient="index", columns=[s.key for s in specs]).astype(float)


def fill_missing(m: pd.DataFrame, specs: List[CriterionSpec], strategy: str) -> Tuple[pd.DataFrame, dict]:
    """Validation forbids gaps, so they are resolved explicitly and reported:
    worst  - the worst observed value (pessimistic: unknown is not rewarded),
    median - the typical value of the sample,
    drop   - alternatives with gaps leave the decision."""
    report = {"strategy": strategy, "dropped_criteria": [], "dropped": [], "filled": [], "by_criterion": {}}
    m = m.copy()
    for s in specs:
        if m[s.key].isna().all():
            report["dropped_criteria"].append(s.name)
            m = m.drop(columns=[s.key])
    keys = list(m.columns)
    if not keys:
        return m, report
    too_sparse = m.index[m.isna().sum(axis=1) > len(keys) / 2]
    if strategy != "drop" and len(too_sparse):
        report["dropped"] += [{"code": a, "why": "нет данных по большинству критериев"} for a in too_sparse]
        m = m.drop(index=too_sparse)
    for k in keys:
        n = int(m[k].isna().sum())
        if n:
            report["by_criterion"][BY_KEY[k].name] = n
    if strategy == "drop":
        gaps = m.index[m.isna().any(axis=1)]
        report["dropped"] += [{"code": a, "why": "есть пропуски: " + ", ".join(BY_KEY[k].name for k in keys if np.isnan(m.at[a, k]))}
                              for a in gaps]
        return m.drop(index=gaps), report
    for k in keys:
        col = m[k]
        if not col.isna().any():
            continue
        if strategy == "median":
            v = float(col.median())
        else:
            v = float(col.min() if BY_KEY[k].kind == BENEFIT else col.max())
        for a in col.index[col.isna()]:
            report["filled"].append({"code": a, "key": k, "criterion": BY_KEY[k].name, "value": v})
        m[k] = col.fillna(v)
    return m, report


def normalize_weights(raw: Dict[str, float], keys: List[str]) -> Dict[str, float]:
    w = {k: max(0.0, float(raw.get(k, 0) or 0)) for k in keys}
    total = sum(w.values())
    if total <= 0:
        return {k: 1 / len(keys) for k in keys}
    w = {k: v / total for k, v in w.items()}
    # rounding residue goes to the largest weight, so the sum is exactly 1
    big = max(w, key=w.get)
    w[big] += 1.0 - sum(w.values())
    return w


# ---- output helpers -----------------------------------------------------------------------

def _records(df: pd.DataFrame, digits: int = 4) -> List[dict]:
    out = []
    for idx, row in df.iterrows():
        rec = {"code": idx}
        for k, v in row.items():
            if isinstance(v, (float, np.floating)):
                rec[k] = None if np.isnan(v) else round(float(v), digits)
            elif isinstance(v, (np.integer,)):
                rec[k] = int(v)
            else:
                rec[k] = v
        out.append(rec)
    return out


def _strengths(norm_row: pd.Series, w: Dict[str, float]) -> Tuple[List[str], List[str]]:
    good = sorted([k for k in norm_row.index if norm_row[k] >= 0.95 and w[k] > 0], key=lambda k: -w[k])
    bad = sorted([k for k in norm_row.index if norm_row[k] < 0.7 and w[k] > 0],
                 key=lambda k: -(w[k] * (1 - norm_row[k])))
    return [BY_KEY[k].name for k in good[:3]], [BY_KEY[k].name for k in bad[:3]]


def consequences(x: dict, raw_row: pd.Series, med: pd.Series, imputed: List[str], mode: str) -> List[str]:
    """What choosing this alternative implies - the 'consequences' part of the formalization."""
    out = []
    if "price" in raw_row and med.get("price"):
        d = raw_row["price"] / med["price"] - 1
        if abs(d) >= 0.03:
            out.append(f"Цена на {abs(d) * 100:.0f}% {'ниже' if d < 0 else 'выше'} медианы подборки "
                       f"({_fmt_money(med['price'])}){' в месяц' if mode == 'rent' else ''}.")
    if "distance" in raw_row and "distance" not in imputed:
        how = {"district": " (оценка по центру района — точность несколько км)",
               "street": " (по улице — точность до 1–2 км)", "complex": " (по ЖК)"}.get(x.get("geo"), "")
        out.append(f"{raw_row['distance']:.1f} км по прямой до выбранной точки{how}.")
    cp = condition_points(x)
    if cp is not None and cp <= 2:
        out.append("Нужен ремонт с нуля — дополнительные расходы и время до заселения." if cp < 2 else
                   "Квартира требует ремонта — заложите бюджет на ремонт.")
    fp = floor_points(x)
    if fp == 1:
        out.append("Первый этаж: шум с улицы, меньше приватности.")
    elif fp == 2:
        out.append("Последний этаж: риск протечек кровли, летом жарче.")
    year = x.get("year")
    if year and year > dt.date.today().year:
        out.append(f"Дом ещё строится (сдача {year}) — риск задержки сдачи.")
    elif year and year < 1985:
        out.append(f"Дом {year} года — изношенные коммуникации, возможен капремонт.")
    if mode == "rent" and x.get("furniture") == "без мебели":
        out.append("Без мебели — расходы на обстановку.")
    if imputed:
        out.append("Не указано в объявлении: " + ", ".join(BY_KEY[k].name.lower() for k in imputed)
                   + " — оценка по подставленному значению, уточните у продавца.")
    return out


def wpm_notes(w_result: pd.DataFrame, p_result: pd.DataFrame, factors: pd.DataFrame,
              names: Dict[str, str], labels: Dict[str, str]) -> List[str]:
    """Why WPM moved someone compared with WSM: the weakest factor r^w of the biggest movers."""
    shift = (p_result["rank"] - w_result["rank"]).sort_values()
    notes = []
    for a in list(shift.index[shift > 0][::-1][:2]) + list(shift.index[shift < 0][:2]):
        k = factors.loc[a].idxmin()
        f = float(factors.at[a, k])
        down = shift[a] > 0
        notes.append(
            f"{labels.get(a, a)}: WSM #{int(w_result.at[a, 'rank'])} → WPM #{int(p_result.at[a, 'rank'])}. "
            + (f"Самый слабый множитель — «{names.get(k, k)}» (r^w = {f:.3f}): в произведении он уменьшает "
               f"весь балл в {1 / f:.2f} раза, и сильные стороны это не перекрывают."
               if down else
               f"Нет провалов: самый слабый множитель «{names.get(k, k)}» = {f:.3f}, поэтому в произведении "
               "квартира теряет мало, хотя в сумме её обгоняли варианты с яркими плюсами."))
    return notes


# ---- the whole decision -------------------------------------------------------------------

def rank(dataset: dict, req: RankRequest) -> dict:
    mode = dataset["mode"]
    items = dataset["listings"]
    point = req.point or CITY_CENTER
    specs = [s for s in specs_for(mode) if float(req.weights.get(s.key, 0) or 0) > 0]
    result: dict = {"mode": mode, "point": {"lat": point[0], "lon": point[1], "name": req.point_name}}
    if not specs:
        result["validation"] = {"ok": False, "errors": ["Включите хотя бы один критерий."], "warnings": []}
        return result

    kept, excluded, cstats = apply_constraints(items, req.constraints, point)
    result.update(constraints=cstats, excluded=excluded, total=len(items))

    raw = build_matrix(kept, specs, point)
    matrix, missing = fill_missing(raw, specs, req.missing)
    result["missing"] = missing
    specs = [s for s in specs if s.key in matrix.columns]
    keys = [s.key for s in specs]
    weights = normalize_weights(req.weights, keys) if keys else {}
    by_code = {x["code"]: x for x in kept}
    labels = {a: short_label(by_code[a]) for a in matrix.index}

    criteria = [Criterion(s.key, s.name, s.kind, s.unit) for s in specs]
    problem = DecisionProblem(matrix, criteria, weights, labels)
    report = validate(problem)
    if len(matrix) < MIN_ALTERNATIVES and report.ok:
        report.warnings.append(f"Осталось {len(matrix)} альтернатив — для задания 4.1 нужно не меньше "
                               f"{MIN_ALTERNATIVES}. Ослабьте ограничения или загрузите больше страниц.")
    result["validation"] = report.as_dict()
    result["criteria"] = [{"key": s.key, "name": s.name, "kind": s.kind, "unit": s.unit, "how": s.how,
                           "scale": s.scale, "weight": round(weights[s.key], 4),
                           "importance": float(req.weights.get(s.key, 0))} for s in specs]
    if not report.ok:
        return result

    w_res, t_res, p_res = wsm(problem), topsis(problem), wpm(problem)
    cmp = compare_rankings(w_res["result"], t_res["result"], labels)
    three = compare_methods({"wsm": w_res["result"], "topsis": t_res["result"], "wpm": p_res["result"]}, labels)
    dom = dict(dominance(problem))
    sens_key = req.sens_key if req.sens_key in keys else max(weights, key=weights.get)
    sens = sensitivity(problem, sens_key, "topsis" if req.sens_method == "topsis" else "wsm")

    norm = w_res["normalized"][keys]
    med = matrix.median()
    imputed: Dict[str, List[str]] = {}
    for f in missing["filled"]:
        imputed.setdefault(f["code"], []).append(f["key"])

    listings = []
    for a in cmp["table"].index:
        x = by_code[a]
        good, bad = _strengths(norm.loc[a], weights)
        listings.append({
            "code": a, "id": x["id"], "url": x["url"], "title": x.get("title"), "label": labels[a],
            "address": x.get("address_full") or x.get("address"), "district": x.get("district"),
            "complex": x.get("complex"), "photo": x.get("photo"), "lat": x.get("lat"), "lon": x.get("lon"),
            "geo": x.get("geo"),
            "price": x.get("price"), "area": x.get("area"), "rooms": x.get("rooms"),
            "floor": x.get("floor"), "floors": x.get("floors"), "year": x.get("year"),
            "condition": x.get("condition"), "house_type": x.get("house_type"), "furniture": x.get("furniture"),
            "values": {k: round(float(matrix.at[a, k]), 3) for k in keys},
            "imputed": imputed.get(a, []),
            "wsm": round(float(w_res["result"].at[a, "score"]), 4),
            "wsm_rank": int(w_res["result"].at[a, "rank"]),
            "topsis": round(float(t_res["result"].at[a, "score"]), 4),
            "topsis_rank": int(t_res["result"].at[a, "rank"]),
            "wpm": round(float(p_res["result"].at[a, "score"]), 4),
            "wpm_rank": int(p_res["result"].at[a, "rank"]),
            "strengths": good, "weaknesses": bad,
            "dominated_by": dom.get(a),
            "consequences": consequences(x, matrix.loc[a], med, imputed.get(a, []), mode),
        })

    names = {s.key: s.name for s in specs}
    top_idx = list(cmp["table"].index[: req.top])
    result.update(
        weights=weights,
        labels=labels,
        listings=listings,
        matrix=_records(matrix, 3),
        normalized=_records(norm),
        wsm=_records(w_res["contribution"].assign(score=w_res["result"]["score"], rank=w_res["result"]["rank"])
                     .sort_values("rank")),
        topsis={"normalized": _records(t_res["normalized"], 5), "weighted": _records(t_res["weighted"], 5),
                "ideal": _records(t_res["ideal"], 5), "result": _records(t_res["result"], 5)},
        # r^w per criterion; their product is the WPM score
        wpm=_records(p_res["factors"].assign(score=p_res["result"]["score"], rank=p_res["result"]["rank"])
                     .sort_values("rank")),
        methods={"table": _records(three["table"]), "leaders": three["leader_codes"],
                 "spearman": {k: round(v, 4) for k, v in three["spearman"].items()},
                 "same_leader": three["same_leader"],
                 "wpm_notes": wpm_notes(w_res["result"], p_res["result"], p_res["factors"], names, labels)},
        comparison={"table": _records(cmp["table"]), "same_winner": cmp["same_winner"],
                    "spearman": round(cmp["spearman"], 4), "identical": cmp["identical"],
                    "answers": [{"q": q, "a": ans} for q, ans in cmp["answers"]]},
        dominance=[{"code": a, "by": b} for a, b in dom.items()],
        sensitivity={"key": sens_key, "name": names[sens_key], "method": sens["method"],
                     "current": round(sens["current_weight"], 4),
                     # scores of the leaders at a few weights - for the assistant's explanations
                     "at": [{"alternative": labels.get(a, a),
                             "scores": {f"{t:.2f}": round(float(sens["scores"][a].iloc[
                                 int(np.abs(sens["scores"].index.to_numpy() - t).argmin())]), 4)
                                 for t in (0.0, 0.25, 0.5, 0.75, 1.0)}}
                            for a in list(sens["scores"].columns)[:5]],
                     "thresholds": [{"weight": round(t["weight"], 4), "from": t["from"], "to": t["to"]}
                                    for t in sens["thresholds"]]},
        charts={
            "scores": charts.figure_json(charts.scores_bar(three["table"], labels, req.top)),
            "contrib": charts.figure_json(charts.contribution_bar(w_res["contribution"], names, labels, keys, req.top)),
            "ranks": charts.figure_json(charts.rank_slope(cmp["table"], labels, req.top)),
            "heatmap": charts.figure_json(charts.heatmap(norm, matrix, names, labels, keys, top_idx)),
            "sensitivity": charts.figure_json(charts.sensitivity_lines(sens["scores"], labels, names[sens_key],
                                                                         sens["current_weight"], sens["thresholds"])),
        },
    )
    result["formalization"] = formalize(dataset, req, result, specs, matrix)
    return result


def formalize(dataset: dict, req: RankRequest, res: dict, specs: List[CriterionSpec], matrix: pd.DataFrame) -> dict:
    """Assignment 4.1, filled from the current decision rather than written once by hand."""
    mode = dataset["mode"]
    deal = "покупки" if mode == "sale" else "долгосрочной аренды"
    leader = res["listings"][0] if res.get("listings") else None
    active = [c for c in res["constraints"]]
    return {
        "name": f"Выбор квартиры для {deal} в Астане",
        "dm": ("Покупатель квартиры (частное лицо или семья), принимающий решение о покупке и "
               "отвечающий за бюджет." if mode == "sale" else
               "Арендатор (частное лицо или семья), выбирающий жильё на срок от нескольких месяцев."),
        "goal": (f"Из актуальных объявлений Krisha.kz выбрать одну квартиру для {deal}, которая наилучшим образом "
                 "соответствует предпочтениям ЛПР по цене, площади, расположению и качеству жилья "
                 "при соблюдении жёстких ограничений."),
        "alternatives": [{"code": x["code"], "title": x["title"], "address": x["address"], "url": x["url"]}
                         for x in res.get("listings", [])],
        "alternatives_total": dataset.get("count", len(dataset["listings"])),
        "criteria": res["criteria"],
        "constraints": active,
        "consequences": [{"code": x["code"], "label": x["label"], "items": x["consequences"]}
                         for x in res.get("listings", [])[:8]],
        "task_type": {
            "value": "Полуструктурированная",
            "why": ("Часть задачи хорошо формализуется: цена, площадь, год, расстояние — числа, а сам выбор "
                    "сводится к ранжированию альтернатив. Но веса критериев — субъективные предпочтения ЛПР, "
                    "качественные признаки (состояние, тип дома) переводятся в баллы экспертной шкалой, "
                    "а данные объявлений неполны и не проверены. Поэтому компьютер считает, а последнее "
                    "слово — за человеком (просмотр квартиры, торг, проверка документов)."),
        },
        "conditions": ("Решение в условиях частичной неопределённости: продавец может указать неверные данные, "
                       "цена может измениться при торге, объявление может быть снято."),
        "dss_type": {
            "value": "Model-driven DSS с data-driven компонентом",
            "why": ("Ядро — модели многокритериального выбора (WSM, TOPSIS, WPM, анализ чувствительности): "
                    "система не просто показывает данные, а вычисляет рейтинг по модели. Data-driven часть — "
                    "сбор и очистка объявлений с Krisha.kz (поиск, кэш, извлечение параметров и координат)."),
        },
        "simon": [
            {"phase": "Intelligence (поиск)", "here": "Поиск объявлений на Krisha.kz по фильтрам, сбор параметров."},
            {"phase": "Design (проектирование)", "here": "Критерии, шкалы, ограничения, веса; матрица решений."},
            {"phase": "Choice (выбор)", "here": "Ранжирование WSM и TOPSIS, сравнение методов, чувствительность."},
            {"phase": "Implementation (реализация)", "here": "Переход к объявлению, просмотр, торг, сделка."},
        ],
        "risks": [
            "Плохие данные: ошибки и пропуски в объявлениях — пропуски показываются и заполняются явно.",
            "Неточные координаты: адрес из объявления переводится в точку через OpenStreetMap (дом, улица, "
            "ЖК или центр района) — точность расстояния указана у каждой квартиры.",
            "Неверная модель: WSM линейна и компенсирует слабые стороны сильными — поэтому рядом TOPSIS и WPM.",
            "Смещение весов: ЛПР может завысить важность одного критерия — поэтому анализ чувствительности.",
            "Автоматическое доверие: рейтинг — подсказка, а не решение; квартиру нужно посмотреть вживую.",
        ],
        "matrix": res.get("matrix", []),
        "weights": res.get("weights", {}),
        "leader": leader and {"code": leader["code"], "label": leader["label"], "url": leader["url"]},
    }
