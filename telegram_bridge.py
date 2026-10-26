#!/usr/bin/env python3
"""Trading OS — Telegram UserBridge (ponte segnali).

================================================================================
ITALIANO — ISTRUZIONI
================================================================================

Questo script collega il TUO account Telegram (tramite la Client API / MTProto,
con Telethon) a Trading OS. Ascolta in tempo reale i canali o i gruppi che
indichi, estrae i parametri del segnale (Simbolo, Azione, Entry, SL, TP) e li
inoltra all'endpoint di acquisizione di Trading OS:

        POST http://localhost:8765/api/v1/signals/incoming

protetto da un token Bearer. Da lì i segnali compaiono nella pagina "Segnali
Telegram" e, se hai armato l'esecuzione automatica, vengono inviati come ordini
sul conto scelto, con tutti i guardrail del rischio.

--------------------------------------------------------------------------------
RISPETTO DELLA PROTEZIONE DEI CONTENUTI
--------------------------------------------------------------------------------
Lo script SALTA i messaggi dei canali che hanno attivato la protezione dei
contenuti (divieto di inoltro/salvataggio, flag "noforwards"), esattamente come
fa il listener interno dell'app. Inoltra solo i segnali dei canali che ricevi
legittimamente e NON aggira in alcun modo le restrizioni di copia o le
protezioni anti-screenshot di un canale.

--------------------------------------------------------------------------------
REQUISITI
--------------------------------------------------------------------------------
    pip install telethon

--------------------------------------------------------------------------------
CONFIGURAZIONE (variabili d'ambiente)
--------------------------------------------------------------------------------
Ottieni API_ID e API_HASH personali da https://my.telegram.org  (API development
tools). Poi imposta:

    export TELEGRAM_API_ID=123456
    export TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
    export TRADING_OS_INGEST_TOKEN=<il token generato in Segnali Telegram → API segnali esterni>
    export TELEGRAM_CHANNELS="@gold_signals, @vip_room, -1001234567890"
    # facoltativo (predefinito http://localhost:8765/api/v1/signals/incoming):
    export TRADING_OS_INGEST_URL="http://localhost:8765/api/v1/signals/incoming"

TELEGRAM_CHANNELS è un elenco separato da virgole di username (con o senza @) o
di id numerici dei canali/gruppi da ascoltare.

--------------------------------------------------------------------------------
AVVIO
--------------------------------------------------------------------------------
    python telegram_bridge.py

Al primo avvio Telegram chiede il numero di telefono e il codice di accesso (e la
password a due fattori, se impostata). La sessione viene salvata nel file
`session_trading_os.session`: ai riavvii successivi NON serve più autenticarsi.
Tieni questo file al sicuro: vale come un accesso al tuo account.

Premi Ctrl+C per fermare il ponte.
================================================================================
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

try:
    # Telethon è il client MTProto che parla come utente Telegram.
    from telethon import TelegramClient, events
except ImportError:  # pragma: no cover - dipendenza esterna
    sys.exit("Telethon non è installato. Esegui:  pip install telethon")


# --------------------------------------------------------------------------- configurazione

# Nome della sessione: Telethon salva lo stato in `<SESSION_NAME>.session`.
SESSION_NAME = "session_trading_os"

# URL dell'endpoint di acquisizione di Trading OS (il ponte gira di solito sulla stessa macchina).
INGEST_URL = os.environ.get("TRADING_OS_INGEST_URL", "http://localhost:8765/api/v1/signals/incoming")

# Token Bearer: generalo in Trading OS → Segnali Telegram → "API segnali esterni".
INGEST_TOKEN = os.environ.get("TRADING_OS_INGEST_TOKEN", "")

# Credenziali personali della Client API (https://my.telegram.org).
API_ID = os.environ.get("TELEGRAM_API_ID", "")
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")

# Canali/gruppi da ascoltare: username (@nome) o id numerici, separati da virgola.
CHANNELS_RAW = os.environ.get("TELEGRAM_CHANNELS", "")


def _targets(raw: str) -> list:
    """Converte la stringa dei canali in una lista di username (str) o id (int) per Telethon."""
    targets = []
    for piece in raw.split(","):
        name = piece.strip()
        if not name:
            continue
        # Un id numerico (anche negativo, es. -1001234567890) va passato come intero.
        if re.fullmatch(r"-?\d+", name):
            targets.append(int(name))
        else:
            targets.append(name)
    return targets


# --------------------------------------------------------------------------- parser del segnale

# Direzione: accetta termini inglesi e italiani comuni.
_LONG_WORDS = r"buy|long|compra|comprare|acquista|rialzo"
_SHORT_WORDS = r"sell|short|vendi|vendere|ribasso"

# Alias di simboli scritti "a parole" verso i ticker usati da Trading OS.
_SYMBOL_ALIASES = {
    "GOLD": "XAUUSD",
    "ORO": "XAUUSD",
    "XAU": "XAUUSD",
    "SILVER": "XAGUSD",
    "ARGENTO": "XAGUSD",
    "NAS100": "NAS100",
    "US100": "NAS100",
    "US30": "US30",
    "DOW": "US30",
    "SPX": "US500",
    "US500": "US500",
}

# Parole che NON sono mai un simbolo (per non confondere un ticker con una parola chiave).
_NOT_SYMBOLS = {"BUY", "SELL", "LONG", "SHORT", "SL", "TP", "ENTRY", "STOP", "LOSS", "PROFIT", "TARGET", "NOW", "AT"}

_NUM = r"(\d+(?:[.,]\d+)?)"


def _num(text: str) -> float | None:
    """Converte un numero trovato (con virgola o punto) in float."""
    return float(text.replace(",", ".")) if text else None


def parse_signal(text: str) -> dict:
    """Estrae {symbol, action, entry, sl, tp} da un messaggio. Campi assenti restano vuoti.

    È un parser volutamente semplice: Trading OS ri-valida e, se qui non si estrae abbastanza,
    il ponte invia il testo grezzo e lascia che sia il parser (più ricco) del server a provarci.
    """
    upper = text.upper()

    # Direzione.
    action = ""
    if re.search(_LONG_WORDS, text, re.I):
        action = "buy"
    elif re.search(_SHORT_WORDS, text, re.I):
        action = "sell"

    # Simbolo: prima gli alias "a parole", poi il primo token maiuscolo plausibile.
    symbol = ""
    for word, ticker in _SYMBOL_ALIASES.items():
        if re.search(rf"\b{word}\b", upper):
            symbol = ticker
            break
    if not symbol:
        for token in re.findall(r"\b[A-Z]{3,10}\d?\b", upper):
            if token not in _NOT_SYMBOLS:
                symbol = token
                break

    # Entry / Stop Loss.
    entry = re.search(rf"(?:ENTRY|ENTRATA|INGRESSO|@|PRICE|PREZZO)\s*[:=]?\s*{_NUM}", upper)
    sl = re.search(rf"(?:SL|STOP\s*LOSS|STOP)\s*[:=]?\s*{_NUM}", upper)

    # Take Profit: uno o più, inclusi i formati "TP 2680 / 2665" (più livelli dopo una sola parola chiave).
    tps = []
    for block in re.finditer(r"(?:TP\d*|TAKE\s*PROFIT|TARGET|OBIETTIVO)\s*[:=]?\s*([\d.,/\s]+)", upper):
        for value in re.findall(r"\d+(?:[.,]\d+)?", block.group(1)):
            tps.append(_num(value))

    return {
        "symbol": symbol,
        "action": action,
        "entry": _num(entry.group(1)) if entry else None,
        "sl": _num(sl.group(1)) if sl else None,
        "tp": tps,
    }


# --------------------------------------------------------------------------- invio a Trading OS


def send_to_trading_os(parsed: dict, raw_text: str, source: str) -> None:
    """Inoltra il segnale a Trading OS. Se il parser locale è sicuro invia i campi strutturati,
    altrimenti manda il testo grezzo così il server lo analizza con il suo parser."""
    if parsed.get("symbol") and parsed.get("action"):
        payload = {
            "symbol": parsed["symbol"],
            "action": parsed["action"],
            "entry": parsed["entry"],
            "sl": parsed["sl"],
            "tp": parsed["tp"],
            "source_channel": source,
        }
    else:
        # Parsing incerto: lascia decidere al server.
        payload = {"text": raw_text, "source_channel": source}

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        INGEST_URL,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {INGEST_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
        auto = body.get("auto")
        esito = f" → auto: {auto['status']}" if auto else ""
        print(f"  ✓ inviato ({source}): {payload.get('symbol') or 'testo grezzo'}{esito}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        print(f"  ✗ Trading OS ha rifiutato il segnale (HTTP {error.code}): {detail}")
    except urllib.error.URLError as error:
        print(f"  ✗ Trading OS non raggiungibile ({error.reason}). È in esecuzione sulla porta 8765?")


# --------------------------------------------------------------------------- controlli preliminari


def _check_config() -> None:
    """Verifica che la configurazione minima ci sia, con messaggi chiari in italiano."""
    problems = []
    if not API_ID or not API_HASH:
        problems.append("Imposta TELEGRAM_API_ID e TELEGRAM_API_HASH (da https://my.telegram.org).")
    if not INGEST_TOKEN:
        problems.append("Imposta TRADING_OS_INGEST_TOKEN (generalo in Segnali Telegram → API segnali esterni).")
    if not _targets(CHANNELS_RAW):
        problems.append('Imposta TELEGRAM_CHANNELS, es. "@gold_signals, -1001234567890".')
    if problems:
        print("Configurazione incompleta:")
        for problem in problems:
            print(f"  • {problem}")
        sys.exit(1)


# --------------------------------------------------------------------------- avvio del ponte


def main() -> None:
    _check_config()
    targets = _targets(CHANNELS_RAW)

    # Il client si autentica come TE; la sessione resta in `session_trading_os.session`.
    client = TelegramClient(SESSION_NAME, int(API_ID), API_HASH)

    @client.on(events.NewMessage(chats=targets))
    async def handler(event):  # noqa: ANN001 - firma richiesta da Telethon
        # Rispetta la protezione dei contenuti: salta i messaggi dei canali che vietano inoltro/salvataggio.
        chat = await event.get_chat()
        protetto = getattr(chat, "noforwards", False) or getattr(event.message, "noforwards", False)
        if protetto:
            titolo = getattr(chat, "title", "") or getattr(chat, "username", "") or str(event.chat_id)
            print(f"  ⤼ saltato (contenuto protetto): {titolo}")
            return

        testo = event.message.message or ""
        if not testo.strip():
            return  # niente testo (foto senza didascalia, ecc.)

        source = getattr(chat, "title", "") or getattr(chat, "username", "") or str(event.chat_id)
        parsed = parse_signal(testo)
        send_to_trading_os(parsed, testo, source)

    print("Trading OS — Telegram UserBridge")
    print(f"  Endpoint : {INGEST_URL}")
    print(f"  Canali   : {', '.join(str(target) for target in targets)}")
    print(f"  Sessione : {SESSION_NAME}.session")
    print("  (Ctrl+C per fermare)\n")

    with client:
        client.run_until_disconnected()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nPonte fermato.")
