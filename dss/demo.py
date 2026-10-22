"""Console walkthrough of Assignment 4.2 on the Lecture 7.1 example (suppliers A-D).
Run: .venv\\Scripts\\python -m dss.demo        (add --html to also save the charts)"""
import sys

import pandas as pd

from . import charts, compare_rankings, sensitivity, topsis, validate, wpm, wsm
from .examples import lecture_suppliers


def section(title):
    print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)


def main(save_html: bool = False):
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 120)
    p = lecture_suppliers()

    section("1. Входные данные")
    print(pd.DataFrame([{"критерий": c.name, "тип": c.kind, "ед.": c.unit, "вес": p.weights[c.key]} for c in p.criteria],
                       index=p.keys))
    print("\nМатрица решений:\n", p.matrix)

    section("2. Проверка данных")
    r = validate(p)
    print("OK" if r.ok else "ОШИБКИ:", *r.errors, *r.warnings, sep="\n  ")

    section("3. Нормализация (benefit: x/max, cost: min/x)")
    w = wsm(p)
    print(w["normalized"].round(4))

    section("4. WSM")
    for a, row in w["result"].iterrows():
        print(f"  {a} → {row.score:.4f}   (место {int(row['rank'])})")

    section("5. TOPSIS")
    t = topsis(p)
    print("Векторная нормализация:\n", t["normalized"].round(4))
    print("\nВзвешенная матрица:\n", t["weighted"].round(5))
    print("\nИдеал и анти-идеал:\n", t["ideal"].round(5))
    print("\nРезультат:\n", t["result"].round(5))

    section("6. Сравнение WSM и TOPSIS")
    c = compare_rankings(w["result"], t["result"])
    print(c["table"].round(4))
    for q, a in c["answers"]:
        print(f"\n{q}\n  {a}")

    section("Дополнительно: WPM и чувствительность к весу «Надёжность»")
    print(wpm(p)["result"].round(4))
    s = sensitivity(p, "reliability", "wsm")
    for th in s["thresholds"]:
        print(f"  лидер меняется {th['from']} → {th['to']} при весе {th['weight']:.4f}")

    if save_html:
        labels = {a: a for a in p.matrix.index}
        names = {c.key: c.name for c in p.criteria}
        figs = [charts.scores_bar(c["table"], labels), charts.contribution_bar(w["contribution"], names, labels, p.keys),
                charts.heatmap(w["normalized"], p.matrix, names, labels, p.keys, list(w["result"].index)),
                charts.sensitivity_lines(s["scores"], labels, "Надёжность", 0.25, s["thresholds"])]
        with open("demo_charts.html", "w", encoding="utf-8") as f:
            f.write("<meta charset='utf-8'>")
            for i, fig in enumerate(figs):
                f.write(fig.to_html(full_html=False, include_plotlyjs="cdn" if i == 0 else False))
        print("\nГрафики сохранены в demo_charts.html")


if __name__ == "__main__":
    main("--html" in sys.argv)
