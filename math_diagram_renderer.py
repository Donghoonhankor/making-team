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
    x_pad = (x_range[1] - x_range[0]) * 0.06
    ax.set_xlim(x_range[0], x_range[1] + x_pad)
    ax.set_ylim(*y_range)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

    xmin, xmax = ax.get_xlim()
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
    x_pos = xmin + (xmax - xmin) * 0.62
    ax.annotate("y = " + text, (x_pos, y_value), xytext=(0, -5),
                textcoords="offset points", ha="center", va="top",
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


def steepen_parabola_view(x_range, y_range, strength=0.72):
    x_span = max(float(x_range[1] - x_range[0]), 1.0)
    y_span = max(float(y_range[1] - y_range[0]), 1.0)
    target_span = max(3.0, x_span * strength)
    # Never crop aggressively: workbook-style graphs should look more upright,
    # but all key intercepts/vertices still need to remain visible.
    if y_span <= target_span or target_span < y_span * 0.92:
        return y_range
    center = (y_range[0] + y_range[1]) / 2
    return center - target_span / 2, center + target_span / 2


def widen_x_for_parabola_style(x_range, amount=0.18):
    xmin, xmax = x_range
    span = max(xmax - xmin, 1.0)
    pad = span * amount
    return xmin - pad, xmax + pad


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


def linear_coefficients(raw):
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty line equation")
    if text.startswith("y=") or text.startswith("y ="):
        expr = normalize_expr(text.split("=", 1)[1])
        m = parse_number(safe_eval(expr, 1) - safe_eval(expr, 0), 0)
        b = parse_number(safe_eval(expr, 0), 0)
        return m, -1.0, b
    if text.startswith("x=") or text.startswith("x ="):
        value = parse_number(text.split("=", 1)[1], 0)
        return 1.0, 0.0, -value
    if "=" in text:
        left, right = text.split("=", 1)
        expr = normalize_expr(f"({left})-({right})")
    else:
        expr = normalize_expr(text)
    f00 = float(eval(expr, {"__builtins__": {}}, {"x": 0, "y": 0, **SAFE_FUNCS}))
    fx1 = float(eval(expr, {"__builtins__": {}}, {"x": 1, "y": 0, **SAFE_FUNCS}))
    fy1 = float(eval(expr, {"__builtins__": {}}, {"x": 0, "y": 1, **SAFE_FUNCS}))
    a = fx1 - f00
    b = fy1 - f00
    c = f00
    if abs(a) < 1e-9 and abs(b) < 1e-9:
        raise ValueError("not a line equation")
    return a, b, c


def parse_line_equation(raw):
    a, b, c = linear_coefficients(raw)
    return {"raw": str(raw or "").strip(), "a": a, "b": b, "c": c}


def parse_line_equations(value):
    items = split_semicolon_outside_parentheses(value or "")
    if len(items) <= 1:
        items = split_csv_outside_parentheses(value or "")
    return [parse_line_equation(item) for item in items if item.strip()]


def line_y(line, x_value):
    if abs(line["b"]) < 1e-9:
        return None
    return -(line["a"] * x_value + line["c"]) / line["b"]


def line_x_at_y(line, y_value):
    if abs(line["a"]) < 1e-9:
        return None
    return -(line["b"] * y_value + line["c"]) / line["a"]


def line_intersection(line1, line2):
    det = line1["a"] * line2["b"] - line2["a"] * line1["b"]
    if abs(det) < 1e-9:
        return None
    x = (line1["b"] * line2["c"] - line2["b"] * line1["c"]) / det
    y = (line1["c"] * line2["a"] - line2["c"] * line1["a"]) / det
    return x, y


def line_axis_intercepts(line):
    points = []
    x0 = line_x_at_y(line, 0)
    y0 = line_y(line, 0)
    if x0 is not None:
        points.append(point_item("A", x0, 0))
    if y0 is not None:
        points.append(point_item("B", 0, y0))
    return points


def line_sample_y_values(lines, x_range):
    xs = np.linspace(x_range[0], x_range[1], 300)
    values = []
    for line in lines:
        if abs(line["b"]) < 1e-9:
            continue
        ys = [line_y(line, x) for x in xs]
        finite = [y for y in ys if y is not None and math.isfinite(y)]
        if finite:
            values.extend([min(finite), max(finite)])
    return values


def parse_named_points(value):
    points = parse_points(value)
    return points


def render_linear_scene(output_path, lines, points=None, polygons=None, guides=True, labels=True,
                        x_candidates=None, y_candidates=None, shade_color="#cfe8d2", extra_draw=None):
    points = points or []
    polygons = polygons or []
    x_candidates = list(x_candidates or [0])
    y_candidates = list(y_candidates or [0])
    x_candidates.extend(point["x"] for point in points)
    y_candidates.extend(point["y"] for point in points)
    for line in lines:
        x0 = line_x_at_y(line, 0)
        y0 = line_y(line, 0)
        if x0 is not None:
            x_candidates.append(x0)
        if y0 is not None:
            y_candidates.append(y0)
    if not x_candidates:
        x_candidates = [-3, 3]
    x_range = pad_range(min(x_candidates), max(x_candidates), 0.22, 4.0)
    sampled = line_sample_y_values(lines, x_range)
    y_candidates.extend(sampled)
    if not y_candidates:
        y_candidates = [-3, 3]
    y_range = pad_range(min(y_candidates), max(y_candidates), 0.20, 4.0)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    for polygon in polygons:
        ax.fill([point["x"] for point in polygon], [point["y"] for point in polygon],
                color=shade_color, alpha=0.65, zorder=1)
        ax.plot([point["x"] for point in polygon + [polygon[0]]],
                [point["y"] for point in polygon + [polygon[0]]],
                color="#6d7f70", lw=lw(1.0), zorder=3)

    for line in lines:
        if abs(line["b"]) < 1e-9:
            x_value = -line["c"] / line["a"]
            ax.axvline(x_value, color="black", lw=lw(1.5), zorder=4)
        else:
            xs = np.linspace(x_range[0], x_range[1], 600)
            ys = [line_y(line, x) for x in xs]
            ax.plot(xs, ys, color="black", lw=lw(1.5), zorder=4)

    if extra_draw:
        extra_draw(ax, x_range, y_range)

    if guides:
        for point in points:
            if abs(point["x"]) > 1e-9 and y_range[0] <= 0 <= y_range[1]:
                ax.plot([point["x"], point["x"]], [0, point["y"]], color="#777777", ls="--", lw=lw(0.9), zorder=2)
                annotate_axis_value(ax, x_range, y_range, "x", point["x"], "below")
            if abs(point["y"]) > 1e-9 and x_range[0] <= 0 <= x_range[1]:
                ax.plot([0, point["x"]], [point["y"], point["y"]], color="#777777", ls="--", lw=lw(0.9), zorder=2)
                annotate_axis_value(ax, x_range, y_range, "y", point["y"], "left")

    if labels:
        plot_labeled_points(ax, points)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


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


def roots_for_y_level(equation, y_value):
    a, b, c = quadratic_coefficients(equation)
    return quadratic_roots_from_coeffs(a, b, c - y_value)


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


def render_quadratic_scene(output_path, equations, points=None, polygons=None, extra_draw=None, x_candidates=None, y_candidates=None):
    points = points or []
    polygons = polygons or []
    x_candidates = list(x_candidates or [])
    y_candidates = list(y_candidates or [])
    for equation in equations:
        try:
            coeffs = quadratic_coefficients(equation)
            roots = quadratic_x_intercepts(coeffs)
            vertex = quadratic_vertex(coeffs)
            x_candidates.extend(roots + [vertex[0], 0])
            y_candidates.extend([0, vertex[1], equation_value(equation, 0)])
        except Exception:
            x_candidates.extend([-3, 3, 0])
            y_candidates.append(0)
    x_candidates.extend(point["x"] for point in points)
    y_candidates.extend(point["y"] for point in points)
    if not x_candidates:
        x_candidates = [-3, 3]
    if not y_candidates:
        y_candidates = [-3, 3]
    x_range = pad_range(min(x_candidates), max(x_candidates), 0.24, 2.0)
    x_range = widen_x_for_parabola_style(x_range, 0.10)
    sampled_y = y_values_for_equations(equations, x_range)
    if sampled_y:
        y_candidates.extend(sampled_y)
    y_range = pad_range(min(y_candidates), max(y_candidates), 0.18, 2.0)
    y_range = steepen_parabola_view(x_range, y_range, 0.78)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    x = np.linspace(x_range[0], x_range[1], 1200)
    for equation in equations:
        y = safe_eval(equation["expr"], x)
        if np.isscalar(y):
            y = np.full_like(x, float(y))
        ax.plot(x, y, lw=lw(2), zorder=3)
    for polygon in polygons:
        ax.fill([point["x"] for point in polygon], [point["y"] for point in polygon],
                color="#f4c7b8", alpha=0.5, zorder=2)
        ax.plot([point["x"] for point in polygon + [polygon[0]]],
                [point["y"] for point in polygon + [polygon[0]]],
                color="#8c5a4a", lw=lw(1.0), zorder=4)
    if extra_draw:
        extra_draw(ax, x_range, y_range)
    plot_labeled_points(ax, points)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


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
    x_range = widen_x_for_parabola_style(x_range, 0.12)

    y_candidates = y_values_for_equations([equation], x_range)
    y_candidates.extend([0, y_intercept[1], vertex[1]])
    y_candidates.extend(point["y"] for point in points)
    y_range = pad_range(min(y_candidates), max(y_candidates), 0.18)
    y_range = steepen_parabola_view(x_range, y_range, 0.72)

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

    if mode == "parabola_basic_shape" and any(point.get("label") == "V" for point in points):
        vx, vy = vertex
        if y_range[0] <= 0 <= y_range[1] and x_range[0] <= vx <= x_range[1] and abs(vy) > 1e-9:
            ax.plot([vx, vx], [0, vy], color="#777777", lw=lw(0.9), ls="--", zorder=2.5)
        if x_range[0] <= 0 <= x_range[1] and y_range[0] <= vy <= y_range[1] and abs(vx) > 1e-9:
            ax.plot([0, vx], [vy, vy], color="#777777", lw=lw(0.9), ls="--", zorder=2.5)

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
    x_range = widen_x_for_parabola_style(x_range, 0.10)
    y_values = y_values_for_equations([eq1, eq2], x_range) + [0, horizontal_y]
    y_range = pad_range(min(y_values), max(y_values), 0.16)
    y_range = steepen_parabola_view(x_range, y_range, 0.82)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    x = np.linspace(x_range[0], x_range[1], 1200)
    for equation in (eq1, eq2):
        ax.plot(x, safe_eval(equation["expr"], x), lw=lw(2), zorder=3)
    ax.axhline(horizontal_y, color="#777777", lw=lw(1.2), zorder=2)
    annotate_horizontal_line_label(ax, x_range, y_range, horizontal_y)
    for point in points:
        ax.scatter([point["x"]], [point["y"]], color="black", s=marker_area(28), zorder=6)
        ax.annotate(point["label"], (point["x"], point["y"]), xytext=(2, 2),
                    textcoords="offset points", ha="left", va="bottom",
                    fontsize=fs(10), zorder=7)
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
    x_range = widen_x_for_parabola_style(x_range, 0.10)
    y_values = y_values_for_equations([eq1, eq2], x_range) + [0, y1, y2]
    y_range = pad_range(min(y_values), max(y_values), 0.16)
    y_range = steepen_parabola_view(x_range, y_range, 0.82)

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
    x_range = widen_x_for_parabola_style(x_range, 0.10)
    y_values = y_values_for_equations([eq1, eq2], x_range) + [0]
    y_range = pad_range(min(y_values), max(y_values), 0.18)
    y_range = steepen_parabola_view(x_range, y_range, 0.78)

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
    x_range = widen_x_for_parabola_style(x_range, 0.10)
    y_values = y_values_for_equations(equations, x_range) + [0]
    y_range = pad_range(min(y_values), max(y_values), 0.12)
    y_range = steepen_parabola_view(x_range, y_range, 0.78)

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
    x_range = widen_x_for_parabola_style(x_range, 0.08)
    y_range = pad_range(min(y_candidates), max(y_candidates), 0.16, 2.0)
    y_range = steepen_parabola_view(x_range, y_range, 0.70)
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


def render_parabola_shift_from_base(spec, output_path):
    base_eq = parse_y_equation(spec.get("base_equation") or spec.get("equation") or "y=x^2")
    shift_x = parse_number(spec.get("shift_x"), 0)
    shift_y = parse_number(spec.get("shift_y"), 0)
    shifted_eq = {
        "raw": f"shifted {base_eq['raw']}",
        "kind": "y",
        "expr": f"({base_eq['expr'].replace('x', '(x-(' + str(shift_x) + '))')})+({shift_y})",
    }

    def extra(ax, x_range, y_range):
        x = np.linspace(x_range[0], x_range[1], 1200)
        y = safe_eval(base_eq["expr"], x)
        if np.isscalar(y):
            y = np.full_like(x, float(y))
        ax.plot(x, y, color="#777777", lw=lw(1.4), ls="--", zorder=2)
        ax.annotate("", xy=(shift_x, shift_y), xytext=(0, 0),
                    arrowprops=dict(arrowstyle="->", lw=lw(1.0), color="#555555"), zorder=5)
        if abs(shift_x) > 1e-9:
            annotate_axis_value(ax, x_range, y_range, "x", shift_x, "below")
        if abs(shift_y) > 1e-9:
            annotate_axis_value(ax, x_range, y_range, "y", shift_y, "left")

    points = [point_item("O'", shift_x, shift_y)] if abs(shift_x) > 1e-9 or abs(shift_y) > 1e-9 else []
    return render_quadratic_scene(output_path, [shifted_eq], points=points, extra_draw=extra,
                                  x_candidates=[-3 + shift_x, 3 + shift_x, 0],
                                  y_candidates=[shift_y, 0])


def render_two_parabolas_same_width_horizontal_chord(spec, output_path):
    eq_left = parse_y_equation(spec.get("equation_left") or spec.get("equation1") or "y=(x+2)^2")
    eq_right = parse_y_equation(spec.get("equation_right") or spec.get("equation2") or "y=(x-2)^2")
    chord_y = spec.get("chord_y")
    if chord_y in (None, ""):
        vertices = [quadratic_vertex(quadratic_coefficients(eq_left)), quadratic_vertex(quadratic_coefficients(eq_right))]
        chord_y = max(vertices[0][1], vertices[1][1]) + 2
    chord_y = parse_number(chord_y, 2)
    left_roots = roots_for_y_level(eq_left, chord_y)
    right_roots = roots_for_y_level(eq_right, chord_y)
    warnings = []
    if not left_roots or not right_roots:
        warnings.append("horizontal-chord template needs intersections at chord_y")
        left_x, right_x = -1, 1
    else:
        left_x = min(left_roots)
        right_x = max(right_roots)
    points = [point_item("A", left_x, chord_y), point_item("B", right_x, chord_y)]

    def extra(ax, x_range, y_range):
        ax.plot([left_x, right_x], [chord_y, chord_y], color="#8c5a4a", lw=lw(1.3), zorder=4)
        annotate_horizontal_line_label(ax, x_range, y_range, chord_y)

    return warnings + render_quadratic_scene(output_path, [eq_left, eq_right], points=points, extra_draw=extra,
                                             x_candidates=[left_x, right_x, 0], y_candidates=[chord_y, 0])


def render_two_origin_parabolas_parallelogram(spec, output_path):
    eq1 = parse_y_equation(spec.get("equation1") or "y=1/3*x^2")
    eq2 = parse_y_equation(spec.get("equation2") or "y=x^2")
    vertical_x = parse_number(spec.get("vertical_x"), 3)
    y1 = equation_value(eq1, vertical_x)
    y2 = equation_value(eq2, vertical_x)
    low, high = sorted([y1, y2])
    height = high - low
    points = [
        point_item("A", 0, height),
        point_item("B", vertical_x, high),
        point_item("C", vertical_x, low),
        point_item("D", 0, 0),
    ]

    def extra(ax, x_range, y_range):
        ax.plot([vertical_x, vertical_x], [low, high], color="#777777", lw=lw(1.0), zorder=2)
        annotate_axis_value(ax, x_range, y_range, "x", vertical_x, "below")

    return render_quadratic_scene(output_path, [eq1, eq2], points=points, polygons=[points], extra_draw=extra,
                                  x_candidates=[0, vertical_x], y_candidates=[0, low, high, height])


def render_parabola_diamond_on_axes(spec, output_path):
    equation = parse_y_equation(spec.get("equation") or "y=1/4*x^2")
    a, b, c = quadratic_coefficients(equation)
    root = parse_number(spec.get("point_x"), 1 / abs(a) if abs(a) > 1e-9 else 4)
    y_value = equation_value(equation, root)
    points = [
        point_item("A", -root, y_value),
        point_item("O", 0, 0),
        point_item("B", root, y_value),
        point_item("C", 0, y_value * 2),
    ]
    return render_quadratic_scene(output_path, [equation], points=points, polygons=[points],
                                  x_candidates=[-root, root, 0], y_candidates=[0, y_value * 2])


def render_two_parabolas_square(spec, output_path):
    eq_top = parse_y_equation(spec.get("equation_top") or spec.get("equation1") or "y=x^2")
    eq_bottom = parse_y_equation(spec.get("equation_bottom") or spec.get("equation2") or "y=-1/2*x^2")
    x_value = parse_number(spec.get("point_x"), 2)
    y_top = equation_value(eq_top, x_value)
    y_bottom = equation_value(eq_bottom, x_value)
    points = [
        point_item("A", -x_value, y_top),
        point_item("B", x_value, y_top),
        point_item("C", x_value, y_bottom),
        point_item("D", -x_value, y_bottom),
    ]
    return render_quadratic_scene(output_path, [eq_top, eq_bottom], points=points, polygons=[points],
                                  x_candidates=[-x_value, x_value, 0], y_candidates=[y_top, y_bottom, 0])


def render_two_parabolas_shared_vertex_intersections(spec, output_path):
    eq1 = parse_y_equation(spec.get("equation1") or "y=x^2-9")
    eq2 = parse_y_equation(spec.get("equation2") or "y=-(x-3)^2")
    coeff1 = quadratic_coefficients(eq1)
    coeff2 = quadratic_coefficients(eq2)
    roots = quadratic_roots_from_coeffs(coeff1[0] - coeff2[0], coeff1[1] - coeff2[1], coeff1[2] - coeff2[2])
    warnings = []
    if len(roots) < 2:
        warnings.append("shared-vertex template needs two intersections")
    points = [
        point_item("A", *quadratic_vertex(coeff1)),
        point_item("B", *quadratic_vertex(coeff2)),
    ]
    for index, root in enumerate(roots[:2]):
        points.append(point_item(chr(ord("C") + index), root, equation_value(eq1, root)))
    return warnings + render_quadratic_scene(output_path, [eq1, eq2], points=points,
                                             x_candidates=roots + [0], y_candidates=[0])


def render_line_to_parabola_quadrant_match(spec, output_path):
    line_eq = parse_y_equation(spec.get("line_equation") or "y=x+1")
    parabola_eq = parse_y_equation(spec.get("parabola_equation") or spec.get("parabola_form") or "y=-(x+1)^2+1")
    x_range = parse_range(spec.get("x_range"), (-4, 4))
    y_values = y_values_for_equations([line_eq, parabola_eq], x_range) + [0]
    y_range = pad_range(min(y_values), max(y_values), 0.18, 2.0)
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    x = np.linspace(x_range[0], x_range[1], 1200)
    ax.plot(x, safe_eval(line_eq["expr"], x), color="#555555", lw=lw(1.5), zorder=3)
    ax.plot(x, safe_eval(parabola_eq["expr"], x), color="#1f77b4", lw=lw(2), zorder=3)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def quadratic_line_intersections(equation, line):
    qa, qb, qc = quadratic_coefficients(equation)
    if abs(line["b"]) < 1e-9:
        x = line_x_at_y(line, 0)
        if x is None:
            return []
        return [(x, equation_value(equation, x))]
    slope = -line["a"] / line["b"]
    intercept = -line["c"] / line["b"]
    roots = quadratic_roots_from_coeffs(qa, qb - slope, qc - intercept)
    return [(x, slope * x + intercept) for x in roots]


def draw_guides_to_axes(ax, points, x_to_axis=True, y_to_axis=False):
    for point in points:
        x, y = point["x"], point["y"]
        if x_to_axis and abs(y) > 1e-9:
            ax.plot([x, x], [0, y], color="#777777", lw=lw(0.8), ls="--", zorder=2.5)
        if y_to_axis and abs(x) > 1e-9:
            ax.plot([0, x], [y, y], color="#777777", lw=lw(0.8), ls="--", zorder=2.5)


def render_parabola_vertex_yintercept_origin_triangle(spec, output_path):
    equation = parse_y_equation(spec.get("equation") or "y=-x^2+4*x+5")
    coeffs = quadratic_coefficients(equation)
    vertex = quadratic_vertex(coeffs)
    y_intercept = coeffs[2]
    points = [
        point_item("A", vertex[0], vertex[1]),
        point_item("B", 0, y_intercept),
        point_item("O", 0, 0),
    ]

    def extra(ax, x_range, y_range):
        draw_guides_to_axes(ax, [points[0]], x_to_axis=True, y_to_axis=True)

    return render_quadratic_scene(output_path, [equation], points=points, polygons=[points], extra_draw=extra,
                                  x_candidates=[vertex[0], 0], y_candidates=[vertex[1], y_intercept, 0])


def render_parabola_xintercepts_vertex_yintercept_quadrilateral(spec, output_path):
    equation = parse_y_equation(spec.get("equation") or "y=-x^2+2*x+3")
    coeffs = quadratic_coefficients(equation)
    roots = quadratic_x_intercepts(coeffs)
    vertex = quadratic_vertex(coeffs)
    warnings = []
    if len(roots) < 2:
        warnings.append("quadrilateral template needs two x-intercepts")
        roots = roots or [-1, 1]
        if len(roots) == 1:
            roots = [roots[0] - 1, roots[0] + 1]
    y_intercept = coeffs[2]
    points = [
        point_item("A", roots[0], 0),
        point_item("B", roots[-1], 0),
        point_item("C", vertex[0], vertex[1]),
        point_item("D", 0, y_intercept),
    ]
    polygon = [points[0], points[2], points[1], points[3]]
    return warnings + render_quadratic_scene(output_path, [equation], points=points, polygons=[polygon],
                                             x_candidates=roots + [vertex[0], 0],
                                             y_candidates=[0, vertex[1], y_intercept])


def render_parabola_yaxis_xpositive_parallelogram(spec, output_path):
    equation = parse_y_equation(spec.get("equation") or "y=-x^2+4*x+5")
    coeffs = quadratic_coefficients(equation)
    roots = [root for root in quadratic_x_intercepts(coeffs) if root >= -1e-9]
    right_root = max(roots) if roots else 3
    y_intercept = coeffs[2]
    vertex = quadratic_vertex(coeffs)
    points = [
        point_item("O", 0, 0),
        point_item("A", 0, y_intercept),
        point_item("B", vertex[0], vertex[1]),
        point_item("C", right_root, 0),
    ]
    return render_quadratic_scene(output_path, [equation], points=points, polygons=[points],
                                  x_candidates=[0, vertex[0], right_root], y_candidates=[0, y_intercept, vertex[1]])


def render_parabola_point_xaxis_triangle(spec, output_path):
    equation = parse_y_equation(spec.get("equation") or "y=1/2*x^2")
    point_x = parse_number(spec.get("point_x"), 3)
    base_x = parse_number(spec.get("base_x"), 4)
    point_y = equation_value(equation, point_x)
    points = [
        point_item("O", 0, 0),
        point_item("A", base_x, 0),
        point_item("P", point_x, point_y),
    ]

    def extra(ax, x_range, y_range):
        draw_guides_to_axes(ax, [points[2]], x_to_axis=True, y_to_axis=False)
        if abs(base_x) > 1e-9:
            annotate_axis_value(ax, x_range, y_range, "x", base_x, "below")

    return render_quadratic_scene(output_path, [equation], points=points, polygons=[points], extra_draw=extra,
                                  x_candidates=[0, base_x, point_x], y_candidates=[0, point_y])


def render_parabola_line_intersections_triangle(spec, output_path):
    equation = parse_y_equation(spec.get("equation") or "y=1/2*(x-2)^2")
    line = parse_line_equation(spec.get("line_equation") or "y=x-1")
    intersections = quadratic_line_intersections(equation, line)
    warnings = []
    if len(intersections) < 2:
        warnings.append("line-intersection template needs two intersections")
        intersections = intersections or [(1, equation_value(equation, 1)), (3, equation_value(equation, 3))]
    intersections = sorted(intersections, key=lambda item: item[0])
    a = point_item("A", intersections[0][0], intersections[0][1])
    b = point_item("B", intersections[-1][0], intersections[-1][1])
    c = point_item("C", a["x"], 0)
    d = point_item("D", b["x"], 0)
    points = [a, b, c, d]

    def extra(ax, x_range, y_range):
        xs = np.linspace(x_range[0], x_range[1], 500)
        ys = [line_y(line, x) for x in xs]
        ax.plot(xs, ys, color="#555555", lw=lw(1.4), zorder=3)
        draw_guides_to_axes(ax, [a, b], x_to_axis=True, y_to_axis=False)

    return warnings + render_quadratic_scene(output_path, [equation], points=points, polygons=[[a, b, d, c]],
                                             extra_draw=extra,
                                             x_candidates=[a["x"], b["x"], c["x"], d["x"], 0],
                                             y_candidates=[a["y"], b["y"], 0])


def render_two_parabolas_lens_rectangle(spec, output_path):
    eq_top = parse_y_equation(spec.get("equation_top") or spec.get("equation1") or "y=-1/2*x^2+4")
    eq_bottom = parse_y_equation(spec.get("equation_bottom") or spec.get("equation2") or "y=x^2-2")
    coeff_top = quadratic_coefficients(eq_top)
    coeff_bottom = quadratic_coefficients(eq_bottom)
    roots = quadratic_roots_from_coeffs(
        coeff_top[0] - coeff_bottom[0],
        coeff_top[1] - coeff_bottom[1],
        coeff_top[2] - coeff_bottom[2],
    )
    warnings = []
    if len(roots) < 2:
        warnings.append("lens-rectangle template needs two parabola intersections")
        roots = [-2, 2]
    left, right = roots[0], roots[-1]
    mid_y = (equation_value(eq_top, 0) + equation_value(eq_bottom, 0)) / 2
    top_y = max(equation_value(eq_top, 0), equation_value(eq_bottom, 0))
    bottom_y = min(equation_value(eq_top, 0), equation_value(eq_bottom, 0))
    points = [
        point_item("A", left, mid_y),
        point_item("B", left, bottom_y),
        point_item("C", right, bottom_y),
        point_item("D", right, mid_y),
    ]

    def extra(ax, x_range, y_range):
        xs = np.linspace(left, right, 500)
        ax.fill_between(xs, safe_eval(eq_top["expr"], xs), safe_eval(eq_bottom["expr"], xs),
                        color="#f4c7b8", alpha=0.45, zorder=2)
        ax.plot([left, left, right, right, left], [bottom_y, mid_y, mid_y, bottom_y, bottom_y],
                color="#8c5a4a", lw=lw(1.0), zorder=4)

    return warnings + render_quadratic_scene(output_path, [eq_top, eq_bottom], points=points, extra_draw=extra,
                                             x_candidates=[left, right, 0], y_candidates=[top_y, bottom_y, mid_y, 0])


def render_parabola_four_family_origin(spec, output_path):
    raw_equations = spec.get("equations") or "y=-x^2; y=-1/3*x^2; y=1/3*x^2; y=2*x^2"
    parts = split_semicolon_outside_parentheses(raw_equations)
    if len(parts) <= 1:
        parts = split_csv_outside_parentheses(raw_equations)
    equations = []
    for part in parts:
        try:
            equations.append(parse_y_equation(part))
        except Exception:
            pass
    labels = parse_labels(spec.get("curve_labels") or "1,2,3,4")
    x_range = parse_range(spec.get("x_range"), (-2.8, 2.8))
    y_values = y_values_for_equations(equations, x_range) + [0]
    y_range = pad_range(min(y_values), max(y_values), 0.12)
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    xs = np.linspace(x_range[0], x_range[1], 900)
    for index, equation in enumerate(equations[:6]):
        ys = safe_eval(equation["expr"], xs)
        ax.plot(xs, ys, color="black", lw=lw(1.4), zorder=3)
        label = labels[index] if index < len(labels) else str(index + 1)
        sample_x = x_range[1] * (0.42 + 0.08 * (index % 3))
        try:
            sample_y = equation_value(equation, sample_x)
            if y_range[0] <= sample_y <= y_range[1]:
                ax.text(sample_x, sample_y, label, fontsize=fs(8), ha="left", va="center",
                        bbox=dict(facecolor="white", edgecolor="none", alpha=0.7, pad=fs(0.25)))
        except Exception:
            pass
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_parabola_axis_values(spec, output_path):
    equation = parse_y_equation(spec.get("equation") or "y=-(x+2)^2")
    coeffs = quadratic_coefficients(equation)
    roots = quadratic_x_intercepts(coeffs)
    vertex = quadratic_vertex(coeffs)
    y_intercept = coeffs[2]
    x_candidates = roots + [vertex[0], 0]
    x_range = pad_range(min(x_candidates), max(x_candidates), 0.35, 2.0)
    x_range = widen_x_for_parabola_style(x_range, 0.10)
    y_values = y_values_for_equations([equation], x_range) + [0, vertex[1], y_intercept]
    y_range = pad_range(min(y_values), max(y_values), 0.18, 2.0)
    y_range = steepen_parabola_view(x_range, y_range, 0.76)
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    xs = np.linspace(x_range[0], x_range[1], 1200)
    ys = safe_eval(equation["expr"], xs)
    ax.plot(xs, ys, color="black", lw=lw(1.8), zorder=3)
    if str(spec.get("show_guides", "true")).lower() != "false":
        vx, vy = vertex
        if abs(vx) > 1e-9:
            ax.plot([vx, vx], [0, vy], color="#777777", lw=lw(0.8), ls="--", zorder=2.5)
        if abs(vy) > 1e-9:
            ax.plot([0, vx], [vy, vy], color="#777777", lw=lw(0.8), ls="--", zorder=2.5)
    for root in roots:
        annotate_axis_value(ax, x_range, y_range, "x", root, "below")
    if abs(vertex[0]) > 1e-9:
        annotate_axis_value(ax, x_range, y_range, "x", vertex[0], "below")
    if abs(vertex[1]) > 1e-9:
        annotate_axis_value(ax, x_range, y_range, "y", vertex[1], "left")
    if abs(y_intercept) > 1e-9 and abs(y_intercept - vertex[1]) > 1e-9:
        annotate_axis_value(ax, x_range, y_range, "y", y_intercept, "left")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_quadratic_motion_height(spec, output_path):
    equation = parse_y_equation(spec.get("equation") or "y=-2*(x-3)^2+50")
    coeffs = quadratic_coefficients(equation)
    vertex = quadratic_vertex(coeffs)
    start_y = equation_value(equation, 0)
    roots = [root for root in quadratic_x_intercepts(coeffs) if root >= -1e-9]
    end_x = max(roots) if roots else vertex[0] * 2
    points = [point_item("", vertex[0], vertex[1]), point_item("", 0, start_y)]

    def extra(ax, x_range, y_range):
        draw_guides_to_axes(ax, [point_item("", vertex[0], vertex[1])], x_to_axis=True, y_to_axis=True)
        annotate_axis_value(ax, x_range, y_range, "x", vertex[0], "below")
        annotate_axis_value(ax, x_range, y_range, "y", vertex[1], "left")
        annotate_axis_value(ax, x_range, y_range, "y", start_y, "left")

    return render_quadratic_scene(output_path, [equation], points=points, extra_draw=extra,
                                  x_candidates=[0, vertex[0], end_x], y_candidates=[0, start_y, vertex[1]])


def render_parabolic_water_cross_section(spec, output_path):
    width = parse_length(spec.get("width"), 20)
    depth = parse_length(spec.get("depth"), 5)
    half = width / 2
    x = np.linspace(-half, half, 600)
    y = depth * (x / half) ** 2
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, depth + 3)
    ax.add_patch(plt.Rectangle((0, 0), width, depth + 1.4, facecolor="#d7ad63", edgecolor="none", alpha=0.85))
    ax.fill_between(x + half, y + 0.8, depth + 0.8, color="#8fd0ef", alpha=0.85)
    ax.plot(x + half, y + 0.8, color="#555555", lw=lw(1.2))
    ax.plot([0, width], [depth + 0.8, depth + 0.8], color="#555555", lw=lw(1.0))
    ax.text(0.2, depth + 0.9, "A", fontsize=fs(9), ha="left", va="bottom")
    ax.text(width - 0.2, depth + 0.9, "B", fontsize=fs(9), ha="right", va="bottom")
    ax.text(half, depth + 1.2, "M", fontsize=fs(9), ha="center", va="bottom")
    draw_dimension(ax, (0, depth + 1.35), (width, depth + 1.35), length_label(spec.get("width"), width, " m"), (0, 0.45))
    draw_dimension(ax, (half, depth + 0.8), (half, 0.8), length_label(spec.get("depth"), depth, " m"), (width * 0.06, 0))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_stacked_blocks_pattern(spec, output_path):
    stages = bounded_count(spec.get("stages"), 3, 1, 5)
    cell = parse_length(spec.get("cell"), 1)
    fig, ax = plt.subplots(figsize=(2.1, 1.25))
    ax.axis("off")
    x0 = 0
    max_h = (stages + 1) * cell
    for stage in range(1, stages + 1):
        cells = []
        for col in range(stage + 1):
            height = max(1, stage + 1 - col)
            for row in range(height):
                cells.append((x0 + col * cell, row * cell))
        for x_cell, y_cell in cells:
            ax.add_patch(plt.Rectangle((x_cell, y_cell + 0.35), cell, cell,
                                       facecolor="#de8f68", edgecolor="black", lw=lw(0.7)))
            ax.add_patch(plt.Polygon([(x_cell, y_cell + 1.35), (x_cell + 0.25, y_cell + 1.55),
                                      (x_cell + 1.25, y_cell + 1.55), (x_cell + cell, y_cell + 1.35)],
                                     closed=True, facecolor="#f1b18c", edgecolor="black", lw=lw(0.5)))
        ax.text(x0 + (stage + 1) * cell / 2, 0.05, f"[{stage}]", fontsize=fs(8), ha="center", va="bottom")
        x0 += (stage + 2.2) * cell
    ax.set_xlim(-0.2, x0)
    ax.set_ylim(0, max_h + 0.8)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_linear_basic_intercepts(spec, output_path):
    lines = parse_line_equations(spec.get("equation") or spec.get("line_equation") or "y=-x+2")
    points = []
    for line in lines[:1]:
        points.extend(line_axis_intercepts(line))
    x_candidates = [point["x"] for point in points] + [0]
    y_candidates = [point["y"] for point in points] + [0]
    return render_linear_scene(output_path, lines[:1], points=points, guides=False,
                               x_candidates=x_candidates, y_candidates=y_candidates)


def render_linear_point_guides(spec, output_path):
    lines = parse_line_equations(spec.get("equation") or spec.get("line_equation") or "y=x+1")
    points = parse_named_points(spec.get("points", ""))
    if not points and lines:
        x_values = [parse_number(item, 0) for item in split_csv_outside_parentheses(spec.get("guide_xs", ""))]
        labels = parse_labels(spec.get("point_labels", "") or spec.get("labels", ""))
        for index, x_value in enumerate(x_values):
            y_value = line_y(lines[0], x_value)
            if y_value is not None:
                label = labels[index] if index < len(labels) else chr(ord("A") + index)
                points.append(point_item(label, x_value, y_value))
    if not points and lines:
        for label, x_value in zip(["A", "B"], [-2, 3]):
            y_value = line_y(lines[0], x_value)
            if y_value is not None:
                points.append(point_item(label, x_value, y_value))
    return render_linear_scene(output_path, lines, points=points, guides=True)


def render_linear_axis_triangle(spec, output_path):
    line = parse_line_equation(spec.get("equation") or spec.get("line_equation") or "y=-1/3*x+2")
    vertical_x = spec.get("vertical_x")
    points = []
    if vertical_x not in (None, ""):
        vx = parse_number(vertical_x, 5)
        vy = line_y(line, vx) or 0
        points = [point_item("A", vx, vy), point_item("B", line_x_at_y(line, 0) or 0, 0), point_item("C", vx, 0)]
    else:
        points = [point_item("A", line_x_at_y(line, 0) or 0, 0), point_item("B", 0, line_y(line, 0) or 0), point_item("O", 0, 0)]
    return render_linear_scene(output_path, [line], points=points, polygons=[points], guides=False,
                               x_candidates=[point["x"] for point in points], y_candidates=[point["y"] for point in points],
                               shade_color="#f6c7d7")


def render_linear_two_lines_region(spec, output_path):
    lines = parse_line_equations(spec.get("equations") or spec.get("equation") or "y=-x+3; y=1/2*x+1")
    points = parse_named_points(spec.get("points", ""))
    warnings = []
    if not points and len(lines) >= 2:
        inter = line_intersection(lines[0], lines[1])
        if inter:
            points.append(point_item("A", inter[0], inter[1]))
        for label, line in zip(["B", "C"], lines[:2]):
            x0 = line_x_at_y(line, 0)
            if x0 is not None:
                points.append(point_item(label, x0, 0))
        if len(points) < 3:
            for label, line in zip(["B", "C"], lines[:2]):
                y0 = line_y(line, 0)
                if y0 is not None:
                    points.append(point_item(label, 0, y0))
    if len(points) < 3:
        warnings.append("linear region template needs at least three points")
    polygon = points[:4] if len(points) >= 4 else points[:3]
    return warnings + render_linear_scene(output_path, lines, points=points, polygons=[polygon] if len(polygon) >= 3 else [],
                                          guides=True, shade_color="#f4c7b8")


def render_linear_square_under_line(spec, output_path):
    line = parse_line_equation(spec.get("equation") or spec.get("line_equation") or "y=1/3*x+1")
    x1 = parse_number(spec.get("x_left"), 2)
    side = parse_number(spec.get("side"), 1)
    x2 = x1 + side
    y1 = line_y(line, x1) or side
    y2 = line_y(line, x2) or side
    bottom = min(y1, y2) - side
    square1 = [
        point_item("", x1, bottom),
        point_item("", x1 + side, bottom),
        point_item("", x1 + side, bottom + side),
        point_item("", x1, bottom + side),
    ]
    square2 = [
        point_item("", x2, bottom),
        point_item("", x2 + side, bottom),
        point_item("", x2 + side, bottom + side),
        point_item("", x2, bottom + side),
    ]
    points = [point_item("1", 0, line_y(line, 0) or 0)]

    def extra(ax, x_range, y_range):
        for square, color in ((square1, "#f6c7d7"), (square2, "white")):
            ax.add_patch(plt.Polygon([(p["x"], p["y"]) for p in square], closed=True,
                                     facecolor=color, edgecolor="black", lw=lw(1.0), zorder=3))

    return render_linear_scene(output_path, [line], points=points, extra_draw=extra,
                               x_candidates=[0, x1, x2 + side], y_candidates=[0, bottom + side, line_y(line, x2 + side) or 0])


def render_grid_number_table(spec, output_path):
    rows = bounded_count(spec.get("rows"), 3, 2, 8)
    cols = bounded_count(spec.get("cols"), 3, 2, 8)
    entries = {}
    for item in split_semicolon_outside_parentheses(spec.get("entries", "")):
        parts = [part.strip() for part in item.split(",")]
        if len(parts) >= 3:
            entries[(int(parse_number(parts[0], 1)) - 1, int(parse_number(parts[1], 1)) - 1)] = parts[2]
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, cols, rows)
    for r in range(rows):
        for c in range(cols):
            ax.add_patch(plt.Rectangle((c, rows - r - 1), 1, 1, facecolor="white", edgecolor="black", lw=lw(0.8)))
            text = entries.get((r, c), "")
            if text:
                ax.text(c + 0.5, rows - r - 0.5, text, fontsize=fs(9), ha="center", va="center")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_tiled_rectangles_layout(spec, output_path):
    cols = bounded_count(spec.get("cols"), 4, 2, 8)
    rows = bounded_count(spec.get("rows"), 2, 2, 5)
    cell_w = parse_length(spec.get("cell_width"), 2)
    cell_h = parse_length(spec.get("cell_height"), 1.25)
    offset = parse_length(spec.get("offset"), cell_w * 0.5)
    width = cols * cell_w
    height = rows * cell_h
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    for r in range(rows):
        row_offset = offset if r % 2 == 1 else 0
        for c in range(cols):
            x = c * cell_w + row_offset
            if x + cell_w > width + 1e-9:
                continue
            ax.add_patch(plt.Rectangle((x, r * cell_h), cell_w, cell_h,
                                       facecolor="#ead8a6", edgecolor="black", lw=lw(0.8)))
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="none", edgecolor="black", lw=lw(1.1)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def regular_polygon_vertices(cx, cy, radius, sides, rotation=0.0):
    return [
        (
            cx + radius * math.cos(rotation + 2 * math.pi * index / sides),
            cy + radius * math.sin(rotation + 2 * math.pi * index / sides),
        )
        for index in range(sides)
    ]


def reflect_point_across_line(point, start, end):
    px, py = point
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return point
    t = ((px - x1) * dx + (py - y1) * dy) / length_sq
    foot_x = x1 + t * dx
    foot_y = y1 + t * dy
    return 2 * foot_x - px, 2 * foot_y - py


def rightmost_edge(vertices):
    best = None
    best_x = None
    count = len(vertices)
    for index in range(count):
        edge = (vertices[index], vertices[(index + 1) % count])
        mid_x = (edge[0][0] + edge[1][0]) / 2
        if best_x is None or mid_x > best_x:
            best_x = mid_x
            best = edge
    return best


def render_regular_polygon_chain(spec, output_path):
    sides = bounded_count(spec.get("sides"), 6, 3, 8)
    count = bounded_count(spec.get("count"), 3, 1, 8)
    side = parse_length(spec.get("side"), 1)
    radius = side / (2 * math.sin(math.pi / sides))
    first = regular_polygon_vertices(radius, radius * 1.2, radius, sides, math.pi / sides)
    polygons = [first]
    for _ in range(1, count):
        edge = rightmost_edge(polygons[-1])
        polygons.append([reflect_point_across_line(point, edge[0], edge[1]) for point in polygons[-1]])
    xs = [x for polygon in polygons for x, _ in polygon]
    ys = [y for polygon in polygons for _, y in polygon]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    padding = radius * 0.25
    width = max_x - min_x + padding * 2
    height = max_y - min_y + padding * 2
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    for polygon in polygons:
        verts = [(x - min_x + padding, y - min_y + padding) for x, y in polygon]
        ax.add_patch(plt.Polygon(verts, closed=True, facecolor="#e8e0ef", edgecolor="#6a5f74", lw=lw(1.0)))
        for x, y in verts:
            ax.scatter([x], [y], color="#c26d3d", s=marker_area(10), zorder=5)
    if str(spec.get("show_count_labels", "false")).lower() == "true":
        ax.text(width * 0.5, -height * 0.08, f"{count}", fontsize=fs(9), ha="center", va="top")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_rectangle_side_point_triangle(spec, output_path):
    width = parse_length(spec.get("width"), 24)
    height = parse_length(spec.get("height"), 32)
    point = str(spec.get("point_side") or "bottom").strip().lower()
    t = parse_number(spec.get("point_ratio"), 0.45)
    t = max(0.05, min(0.95, t))
    vertices = {
        "A": (0, height),
        "B": (0, 0),
        "C": (width, 0),
        "D": (width, height),
    }
    if point == "right":
        p = (width, height * t)
    elif point == "left":
        p = (0, height * t)
    elif point == "top":
        p = (width * t, height)
    else:
        p = (width * t, 0)
    triangle_names = parse_labels(spec.get("triangle_points") or "A,B,P")
    point_map = {**vertices, "P": p, "Q": p}
    triangle = [point_map.get(name, p) for name in triangle_names[:3]]
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.add_patch(plt.Polygon(triangle, closed=True, facecolor="#d8c8e8", edgecolor="#6a5f74", lw=lw(1.0), alpha=0.75))
    draw_dimension(ax, (0, height), (width, height), length_label(spec.get("width"), width, " cm"), (0, height * 0.08))
    draw_dimension(ax, (0, 0), (0, height), length_label(spec.get("height"), height, " cm"), (-width * 0.08, 0))
    for label, (x, y) in {**vertices, "P": p}.items():
        ax.text(x, y, label, fontsize=fs(9), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=fs(0.4)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_rectangle_cut_corner(spec, output_path):
    width = parse_length(spec.get("width"), 18)
    height = parse_length(spec.get("height"), 12)
    top_cut = parse_length(spec.get("top_cut"), width * 0.28)
    right_cut = parse_length(spec.get("right_cut"), height * 0.25)
    e = (top_cut, height)
    f = (width, right_cut)
    shaded = [(0, height), e, f, (width, 0), (0, 0)]
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.add_patch(plt.Polygon(shaded, closed=True, facecolor="#f4df86", edgecolor="#a3863a", lw=lw(1.0), alpha=0.75))
    ax.plot([e[0], f[0]], [e[1], f[1]], color="#777777", lw=lw(1.0))
    draw_dimension(ax, (0, height), e, length_label(spec.get("top_cut"), top_cut, " m"), (0, height * 0.09))
    draw_dimension(ax, (width, 0), f, length_label(spec.get("right_cut"), right_cut, " m"), (width * 0.09, 0))
    for label, point in {"A": (0, height), "B": (0, 0), "C": (width, 0), "D": (width, height), "E": e, "F": f}.items():
        ax.text(point[0], point[1], label, fontsize=fs(8), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=fs(0.35)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_rectangle_expanding_sides(spec, output_path):
    width = parse_length(spec.get("width"), 60)
    height = parse_length(spec.get("height"), 33)
    right_expand = parse_length(spec.get("right_expand"), width * 0.25)
    bottom_expand = parse_length(spec.get("bottom_expand"), height * 0.35)
    total_width = width + right_expand
    total_height = height + bottom_expand
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, total_width, total_height)
    ax.add_patch(plt.Rectangle((0, bottom_expand), width, height, facecolor="#ead8ee", edgecolor="black", lw=lw(1.0), alpha=0.75))
    ax.add_patch(plt.Rectangle((width, bottom_expand), right_expand, height, facecolor="#ead8ee", edgecolor="black", lw=lw(1.0), alpha=0.75))
    ax.add_patch(plt.Rectangle((0, 0), width, bottom_expand, facecolor="white", edgecolor="black", lw=lw(1.0)))
    ax.annotate("", xy=(width + right_expand * 0.35, bottom_expand + height * 0.5),
                xytext=(width + right_expand * 0.75, bottom_expand + height * 0.5),
                arrowprops=dict(arrowstyle="->", lw=lw(1.4), color="#e85b8b"))
    ax.annotate("", xy=(width * 0.5, bottom_expand * 0.25), xytext=(width * 0.5, bottom_expand * 0.75),
                arrowprops=dict(arrowstyle="->", lw=lw(1.4), color="#e85b8b"))
    draw_dimension(ax, (0, total_height), (width, total_height), length_label(spec.get("width"), width, " cm"), (0, total_height * 0.07))
    draw_dimension(ax, (0, bottom_expand), (0, total_height), length_label(spec.get("height"), height, " cm"), (-total_width * 0.07, 0))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_three_semicircles(spec, output_path):
    total = parse_length(spec.get("diameter"), 20)
    split = parse_length(spec.get("split"), total * 0.5)
    split = max(total * 0.15, min(total * 0.85, split))
    radius = total / 2
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, total, radius)
    theta = np.linspace(0, math.pi, 240)
    outer_x = radius + radius * np.cos(theta)
    outer_y = radius * np.sin(theta)
    left_r = split / 2
    right_r = (total - split) / 2
    left_x = left_r + left_r * np.cos(theta)
    left_y = left_r * np.sin(theta)
    right_x = split + right_r + right_r * np.cos(theta)
    right_y = right_r * np.sin(theta)
    fill_x = list(outer_x) + list(reversed(right_x)) + list(reversed(left_x))
    fill_y = list(outer_y) + list(reversed(right_y)) + list(reversed(left_y))
    ax.fill(fill_x, fill_y, color="#f2cf73", alpha=0.75, zorder=1)
    ax.plot(outer_x, outer_y, color="black", lw=lw(1.1))
    ax.plot(left_x, left_y, color="black", lw=lw(1.0))
    ax.plot(right_x, right_y, color="black", lw=lw(1.0))
    ax.plot([0, total], [0, 0], color="black", lw=lw(1.0))
    ax.text(0, 0, "A", fontsize=fs(9), ha="right", va="top")
    ax.text(split, 0, "C", fontsize=fs(9), ha="center", va="top")
    ax.text(total, 0, "B", fontsize=fs(9), ha="left", va="top")
    draw_dimension(ax, (0, 0), (total, 0), length_label(spec.get("diameter"), total, " cm"), (0, -radius * 0.18))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_folded_rectangle_overlap(spec, output_path):
    width = parse_length(spec.get("width"), 12)
    height = parse_length(spec.get("height"), 8)
    fold_x = parse_length(spec.get("fold_x"), width * 0.58)
    right_height = parse_length(spec.get("right_height"), height * 0.72)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Polygon([(0, 0), (fold_x, 0), (fold_x, height), (0, height)],
                             closed=True, facecolor="#cfe8d2", edgecolor="black", lw=lw(1.0), alpha=0.75))
    ax.add_patch(plt.Polygon([(fold_x, 0), (width, 0), (width, right_height), (fold_x, height)],
                             closed=True, facecolor="#a9d4c8", edgecolor="black", lw=lw(1.0), alpha=0.85))
    ax.plot([0, fold_x], [0, height], color="#777777", lw=lw(0.9))
    draw_dimension(ax, (0, 0), (0, height), length_label(spec.get("height"), height, " cm"), (-width * 0.08, 0))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_square_internal_rectangles(spec, output_path):
    side = parse_length(spec.get("side"), 10)
    x = parse_length(spec.get("inner_x"), side * 0.35)
    y = parse_length(spec.get("inner_y"), side * 0.55)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, side, side)
    ax.add_patch(plt.Rectangle((0, 0), side, side, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.plot([x, x], [0, side], color="#777777", lw=lw(0.9))
    ax.plot([0, side], [y, y], color="#777777", lw=lw(0.9))
    ax.add_patch(plt.Rectangle((x, 0), side - x, y, facecolor="#cde8c6", edgecolor="none", alpha=0.8))
    labels = {"A": (0, side), "B": (0, 0), "C": (side, 0), "D": (side, side),
              "P": (x, y), "F": (x, 0), "G": (side, y), "H": (x, side)}
    for label, point in labels.items():
        ax.text(point[0], point[1], label, fontsize=fs(8), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=fs(0.35)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_regular_polygon_diagonals(spec, output_path):
    sides = bounded_count(spec.get("sides"), 5, 5, 8)
    radius = parse_length(spec.get("side"), 1.0)
    verts = regular_polygon_vertices(radius * 1.4, radius * 1.45, radius, sides, math.pi / 2)
    xs = [x for x, _ in verts]
    ys = [y for _, y in verts]
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, max(xs) - min(xs), max(ys) - min(ys))
    shifted = [(x - min(xs), y - min(ys)) for x, y in verts]
    ax.add_patch(plt.Polygon(shifted, closed=True, facecolor="white", edgecolor="black", lw=lw(1.0)))
    for i in range(sides):
        for j in range(i + 2, sides):
            if i == 0 and j == sides - 1:
                continue
            ax.plot([shifted[i][0], shifted[j][0]], [shifted[i][1], shifted[j][1]], color="#777777", lw=lw(0.7))
    for label, point in zip(list("ABCDE")[:sides], shifted):
        ax.text(point[0], point[1], label, fontsize=fs(8), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=fs(0.3)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_linear_parallel_lines(spec, output_path):
    base = parse_line_equation(spec.get("equation") or "y=-x+2")
    offsets = [parse_number(item, 0) for item in split_csv_outside_parentheses(spec.get("offsets", "-2,2"))]
    lines = [base]
    for offset in offsets:
        lines.append({"raw": base["raw"], "a": base["a"], "b": base["b"], "c": base["c"] + offset})
    points = parse_named_points(spec.get("points", ""))
    return render_linear_scene(output_path, lines, points=points, guides=True)


def render_multiple_choice_linear_position(spec, output_path):
    equations = parse_line_equations(spec.get("choices") or spec.get("equations") or "y=-x+1; y=x+1; y=-x-1; y=x-1")
    equations = equations[:5]
    labels = ["①", "②", "③", "④", "⑤"]
    x_range = parse_range(spec.get("x_range"), (-3, 3))
    y_values = line_sample_y_values(equations, x_range) + [0]
    y_range = pad_range(min(y_values), max(y_values), 0.16, 2.0)
    fig, axes = plt.subplots(3, 2, figsize=CHOICE_FIGURE_SIZE_INCHES)
    axes_flat = list(axes.flatten())
    xs = np.linspace(x_range[0], x_range[1], 300)
    for index, ax in enumerate(axes_flat):
        if index >= len(equations):
            ax.axis("off")
            continue
        setup_choice_axes(ax, x_range, y_range)
        line = equations[index]
        if abs(line["b"]) < 1e-9:
            ax.axvline(-line["c"] / line["a"], color="black", lw=lw(1.4))
        else:
            ax.plot(xs, [line_y(line, x) for x in xs], color="black", lw=lw(1.4))
        ax.text(0.04, 0.92, labels[index], transform=ax.transAxes, ha="left", va="top", fontsize=fs(9))
    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.02, top=0.98, wspace=0.18, hspace=0.28)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_annulus_radius_increase(spec, output_path):
    inner = parse_length(spec.get("inner_radius"), 8)
    increase = parse_length(spec.get("increase") or spec.get("radius_gap"), max(inner * 0.25, 1))
    outer = inner + increase
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, outer * 2, outer * 2)
    center = (outer, outer)
    ax.add_patch(plt.Circle(center, outer, facecolor="#bcdff7", edgecolor="black", lw=lw(1.1), alpha=0.8))
    ax.add_patch(plt.Circle(center, inner, facecolor="white", edgecolor="#555555", lw=lw(1.0)))
    ax.plot([center[0], center[0] + inner], [center[1], center[1]], color="#555555", lw=lw(0.9))
    ax.plot([center[0] + inner, center[0] + outer], [center[1], center[1]], color="#555555", lw=lw(0.9))
    ax.scatter([center[0]], [center[1]], color="black", s=marker_area(10), zorder=5)
    ax.text(center[0] + inner * 0.52, center[1] + outer * 0.07,
            length_label(spec.get("inner_radius"), inner, " cm"), fontsize=fs(8), ha="center", va="bottom")
    ax.text(center[0] + inner + increase * 0.55, center[1] + outer * 0.07,
            str(spec.get("increase") or spec.get("radius_gap") or "x") + (" cm" if spec.get("increase") else ""),
            fontsize=fs(8), ha="center", va="bottom")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_rectangle_u_shaped_path(spec, output_path):
    width = parse_length(spec.get("width"), 14)
    height = parse_length(spec.get("height"), 9)
    path = parse_length(spec.get("path_width") or spec.get("road_width"), min(width, height) * 0.14)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="#cfe8b8", edgecolor="black", lw=lw(1.1)))
    path_color = "#d98f89"
    ax.add_patch(plt.Rectangle((0, 0), width, path, facecolor=path_color, edgecolor="none", alpha=0.88))
    ax.add_patch(plt.Rectangle((0, height - path), width, path, facecolor=path_color, edgecolor="none", alpha=0.88))
    ax.add_patch(plt.Rectangle((0, 0), path, height, facecolor=path_color, edgecolor="none", alpha=0.88))
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="none", edgecolor="black", lw=lw(1.1), zorder=5))
    ax.plot([path, width], [path, path], color="#777777", lw=lw(0.8), ls="--")
    ax.plot([path, width], [height - path, height - path], color="#777777", lw=lw(0.8), ls="--")
    ax.plot([path, path], [path, height - path], color="#777777", lw=lw(0.8), ls="--")
    draw_dimension(ax, (0, height), (width, height), length_label(spec.get("width"), width, " m"), (0, height * 0.09))
    draw_dimension(ax, (0, 0), (0, height), length_label(spec.get("height"), height, " m"), (-width * 0.08, 0))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_linear_vertical_line_triangle(spec, output_path):
    spec = dict(spec)
    spec.setdefault("vertical_x", spec.get("x_right") or "5")
    return render_linear_axis_triangle(spec, output_path)


def render_parallelogram_diagonal_intersection(spec, output_path):
    width = parse_length(spec.get("base") or spec.get("width"), 10)
    height = parse_length(spec.get("height"), 6)
    skew = parse_length(spec.get("skew"), width * 0.22)
    p_ratio = parse_number(spec.get("point_ratio"), 0.68)
    p_ratio = max(0.15, min(0.9, p_ratio))
    a, b, c, d = (skew, height), (0, 0), (width, 0), (width + skew, height)
    p = (width * p_ratio, 0)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width + skew, height)
    ax.add_patch(plt.Polygon([a, b, c, d], closed=True, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.plot([b[0], d[0]], [b[1], d[1]], color="#555555", lw=lw(0.9))
    ax.plot([a[0], c[0]], [a[1], c[1]], color="#555555", lw=lw(0.9))
    ax.plot([a[0], p[0]], [a[1], p[1]], color="#555555", lw=lw(0.9))
    ax.add_patch(plt.Polygon([d, (width * 0.56 + skew * 0.25, height * 0.45), p],
                             closed=True, facecolor="#d8c8e8", edgecolor="none", alpha=0.55))
    draw_dimension(ax, a, d, length_label(spec.get("top_length") or spec.get("base"), width, " cm"), (0, height * 0.1))
    for label, point in {"A": a, "B": b, "C": c, "D": d, "P": p, "O": (width * 0.56 + skew * 0.25, height * 0.45)}.items():
        ax.text(point[0], point[1], label, fontsize=fs(8), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=fs(0.35)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_collinear_two_squares(spec, output_path):
    left = parse_length(spec.get("left_side") or spec.get("ab"), 4)
    right = parse_length(spec.get("right_side") or spec.get("bc"), 8)
    total = left + right
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, total, max(left, right))
    ax.plot([0, total], [0, 0], color="black", lw=lw(1.0))
    ax.add_patch(plt.Rectangle((0, 0), left, left, facecolor="#f1d36f", edgecolor="black", lw=lw(1.0), alpha=0.78))
    ax.add_patch(plt.Rectangle((left, 0), right, right, facecolor="#f1d36f", edgecolor="black", lw=lw(1.0), alpha=0.78))
    labels = {"A": (0, 0), "B": (left, 0), "M": (left + right / 2, 0), "C": (total, 0)}
    for label, point in labels.items():
        ax.scatter([point[0]], [point[1]], color="black", s=marker_area(12), zorder=6)
        ax.text(point[0], point[1] - max(left, right) * 0.08, label, fontsize=fs(9), ha="center", va="top")
    draw_dimension(ax, (0, left), (left, left), str(spec.get("left_side") or spec.get("ab") or "x"), (0, max(left, right) * 0.08))
    draw_dimension(ax, (left, right), (total, right), str(spec.get("right_side") or spec.get("bc") or "8"), (0, max(left, right) * 0.08))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_square_cut_and_shift(spec, output_path):
    side = parse_length(spec.get("side"), 10)
    top_cut = parse_length(spec.get("top_cut"), side * 0.25)
    right_shift = parse_length(spec.get("right_shift"), side * 0.28)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, side + right_shift, side)
    ax.add_patch(plt.Rectangle((0, 0), side, side, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.plot([0, side], [side - top_cut, side - top_cut], color="#777777", lw=lw(0.9))
    ax.add_patch(plt.Rectangle((0, 0), side, side - top_cut, facecolor="#f1d36f", edgecolor="none", alpha=0.85))
    ax.add_patch(plt.Polygon([(side, 0), (side + right_shift, 0), (side + right_shift, side - top_cut), (side, side - top_cut)],
                             closed=True, facecolor="#f1d36f", edgecolor="black", lw=lw(1.0), alpha=0.85))
    draw_dimension(ax, (0, side), (0, side - top_cut), length_label(spec.get("top_cut"), top_cut), (-side * 0.08, 0))
    draw_dimension(ax, (side, 0), (side + right_shift, 0), length_label(spec.get("right_shift"), right_shift), (0, -side * 0.08))
    for label, point in {"A": (0, side), "B": (0, 0), "C": (side, 0), "D": (side, side)}.items():
        ax.text(point[0], point[1], label, fontsize=fs(8), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=fs(0.35)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_rectangle_square_similar_split(spec, output_path):
    width = parse_length(spec.get("width"), 12)
    height = parse_length(spec.get("height"), width * 0.62)
    split = parse_length(spec.get("square_side") or spec.get("split"), height)
    split = max(width * 0.2, min(width * 0.85, split))
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.plot([split, split], [0, height], color="black", lw=lw(1.0))
    ax.add_patch(plt.Rectangle((0, 0), split, height, facecolor="#f7f7f7", edgecolor="none", alpha=0.5))
    draw_dimension(ax, (0, height), (width, height), length_label(spec.get("width"), width), (0, height * 0.1))
    for label, point in {"A": (0, height), "B": (0, 0), "C": (width, 0), "D": (width, height), "E": (split, height), "F": (split, 0)}.items():
        ax.text(point[0], point[1], label, fontsize=fs(8), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=fs(0.35)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_nested_rectangles_frame(spec, output_path):
    outer_w = parse_length(spec.get("outer_width"), 10)
    outer_h = parse_length(spec.get("outer_height"), 8)
    frame = parse_length(spec.get("frame_width"), 1)
    levels = bounded_count(spec.get("levels"), 3, 2, 5)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, outer_w, outer_h)
    colors = ["#f4c7b8", "white", "#f4c7b8", "white", "#f4c7b8"]
    for index in range(levels):
        inset = frame * index
        w = max(outer_w - 2 * inset, frame)
        h = max(outer_h - 2 * inset, frame)
        ax.add_patch(plt.Rectangle((inset, inset), w, h, facecolor=colors[index % len(colors)],
                                   edgecolor="black", lw=lw(1.0), alpha=0.82))
    ax.text(outer_w * 0.72, outer_h - frame * 0.5, length_label(spec.get("frame_width"), frame, "cm"),
            fontsize=fs(8), ha="center", va="center",
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=fs(0.4)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_dot_pattern(spec, output_path, triangular=False):
    stages = bounded_count(spec.get("stages"), 4, 1, 6)
    dot_size = parse_number(spec.get("dot_size"), 18)
    spacing = 0.36
    fig, ax = plt.subplots(figsize=(2.1, 1.25))
    ax.axis("off")
    x_cursor = 0.25
    max_height = stages * spacing + 0.4
    for stage in range(1, stages + 1):
        points = []
        if triangular:
            for row in range(stage):
                for col in range(row + 1):
                    points.append((x_cursor + col * spacing, max_height - row * spacing - 0.25))
        else:
            rows = stage
            cols = stage + 2
            for row in range(rows):
                for col in range(cols):
                    points.append((x_cursor + col * spacing, max_height - row * spacing - 0.25))
        if points:
            xs, ys = zip(*points)
            ax.scatter(xs, ys, s=marker_area(dot_size), color="#333333")
        ax.text(x_cursor + (stage * spacing if triangular else (stage + 1) * spacing) / 2,
                0.06, f"[{stage}]", fontsize=fs(8), ha="center", va="bottom")
        x_cursor += (stage + (1 if triangular else 3)) * spacing + 0.45
    ax.set_xlim(0, x_cursor)
    ax.set_ylim(0, max_height + 0.15)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


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
        if any(equation.get("kind") == "y" and "x**2" in equation.get("expr", "") for equation in y_equations):
            x_range = widen_x_for_parabola_style(x_range, 0.08)
            y_range = steepen_parabola_view(x_range, y_range, 0.74)

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


def length_label(value, fallback, unit=""):
    text = str(value or "").strip()
    if text:
        return text + unit
    formatted = format_number(fallback)
    return (formatted if formatted else str(fallback)) + unit


def bounded_count(value, default, minimum=1, maximum=12):
    try:
        count = int(parse_number(value, default))
    except Exception:
        count = default
    return max(minimum, min(maximum, count))


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
    road_count = bounded_count(spec.get("road_count"), 3, 1, 7)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    field = plt.Rectangle((0, 0), width, height, facecolor="#efe1b0", edgecolor="black", lw=lw(1.2))
    ax.add_patch(field)

    if slanted or multi:
        strips = [(-width * 0.15, width * 0.35, width * 0.55, width * 1.05)]
        if multi:
            spacing = width / max(road_count, 1)
            strips = []
            for index in range(road_count):
                center = spacing * (index + 0.55)
                strips.append((center - road_width, center, center + width * 0.2, center - road_width + width * 0.2))
        for x_bottom1, x_bottom2, x_top2, x_top1 in strips:
            road = plt.Polygon(
                [(x_bottom1, 0), (x_bottom2, 0), (x_top2, height), (x_top1, height)],
                closed=True, facecolor="white", edgecolor="none", alpha=0.95
            )
            road.set_clip_path(field)
            ax.add_patch(road)
        crossing = plt.Polygon(
            [(0, height * 0.45), (width, height * 0.65), (width, height * 0.65 + road_width), (0, height * 0.45 + road_width)],
            closed=True, facecolor="white", edgecolor="none", alpha=0.95
        )
        crossing.set_clip_path(field)
        ax.add_patch(crossing)
        ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="none", edgecolor="black", lw=lw(1.2), zorder=5))
    else:
        cx = width * 0.5 - road_width / 2
        cy = height * 0.5 - road_width / 2
        ax.add_patch(plt.Rectangle((cx, 0), road_width, height, facecolor="white", edgecolor="none"))
        ax.add_patch(plt.Rectangle((0, cy), width, road_width, facecolor="white", edgecolor="none"))
        ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="none", edgecolor="black", lw=lw(1.2), zorder=5))

    draw_dimension(ax, (0, height), (width, height), length_label(spec.get("width"), width, " m"), (0, height * 0.09))
    draw_dimension(ax, (0, 0), (0, height), length_label(spec.get("height"), height, " m"), (-width * 0.08, 0))
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
                   length_label(spec.get("inner_width"), inner_width), (0, border * 0.45))
    draw_dimension(ax, (border, border), (border, border + inner_height),
                   length_label(spec.get("inner_height"), inner_height), (-border * 0.45, 0))
    ax.text(outer_width - border / 2, outer_height / 2, length_label(spec.get("border_width"), border, " m"),
            fontsize=fs(8), rotation=90,
            ha="center", va="center", bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=fs(0.6)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_two_squares_on_segment(spec, output_path):
    total = parse_length(spec.get("total_length"), 11)
    left_raw = spec.get("left_side") or spec.get("left_square_side")
    right_raw = spec.get("right_side") or spec.get("right_square_side")
    left_side = parse_length(left_raw, total * 0.62)
    right_side = parse_length(right_raw, max(total - left_side, total * 0.25))
    if left_side + right_side > total * 1.25 or left_side + right_side < total * 0.75:
        scale = total / max(left_side + right_side, 1)
        left_side *= scale
        right_side *= scale
    total = left_side + right_side
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, total, max(left_side, right_side))
    ax.add_patch(plt.Rectangle((0, 0), left_side, left_side, facecolor="#f1d36f", edgecolor="black", lw=lw(1.1)))
    ax.add_patch(plt.Rectangle((left_side, 0), right_side, right_side, facecolor="#f1d36f", edgecolor="black", lw=lw(1.1)))
    ax.text(0, -total * 0.08, "A", fontsize=fs(10), ha="center", va="top")
    ax.text(left_side, -total * 0.08, "C", fontsize=fs(10), ha="center", va="top")
    ax.text(total, -total * 0.08, "B", fontsize=fs(10), ha="center", va="top")
    draw_dimension(ax, (0, 0), (left_side, 0), length_label(left_raw, left_side), (0, -total * 0.14))
    draw_dimension(ax, (left_side, 0), (total, 0), length_label(right_raw, right_side), (0, -total * 0.14))
    draw_dimension(ax, (0, 0), (total, 0), length_label(spec.get("total_length"), total, " cm"), (0, -total * 0.26))
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
    draw_dimension(ax, (0, paper_height), (paper_width, paper_height),
                   length_label(spec.get("paper_width") or paper_side, paper_width, " cm"), (0, paper_height * 0.1))
    draw_dimension(ax, (0, 0), (0, paper_height),
                   length_label(spec.get("paper_height") or paper_side, paper_height, " cm"), (-paper_width * 0.08, 0))
    ax.text(paper_width + paper_width * 0.06, cut / 2, length_label(spec.get("cut_side"), cut, " cm"),
            fontsize=fs(8), ha="left", va="center")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_square_expanded_garden(spec, output_path):
    side = parse_length(spec.get("inner_side"), 8)
    right = parse_length(spec.get("expand_right"), 9)
    bottom = parse_length(spec.get("expand_bottom"), 6)
    width = side + right
    height = side + bottom
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, bottom), side, side, facecolor="#cde8c6", edgecolor="black", lw=lw(1.1)))
    ax.add_patch(plt.Rectangle((side, bottom), right, side, facecolor="#ead8a6", edgecolor="black", lw=lw(1.0)))
    ax.add_patch(plt.Rectangle((0, 0), width, bottom, facecolor="#ead8a6", edgecolor="black", lw=lw(1.0)))
    draw_dimension(ax, (0, height), (side, height), str(spec.get("inner_side") or format_number(side)), (0, height * 0.08))
    draw_dimension(ax, (side, height), (width, height), str(spec.get("expand_right") or format_number(right)), (0, height * 0.08))
    draw_dimension(ax, (width, bottom), (width, height), str(spec.get("inner_side") or format_number(side)), (width * 0.08, 0))
    draw_dimension(ax, (width, 0), (width, bottom), str(spec.get("expand_bottom") or format_number(bottom)), (width * 0.08, 0))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_rectangle_diagonal_flower_path(spec, output_path):
    width = parse_length(spec.get("width"), parse_length(spec.get("width_ratio"), 2) * 8)
    height = parse_length(spec.get("height"), parse_length(spec.get("height_ratio"), 1) * 8)
    path_width = parse_length(spec.get("path_width"), 2)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="#f2cf73", edgecolor="black", lw=lw(1.1)))
    offset = path_width / max(math.sqrt(width * width + height * height), 1) * width
    ax.add_patch(plt.Polygon([(0, offset), (offset, 0), (width, height - offset), (width - offset, height)],
                             closed=True, facecolor="white", edgecolor="#777777", lw=lw(1.0)))
    draw_dimension(ax, (0, height), (width, height), format_number(width), (0, height * 0.08))
    draw_dimension(ax, (0, 0), (0, height), format_number(height), (-width * 0.08, 0))
    ax.text(width * 0.58, height * 0.42, str(spec.get("path_width") or format_number(path_width)),
            fontsize=fs(8), ha="center", va="center",
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=fs(0.6)))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_growing_rectangle(spec, output_path):
    width = parse_length(spec.get("initial_width"), 30)
    height = parse_length(spec.get("initial_height"), 24)
    dw = str(spec.get("width_change_per_time") or "-2").strip()
    dh = str(spec.get("height_change_per_time") or "3").strip()
    t = str(spec.get("time_label") or "x").strip()
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="#d9ecff", edgecolor="black", lw=lw(1.1)))
    draw_dimension(ax, (0, height), (width, height), format_number(width), (0, height * 0.1))
    draw_dimension(ax, (0, 0), (0, height), format_number(height), (-width * 0.08, 0))
    ax.annotate(dw + t, xy=(width * 0.75, height * 0.15), xytext=(width * 0.95, height * 0.15),
                arrowprops=dict(arrowstyle="->", lw=lw(1.0)), fontsize=fs(8), ha="center", va="center")
    ax.annotate(dh + t, xy=(width * 0.15, height * 0.75), xytext=(width * 0.15, height * 0.95),
                arrowprops=dict(arrowstyle="->", lw=lw(1.0)), fontsize=fs(8), ha="center", va="center")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_folded_tray(spec, output_path):
    sheet_width = parse_length(spec.get("sheet_width"), 40)
    fold_height = parse_length(spec.get("fold_height"), 8)
    bottom_width = max(sheet_width - 2 * fold_height, sheet_width * 0.35)
    height = fold_height * 1.2
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, sheet_width, height)
    left = (sheet_width - bottom_width) / 2
    ax.add_patch(plt.Polygon([(left, 0), (left + bottom_width, 0), (sheet_width, height), (0, height)],
                             closed=True, facecolor="#d9ecff", edgecolor="black", lw=lw(1.1)))
    ax.plot([left, left], [0, height * 0.85], color="#777777", ls="--", lw=lw(0.9))
    ax.plot([left + bottom_width, left + bottom_width], [0, height * 0.85], color="#777777", ls="--", lw=lw(0.9))
    draw_dimension(ax, (0, height), (sheet_width, height), format_number(sheet_width), (0, height * 0.16))
    ax.text(left / 2, height * 0.45, str(spec.get("fold_height") or "x"), fontsize=fs(9), ha="center", va="center")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_adjacent_rectangles(spec, output_path):
    left_w = parse_length(spec.get("left_width"), 8)
    left_h = parse_length(spec.get("left_height") or spec.get("shared_height"), 8)
    right_w = parse_length(spec.get("right_width"), 6)
    right_h = parse_length(spec.get("right_height") or spec.get("shared_height"), left_h)
    width = left_w + right_w
    height = max(left_h, right_h)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, 0), left_w, left_h, facecolor="#d9ecff", edgecolor="black", lw=lw(1.1)))
    ax.add_patch(plt.Rectangle((left_w, 0), right_w, right_h, facecolor="#f2cf73", edgecolor="black", lw=lw(1.1)))
    draw_dimension(ax, (0, height), (left_w, height), str(spec.get("left_width") or format_number(left_w)), (0, height * 0.08))
    draw_dimension(ax, (left_w, height), (width, height), str(spec.get("right_width") or format_number(right_w)), (0, height * 0.08))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_rectangle_point_triangle(spec, output_path):
    width = parse_length(spec.get("width"), 10)
    height = parse_length(spec.get("height"), 6)
    top_dist = parse_length(spec.get("point_top_distance"), width * 0.45)
    right_dist = parse_length(spec.get("point_right_distance"), height * 0.45)
    p = point_item("P", top_dist, height)
    b = point_item("B", width, height)
    q = point_item("Q", width, height - right_dist)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.add_patch(plt.Polygon([(p["x"], p["y"]), (b["x"], b["y"]), (q["x"], q["y"])],
                             closed=True, facecolor="#f4c7b8", edgecolor="#8c5a4a", lw=lw(1.0), alpha=0.6))
    draw_dimension(ax, (0, height), (width, height), length_label(spec.get("width"), width), (0, height * 0.1))
    draw_dimension(ax, (0, 0), (0, height), length_label(spec.get("height"), height), (-width * 0.08, 0))
    draw_dimension(ax, (0, height), (top_dist, height), length_label(spec.get("point_top_distance"), top_dist), (0, height * 0.22))
    draw_dimension(ax, (width, height), (width, height - right_dist), length_label(spec.get("point_right_distance"), right_dist), (width * 0.1, 0))
    plot_labeled_points(ax, [p, b, q])
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_moving_points_rectangle_triangle(spec, output_path):
    width = parse_length(spec.get("rectangle_width"), 10)
    height = parse_length(spec.get("rectangle_height"), 15)
    p_speed = parse_length(spec.get("point_p_speed"), 1)
    q_speed = parse_length(spec.get("point_q_speed"), 2)
    total_speed = max(p_speed + q_speed, 1)
    p_x = width * min(0.72, max(0.22, p_speed / total_speed))
    q_y = height * min(0.78, max(0.25, q_speed / total_speed))
    points = [point_item("P", p_x, height), point_item("C", width, 0), point_item("Q", width, q_y)]
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.add_patch(plt.Polygon([(p_x, height), (width, 0), (width, q_y)],
                             closed=True, facecolor="#f4c7b8", edgecolor="#8c5a4a", lw=lw(1.0), alpha=0.6))
    draw_dimension(ax, (0, height), (width, height), length_label(spec.get("rectangle_width"), width), (0, height * 0.1))
    draw_dimension(ax, (0, 0), (0, height), length_label(spec.get("rectangle_height"), height), (-width * 0.08, 0))
    time_label = str(spec.get("time_label") or "x")
    ax.annotate(length_label(spec.get("point_p_speed"), p_speed) + time_label,
                xy=(p_x * 0.5, height), xytext=(p_x * 0.5, height * 1.12),
                arrowprops=dict(arrowstyle="->", lw=lw(0.9)), fontsize=fs(8), ha="center", va="center")
    ax.annotate(length_label(spec.get("point_q_speed"), q_speed) + time_label,
                xy=(width, q_y + (height - q_y) * 0.5), xytext=(width * 1.12, q_y + (height - q_y) * 0.5),
                arrowprops=dict(arrowstyle="->", lw=lw(0.9)), fontsize=fs(8), ha="center", va="center")
    plot_labeled_points(ax, points)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_right_isosceles_triangle_inner(spec, output_path, parallelogram=False):
    leg = parse_length(spec.get("leg"), 10)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, leg, leg)
    ax.add_patch(plt.Polygon([(0, 0), (leg, 0), (0, leg)], closed=True,
                             facecolor="white", edgecolor="black", lw=lw(1.1)))
    if parallelogram:
        poly = [(leg * 0.18, 0), (leg * 0.58, 0), (leg * 0.42, leg * 0.42), (leg * 0.02, leg * 0.42)]
        labels = ["A", "D", "E", "F"]
    else:
        poly = [(leg * 0.25, 0), (leg * 0.68, 0), (leg * 0.68, leg * 0.32), (leg * 0.25, leg * 0.32)]
        labels = ["P", "Q", "C", "R"]
    ax.add_patch(plt.Polygon(poly, closed=True, facecolor="#d9ecff", edgecolor="#1f77b4", lw=lw(1.0), alpha=0.75))
    for label, (x, y) in zip(labels, poly):
        ax.text(x, y, label, fontsize=fs(8), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=fs(0.5)))
    draw_dimension(ax, (0, 0), (leg, 0), length_label(spec.get("leg"), leg), (0, -leg * 0.1))
    draw_dimension(ax, (0, 0), (0, leg), length_label(spec.get("leg"), leg), (-leg * 0.1, 0))
    ax.text(0, -leg * 0.06, "A", fontsize=fs(9), ha="center", va="top")
    ax.text(leg, -leg * 0.06, "B", fontsize=fs(9), ha="center", va="top")
    ax.text(0, leg + leg * 0.04, "C", fontsize=fs(9), ha="center", va="bottom")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_tiled_rectangle_corner_square(spec, output_path):
    rows = int(parse_number(spec.get("tile_rows"), 3))
    cols = int(parse_number(spec.get("tile_cols"), 5))
    side = parse_length(spec.get("small_square_side"), 4)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, cols * side, rows * side)
    for r in range(rows):
        for c in range(cols):
            color = "#f4c7b8" if r == 0 and c == cols - 1 else "white"
            ax.add_patch(plt.Rectangle((c * side, r * side), side, side,
                                       facecolor=color, edgecolor="black", lw=lw(0.8)))
    ax.text((cols - 0.5) * side, side * 0.5, str(spec.get("small_square_side") or format_number(side)),
            fontsize=fs(8), ha="center", va="center")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_circle_template(spec, output_path, semicircles=False):
    outer = parse_length(spec.get("outer_radius") or spec.get("outer_diameter"), 6)
    if semicircles or spec.get("outer_diameter"):
        radius = outer / 2
    else:
        radius = outer
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, radius * 2, radius * 2)
    center = (radius, radius)
    outer_color = "#d9ecff"
    inner_color = "white"
    if str(spec.get("shade", "")).strip().lower() == "inner":
        outer_color = "white"
        inner_color = "#d9ecff"
    ax.add_patch(plt.Circle(center, radius, facecolor=outer_color, edgecolor="black", lw=lw(1.1), alpha=0.7))
    if semicircles:
        left_diameter = parse_length(spec.get("left_inner_diameter"), radius)
        right_diameter = parse_length(spec.get("right_inner_diameter"), radius)
        left_radius = min(left_diameter / 2, radius)
        right_radius = min(right_diameter / 2, radius)
        ax.add_patch(plt.Circle((left_radius, radius), left_radius, facecolor="white", edgecolor="#777777", lw=lw(1.0)))
        ax.add_patch(plt.Circle((radius * 2 - right_radius, radius), right_radius, facecolor="white", edgecolor="#777777", lw=lw(1.0)))
    else:
        if spec.get("inner_radius"):
            inner_radius = parse_length(spec.get("inner_radius"), radius * 0.55)
        else:
            gap = parse_length(spec.get("radius_gap"), radius * 0.45)
            inner_radius = max(radius - gap, radius * 0.2)
        ax.add_patch(plt.Circle(center, inner_radius, facecolor=inner_color, edgecolor="#777777", lw=lw(1.0)))
        ax.plot([center[0], center[0] + radius], [center[1], center[1]], color="#777777", lw=lw(0.8))
        ax.text(center[0] + radius * 0.52, center[1] + radius * 0.08,
                length_label(spec.get("outer_radius"), radius), fontsize=fs(8), ha="center", va="bottom")
    ax.axis("off")
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
    elif template == "parabola_shift_from_base":
        unsupported = render_parabola_shift_from_base(spec, output_path)
    elif template == "two_parabolas_same_width_horizontal_chord":
        unsupported = render_two_parabolas_same_width_horizontal_chord(spec, output_path)
    elif template == "two_origin_parabolas_parallelogram":
        unsupported = render_two_origin_parabolas_parallelogram(spec, output_path)
    elif template == "parabola_diamond_on_axes":
        unsupported = render_parabola_diamond_on_axes(spec, output_path)
    elif template == "two_parabolas_square":
        unsupported = render_two_parabolas_square(spec, output_path)
    elif template == "two_parabolas_shared_vertex_intersections":
        unsupported = render_two_parabolas_shared_vertex_intersections(spec, output_path)
    elif template == "line_to_parabola_quadrant_match":
        unsupported = render_line_to_parabola_quadrant_match(spec, output_path)
    elif template == "parabola_vertex_yintercept_origin_triangle":
        unsupported = render_parabola_vertex_yintercept_origin_triangle(spec, output_path)
    elif template == "parabola_xintercepts_vertex_yintercept_quadrilateral":
        unsupported = render_parabola_xintercepts_vertex_yintercept_quadrilateral(spec, output_path)
    elif template == "parabola_yaxis_xpositive_parallelogram":
        unsupported = render_parabola_yaxis_xpositive_parallelogram(spec, output_path)
    elif template == "parabola_point_xaxis_triangle":
        unsupported = render_parabola_point_xaxis_triangle(spec, output_path)
    elif template == "parabola_line_intersections_triangle":
        unsupported = render_parabola_line_intersections_triangle(spec, output_path)
    elif template == "two_parabolas_lens_rectangle":
        unsupported = render_two_parabolas_lens_rectangle(spec, output_path)
    elif template == "parabola_four_family_origin":
        unsupported = render_parabola_four_family_origin(spec, output_path)
    elif template == "parabola_axis_values":
        unsupported = render_parabola_axis_values(spec, output_path)
    elif template == "quadratic_motion_height":
        unsupported = render_quadratic_motion_height(spec, output_path)
    elif template == "parabolic_water_cross_section":
        unsupported = render_parabolic_water_cross_section(spec, output_path)
    elif template == "stacked_blocks_pattern":
        unsupported = render_stacked_blocks_pattern(spec, output_path)
    elif template == "linear_basic_intercepts":
        unsupported = render_linear_basic_intercepts(spec, output_path)
    elif template == "linear_point_guides":
        unsupported = render_linear_point_guides(spec, output_path)
    elif template == "linear_axis_triangle":
        unsupported = render_linear_axis_triangle(spec, output_path)
    elif template == "linear_two_lines_region":
        unsupported = render_linear_two_lines_region(spec, output_path)
    elif template == "linear_square_under_line":
        unsupported = render_linear_square_under_line(spec, output_path)
    elif template == "grid_number_table":
        unsupported = render_grid_number_table(spec, output_path)
    elif template == "tiled_rectangles_layout":
        unsupported = render_tiled_rectangles_layout(spec, output_path)
    elif template == "regular_polygon_chain":
        unsupported = render_regular_polygon_chain(spec, output_path)
    elif template == "rectangle_side_point_triangle":
        unsupported = render_rectangle_side_point_triangle(spec, output_path)
    elif template == "rectangle_cut_corner":
        unsupported = render_rectangle_cut_corner(spec, output_path)
    elif template == "rectangle_expanding_sides":
        unsupported = render_rectangle_expanding_sides(spec, output_path)
    elif template == "three_semicircles":
        unsupported = render_three_semicircles(spec, output_path)
    elif template == "folded_rectangle_overlap":
        unsupported = render_folded_rectangle_overlap(spec, output_path)
    elif template == "square_internal_rectangles":
        unsupported = render_square_internal_rectangles(spec, output_path)
    elif template == "regular_polygon_diagonals":
        unsupported = render_regular_polygon_diagonals(spec, output_path)
    elif template == "linear_parallel_lines":
        unsupported = render_linear_parallel_lines(spec, output_path)
    elif template == "multiple_choice_linear_position":
        unsupported = render_multiple_choice_linear_position(spec, output_path)
    elif template == "annulus_radius_increase":
        unsupported = render_annulus_radius_increase(spec, output_path)
    elif template == "rectangle_u_shaped_path":
        unsupported = render_rectangle_u_shaped_path(spec, output_path)
    elif template == "linear_vertical_line_triangle":
        unsupported = render_linear_vertical_line_triangle(spec, output_path)
    elif template == "parallelogram_diagonal_intersection":
        unsupported = render_parallelogram_diagonal_intersection(spec, output_path)
    elif template == "collinear_two_squares":
        unsupported = render_collinear_two_squares(spec, output_path)
    elif template == "square_cut_and_shift":
        unsupported = render_square_cut_and_shift(spec, output_path)
    elif template == "rectangle_square_similar_split":
        unsupported = render_rectangle_square_similar_split(spec, output_path)
    elif template == "nested_rectangles_frame":
        unsupported = render_nested_rectangles_frame(spec, output_path)
    elif template == "triangular_dot_pattern":
        unsupported = render_dot_pattern(spec, output_path, triangular=True)
    elif template == "rectangular_dot_pattern":
        unsupported = render_dot_pattern(spec, output_path, triangular=False)
    elif template == "annulus_area":
        unsupported = render_circle_template(spec, output_path)
    elif template == "circle_with_two_semicircles":
        unsupported = render_circle_template(spec, output_path, semicircles=True)
    elif template == "rectangle_point_triangle":
        unsupported = render_rectangle_point_triangle(spec, output_path)
    elif template == "rectangle_cross_road":
        unsupported = render_rectangle_cross_road(spec, output_path)
    elif template == "rectangle_slanted_cross_road":
        unsupported = render_rectangle_cross_road(spec, output_path, slanted=True)
    elif template == "rectangle_multi_slanted_roads":
        unsupported = render_rectangle_cross_road(spec, output_path, slanted=True, multi=True)
    elif template == "square_expanded_garden":
        unsupported = render_square_expanded_garden(spec, output_path)
    elif template == "rectangular_park_border":
        unsupported = render_rectangular_park_border(spec, output_path)
    elif template == "rectangle_diagonal_flower_path":
        unsupported = render_rectangle_diagonal_flower_path(spec, output_path)
    elif template in ("two_squares_on_segment", "two_squares_from_segment"):
        unsupported = render_two_squares_on_segment(spec, output_path)
    elif template == "growing_rectangle":
        unsupported = render_growing_rectangle(spec, output_path)
    elif template == "open_box_net_equal_cuts":
        unsupported = render_open_box_net(spec, output_path)
    elif template == "open_box_net_rectangular_paper":
        unsupported = render_open_box_net(spec, output_path, rectangular=True)
    elif template == "folded_tray":
        unsupported = render_folded_tray(spec, output_path)
    elif template == "adjacent_rectangles":
        unsupported = render_adjacent_rectangles(spec, output_path)
    elif template == "moving_points_rectangle_triangle":
        unsupported = render_moving_points_rectangle_triangle(spec, output_path)
    elif template == "right_isosceles_triangle_inner_rectangle":
        unsupported = render_right_isosceles_triangle_inner(spec, output_path)
    elif template == "right_isosceles_triangle_parallelogram":
        unsupported = render_right_isosceles_triangle_inner(spec, output_path, parallelogram=True)
    elif template == "tiled_rectangle_corner_square":
        unsupported = render_tiled_rectangle_corner_square(spec, output_path)
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
