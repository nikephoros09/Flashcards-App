import tkinter as tk
from tkinter import font, messagebox, scrolledtext, simpledialog
from models import EditMode, PartOfSpeech, SortKey

SORT_LABELS = {
    SortKey.UPDATED: "дате изменения",
    SortKey.ALPHABET: "алфавиту",
    SortKey.CREATED: "дате создания",
}
STAGE_LABELS = {
    1: "<1 мин", 2: "<5 мин", 3: "<10 мин", 4: "<1 час", 5: "1 день",
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
        for pos in PartOfSpeech:
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
