import argparse
import math
import os
import re
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "math_diagram_renderer_mpl"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


FIGURE_SIZE_INCHES = (1.8, 1.3)  # 720 x 520 px at 400 dpi; quarter-size in HWP/print layout.
GEOMETRY_SIZE_INCHES = (1.5, 1.5)
CHOICE_FIGURE_SIZE_INCHES = (1.8, 2.2)
OUTPUT_DPI = 400
STYLE_SCALE = 200 / OUTPUT_DPI


SAFE_FUNCS = {
    "sqrt": np.sqrt,
    "abs": np.abs,
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
    "log": np.log,
    "ln": np.log,
    "pi": math.pi,
}


def fs(value):
    return value * STYLE_SCALE


def lw(value):
    return value * STYLE_SCALE


def marker_area(value):
    return value * STYLE_SCALE * STYLE_SCALE


def extract_image_prompt_blocks(text):
    pattern = re.compile(r"\[IMAGE_PROMPT\s*(\d*)\s*:([\s\S]*?)\]", re.MULTILINE)
    blocks = []
    for index, match in enumerate(pattern.finditer(text), start=1):
        tag_number = int(match.group(1)) if match.group(1) else index
        blocks.append((tag_number, match.group(2).strip()))
    return blocks


def parse_key_values(block):
    data = {}
    for raw_line in block.replace("\\n", "\n").splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def parse_range(value, default):
    if not value:
        return default
    match = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*(?:\.\.|,|~|to)\s*(-?\d+(?:\.\d+)?)\s*$", value)
    if not match:
        return default
    lo = float(match.group(1))
    hi = float(match.group(2))
    if lo == hi:
        return default
    return (min(lo, hi), max(lo, hi))


def split_csv_outside_parentheses(value):
    items = []
    current = []
    depth = 0
    for ch in value or "":
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
        else:
            current.append(ch)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def split_semicolon_outside_parentheses(value):
    items = []
    current = []
    depth = 0
    for ch in value or "":
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == ";" and depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
        else:
            current.append(ch)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def normalize_expr(expr):
    text = str(expr or "").strip()
    text = text.replace("α", "alpha").replace("β", "beta")
    text = text.replace("²", "^2")
    text = text.replace("−", "-")
    text = text.replace("^", "**")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"(\d)(x)", r"\1*\2", text)
    text = re.sub(r"(\d)(\()", r"\1*\2", text)
    text = re.sub(r"(\))(\()", r"\1*\2", text)
    text = re.sub(r"(\))([x\d])", r"\1*\2", text)
    text = re.sub(r"(x)(\()", r"\1*\2", text)
    return text


def parse_equations(value):
    equations = []
    for item in split_csv_outside_parentheses(value):
        clean = item.strip()
        if not clean:
            continue
        if re.search(r"\b[a-wzA-WZ]\b", clean.replace("x", "").replace("y", "")):
            equations.append({"raw": clean, "kind": "unsupported", "reason": "unresolved variable"})
            continue
        if clean.startswith("y=") or clean.startswith("y ="):
            rhs = clean.split("=", 1)[1]
            equations.append({"raw": clean, "kind": "y", "expr": normalize_expr(rhs)})
        elif "=" in clean and re.search(r"\bx\b", clean):
            left, right = clean.split("=", 1)
            if normalize_expr(right) == "0":
                equations.append({"raw": "y = " + left.strip(), "kind": "y", "expr": normalize_expr(left)})
            else:
                equations.append({"raw": clean, "kind": "unsupported", "reason": "not explicit y= or x="})
        elif clean.startswith("x=") or clean.startswith("x ="):
            rhs = clean.split("=", 1)[1]
            try:
                equations.append({"raw": clean, "kind": "x", "value": float(normalize_expr(rhs))})
            except Exception:
                equations.append({"raw": clean, "kind": "unsupported", "reason": "bad vertical line"})
        else:
            equations.append({"raw": clean, "kind": "unsupported", "reason": "not y= or x="})
    return equations


def parse_points(value):
    if not value:
        return []
    if re.search(r"문제\s*본문|제시된\s*점|given", value, re.I):
        return []
    points = []
    pattern = re.compile(r"(?:(?P<label>[A-Za-z가-힣]\w*)\s*)?\(\s*(?P<x>-?\d+(?:\.\d+)?)\s*,\s*(?P<y>-?\d+(?:\.\d+)?)\s*\)")
    for idx, match in enumerate(pattern.finditer(value), start=1):
        label = match.group("label") or ""
        points.append({
            "label": label,
            "x": float(match.group("x")),
            "y": float(match.group("y")),
        })
    return points


def has_ambiguous_points(value):
    return bool(re.search(r"문제\s*본문|제시된\s*점|given", value or "", re.I))


def parse_labels(value):
    labels = []
    for item in split_csv_outside_parentheses(value or ""):
        label = item.strip()
        if not label or label.lower() == "none":
            continue
        if re.search(r"[=^²√]|\b[xy]\b", label, re.I):
            continue
        labels.append(label)
    return labels


def safe_eval(expr, x):
    return eval(expr, {"__builtins__": {}}, {"x": x, **SAFE_FUNCS})


def parse_y_equation(raw):
    text = str(raw or "").strip()
    if text.startswith("y=") or text.startswith("y ="):
        return {
            "raw": text,
            "kind": "y",
            "expr": normalize_expr(text.split("=", 1)[1]),
        }
    return {
        "raw": "y=" + text,
        "kind": "y",
        "expr": normalize_expr(text),
    }


def parse_number(value, default=0.0):
    try:
        return float(normalize_expr(value))
    except Exception:
        return float(default)


def setup_axes(ax, x_range, y_range):
    ax.set_xlim(*x_range)
    ax.set_ylim(*y_range)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

    xmin, xmax = x_range
    ymin, ymax = y_range
    axis_arrow = dict(
        arrowstyle="-|>",
        color="black",
        lw=lw(1.2),
        mutation_scale=fs(9),
        shrinkA=0,
        shrinkB=0,
    )
    if ymin <= 0 <= ymax:
        ax.annotate("", xy=(xmax, 0), xytext=(xmin, 0), arrowprops=axis_arrow, zorder=3)
        ax.text(xmax, 0, "  x", ha="left", va="center", fontsize=fs(10), clip_on=False)
    if xmin <= 0 <= xmax:
        ax.annotate("", xy=(0, ymax), xytext=(0, ymin), arrowprops=axis_arrow, zorder=3)
        ax.text(0, ymax, "y", ha="center", va="bottom", fontsize=fs(10), clip_on=False)
    if xmin <= 0 <= xmax and ymin <= 0 <= ymax:
        ax.text(0, 0, " O", ha="left", va="top", fontsize=fs(10), zorder=6)

    ax.grid(False)
    ax.set_aspect("auto")


def setup_choice_axes(ax, x_range, y_range):
    ax.set_xlim(*x_range)
    ax.set_ylim(*y_range)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

    xmin, xmax = x_range
    ymin, ymax = y_range
    axis_arrow = dict(
        arrowstyle="-|>",
        color="black",
        lw=lw(0.9),
        mutation_scale=fs(7),
        shrinkA=0,
        shrinkB=0,
    )
    if ymin <= 0 <= ymax:
        ax.annotate("", xy=(xmax, 0), xytext=(xmin, 0), arrowprops=axis_arrow, zorder=3)
        ax.text(xmax, 0, " x", ha="left", va="center", fontsize=fs(7), clip_on=False)
    if xmin <= 0 <= xmax:
        ax.annotate("", xy=(0, ymax), xytext=(0, ymin), arrowprops=axis_arrow, zorder=3)
        ax.text(0, ymax, "y", ha="center", va="bottom", fontsize=fs(7), clip_on=False)
    if xmin <= 0 <= xmax and ymin <= 0 <= ymax:
        ax.text(0, 0, " O", ha="left", va="top", fontsize=fs(7), zorder=6)

    ax.grid(False)
    ax.set_aspect("auto")


def format_number(value):
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    fraction = Fraction(number).limit_denominator(12)
    if abs(float(fraction) - number) < 1e-6:
        return f"{fraction.numerator}/{fraction.denominator}"
    return ""


def annotate_axis_value(ax, x_range, y_range, axis, value, side="auto"):
    xmin, xmax = x_range
    ymin, ymax = y_range
    text = format_number(value)
    if not text:
        return
    if axis == "x" and xmin <= value <= xmax and ymin <= 0 <= ymax:
        is_above = side == "above"
        y_offset = 7 if is_above else -8
        va = "bottom" if is_above else "top"
        ax.annotate(text, (value, 0), xytext=(0, y_offset),
                    textcoords="offset points", ha="center", va=va,
                    fontsize=fs(9), color="dimgray", zorder=8,
                    bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=fs(0.6)))
    if axis == "y" and ymin <= value <= ymax and xmin <= 0 <= xmax:
        is_right = side in ("right", "right_below", "right_above")
        is_below = side in ("left_below", "right_below")
        is_above = side in ("left_above", "right_above")
        x_offset = 7 if is_right else -7
        y_offset = -6 if is_below else (6 if is_above else 0)
        ha = "left" if is_right else "right"
        va = "top" if is_below else ("bottom" if is_above else "center")
        ax.annotate(text, (0, value), xytext=(x_offset, y_offset),
                    textcoords="offset points", ha=ha, va=va,
                    fontsize=fs(9), color="dimgray", zorder=8,
                    bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=fs(0.6)))


def annotate_horizontal_line_label(ax, x_range, y_range, y_value):
    text = format_number(y_value)
    if not text:
        return
    xmin, xmax = x_range
    ymin, ymax = y_range
    if not (ymin <= y_value <= ymax):
        return
    x_pos = xmin + (xmax - xmin) * 0.78
    ax.annotate("y = " + text, (x_pos, y_value), xytext=(4, 4),
                textcoords="offset points", ha="left", va="bottom",
                fontsize=fs(9), color="dimgray", zorder=8,
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=fs(0.6)))


def expand_range_for_points(x_range, y_range, points):
    if not points:
        return x_range, y_range
    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    xmin, xmax = x_range
    ymin, ymax = y_range
    xmin = min(xmin, min(xs) - 1)
    xmax = max(xmax, max(xs) + 1)
    ymin = min(ymin, min(ys) - 1)
    ymax = max(ymax, max(ys) + 1)
    return (xmin, xmax), (ymin, ymax)


def pad_range(lo, hi, ratio=0.15, minimum=1.0):
    if lo == hi:
        lo -= minimum
        hi += minimum
    span = max(abs(hi - lo), minimum)
    pad = span * ratio
    return lo - pad, hi + pad


def y_values_for_equations(equations, x_range):
    x = np.linspace(x_range[0], x_range[1], 600)
    values = []
    for equation in equations:
        if equation.get("kind") != "y":
            continue
        try:
            y = safe_eval(equation["expr"], x)
            if np.isscalar(y):
                y = np.full_like(x, float(y))
            finite = np.asarray(y)[np.isfinite(y)]
            if finite.size:
                values.extend([float(np.nanmin(finite)), float(np.nanmax(finite))])
        except Exception:
            pass
    return values


def quadratic_coefficients(equation):
    xs = np.array([-1.0, 0.0, 1.0, 2.0])
    ys = np.asarray(safe_eval(equation["expr"], xs), dtype=float)
    if np.isscalar(ys):
        ys = np.full_like(xs, float(ys))
    coeffs = np.polyfit(xs, ys, 2)
    a, b, c = [float(value) for value in coeffs]
    if abs(a) < 1e-9:
        raise ValueError("not a quadratic equation")
    return a, b, c


def quadratic_vertex(coeffs):
    a, b, c = coeffs
    x = -b / (2 * a)
    y = a * x * x + b * x + c
    return x, y


def quadratic_x_intercepts(coeffs):
    a, b, c = coeffs
    discriminant = b * b - 4 * a * c
    if discriminant < -1e-9:
        return []
    if abs(discriminant) < 1e-9:
        return [-b / (2 * a)]
    root = math.sqrt(discriminant)
    return sorted([(-b - root) / (2 * a), (-b + root) / (2 * a)])


def quadratic_roots_from_coeffs(a, b, c):
    if abs(a) < 1e-9:
        if abs(b) < 1e-9:
            return []
        return [-c / b]
    discriminant = b * b - 4 * a * c
    if discriminant < -1e-9:
        return []
    if abs(discriminant) < 1e-9:
        return [-b / (2 * a)]
    root = math.sqrt(discriminant)
    return sorted([(-b - root) / (2 * a), (-b + root) / (2 * a)])


def positive_roots_for_y_level(equation, y_value):
    a, b, c = quadratic_coefficients(equation)
    roots = quadratic_roots_from_coeffs(a, b, c - y_value)
    return sorted(root for root in roots if root >= -1e-9)


def equation_value(equation, x_value):
    return float(safe_eval(equation["expr"], float(x_value)))


def point_item(label, x, y):
    return {"label": label, "x": float(x), "y": float(y)}


def plot_labeled_points(ax, points):
    for point in points:
        ax.scatter([point["x"]], [point["y"]], color="black", s=marker_area(28), zorder=6)
        if point.get("label"):
            ax.annotate(point["label"], (point["x"], point["y"]), xytext=(4, 4),
                        textcoords="offset points", fontsize=fs(10), zorder=7)


def axis_label_sides_for_polygon(polygon):
    if not polygon:
        return "below", "left"
    centroid_x = sum(point["x"] for point in polygon) / len(polygon)
    centroid_y = sum(point["y"] for point in polygon) / len(polygon)
    x_side = "above" if centroid_y < 0 else "below"
    y_side = "right" if centroid_x < 0 else "left"
    return x_side, y_side


def render_parabola_calculated_template(spec, output_path, mode):
    equation = parse_y_equation(spec.get("equation", ""))
    coeffs = quadratic_coefficients(equation)
    roots = quadratic_x_intercepts(coeffs)
    vertex = quadratic_vertex(coeffs)
    y_intercept = (0.0, coeffs[2])
    warnings = []

    points = []
    polygon = []
    if mode == "parabola_xintercepts_vertex_triangle":
        if len(roots) < 2:
            warnings.append("triangle template needs two x-intercepts")
        else:
            points = [
                point_item("A", roots[0], 0),
                point_item("B", roots[-1], 0),
                point_item("C", vertex[0], vertex[1]),
            ]
            polygon = points
    elif mode == "parabola_xintercepts_yintercept_triangle":
        if len(roots) < 2:
            warnings.append("triangle template needs two x-intercepts")
        else:
            points = [
                point_item("A", roots[0], 0),
                point_item("B", roots[-1], 0),
                point_item("C", y_intercept[0], y_intercept[1]),
            ]
            polygon = points
    elif mode == "parabola_yintercept_vertex_xintercept_triangle":
        if not roots:
            warnings.append("triangle template needs an x-intercept")
        else:
            selector = str(spec.get("x_intercept", "positive")).strip().lower()
            selected = roots[-1]
            if selector in ("negative", "left"):
                selected = roots[0]
            elif selector in ("positive", "right"):
                selected = roots[-1]
            elif len(roots) == 1:
                selected = roots[0]
            points = [
                point_item("A", y_intercept[0], y_intercept[1]),
                point_item("B", vertex[0], vertex[1]),
                point_item("C", selected, 0),
            ]
            polygon = points
    else:
        show_x = str(spec.get("show_x_intercepts", "true")).lower() != "false"
        show_y = str(spec.get("show_y_intercept", "true")).lower() != "false"
        show_vertex = str(spec.get("show_vertex", "true")).lower() != "false"
        if show_x:
            if len(roots) == 1:
                points.append(point_item("A", roots[0], 0))
            elif len(roots) >= 2:
                points.extend([point_item("A", roots[0], 0), point_item("B", roots[-1], 0)])
        if show_y and abs(y_intercept[1]) > 1e-9:
            points.append(point_item("C", y_intercept[0], y_intercept[1]))
        if show_vertex:
            points.append(point_item("V", vertex[0], vertex[1]))

    xs_for_range = [vertex[0], 0] + roots + [point["x"] for point in points]
    if len(roots) >= 2:
        x_range = pad_range(min(xs_for_range), max(xs_for_range), 0.25)
    else:
        x_range = pad_range(vertex[0] - 3, vertex[0] + 3, 0.05)

    y_candidates = y_values_for_equations([equation], x_range)
    y_candidates.extend([0, y_intercept[1], vertex[1]])
    y_candidates.extend(point["y"] for point in points)
    y_range = pad_range(min(y_candidates), max(y_candidates), 0.18)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)

    x = np.linspace(x_range[0], x_range[1], 1200)
    y = safe_eval(equation["expr"], x)
    if np.isscalar(y):
        y = np.full_like(x, float(y))
    ax.plot(x, y, lw=lw(2), color="#1f77b4", zorder=3)

    if polygon:
        ax.fill([point["x"] for point in polygon], [point["y"] for point in polygon],
                color="#f4c7b8", alpha=0.55, zorder=2)
        ax.plot([point["x"] for point in polygon + [polygon[0]]],
                [point["y"] for point in polygon + [polygon[0]]],
                color="#8c5a4a", lw=lw(1.0), zorder=4)

    plot_labeled_points(ax, points)
    x_label_side, y_label_side = axis_label_sides_for_polygon(polygon)
    if mode in (
        "parabola_xintercepts_vertex_triangle",
        "parabola_xintercepts_yintercept_triangle",
        "parabola_yintercept_vertex_xintercept_triangle",
    ):
        x_label_side = "below"
    for value in roots:
        annotate_axis_value(ax, x_range, y_range, "x", value, x_label_side)
    vertex_shares_y_axis = abs(vertex[0]) < 1e-9 and abs(y_intercept[1] - vertex[1]) < 1e-9
    if abs(y_intercept[1]) > 1e-9 and not vertex_shares_y_axis:
        annotate_axis_value(ax, x_range, y_range, "y", y_intercept[1], y_label_side)
    if abs(vertex[0]) > 1e-9:
        annotate_axis_value(ax, x_range, y_range, "x", vertex[0], x_label_side)
    if abs(vertex[1]) > 1e-9:
        vertex_y_side = y_label_side
        if abs(vertex[0]) < 1e-9:
            vertex_y_side = "left_below" if y_label_side == "left" else "right_below"
        annotate_axis_value(ax, x_range, y_range, "y", vertex[1], vertex_y_side)

    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings


def render_two_origin_parabolas_horizontal_line(spec, output_path):
    eq1 = parse_y_equation(spec.get("equation1") or spec.get("equation_left") or "")
    eq2 = parse_y_equation(spec.get("equation2") or spec.get("equation_right") or "")
    horizontal_y = parse_number(spec.get("horizontal_y"), 4)
    warnings = []

    roots = []
    for equation in (eq1, eq2):
        candidates = positive_roots_for_y_level(equation, horizontal_y)
        if candidates:
            roots.append(max(candidates))
    roots = sorted(roots)
    if len(roots) < 2:
        warnings.append("horizontal-line template needs two positive intersections")

    points = [point_item("P", 0, horizontal_y)]
    if roots:
        points.append(point_item("Q", roots[0], horizontal_y))
    if len(roots) > 1:
        points.append(point_item("R", roots[-1], horizontal_y))

    x_hi = max([1.0] + roots) * 1.25
    x_range = (-0.4 * x_hi, x_hi)
    y_values = y_values_for_equations([eq1, eq2], x_range) + [0, horizontal_y]
    y_range = pad_range(min(y_values), max(y_values), 0.16)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    x = np.linspace(x_range[0], x_range[1], 1200)
    for equation in (eq1, eq2):
        ax.plot(x, safe_eval(equation["expr"], x), lw=lw(2), zorder=3)
    ax.axhline(horizontal_y, color="#777777", lw=lw(1.2), zorder=2)
    annotate_horizontal_line_label(ax, x_range, y_range, horizontal_y)
    plot_labeled_points(ax, points)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings


def render_two_origin_parabolas_vertical_line_ratio(spec, output_path):
    eq1 = parse_y_equation(spec.get("equation1") or "")
    eq2 = parse_y_equation(spec.get("equation2") or "")
    vertical_x = parse_number(spec.get("vertical_x"), 1)
    y1 = equation_value(eq1, vertical_x)
    y2 = equation_value(eq2, vertical_x)
    ordered = sorted([y1, y2])
    points = [
        point_item("A", vertical_x, 0),
        point_item("B", vertical_x, ordered[0]),
        point_item("C", vertical_x, ordered[-1]),
    ]

    x_range = pad_range(0, max(vertical_x * 1.6, 1.5), 0.15)
    y_values = y_values_for_equations([eq1, eq2], x_range) + [0, y1, y2]
    y_range = pad_range(min(y_values), max(y_values), 0.16)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    x = np.linspace(x_range[0], x_range[1], 1200)
    for equation in (eq1, eq2):
        ax.plot(x, safe_eval(equation["expr"], x), lw=lw(2), zorder=3)
    ax.axvline(vertical_x, color="#777777", lw=lw(1.2), zorder=2)
    annotate_axis_value(ax, x_range, y_range, "x", vertical_x, "below")
    for point in points:
        ax.scatter([point["x"]], [point["y"]], color="black", s=marker_area(28), zorder=6)
    ax.annotate("A", (vertical_x, 0), xytext=(6, -10), textcoords="offset points",
                ha="left", va="top", fontsize=fs(10), zorder=7)
    ax.annotate("B", (vertical_x, ordered[0]), xytext=(6, 4), textcoords="offset points",
                ha="left", va="bottom", fontsize=fs(10), zorder=7)
    ax.annotate("C", (vertical_x, ordered[-1]), xytext=(6, 4), textcoords="offset points",
                ha="left", va="bottom", fontsize=fs(10), zorder=7)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_two_parabolas_between_area(spec, output_path):
    eq1 = parse_y_equation(spec.get("equation1") or spec.get("equation_left") or "")
    eq2 = parse_y_equation(spec.get("equation2") or spec.get("equation_right") or "")
    a1, b1, c1 = quadratic_coefficients(eq1)
    a2, b2, c2 = quadratic_coefficients(eq2)
    roots = quadratic_roots_from_coeffs(a1 - a2, b1 - b2, c1 - c2)
    warnings = []
    if len(roots) < 2:
        warnings.append("between-area template needs two intersections")
        roots = roots or [-2, 2]
        if len(roots) == 1:
            roots = [roots[0] - 1, roots[0] + 1]
    left, right = roots[0], roots[-1]
    x_range = pad_range(left, right, 0.35)
    y_values = y_values_for_equations([eq1, eq2], x_range) + [0]
    y_range = pad_range(min(y_values), max(y_values), 0.18)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    x = np.linspace(x_range[0], x_range[1], 1200)
    y1 = safe_eval(eq1["expr"], x)
    y2 = safe_eval(eq2["expr"], x)
    ax.plot(x, y1, lw=lw(2), zorder=3)
    ax.plot(x, y2, lw=lw(2), zorder=3)
    x_fill = np.linspace(left, right, 500)
    ax.fill_between(x_fill, safe_eval(eq1["expr"], x_fill), safe_eval(eq2["expr"], x_fill),
                    color="#f4c7b8", alpha=0.55, zorder=2)
    points = [
        point_item("A", left, equation_value(eq1, left)),
        point_item("B", right, equation_value(eq1, right)),
    ]
    plot_labeled_points(ax, points)
    for value in roots[:2]:
        annotate_axis_value(ax, x_range, y_range, "x", value, "below")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings


def render_parabola_family_origin(spec, output_path):
    equations = [item for item in parse_equations(spec.get("equations") or spec.get("equation", "")) if item.get("kind") == "y"]
    labels = parse_labels(spec.get("curve_labels") or spec.get("labels", ""))
    if not equations:
        equations = [parse_y_equation("y=x^2")]
    x_range = parse_range(spec.get("x_range"), (-3, 3))
    y_values = y_values_for_equations(equations, x_range) + [0]
    y_range = pad_range(min(y_values), max(y_values), 0.12)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    x = np.linspace(x_range[0], x_range[1], 1200)
    label_x = x_range[1] * 0.72
    for index, equation in enumerate(equations):
        y = safe_eval(equation["expr"], x)
        ax.plot(x, y, lw=lw(1.7), zorder=3)
        label = labels[index] if index < len(labels) else chr(ord("a") + index)
        try:
            label_y = equation_value(equation, label_x)
            if y_range[0] <= label_y <= y_range[1]:
                ax.text(label_x, label_y, label, fontsize=fs(10), ha="left", va="center")
        except Exception:
            pass
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def parse_choice_equations(spec):
    raw = spec.get("choices") or spec.get("equations") or spec.get("equation") or ""
    if ";" in raw:
        items = split_semicolon_outside_parentheses(raw)
    else:
        items = split_csv_outside_parentheses(raw)
    equations = []
    warnings = []
    for index, item in enumerate(items, start=1):
        text = item.strip()
        text = re.sub(r"^(?:[①②③④⑤⑥⑦⑧⑨⑩]|\(?\d+\)?[.)]?)\s*", "", text).strip()
        if not text:
            continue
        try:
            equation = parse_y_equation(text)
            quadratic_coefficients(equation)
            equations.append(equation)
        except Exception as err:
            warnings.append(f"choice {index} equation error: {err}")
    return equations, warnings


def common_quadratic_choice_range(equations):
    x_candidates = [-3.0, 3.0, 0.0]
    y_candidates = [0.0]
    for equation in equations:
        try:
            coeffs = quadratic_coefficients(equation)
            roots = quadratic_x_intercepts(coeffs)
            vertex = quadratic_vertex(coeffs)
            x_candidates.extend(roots)
            x_candidates.append(vertex[0])
            y_candidates.append(vertex[1])
            if roots:
                y_candidates.append(0.0)
            if -10 < equation_value(equation, 0) < 10:
                y_candidates.append(equation_value(equation, 0))
        except Exception:
            pass

    x_range = pad_range(min(x_candidates), max(x_candidates), 0.16, 2.0)
    y_range = pad_range(min(y_candidates), max(y_candidates), 0.16, 2.0)
    if y_range[1] - y_range[0] < 4:
        center = (y_range[0] + y_range[1]) / 2
        y_range = (center - 2, center + 2)
    return x_range, y_range


def render_multiple_choice_parabola_position(spec, output_path):
    equations, warnings = parse_choice_equations(spec)
    if not equations:
        equations = [parse_y_equation("y=x^2"), parse_y_equation("y=-x^2")]
        warnings.append("multiple-choice template needs choices or equations")

    equations = equations[:5]
    labels = ["①", "②", "③", "④", "⑤"]
    x_range = parse_range(spec.get("x_range"), None)
    y_range = parse_range(spec.get("y_range"), None)
    if not x_range or not y_range:
        x_range, y_range = common_quadratic_choice_range(equations)

    fig, axes = plt.subplots(3, 2, figsize=CHOICE_FIGURE_SIZE_INCHES)
    axes_flat = list(axes.flatten())
    x = np.linspace(x_range[0], x_range[1], 600)

    for index, ax in enumerate(axes_flat):
        if index >= len(equations):
            ax.axis("off")
            continue
        setup_choice_axes(ax, x_range, y_range)
        equation = equations[index]
        try:
            y = safe_eval(equation["expr"], x)
            if np.isscalar(y):
                y = np.full_like(x, float(y))
            ax.plot(x, y, lw=lw(1.8), color="black", zorder=4)
        except Exception as err:
            warnings.append(f"choice {index + 1} render error: {err}")
        ax.text(0.04, 0.92, labels[index], transform=ax.transAxes,
                ha="left", va="top", fontsize=fs(9), zorder=8)

    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.02, top=0.98, wspace=0.18, hspace=0.28)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings


def should_auto_use_xintercepts_vertex_triangle(spec):
    if spec.get("template"):
        return False
    if str(spec.get("type", "coordinate_plane")).strip() not in ("", "coordinate_plane"):
        return False
    if spec.get("points"):
        return False
    labels = set(parse_labels(spec.get("labels", "")))
    region_text = str(spec.get("region", "")).lower()
    marker_text = " ".join([
        str(spec.get("labels", "")),
        str(spec.get("region", "")),
        str(spec.get("intersections", "")),
        str(spec.get("vertex", "")),
    ]).lower()
    if not ({"A", "B", "C"}.issubset(labels) or "triangle" in region_text or "abc" in marker_text):
        return False
    equations = [equation for equation in parse_equations(spec.get("equation", "")) if equation.get("kind") == "y"]
    if len(equations) != 1:
        return False
    try:
        roots = quadratic_x_intercepts(quadratic_coefficients(equations[0]))
        return len(roots) >= 2
    except Exception:
        return False


def shade_enclosed_region(ax, equations):
    y_equations = [equation for equation in equations if equation.get("kind") == "y"]
    verticals = sorted(equation["value"] for equation in equations if equation.get("kind") == "x")
    if len(y_equations) < 2 or len(verticals) < 2:
        return

    left = verticals[0]
    right = verticals[-1]
    if left == right:
        return
    x_fill = np.linspace(left, right, 400)
    try:
        y1 = safe_eval(y_equations[0]["expr"], x_fill)
        y2 = safe_eval(y_equations[1]["expr"], x_fill)
        if np.isscalar(y1):
            y1 = np.full_like(x_fill, float(y1))
        if np.isscalar(y2):
            y2 = np.full_like(x_fill, float(y2))
        ax.fill_between(x_fill, y1, y2, color="#b7d3ee", alpha=0.35, zorder=1)
    except Exception:
        return


def auto_focus_ranges(spec, equations, points, x_range, y_range):
    if StringFalse(spec.get("auto_focus", "")):
        return x_range, y_range

    verticals = [equation["value"] for equation in equations if equation.get("kind") == "x"]
    y_equations = [equation for equation in equations if equation.get("kind") == "y"]

    if len(verticals) >= 2:
        left = min(verticals)
        right = max(verticals)
        span = max(right - left, 1.0)
        current_span = max(x_range[1] - x_range[0], 1.0)
        if current_span > span * 2.4:
            x_lo = min(0, left - span * 0.35)
            x_hi = right + span * 0.35
            x_range = (x_lo, x_hi)
    elif points:
        xs = [point["x"] for point in points]
        point_span = max(max(xs) - min(xs), 1.0)
        current_span = max(x_range[1] - x_range[0], 1.0)
        if current_span > point_span * 2.8:
            x_range = pad_range(min([0] + xs), max(xs), 0.2)

    if y_equations:
        ys = y_values_for_equations(y_equations, x_range)
        ys.extend(point["y"] for point in points)
        if 0 >= min(ys or [0]) - 1 and 0 <= max(ys or [0]) + 1:
            ys.append(0)
        if ys:
            focused_y_range = pad_range(min(ys), max(ys), 0.12)
            focused_span = max(focused_y_range[1] - focused_y_range[0], 1.0)
            current_span = max(y_range[1] - y_range[0], 1.0)
            if current_span > focused_span * 1.4 or focused_span > current_span * 1.15:
                y_range = focused_y_range

    return x_range, y_range


def StringFalse(value):
    return str(value or "").strip().lower() in ("false", "no", "0", "off")


def render_coordinate_plane(spec, output_path):
    x_range = parse_range(spec.get("x_range"), (-6, 6))
    y_range = parse_range(spec.get("y_range"), (-10, 10))
    equations = parse_equations(spec.get("equation", ""))
    points = parse_points(spec.get("points", ""))
    labels = parse_labels(spec.get("labels", ""))
    x_range, y_range = auto_focus_ranges(spec, equations, points, x_range, y_range)
    x_range, y_range = expand_range_for_points(x_range, y_range, points)
    warnings = []
    if has_ambiguous_points(spec.get("points", "")):
        warnings.append("points field is ambiguous")

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)

    x = np.linspace(x_range[0], x_range[1], 1400)
    y_curves = []
    unsupported = []
    for equation in equations:
        if equation["kind"] == "y":
            try:
                y = safe_eval(equation["expr"], x)
                if np.isscalar(y):
                    y = np.full_like(x, float(y))
                ax.plot(x, y, lw=lw(2))
                y_curves.append((equation["raw"], y))
            except Exception as err:
                unsupported.append(f"{equation['raw']}: {err}")
        elif equation["kind"] == "x":
            ax.axvline(equation["value"], color="red", ls="--", lw=lw(1.8))
            annotate_axis_value(ax, x_range, y_range, "x", equation["value"])
        else:
            unsupported.append(f"{equation['raw']}: {equation.get('reason', 'unsupported')}")

    shade_enclosed_region(ax, equations)

    horizontal_lines = []
    for equation in equations:
        if equation["kind"] != "y":
            continue
        expr = equation.get("expr", "")
        if "x" not in expr:
            try:
                horizontal_value = float(safe_eval(expr, 0))
                horizontal_lines.append(horizontal_value)
                annotate_axis_value(ax, x_range, y_range, "y", horizontal_value)
            except Exception:
                pass

    for idx, point in enumerate(points):
        ax.scatter([point["x"]], [point["y"]], color="red", s=marker_area(36), zorder=5)
        label = point["label"] or (labels[idx] if idx < len(labels) else "")
        if label:
            if abs(point["x"]) < 1e-9 and abs(point["y"]) < 1e-9 and label != "O":
                ax.annotate(label, (point["x"], point["y"]), xytext=(-8, -12),
                            textcoords="offset points", ha="right", va="top",
                            fontsize=fs(11), zorder=6)
            else:
                ax.annotate(label, (point["x"], point["y"]), xytext=(4, 4),
                            textcoords="offset points", ha="left", va="bottom",
                            fontsize=fs(11), zorder=6)

    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings + unsupported


def render_parabola_band_area(spec, output_path):
    top_eq = parse_y_equation(spec.get("equation_top", ""))
    bottom_eq = parse_y_equation(spec.get("equation_bottom", ""))
    x_left = parse_number(spec.get("x_left"), 0)
    x_right = parse_number(spec.get("x_right"), x_left + 1)
    left = min(x_left, x_right)
    right = max(x_left, x_right)
    if left == right:
        right = left + 1

    mid = (left + right) / 2
    try:
        top_mid = float(safe_eval(top_eq["expr"], mid))
        bottom_mid = float(safe_eval(bottom_eq["expr"], mid))
        if top_mid < bottom_mid:
            top_eq, bottom_eq = bottom_eq, top_eq
    except Exception:
        pass

    span = max(right - left, 1.0)
    x_range = (min(0, left - span * 0.35), right + span * 0.35)
    y_candidates = y_values_for_equations([top_eq, bottom_eq], (left, right))
    y_candidates.append(0)
    y_range = pad_range(min(y_candidates), max(y_candidates), 0.22)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)

    x = np.linspace(x_range[0], x_range[1], 1200)
    x_fill = np.linspace(left, right, 500)
    warnings = []
    try:
        y_top = safe_eval(top_eq["expr"], x)
        y_bottom = safe_eval(bottom_eq["expr"], x)
        y_top_fill = safe_eval(top_eq["expr"], x_fill)
        y_bottom_fill = safe_eval(bottom_eq["expr"], x_fill)
        ax.fill_between(x_fill, y_top_fill, y_bottom_fill, color="#b7d3ee", alpha=0.35, zorder=1)
        ax.plot(x, y_top, lw=lw(2))
        ax.plot(x, y_bottom, lw=lw(2))
    except Exception as err:
        warnings.append("parabola_band_area equation error: " + str(err))

    for value in (left, right):
        # When a boundary is x=0, the y-axis already represents it.
        # Drawing a red dashed line there makes the diagram noisy and hides O.
        if abs(value) < 1e-9:
            continue
        ax.axvline(value, color="red", ls="--", lw=lw(1.8))
        annotate_axis_value(ax, x_range, y_range, "x", value)

    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings


def render_geometry(spec, output_path):
    coords = parse_points(spec.get("coordinates", "") or spec.get("points", ""))
    labels = parse_labels(spec.get("labels", ""))

    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    if coords:
        xs = [p["x"] for p in coords]
        ys = [p["y"] for p in coords]
        ax.plot(xs + [xs[0]], ys + [ys[0]], lw=lw(2))
        for idx, point in enumerate(coords):
            ax.scatter([point["x"]], [point["y"]], color="red", s=marker_area(36), zorder=5)
            label = point["label"] or (labels[idx] if idx < len(labels) else "")
            if label:
                ax.text(point["x"], point["y"], " " + label, fontsize=fs(11))
        margin = 1
        ax.set_xlim(min(xs) - margin, max(xs) + margin)
        ax.set_ylim(min(ys) - margin, max(ys) + margin)
    ax.grid(True, alpha=0.2)
    ax.set_aspect("equal", adjustable="box")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def parse_length(value, default=1.0):
    text = str(value or "").strip()
    if not text or re.search(r"[A-Za-z]", text):
        return float(default)
    return parse_number(text, default)


def setup_plain_geometry_axes(ax, width, height):
    margin = max(width, height) * 0.15
    ax.set_xlim(-margin, width + margin)
    ax.set_ylim(-margin, height + margin)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")


def draw_dimension(ax, start, end, text, offset=(0, 0)):
    x1, y1 = start
    x2, y2 = end
    ox, oy = offset
    ax.annotate("", xy=(x2 + ox, y2 + oy), xytext=(x1 + ox, y1 + oy),
                arrowprops=dict(arrowstyle="<->", lw=lw(0.9), color="dimgray"))
    ax.text((x1 + x2) / 2 + ox, (y1 + y2) / 2 + oy, text,
            ha="center", va="center", fontsize=fs(9),
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=fs(0.6)))


def render_rectangle_cross_road(spec, output_path, slanted=False, multi=False):
    width = parse_length(spec.get("width"), 40)
    height = parse_length(spec.get("height"), 30)
    road_width = parse_length(spec.get("road_width"), min(width, height) * 0.12)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="#efe1b0", edgecolor="black", lw=lw(1.2)))

    if slanted or multi:
        strips = [(-width * 0.15, width * 0.35, width * 0.55, width * 1.05)]
        if multi:
            strips.append((width * 0.65, width * 0.95, width * 1.15, width * 0.85))
        for x_bottom1, x_bottom2, x_top2, x_top1 in strips:
            ax.add_patch(plt.Polygon(
                [(x_bottom1, 0), (x_bottom2, 0), (x_top2, height), (x_top1, height)],
                closed=True, facecolor="white", edgecolor="#777777", lw=lw(1.0), alpha=0.95
            ))
        ax.add_patch(plt.Polygon(
            [(0, height * 0.45), (width, height * 0.65), (width, height * 0.65 + road_width), (0, height * 0.45 + road_width)],
            closed=True, facecolor="white", edgecolor="#777777", lw=lw(1.0), alpha=0.95
        ))
    else:
        cx = width * 0.5 - road_width / 2
        cy = height * 0.5 - road_width / 2
        ax.add_patch(plt.Rectangle((cx, 0), road_width, height, facecolor="white", edgecolor="#777777", lw=lw(1.0)))
        ax.add_patch(plt.Rectangle((0, cy), width, road_width, facecolor="white", edgecolor="#777777", lw=lw(1.0)))

    draw_dimension(ax, (0, height), (width, height), format_number(width) + " m", (0, height * 0.09))
    draw_dimension(ax, (0, 0), (0, height), format_number(height) + " m", (-width * 0.08, 0))
    if str(spec.get("road_width", "")).strip():
        ax.text(width * 0.82, height * 0.12, str(spec.get("road_width")).strip(),
                fontsize=fs(9), ha="center", va="center")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_rectangular_park_border(spec, output_path):
    inner_width = parse_length(spec.get("inner_width"), 30)
    inner_height = parse_length(spec.get("inner_height"), 20)
    border = parse_length(spec.get("border_width"), 6)
    outer_width = inner_width + 2 * border
    outer_height = inner_height + 2 * border
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, outer_width, outer_height)
    ax.add_patch(plt.Rectangle((0, 0), outer_width, outer_height, facecolor="#c7b08a", edgecolor="black", lw=lw(1.2)))
    ax.add_patch(plt.Rectangle((border, border), inner_width, inner_height, facecolor="#cde8c6", edgecolor="black", lw=lw(1.0)))
    draw_dimension(ax, (border, border + inner_height), (border + inner_width, border + inner_height),
                   format_number(inner_width) or str(spec.get("inner_width") or ""), (0, border * 0.45))
    draw_dimension(ax, (border, border), (border, border + inner_height),
                   format_number(inner_height) or str(spec.get("inner_height") or ""), (-border * 0.45, 0))
    ax.text(outer_width - border / 2, outer_height / 2, format_number(border) + " m", fontsize=fs(8), rotation=90,
            ha="center", va="center", bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=fs(0.6)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_two_squares_on_segment(spec, output_path):
    total = parse_length(spec.get("total_length"), 11)
    left_side = total * 0.62
    right_side = total - left_side
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, total, max(left_side, right_side))
    ax.add_patch(plt.Rectangle((0, 0), left_side, left_side, facecolor="#f1d36f", edgecolor="black", lw=lw(1.1)))
    ax.add_patch(plt.Rectangle((left_side, 0), right_side, right_side, facecolor="#f1d36f", edgecolor="black", lw=lw(1.1)))
    ax.text(0, -total * 0.08, "A", fontsize=fs(10), ha="center", va="top")
    ax.text(left_side, -total * 0.08, "C", fontsize=fs(10), ha="center", va="top")
    ax.text(total, -total * 0.08, "B", fontsize=fs(10), ha="center", va="top")
    draw_dimension(ax, (0, 0), (total, 0), format_number(total) + " cm", (0, -total * 0.16))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_open_box_net(spec, output_path, rectangular=False):
    paper_side = spec.get("paper_side", "")
    paper_width = parse_length(spec.get("paper_width") or paper_side, 10 if not rectangular else 14)
    paper_height = parse_length(spec.get("paper_height") or paper_side, 10)
    cut = parse_length(spec.get("cut_side"), 2)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, paper_width, paper_height)
    ax.add_patch(plt.Rectangle((0, 0), paper_width, paper_height, facecolor="#d9ecff", edgecolor="black", lw=lw(1.1)))
    corners = [(0, 0), (paper_width - cut, 0), (0, paper_height - cut), (paper_width - cut, paper_height - cut)]
    for x, y in corners:
        ax.add_patch(plt.Rectangle((x, y), cut, cut, facecolor="white", edgecolor="#888888", lw=lw(0.9), ls="--"))
    ax.plot([cut, cut], [0, paper_height], color="#888888", lw=lw(0.8), ls="--")
    ax.plot([paper_width - cut, paper_width - cut], [0, paper_height], color="#888888", lw=lw(0.8), ls="--")
    ax.plot([0, paper_width], [cut, cut], color="#888888", lw=lw(0.8), ls="--")
    ax.plot([0, paper_width], [paper_height - cut, paper_height - cut], color="#888888", lw=lw(0.8), ls="--")
    draw_dimension(ax, (0, paper_height), (paper_width, paper_height), format_number(paper_width) + " cm", (0, paper_height * 0.1))
    ax.text(paper_width + paper_width * 0.06, cut / 2, format_number(cut) + " cm", fontsize=fs(8), ha="left", va="center")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def build_output_path(input_path, output_dir, index):
    return output_dir / f"{input_path.stem}_이미지{index}.PNG"


def render_block(index, block, input_path, output_dir):
    spec = parse_key_values(block)
    kind = spec.get("type", "coordinate_plane")
    template = spec.get("template", "")
    output_path = build_output_path(input_path, output_dir, index)
    if template == "parabola_band_area":
        unsupported = render_parabola_band_area(spec, output_path)
    elif template == "two_origin_parabolas_horizontal_line":
        unsupported = render_two_origin_parabolas_horizontal_line(spec, output_path)
    elif template == "two_origin_parabolas_vertical_line_ratio":
        unsupported = render_two_origin_parabolas_vertical_line_ratio(spec, output_path)
    elif template == "two_parabolas_between_area":
        unsupported = render_two_parabolas_between_area(spec, output_path)
    elif template == "parabola_family_origin":
        unsupported = render_parabola_family_origin(spec, output_path)
    elif template == "multiple_choice_parabola_position":
        unsupported = render_multiple_choice_parabola_position(spec, output_path)
    elif template == "rectangle_cross_road":
        unsupported = render_rectangle_cross_road(spec, output_path)
    elif template == "rectangle_slanted_cross_road":
        unsupported = render_rectangle_cross_road(spec, output_path, slanted=True)
    elif template == "rectangle_multi_slanted_roads":
        unsupported = render_rectangle_cross_road(spec, output_path, slanted=True, multi=True)
    elif template == "rectangular_park_border":
        unsupported = render_rectangular_park_border(spec, output_path)
    elif template in ("two_squares_on_segment", "two_squares_from_segment"):
        unsupported = render_two_squares_on_segment(spec, output_path)
    elif template == "open_box_net_equal_cuts":
        unsupported = render_open_box_net(spec, output_path)
    elif template == "open_box_net_rectangular_paper":
        unsupported = render_open_box_net(spec, output_path, rectangular=True)
    elif template in (
        "parabola_basic_shape",
        "parabola_xintercepts_vertex_triangle",
        "parabola_x_intercepts_vertex_triangle",
        "parabola_xintercepts_yintercept_triangle",
        "parabola_x_intercepts_y_intercept_triangle",
        "parabola_yintercept_vertex_xintercept_triangle",
        "parabola_y_intercept_vertex_x_intercept_triangle",
    ):
        normalized_template = {
            "parabola_x_intercepts_vertex_triangle": "parabola_xintercepts_vertex_triangle",
            "parabola_x_intercepts_y_intercept_triangle": "parabola_xintercepts_yintercept_triangle",
            "parabola_y_intercept_vertex_x_intercept_triangle": "parabola_yintercept_vertex_xintercept_triangle",
        }.get(template, template)
        unsupported = render_parabola_calculated_template(spec, output_path, normalized_template)
    elif kind == "geometry":
        unsupported = render_geometry(spec, output_path)
    elif should_auto_use_xintercepts_vertex_triangle(spec):
        unsupported = render_parabola_calculated_template(spec, output_path, "parabola_xintercepts_vertex_triangle")
    else:
        unsupported = render_coordinate_plane(spec, output_path)
    return output_path, unsupported, spec


def main():
    parser = argparse.ArgumentParser(description="Render IMAGE_PROMPT math diagrams to PNG.")
    parser.add_argument("input", nargs="?", help="Text file containing [IMAGE_PROMPT: ...] blocks.")
    parser.add_argument("-o", "--output-dir", default="", help="Output directory. Defaults to the input file folder.")
    args = parser.parse_args()

    if not args.input:
        input_text = select_input_file()
        if not input_text:
            print("입력 파일이 없습니다.")
            input("Enter 키를 누르면 종료합니다.")
            return
        args.input = input_text

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"입력 파일을 찾지 못했습니다: {input_path}")
        input("Enter 키를 누르면 종료합니다.")
        return

    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    text = input_path.read_text(encoding="utf-8")
    blocks = extract_image_prompt_blocks(text)
    if not blocks:
        print("No IMAGE_PROMPT blocks found.")
        return

    for index, block in blocks:
        output_path, unsupported, spec = render_block(index, block, input_path, output_dir)
        print(f"[{index}] {output_path.resolve()}")
        if unsupported:
            print("  warnings:")
            for item in unsupported:
                print(f"  - {item}")

    print("")
    print(f"완료: {len(blocks)}개 이미지 생성")
    print(f"저장 위치: {output_dir.resolve()}")


def select_input_file():
    if sys.platform.startswith("win"):
        script = "\n".join([
            "Add-Type -AssemblyName System.Windows.Forms",
            "$dialog = New-Object System.Windows.Forms.OpenFileDialog",
            "$dialog.Title = '쌍둥이문항 텍스트 파일을 선택하세요'",
            "$dialog.Filter = '텍스트 파일 (*.txt)|*.txt|모든 파일 (*.*)|*.*'",
            "$dialog.Multiselect = $false",
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {",
            "  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "  Write-Output $dialog.FileName",
            "}",
        ])
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        selected = completed.stdout.strip()
        return selected.splitlines()[-1].strip() if selected else ""

    return input("텍스트 파일 경로를 입력하세요: ").strip().strip('"')


if __name__ == "__main__":
    main()
