from dataclasses import dataclass
from datetime import timezone
from enum import Enum, StrEnum


class PartOfSpeech(StrEnum):
    NOUN = "Noun"
    VERB = "Verb"
    ADVERB = "Adverb"
    ADJECTIVE = "Adjective"
    INTERJECTION = "Interjection"
    CONJUNCTION = "Conjunction"
    PRONOUN = "Pronoun"
    PREPOSITION = "Preposition"
    NUMERAL = "Numeral"
    PROPER_NOUN = "Proper_noun"


PAGE_SIZE = 10
MIN_STAGE = 1
MAX_STAGE = 15


def ts(dt, ts_format="%Y-%m-%d %H:%M:%S"):
    return dt.astimezone(timezone.utc).strftime(ts_format)


class SortKey(StrEnum):
    UPDATED = "updated"
    ALPHABET = "alphabet"
    CREATED = "created"


@dataclass(frozen=True)
class Word:
    id: int
    text: str
    definition: str
    created_at: str
    updated_at: str
    stage: int
    revision_date: str

    @classmethod
    def from_row(cls, row):
        return cls(*row)


@dataclass(frozen=True)
class WordPage:
    words: list[Word]
    total: int
    page: int
    page_size: int = PAGE_SIZE

    @property
    def max_page(self):
        return max(0, (self.total - 1) // self.page_size)

    @property
    def has_prev(self):
        return self.page > 0

    @property
    def has_next(self):
        return (self.page + 1) * self.page_size < self.total


class FetchStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    NETWORK_ERROR = "network_error"


@dataclass(frozen=True)
class FetchResult:
    code: FetchStatus
    definition: str | None = None
    detail: str | None = None


class DBStatus(StrEnum):
    OK = "ok"
    EXISTS = "exists"
    NOT_FOUND = "not_found"
    NETWORK_ERROR = "network_error"
    ERROR = "error"


@dataclass(frozen=True)
class DBResult:
    code: DBStatus
    detail: str | None = None

    @property
    def success(self):
        return self.code is DBStatus.OK


@dataclass(frozen=True)
class AddResult:
    code: DBStatus
    word: Word | None = None
    detail: str | None = None


@dataclass(frozen=True)
class BatchAddResult:
    added: list[str]
    already_added: list[str]
    not_found: list[str]
    failed: list[tuple[str, str | None]]
    skipped: list[str]
    network_detail: str | None = None

    @property
    def has_failures(self):
        return bool(self.failed or self.skipped)


class EditMode(Enum):
    NEW = "new"
    EXISTING = "existing"


@dataclass
class ListState:
    sort_key: SortKey = SortKey.CREATED
    descending: bool = False
    page: int = 0
