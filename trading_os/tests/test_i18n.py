"""Interface languages: catalogs, coverage of every translated text, server language context."""

import ast
import json
import re
import string
from pathlib import Path

from trading_os.services import i18n

from .conftest import update_settings

PACKAGE = Path(__file__).resolve().parent.parent
PY_CALLS = {"tr": (0,), "tr_n": (1, 2), "N_": (0,)}  # function -> positions of the translatable texts
JS_CALL = re.compile(
    r"""(?<![\w.$])(?P<fn>t|N_|tn)\(\s*(?:(?P<count>[^,()]+(?:\([^()]*\))?)\s*,\s*)?"""
    r"""(?P<q>["'`])(?P<text>(?:\\.|(?!(?P=q)).)*)(?P=q)"""
    r"""(?:\s*,\s*(?P<q2>["'`])(?P<plural>(?:\\.|(?!(?P=q2)).)*)(?P=q2))?""",
    re.S,
)


_JS_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0"}


def js_unescape(text: str) -> str:
    """The value of a JS string literal's body (\\n, \\uXXXX, escaped quotes and backslashes)."""

    def replace(match: re.Match) -> str:
        escaped = match.group(1)
        if escaped.startswith("u"):
            return chr(int(escaped[1:], 16))
        return _JS_ESCAPES.get(escaped, escaped)

    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", replace, text, flags=re.S)


def python_keys() -> set[str]:
    keys = set()
    for path in PACKAGE.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            for index in PY_CALLS.get(name, ()):
                if index < len(node.args) and isinstance(node.args[index], ast.Constant):
                    if isinstance(node.args[index].value, str):
                        keys.add(node.args[index].value)
    return keys


def js_keys() -> set[str]:
    keys = set()
    for path in (PACKAGE / "static" / "js").rglob("*.js"):
        if path.name == "i18n.js":
            continue
        source = path.read_text(encoding="utf-8")
        for match in JS_CALL.finditer(source):
            fn, text, plural = match.group("fn"), match.group("text"), match.group("plural")
            if fn == "tn":
                if match.group("count") is None or plural is None:
                    continue
                keys.update({js_unescape(text), js_unescape(plural)})
            elif match.group("count") is None and "${" not in text:
                keys.add(js_unescape(text))
    return keys


def python_fields(template: str) -> set[str]:
    return {field.split(".")[0].split("[")[0] for _, field, _, _ in string.Formatter().parse(template) if field}


def js_fields(template: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", template))


def test_catalogs_are_valid_and_keep_every_placeholder():
    for language in i18n.LANGUAGES:
        if language == i18n.DEFAULT_LANGUAGE:
            continue
        for part, fields in (("server", python_fields), ("web", js_fields)):
            seen: dict[str, str] = {}
            for path in sorted((i18n.LOCALES_DIR / language / part).glob("*.json")):
                entries = json.loads(path.read_text(encoding="utf-8"))
                assert isinstance(entries, dict), path
                for key, value in entries.items():
                    assert isinstance(value, str) and value.strip(), f"{path.name}: empty translation for {key!r}"
                    assert fields(value) == fields(key), f"{path.name}: placeholders differ in {key!r}"
                    if key in seen:
                        assert seen[key] == value, f"{path.name}: {key!r} translated differently in two files"
                    seen[key] = value


def test_every_translated_server_text_has_an_italian_entry():
    missing = sorted(python_keys() - set(i18n.catalog("it", "server")))
    assert not missing, f"{len(missing)} server texts without Italian: {missing[:20]}"


def test_every_translated_interface_text_has_an_italian_entry():
    missing = sorted(js_keys() - set(i18n.catalog("it", "web")))
    assert not missing, f"{len(missing)} interface texts without Italian: {missing[:20]}"


def test_tr_formats_and_falls_back_to_english():
    assert i18n.tr("Unknown language '{language}'", language="xx") == "Unknown language 'xx'"
    with i18n.using("it"):
        assert i18n.tr("Unknown language '{language}'", language="xx") == "Lingua sconosciuta 'xx'"
        assert i18n.tr("Not in any catalog {x:.1f}", x=1.25) == "Not in any catalog 1.2"
    with i18n.using("klingon"):
        assert i18n.current() == "en"


def test_the_language_setting_drives_server_messages_and_the_browser_catalog(client):
    assert client.get("/api/i18n").json()["languages"] == [
        {"code": "en", "name": "English"},
        {"code": "it", "name": "Italiano"},
    ]
    assert client.get("/api/i18n/xx").json()["detail"] == "Unknown language 'xx'"
    update_settings(client, language="it")
    assert client.get("/api/i18n/xx").json()["detail"] == "Lingua sconosciuta 'xx'"
    assert client.get("/api/plan").json()["settings"]["language"] == "it"
    assert isinstance(client.get("/api/i18n/it").json()["messages"], dict)
    assert client.put("/api/settings", json={"language": "fr"}).status_code == 422


def test_background_jobs_run_in_the_stored_language(client):
    scheduler = client.app.state.scheduler
    seen = []
    scheduler.register("language-probe", lambda: 3600.0, lambda factory: seen.append(i18n.current()), description="")
    update_settings(client, language="it")
    scheduler.run_now("language-probe")
    assert seen == ["it"] and i18n.current() == "en"
