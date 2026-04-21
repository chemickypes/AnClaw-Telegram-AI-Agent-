import csv
import math
import os
import statistics
import threading
from decimal import Decimal

from RestrictedPython import compile_restricted, safe_globals
from RestrictedPython.Guards import safe_builtins, safer_getattr, full_write_guard

_TIMEOUT_SECONDS = 5


def _run_with_timeout(fn: callable, timeout: int = _TIMEOUT_SECONDS) -> None:
    error: list[Exception | None] = [None]

    def target():
        try:
            fn()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        raise TimeoutError(f"Esecuzione superata il limite di {timeout} secondi.")
    if error[0]:
        raise error[0]


def _default_getitem(obj, index):
    return obj[index]


def _default_getiter(obj):
    return iter(obj)


_EXTRA_BUILTINS = {
    "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
    "len": len, "range": range, "enumerate": enumerate, "zip": zip,
    "sorted": sorted, "reversed": reversed, "filter": filter, "map": map,
    "int": int, "float": float, "str": str, "bool": bool,
    "list": list, "dict": dict, "set": set, "tuple": tuple,
    "isinstance": isinstance, "type": type,
}


def _build_restricted_globals() -> dict:
    glb = safe_globals.copy()
    builtins = safe_builtins.copy()
    builtins.update(_EXTRA_BUILTINS)
    glb["__builtins__"] = builtins
    glb["math"] = math
    glb["statistics"] = statistics
    glb["Decimal"] = Decimal
    glb["_getattr_"] = safer_getattr
    glb["_getitem_"] = _default_getitem
    glb["_getiter_"] = _default_getiter
    glb["_write_"] = full_write_guard
    return glb


def execute_math(code: str) -> str:
    """Esegue codice Python per operazioni matematiche e statistiche in un ambiente ristretto.

    Il codice può usare i moduli math, statistics e Decimal.
    Il risultato finale deve essere assegnato alla variabile 'result'.
    Esempio: result = math.sqrt(144) + statistics.mean([1, 2, 3])

    Args:
        code: Codice Python da eseguire. Deve assegnare il risultato a 'result'.

    Returns:
        Il valore di 'result' come stringa, oppure un messaggio di errore.
    """
    try:
        byte_code = compile_restricted(code, "<execute_math>", "exec")
    except SyntaxError as e:
        return f"Errore di sintassi nel codice: {e}"

    glb = _build_restricted_globals()
    loc: dict = {}

    def execute():
        exec(byte_code, glb, loc)

    try:
        _run_with_timeout(execute)
    except TimeoutError as e:
        return str(e)
    except Exception as e:
        return f"Errore durante l'esecuzione: {e}"

    result = loc.get("result")
    if result is None:
        user_vars = {k: v for k, v in loc.items() if not k.startswith("_")}
        if user_vars:
            return str(user_vars)
        return "Nessun risultato: assegna il valore finale alla variabile 'result'."
    return str(result)


def search_in_file(file_path: str, search_column: str, search_value: str) -> str:
    """Cerca righe in un file CSV o Excel dove una colonna contiene un certo valore.

    La ricerca è case-insensitive e parziale (contiene, non uguale esatto).
    Restituisce al massimo 20 righe corrispondenti.

    Args:
        file_path: Percorso assoluto o relativo al file CSV (.csv) o Excel (.xlsx/.xls).
        search_column: Nome della colonna su cui cercare (anche parziale, case-insensitive).
        search_value: Valore da cercare nella colonna.

    Returns:
        Le righe trovate formattate come testo, oppure un messaggio di errore.
    """
    if not os.path.exists(file_path):
        return f"File non trovato: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        rows = _read_csv(file_path)
    elif ext in (".xlsx", ".xls"):
        rows = _read_excel(file_path)
    else:
        return f"Formato non supportato: '{ext}'. Usa un file .csv, .xlsx o .xls."

    if not rows:
        return "Il file è vuoto o non contiene dati leggibili."

    columns = list(rows[0].keys())
    matched_col = next(
        (c for c in columns if search_column.lower() in c.lower() or c.lower() in search_column.lower()),
        None,
    )
    if not matched_col:
        return (
            f"Colonna '{search_column}' non trovata.\n"
            f"Colonne disponibili: {', '.join(columns)}"
        )

    matching = [r for r in rows if search_value.lower() in str(r.get(matched_col, "")).lower()]

    if not matching:
        return f"Nessuna riga trovata dove '{matched_col}' contiene '{search_value}'."

    lines = [f"Trovate {len(matching)} righe ('{matched_col}' contiene '{search_value}'):"]
    for i, row in enumerate(matching[:20], 1):
        lines.append(f"{i}. " + " | ".join(f"{k}: {v}" for k, v in row.items()))
    if len(matching) > 20:
        lines.append(f"... e altre {len(matching) - 20} righe non mostrate.")

    return "\n".join(lines)


def filter_file_rows(file_path: str, condition_code: str) -> str:
    """Filtra le righe di un file CSV o Excel con codice Python ristretto.

    Il codice riceve la variabile 'rows' (lista di dizionari) e deve assegnare
    il risultato filtrato (o calcolato) alla variabile 'result'.
    Moduli disponibili nel codice: math, statistics, Decimal.

    Esempio:
        result = [r for r in rows if float(r.get('prezzo', 0)) > 100]
        result = sum(float(r['importo']) for r in rows if r['categoria'] == 'spesa')

    Args:
        file_path: Percorso al file CSV o Excel.
        condition_code: Codice Python ristretto che filtra 'rows' e scrive in 'result'.

    Returns:
        Le righe risultanti formattate come testo, oppure il valore calcolato.
    """
    if not os.path.exists(file_path):
        return f"File non trovato: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        rows = _read_csv(file_path)
    elif ext in (".xlsx", ".xls"):
        rows = _read_excel(file_path)
    else:
        return f"Formato non supportato: '{ext}'. Usa .csv, .xlsx o .xls."

    if not rows:
        return "Il file è vuoto."

    try:
        byte_code = compile_restricted(condition_code, "<filter_rows>", "exec")
    except SyntaxError as e:
        return f"Errore di sintassi nel codice: {e}"

    glb = _build_restricted_globals()
    loc: dict = {"rows": rows}

    def execute():
        exec(byte_code, glb, loc)

    try:
        _run_with_timeout(execute)
    except TimeoutError as e:
        return str(e)
    except Exception as e:
        return f"Errore durante il filtraggio: {e}"

    result = loc.get("result")
    if result is None:
        return "Nessun risultato: assegna la lista o il valore finale alla variabile 'result'."

    if isinstance(result, list):
        if not result:
            return "Nessuna riga corrisponde al filtro."
        lines = [f"Trovate {len(result)} righe:"]
        for i, row in enumerate(result[:20], 1):
            if isinstance(row, dict):
                lines.append(f"{i}. " + " | ".join(f"{k}: {v}" for k, v in row.items()))
            else:
                lines.append(f"{i}. {row}")
        if len(result) > 20:
            lines.append(f"... e altre {len(result) - 20} righe non mostrate.")
        return "\n".join(lines)

    return str(result)


# ── Helpers lettura file ──────────────────────────────────────────────────────

def _read_csv(file_path: str) -> list[dict]:
    with open(file_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _read_excel(file_path: str) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not rows:
        return []

    headers = [str(h) if h is not None else f"Col{i}" for i, h in enumerate(rows[0])]
    return [
        dict(zip(headers, (str(v) if v is not None else "" for v in row)))
        for row in rows[1:]
        if any(v is not None for v in row)
    ]
