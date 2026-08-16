from dataclasses import dataclass
from typing import Literal

from spaceten.contest.letters import parse_letter_list


@dataclass(frozen=True)
class Inventory:
    kind: Literal["inventory"] = "inventory"
    name: str = ""
    letters: tuple[str, ...] = ()


@dataclass(frozen=True)
class Extract:
    kind: Literal["extract"] = "extract"
    word: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Pair:
    kind: Literal["pair"] = "pair"
    name: str = ""
    words: tuple[str, str] = ("", "")


@dataclass(frozen=True)
class Retract:
    kind: Literal["retract"] = "retract"
    target: Literal["extract", "pair"] = "pair"
    name: str | None = None
    word: str | None = None


Step = Inventory | Extract | Pair | Retract


def _strip_comment(line: str) -> str:
    cut = line.find("#")
    if cut >= 0:
        return line[:cut].rstrip()
    return line.rstrip()


def parse_step(text: str) -> Step:
    fields: dict[str, str] = {}
    list_key: str | None = None
    items: list[str] = []
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("- "):
            if list_key is None:
                raise ValueError("list item without a key")
            items.append(line[2:].strip())
            continue
        if ":" not in line:
            raise ValueError(f"expected key: value, got {line!r}")
        key, value = line.split(":", 1)
        key = key.strip().casefold()
        value = value.strip()
        if list_key is not None:
            fields[list_key] = "\n".join(items)
            items = []
        list_key = None
        if value == "":
            list_key = key
            continue
        fields[key] = value
    if list_key is not None:
        fields[list_key] = "\n".join(items)

    kind = fields.get("kind", "").casefold()
    if kind == "inventory":
        return Inventory(
            name=fields.get("name", ""),
            letters=parse_letter_list(fields.get("letters", "")),
        )
    if kind == "extract":
        evidence = _word_list(fields.get("from", fields.get("evidence", "")))
        return Extract(word=fields.get("word", "").strip().upper(), evidence=evidence)
    if kind == "pair":
        words = _word_list(fields.get("words", ""))
        if len(words) != 2:
            raise ValueError("pair requires exactly two words")
        return Pair(
            name=fields.get("name", ""),
            words=(words[0].upper(), words[1].upper()),
        )
    if kind == "retract":
        target = fields.get("target", "pair").casefold()
        if target not in {"extract", "pair"}:
            raise ValueError("retract target must be extract or pair")
        name = fields.get("name") or None
        word = fields.get("word", "").strip().upper() or None
        if target == "extract":
            return Retract(target="extract", name=name, word=word)
        return Retract(target="pair", name=name, word=word)
    raise ValueError(f"unknown step kind {kind!r}")


def _word_list(raw: str) -> tuple[str, ...]:
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if "\n" in raw and not text.startswith("["):
        parts = [p.strip() for p in raw.splitlines() if p.strip()]
    else:
        parts = [p.strip() for p in text.split(",") if p.strip()]
    return tuple(parts)


def parse_solution(text: str) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    terms: frozenset[str] | None = None
    pairs: dict[str, frozenset[str]] = {}
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.upper().startswith("TERMS:"):
            body = line.split(":", 1)[1]
            terms = frozenset(p.strip().upper() for p in body.split(",") if p.strip())
            continue
        if "=" not in line:
            raise ValueError(f"expected Name = WORD + WORD, got {line!r}")
        name, rhs = line.split("=", 1)
        words = [
            p.strip().upper() for p in rhs.replace("+", ",").split(",") if p.strip()
        ]
        if len(words) != 2:
            raise ValueError(f"pair needs two words: {line!r}")
        pairs[name.strip().casefold()] = frozenset(words)
    if terms is None:
        raise ValueError("SOLUTION.md missing TERMS:")
    return terms, pairs
