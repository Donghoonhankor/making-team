import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Token:
    kind: str
    value: str
    number: int = 0


def select_input_file():
    if sys.platform.startswith("win"):
        script = "\n".join([
            "Add-Type -AssemblyName System.Windows.Forms",
            "$dialog = New-Object System.Windows.Forms.OpenFileDialog",
            "$dialog.Title = 'HWP로 변환할 쌍둥이문항 텍스트 파일을 선택하세요'",
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


def parse_tokens(text):
    pattern = re.compile(
        r"(\[이미지\s*필요\s*(\d*)\s*:[\s\S]*?\]|\[IMAGE_PROMPT\s*(\d*)\s*:[\s\S]*?\]|\[수식\s*:\s*([\s\S]*?)\])",
        re.IGNORECASE,
    )
    tokens = []
    cursor = 0
    fallback_image_number = 1

    for match in pattern.finditer(text):
        if match.start() > cursor:
            tokens.append(Token("text", text[cursor:match.start()]))

        full = match.group(1)
        korean_image_number = match.group(2)
        english_image_number = match.group(3)
        formula = match.group(4)

        if full.upper().startswith("[IMAGE_PROMPT"):
            pass
        elif full.startswith("[이미지"):
            number_text = korean_image_number or ""
            number = int(number_text) if number_text else fallback_image_number
            fallback_image_number = max(fallback_image_number, number + 1)
            tokens.append(Token("image", "", number))
        else:
            tokens.append(Token("formula", formula or ""))

        cursor = match.end()

    if cursor < len(text):
        tokens.append(Token("text", text[cursor:]))
    return tokens


def normalize_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Gemini occasionally leaves Markdown emphasis in explanations; HWP COM is
    # more stable when we insert plain text only.
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    text = text.replace("`", "")
    return text


def split_text_chunks(text, limit=180):
    chunks = []
    current = ""
    for piece in re.split(r"(\s+)", text):
        if len(current) + len(piece) > limit and current:
            chunks.append(current)
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current)
    return chunks


def convert_formula_to_hwp(value):
    text = str(value or "").strip()
    if not text:
        return ""

    replacements = {
        "²": "^2",
        "³": "^3",
        "⁴": "^4",
        "×": "times",
        "÷": "div",
        "±": "plusminus",
        "≤": "<=",
        "≥": ">=",
        "≠": "!=",
        "π": "pi",
        "θ": "theta",
        "∠": "angle",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)

    text = re.sub(r"\\+frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"{\1} over {\2}", text)
    text = re.sub(r"\bfrac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"{\1} over {\2}", text)
    text = re.sub(r"√\s*\(([^()]+)\)", r"sqrt {\1}", text)
    text = re.sub(r"√\s*([A-Za-z0-9가-힣]+)", r"sqrt {\1}", text)

    def fraction_repl(match):
        numerator = match.group(1)
        denominator = match.group(2)
        return "{" + numerator + "} over {" + denominator + "}"

    text = re.sub(
        r"(?<![\w}])([A-Za-z0-9]+)\s*/\s*([A-Za-z0-9]+)(?![\w{])",
        fraction_repl,
        text,
    )
    return text


def get_image_path(input_path, number):
    suffixes = [".PNG", ".png", ".JPG", ".jpg", ".JPEG", ".jpeg"]
    for suffix in suffixes:
        candidate = input_path.with_name(f"{input_path.stem}_이미지{number}{suffix}")
        if candidate.exists():
            return candidate
    return None


def dispatch_hwp():
    try:
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("pywin32가 설치되어 있지 않습니다. pywin32 설치 후 다시 실행하세요.") from exc

    try:
        hwp = win32com.client.gencache.EnsureDispatch("HWPFrame.HwpObject")
    except Exception:
        hwp = win32com.client.Dispatch("HWPFrame.HwpObject")

    for module_name in ("FilePathCheckDLL", "FilePathCheckerModule"):
        try:
            hwp.RegisterModule("FilePathCheckDLL", module_name)
            break
        except Exception:
            pass
    try:
        hwp.XHwpWindows.Item(0).Visible = True
    except Exception:
        pass
    return hwp


def run_action(hwp, action_name):
    try:
        hwp.HAction.Run(action_name)
    except Exception:
        pass


def insert_text_chunk(hwp, chunk):
    try:
        hwp.InsertText(chunk)
        return
    except Exception:
        pass

    pset = hwp.HParameterSet.HInsertText
    hwp.HAction.GetDefault("InsertText", pset.HSet)
    pset.Text = chunk
    hwp.HAction.Execute("InsertText", pset.HSet)


def insert_plain_text(hwp, text):
    text = normalize_text(text)
    if not text:
        return
    parts = text.split("\n")
    for index, part in enumerate(parts):
        if part:
            for chunk in split_text_chunks(part):
                insert_text_chunk(hwp, chunk)
        if index < len(parts) - 1:
            run_action(hwp, "BreakLine")


def insert_equation(hwp, formula):
    equation = convert_formula_to_hwp(formula)
    if not equation:
        return
    try:
        action = hwp.CreateAction("EquationCreate")
        param = action.CreateSet()
        action.GetDefault(param)
        param.SetItem("String", equation)
        action.Execute(param)
    except Exception as first_error:
        try:
            pset = hwp.HParameterSet.HEqEdit
            hwp.HAction.GetDefault("EquationCreate", pset.HSet)
            pset.string = equation
            hwp.HAction.Execute("EquationCreate", pset.HSet)
        except Exception:
            insert_plain_text(hwp, "[수식: " + str(formula or "").strip() + "]")


def insert_picture(hwp, image_path):
    if not image_path:
        insert_plain_text(hwp, "[이미지 파일 없음]")
        return

    path = str(image_path.resolve())
    try:
        hwp.InsertPicture(path, True, 0, False, False, 0, 0, 0)
        return
    except Exception:
        pass

    try:
        action = hwp.CreateAction("InsertPicture")
        param = action.CreateSet()
        action.GetDefault(param)
        param.SetItem("FileName", path)
        param.SetItem("Embed", True)
        action.Execute(param)
    except Exception:
        insert_plain_text(hwp, f"[이미지 삽입 실패: {image_path.name}]")


def save_hwp(hwp, output_path):
    output = str(output_path.resolve())
    try:
        hwp.SaveAs(output, "HWP", "")
    except Exception:
        hwp.SaveAs(output)


def build_hwp(input_path, output_path):
    text = input_path.read_text(encoding="utf-8")
    tokens = parse_tokens(text)
    hwp = dispatch_hwp()
    error_log_path = input_path.with_name(input_path.stem + "_hwp_error.log")
    try:
        try:
            hwp.Clear(1)
        except Exception:
            pass

        for index, token in enumerate(tokens, start=1):
            try:
                if token.kind == "text":
                    insert_plain_text(hwp, token.value)
                elif token.kind == "formula":
                    insert_equation(hwp, token.value)
                elif token.kind == "image":
                    insert_picture(hwp, get_image_path(input_path, token.number))
            except Exception as exc:
                preview = normalize_text(token.value or "")[:300]
                error_log_path.write_text(
                    "HWP 생성 중 오류\n"
                    f"토큰 번호: {index}\n"
                    f"토큰 종류: {token.kind}\n"
                    f"이미지 번호: {token.number}\n"
                    f"오류: {exc}\n"
                    f"내용 일부:\n{preview}\n",
                    encoding="utf-8",
                )
                raise

        save_hwp(hwp, output_path)
    except Exception:
        raise
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Build HWP from generated math text and numbered images.")
    parser.add_argument("input", nargs="?", help="Text file containing generated problems.")
    parser.add_argument("-o", "--output", default="", help="Output HWP path. Defaults to input stem + .hwp.")
    args = parser.parse_args()

    input_text = args.input or select_input_file()
    if not input_text:
        print("파일 선택이 취소되었습니다.")
        return 1

    input_path = Path(input_text).expanduser().resolve()
    if not input_path.exists():
        print(f"입력 파일을 찾을 수 없습니다: {input_path}")
        return 1

    output_path = Path(args.output).expanduser().resolve() if args.output else input_path.with_suffix(".hwp")
    try:
        result = build_hwp(input_path, output_path)
    except Exception as exc:
        print(f"HWP 생성 실패: {exc}")
        return 1

    print(f"완료: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
