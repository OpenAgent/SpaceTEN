def letters_of(text: str) -> tuple[str, ...]:
    return tuple(sorted(ch for ch in text.upper() if "A" <= ch <= "Z"))


def parse_letter_list(text: str) -> tuple[str, ...]:
    parts = [p.strip().upper() for p in text.replace(" ", ",").split(",")]
    chars: list[str] = []
    for part in parts:
        if not part:
            continue
        if not all("A" <= ch <= "Z" for ch in part):
            raise ValueError(f"invalid letters: {part!r}")
        chars.extend(part)
    return tuple(sorted(chars))


def is_submultiset(inner: tuple[str, ...], outer: tuple[str, ...]) -> bool:
    bag = list(outer)
    for ch in inner:
        try:
            bag.remove(ch)
        except ValueError:
            return False
    return True
