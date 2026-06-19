import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "math_diagram_renderer_mpl"))

import matplotlib
from matplotlib.patches import Wedge
from matplotlib.font_manager import FontProperties, fontManager

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from PIL import Image, ImageDraw, ImageFont


FIGURE_SIZE_INCHES = (1.8, 1.3)  # 720 x 520 px at 400 dpi; quarter-size in HWP/print layout.
GEOMETRY_SIZE_INCHES = (1.5, 1.5)
CHOICE_FIGURE_SIZE_INCHES = (1.8, 2.2)
OUTPUT_DPI = 400
STYLE_SCALE = 200 / OUTPUT_DPI
KOREAN_FONT_PATH = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "malgun.ttf"
KOREAN_FONT = FontProperties(fname=str(KOREAN_FONT_PATH)) if KOREAN_FONT_PATH.exists() else None
PIL_KOREAN_FONT_PATH = KOREAN_FONT_PATH


def select_math_font():
    installed = {font.name for font in fontManager.ttflist}
    for family in (
        "Cambria Math",
        "Latin Modern Math",
        "STIX Two Math",
        "Times New Roman",
    ):
        if family in installed:
            return family
    return matplotlib.rcParams.get("font.family", ["sans-serif"])[0]


MATH_FONT_FAMILY = select_math_font()
matplotlib.rcParams.update(
    {
        "mathtext.fontset": "custom",
        "mathtext.rm": MATH_FONT_FAMILY,
        "mathtext.it": f"{MATH_FONT_FAMILY}:italic",
        "mathtext.bf": MATH_FONT_FAMILY,
        "mathtext.default": "it",
    }
)

_ORIGINAL_AXES_TEXT = Axes.text
_ORIGINAL_AXES_ANNOTATE = Axes.annotate
_HANGUL_RE = re.compile(r"[가-힣]")
_KOREAN_TEXT_RE = re.compile(r"[가-힣\u3130-\u318f\u3200-\u321e\u3260-\u327f]")
_MATH_SIGNAL_RE = re.compile(
    r"[A-Za-zθπ∠]|(?:\d|\))\s*[,=+\-*/^]|[=+\-*/^]\s*(?:\d|\()"
)
_MIXED_MATH_RE = re.compile(
    r"(?<![A-Za-z가-힣])"
    r"(∠?[A-Za-zθπ](?:\s*\([^()\n]*\))?"
    r"(?:\s*(?:=|[+\-*/^])\s*[A-Za-z0-9θπ().,+\-*/^]+)*)"
)


def _mathtext_body(value):
    text = str(value)
    text = text.replace("−", "-")
    text = text.replace("²", "^{2}").replace("³", "^{3}").replace("⁴", "^{4}")
    text = re.sub(r"\*\*(\d+)", r"^{\1}", text)
    text = re.sub(r"\^(\-?\d+)", r"^{\1}", text)
    text = text.replace("∠", r"\angle ")
    text = text.replace("θ", r"\theta ").replace("π", r"\pi ")
    text = text.replace("×", r"\times ").replace("÷", r"\div ")
    text = re.sub(r"([A-Za-z])([₀₁₂₃₄₅₆₇₈₉]+)", _unicode_subscript_repl, text)
    text = re.sub(r"(?:kcal|cm|mm|km)(?![A-Za-z])", _unit_repl, text)
    return text


def _unicode_subscript_repl(match):
    table = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
    return f"{match.group(1)}_{{{match.group(2).translate(table)}}}"


def _unit_repl(match):
    return r"\mathrm{" + match.group(0) + "}"


def format_math_text(value):
    if not isinstance(value, str) or not value or "$" in value:
        return value
    if not _MATH_SIGNAL_RE.search(value):
        return value

    leading = value[: len(value) - len(value.lstrip())]
    trailing = value[len(value.rstrip()) :]
    core = value.strip()
    if not core:
        return value

    if not _HANGUL_RE.search(core):
        return leading + "$" + _mathtext_body(core) + "$" + trailing

    def replace_mixed(match):
        return "$" + _mathtext_body(match.group(1)) + "$"

    return _MIXED_MATH_RE.sub(replace_mixed, value)


def math_styled_text(self, x, y, s, *args, **kwargs):
    formatted = format_math_text(s)
    if isinstance(formatted, str) and "$" in formatted:
        kwargs.setdefault("math_fontfamily", "stix")
    elif KOREAN_FONT is not None and isinstance(formatted, str) and _KOREAN_TEXT_RE.search(formatted):
        kwargs.setdefault("fontproperties", KOREAN_FONT)
    return _ORIGINAL_AXES_TEXT(self, x, y, formatted, *args, **kwargs)


def math_styled_annotate(self, text, *args, **kwargs):
    formatted = format_math_text(text)
    if isinstance(formatted, str) and "$" in formatted:
        kwargs.setdefault("math_fontfamily", "stix")
    elif KOREAN_FONT is not None and isinstance(formatted, str) and _KOREAN_TEXT_RE.search(formatted):
        kwargs.setdefault("fontproperties", KOREAN_FONT)
    return _ORIGINAL_AXES_ANNOTATE(
        self, formatted, *args, **kwargs
    )


Axes.text = math_styled_text
Axes.annotate = math_styled_annotate


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
    blocks = []
    pattern = re.compile(r"(?m)(\[)?IMAGE_PROMPT\s*(?:\((\d+)\)|(\d+))?\s*:")
    search_from = 0
    while True:
        match = pattern.search(text, search_from)
        if not match:
            break
        has_bracket = bool(match.group(1))
        cursor = match.end()
        if not has_bracket:
            lines = []
            line_start = cursor
            while line_start < len(text):
                line_end = text.find("\n", line_start)
                if line_end < 0:
                    line_end = len(text)
                line = text[line_start:line_end]
                stripped = line.strip()
                if not stripped:
                    if lines:
                        break
                    line_start = line_end + 1
                    continue
                if not re.match(r"^[A-Za-z0-9_가-힣]+\s*=", stripped):
                    break
                lines.append(line)
                line_start = line_end + 1
            if lines:
                tag_number = int(match.group(2) or match.group(3)) if (match.group(2) or match.group(3)) else len(blocks) + 1
                blocks.append((tag_number, "\n".join(lines).strip()))
            search_from = max(line_start, match.end())
            continue

        depth = 1
        quote = ""
        escaped = False
        while cursor < len(text):
            char = text[cursor]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote:
                if char == quote:
                    quote = ""
            elif char in ('"', "'"):
                quote = char
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if depth != 0:
            break
        tag_number = int(match.group(2) or match.group(3)) if (match.group(2) or match.group(3)) else len(blocks) + 1
        blocks.append((tag_number, text[match.end():cursor].strip()))
        search_from = cursor + 1
    return blocks


def parse_key_values(block):
    stripped = str(block or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
            data = {str(key): str(value) for key, value in parsed.items()}
            if "imageTemplate" in data and "template" not in data:
                data["template"] = data["imageTemplate"]
            return data
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    data = {}
    pending_key = ""
    pending_value = ""
    last_collection_key = ""

    collection_keys = {"points", "coordinates", "label_coords", "text_labels"}

    def is_balanced(text):
        pairs = {"(": ")", "[": "]", "{": "}"}
        stack = []
        for char in str(text):
            if char in pairs:
                stack.append(pairs[char])
            elif char in pairs.values():
                if stack and stack[-1] == char:
                    stack.pop()
        return not stack

    def commit_pending():
        nonlocal pending_key, pending_value, last_collection_key
        if pending_key:
            data[pending_key.strip()] = pending_value.strip()
            if pending_key.strip().lower() in collection_keys:
                last_collection_key = pending_key.strip()
            pending_key = ""
            pending_value = ""

    def looks_like_collection_continuation(key, value):
        key_text = str(key or "").strip()
        value_text = str(value or "").strip()
        if not last_collection_key:
            return False
        if last_collection_key.lower() in {"points", "coordinates", "label_coords"}:
            return bool(re.match(r"^[A-Z][A-Z0-9_]{0,5}$", key_text)) and bool(
                re.match(r"^\s*(?:\(|\[)?\s*-?\d+(?:\.\d+)?\s*,", value_text)
            )
        if last_collection_key.lower() == "text_labels":
            return bool(re.match(r"^[A-Z][A-Z0-9_]{0,5}$", key_text))
        return False

    for raw_line in block.replace("\\n", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if pending_key and not is_balanced(pending_value):
            pending_value += " " + line
            if is_balanced(pending_value):
                commit_pending()
            continue
        equals_index = line.find("=")
        colon_index = line.find(":")
        if equals_index < 0:
            separator = ":" if colon_index >= 0 else ""
        elif colon_index < 0:
            separator = "="
        else:
            separator = ":" if colon_index < equals_index else "="
        if not separator:
            continue
        commit_pending()
        key, value = line.split(separator, 1)
        if looks_like_collection_continuation(key, value):
            data[last_collection_key] = (str(data.get(last_collection_key, "")).strip() + " " + line).strip()
            continue
        last_collection_key = ""
        pending_key = key
        pending_value = value
        if is_balanced(pending_value):
            commit_pending()
    commit_pending()
    if "imageTemplate" in data and "template" not in data:
        data["template"] = data["imageTemplate"]
    return data


def validate_spec_structure(spec, block):
    warnings = []
    if "≠uation" in block:
        warnings.append("image spec appears to have a corrupted equation key")
    for key, value in spec.items():
        if "≠" in key or "≠uation" in value:
            warnings.append(f"corrupted key/value near {key}")
    template = str(spec.get("template", "")).strip().lower()
    required_fields = {
        "past_exam_image": ("source_id",),
        "parabola_basic_shape": ("equation",),
        # The renderer already falls back to the equation text for the label.
        "parabola_labeled_xintercepts": ("equation",),
        "parabola_family_origin": ("equations",),
        "parabola_vertex_yintercept_origin_triangle": ("vertex", "y_intercept"),
        "multiple_choice_parabola_position": ("choices",),
        "three_semicircles": ("diameter", "split"),
        "circle_with_two_semicircles": (
            "outer_diameter", "left_inner_diameter", "right_inner_diameter"
        ),
        "unit_quarter_circle_trig": ("angle",),
        "parabola_inscribed_square": ("equation", "x_left", "x_right", "y_bottom"),
        "two_parabolas_axis_aligned_square": (
            "equation_left", "equation_right", "square_side"
        ),
        "parabola_origin_two_points": ("point1_x", "point1_y", "point2_x"),
        "two_parabolas_vertical_segment": ("equation_top", "equation_bottom", "vertical_x"),
        "square_side_points_trapezoid": ("ae", "df"),
        "coordinate_parallelogram": ("points",),
        "two_origin_parabolas_parallelogram": ("equation1", "equation2", "vertical_x"),
        "two_origin_parabolas_vertical_line_ratio": ("equation1", "equation2", "vertical_x"),
        "parabola_yaxis_xpositive_parallelogram": ("equation", "y_axis_y"),
        "two_parabolas_shared_vertex_intersections": ("equation1", "equation2"),
        "rectangle_square_similar_split": ("width", "height", "square_side"),
        "open_box_net_rectangular_paper": ("paper_width", "paper_height", "cut_side"),
        "open_box_net_equal_cuts": ("paper_side", "cut_side"),
        "moving_points_rectangle_triangle": (
            "rectangle_width", "rectangle_height", "point_p_speed", "point_q_speed"
        ),
        "moving_points_right_triangle": (
            "vertical_leg", "horizontal_leg", "point_p_speed", "point_q_speed"
        ),
        "activity_calorie_table": ("activities", "calories_per_10min"),
        "linear_sign_diagram": ("slope_sign", "y_intercept_sign"),
        "regular_polygon_chain_sequence": ("sides", "side", "stage_counts"),
        "moving_point_rectangle_trapezoid": (
            "rectangle_width", "rectangle_height", "point_speed"
        ),
        "linear_vertical_line_position": ("x_value",),
        "linear_two_lines_labeled_points": (
            "equation1", "equation2", "point_a_x", "point_b_x"
        ),
        "linear_two_lines_xaxis_square": ("equation_left", "equation_right"),
    }
    missing = [field for field in required_fields.get(template, ()) if not spec.get(field)]
    if template == "past_exam_image" and missing and get_past_exam_source_id(spec):
        missing = []
    if missing:
        warnings.append(f"{template} missing required fields: {', '.join(missing)}")
    for field in ("x_range", "y_range"):
        if spec.get(field) and not re.match(
            r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$",
            str(spec[field]),
        ):
            warnings.append(f"{field} must be numeric min,max")
    return warnings


def get_past_exam_source_id(spec):
    for key in (
        "source_id",
        "past_exam_image_id",
        "pastExamImageId",
        "exam_image_id",
        "기출이미지ID",
    ):
        value = str(spec.get(key, "")).strip()
        if value:
            return value
    return ""


def get_past_exam_library_roots(spec):
    roots = []
    for value in (
        spec.get("library_root"),
        spec.get("libraryRoot"),
        spec.get("라이브러리경로"),
        os.environ.get("PAST_EXAM_IMAGE_LIBRARY"),
    ):
        if value:
            roots.append(Path(str(value).strip()))

    executable_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    cwd = Path.cwd()
    roots.extend(
        [
            executable_dir / "library",
            executable_dir / "past_exam_image_library",
            executable_dir / "past_exam_image_builder" / "dist" / "library",
            executable_dir.parent / "past_exam_image_builder" / "dist" / "library",
            cwd / "library",
            cwd / "past_exam_image_library",
            cwd / "past_exam_image_builder" / "dist" / "library",
            cwd.parent / "past_exam_image_builder" / "dist" / "library",
            Path(__file__).resolve().parent / "past_exam_image_builder" / "dist" / "library",
        ]
    )

    unique = []
    seen = set()
    for root in roots:
        try:
            resolved = root.expanduser().resolve()
        except OSError:
            resolved = root.expanduser()
        if str(resolved).lower() in seen:
            continue
        seen.add(str(resolved).lower())
        unique.append(resolved)
    return unique


def find_past_exam_entry_dir(spec):
    source_id = get_past_exam_source_id(spec)
    if not source_id:
        return None, "past_exam_image missing source_id"
    safe_source_id = re.sub(r'[<>:"/\\|?*]+', "_", source_id).strip(" .")
    checked = []
    for root in get_past_exam_library_roots(spec):
        candidate = root / safe_source_id
        checked.append(str(candidate))
        if (candidate / "recipe.json").exists() and (candidate / "original.png").exists():
            return candidate, ""
        candidate = root / source_id
        checked.append(str(candidate))
        if (candidate / "recipe.json").exists() and (candidate / "original.png").exists():
            return candidate, ""
    return None, "past_exam_image source not found: " + source_id


def load_pil_font(size):
    candidates = [
        PIL_KOREAN_FONT_PATH,
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(str(candidate), max(8, int(size)))
    return ImageFont.load_default()


def parse_past_exam_values(spec):
    reserved = {
        "template", "type", "source_id", "past_exam_image_id", "pastExamImageId",
        "exam_image_id", "기출이미지ID", "library_root", "libraryRoot", "라이브러리경로",
        "values", "overlays",
    }
    values = {
        str(key).strip(): str(value).strip()
        for key, value in spec.items()
        if str(key).strip() not in reserved and str(value).strip()
    }

    packed_values = str(spec.get("values") or spec.get("overlays") or "").strip()
    if packed_values:
        for item in re.split(r"\s*[;,]\s*", packed_values):
            if not item or "=" not in item:
                continue
            key, value = item.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def render_past_exam_image(spec, output_path):
    entry_dir, error = find_past_exam_entry_dir(spec)
    if error:
        return [error]

    recipe_path = entry_dir / "recipe.json"
    original_path = entry_dir / "original.png"
    try:
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"past_exam_image bad recipe: {exc}"]

    overlays = recipe.get("overlays") or []
    values = parse_past_exam_values(spec)
    if not overlays:
        shutil.copy2(original_path, output_path)
        return []

    image = Image.open(original_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    missing = []
    for overlay in overlays:
        name = str(overlay.get("name", "")).strip()
        if not name:
            continue
        value = values.get(name)
        if value is None:
            value = str(overlay.get("original", "")).strip()
            missing.append(name)
        value = format_pil_overlay_text(value)
        try:
            box = tuple(int(float(item)) for item in overlay.get("box", []))
        except (TypeError, ValueError):
            continue
        if len(box) != 4:
            continue
        draw.rectangle(box, fill="white")
        font = load_pil_font(overlay.get("font_size", 28))
        text_box = draw.textbbox((0, 0), value, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        x = box[0] + max(0, (box[2] - box[0] - text_width) / 2)
        y = box[1] + max(0, (box[3] - box[1] - text_height) / 2) - text_box[1]
        draw.text((x, y), value, fill=str(overlay.get("color") or "#000000"), font=font)

    image.save(output_path)
    if missing:
        return ["past_exam_image used original values for missing overlays: " + ", ".join(missing)]
    return []


def format_pil_overlay_text(value):
    text = str(value or "")
    vulgar_fractions = {
        "1/2": "½",
        "1/3": "⅓",
        "2/3": "⅔",
        "1/4": "¼",
        "3/4": "¾",
        "1/5": "⅕",
        "2/5": "⅖",
        "3/5": "⅗",
        "4/5": "⅘",
        "1/6": "⅙",
        "5/6": "⅚",
        "1/8": "⅛",
        "3/8": "⅜",
        "5/8": "⅝",
        "7/8": "⅞",
    }
    for source, target in sorted(vulgar_fractions.items(), key=lambda item: -len(item[0])):
        text = re.sub(rf"(?<![\d/]){re.escape(source)}(?![\d/])", target, text)
    superscripts = str.maketrans({
        "0": "⁰",
        "1": "¹",
        "2": "²",
        "3": "³",
        "4": "⁴",
        "5": "⁵",
        "6": "⁶",
        "7": "⁷",
        "8": "⁸",
        "9": "⁹",
        "-": "⁻",
    })

    def exponent_repl(match):
        return match.group(1).translate(superscripts)

    text = re.sub(r"\^\{([^{}]+)\}", exponent_repl, text)
    text = re.sub(r"\^(-?\d+)", exponent_repl, text)
    text = text.replace("**2", "²").replace("**3", "³").replace("**4", "⁴")
    text = text.replace("*", "")
    return text


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
    text = re.sub(r"(\d)([xy])", r"\1*\2", text)
    text = re.sub(r"(\d)(\()", r"\1*\2", text)
    text = re.sub(r"(\))(\()", r"\1*\2", text)
    text = re.sub(r"(\))([xy\d])", r"\1*\2", text)
    text = re.sub(r"(x)(\()", r"\1*\2", text)
    return text


def format_display_math(value):
    text = str(value or "").strip()
    text = text.replace("**2", "²").replace("^2", "²")
    text = text.replace("**3", "³").replace("^3", "³")
    text = text.replace("**4", "⁴").replace("^4", "⁴")
    text = text.replace("*", "")
    text = text.replace("−", "-")
    return text


def parse_equations(value):
    equations = []
    items = split_semicolon_outside_parentheses(value)
    if len(items) <= 1:
        items = split_csv_outside_parentheses(value)
    for item in items:
        clean = item.strip()
        clean = clean.strip("'\"")
        clean = re.sub(r"\{\s*label\s*=\s*[^{}]+?\s*\}", "", clean, flags=re.IGNORECASE).strip()
        clean = re.sub(r"\s+label\s*=\s*[^;,\n]+$", "", clean, flags=re.IGNORECASE).strip()
        if not clean:
            continue
        unresolved_probe = clean.replace("x", "").replace("X", "").replace("y", "").replace("Y", "")
        if re.search(r"\b[a-wzA-WZ]\b", unresolved_probe):
            equations.append({"raw": clean, "kind": "unsupported", "reason": "unresolved variable"})
            continue
        if clean.startswith("y=") or clean.startswith("y ="):
            rhs = clean.split("=", 1)[1]
            equations.append({"raw": clean, "kind": "y", "expr": normalize_expr(rhs)})
        elif "=" in clean and re.search(r"[xyXY]", clean):
            left, right = clean.split("=", 1)
            try:
                a, b, c = linear_coefficients(clean)
                if abs(b) > 1e-9:
                    equations.append({"raw": clean, "kind": "y", "expr": f"(-({a})*x-({c}))/({b})"})
                elif abs(a) > 1e-9:
                    equations.append({"raw": clean, "kind": "x", "value": -c / a})
                elif normalize_expr(right) == "0":
                    equations.append({"raw": "y = " + left.strip(), "kind": "y", "expr": normalize_expr(left)})
                else:
                    equations.append({"raw": clean, "kind": "unsupported", "reason": "not explicit y= or x="})
            except Exception:
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


def parse_points(value):
    if not value:
        return []
    lower_value = str(value).lower()
    if "given" in lower_value or "problem" in lower_value:
        return []
    points = []
    pattern = re.compile(
        r"(?:(?P<prefix>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=|:)?\s*)?"
        r"\(\s*(?P<x>-?\d+(?:\.\d+)?)\s*,\s*(?P<y>-?\d+(?:\.\d+)?)"
        r"(?:\s*,\s*['\"]?(?P<suffix>[A-Za-z_][A-Za-z0-9_]*)['\"]?)?(?:\s+[^)]*)?\s*\)"
        r"\s*(?::\s*|label\s*=\s*|\{\s*)?(?P<postfix>[A-Za-z_][A-Za-z0-9_]*|-?\d+)?\s*\}?"
        r"|(?P<bracket_label>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*"
        r"\[\s*(?P<bracket_x>-?\d+(?:\.\d+)?)\s*,\s*(?P<bracket_y>-?\d+(?:\.\d+)?)\s*\]"
        r"|\[\s*(?P<plain_bracket_x>-?\d+(?:\.\d+)?)\s*,\s*(?P<plain_bracket_y>-?\d+(?:\.\d+)?)\s*\]"
    )
    for match in pattern.finditer(value):
        label = (
            match.group("prefix")
            or match.group("suffix")
            or match.group("postfix")
            or match.group("bracket_label")
            or ""
        )
        x_value = match.group("x") or match.group("bracket_x") or match.group("plain_bracket_x")
        y_value = match.group("y") or match.group("bracket_y") or match.group("plain_bracket_y")
        points.append({
            "label": label,
            "x": float(x_value),
            "y": float(y_value),
        })
    return points


def parse_labels(value):
    labels = []
    raw = str(value or "").strip()
    if raw.startswith("[") or raw.startswith("{"):
        return labels
    items = split_semicolon_outside_parentheses(raw) if ";" in raw else split_csv_outside_parentheses(raw)
    for item in items:
        label = item.strip()
        if not label or label.lower() == "none" or label.startswith("("):
            continue
        labels.append(label)
    return labels


def safe_eval(expr, x):
    return eval(expr, {"__builtins__": {}}, {"x": x, **SAFE_FUNCS})


def parse_y_equation(raw):
    text = str(raw or "").strip().strip("'\"")
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
        ax.text(
            xmax, 0, r"  $x$", ha="left", va="center", fontsize=fs(10),
            math_fontfamily="stix", clip_on=False
        )
    if xmin <= 0 <= xmax:
        ax.annotate("", xy=(0, ymax), xytext=(0, ymin), arrowprops=axis_arrow, zorder=3)
        ax.text(
            0, ymax, r"$y$", ha="center", va="bottom", fontsize=fs(10),
            math_fontfamily="stix", clip_on=False
        )
    if xmin <= 0 <= xmax and ymin <= 0 <= ymax:
        ax.text(
            0, 0, r" $O$", ha="left", va="top", fontsize=fs(10),
            math_fontfamily="stix", zorder=6
        )

    ax.grid(False)
    ax.set_aspect("auto")


def redraw_axes_in_front(ax, x_range, y_range):
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
        ax.annotate("", xy=(xmax, 0), xytext=(xmin, 0), arrowprops=axis_arrow, zorder=20)
        ax.text(
            xmax, 0, r"  $x$", ha="left", va="center", fontsize=fs(10),
            math_fontfamily="stix", clip_on=False, zorder=21
        )
    if xmin <= 0 <= xmax:
        ax.annotate("", xy=(0, ymax), xytext=(0, ymin), arrowprops=axis_arrow, zorder=20)
        ax.text(
            0, ymax, r"$y$", ha="center", va="bottom", fontsize=fs(10),
            math_fontfamily="stix", clip_on=False, zorder=21
        )
    if xmin <= 0 <= xmax and ymin <= 0 <= ymax:
        ax.text(
            0, 0, r" $O$", ha="left", va="top", fontsize=fs(10),
            math_fontfamily="stix", zorder=21
        )


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
        ax.text(
            xmax, 0, r" $x$", ha="left", va="center", fontsize=fs(7),
            math_fontfamily="stix", clip_on=False
        )
    if xmin <= 0 <= xmax:
        ax.annotate("", xy=(0, ymax), xytext=(0, ymin), arrowprops=axis_arrow, zorder=3)
        ax.text(
            0, ymax, r"$y$", ha="center", va="bottom", fontsize=fs(7),
            math_fontfamily="stix", clip_on=False
        )
    if xmin <= 0 <= xmax and ymin <= 0 <= ymax:
        ax.text(
            0, 0, r" $O$", ha="left", va="top", fontsize=fs(7),
            math_fontfamily="stix", zorder=6
        )

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


def relax_parabola_graph_view(spec, equations, points, x_range, y_range):
    """Keep workbook-style parabola diagrams readable even with tight AI ranges."""
    has_quadratic = any(
        equation.get("kind") == "y" and "x**2" in equation.get("expr", "")
        for equation in equations
    )
    if not has_quadratic or StringFalse(spec.get("schematic", "true")):
        return x_range, y_range

    topology_text = " ".join(
        str(spec.get(key, "")).strip()
        for key in ("segments", "segment", "edges", "connections", "connect", "polygon", "rectangle_points")
        if str(spec.get(key, "")).strip()
    )
    if not points and not topology_text:
        return x_range, y_range

    xmin, xmax = x_range
    ymin, ymax = y_range
    x_span = max(xmax - xmin, 1.0)
    y_span = max(ymax - ymin, 1.0)
    point_ys = [0.0] + [point["y"] for point in points]
    center_y = (min(point_ys) + max(point_ys)) / 2
    y_equation_count = len([equation for equation in equations if equation.get("kind") == "y"])
    if points:
        point_xs = [0.0] + [point["x"] for point in points]
        point_x_span = max(point_xs) - min(point_xs)
        if y_equation_count <= 1 and len(points) >= 4:
            min_x_span = max(x_span, 16.0, point_x_span * 3.8)
        else:
            min_x_span = max(x_span, 13.5, point_x_span * 3.0)
        if min_x_span > x_span:
            center_x = (xmin + xmax) / 2
            xmin = center_x - min_x_span / 2
            xmax = center_x + min_x_span / 2
            x_span = min_x_span

    # School-test diagrams should show the shape first. A very tight vertical
    # window makes parabolas look like a wall and hides the overall relation.
    if y_equation_count <= 1 and len(points) >= 4:
        min_y_span = max(y_span, x_span * 2.45, (max(point_ys) - min(point_ys) + 1.0) * 4.0)
    else:
        min_y_span = max(y_span, x_span * 2.45, (max(point_ys) - min(point_ys) + 1.0) * 3.4)
    if min_y_span > y_span:
        ymin = min(ymin, center_y - min_y_span / 2)
        ymax = max(ymax, center_y + min_y_span / 2)

    # If the rectangle/points sit right at the edge, give labels and the curve
    # one more breath of space instead of cropping the educational cue.
    margin = max(x_span * 0.06, 0.35)
    if points:
        xs = [point["x"] for point in points]
        xmin = min(xmin, min(xs) - margin)
        xmax = max(xmax, max(xs) + margin)

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
                        x_candidates=None, y_candidates=None, shade_color="#cfe8d2", extra_draw=None,
                        axes_front=False):
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

    if axes_front:
        redraw_axes_in_front(ax, x_range, y_range)

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
        if str(point.get("label", "")).strip().upper() == "O" and abs(point["x"]) < 1e-9 and abs(point["y"]) < 1e-9:
            continue
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
    show_vertex = False
    show_axis_values = str(spec.get("show_axis_values", "false")).lower() == "true"
    shade_region = str(spec.get("shade_region", "false")).lower() == "true"
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
            vertex_is_y_intercept = (
                show_y
                and abs(y_intercept[0] - vertex[0]) < 1e-9
                and abs(y_intercept[1] - vertex[1]) < 1e-9
            )
            if not vertex_is_y_intercept:
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
        if shade_region:
            ax.fill([point["x"] for point in polygon], [point["y"] for point in polygon],
                    color="#f4c7b8", alpha=0.55, zorder=2)
        ax.plot([point["x"] for point in polygon + [polygon[0]]],
                [point["y"] for point in polygon + [polygon[0]]],
                color="black", lw=lw(1.0), zorder=4)

    if mode == "parabola_basic_shape" and any(point.get("label") == "V" for point in points):
        vx, vy = vertex
        if y_range[0] <= 0 <= y_range[1] and x_range[0] <= vx <= x_range[1] and abs(vy) > 1e-9:
            ax.plot([vx, vx], [0, vy], color="#777777", lw=lw(0.9), ls="--", zorder=2.5)
        if x_range[0] <= 0 <= x_range[1] and y_range[0] <= vy <= y_range[1] and abs(vx) > 1e-9:
            ax.plot([0, vx], [vy, vy], color="#777777", lw=lw(0.9), ls="--", zorder=2.5)

    show_point_labels = str(spec.get("show_point_labels", "true")).lower() != "false"
    if show_point_labels:
        plot_labeled_points(ax, points)
    else:
        for point in points:
            ax.scatter([point["x"]], [point["y"]], color="black", s=marker_area(28), zorder=6)
    x_label_side, y_label_side = axis_label_sides_for_polygon(polygon)
    y_label_side = str(spec.get("y_intercept_label_side") or y_label_side)
    if mode in (
        "parabola_xintercepts_vertex_triangle",
        "parabola_xintercepts_yintercept_triangle",
        "parabola_yintercept_vertex_xintercept_triangle",
    ):
        x_label_side = "below"
    if show_axis_values:
        for value in roots:
            annotate_axis_value(ax, x_range, y_range, "x", value, x_label_side)
    vertex_shares_y_axis = abs(vertex[0]) < 1e-9 and abs(y_intercept[1] - vertex[1]) < 1e-9
    if show_axis_values and abs(y_intercept[1]) > 1e-9 and not vertex_shares_y_axis:
        annotate_axis_value(ax, x_range, y_range, "y", y_intercept[1], y_label_side)
    if show_axis_values and show_vertex:
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
        point_item("R", vertical_x, 0),
        point_item("Q", vertical_x, ordered[0]),
        point_item("P", vertical_x, ordered[-1]),
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
    ax.plot([vertical_x, vertical_x], [0, ordered[-1]],
            color="#777777", lw=lw(1.2), zorder=2)
    for point in points:
        ax.scatter([point["x"]], [point["y"]], color="black", s=marker_area(28), zorder=6)
    ax.annotate("R", (vertical_x, 0), xytext=(7, -3), textcoords="offset points",
                ha="left", va="top", fontsize=fs(10), zorder=7)
    ax.annotate("Q", (vertical_x, ordered[0]), xytext=(6, 4), textcoords="offset points",
                ha="left", va="bottom", fontsize=fs(10), zorder=7)
    ax.annotate("P", (vertical_x, ordered[-1]), xytext=(-6, 4), textcoords="offset points",
                ha="right", va="bottom", fontsize=fs(10), zorder=7)
    curve_labels = parse_labels(spec.get("curve_labels") or "")
    if curve_labels:
        label_x = x_range[0] + (x_range[1] - x_range[0]) * 0.16
        for index, equation in enumerate((eq1, eq2)):
            if index >= len(curve_labels):
                break
            label_y = equation_value(equation, label_x)
            ax.text(label_x, label_y, format_display_math(curve_labels[index]), fontsize=fs(7),
                    ha="left", va="bottom", zorder=7)
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
    if str(spec.get("label_order") or "").lower() in ("counterclockwise", "ccw"):
        points = [
            point_item("A", -x_value, y_top),
            point_item("B", -x_value, y_bottom),
            point_item("C", x_value, y_bottom),
            point_item("D", x_value, y_top),
        ]
    else:
        points = [
            point_item("A", -x_value, y_top),
            point_item("B", x_value, y_top),
            point_item("C", x_value, y_bottom),
            point_item("D", -x_value, y_bottom),
        ]
    return render_quadratic_scene(output_path, [eq_top, eq_bottom], points=points, polygons=[points],
                                  x_candidates=[-x_value, x_value, 0], y_candidates=[y_top, y_bottom, 0])


def render_two_parabolas_axis_aligned_square(spec, output_path):
    eq_left = parse_y_equation(spec.get("equation_left") or "y=x^2")
    eq_right = parse_y_equation(spec.get("equation_right") or "y=1/2*x^2")
    side = parse_length(spec.get("square_side"), 1)
    a1, b1, c1 = quadratic_coefficients(eq_left)
    a2, b2, c2 = quadratic_coefficients(eq_right)
    roots = quadratic_roots_from_coeffs(
        a1 - a2,
        b1 - (2 * a2 * side + b2),
        c1 - side - (a2 * side * side + b2 * side + c2),
    )
    candidates = [root for root in roots if root > 0]
    warnings = []
    if not candidates:
        warnings.append("axis-aligned square needs a positive first-quadrant solution")
        x_left = 3
    else:
        x_left = min(candidates)
    y_top = equation_value(eq_left, x_left)
    x_right = x_left + side
    y_bottom = y_top - side
    points = [
        point_item("A", x_left, y_top),
        point_item("B", x_left, y_bottom),
        point_item("C", x_right, y_bottom),
        point_item("D", x_right, y_top),
    ]
    x_range = (-max(0.7, x_left * 0.22), x_right + max(1.0, side * 1.5))
    y_range = (-max(0.6, side * 0.6), y_top + max(2.0, side * 2.0))
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    ax.set_aspect("equal", adjustable="box")
    xs = np.linspace(x_range[0], x_range[1], 1200)
    ax.plot(xs, safe_eval(eq_left["expr"], xs), color="#333333", lw=lw(1.7), zorder=3)
    ax.plot(xs, safe_eval(eq_right["expr"], xs), color="#666666", lw=lw(1.7), zorder=3)
    ax.add_patch(plt.Polygon(
        [(point["x"], point["y"]) for point in points],
        closed=True, facecolor="#f4dfae", edgecolor="black", lw=lw(1.0), alpha=0.75, zorder=4
    ))
    offsets = {
        "A": (-side * 0.14, side * 0.10, "right", "bottom"),
        "B": (-side * 0.14, -side * 0.10, "right", "top"),
        "C": (side * 0.14, -side * 0.10, "left", "top"),
        "D": (side * 0.14, side * 0.10, "left", "bottom"),
    }
    for point in points:
        dx, dy, ha, va = offsets[point["label"]]
        ax.text(point["x"] + dx, point["y"] + dy, point["label"],
                fontsize=fs(8), ha=ha, va=va, zorder=6)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings


def render_two_parabolas_shared_vertex_intersections(spec, output_path):
    eq1_raw = str(spec.get("equation1") or "")
    eq2_raw = str(spec.get("equation2") or "")
    if re.search(r"\ba\b", eq1_raw) and re.search(r"\bp\b", eq1_raw):
        try:
            eq2_probe = parse_y_equation(eq2_raw or "y=-x^2+4")
            c2 = quadratic_coefficients(eq2_probe)
            vertex2 = quadratic_vertex(c2)
            if abs(vertex2[0]) < 1e-9 and vertex2[1] > 0:
                p_value = math.sqrt(vertex2[1] / abs(c2[0]))
                a_value = vertex2[1] / (p_value ** 2)
                eq1_raw = f"y={a_value}*(x-{p_value})^2"
        except Exception:
            pass
    eq1 = parse_y_equation(eq1_raw or "y=x^2-9")
    eq2 = parse_y_equation(eq2_raw or "y=-(x-3)^2")
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
        intersection = point_item(chr(ord("C") + index), root, equation_value(eq1, root))
        duplicate = any(
            abs(point["x"] - intersection["x"]) < 1e-6
            and abs(point["y"] - intersection["y"]) < 1e-6
            for point in points
        )
        if not duplicate:
            points.append(intersection)
    return warnings + render_quadratic_scene(output_path, [eq1, eq2], points=points,
                                             x_candidates=roots + [0], y_candidates=[0])


def render_line_to_parabola_quadrant_match(spec, output_path):
    line_candidates = parse_equations(spec.get("line_equation") or "y=x+1")
    line_eq = line_candidates[0] if line_candidates else parse_y_equation("y=x+1")
    parabola_eq = parse_y_equation(spec.get("parabola_equation") or spec.get("parabola_form") or "y=-(x+1)^2+1")
    x_range = parse_range(spec.get("x_range"), (-4, 4))
    y_values = y_values_for_equations([parabola_eq], x_range) + [0]
    if line_eq.get("kind") == "y":
        y_values += y_values_for_equations([line_eq], x_range)
    y_range = pad_range(min(y_values), max(y_values), 0.18, 2.0)
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    x = np.linspace(x_range[0], x_range[1], 1200)
    if line_eq.get("kind") == "x":
        ax.axvline(line_eq["value"], color="#555555", lw=lw(1.5), zorder=3)
    else:
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
    equation = parse_y_equation(spec.get("equation") or "y=1/3*x^2")
    coeffs = quadratic_coefficients(equation)
    y_axis_y = parse_number(spec.get("y_axis_y"), 12)
    level_roots = positive_roots_for_y_level(equation, y_axis_y)
    right_x = max(level_roots) if level_roots else 6
    left_x = -right_x / 2
    lower_right_x = right_x / 2
    lower_y = equation_value(equation, left_x)
    points = [
        point_item("A", 0, y_axis_y),
        point_item("B", left_x, lower_y),
        point_item("C", lower_right_x, lower_y),
        point_item("D", right_x, y_axis_y),
    ]

    def extra(ax, x_range, y_range):
        annotate_axis_value(ax, x_range, y_range, "y", y_axis_y, "right")

    return render_quadratic_scene(
        output_path,
        [equation],
        points=points,
        polygons=[points],
        extra_draw=extra,
        x_candidates=[left_x, 0, lower_right_x, right_x],
        y_candidates=[0, lower_y, y_axis_y],
    )


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


def render_parabola_origin_two_points(spec, output_path):
    p1x = parse_number(spec.get("point1_x") or spec.get("x_value"), 4)
    p1y = parse_number(spec.get("point1_y") or spec.get("y_value") or spec.get("y_vaule"), -8)
    p2x = parse_number(spec.get("point2_x") or spec.get("k_x_value"), -2)
    p2_label = str(spec.get("point2_y") or spec.get("k_value") or "k").strip()
    try:
        p2y = float(normalize_expr(p2_label))
    except Exception:
        p2y = None
    if p2y is None:
        a = p1y / (p1x ** 2) if abs(p1x) > 1e-9 else -0.5
        p2y = a * (p2x ** 2)
    else:
        a = p1y / (p1x ** 2) if abs(p1x) > 1e-9 else p2y / (p2x ** 2)
    equation = {"expr": f"({a})*x**2", "label": f"y={format_number(a)}x^2"}
    x_values = [0, p1x, p2x]
    y_values = [0, p1y, p2y]
    x_range = widen_x_for_parabola_style(pad_range(min(x_values), max(x_values), 0.25, 2.0), 0.08)
    curve_y = y_values_for_equations([equation], x_range)
    y_range = steepen_parabola_view(x_range, pad_range(min(curve_y + y_values), max(curve_y + y_values), 0.20, 2.0), 0.78)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    xs = np.linspace(x_range[0], x_range[1], 1200)
    ax.plot(xs, safe_eval(equation["expr"], xs), color="#1f77b4", lw=lw(2.0), zorder=3)
    points = [
        point_item("", p1x, p1y),
        point_item("", p2x, p2y),
    ]
    for point in points:
        ax.scatter([point["x"]], [point["y"]], color="black", s=marker_area(22), zorder=5)
        ax.plot([point["x"], point["x"]], [0, point["y"]], color="#e6c982", lw=lw(0.8), ls="--", zorder=2)
        ax.plot([0, point["x"]], [point["y"], point["y"]], color="#e6c982", lw=lw(0.8), ls="--", zorder=2)
    annotate_axis_value(ax, x_range, y_range, "x", p2x, "above")
    annotate_axis_value(ax, x_range, y_range, "x", p1x, "above")
    annotate_axis_value(ax, x_range, y_range, "y", p1y, "left")
    if p2_label and not re.fullmatch(r"-?\d+(?:\.\d+)?", p2_label):
        ax.text(0.04, p2y, p2_label, fontsize=fs(9), ha="left", va="center")
    else:
        annotate_axis_value(ax, x_range, y_range, "y", p2y, "right")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_two_parabolas_vertical_segment(spec, output_path):
    eq_top = parse_y_equation(spec.get("equation_top") or spec.get("positive_function") or spec.get("equation1") or "y=x^2+3")
    eq_bottom = parse_y_equation(spec.get("equation_bottom") or spec.get("negative_function") or spec.get("equation2") or "y=-x^2-5")
    vertical_x = parse_number(spec.get("vertical_x"), -3)
    top_y = equation_value(eq_top, vertical_x)
    bottom_y = equation_value(eq_bottom, vertical_x)
    x_range = widen_x_for_parabola_style(pad_range(min(vertical_x, 0), max(vertical_x, 0), 0.45, 2.0), 0.10)
    y_values = y_values_for_equations([eq_top, eq_bottom], x_range) + [0, top_y, bottom_y]
    y_range = steepen_parabola_view(x_range, pad_range(min(y_values), max(y_values), 0.18, 2.0), 0.78)
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    xs = np.linspace(x_range[0], x_range[1], 1200)
    ax.plot(xs, safe_eval(eq_top["expr"], xs), color="#1f77b4", lw=lw(2.0), zorder=3)
    ax.plot(xs, safe_eval(eq_bottom["expr"], xs), color="#2ca02c", lw=lw(2.0), zorder=3)
    ax.plot([vertical_x, vertical_x], [bottom_y, top_y], color="#8e44ad", lw=lw(1.1), zorder=4)
    points = [point_item("P", vertical_x, top_y), point_item("Q", vertical_x, bottom_y)]
    plot_labeled_points(ax, points)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_square_side_points_trapezoid(spec, output_path):
    ae = parse_length(spec.get("ae"), 2)
    df = parse_length(spec.get("df"), 8)
    side = parse_length(spec.get("side") or spec.get("square_side"), max(10, ae, df) + 2)
    ae = min(max(ae, 0), side)
    df = min(max(df, 0), side)
    a, b, c, d = (0, side), (0, 0), (side, 0), (side, side)
    e = (0, side - ae)
    f = (side, side - df)
    polygon = [e, b, f, d]
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, side, side)
    ax.add_patch(plt.Rectangle((0, 0), side, side, facecolor="white", edgecolor="black", lw=lw(1.2), zorder=2))
    ax.add_patch(plt.Polygon(polygon, closed=True, facecolor="#9eece7", edgecolor="#267a2a", lw=lw(1.0), alpha=0.8, zorder=3))
    label_offsets = {
        "A": (-side * 0.06, side * 0.05, "right", "bottom"),
        "B": (-side * 0.06, -side * 0.05, "right", "top"),
        "C": (side * 0.06, -side * 0.05, "left", "top"),
        "D": (side * 0.06, side * 0.05, "left", "bottom"),
        "E": (-side * 0.06, 0, "right", "center"),
        "F": (side * 0.06, 0, "left", "center"),
    }
    for label, (x, y) in {"A": a, "B": b, "C": c, "D": d, "E": e, "F": f}.items():
        dx, dy, ha, va = label_offsets[label]
        ax.text(x + dx, y + dy, label, fontsize=fs(9), ha=ha, va=va, zorder=5)
    draw_dimension(ax, a, e, length_label(spec.get("ae"), ae), (-side * 0.22, 0))
    draw_dimension(ax, d, f, length_label(spec.get("df"), df), (side * 0.18, 0))
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


def render_parabola_horizontal_equal_intersections(spec, output_path):
    eq1 = parse_y_equation(spec.get("equation1") or "y=3*x^2")
    eq2 = parse_y_equation(spec.get("equation2") or "y=x^2")
    horizontal_y = parse_number(spec.get("horizontal_y"), 9)
    intersections = []
    for equation in (eq1, eq2):
        for root in roots_for_y_level(equation, horizontal_y):
            intersections.append((root, horizontal_y))
    intersections.append((0.0, horizontal_y))
    intersections = sorted(intersections, key=lambda item: item[0])
    points = [point_item(chr(ord("A") + index), x, y) for index, (x, y) in enumerate(intersections[:5])]

    def extra(ax, x_range, y_range):
        ax.axhline(horizontal_y, color="#555555", lw=lw(1.1), zorder=2)
        annotate_horizontal_line_label(ax, x_range, y_range, horizontal_y)
        draw_guides_to_axes(ax, points, x_to_axis=True, y_to_axis=False)

    return render_quadratic_scene(output_path, [eq1, eq2], points=points, extra_draw=extra,
                                  x_candidates=[point["x"] for point in points] + [0],
                                  y_candidates=[0, horizontal_y])


def render_parabola_inscribed_square(spec, output_path):
    equation = parse_y_equation(spec.get("equation") or "y=2*x^2")
    x_left = parse_number(spec.get("x_left"), -2)
    x_right = parse_number(spec.get("x_right"), 2)
    y_bottom = parse_number(spec.get("y_bottom"), 0)
    left_top = equation_value(equation, x_left)
    right_top = equation_value(equation, x_right)
    y_top = (left_top + right_top) / 2
    warnings = []
    tolerance = max(1e-6, abs(y_top) * 1e-4)
    if x_left >= x_right:
        warnings.append("parabola_inscribed_square requires x_left < x_right")
    if abs(left_top - right_top) > tolerance:
        warnings.append("x_left and x_right must have the same y-value on the parabola")
    width = x_right - x_left
    height = y_top - y_bottom
    if abs(width - height) > max(1e-6, abs(width) * 1e-4):
        warnings.append("parabola_inscribed_square width and height must be equal")
    if warnings:
        return warnings
    points = [
        point_item("A", x_left, y_top),
        point_item("B", x_right, y_top),
        point_item("C", x_right, y_bottom),
        point_item("D", x_left, y_bottom),
    ]

    vertex = quadratic_vertex(quadratic_coefficients(equation))
    x_range = pad_range(min(x_left, vertex[0] - width), max(x_right, vertex[0] + width), 0.18)
    y_values = y_values_for_equations([equation], x_range)
    y_values.extend([0, y_bottom, y_top, vertex[1]])
    y_range = pad_range(min(y_values), max(y_values), 0.15)
    y_range = steepen_parabola_view(x_range, y_range, 0.78)
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    xs = np.linspace(x_range[0], x_range[1], 1200)
    ax.plot(xs, safe_eval(equation["expr"], xs), color="#1f77b4", lw=lw(2), zorder=3)
    ax.fill(
        [point["x"] for point in points],
        [point["y"] for point in points],
        color="#f4c7b8", alpha=0.5, zorder=2
    )
    ax.plot(
        [point["x"] for point in points + [points[0]]],
        [point["y"] for point in points + [points[0]]],
        color="#8c5a4a", lw=lw(1.0), zorder=4
    )
    label_offsets = {
        "A": (-8, 7),
        "B": (8, 7),
        "C": (8, -9),
        "D": (-8, -9),
    }
    for point in points:
        ax.scatter([point["x"]], [point["y"]], color="black", s=marker_area(28), zorder=6)
        offset = label_offsets[point["label"]]
        ax.annotate(
            point["label"], (point["x"], point["y"]), xytext=offset,
            textcoords="offset points", fontsize=fs(10),
            ha="right" if offset[0] < 0 else "left",
            va="bottom" if offset[1] >= 0 else "top", zorder=7
        )
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_coordinate_parallelogram(spec, output_path):
    points = parse_points(spec.get("points", ""))
    if len(points) != 4:
        return ["coordinate_parallelogram requires exactly four named points"]
    labels = [point.get("label") for point in points]
    if any(not label for label in labels):
        return ["coordinate_parallelogram requires named points such as A(1,1)"]
    x_range = parse_range(spec.get("x_range"), pad_range(
        min(point["x"] for point in points), max(point["x"] for point in points), 0.35, 2.0
    ))
    y_range = parse_range(spec.get("y_range"), pad_range(
        min(0, min(point["y"] for point in points)),
        max(point["y"] for point in points), 0.35, 2.0
    ))
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    ax.fill(
        [point["x"] for point in points],
        [point["y"] for point in points],
        color="#f4c7b8", alpha=0.5, zorder=2
    )
    ax.plot(
        [point["x"] for point in points + [points[0]]],
        [point["y"] for point in points + [points[0]]],
        color="#333333", lw=lw(1.2), zorder=4
    )
    plot_labeled_points(ax, points)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_two_parabolas_vertical_trapezoid(spec, output_path):
    eq_top = parse_y_equation(spec.get("equation_top") or "y=2*x^2")
    eq_bottom = parse_y_equation(spec.get("equation_bottom") or "y=-4/3*x^2")
    x_left = parse_number(spec.get("x_left"), -1)
    x_right = parse_number(spec.get("x_right"), 1)
    points = [
        point_item("A", x_left, equation_value(eq_top, x_left)),
        point_item("B", x_right, equation_value(eq_top, x_right)),
        point_item("D", x_right, equation_value(eq_bottom, x_right)),
        point_item("C", x_left, equation_value(eq_bottom, x_left)),
    ]

    def extra(ax, x_range, y_range):
        draw_guides_to_axes(ax, points, x_to_axis=True, y_to_axis=False)

    return render_quadratic_scene(output_path, [eq_top, eq_bottom], points=points, polygons=[points], extra_draw=extra,
                                  x_candidates=[x_left, x_right, 0],
                                  y_candidates=[point["y"] for point in points] + [0])


def render_two_parabolas_vertical_strip(spec, output_path):
    eq1 = parse_y_equation(spec.get("equation1") or "y=-3*x^2")
    eq2 = parse_y_equation(spec.get("equation2") or "y=-3*(x-1)^2+3")
    vertical_x = parse_number(spec.get("vertical_x"), 1)
    c1 = quadratic_coefficients(eq1)
    c2 = quadratic_coefficients(eq2)
    roots = quadratic_roots_from_coeffs(c1[0] - c2[0], c1[1] - c2[1], c1[2] - c2[2])
    left = max([root for root in roots if root < vertical_x] or [vertical_x - 1])
    x_range = pad_range(left, vertical_x, 0.55, 2.0)
    y_values = y_values_for_equations([eq1, eq2], x_range) + [0]
    y_range = pad_range(min(y_values), max(y_values), 0.18, 2.0)
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    xs = np.linspace(x_range[0], x_range[1], 900)
    ax.plot(xs, safe_eval(eq1["expr"], xs), lw=lw(1.8), color="black")
    ax.plot(xs, safe_eval(eq2["expr"], xs), lw=lw(1.8), color="#555555")
    fill_x = np.linspace(left, vertical_x, 500)
    ax.fill_between(fill_x, safe_eval(eq1["expr"], fill_x), safe_eval(eq2["expr"], fill_x),
                    color="#f4a58c", alpha=0.65, zorder=2)
    ax.axvline(vertical_x, color="#555555", lw=lw(1.0))
    annotate_axis_value(ax, x_range, y_range, "x", vertical_x, "below")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_parabola_horizontal_chord_rectangle(spec, output_path):
    equation = parse_y_equation(spec.get("equation") or "y=-x^2")
    horizontal_y = parse_number(spec.get("horizontal_y"), -4)
    roots = roots_for_y_level(equation, horizontal_y)
    if len(roots) < 2:
        roots = [-2, 2]
    left, right = roots[0], roots[-1]
    points = [
        point_item("A", left, 0),
        point_item("D", right, 0),
        point_item("B", left, horizontal_y),
        point_item("C", right, horizontal_y),
    ]
    polygon = [points[0], points[1], points[3], points[2]]
    return render_quadratic_scene(output_path, [equation], points=points, polygons=[polygon],
                                  x_candidates=[left, right, 0], y_candidates=[horizontal_y, 0])


def render_parabola_vertex_horizontal_chord_triangle(spec, output_path):
    equation = parse_y_equation(spec.get("equation") or "y=x^2-6*x+4")
    vertex = quadratic_vertex(quadratic_coefficients(equation))
    chord_y = parse_number(spec.get("chord_y"), equation_value(equation, 0))
    roots = roots_for_y_level(equation, chord_y)
    if len(roots) < 2:
        roots = [vertex[0] - 2, vertex[0] + 2]
    vertex_label = str(spec.get("vertex_label") or "A")
    if vertex_label.upper() == "O" and abs(vertex[0]) < 1e-9 and abs(vertex[1]) < 1e-9:
        vertex_label = ""
    points = [
        point_item(vertex_label, vertex[0], vertex[1]),
        point_item(str(spec.get("left_label") or "B"), roots[0], chord_y),
        point_item(str(spec.get("right_label") or "C"), roots[-1], chord_y),
    ]

    def extra(ax, x_range, y_range):
        ax.plot([roots[0], roots[-1]], [chord_y, chord_y], color="#555555", lw=lw(1.0))
        draw_guides_to_axes(ax, [points[0]], x_to_axis=True, y_to_axis=True)

    return render_quadratic_scene(output_path, [equation], points=points, polygons=[points], extra_draw=extra,
                                  x_candidates=roots + [vertex[0], 0], y_candidates=[vertex[1], chord_y, 0])


def render_three_parabolas_enclosed_region(spec, output_path):
    equations = []
    for part in split_semicolon_outside_parentheses(spec.get("equations") or "y=x^2; y=(x-4)^2; y=x^2-4*x"):
        equations.append(parse_y_equation(part))
    x_range = parse_range(spec.get("x_range"), (-1, 5))
    y_values = y_values_for_equations(equations, x_range) + [0]
    y_range = pad_range(min(y_values), max(y_values), 0.15, 2.0)
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    xs = np.linspace(x_range[0], x_range[1], 1000)
    values = [np.asarray(safe_eval(eq["expr"], xs), dtype=float) for eq in equations]
    for ys in values:
        ax.plot(xs, ys, color="black", lw=lw(1.5), zorder=3)
    if len(values) >= 3:
        upper = np.minimum(values[0], values[1])
        lower = values[2]
        mask = upper >= lower
        ax.fill_between(xs, lower, upper, where=mask, color="#c8e58a", alpha=0.75, zorder=2)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_rectangle_corner_extension(spec, output_path):
    width = parse_length(spec.get("width"), 5)
    height = parse_length(spec.get("height"), 3)
    add_width = parse_length(spec.get("add_width"), 2)
    add_height = parse_length(spec.get("add_height"), 2)
    total_w, total_h = width + add_width, height + add_height
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, total_w, total_h)
    ax.add_patch(plt.Rectangle((0, 0), total_w, total_h, facecolor="#efb4b4", edgecolor="black", lw=lw(1.1), alpha=0.82))
    ax.plot([width, width], [height, total_h], color="#777777", lw=lw(0.9))
    ax.plot([0, width], [height, height], color="#777777", lw=lw(0.9))
    draw_dimension(ax, (0, total_h), (width, total_h), length_label(spec.get("width"), width, " cm"), (0, total_h * 0.08))
    draw_dimension(ax, (width, total_h), (total_w, total_h), length_label(spec.get("add_width"), add_width, " cm"), (0, total_h * 0.08))
    draw_dimension(ax, (0, height), (0, total_h), length_label(spec.get("height"), height, " cm"), (-total_w * 0.08, 0))
    draw_dimension(ax, (0, 0), (0, add_height), length_label(spec.get("add_height"), add_height, " cm"), (-total_w * 0.08, 0))
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


def render_linear_sign_diagram(spec, output_path):
    slope_sign = str(spec.get("slope_sign") or "negative").strip().lower()
    intercept_sign = str(spec.get("y_intercept_sign") or "negative").strip().lower()
    slope = 1.0 if slope_sign in ("positive", "+", "plus") else -1.0
    intercept = 1.0 if intercept_sign in ("positive", "+", "plus") else -1.0
    x_range = (-2.4, 2.4)
    y_range = (-2.4, 2.4)
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    xs = np.linspace(x_range[0], x_range[1], 400)
    ax.plot(xs, slope * xs + intercept, color="black", lw=lw(1.7), zorder=4)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_linear_vertical_line_position(spec, output_path):
    x_value = parse_number(spec.get("x_value"), -4)
    span = max(2.5, abs(x_value) * 1.35)
    x_range = (-span, span)
    y_range = (-span * 0.75, span * 0.75)
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    ax.plot([x_value, x_value], [y_range[0], y_range[1]],
            color="black", lw=lw(1.7), zorder=4)
    annotate_axis_value(ax, x_range, y_range, "x", x_value, "below")
    marker_size = min(abs(x_value) * 0.13, 0.38)
    direction = 1 if x_value < 0 else -1
    ax.plot(
        [x_value, x_value + direction * marker_size, x_value + direction * marker_size],
        [0, 0, marker_size],
        color="black", lw=lw(0.8), zorder=5
    )
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


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
        points = [point_item("A", line_x_at_y(line, 0) or 0, 0), point_item("B", 0, line_y(line, 0) or 0), point_item("", 0, 0)]
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
    for point in points:
        if point.get("label", "").upper() == "O" and abs(point["x"]) < 1e-9 and abs(point["y"]) < 1e-9:
            point["label"] = ""
    show_guides = str(spec.get("show_guides", "true")).lower() != "false"
    return warnings + render_linear_scene(output_path, lines, points=points, polygons=[polygon] if len(polygon) >= 3 else [],
                                          guides=show_guides, shade_color="#f4c7b8", axes_front=True)


def render_linear_two_lines_labeled_points(spec, output_path):
    line1 = parse_line_equation(spec.get("equation1") or "x+y+3=0")
    line2 = parse_line_equation(spec.get("equation2") or "-3*x+y-5=0")
    point_a_x = parse_number(spec.get("point_a_x"), -3.5)
    point_b_x = parse_number(spec.get("point_b_x"), -0.25)
    point_a_y = line_y(line1, point_a_x)
    point_b_y = line_y(line2, point_b_x)
    intersection = line_intersection(line1, line2)
    warnings = []
    if point_a_y is None or point_b_y is None:
        return ["labeled-points template requires non-vertical source lines"]
    if intersection is None:
        warnings.append("labeled-points template requires intersecting lines")
        intersection = (0.0, 0.0)

    points = [
        point_item("A(a, b)", point_a_x, point_a_y),
        point_item("B(s, t)", point_b_x, point_b_y),
        point_item("C(m, n)", intersection[0], intersection[1]),
    ]
    x_candidates = [0, point_a_x, point_b_x, intersection[0]]
    for line in (line1, line2):
        x0 = line_x_at_y(line, 0)
        if x0 is not None:
            x_candidates.append(x0)
    x_range = pad_range(min(x_candidates), max(x_candidates), 0.28, 5.0)
    y_candidates = [0, point_a_y, point_b_y, intersection[1]]
    y_candidates.extend(line_sample_y_values([line1, line2], x_range))
    y_range = pad_range(min(y_candidates), max(y_candidates), 0.18, 5.0)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    xs = np.linspace(x_range[0], x_range[1], 600)
    for line in (line1, line2):
        ax.plot(xs, [line_y(line, x) for x in xs], color="black", lw=lw(1.4), zorder=3)

    offsets = {
        "A(a, b)": (-8, 8, "right", "bottom"),
        "B(s, t)": (8, 8, "left", "bottom"),
        "C(m, n)": (0, -10, "center", "top"),
    }
    for point in points:
        ax.scatter([point["x"]], [point["y"]], color="#6f50b5", s=marker_area(22), zorder=6)
        dx, dy, ha, va = offsets[point["label"]]
        ax.annotate(point["label"], (point["x"], point["y"]),
                    xytext=(dx, dy), textcoords="offset points",
                    fontsize=fs(8), ha=ha, va=va, zorder=7)

    equation_labels = [
        str(spec.get("equation1_label") or spec.get("equation1") or "x+y+3=0"),
        str(spec.get("equation2_label") or spec.get("equation2") or "-3x+y-5=0"),
    ]
    equation_labels = [label.replace("*", "") for label in equation_labels]
    label_xs = [
        x_range[0] + (x_range[1] - x_range[0]) * 0.08,
        x_range[1] - (x_range[1] - x_range[0]) * 0.18,
    ]
    label_offsets = [(0, 7), (0, 6)]
    for line, label, label_x, offset in zip((line1, line2), equation_labels, label_xs, label_offsets):
        label_y = line_y(line, label_x)
        if label_y is not None:
            ax.annotate(label, (label_x, label_y), xytext=offset, textcoords="offset points",
                        fontsize=fs(7), ha="left", va="bottom",
                        bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=fs(0.25)),
                        zorder=7)

    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings


def substitute_line_parameter(raw, parameter, value):
    replacement = "(" + format_number(value) + ")"
    return re.sub(r"\b" + re.escape(parameter) + r"\b", replacement, str(raw or ""))


def triangle_intersections_from_lines(lines):
    intersections = []
    for first_index in range(len(lines)):
        for second_index in range(first_index + 1, len(lines)):
            point = line_intersection(lines[first_index], lines[second_index])
            if point is None:
                continue
            if not any(abs(point[0] - saved[0]) < 1e-8 and abs(point[1] - saved[1]) < 1e-8
                       for saved in intersections):
                intersections.append(point)
    return intersections


def render_linear_parameter_triangle_cases(spec, output_path):
    raw_equations = split_semicolon_outside_parentheses(
        spec.get("equations") or "a*x-3*y=0; 2*x-3*y+6=0; x=0"
    )
    parameter = str(spec.get("parameter") or "a").strip()
    value_texts = split_csv_outside_parentheses(spec.get("parameter_values") or "1,3")
    parameter_values = [parse_number(value, float("nan")) for value in value_texts]
    parameter_values = [value for value in parameter_values if math.isfinite(value)]
    if len(raw_equations) < 3:
        return ["linear_parameter_triangle_cases needs three line equations"]
    if not parameter_values:
        return ["linear_parameter_triangle_cases needs parameter_values"]

    cases = []
    warnings = []
    for parameter_value in parameter_values:
        try:
            lines = [
                parse_line_equation(substitute_line_parameter(raw, parameter, parameter_value))
                for raw in raw_equations
            ]
            vertices = triangle_intersections_from_lines(lines)
            if len(vertices) != 3:
                warnings.append(
                    "parameter " + format_number(parameter_value)
                    + " does not form one triangle"
                )
                continue
            cases.append((parameter_value, lines, vertices))
        except Exception as err:
            warnings.append(
                "parameter " + format_number(parameter_value) + ": " + str(err)
            )

    if not cases:
        return warnings or ["no drawable parameter cases"]

    figure_width = max(FIGURE_SIZE_INCHES[0], FIGURE_SIZE_INCHES[0] * len(cases))
    fig, axes = plt.subplots(1, len(cases), figsize=(figure_width, FIGURE_SIZE_INCHES[1]), squeeze=False)
    show_parameter_labels = str(spec.get("show_parameter_labels", "false")).lower() == "true"

    for case_index, (parameter_value, lines, vertices) in enumerate(cases):
        ax = axes[0][case_index]
        xs = [point[0] for point in vertices] + [0]
        ys = [point[1] for point in vertices] + [0]
        x_range = pad_range(min(xs), max(xs), 0.28, 4.0)
        sampled_y = line_sample_y_values(lines, x_range)
        y_range = pad_range(min(ys + sampled_y), max(ys + sampled_y), 0.18, 4.0)
        setup_axes(ax, x_range, y_range)

        polygon_points = sorted(
            vertices,
            key=lambda point: math.atan2(
                point[1] - sum(vertex[1] for vertex in vertices) / 3,
                point[0] - sum(vertex[0] for vertex in vertices) / 3
            )
        )
        ax.fill(
            [point[0] for point in polygon_points],
            [point[1] for point in polygon_points],
            color="#f4c7b8",
            alpha=0.65,
            zorder=1
        )

        sample_xs = np.linspace(x_range[0], x_range[1], 600)
        for line in lines:
            if abs(line["b"]) < 1e-9:
                x_value = -line["c"] / line["a"]
                if abs(x_value) > 1e-9:
                    ax.axvline(x_value, color="black", lw=lw(1.5), zorder=4)
            else:
                ax.plot(sample_xs, [line_y(line, x) for x in sample_xs],
                        color="black", lw=lw(1.5), zorder=4)

        axis_points = sorted(
            [point for point in vertices if abs(point[0]) < 1e-8],
            key=lambda point: point[1]
        )
        off_axis_points = [point for point in vertices if abs(point[0]) >= 1e-8]
        labels = []
        for point in axis_points:
            label = "O" if abs(point[1]) < 1e-8 else "B"
            labels.append((label, point))
        if off_axis_points:
            labels.append(("A", off_axis_points[0]))

        redraw_axes_in_front(ax, x_range, y_range)
        for label, point in labels:
            ax.scatter([point[0]], [point[1]], color="black", s=marker_area(28), zorder=22)
            if label != "O":
                ax.annotate(label, point, xytext=(4, 4), textcoords="offset points",
                            fontsize=fs(10), zorder=23)
        if show_parameter_labels:
            ax.text(0.5, 0.97, parameter + "=" + format_number(parameter_value),
                    transform=ax.transAxes, ha="center", va="top", fontsize=fs(9))

    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings


def render_parabola_labeled_xintercepts(spec, output_path):
    equation = parse_y_equation(spec.get("equation", ""))
    coeffs = quadratic_coefficients(equation)
    roots = quadratic_x_intercepts(coeffs)
    vertex = quadratic_vertex(coeffs)
    warnings = []
    if len(roots) < 2:
        warnings.append("labeled x-intercepts template needs two x-intercepts")
        roots = [vertex[0] - 2, vertex[0] + 2]

    x_range = pad_range(min(roots[0], 0), max(roots[-1], 0), 0.28)
    x_range = widen_x_for_parabola_style(x_range, 0.12)
    edge_values = [
        float(safe_eval(equation["expr"], x_range[0])),
        float(safe_eval(equation["expr"], x_range[1])),
    ]
    y_range = pad_range(min(vertex[1], 0), max(edge_values + [0]), 0.16)
    y_range = steepen_parabola_view(x_range, y_range, 0.76)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    x = np.linspace(x_range[0], x_range[1], 1200)
    y = safe_eval(equation["expr"], x)
    ax.plot(x, y, lw=lw(1.8), color="black", zorder=4)

    label_offset = (y_range[1] - y_range[0]) * 0.035
    ax.text(roots[0], -label_offset, "A", fontsize=fs(9), ha="center", va="top", zorder=6)
    ax.text(roots[-1], -label_offset, "B", fontsize=fs(9), ha="center", va="top", zorder=6)

    curve_label = format_display_math(
        spec.get("curve_label") or spec.get("display_equation") or equation["raw"]
    )
    label_x = x_range[1] - (x_range[1] - x_range[0]) * 0.05
    label_y = float(safe_eval(equation["expr"], label_x))
    label_y = min(label_y, y_range[1] - (y_range[1] - y_range[0]) * 0.08)
    ax.text(label_x, label_y, curve_label, fontsize=fs(8), ha="right", va="top",
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=fs(0.3)), zorder=6)

    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings


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


def render_linear_two_lines_xaxis_square(spec, output_path):
    raw_left = spec.get("equation_left") or spec.get("equation1") or "y=2*x"
    raw_right = spec.get("equation_right") or spec.get("equation2") or "y=-x+15"
    source_lines = [parse_line_equation(raw_left), parse_line_equation(raw_right)]
    warnings = []

    def slope_intercept(line):
        if abs(line["b"]) < 1e-9:
            return None
        return -line["a"] / line["b"], -line["c"] / line["b"]

    def solve_square(left_line, right_line):
        left_form = slope_intercept(left_line)
        right_form = slope_intercept(right_line)
        if left_form is None or right_form is None:
            return None
        m_left, q_left = left_form
        m_right, q_right = right_form
        matrix = np.array([
            [-m_left, 1.0],
            [-m_right, 1.0 - m_right],
        ])
        vector = np.array([q_left, q_right])
        if abs(np.linalg.det(matrix)) < 1e-9:
            return None
        x_left, side = np.linalg.solve(matrix, vector)
        if not (math.isfinite(x_left) and math.isfinite(side)):
            return None
        return float(x_left), float(side)

    ordered_lines = source_lines
    solved = solve_square(source_lines[0], source_lines[1])
    if solved is None or solved[0] <= 0 or solved[1] <= 0:
        swapped = solve_square(source_lines[1], source_lines[0])
        if swapped is not None and swapped[0] > 0 and swapped[1] > 0:
            ordered_lines = [source_lines[1], source_lines[0]]
            solved = swapped
    if solved is None:
        return ["linear_two_lines_xaxis_square could not solve the square"]

    x_left, side = solved
    if x_left <= 0 or side <= 0:
        warnings.append("linear_two_lines_xaxis_square produced a non-positive placement")

    a = (x_left, 0.0)
    b = (x_left + side, 0.0)
    c = (x_left + side, side)
    d = (x_left, side)
    intersection = line_intersection(ordered_lines[0], ordered_lines[1])
    if intersection is None:
        warnings.append("linear_two_lines_xaxis_square requires intersecting lines")
        intersection = (x_left + side / 2, side * 1.5)

    x_values = [0.0, a[0], b[0], intersection[0]]
    y_values = [0.0, side, intersection[1]]
    for line in ordered_lines:
        x0 = line_x_at_y(line, 0)
        y0 = line_y(line, 0)
        if x0 is not None:
            x_values.append(x0)
        if y0 is not None:
            y_values.append(y0)
    x_range = pad_range(min(x_values), max(x_values), 0.18, 5.0)
    y_range = pad_range(min(y_values), max(y_values), 0.15, 5.0)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)
    xs = np.linspace(x_range[0], x_range[1], 800)
    for line in ordered_lines:
        ax.plot(xs, [line_y(line, x) for x in xs], color="black", lw=lw(1.45), zorder=3)

    ax.add_patch(plt.Polygon(
        [a, b, c, d],
        closed=True,
        facecolor="#d6d6d6",
        edgecolor="black",
        lw=lw(1.1),
        zorder=4,
    ))

    span_x = x_range[1] - x_range[0]
    span_y = y_range[1] - y_range[0]
    label_specs = [
        ("A", a, (-0.015 * span_x, -0.035 * span_y), "right", "top"),
        ("B", b, (0.015 * span_x, -0.035 * span_y), "left", "top"),
        ("C", c, (0.015 * span_x, 0.01 * span_y), "left", "bottom"),
        ("D", d, (-0.015 * span_x, 0.01 * span_y), "right", "bottom"),
        ("E", intersection, (-0.012 * span_x, 0.015 * span_y), "right", "bottom"),
    ]
    for label, point, offset, ha, va in label_specs:
        ax.text(point[0] + offset[0], point[1] + offset[1], label,
                fontsize=fs(9), ha=ha, va=va, zorder=7)

    equation_labels = [
        str(spec.get("equation_left_label") or raw_left).replace("*", ""),
        str(spec.get("equation_right_label") or raw_right).replace("*", ""),
    ]
    right_x_intercept = line_x_at_y(ordered_lines[1], 0)
    label_xs = [
        min(x_range[1] - 0.12 * span_x, intersection[0] + 0.10 * span_x),
        min(x_range[1] - 0.08 * span_x,
            (right_x_intercept if right_x_intercept is not None else b[0]) - 0.08 * span_x),
    ]
    for line, label, label_x in zip(ordered_lines, equation_labels, label_xs):
        label_y = line_y(line, label_x)
        if label_y is not None:
            ax.text(label_x, label_y + 0.025 * span_y, label,
                    fontsize=fs(8), ha="left", va="bottom",
                    bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=fs(0.25)),
                    zorder=7)

    redraw_axes_in_front(ax, x_range, y_range)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings


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


def render_regular_polygon_chain_sequence(spec, output_path):
    sides = bounded_count(spec.get("sides"), 5, 3, 8)
    side = parse_length(spec.get("side"), 3)
    stage_count_text = str(spec.get("stage_counts") or "1,2,3").strip().strip("[]")
    stage_counts = [
        bounded_count(value, 1, 1, 8)
        for value in split_csv_outside_parentheses(stage_count_text)
    ]
    stage_counts = stage_counts[:4] or [1, 2, 3]
    radius = side / (2 * math.sin(math.pi / sides))

    stage_polygons = []
    stage_bounds = []
    for count in stage_counts:
        first = regular_polygon_vertices(radius, radius * 1.2, radius, sides, math.pi / sides)
        polygons = [first]
        for _ in range(1, count):
            edge = rightmost_edge(polygons[-1])
            polygons.append([
                reflect_point_across_line(point, edge[0], edge[1])
                for point in polygons[-1]
            ])
        xs = [x for polygon in polygons for x, _ in polygon]
        ys = [y for polygon in polygons for _, y in polygon]
        stage_polygons.append(polygons)
        stage_bounds.append((min(xs), max(xs), min(ys), max(ys)))

    arrow_gap = radius * 1.35
    stage_gap = radius * 0.35
    widths = [bounds[1] - bounds[0] for bounds in stage_bounds]
    heights = [bounds[3] - bounds[2] for bounds in stage_bounds]
    total_width = sum(widths) + (len(widths) - 1) * (arrow_gap + stage_gap)
    max_height = max(heights)
    fig, ax = plt.subplots(figsize=(2.8, 1.0))
    ax.set_xlim(-radius * 0.2, total_width + radius * 0.2)
    ax.set_ylim(-radius * 0.25, max_height + radius * 0.25)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    cursor = 0.0
    for stage_index, (polygons, bounds, width, height) in enumerate(
        zip(stage_polygons, stage_bounds, widths, heights)
    ):
        min_x, _, min_y, _ = bounds
        y_offset = (max_height - height) / 2
        for polygon in polygons:
            verts = [
                (x - min_x + cursor, y - min_y + y_offset)
                for x, y in polygon
            ]
            ax.add_patch(plt.Polygon(
                verts, closed=True, facecolor="#dddddd",
                edgecolor="#333333", lw=lw(1.0), zorder=3
            ))
        cursor += width
        if stage_index < len(stage_polygons) - 1:
            arrow_start = cursor + stage_gap * 0.25
            arrow_end = cursor + arrow_gap
            ax.annotate(
                "", xy=(arrow_end, max_height / 2),
                xytext=(arrow_start, max_height / 2),
                arrowprops=dict(arrowstyle="->", lw=lw(1.0), color="#555555"),
                zorder=4
            )
            cursor = arrow_end + stage_gap

    fig.subplots_adjust(left=0.01, right=0.99, bottom=0.03, top=0.97)
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


def render_unit_quarter_circle_trig(spec, output_path):
    angle = parse_number(spec.get("angle"), 40)
    angle = max(10, min(80, angle))
    radians = math.radians(angle)
    bx = math.cos(radians)
    ay = math.sin(radians)
    cy = math.tan(radians)

    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    ax.set_aspect("equal")
    ax.set_xlim(-0.08, 1.18)
    ax.set_ylim(-0.08, max(1.08, cy + 0.12))
    ax.axis("off")

    theta = np.linspace(0, math.pi / 2, 240)
    ax.plot(np.cos(theta), np.sin(theta), color="black", lw=lw(1.1), zorder=3)
    ax.plot([0, 1.08], [0, 0], color="black", lw=lw(1.0), zorder=3)
    ax.plot([0, 0], [0, 1.08], color="black", lw=lw(1.0), zorder=3)
    ax.plot([0, 1], [0, cy], color="#2667b5", lw=lw(1.1), zorder=4)
    ax.plot([bx, bx], [0, ay], color="#555555", lw=lw(0.9), zorder=4)
    ax.plot([1, 1], [0, cy], color="#555555", lw=lw(0.9), zorder=4)

    label_points = {
        "O": (0, 0), "A": (bx, ay), "B": (bx, 0), "C": (1, cy), "D": (1, 0)
    }
    for label, (x_value, y_value) in label_points.items():
        ax.text(x_value, y_value, label, fontsize=fs(8), ha="left", va="bottom")

    arc_theta = np.linspace(0, radians, 40)
    arc_radius = 0.18
    ax.plot(arc_radius * np.cos(arc_theta), arc_radius * np.sin(arc_theta),
            color="#777777", lw=lw(0.8))
    ax.text(
        arc_radius * 1.25 * math.cos(radians / 2),
        arc_radius * 1.25 * math.sin(radians / 2),
        format_number(angle) + "°",
        fontsize=fs(7),
        ha="center",
        va="center",
    )
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_activity_calorie_table(spec, output_path):
    activities = [item.strip() for item in split_csv_outside_parentheses(
        spec.get("activities", "")
    ) if item.strip()]
    calories = [item.strip() for item in split_csv_outside_parentheses(
        spec.get("calories_per_10min", "")
    ) if item.strip()]
    warnings = []
    if len(activities) < 2 or len(activities) != len(calories):
        warnings.append("activity_calorie_table needs matching activity and calorie lists")
        activities = activities or ["줄넘기", "배드민턴"]
        calories = calories or ["75", "60"]
    count = min(len(activities), len(calories))
    activities = activities[:count]
    calories = calories[:count]

    left_width = 2.4
    cell_width = 1.45
    row_height = 0.78
    width = left_width + count * cell_width
    height = row_height * 2
    fig, ax = plt.subplots(figsize=(2.3, 0.9))
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")

    cell_style = dict(facecolor="white", edgecolor="black", lw=lw(0.9))
    ax.add_patch(plt.Rectangle((0, row_height), left_width, row_height, **cell_style))
    ax.add_patch(plt.Rectangle((0, 0), left_width, row_height, **cell_style))
    for index in range(count):
        x = left_width + index * cell_width
        ax.add_patch(plt.Rectangle((x, row_height), cell_width, row_height, **cell_style))
        ax.add_patch(plt.Rectangle((x, 0), cell_width, row_height, **cell_style))
        ax.text(x + cell_width / 2, row_height * 1.5, activities[index],
                fontsize=fs(9), ha="center", va="center", fontproperties=KOREAN_FONT)
        ax.text(x + cell_width / 2, row_height * 0.5, calories[index],
                fontsize=fs(9), ha="center", va="center")

    ax.text(left_width / 2, row_height * 0.5, "10분 동안 소모되는 열량\n(kcal)",
            fontsize=fs(8), ha="center", va="center", fontproperties=KOREAN_FONT)
    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.06, top=0.94)
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return warnings


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
    inner_label = spec.get("inner_radius_label") or spec.get("radius_label") or "r"
    increase_label = spec.get("increase_label") or spec.get("radius_gap_label") or spec.get("increase") or spec.get("radius_gap") or "x"
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, outer * 2, outer * 2)
    center = (outer, outer)
    ax.add_patch(plt.Circle(center, outer, facecolor="#bcdff7", edgecolor="black", lw=lw(1.1), alpha=0.8))
    ax.add_patch(plt.Circle(center, inner, facecolor="white", edgecolor="#555555", lw=lw(1.0)))
    ax.plot([center[0], center[0] + inner], [center[1], center[1]], color="#555555", lw=lw(0.9))
    ax.plot([center[0] + inner, center[0] + outer], [center[1], center[1]], color="#555555", lw=lw(0.9))
    ax.scatter([center[0]], [center[1]], color="black", s=marker_area(10), zorder=5)
    ax.text(center[0] + inner * 0.52, center[1] + outer * 0.07,
            length_label(inner_label, inner, " cm"), fontsize=fs(8), ha="center", va="bottom")
    ax.text(center[0] + inner + increase * 0.55, center[1] + outer * 0.07,
            length_label(increase_label, increase, " cm"),
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
    width_label = spec.get("width_label") or spec.get("outer_width_label") or spec.get("width")
    height_label = spec.get("height_label") or spec.get("square_label") or spec.get("height")
    draw_dimension(ax, (0, height), (width, height), length_label(width_label, width), (0, height * 0.1))
    draw_dimension(ax, (0, 0), (0, height), length_label(height_label, height), (-width * 0.08, 0))
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


def validate_coordinate_plane_semantics(spec, equations, points):
    errors = []
    unsupported = [equation for equation in equations if equation.get("kind") == "unsupported"]
    if unsupported:
        errors.extend(
            f"{equation.get('raw', 'equation')}: {equation.get('reason', 'unsupported')}"
            for equation in unsupported
        )

    topology_keys = (
        "segments", "segment", "polygon", "polygons", "rectangle_points",
        "region", "connect", "connections", "edges",
    )
    topology_values = [
        str(spec.get(key, "")).strip()
        for key in topology_keys
        if str(spec.get(key, "")).strip()
    ]
    axis_only_values = {"x_axis", "y_axis", "x_axis,y_axis", "x_axis; y_axis"}
    has_topology = any(
        value.lower().replace(" ", "") not in {
            item.replace(" ", "") for item in axis_only_values
        }
        for value in topology_values
    )
    y_equations = [equation for equation in equations if equation.get("kind") == "y"]
    if points and len(y_equations) == 1 and not has_topology:
        equation = y_equations[0]
        inconsistent = []
        for point in points:
            try:
                expected_y = equation_value(equation, point["x"])
            except Exception as err:
                errors.append(f"{equation.get('raw', 'equation')}: {err}")
                break
            tolerance = max(1e-6, abs(expected_y) * 1e-4)
            if abs(point["y"] - expected_y) > tolerance:
                label = point.get("label") or f"({format_number(point['x'])},{format_number(point['y'])})"
                inconsistent.append(label)
        if inconsistent:
            errors.append(
                "points do not lie on the supplied equation: " + ", ".join(inconsistent)
            )
    return errors


def is_parabola_rectangle_perimeter_diagram(spec, equations, points):
    labels = {str(point.get("label", "")).strip().upper() for point in points if point.get("label")}
    if not {"A", "B", "C", "D", "O"}.issubset(labels):
        return False
    y_equations = [equation for equation in equations if equation.get("kind") == "y"]
    if len(y_equations) != 1 or "x**2" not in y_equations[0].get("expr", ""):
        return False
    segment_text = str(
        spec.get("segments", "")
        or spec.get("segment", "")
        or spec.get("edges", "")
        or spec.get("connections", "")
        or spec.get("connect", "")
    ).replace(" ", "").upper()
    required_edges = ("AB", "BC", "CD", "DA")
    return all(edge in segment_text for edge in required_edges)


def parabola_rectangle_perimeter_ranges(points):
    rect_points = [
        point for point in points
        if str(point.get("label", "")).strip().upper() in {"A", "B", "C", "D"}
    ]
    xs = [point["x"] for point in rect_points]
    ys = [point["y"] for point in rect_points]
    left, right = min(xs), max(xs)
    bottom, top = min(ys), max(ys)
    width = max(right - left, 1.0)
    height = max(top - bottom, 1.0)
    # This problem type is read visually. Keep O, the rectangle, and the
    # parabola shape large enough, instead of treating it like a data plot.
    x_range = (min(0.0, left - width * 1.12), right + width * 0.58)
    y_range = (min(0.0, bottom) - height * 0.42, top + height * 0.58)
    return x_range, y_range


def render_coordinate_plane(spec, output_path):
    x_range = parse_range(spec.get("x_range"), (-6, 6))
    y_range = parse_range(spec.get("y_range"), (-10, 10))
    equation_text = spec.get("equation", "") or spec.get("equations", "") or spec.get("line_equation", "")
    inline_curve_labels = [
        match.group(1).strip()
        for match in re.finditer(r"\{\s*label\s*=\s*([^{}]+?)\s*\}", str(equation_text), flags=re.IGNORECASE)
    ]
    equations = parse_equations(equation_text)
    points = parse_points(spec.get("points", ""))
    labels = parse_labels(spec.get("labels", "") or spec.get("curve_labels", ""))
    if not labels:
        labels = inline_curve_labels
    rectangle_perimeter_diagram = is_parabola_rectangle_perimeter_diagram(spec, equations, points)
    y_equation_count = len([equation for equation in equations if equation.get("kind") == "y"])
    quadratic_equation_count = len([
        equation for equation in equations
        if equation.get("kind") == "y" and "x**2" in equation.get("expr", "")
    ])
    schematic_quadratic = quadratic_equation_count > 0 and not StringFalse(spec.get("schematic", "true"))
    exaggerate_quadratic_curves = (
        not spec.get("x_range")
        and not points
        and y_equation_count >= 3
        and quadratic_equation_count == y_equation_count
    )
    quadratic_display_scale = 1.75 if exaggerate_quadratic_curves else 1.0
    if exaggerate_quadratic_curves:
        x_range = (-2.4, 2.4)
        if not spec.get("y_range"):
            y_range = (-8, 8)
    if schematic_quadratic and points and not spec.get("x_range") and not spec.get("y_range"):
        focus_x = [0.0]
        focus_y = [0.0]
        for equation in equations:
            if equation.get("kind") != "y" or "x**2" not in equation.get("expr", ""):
                continue
            try:
                vertex = quadratic_vertex(quadratic_coefficients(equation))
                focus_x.append(vertex[0])
                focus_y.append(vertex[1])
            except Exception:
                pass
        focus_x.extend(point["x"] for point in points)
        focus_y.extend(point["y"] for point in points)
        x_range = pad_range(min(focus_x), max(focus_x), 0.65, 4.0)
        y_range = pad_range(min(focus_y), max(focus_y), 0.45, 4.0)
    else:
        x_range, y_range = auto_focus_ranges(spec, equations, points, x_range, y_range)
    x_range, y_range = relax_parabola_graph_view(spec, equations, points, x_range, y_range)
    x_range, y_range = expand_range_for_points(x_range, y_range, points)
    if rectangle_perimeter_diagram:
        x_range, y_range = parabola_rectangle_perimeter_ranges(points)
    warnings = []
    if not str(equation_text).strip() and not points:
        warnings.append("coordinate_plane has no equation or concrete points")
    if has_ambiguous_points(spec.get("points", "")):
        warnings.append("points field is ambiguous")
    warnings.extend(validate_coordinate_plane_semantics(spec, equations, points))
    if warnings:
        return warnings

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_INCHES)
    setup_axes(ax, x_range, y_range)

    x = np.linspace(x_range[0], x_range[1], 1400)
    y_curves = []
    unsupported = []
    plotted_curves = []
    for equation in equations:
        if equation["kind"] == "y":
            try:
                y = safe_eval(equation["expr"], x)
                if np.isscalar(y):
                    y = np.full_like(x, float(y))
                if quadratic_display_scale != 1.0:
                    y = y * quadratic_display_scale
                line, = ax.plot(x, y, lw=lw(2))
                y_curves.append((equation["raw"], y))
                plotted_curves.append((equation, y, line.get_color()))
            except Exception as err:
                unsupported.append(f"{equation['raw']}: {err}")
        elif equation["kind"] == "x":
            ax.axvline(equation["value"], color="red", ls="--", lw=lw(1.8))
            annotate_axis_value(ax, x_range, y_range, "x", equation["value"])
        else:
            unsupported.append(f"{equation['raw']}: {equation.get('reason', 'unsupported')}")

    shade_enclosed_region(ax, equations)
    segment_text = (
        spec.get("segments", "")
        or spec.get("segment", "")
        or spec.get("edges", "")
        or spec.get("connections", "")
        or spec.get("connect", "")
        or spec.get("polygon", "")
        or spec.get("polygons", "")
        or spec.get("rectangle_points", "")
    )
    draw_named_segments(ax, points, segment_text)

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

    labeled_points = [point for point in points if point.get("label")]
    centroid_x = sum(point["x"] for point in labeled_points) / len(labeled_points) if labeled_points else 0
    centroid_y = sum(point["y"] for point in labeled_points) / len(labeled_points) if labeled_points else 0
    for idx, point in enumerate(points):
        label = point["label"] or (labels[idx] if idx < len(labels) else "")
        if label == "O" and abs(point["x"]) < 1e-9 and abs(point["y"]) < 1e-9 and not rectangle_perimeter_diagram:
            continue
        if rectangle_perimeter_diagram and label in ("A", "B", "C", "D", "O"):
            ax.scatter([point["x"]], [point["y"]], color="red", s=marker_area(34), zorder=5)
        elif label in ("A", "B", "C", "D") and len(labeled_points) >= 4:
            ax.scatter([point["x"]], [point["y"]], color="black", s=marker_area(10), zorder=5)
        else:
            ax.scatter([point["x"]], [point["y"]], color="red", s=marker_area(36), zorder=5)
        if label:
            if abs(point["x"]) < 1e-9 and abs(point["y"]) < 1e-9 and label != "O":
                ax.annotate(label, (point["x"], point["y"]), xytext=(-8, -12),
                            textcoords="offset points", ha="right", va="top",
                            fontsize=fs(11), zorder=6)
            elif label in ("A", "B", "C", "D") and len(labeled_points) >= 4:
                xoff = 8
                yoff = 8 if point["y"] >= centroid_y else -8
                ax.annotate(label, (point["x"], point["y"]), xytext=(xoff, yoff),
                            textcoords="offset points",
                            ha="left",
                            va="bottom" if yoff > 0 else "top",
                            fontsize=fs(11), zorder=6)
            else:
                ax.annotate(label, (point["x"], point["y"]), xytext=(4, 4),
                            textcoords="offset points", ha="left", va="bottom",
                            fontsize=fs(11), zorder=6)

    if not points and labels:
        for idx, (equation, y, color) in enumerate(plotted_curves):
            if idx >= len(labels):
                break
            visible = np.where(
                np.isfinite(y)
                & (y >= y_range[0])
                & (y <= y_range[1])
                & (x >= x_range[0] + (x_range[1] - x_range[0]) * 0.58)
            )[0]
            if visible.size == 0:
                visible = np.where(np.isfinite(y) & (y >= y_range[0]) & (y <= y_range[1]))[0]
            if visible.size == 0:
                continue
            pos = int(visible[min(len(visible) - 1, max(0, int(len(visible) * 0.72)))])
            ax.text(x[pos], y[pos], labels[idx], color=color, fontsize=fs(10),
                    ha="left", va="center", zorder=7)

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
    coordinate_points = parse_points(spec.get("coordinates", ""))
    named_points = parse_points(spec.get("points", ""))
    coords = named_points if len(named_points) > len(coordinate_points) else coordinate_points
    labels = parse_labels(spec.get("labels", ""))

    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    if coords:
        xs = [p["x"] for p in coords]
        ys = [p["y"] for p in coords]
        by_label = {str(point.get("label", "")).strip(): point for point in coords if point.get("label")}
        label_set = set(by_label)
        if {"A", "B", "C", "D", "E", "F"}.issubset(label_set) and str(spec.get("shape", "")).lower() == "quadrilateral":
            a, b, c, d, e, f = (by_label[key] for key in ("A", "B", "C", "D", "E", "F"))
            ax.plot([a["x"], b["x"], c["x"], d["x"], a["x"]],
                    [a["y"], b["y"], c["y"], d["y"], a["y"]],
                    color="black", lw=lw(1.35), zorder=2)
            shade = [e, b, c, f]
            ax.fill([p["x"] for p in shade], [p["y"] for p in shade],
                    color="#68e3df", alpha=0.82, zorder=1)
            ax.plot([e["x"], b["x"], c["x"], f["x"], e["x"]],
                    [e["y"], b["y"], c["y"], f["y"], e["y"]],
                    color="#1a9f3c", lw=lw(1.1), zorder=3)
            ax.scatter([e["x"], f["x"]], [e["y"], f["y"]],
                       color="red", s=marker_area(16), zorder=4)
        else:
            segment_text = (
                spec.get("segments", "")
                or spec.get("segment", "")
                or spec.get("edges", "")
                or spec.get("connections", "")
                or spec.get("connect", "")
            )
            polygon_text = spec.get("polygon", "") or spec.get("polygons", "")
            polygon_groups = []
            if polygon_text:
                for group in split_semicolon_outside_parentheses(polygon_text):
                    names = [name.strip() for name in split_csv_outside_parentheses(group) if name.strip()]
                    polygon = [by_label[name] for name in names if name in by_label]
                    if len(polygon) >= 3:
                        polygon_groups.append(polygon)
                for polygon in polygon_groups:
                    fill_value = str(spec.get("fill", "")).strip()
                    if fill_value and not StringFalse(fill_value):
                        ax.fill([p["x"] for p in polygon], [p["y"] for p in polygon],
                                color="#d7e8f7", alpha=0.35, zorder=1)
                    ax.plot([p["x"] for p in polygon + [polygon[0]]],
                            [p["y"] for p in polygon + [polygon[0]]],
                            color="#1f77b4", lw=lw(1.2), zorder=2)
            if segment_text:
                draw_named_segments(ax, coords, segment_text, color="#1f77b4", zorder=2)
            elif not polygon_groups:
                ax.plot(xs + [xs[0]], ys + [ys[0]], lw=lw(2))
        for idx, point in enumerate(coords):
            label = point["label"] or (labels[idx] if idx < len(labels) else "")
            if label in ("E", "F"):
                pass
            else:
                ax.scatter([point["x"]], [point["y"]], color="red", s=marker_area(20), zorder=5)
            if label:
                ax.text(point["x"], point["y"], " " + label, fontsize=fs(11), math_fontfamily="stix")
        margin = max(max(xs) - min(xs), max(ys) - min(ys), 1) * 0.12
        ax.set_xlim(min(xs) - margin, max(xs) + margin)
        ax.set_ylim(min(ys) - margin, max(ys) + margin)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def parse_segment_pairs(value):
    pairs = []
    raw = str(value or "").strip()
    if not raw:
        return pairs
    items = split_semicolon_outside_parentheses(raw)
    expanded = []
    for item in items:
        expanded.extend(split_csv_outside_parentheses(item))
    simple_labels = [item.strip() for item in expanded if item.strip()]
    if len(simple_labels) >= 3 and all(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", item) for item in simple_labels):
        return [
            (simple_labels[index], simple_labels[(index + 1) % len(simple_labels)])
            for index in range(len(simple_labels))
        ]
    for item in expanded:
        text = item.strip()
        if not text:
            continue
        if "-" in text:
            parts = [part.strip() for part in text.split("-", 1)]
        elif len(text) == 2 and text.isalpha():
            parts = [text[0], text[1]]
        else:
            parts = [part.strip() for part in re.split(r"\s+", text) if part.strip()]
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return pairs


def draw_named_segments(ax, points, segment_text, color="#666666", zorder=2.5):
    coord_pattern = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")
    raw_items = split_semicolon_outside_parentheses(segment_text)
    for raw_item in raw_items:
        coords = [
            (float(match.group(1)), float(match.group(2)))
            for match in coord_pattern.finditer(raw_item)
        ]
        if len(coords) >= 2:
            p1, p2 = coords[0], coords[1]
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]],
                    color=color, lw=lw(1.1), zorder=zorder)
    by_label = {str(point.get("label", "")).strip(): point for point in points if point.get("label")}
    for left, right in parse_segment_pairs(segment_text):
        p1 = by_label.get(left)
        p2 = by_label.get(right)
        if not p1 or not p2:
            continue
        ax.plot([p1["x"], p2["x"]], [p1["y"], p2["y"]],
                color=color, lw=lw(1.1), zorder=zorder)


def render_rectangle_inner_slanted_quadrilateral(spec, output_path):
    width = parse_length(spec.get("width"), 10)
    height = parse_length(spec.get("height"), width)
    top_point = parse_length(spec.get("top_point") or spec.get("ae") or spec.get("left_top_offset"), width * 0.2)
    bottom_point = parse_length(spec.get("bottom_point") or spec.get("df") or spec.get("right_top_offset"), width * 0.5)
    top_point = min(max(top_point, 0.0), height)
    bottom_point = min(max(bottom_point, 0.0), height)

    a = (0, height)
    b = (0, 0)
    c = (width, 0)
    d = (width, height)
    e = (0, height - top_point)
    f = (width, height - bottom_point)
    shade = [e, d, f, b]

    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle((0, 0), width, height, facecolor="white", edgecolor="black", lw=lw(1.4)))
    ax.fill([p[0] for p in shade], [p[1] for p in shade],
            color="#68e3df", alpha=0.88, zorder=1)
    ax.plot([e[0], d[0]], [e[1], d[1]], color="#1a9f3c", lw=lw(1.2), zorder=3)
    ax.plot([b[0], f[0]], [b[1], f[1]], color="#1a9f3c", lw=lw(1.2), zorder=3)
    ax.scatter([e[0], f[0]], [e[1], f[1]], color="red", s=marker_area(16), zorder=4)

    label_specs = [
        ("A", a, (-0.55, 0.35), "right", "bottom"),
        ("B", b, (-0.55, -0.35), "right", "top"),
        ("C", c, (0.35, -0.35), "left", "top"),
        ("D", d, (0.35, 0.35), "left", "bottom"),
        ("E", e, (-0.55, 0.0), "right", "center"),
        ("F", f, (0.35, 0.0), "left", "center"),
    ]
    for label, point, offset, ha, va in label_specs:
        ax.text(point[0] + offset[0], point[1] + offset[1], label,
                ha=ha, va=va, fontsize=fs(11), math_fontfamily="stix")
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


def render_rectangle_parallel_roads(spec, output_path):
    width = parse_length(spec.get("width"), 16)
    height = parse_length(spec.get("height"), 12)
    road_width = parse_length(spec.get("road_width"), min(width, height) * 0.1)
    vertical_count = bounded_count(spec.get("vertical_road_count"), 2, 0, 7)
    horizontal_count = bounded_count(spec.get("horizontal_road_count"), 1, 0, 7)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    field = plt.Rectangle((0, 0), width, height, facecolor="#efe1b0", edgecolor="black", lw=lw(1.2))
    ax.add_patch(field)

    for index in range(vertical_count):
        center = width * (index + 1) / (vertical_count + 1)
        ax.add_patch(plt.Rectangle(
            (center - road_width / 2, 0), road_width, height,
            facecolor="white", edgecolor="none", zorder=2
        ))
    for index in range(horizontal_count):
        center = height * (index + 1) / (horizontal_count + 1)
        ax.add_patch(plt.Rectangle(
            (0, center - road_width / 2), width, road_width,
            facecolor="white", edgecolor="none", zorder=2
        ))

    ax.add_patch(plt.Rectangle(
        (0, 0), width, height, facecolor="none",
        edgecolor="black", lw=lw(1.2), zorder=5
    ))
    draw_dimension(
        ax, (0, height), (width, height),
        length_label(spec.get("width"), width, " m"), (0, height * 0.09)
    )
    draw_dimension(
        ax, (0, 0), (0, height),
        length_label(spec.get("height"), height, " m"), (-width * 0.08, 0)
    )
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


def render_moving_point_rectangle_trapezoid(spec, output_path):
    width = parse_length(spec.get("rectangle_width"), 12)
    height = parse_length(spec.get("rectangle_height"), 8)
    speed = parse_length(spec.get("point_speed"), 0.5)
    display_time = parse_number(
        spec.get("display_time"),
        width * 0.42 / max(speed, 1e-9),
    )
    point_x = max(width * 0.18, min(width * 0.78, speed * display_time))
    points = {
        "A": (0, height),
        "B": (0, 0),
        "C": (width, 0),
        "D": (width, height),
        "P": (point_x, 0),
    }
    trapezoid = [points[name] for name in ("A", "P", "C", "D")]

    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, height)
    ax.add_patch(plt.Rectangle(
        (0, 0), width, height,
        facecolor="white", edgecolor="black", lw=lw(1.1), zorder=2
    ))
    ax.add_patch(plt.Polygon(
        trapezoid, closed=True,
        facecolor="#d9d9d9", edgecolor="black", lw=lw(1.0), alpha=0.9, zorder=3
    ))
    for label, (x, y) in points.items():
        offsets = {
            "A": (-width * 0.025, height * 0.035),
            "B": (-width * 0.025, -height * 0.035),
            "C": (width * 0.025, -height * 0.035),
            "D": (width * 0.025, height * 0.035),
            "P": (0, -height * 0.055),
        }
        dx, dy = offsets[label]
        ax.text(x + dx, y + dy, label, fontsize=fs(8), ha="center", va="center", zorder=5)
    draw_dimension(
        ax, points["A"], points["D"],
        length_label(spec.get("rectangle_width"), width, " cm"),
        (0, height * 0.12),
    )
    draw_dimension(
        ax, points["C"], points["D"],
        length_label(spec.get("rectangle_height"), height, " cm"),
        (width * 0.10, 0),
    )
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_moving_points_right_triangle(spec, output_path):
    vertical = parse_length(spec.get("vertical_leg"), 45)
    horizontal = parse_length(spec.get("horizontal_leg"), 36)
    p_speed = parse_length(spec.get("point_p_speed"), 3)
    q_speed = parse_length(spec.get("point_q_speed"), 1)
    default_time = min(vertical / max(p_speed, 1) * 0.32, horizontal / max(q_speed, 1) * 0.25)
    display_time = parse_number(spec.get("display_time"), default_time)
    p_height = max(vertical - p_speed * display_time, vertical * 0.18)
    q_x = horizontal + q_speed * display_time

    a = point_item("A", 0, vertical)
    b = point_item("B", 0, 0)
    c = point_item("C", horizontal, 0)
    p = point_item("P", 0, p_height)
    q = point_item("Q", q_x, 0)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, q_x, vertical)
    ax.plot([a["x"], b["x"], c["x"], a["x"]],
            [a["y"], b["y"], c["y"], a["y"]], color="black", lw=lw(1.0), zorder=4)
    ax.plot([a["x"], q["x"]], [a["y"], q["y"]], color="#777777", lw=lw(0.9), ls="--", zorder=2)
    ax.add_patch(plt.Polygon(
        [(p["x"], p["y"]), (b["x"], b["y"]), (q["x"], q["y"])],
        closed=True, facecolor="#cfe8c5", edgecolor="#557755", lw=lw(1.0), alpha=0.75, zorder=3
    ))
    plot_labeled_points(ax, [a, b, c, p, q])
    draw_dimension(ax, (0, 0), (0, vertical), length_label(spec.get("vertical_leg"), vertical, " cm"),
                   (-q_x * 0.10, 0))
    draw_dimension(ax, (0, 0), (horizontal, 0), length_label(spec.get("horizontal_leg"), horizontal, " cm"),
                   (0, -vertical * 0.10))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_sliding_right_triangles_overlap(spec, output_path):
    base = parse_length(spec.get("base"), 20)
    height = parse_length(spec.get("height"), 35)
    speed = parse_length(spec.get("speed"), 3)
    shift_ratio = max(0.18, min(0.55, parse_number(spec.get("display_shift_ratio"), 0.34)))
    shift = base * shift_ratio
    left_triangle = [(0, 0), (base, 0), (0, height)]
    right_triangle = [(shift, 0), (shift + base, 0), (shift, height)]
    overlap_height = height * (base - shift) / base
    overlap = [(shift, 0), (base, 0), (shift, overlap_height)]

    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, base + shift, height)
    ax.add_patch(plt.Polygon(
        left_triangle, closed=True, facecolor="white",
        edgecolor="#555555", lw=lw(1.0), zorder=2
    ))
    ax.add_patch(plt.Polygon(
        right_triangle, closed=True, facecolor="white",
        edgecolor="black", lw=lw(1.15), zorder=3
    ))
    ax.add_patch(plt.Polygon(
        overlap, closed=True, facecolor="#b8b8b8",
        edgecolor="none", alpha=0.82, zorder=3.5
    ))
    ax.plot(
        [shift, base], [overlap_height, 0],
        color="black", lw=lw(1.0), zorder=4
    )

    draw_dimension(
        ax, (shift, 0), (shift + base, 0),
        length_label(spec.get("base"), base, " cm"), (0, -height * 0.11)
    )
    draw_dimension(
        ax, (0, 0), (0, height),
        length_label(spec.get("height"), height, " cm"), (-base * 0.09, 0)
    )
    arrow_left = shift + base * 0.22
    arrow_right = shift + base * 0.72
    arrow_y = height * 1.04
    ax.annotate(
        "", xy=(arrow_right, arrow_y), xytext=(arrow_left, arrow_y),
        arrowprops=dict(arrowstyle="->", lw=lw(1.0), color="#555555")
    )
    ax.text(
        (arrow_left + arrow_right) / 2, arrow_y + height * 0.035,
        length_label(spec.get("speed"), speed, " cm/min"),
        fontsize=fs(8), ha="center", va="bottom", color="#333333"
    )
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


def render_right_triangle_equal_segments(spec, output_path):
    base = parse_length(spec.get("base"), 6)
    height = parse_length(spec.get("height"), 8)
    p_ratio = max(0.15, min(0.85, parse_number(spec.get("p_ratio"), 0.5)))
    q_ratio = max(0.15, min(0.85, parse_number(spec.get("q_ratio"), 0.55)))
    a, b, c = (base, height), (0, 0), (base, 0)
    p = (base, height * p_ratio)
    q = (base * q_ratio, 0)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, base, height)
    ax.add_patch(plt.Polygon([a, b, c], closed=True, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.add_patch(plt.Polygon([p, q, c], closed=True, facecolor="#f4c7b8", edgecolor="#8c5a4a", lw=lw(1.0), alpha=0.75))
    ax.plot([base - base * 0.04, base + base * 0.04], [height * 0.72, height * 0.72], color="#555555", lw=lw(0.9))
    ax.plot([base * 0.72, base * 0.72], [-height * 0.03, height * 0.03], color="#555555", lw=lw(0.9))
    for label, point in {"A": a, "B": b, "C": c, "P": p, "Q": q}.items():
        ax.text(point[0], point[1], label, fontsize=fs(8), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=fs(0.3)))
    draw_dimension(ax, b, c, length_label(spec.get("base"), base, " cm"), (0, -height * 0.1))
    draw_dimension(ax, c, a, length_label(spec.get("height"), height, " cm"), (base * 0.1, 0))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_square_rotated_inscribed(spec, output_path):
    side = parse_length(spec.get("side"), 12)
    offset = max(side * 0.08, min(side * 0.42, parse_length(spec.get("offset"), side * 0.18)))
    points = [(offset, side), (side, side - offset), (side - offset, 0), (0, offset)]
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, side, side)
    ax.add_patch(plt.Rectangle((0, 0), side, side, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.add_patch(plt.Polygon(points, closed=True, facecolor="#d8c8e8", edgecolor="#555555", lw=lw(1.0), alpha=0.8))
    for label, point in {"A": (0, side), "B": (0, 0), "C": (side, 0), "D": (side, side),
                         "E": points[3], "F": points[2], "G": points[1], "H": points[0]}.items():
        ax.text(point[0], point[1], label, fontsize=fs(7), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.72, pad=fs(0.25)))
    draw_dimension(ax, (0, side), (side, side), length_label(spec.get("side"), side, " cm"), (0, side * 0.09))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_isosceles_trapezoid_altitude(spec, output_path):
    bottom = parse_length(spec.get("bottom_base"), 10)
    top = parse_length(spec.get("top_base"), 7)
    height = parse_length(spec.get("height"), 5)
    inset = (bottom - top) / 2
    foot = max(0, min(bottom, parse_length(spec.get("foot_offset"), 2)))
    verts = [(0, 0), (bottom, 0), (bottom - inset, height), (inset, height)]
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, bottom, height)
    ax.add_patch(plt.Polygon(verts, closed=True, facecolor="#ead8a6", edgecolor="black", lw=lw(1.1), alpha=0.82))
    ax.plot([inset, foot], [height, 0], color="#555555", lw=lw(1.0))
    ax.plot([foot, foot + bottom * 0.055, foot + bottom * 0.055],
            [0, 0, height * 0.1], color="#555555", lw=lw(0.8))
    for label, point in {"A": verts[3], "B": verts[0], "C": verts[1], "D": verts[2], "H": (foot, 0)}.items():
        ax.text(point[0], point[1], label, fontsize=fs(8), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=fs(0.3)))
    draw_dimension(ax, verts[0], (foot, 0), length_label(spec.get("foot_offset"), foot, " cm"), (0, -height * 0.1))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_segment_square_triangle(spec, output_path):
    total = parse_length(spec.get("total_length"), 15)
    split = max(total * 0.15, min(total * 0.75, parse_length(spec.get("split"), total * 0.38)))
    square_side = split
    tri_height = parse_length(spec.get("triangle_height"), total - split)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, total, max(square_side, tri_height))
    ax.add_patch(plt.Rectangle((0, 0), square_side, square_side, facecolor="#f1d36f", edgecolor="black", lw=lw(1.0), alpha=0.8))
    ax.add_patch(plt.Polygon([(split, 0), (total, 0), (total, tri_height)], closed=True,
                             facecolor="#f1d36f", edgecolor="black", lw=lw(1.0), alpha=0.8))
    for label, point in {"A": (0, 0), "P": (split, 0), "B": (total, 0)}.items():
        ax.text(point[0], point[1], label, fontsize=fs(9), ha="center", va="top")
    draw_dimension(ax, (0, 0), (total, 0), length_label(spec.get("total_length"), total, " cm"),
                   (0, -max(square_side, tri_height) * 0.12))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_tiled_wall_gap(spec, output_path):
    rows = bounded_count(spec.get("rows"), 3, 2, 6)
    cols = bounded_count(spec.get("cols"), 5, 3, 9)
    gap_cols = bounded_count(spec.get("gap_cols"), 1, 1, 3)
    gap_width = parse_length(spec.get("gap_width"), 9)
    cell_w, cell_h = 2.0, 1.0
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, cols * cell_w, rows * cell_h)
    gap_start = max(1, cols - gap_cols)
    for r in range(rows):
        for c in range(cols):
            if r == 0 and c >= gap_start:
                continue
            x = c * cell_w + (cell_w * 0.5 if r % 2 else 0)
            if x + cell_w > cols * cell_w:
                x = cols * cell_w - cell_w
            color = "#8f866b" if r == 0 and c == gap_start - 1 else "#e0b193"
            ax.add_patch(plt.Rectangle((x, r * cell_h), cell_w, cell_h, facecolor=color,
                                       edgecolor="black", lw=lw(0.7), alpha=0.85))
    ax.text((gap_start + gap_cols / 2) * cell_w, cell_h * 0.5,
            length_label(spec.get("gap_width"), gap_width, " cm"), fontsize=fs(8), ha="center", va="center")
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_square_diagonal_paths(spec, output_path):
    side = parse_length(spec.get("side"), 30)
    path = parse_length(spec.get("path_width"), side * 0.12)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, side, side)
    ax.add_patch(plt.Rectangle((0, 0), side, side, facecolor="#d7efb7", edgecolor="black", lw=lw(1.1)))
    offset = path / math.sqrt(2)
    for reverse in (False, True):
        if not reverse:
            poly = [(0, offset), (offset, 0), (side, side - offset), (side - offset, side)]
        else:
            poly = [(0, side - offset), (offset, side), (side, offset), (side - offset, 0)]
        ax.add_patch(plt.Polygon(poly, closed=True, facecolor="#bca77e", edgecolor="none", alpha=0.9))
    ax.add_patch(plt.Rectangle((0, 0), side, side, facecolor="none", edgecolor="black", lw=lw(1.1), zorder=5))
    draw_dimension(ax, (0, side), (side, side), length_label(spec.get("side"), side, " m"), (0, side * 0.08))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_isosceles_triangle_bisector(spec, output_path):
    base = parse_length(spec.get("base"), 6)
    equal_side = parse_length(spec.get("equal_side"), 6)
    height = math.sqrt(max(equal_side * equal_side - (base / 2) ** 2, 0.1))
    split_ratio = max(0.2, min(0.8, parse_number(spec.get("split_ratio"), 0.55)))
    a, b, c = (base * split_ratio, height), (0, 0), (base, 0)
    d = (base * 0.56, 0)
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, base, height)
    ax.add_patch(plt.Polygon([a, b, c], closed=True, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.plot([a[0], d[0]], [a[1], d[1]], color="#555555", lw=lw(1.0))
    ax.text(b[0] + base * 0.13, height * 0.1, str(spec.get("base_angle") or "36°"), fontsize=fs(8))
    for label, point in {"A": a, "B": b, "C": c, "D": d}.items():
        ax.text(point[0], point[1], label, fontsize=fs(8), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=fs(0.3)))
    draw_dimension(ax, b, c, length_label(spec.get("base"), base, " cm"), (0, -height * 0.11))
    fig.savefig(output_path, dpi=OUTPUT_DPI, facecolor="white")
    plt.close(fig)
    return []


def render_attached_rectangles_diagonal(spec, output_path):
    left_w = parse_length(spec.get("left_width"), 8)
    left_h = parse_length(spec.get("left_height"), 8)
    right_w = parse_length(spec.get("right_width"), 4)
    right_h = parse_length(spec.get("right_height"), 4)
    width = left_w + right_w
    fig, ax = plt.subplots(figsize=GEOMETRY_SIZE_INCHES)
    setup_plain_geometry_axes(ax, width, left_h)
    ax.add_patch(plt.Rectangle((0, 0), left_w, left_h, facecolor="white", edgecolor="black", lw=lw(1.1)))
    ax.add_patch(plt.Rectangle((left_w, 0), right_w, right_h, facecolor="white", edgecolor="black", lw=lw(1.0)))
    q = (left_w, right_h * 0.45)
    p = (left_w + right_w * 0.65, 0)
    ax.add_patch(plt.Polygon([(0, left_h), (0, 0), p, q], closed=True,
                             facecolor="#76b5e8", edgecolor="#4a7da3", lw=lw(1.0), alpha=0.78))
    ax.plot([0, left_h], [left_h, 0], color="#555555", lw=lw(0.9))
    for label, point in {"A": (0, left_h), "B": (0, 0), "C": (left_w, 0), "D": (left_w, left_h),
                         "E": (width, 0), "F": (width, right_h), "G": (left_w, right_h), "P": p, "Q": q}.items():
        ax.text(point[0], point[1], label, fontsize=fs(7), ha="center", va="center",
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.72, pad=fs(0.25)))
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
        ax.add_patch(Wedge(
            (left_radius, radius), left_radius, 0, 180,
            facecolor="white", edgecolor="#555555", lw=lw(1.0), zorder=3
        ))
        ax.add_patch(Wedge(
            (radius * 2 - right_radius, radius), right_radius, 180, 360,
            facecolor="white", edgecolor="#555555", lw=lw(1.0), zorder=3
        ))
        ax.plot([0, radius * 2], [radius, radius], color="#555555", lw=lw(0.9), zorder=4)
        ax.scatter([radius], [radius], color="black", s=marker_area(8), zorder=5)
        ax.text(radius, radius + radius * 0.04, "O", fontsize=fs(8), ha="center", va="bottom", zorder=6)
        ax.text(left_radius, radius + left_radius * 0.48, "S₁", fontsize=fs(9),
                ha="center", va="center", zorder=6)
        ax.text(radius * 2 - right_radius, radius - right_radius * 0.48, "S₂", fontsize=fs(9),
                ha="center", va="center", zorder=6)
        diameter_label = length_label(spec.get("outer_diameter"), outer, " cm")
        ax.text(radius, radius - radius * 0.10, diameter_label, fontsize=fs(8),
                ha="center", va="top", zorder=6)
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
    if (
        template == "parabola_xintercepts_yintercept_triangle"
        and spec.get("equation", "")
        and "^2" not in spec.get("equation", "")
        and "**2" not in spec.get("equation", "")
    ):
        template = "linear_axis_triangle"
    output_path = build_output_path(input_path, output_dir, index)
    validation_warnings = validate_spec_structure(spec, block)
    if template == "past_exam_image":
        unsupported = render_past_exam_image(spec, output_path)
    elif template == "parabola_band_area":
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
    elif template == "two_parabolas_axis_aligned_square":
        unsupported = render_two_parabolas_axis_aligned_square(spec, output_path)
    elif template == "parabola_origin_two_points":
        unsupported = render_parabola_origin_two_points(spec, output_path)
    elif template == "two_parabolas_vertical_segment":
        unsupported = render_two_parabolas_vertical_segment(spec, output_path)
    elif template == "square_side_points_trapezoid":
        unsupported = render_square_side_points_trapezoid(spec, output_path)
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
    elif template == "parabola_horizontal_equal_intersections":
        unsupported = render_parabola_horizontal_equal_intersections(spec, output_path)
    elif template == "parabola_inscribed_square":
        unsupported = render_parabola_inscribed_square(spec, output_path)
    elif template == "coordinate_parallelogram":
        unsupported = render_coordinate_parallelogram(spec, output_path)
    elif template == "two_parabolas_vertical_trapezoid":
        unsupported = render_two_parabolas_vertical_trapezoid(spec, output_path)
    elif template == "two_parabolas_vertical_strip":
        unsupported = render_two_parabolas_vertical_strip(spec, output_path)
    elif template == "parabola_horizontal_chord_rectangle":
        unsupported = render_parabola_horizontal_chord_rectangle(spec, output_path)
    elif template == "parabola_vertex_horizontal_chord_triangle":
        unsupported = render_parabola_vertex_horizontal_chord_triangle(spec, output_path)
    elif template == "three_parabolas_enclosed_region":
        unsupported = render_three_parabolas_enclosed_region(spec, output_path)
    elif template == "rectangle_corner_extension":
        unsupported = render_rectangle_corner_extension(spec, output_path)
    elif template == "stacked_blocks_pattern":
        unsupported = render_stacked_blocks_pattern(spec, output_path)
    elif template == "linear_basic_intercepts":
        unsupported = render_linear_basic_intercepts(spec, output_path)
    elif template == "linear_sign_diagram":
        unsupported = render_linear_sign_diagram(spec, output_path)
    elif template == "linear_vertical_line_position":
        unsupported = render_linear_vertical_line_position(spec, output_path)
    elif template == "linear_point_guides":
        unsupported = render_linear_point_guides(spec, output_path)
    elif template == "linear_axis_triangle":
        unsupported = render_linear_axis_triangle(spec, output_path)
    elif template == "linear_two_lines_region":
        unsupported = render_linear_two_lines_region(spec, output_path)
    elif template == "linear_two_lines_labeled_points":
        unsupported = render_linear_two_lines_labeled_points(spec, output_path)
    elif template == "linear_parameter_triangle_cases":
        unsupported = render_linear_parameter_triangle_cases(spec, output_path)
    elif template == "linear_square_under_line":
        unsupported = render_linear_square_under_line(spec, output_path)
    elif template == "linear_two_lines_xaxis_square":
        unsupported = render_linear_two_lines_xaxis_square(spec, output_path)
    elif template == "grid_number_table":
        unsupported = render_grid_number_table(spec, output_path)
    elif template == "activity_calorie_table":
        unsupported = render_activity_calorie_table(spec, output_path)
    elif template == "tiled_rectangles_layout":
        unsupported = render_tiled_rectangles_layout(spec, output_path)
    elif template == "regular_polygon_chain":
        unsupported = render_regular_polygon_chain(spec, output_path)
    elif template == "regular_polygon_chain_sequence":
        unsupported = render_regular_polygon_chain_sequence(spec, output_path)
    elif template == "rectangle_side_point_triangle":
        unsupported = render_rectangle_side_point_triangle(spec, output_path)
    elif template == "rectangle_inner_slanted_quadrilateral":
        unsupported = render_rectangle_inner_slanted_quadrilateral(spec, output_path)
    elif template == "rectangle_cut_corner":
        unsupported = render_rectangle_cut_corner(spec, output_path)
    elif template == "rectangle_expanding_sides":
        unsupported = render_rectangle_expanding_sides(spec, output_path)
    elif template == "unit_quarter_circle_trig":
        unsupported = render_unit_quarter_circle_trig(spec, output_path)
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
    elif template == "rectangle_parallel_roads":
        unsupported = render_rectangle_parallel_roads(spec, output_path)
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
    elif template == "moving_point_rectangle_trapezoid":
        unsupported = render_moving_point_rectangle_trapezoid(spec, output_path)
    elif template == "moving_points_right_triangle":
        unsupported = render_moving_points_right_triangle(spec, output_path)
    elif template == "sliding_right_triangles_overlap":
        unsupported = render_sliding_right_triangles_overlap(spec, output_path)
    elif template == "right_isosceles_triangle_inner_rectangle":
        unsupported = render_right_isosceles_triangle_inner(spec, output_path)
    elif template == "right_isosceles_triangle_parallelogram":
        unsupported = render_right_isosceles_triangle_inner(spec, output_path, parallelogram=True)
    elif template == "tiled_rectangle_corner_square":
        unsupported = render_tiled_rectangle_corner_square(spec, output_path)
    elif template == "right_triangle_equal_segments":
        unsupported = render_right_triangle_equal_segments(spec, output_path)
    elif template == "square_rotated_inscribed":
        unsupported = render_square_rotated_inscribed(spec, output_path)
    elif template == "isosceles_trapezoid_altitude":
        unsupported = render_isosceles_trapezoid_altitude(spec, output_path)
    elif template == "segment_square_triangle":
        unsupported = render_segment_square_triangle(spec, output_path)
    elif template == "tiled_wall_gap":
        unsupported = render_tiled_wall_gap(spec, output_path)
    elif template == "square_diagonal_paths":
        unsupported = render_square_diagonal_paths(spec, output_path)
    elif template == "isosceles_triangle_bisector":
        unsupported = render_isosceles_triangle_bisector(spec, output_path)
    elif template == "attached_rectangles_diagonal":
        unsupported = render_attached_rectangles_diagonal(spec, output_path)
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
    elif template == "parabola_labeled_xintercepts":
        unsupported = render_parabola_labeled_xintercepts(spec, output_path)
    elif kind == "geometry":
        unsupported = render_geometry(spec, output_path)
    elif should_auto_use_xintercepts_vertex_triangle(spec):
        unsupported = render_parabola_calculated_template(spec, output_path, "parabola_xintercepts_vertex_triangle")
    else:
        unsupported = render_coordinate_plane(spec, output_path)
    return output_path, validation_warnings + unsupported, spec


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

    failed = []
    for index, block in blocks:
        output_path = build_output_path(input_path, output_dir, index)
        try:
            output_path, unsupported, spec = render_block(index, block, input_path, output_dir)
        except Exception as error:
            print(f"[{index}] failed: {error}")
            failed.append(index)
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass
            continue
        print(f"[{index}] {output_path.resolve()}")
        if unsupported:
            print("  warnings:")
            for item in unsupported:
                print(f"  - {item}")
            failed.append(index)
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass

    success_count = len(blocks) - len(failed)
    print("")
    print(f"완료: 정상 이미지 {success_count}개 생성")
    print(f"저장 위치: {output_dir.resolve()}")
    if failed:
        print(f"실패: {len(failed)}개 이미지 명세 오류 ({', '.join(map(str, failed))})")
        sys.exit(2)


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
