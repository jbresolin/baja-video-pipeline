"""Read worker.json without ever crashing, and explain what is wrong with it.

Volunteers (and João) edit worker.json by hand. Before this module a
missing comma made the service crash with a Python traceback in
service-stderr.log, NSSM restarted it every few seconds, and the status
page, the one place a volunteer looks, never came up.

read_config() never raises. It answers with the parsed config, or with a
ConfigProblem: the file, the exact line and column, the line itself, and a
plain-language pt-BR hint ("falta uma vírgula", "use \\\\ nos caminhos").
The status page shows it, the tray icon turns red, and the worker waits
until the file is fixed (worker.lifecycle).

Three kinds of problem:
  * the text is not valid JSON (quotes, commas, braces, backslashes);
  * valid JSON, but a key the worker does not know (typo) or a value of
    the wrong type;
  * the identity is incomplete: some of worker_id / bearer_token / role
    are there and others were deleted, or an enrolled worker has no
    server address left. The fix is to restore the field or
    "Registrar de novo".
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from worker.config import WorkerConfig, read_config_text

# The three fields enrollment writes together; all or none must be there.
IDENTITY_FIELDS = ("worker_id", "bearer_token", "role")

# json.JSONDecodeError.msg → what to look for, in pt-BR. The keys are the
# messages of Python's json module (they start the error text).
JSON_HINTS = {
    "Expecting ',' delimiter": (
        'Falta uma vírgula. Cada linha com "nome": valor termina com vírgula, '
        "menos a última antes do }. Confira o fim da linha anterior."
    ),
    "Expecting property name enclosed in double quotes": (
        'Esperava um nome entre aspas duplas ("assim"). Causas comuns: uma '
        "vírgula sobrando antes do }, ou aspas simples ' no lugar de aspas duplas \"."
    ),
    "Expecting ':' delimiter": (
        'Falta o sinal de dois-pontos entre o nome e o valor ("nome": valor).'
    ),
    "Expecting value": (
        "Falta um valor aqui: sobrou uma vírgula, ou um texto está sem aspas "
        'duplas (textos vão entre "aspas"; números e true/false/null, sem).'
    ),
    "Unterminated string starting at": (
        'Um texto começou com aspas " e não terminou: falta fechar as aspas.'
    ),
    "Invalid \\escape": (
        "Uma barra invertida \\ sozinha não vale dentro de aspas. Em caminhos do "
        "Windows escreva duas (C:\\\\BajaWorker) ou use a barra normal (C:/BajaWorker)."
    ),
    "Invalid \\uXXXX escape": (
        "Uma barra invertida \\ seguida de u não vale dentro de aspas. Em caminhos "
        "do Windows escreva duas (C:\\\\Users) ou use a barra normal (C:/Users)."
    ),
    "Invalid control character at": (
        "Há uma quebra de linha ou tabulação dentro de um texto entre aspas: "
        "o texto precisa ficar inteiro numa linha só."
    ),
    "Extra data": (
        "Há algo depois do } final. Talvez uma chave } a mais, ou dois arquivos colados."
    ),
}
GENERIC_JSON_HINT = "Confira aspas, vírgulas, dois-pontos e chaves { } perto desta posição."
WRONG_VALUE_HINT = (
    "Confira o valor: textos vão entre aspas duplas, números e true/false/null "
    "sem aspas, e caminhos do Windows com \\\\ ou /."
)


@dataclass(frozen=True)
class ConfigProblem:
    """What is wrong with worker.json, ready to show to a volunteer (pt-BR)."""

    file: str
    message: str
    hint: str
    # 1-based, like a text editor; None when the problem has no position.
    line: int | None = None
    column: int | None = None
    # The text of that line, so the page can show it with a marker.
    line_text: str | None = None
    # Identity problems: the fields that are missing. "Registrar de novo"
    # fixes those (the file itself parses), so the page offers it.
    missing_fields: tuple[str, ...] = ()

    def as_json(self) -> dict:
        document = asdict(self)
        document["missing_fields"] = list(self.missing_fields)
        return document

    @property
    def fixable_by_re_registering(self) -> bool:
        return bool(self.missing_fields)


@dataclass(frozen=True)
class ConfigReading:
    """read_config's answer. `config` is None only when the file does not
    parse at all; with an identity problem the parsed config comes along,
    because "Registrar de novo" starts from it."""

    config: WorkerConfig | None
    problem: ConfigProblem | None = None

    @property
    def usable(self) -> bool:
        return self.config is not None and self.problem is None


def read_config(config_path: Path) -> ConfigReading:
    """worker.json → config or a problem; never raises. No file = all defaults."""
    if not config_path.exists():
        return ConfigReading(WorkerConfig())
    file = str(config_path)
    try:
        text = read_config_text(config_path)
    except UnicodeDecodeError:
        return ConfigReading(None, _not_utf8(file))
    except OSError as error:
        return ConfigReading(
            None,
            ConfigProblem(
                file,
                f"Não consegui ler o arquivo ({error.strerror or error}).",
                "Confira se o arquivo existe e se não está aberto e bloqueado por outro programa.",
            ),
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return ConfigReading(None, _json_syntax_problem(file, text, error))
    try:
        config = WorkerConfig.model_validate(data)
    except ValidationError as error:
        return ConfigReading(None, _validation_problem(file, text, error))
    return ConfigReading(config, _identity_problem(file, config))


def _not_utf8(file: str) -> ConfigProblem:
    return ConfigProblem(
        file,
        "O arquivo não está salvo como texto UTF-8.",
        "No Bloco de Notas: Arquivo → Salvar como → Codificação: UTF-8.",
    )


def _line_text(text: str, line: int) -> str | None:
    lines = text.splitlines()
    return lines[line - 1] if 1 <= line <= len(lines) else None


def _json_syntax_problem(file: str, text: str, error: json.JSONDecodeError) -> ConfigProblem:
    hint = next(
        (hint for start, hint in JSON_HINTS.items() if error.msg.startswith(start)),
        GENERIC_JSON_HINT,
    )
    return ConfigProblem(
        file,
        f"O arquivo tem um erro de formato na linha {error.lineno}, coluna {error.colno}.",
        hint,
        line=error.lineno,
        column=error.colno,
        line_text=_line_text(text, error.lineno),
    )


def _line_of_key(text: str, key: str) -> int | None:
    """The first line that has `"key"` on it, for pointing at a bad field."""
    quoted = f'"{key}"'
    for number, line in enumerate(text.splitlines(), start=1):
        if quoted in line:
            return number
    return None


def _validation_problem(file: str, text: str, error: ValidationError) -> ConfigProblem:
    """Only the first error is shown: one thing to fix at a time is enough."""
    first = error.errors()[0]
    location = first.get("loc") or ()
    key = str(location[0]) if location else None
    if key is None:
        return ConfigProblem(
            file,
            "O arquivo não tem o formato esperado: precisa ser um objeto { ... } com os campos.",
            GENERIC_JSON_HINT,
        )
    line = _line_of_key(text, key)
    if first.get("type") == "extra_forbidden":
        message = f'O campo "{key}" não existe.'
        hint = (
            "Confira se o nome está escrito certo (a lista de campos está em "
            "worker/README.md) ou apague essa linha."
        )
    else:
        message = f'O campo "{key}" tem um valor inválido ({first.get("msg")}).'
        hint = WRONG_VALUE_HINT
    return ConfigProblem(
        file, message, hint, line=line, column=None, line_text=_line_text(text, line or 0)
    )


def _identity_problem(file: str, config: WorkerConfig) -> ConfigProblem | None:
    """Enrolled-looking files that lost a field the worker cannot run without."""
    present = [name for name in IDENTITY_FIELDS if getattr(config, name)]
    missing = [name for name in IDENTITY_FIELDS if not getattr(config, name)]
    if present and missing:
        names = ", ".join(f'"{name}"' for name in missing)
        return ConfigProblem(
            file,
            f"A identidade deste PC está incompleta: falta {names}.",
            "Se você apagou o campo sem querer, desfaça a edição. Senão, use "
            '"Registrar de novo" com um token novo da equipe Baja (os vídeos guardados ficam).',
            missing_fields=tuple(missing),
        )
    return None


class ConfigProblemError(Exception):
    """Raised by startup steps that found worker.json broken mid-way; the
    lifecycle then goes back to showing the problem until it is fixed."""

    def __init__(self, problem: ConfigProblem) -> None:
        self.problem = problem
        super().__init__(problem.message)


def require_usable_config(config_path: Path) -> WorkerConfig:
    """read_config, but raise ConfigProblemError instead of answering a problem."""
    reading = read_config(config_path)
    if reading.problem is not None or reading.config is None:
        assert reading.problem is not None
        raise ConfigProblemError(reading.problem)
    return reading.config
