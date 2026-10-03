import contextlib
import sqlite3

from models import (
    MAX_STAGE,
    MIN_STAGE,
    PAGE_SIZE,
    DBResult,
    DBStatus,
    SortKey,
    Word,
    WordPage,
    ts,
)


class DBManager:
    ID_COLUMNS = "id, word, definition, created_at, updated_at, stage, revision_date"
    SORTED_COLUMNS = {
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
                f"SELECT {self.ID_COLUMNS} FROM dictionary WHERE word=?;", (word,))
            row = cursor.fetchone()
            return Word.from_row(row) if row else None

    def get_page(self, sort_key, descending, page, page_size=PAGE_SIZE):
        column = self.SORTED_COLUMNS[sort_key]
        order = "DESC" if descending else "ASC"
        with contextlib.closing(self.get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM dictionary")
            total = cursor.fetchone()[0]
            max_page = max(0, (total - 1) // page_size)
            page = min(max(0, page), max_page)
            cursor.execute(
                f"SELECT {self.ID_COLUMNS} FROM dictionary ORDER BY {column} {order} LIMIT ? OFFSET ?",
                (page_size, page * page_size),
            )
            words = [Word.from_row(r) for r in cursor.fetchall()]
            return WordPage(words, total, page, page_size)

    def get_words_due(self, cutoff):
        with contextlib.closing(self.get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {self.ID_COLUMNS} FROM dictionary WHERE revision_date <= ?;",
                (ts(cutoff),),
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
                    (stage, ts(revision_at), word),
                )
                conn.commit()
                if cursor.rowcount == 0:
                    return DBResult(DBStatus.NOT_FOUND)
            return DBResult(DBStatus.OK)
        except sqlite3.Error as e:
            return DBResult(DBStatus.ERROR, str(e))
