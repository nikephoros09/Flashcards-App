from database import DBManager
from models import DBStatus, EditMode, ListState
from services import Fetcher, StudyPlanner, WordsAdder, process_string
from ui import EditWordUI, LearnUI, MainMenuUI, MainWindow, WordListUI

NETWORK_ERROR_TEXT = (
    "Не удалось связаться с Wiktionary."
)


def db_error_text(code, detail):
    _DB_STATUS_TEXT = {
        DBStatus.NOT_FOUND: "Слово не найдено.",
        DBStatus.EXISTS: "Такое слово уже есть в словаре.",
        DBStatus.NETWORK_ERROR: NETWORK_ERROR_TEXT,
    }
    if code is DBStatus.ERROR:
        return f"Ошибка базы данных: {detail}"
    return _DB_STATUS_TEXT.get(code, "Неизвестная ошибка")


def format_batch(batch):
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
    if batch.skipped:
        parts.append(
            f"Не обработаны из-за ошибки сети: {', '.join(batch.skipped)}\n"
            f"{NETWORK_ERROR_TEXT}"
        )
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
            self.multiple_add_UI(words)
        elif len(words) == 1:
            self.single_add_UI(words[0])

    def single_add_UI(self, text):
        processed_text = process_string(text)
        result = self.words_adder.add_single_word(processed_text)

        if result.code is DBStatus.OK:
            self.view.show_message(
                f'Слово "{result.word.text}" найдено онлайн.')
            self.router.open_edit(result.word, EditMode.NEW, from_list=False)
        elif result.code is DBStatus.EXISTS:
            self.view.show_message(
                f'Слово "{result.word.text}" уже есть в словаре.')
            self.router.open_edit(
                result.word, EditMode.EXISTING, from_list=False)
        elif result.code is DBStatus.NOT_FOUND:
            self.view.show_message(
                "Слово не найдено в словаре или не является английским.")
            self.router.open_main_menu()
        elif result.code is DBStatus.NETWORK_ERROR:
            self.view.show_error(
                f'Не удалось проверить слово "{processed_text}".\n{NETWORK_ERROR_TEXT}',
                title="Ошибка сети",
            )
        else:
            self.view.show_error(
                f'Не удалось добавить слово "{processed_text}".\n{db_error_text(result.code, result.detail)}'
            )
            self.router.open_main_menu()

    def multiple_add_UI(self, words):
        batch = self.words_adder.add_multiple_words(words)
        self.router.open_main_menu()
        text = format_batch(batch)
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
        if self.report(result, "Определение обновлено."):
            self.leave_edit()

    def delete_word(self, word):
        result = self.db.delete_word(word)
        if self.report(result, "Слово удалено."):
            self.leave_edit()

    def reset_word_progress(self, word):
        self.report(self.planner.reset(word), "Прогресс изучения обнулён.")

    def return_to_list(self):
        self.router.list_ctrl.show()

    def open_main_menu(self):
        self.router.open_main_menu()

    def leave_edit(self):
        if self._from_list:
            self.router.list_ctrl.show()
        else:
            self.router.open_main_menu()

    def report(self, result, ok_text=None):
        if result.success:
            if ok_text:
                self.view.show_message(ok_text)
            return True
        self.view.show_error(db_error_text(result.code, result.detail))
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
            self.offer_extension()
            return
        self._card = word
        self.show_card(revealed=False)

    def reveal_card(self):
        self.show_card(revealed=True)

    def grade_word(self, stage):
        result = self.planner.reschedule(self._card.text, stage)
        if not result.success:
            self.view.show_error(db_error_text(result.code, result.detail))
        self.start(self._advance_days)

    def open_main_menu(self):
        self.router.open_main_menu()

    def show_card(self, revealed):
        stages = self.planner.answer_stages(self._card.stage)
        self.view.switch_screen(LearnUI, self, self._card, revealed, stages)

    def offer_extension(self):
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
        if self.guard_not_empty():
            self.list_ctrl.show()

    def handle_add_request(self, request):
        self.add_ctrl.handle_add_request(request)

    def open_edit(self, word, mode, from_list):
        self.edit_ctrl.show(word, mode, from_list)

    def open_learn(self, advance_days=0):
        if self.guard_not_empty():
            self.study_ctrl.start(advance_days)

    def guard_not_empty(self):
        if self.db.is_dict_empty():
            self.view.show_error(
                "Словарь пуст. Добавьте слова, чтобы начать их изучение.", title="Ошибка!")
            self.open_main_menu()
            return False
        return True


if __name__ == "__main__":
    AppController().run()
