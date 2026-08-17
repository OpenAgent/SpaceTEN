from spaceten.contest.letters import letters_of

FIXTURE_ID = "anagrams"

# Sealed key. Not copied into the jail.
TERMS = frozenset({"ENERGY", "NUMBER", "SPACE", "TIME"})

# Display names, shuffled so pair-order is not the list order.
NAMES: tuple[str, ...] = (
    "Ben Mutimer",
    "Spence Geary",
    "Emy Runenberg",
    "Pete Micas",
    "Reuben Camps",
    "Meg Tierney",
)

PAIRS: dict[str, frozenset[str]] = {
    "pete micas": frozenset({"SPACE", "TIME"}),
    "spence geary": frozenset({"SPACE", "ENERGY"}),
    "reuben camps": frozenset({"SPACE", "NUMBER"}),
    "meg tierney": frozenset({"TIME", "ENERGY"}),
    "ben mutimer": frozenset({"TIME", "NUMBER"}),
    "emy runenberg": frozenset({"ENERGY", "NUMBER"}),
}

DEFAULT_ENERGY = 10_000

PROMPT = "These six names belong together. Why?"

NAMES_MD = PROMPT + "\n\n" + "\n".join(NAMES) + "\n"

RULES_MD = """# Contest rules

NAMES.md asks why the six names belong together.

Each name is an anagram of **two** hidden source words combined (all
letters, no extras). There are **four** source words in total. The six
names are the six unique pairings of those words.

Work only through SpaceTEN. Editor-only edits that are not committed as Acts
will fail `check --rebuild`.

## Steps

Write files under `steps/` as Acts. Each file is one move:

```
kind: inventory
name: <exactly as listed>
letters: A,B,C
```

```
kind: extract
word: <candidate source word>
from:
  - <name>
  - <name>
```

`from` names must contain the letters of `word`.

```
kind: pair
name: <exactly as listed>
words: [WORD_A, WORD_B]
```

```
kind: retract
target: pair
name: <name>
```

or `target: extract` with `word:`.

## Finish

After at least one inventory, one extract, and all six pairs, write
`SOLUTION.md` as an Act (last), then you may `spaceten contest finish`.

```
TERMS: WORD, WORD, WORD, WORD
Name One = WORD + WORD
Name Two = WORD + WORD
...
```

`spaceten contest verify` is the judge. Lowest Energy among valid runs wins;
then fewer events; then fewer bytes under `steps/`.
"""


def normalize_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def name_letters(name: str) -> tuple[str, ...]:
    return letters_of(name)


def term_letters(term: str) -> tuple[str, ...]:
    return letters_of(term)
