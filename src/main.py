import contextlib
import random
import sqlite3
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, StrEnum
from tkinter import font, messagebox, scrolledtext, simpledialog
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup

PARTS_OF_SPEECH = [
    "Noun", "Verb", "Adverb", "Adjective", "Interjection",
    "Conjunction", "Pronoun", "Preposition", "Numeral", "Proper_noun"
]
PAGE_SIZE = 10
MIN_STAGE = 1
MAX_STAGE = 15


def _ts(dt):
    TS_FORMAT = "%Y-%m-%d %H:%M:%S"
    return dt.astimezone(timezone.utc).strftime(TS_FORMAT)


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


class Fetcher:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/115.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.5",
    }

    def make_unordered_list(self, ol_element):
        item_symbol = "*"
        the_list = ol_element.find_all("li", recursive=False)
        for li in the_list:
            if li.contents:
                if isinstance(li.contents[0], str):
                    li.contents[0].replace_with(
                        f"{item_symbol} {li.contents[0]}")
                else:
                    li.insert(0, f"{item_symbol} ")
            nested_ol = li.find("ol")
            if nested_ol:
                self.make_unordered_list(nested_ol)

    def enumerate_list(self, ol_element):
        the_list = ol_element.find_all("li", recursive=False)
        for index, li in enumerate(the_list, start=1):
            enumeration = f"{index})"
            if li.contents:
                if isinstance(li.contents[0], str):
                    li.contents[0].replace_with(
                        f"{enumeration} {li.contents[0]}")
                else:
                    li.insert(0, f"{enumeration} ")
            nested_ol = li.find("ol")
            if nested_ol:
                self.make_unordered_list(nested_ol)

    def merge_strings(self, input_list):
        result = []
        buffer = ""
        for s in input_list:
            stripped_str = s.strip()
            if stripped_str:
                if stripped_str[0].isdigit():
                    if buffer:
                        result.append(buffer)
                    buffer = s
                elif stripped_str.startswith("*"):
                    buffer += " " + s
        if buffer:
            result.append(buffer)
        return result

    def process_def_list(self, def_list):
        for li in def_list.find_all("li"):
            first_c = li.find(True)
            if first_c and first_c.name == "b" and first_c.get_text().strip().isdigit():
                li.decompose()
        unwanted_classes = [
            "citation-whole", "h-usage-example", "Latn mention e-example",
            "cited-source", "q-hellip-sp", "q-hellip-b", "see-cites",
            "external text", "mw-empty-elt"
        ]
        for unwanted in def_list.find_all(class_=unwanted_classes):
            unwanted.extract()
        self.enumerate_list(def_list)
        remaining_text = def_list.get_text()
        lines = remaining_text.splitlines()
        merged_strings = self.merge_strings(lines)
        return [item.replace("*", "\n*") for item in merged_strings]

    def from_word_to_list(self, word):
        url = f"https://en.wiktionary.org/wiki/{quote(word.strip())}"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code != 200:
                return None
            soup = BeautifulSoup(response.content, "html.parser")
            if not soup.find("h2", string="English"):
                return None
            list_of_res = []
            for i in PARTS_OF_SPEECH:
                part_section = soup.find(id=i)
                if part_section:
                    def_list = part_section.findNext("ol")
                    if def_list:
                        processed_list = self.process_def_list(def_list)
                        if processed_list:
                            list_of_res.append(i)
                            list_of_res.extend(processed_list)
            return " ".join(list_of_res) if list_of_res else None
        except requests.RequestException:
            return None


class DBStatus(StrEnum):
    OK = "ok"
    EXISTS = "exists"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass(frozen=True)
class DBResult:
    code: DBStatus
    detail: str | None = None

    @property
    def success(self):
        return self.code is DBStatus.OK


class DBManager:
    _COLUMNS = "id, word, definition, created_at, updated_at, stage, revision_date"
    _SORT_COLUMNS = {
        SortKey.UPDATED: "updated_at",
        SortKey.ALPHABET: "word COLLATE NOCASE",
        SortKey.CREATED: "created_at",
    }

    def __init__(self, db_path="words.db"):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        with contextlib.closing(self.get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS dictionary (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "word TEXT UNIQUE NOT NULL, definition TEXT NOT NULL, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                f"stage INTEGER CHECK (stage BETWEEN {MIN_STAGE} AND {MAX_STAGE}) DEFAULT 1, "
                "revision_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
            )
            conn.commit()

    def is_dict_empty(self):
        with contextlib.closing(self.get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM dictionary;")
            return cursor.fetchone()[0] == 0

    def get_word(self, word):
        with contextlib.closing(self.get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {self._COLUMNS} FROM dictionary WHERE word=?;", (word,))
            row = cursor.fetchone()
            return Word.from_row(row) if row else None

    def get_page(self, sort_key, descending, page, page_size=PAGE_SIZE):
        column = self._SORT_COLUMNS[sort_key]
        order = "DESC" if descending else "ASC"
        with contextlib.closing(self.get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM dictionary")
            total = cursor.fetchone()[0]
            max_page = max(0, (total - 1) // page_size)
            page = min(max(0, page), max_page)
            cursor.execute(
                f"SELECT {self._COLUMNS} FROM dictionary ORDER BY {column} {order} LIMIT ? OFFSET ?",
                (page_size, page * page_size),
            )
            words = [Word.from_row(r) for r in cursor.fetchall()]
            return WordPage(words, total, page, page_size)

    def get_words_due(self, cutoff):
        with contextlib.closing(self.get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {self._COLUMNS} FROM dictionary WHERE revision_date <= ?;",
                (_ts(cutoff),),
            )
            return [Word.from_row(r) for r in cursor.fetchall()]

    def add_word(self, word, definition):
        try:
            with contextlib.closing(self.get_connection()) as conn:
                conn.execute(
                    "INSERT INTO dictionary (word, definition, updated_at) "
                    "VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (word, definition),
                )
                conn.commit()
            return DBResult(DBStatus.OK)
        except sqlite3.IntegrityError:
            return DBResult(DBStatus.EXISTS)
        except sqlite3.Error as e:
            return DBResult(DBStatus.ERROR, str(e))

    def update_word(self, word, definition):
        try:
            with contextlib.closing(self.get_connection()) as conn:
                cursor = conn.execute(
                    "UPDATE dictionary SET definition = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE word = ?",
                    (definition, word),
                )
                conn.commit()
                if cursor.rowcount == 0:
                    return DBResult(DBStatus.NOT_FOUND)
            return DBResult(DBStatus.OK)
        except sqlite3.Error as e:
            return DBResult(DBStatus.ERROR, str(e))

    def delete_word(self, word):
        try:
            with contextlib.closing(self.get_connection()) as conn:
                cursor = conn.execute(
                    "DELETE FROM dictionary WHERE word = ?", (word,))
                conn.commit()
                if cursor.rowcount == 0:
                    return DBResult(DBStatus.NOT_FOUND)
            return DBResult(DBStatus.OK)
        except sqlite3.Error as e:
            return DBResult(DBStatus.ERROR, str(e))

    def set_stage(self, word, stage, revision_at):
        try:
            with contextlib.closing(self.get_connection()) as conn:
                cursor = conn.execute(
                    "UPDATE dictionary SET stage = ?, revision_date = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE word = ?;",
                    (stage, _ts(revision_at), word),
                )
                conn.commit()
                if cursor.rowcount == 0:
                    return DBResult(DBStatus.NOT_FOUND)
            return DBResult(DBStatus.OK)
        except sqlite3.Error as e:
            return DBResult(DBStatus.ERROR, str(e))


class StudyPlanner:
    STAGE_INTERVALS = {
        1: timedelta(0),
        2: timedelta(minutes=5),
        3: timedelta(minutes=10),
        4: timedelta(hours=1),
        5: timedelta(days=1),
        6: timedelta(days=3),
        7: timedelta(days=7),
        8: timedelta(days=14),
        9: timedelta(days=21),
        10: timedelta(days=30),
        11: timedelta(days=60),
        12: timedelta(days=90),
        13: timedelta(days=180),
        14: timedelta(days=270),
        15: timedelta(days=365),
    }
    LOOKAHEAD_STAGES = (2, 3, 4)

    def __init__(self, db, rng=random):
        self.db = db
        self.rng = rng

    def next_word(self, advance_days=0):
        now = datetime.now(timezone.utc)
        horizons = [timedelta(days=advance_days)]
        horizons += [self.STAGE_INTERVALS[s] for s in self.LOOKAHEAD_STAGES]
        for horizon in horizons:
            due = self.db.get_words_due(now + horizon)
            if due:
                return self.rng.choice(due)
        return None

    @staticmethod
    def answer_stages(current_stage):
        stages = [MIN_STAGE]
        if current_stage != MIN_STAGE:
            stages.append(current_stage)
        stages += [s for s in (current_stage + 1,
                               current_stage + 2) if s <= MAX_STAGE]
        return stages

    def reschedule(self, word, stage):
        stage = min(MAX_STAGE, max(MIN_STAGE, stage))
        revision_at = datetime.now(timezone.utc) + self.STAGE_INTERVALS[stage]
        return self.db.set_stage(word, stage, revision_at)

    def reset(self, word):
        return self.reschedule(word, MIN_STAGE)


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

    @property
    def has_failures(self):
        return bool(self.failed)


class WordsAdder:
    def __init__(self, db, fetcher):
        self.db = db
        self.fetcher = fetcher

    def add_single_word(self, text):
        existing = self.db.get_word(text)
        if existing:
            return AddResult(DBStatus.EXISTS, existing)
        definition = self.fetcher.from_word_to_list(text)
        if not definition:
            return AddResult(DBStatus.NOT_FOUND)
        result = self.db.add_word(text, definition)
        if result.success:
            return AddResult(DBStatus.OK, self.db.get_word(text))
        if result.code is DBStatus.EXISTS:
            return AddResult(DBStatus.EXISTS, self.db.get_word(text))
        return AddResult(DBStatus.ERROR, detail=result.detail)

    def add_multiple_words(self, words):
        batch = BatchAddResult(added=[], already_added=[],
                               not_found=[], failed=[])
        for text in words:
            if self.db.get_word(text):
                batch.already_added.append(text)
                continue
            definition = self.fetcher.from_word_to_list(text)
            if not definition:
                batch.not_found.append(text)
                continue
            result = self.db.add_word(text, definition)
            if result.success:
                batch.added.append(text)
            elif result.code is DBStatus.EXISTS:
                batch.already_added.append(text)
            else:
                batch.failed.append((text, result.detail))
        return batch


class EditMode(Enum):
    NEW = "new"
    EXISTING = "existing"


@dataclass
class ListState:
    sort_key: SortKey = SortKey.CREATED
    descending: bool = False
    page: int = 0


SORT_LABELS = {
    SortKey.UPDATED: "дате изменения",
    SortKey.ALPHABET: "алфавиту",
    SortKey.CREATED: "дате создания",
}
STAGE_LABELS = {
    1: "<1 мин", 2: "<5 мин", 3: "<1 час", 4: "<1 час", 5: "1 день",
    6: "3 дня", 7: "7 дней", 8: "14 дней", 9: "21 день", 10: "1 месяц",
    11: "2 месяца", 12: "3 месяца", 13: "6 месяцев", 14: "9 месяцев", 15: "1 год",
}


class Screen(tk.Frame):
    def __init__(self, window, controller):
        super().__init__(window, bg="linen")
        self.controller = controller


class MainMenuUI(Screen):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.search_entry = tk.Entry(self, width=30)
        self.search_entry.pack(pady=(60, 20))
        tk.Button(self, text="Найти слово", width=25,
                  command=self.on_search).pack(pady=10)
        tk.Button(self, text="Список слов", width=25,
                  command=controller.open_word_list).pack(pady=10)
        tk.Button(self, text="Учить слова", width=25,
                  command=controller.open_learn).pack(pady=10)
        tk.Button(self, text="Выйти", width=25,
                  command=controller.quit).pack(pady=10)

    def on_search(self):
        request = self.search_entry.get().strip()
        if request:
            self.controller.handle_add_request(request)


class EditWordUI(Screen):
    def __init__(self, parent, controller, word, mode, from_list):
        super().__init__(parent, controller)
        self.word = word
        self.mode = mode
        self.from_list = from_list
        self.create_widgets()

    def create_widgets(self):
        tk.Label(self, text=self.word.text, font=(
            "Arial", 20), bg="linen").pack(pady=15)
        self.edit_entry = scrolledtext.ScrolledText(self, height=10, width=65)
        self.edit_entry.insert("1.0", self.word.definition)
        self.edit_entry.pack(pady=5)
        self.apply_bold_formatting()
        self.create_buttons()

    def apply_bold_formatting(self):
        self.edit_entry.tag_configure(
            "bold", font=font.Font(size=10, weight="bold"))
        for pos in PARTS_OF_SPEECH:
            start_index = "1.0"
            while True:
                start_index = self.edit_entry.search(
                    pos, start_index, stopindex=tk.END)
                if not start_index:
                    break
                end_index = f"{start_index}+{len(pos)}c"
                self.edit_entry.tag_add("bold", start_index, end_index)
                start_index = end_index

    def create_buttons(self):
        c = self.controller
        button_frame = tk.Frame(self, bg="linen")
        button_frame.pack(pady=10)
        tk.Button(button_frame, text="Сохранить",
                  command=self.save_word).pack(side="left", padx=5)
        is_new = self.mode is EditMode.NEW
        tk.Button(
            button_frame,
            text="Не сохранять" if is_new else "Удалить",
            command=lambda: c.delete_word(self.word.text),
        ).pack(side="left", padx=5)
        if not is_new:
            tk.Button(
                button_frame,
                text="Обнулить прогресс",
                command=lambda: c.reset_word_progress(self.word.text),
            ).pack(side="left", padx=5)
        nav_frame = tk.Frame(self, bg="linen")
        nav_frame.pack(pady=5)
        if self.from_list:
            tk.Button(nav_frame, text="Список слов",
                      command=c.return_to_list).pack(side="left", padx=5)
        tk.Button(nav_frame, text="Главное меню",
                  command=c.open_main_menu).pack(side="left", padx=5)

    def save_word(self):
        definition = self.edit_entry.get("1.0", "end-1c")
        self.controller.save_word_definition(self.word.text, definition)


class WordListUI(Screen):
    def __init__(self, parent, controller, page, state):
        super().__init__(parent, controller)
        self.page = page
        self.state = state
        self.create_widgets()

    def create_widgets(self):
        start = self.page.page * self.page.page_size + 1
        for i, word in enumerate(self.page.words, start=start):
            tk.Button(
                self,
                text=f"{i}) {word.text}",
                wraplength=500,
                anchor="center",
                width=65,
                command=lambda w=word: self.controller.open_word(w),
            ).pack(anchor="center", pady=2)
        self.create_bottom_controls()

    def create_bottom_controls(self):
        c = self.controller
        label_to_key = {label: key for key, label in SORT_LABELS.items()}
        sort_frame = tk.Frame(self, bg="linen")
        sort_frame.pack(side="bottom", pady=5)
        selected = tk.StringVar(value=SORT_LABELS[self.state.sort_key])
        arrow = "↓" if self.state.descending else "↑"
        tk.Button(
            sort_frame,
            text=f"Сортировать по {arrow}",
            command=lambda: c.sort_by(label_to_key[selected.get()]),
        ).pack(side="left", padx=2)
        tk.OptionMenu(sort_frame, selected, *SORT_LABELS.values()
                      ).pack(side="left", padx=2)
        nav_frame = tk.Frame(self, bg="linen")
        nav_frame.pack(side="bottom", pady=2)
        if self.page.has_prev:
            tk.Button(nav_frame, text="Назад",
                      command=c.prev_page).pack(side="left")
        if self.page.has_prev or self.page.has_next:
            tk.Label(
                nav_frame,
                text=f"Страница {self.page.page + 1}/{self.page.max_page + 1}",
                bg="linen",
            ).pack(side="left", padx=5)
        if self.page.has_next:
            tk.Button(nav_frame, text="Вперёд",
                      command=c.next_page).pack(side="left")
        lower_frame = tk.Frame(self, bg="linen")
        lower_frame.pack(side="bottom", pady=2)
        tk.Button(lower_frame, text="Главное меню",
                  command=c.open_main_menu).pack(side="left", padx=5)
        if self.page.total > self.page.page_size:
            page_entry = tk.Entry(lower_frame, width=5)
            page_entry.pack(side="left", padx=2)
            tk.Button(
                lower_frame,
                text="Перейти",
                command=lambda: c.jump_to_page(page_entry.get()),
            ).pack(side="left")


class LearnUI(Screen):
    def __init__(self, parent, controller, word, revealed, answer_stages):
        super().__init__(parent, controller)
        self.word = word
        self.revealed = revealed
        self.answer_stages = answer_stages
        self.create_widgets()

    def create_widgets(self):
        nav_frame = tk.Frame(self, bg="linen")
        nav_frame.pack(side="top", anchor="w", padx=10, pady=5)
        tk.Button(nav_frame, text="Главное меню",
                  command=self.controller.open_main_menu).pack()
        if not self.revealed:
            tk.Label(self, text=self.word.text, font=(
                "Arial", 22), bg="linen").pack(pady=40)
        else:
            card = scrolledtext.ScrolledText(
                self, wrap="word", height=12, width=65)
            card.pack(side="top", pady=10)
            card.insert("1.0", self.word.definition)
        button_frame = tk.Frame(self, bg="linen")
        button_frame.pack(pady=10, side="bottom")
        if not self.revealed:
            tk.Button(
                button_frame,
                text="Показать",
                width=15,
                command=self.controller.reveal_card,
            ).pack(side="left", padx=5, pady=20)
        else:
            for stage in self.answer_stages:
                tk.Button(
                    button_frame,
                    text=STAGE_LABELS[stage],
                    command=lambda s=stage: self.controller.grade_word(s),
                ).pack(side="left", padx=3, pady=10)


class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Flashcards App")
        self.geometry("600x600")
        self.configure(bg="linen")
        self.current_frame = None

    def switch_screen(self, frame_class, controller, *args, **kwargs):
        if self.current_frame is not None:
            self.current_frame.destroy()
        self.current_frame = frame_class(self, controller, *args, **kwargs)
        self.current_frame.pack(fill="both", expand=True)

    def show_message(self, text, title="Уведомление"):
        messagebox.showinfo(title, text)

    def show_warning(self, text, title="Внимание"):
        messagebox.showwarning(title, text)

    def show_error(self, text, title="Ошибка"):
        messagebox.showerror(title, text)

    def ask_advance_days(self):
        return simpledialog.askstring(
            "Отлично!",
            "Все слова на сейчас повторены.\nЕсли хотите, выберите на сколько дней\nхотите идти вперёд плана:",
        )


def _db_error_text(code, detail):
    _DB_STATUS_TEXT = {
        DBStatus.NOT_FOUND: "Слово не найдено.",
        DBStatus.EXISTS: "Такое слово уже есть в словаре.",
    }
    if code is DBStatus.ERROR:
        return f"Ошибка базы данных: {detail}"
    return _DB_STATUS_TEXT.get(code, "Неизвестная ошибка")


def _format_batch(batch):
    parts = []
    if batch.added:
        parts.append(f"Добавлены: {', '.join(batch.added)}")
    if batch.already_added:
        parts.append(f"Уже в словаре: {', '.join(batch.already_added)}")
    if batch.not_found:
        parts.append(f"Не найдены: {', '.join(batch.not_found)}")
    if batch.failed:
        details = "; ".join(f"{w} ({d})" for w, d in batch.failed)
        parts.append(f"Не удалось сохранить: {details}")
    return "\n".join(parts)


class WordListController:
    def __init__(self, view, db, router):
        self.view = view
        self.db = db
        self.router = router
        self.state = ListState()
        self._page = None

    def show(self):
        self._page = self.db.get_page(
            self.state.sort_key, self.state.descending, self.state.page)
        self.state.page = self._page.page
        self.view.switch_screen(WordListUI, self, self._page, self.state)

    def sort_by(self, key):
        if key == self.state.sort_key:
            self.state.descending = not self.state.descending
        else:
            self.state.sort_key = key
        self.show()

    def next_page(self):
        self.state.page += 1
        self.show()

    def prev_page(self):
        self.state.page -= 1
        self.show()

    def jump_to_page(self, raw):
        try:
            target = int(raw) - 1
        except ValueError:
            return
        if self._page and 0 <= target <= self._page.max_page:
            self.state.page = target
            self.show()

    def open_word(self, word):
        self.router.open_edit(word, EditMode.EXISTING, from_list=True)

    def open_main_menu(self):
        self.router.open_main_menu()


class AddWordController:
    def __init__(self, view, words_adder, router):
        self.view = view
        self.words_adder = words_adder
        self.router = router

    def handle_add_request(self, request):
        words = [w.strip() for w in request.split(",") if w.strip()]
        if len(words) > 1:
            self._handle_multiple_add(words)
        elif len(words) == 1:
            self._handle_single_add(words[0])

    def _handle_single_add(self, text):
        result = self.words_adder.add_single_word(text)
        if result.code is DBStatus.OK:
            self.view.show_message(f'Слово "{text}" найдено онлайн.')
            self.router.open_edit(result.word, EditMode.NEW, from_list=False)
        elif result.code is DBStatus.EXISTS:
            self.view.show_message(f'Слово "{text}" уже есть в словаре.')
            self.router.open_edit(
                result.word, EditMode.EXISTING, from_list=False)
        elif result.code is DBStatus.NOT_FOUND:
            self.view.show_message(
                "Слово не найдено в словаре или не является английским.")
            self.router.open_main_menu()
        else:
            self.view.show_error(
                f'Не удалось добавить слово "{text}".\n{_db_error_text(result.code, result.detail)}'
            )
            self.router.open_main_menu()

    def _handle_multiple_add(self, words):
        batch = self.words_adder.add_multiple_words(words)
        self.router.open_main_menu()
        text = _format_batch(batch)
        if batch.has_failures:
            self.view.show_warning(text, title="Результаты")
        else:
            self.view.show_message(text, title="Результаты")


class EditWordController:
    def __init__(self, view, db, planner, router):
        self.view = view
        self.db = db
        self.planner = planner
        self.router = router
        self._from_list = False

    def show(self, word, mode, from_list):
        self._from_list = from_list
        self.view.switch_screen(EditWordUI, self, word, mode, from_list)

    def save_word_definition(self, word, definition):
        result = self.db.update_word(word, definition)
        if self._report(result, "Определение обновлено."):
            self._leave_edit()

    def delete_word(self, word):
        result = self.db.delete_word(word)
        if self._report(result, "Слово удалено."):
            self._leave_edit()

    def reset_word_progress(self, word):
        self._report(self.planner.reset(word), "Прогресс изучения обнулён.")

    def return_to_list(self):
        self.router.list_ctrl.show()

    def open_main_menu(self):
        self.router.open_main_menu()

    def _leave_edit(self):
        if self._from_list:
            self.router.list_ctrl.show()
        else:
            self.router.open_main_menu()

    def _report(self, result, ok_text=None):
        if result.success:
            if ok_text:
                self.view.show_message(ok_text)
            return True
        self.view.show_error(_db_error_text(result.code, result.detail))
        return False


class StudyController:
    def __init__(self, view, planner, router):
        self.view = view
        self.planner = planner
        self.router = router
        self._card = None
        self._advance_days = 0

    def start(self, advance_days=0):
        self._advance_days = advance_days
        word = self.planner.next_word(advance_days)
        if word is None:
            self._offer_extension()
            return
        self._card = word
        self._show_card(revealed=False)

    def reveal_card(self):
        self._show_card(revealed=True)

    def grade_word(self, stage):
        result = self.planner.reschedule(self._card.text, stage)
        if not result.success:
            self.view.show_error(_db_error_text(result.code, result.detail))
        self.start(self._advance_days)

    def open_main_menu(self):
        self.router.open_main_menu()

    def _show_card(self, revealed):
        stages = self.planner.answer_stages(self._card.stage)
        self.view.switch_screen(LearnUI, self, self._card, revealed, stages)

    def _offer_extension(self):
        raw = self.view.ask_advance_days()
        if raw:
            try:
                days = int(raw)
                if days > 0:
                    self.start(days)
                    return
            except ValueError:
                pass
            self.view.show_error(
                "Вы ввели недопустимое значение.", title="Ошибка!")
        self.router.open_main_menu()


class AppController:
    def __init__(self):
        self.db = DBManager()
        self.fetcher = Fetcher()
        self.words_adder = WordsAdder(self.db, self.fetcher)
        self.planner = StudyPlanner(self.db)
        self.view = MainWindow()
        self.list_ctrl = WordListController(self.view, self.db, router=self)
        self.add_ctrl = AddWordController(
            self.view, self.words_adder, router=self)
        self.edit_ctrl = EditWordController(
            self.view, self.db, self.planner, router=self)
        self.study_ctrl = StudyController(self.view, self.planner, router=self)

    def run(self):
        self.open_main_menu()
        self.view.mainloop()

    def open_main_menu(self):
        self.view.switch_screen(MainMenuUI, self)

    def quit(self):
        self.view.quit()

    def open_word_list(self):
        if self._guard_not_empty():
            self.list_ctrl.show()

    def handle_add_request(self, request):
        self.add_ctrl.handle_add_request(request)

    def open_edit(self, word, mode, from_list):
        self.edit_ctrl.show(word, mode, from_list)

    def open_learn(self, advance_days=0):
        if self._guard_not_empty():
            self.study_ctrl.start(advance_days)

    def _guard_not_empty(self):
        if self.db.is_dict_empty():
            self.view.show_error(
                "Словарь пуст. Добавьте слова, чтобы начать их изучение.", title="Ошибка!")
            self.open_main_menu()
            return False
        return True


if __name__ == "__main__":
    AppController().run()
