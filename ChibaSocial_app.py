# coding: utf-8
"""千葉県高校入試サポートアプリ 〜社会科編〜 for Pythonista 3.

配置:
    ChibaSocial/ChibaSocial_app.py
    ChibaSocial/Days/Day01.json
    ChibaSocial/images/<画像ファイル>
"""

import base64
import html
import json
import mimetypes
import hashlib
import shutil
import urllib.request
from urllib.parse import urljoin
from datetime import date, datetime, timedelta
from pathlib import Path

import console
import ui


BASE_DIR = Path(__file__).resolve().parent
DAYS_DIR = BASE_DIR / "Days"
IMAGES_DIR = BASE_DIR / "images"
DATA_DIR = BASE_DIR / "Data"
EXPORTS_DIR = BASE_DIR / "Exports"
BACKUPS_DIR = BASE_DIR / "Backups"
HISTORY_PATH = DATA_DIR / "learning_history.json"
CONTENT_STATE_PATH = DATA_DIR / "content_version.json"
APP_VERSION = "2026.09.30.1"
UPDATE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/drugstar6-web/"
    "social-studies-quiz-app/main/version.json"
)

COLORS = {
    "background": "#F4F7FB",
    "card": "#FFFFFF",
    "primary": "#2155CD",
    "primary_dark": "#163A8A",
    "text": "#172033",
    "muted": "#65708A",
    "success": "#16803C",
    "danger": "#C93434",
    "line": "#DCE3EF",
}


def read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def available_days():
    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(DAYS_DIR.glob("Day*.json"))


def validate_lesson(data, expected_name):
    """ダウンロードした教材がアプリで安全に読めるか確認する。"""
    required = ("schema_version", "course", "day", "title", "goal", "terms", "questions")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError("{}に必要な項目がありません: {}".format(expected_name, ", ".join(missing)))
    if not isinstance(data["terms"], list) or not data["terms"]:
        raise ValueError("{}の重要語句が読めません".format(expected_name))
    if not isinstance(data["questions"], list) or not data["questions"]:
        raise ValueError("{}の問題が読めません".format(expected_name))
    ids = set()
    for question in data["questions"]:
        question_id = question.get("id")
        choices = question.get("choices")
        correct_index = question.get("correct_index")
        if not question_id or question_id in ids:
            raise ValueError("{}の問題IDが不正です".format(expected_name))
        ids.add(question_id)
        if not isinstance(choices, list) or len(choices) != 4:
            raise ValueError("{}の選択肢は4つ必要です".format(question_id))
        if not isinstance(correct_index, int) or not 0 <= correct_index < 4:
            raise ValueError("{}の正解番号が不正です".format(question_id))
        if not question.get("explanation"):
            raise ValueError("{}の解説がありません".format(question_id))


def fetch_bytes(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ChibaSocial-Pythonista/{}".format(APP_VERSION)},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def update_lessons_from_network():
    """GitHub上のversion.jsonを読み、検査に合格した教材だけ入れ替える。"""
    if not UPDATE_MANIFEST_URL:
        raise RuntimeError("配信先URLがまだ設定されていません")

    manifest_bytes = fetch_bytes(UPDATE_MANIFEST_URL)
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    content_version = str(manifest.get("content_version", "")).strip()
    files = manifest.get("files", [])
    if not content_version or not isinstance(files, list) or not files:
        raise ValueError("更新情報の形式が不正です")

    current_version = ""
    if CONTENT_STATE_PATH.exists():
        try:
            current_version = str(read_json(CONTENT_STATE_PATH).get("content_version", ""))
        except Exception:
            current_version = ""
    if current_version == content_version:
        return {"updated": False, "version": content_version, "files": []}

    staging_dir = BASE_DIR / "UpdateStaging"
    if staging_dir.exists():
        shutil.rmtree(str(staging_dir))
    staging_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    try:
        for item in files:
            name = str(item.get("name", ""))
            expected_hash = str(item.get("sha256", "")).lower()
            source_url = urljoin(UPDATE_MANIFEST_URL, str(item.get("url", "")))
            if not name.startswith("Day") or not name.endswith(".json") or "/" in name or "\\" in name:
                raise ValueError("配信ファイル名が不正です")
            if not source_url.startswith("https://") or len(expected_hash) != 64:
                raise ValueError("{}の配信設定が不正です".format(name))
            payload = fetch_bytes(source_url)
            actual_hash = hashlib.sha256(payload).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError("{}の内容が配信情報と一致しません".format(name))
            lesson = json.loads(payload.decode("utf-8"))
            validate_lesson(lesson, name)
            staged_path = staging_dir / name
            staged_path.write_bytes(payload)
            downloaded.append((name, staged_path))

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = BACKUPS_DIR / timestamp
        for name, staged_path in downloaded:
            destination = DAYS_DIR / name
            if destination.exists():
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(destination), str(backup_dir / name))
            staged_path.replace(destination)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temporary_state = CONTENT_STATE_PATH.with_suffix(".tmp")
        temporary_state.write_text(
            json.dumps(
                {
                    "content_version": content_version,
                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "files": [name for name, _ in downloaded],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary_state.replace(CONTENT_STATE_PATH)
        return {
            "updated": True,
            "version": content_version,
            "files": [name for name, _ in downloaded],
        }
    finally:
        if staging_dir.exists():
            shutil.rmtree(str(staging_dir))


class HistoryStore:
    """学習履歴をiCloud上のJSONへ保存する。"""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        self.data = self.load()
        self.save()

    @staticmethod
    def now():
        return datetime.now().astimezone()

    def default_data(self):
        today = date.today().isoformat()
        return {
            "schema_version": 1,
            "created_at": self.now().isoformat(timespec="seconds"),
            "study_plan": {
                "start_date": today,
                "study_weekdays": [0, 1, 2, 3, 4],
                "minimum_minutes": 10,
                "timezone": str(self.now().tzinfo),
            },
            "question_stats": {},
            "daily_activity": {},
            "events": [],
        }

    def load(self):
        if not HISTORY_PATH.exists():
            return self.default_data()
        try:
            return read_json(HISTORY_PATH)
        except Exception:
            backup = HISTORY_PATH.with_name(
                "learning_history_broken_{}.json".format(
                    self.now().strftime("%Y%m%d_%H%M%S")
                )
            )
            try:
                HISTORY_PATH.replace(backup)
            except Exception:
                pass
            return self.default_data()

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temporary = HISTORY_PATH.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(HISTORY_PATH)

    def daily_entry(self, timestamp):
        day_key = timestamp.date().isoformat()
        activity = self.data.setdefault("daily_activity", {})
        entry = activity.setdefault(
            day_key,
            {
                "first_activity_at": timestamp.isoformat(timespec="seconds"),
                "last_activity_at": timestamp.isoformat(timespec="seconds"),
                "reference_open_count": 0,
                "terms_viewed": 0,
                "questions_answered": 0,
                "correct": 0,
                "wrong": 0,
                "days_opened": [],
                "question_ids": [],
            },
        )
        entry["last_activity_at"] = timestamp.isoformat(timespec="seconds")
        return day_key, entry

    def append_event(self, event):
        events = self.data.setdefault("events", [])
        events.append(event)
        if len(events) > 5000:
            del events[:-5000]

    def record_reference(self, lesson):
        timestamp = self.now()
        day_key, entry = self.daily_entry(timestamp)
        lesson_key = "W{}D{}".format(lesson.get("week", 1), lesson.get("day", 1))
        entry["reference_open_count"] += 1
        entry["terms_viewed"] += len(lesson.get("terms", []))
        if lesson_key not in entry["days_opened"]:
            entry["days_opened"].append(lesson_key)
        self.append_event(
            {
                "timestamp": timestamp.isoformat(timespec="seconds"),
                "type": "reference_opened",
                "lesson": lesson_key,
                "term_count": len(lesson.get("terms", [])),
            }
        )
        self.save()

    def record_answer(self, question, selected_index, lesson):
        timestamp = self.now()
        day_key, entry = self.daily_entry(timestamp)
        question_id = question.get("id", "unknown")
        correct_index = question.get("correct_index")
        is_correct = selected_index == correct_index
        category = question.get("category", lesson.get("category", "uncategorized"))
        category_label = question.get(
            "category_label", lesson.get("category_label", "未分類")
        )

        stats = self.data.setdefault("question_stats", {})
        item = stats.setdefault(
            question_id,
            {
                "question_id": question_id,
                "category": category,
                "category_label": category_label,
                "tags": question.get("tags", []),
                "prompt": question.get("prompt", ""),
                "attempts": 0,
                "correct_count": 0,
                "wrong_count": 0,
                "current_streak": 0,
                "best_streak": 0,
                "wrong_choices": {},
            },
        )
        item["attempts"] += 1
        item["last_answer_index"] = selected_index
        item["last_correct"] = is_correct
        item["last_attempted_at"] = timestamp.isoformat(timespec="seconds")
        if is_correct:
            item["correct_count"] += 1
            item["current_streak"] += 1
            item["best_streak"] = max(item["best_streak"], item["current_streak"])
        else:
            item["wrong_count"] += 1
            item["current_streak"] = 0
            choice_key = str(selected_index)
            item["wrong_choices"][choice_key] = (
                item["wrong_choices"].get(choice_key, 0) + 1
            )

        entry["questions_answered"] += 1
        entry["correct" if is_correct else "wrong"] += 1
        if question_id not in entry["question_ids"]:
            entry["question_ids"].append(question_id)
        self.append_event(
            {
                "timestamp": timestamp.isoformat(timespec="seconds"),
                "type": "question_answered",
                "question_id": question_id,
                "category": category,
                "tags": question.get("tags", []),
                "selected_index": selected_index,
                "correct_index": correct_index,
                "is_correct": is_correct,
                "day": day_key,
            }
        )
        self.save()

    @staticmethod
    def estimated_minutes(entry):
        try:
            first = datetime.fromisoformat(entry["first_activity_at"])
            last = datetime.fromisoformat(entry["last_activity_at"])
            return max(1, min(720, round((last - first).total_seconds() / 60)))
        except Exception:
            return 1

    def analytics(self):
        stats = self.data.get("question_stats", {})
        totals = {
            "attempts": sum(x.get("attempts", 0) for x in stats.values()),
            "correct": sum(x.get("correct_count", 0) for x in stats.values()),
            "wrong": sum(x.get("wrong_count", 0) for x in stats.values()),
        }
        totals["accuracy_percent"] = round(
            100 * totals["correct"] / totals["attempts"], 1
        ) if totals["attempts"] else 0.0

        categories = {}
        for item in stats.values():
            category = item.get("category", "uncategorized")
            result = categories.setdefault(
                category,
                {
                    "category": category,
                    "label": item.get("category_label", "未分類"),
                    "attempts": 0,
                    "correct": 0,
                    "wrong": 0,
                },
            )
            result["attempts"] += item.get("attempts", 0)
            result["correct"] += item.get("correct_count", 0)
            result["wrong"] += item.get("wrong_count", 0)
        for result in categories.values():
            result["score"] = round(
                100 * result["correct"] / result["attempts"], 1
            ) if result["attempts"] else 0.0

        activity = self.data.get("daily_activity", {})
        active_dates = sorted(activity)
        plan = self.data.get("study_plan", {})
        try:
            start = date.fromisoformat(plan.get("start_date", date.today().isoformat()))
        except ValueError:
            start = date.today()
        weekdays = set(plan.get("study_weekdays", [0, 1, 2, 3, 4]))
        yesterday = date.today() - timedelta(days=1)
        missed_dates = []
        cursor = start
        while cursor <= yesterday:
            key = cursor.isoformat()
            if cursor.weekday() in weekdays and key not in activity:
                missed_dates.append(key)
            cursor += timedelta(days=1)

        enriched_activity = {}
        for key, entry in activity.items():
            item = dict(entry)
            item["estimated_minutes"] = self.estimated_minutes(entry)
            enriched_activity[key] = item

        return {
            "generated_at": self.now().isoformat(timespec="seconds"),
            "totals": totals,
            "categories": sorted(categories.values(), key=lambda x: x["label"]),
            "active_dates": active_dates,
            "missed_planned_dates": missed_dates,
            "daily_activity": enriched_activity,
        }

    def export(self):
        timestamp = self.now()
        export_path = EXPORTS_DIR / "SG_learning_export_{}.json".format(
            timestamp.strftime("%Y%m%d_%H%M%S")
        )
        payload = {
            "export_version": 1,
            "analytics": self.analytics(),
            "study_plan": self.data.get("study_plan", {}),
            "question_stats": self.data.get("question_stats", {}),
            "daily_activity": self.data.get("daily_activity", {}),
            "events": self.data.get("events", []),
        }
        export_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return export_path


STORE = HistoryStore()


class ZoomImageView(ui.View):
    """全画面画像ビュー。WebViewの標準ピンチズームを利用する。"""

    def __init__(self, image_path, title="画像"):
        super().__init__()
        self.frame = (0, 0, 375, 667)
        self.name = title
        self.background_color = "#111111"

        self.web = ui.WebView(frame=self.bounds, flex="WH")
        self.web.background_color = "#111111"
        self.add_subview(self.web)

        close_button = ui.Button(
            frame=(14, 58, 104, 46),
            title="× 閉じる",
            background_color="#E8ECF5",
            tint_color="#172033",
            corner_radius=10,
            action=self.close_view,
        )
        close_button.flex = "RB"
        self.add_subview(close_button)

        mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        image_uri = "data:{};base64,{}".format(mime_type, encoded)
        page = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0,
 maximum-scale=8.0, minimum-scale=0.25, user-scalable=yes">
<style>
html, body { margin:0; padding:0; width:100%; min-height:100%;
 background:#111; overflow:auto; -webkit-overflow-scrolling:touch; }
.stage { min-height:100vh; display:flex; align-items:center;
 justify-content:center; }
img { width:100%; height:auto; display:block; }
</style>
</head>
<body><div class="stage"><img src="__IMAGE_URI__"></div></body>
</html>""".replace("__IMAGE_URI__", image_uri)
        self.web.load_html(page)

    def close_view(self, sender):
        self.close()


class TextDetailView(ui.View):
    def __init__(self, title, body):
        super().__init__()
        self.frame = (0, 0, 375, 667)
        self.name = title
        self.background_color = COLORS["background"]

        self.text_view = ui.TextView()
        self.text_view.text = body
        self.text_view.font = ("<system>", 18)
        self.text_view.text_color = COLORS["text"]
        self.text_view.background_color = COLORS["card"]
        self.text_view.editable = False
        self.text_view.scroll_enabled = True
        self.text_view.corner_radius = 12
        self.text_view.content_inset = (16, 16, 16, 16)
        self.add_subview(self.text_view)

    def layout(self):
        margin = 12
        self.text_view.frame = (
            margin,
            margin,
            max(1, self.width - margin * 2),
            max(1, self.height - margin * 2),
        )


class ReferenceWebDelegate:
    def __init__(self, owner):
        self.owner = owner

    def webview_should_start_load(self, webview, url, nav_type):
        if url.startswith("sgimage://"):
            try:
                image_index = int(url.split("//", 1)[1].split("/", 1)[0])
                # WebViewのナビゲーション処理が終わってからモーダルを開く。
                # Pythonistaではdelegate内から即時presentすると失敗する場合がある。
                ui.delay(lambda: self.owner.open_image(image_index), 0.1)
            except (ValueError, IndexError):
                pass
            return False
        if url.startswith("sgnav://back"):
            self.owner.close()
            return False
        if url.startswith("sgnav://menu"):
            self.owner.open_menu()
            return False
        if url.startswith("sgnav://quiz"):
            self.owner.open_quiz()
            return False
        return True


class ReferenceView(ui.View):
    """1日分の参考書を、分割せず連続表示する画面。"""

    def __init__(self, lesson):
        super().__init__()
        self.frame = (0, 0, 375, 667)
        self.lesson = lesson
        self.images = lesson.get("images", [])
        self.zoom_view = None
        self.name = "参考書：Day {}".format(lesson.get("day", 1))
        self.background_color = COLORS["background"]
        STORE.record_reference(lesson)

        self.web = ui.WebView(frame=self.bounds, flex="WH")
        self.web.background_color = COLORS["background"]
        self.web_delegate = ReferenceWebDelegate(self)
        self.web.delegate = self.web_delegate
        self.add_subview(self.web)
        self.web.load_html(self.make_html())

    @staticmethod
    def escaped(value):
        return html.escape(str(value or ""))

    def image_html(self, image, image_index):
        image_path = IMAGES_DIR / image.get("file", "")
        if not image_path.exists():
            return (
                '<div class="missing">画像が見つかりません：{}</div>'.format(
                    self.escaped(image.get("file", ""))
                )
            )

        mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        title = self.escaped(image.get("title", image.get("file", "画像")))
        caption = self.escaped(image.get("caption", "タップして拡大"))
        return """
<figure>
  <div class="figure-title">{title}</div>
  <a href="sgimage://{index}">
    <img src="data:{mime};base64,{data}" alt="{title}">
  </a>
  <figcaption>{caption}｜タップすると全画面表示</figcaption>
</figure>
""".format(
            title=title,
            index=image_index,
            mime=mime_type,
            data=encoded,
            caption=caption,
        )

    def make_html(self):
        images_by_term = {}
        trailing_images = []
        for index, image in enumerate(self.images):
            item = dict(image)
            item["_index"] = index
            after_term_id = item.get("after_term_id")
            if after_term_id:
                images_by_term.setdefault(after_term_id, []).append(item)
            else:
                trailing_images.append(item)

        sections = []
        for number, term in enumerate(self.lesson.get("terms", []), 1):
            sections.append(
                """
<section class="term-card">
  <div class="term-number">{number}</div>
  <h2>{term}<span>{english}</span></h2>
  <h3>一言でいうと</h3><p>{summary}</p>
  <h3>身近な例</h3><p>{example}</p>
  <h3>見分け方</h3><p>{distinction}</p>
  <h3>問題文の合図</h3><p>{clue}</p>
</section>
""".format(
                    number=number,
                    term=self.escaped(term.get("term")),
                    english=self.escaped(term.get("english")),
                    summary=self.escaped(term.get("summary")),
                    example=self.escaped(term.get("example")),
                    distinction=self.escaped(term.get("distinction")),
                    clue=self.escaped(term.get("clue")),
                )
            )
            for image in images_by_term.get(term.get("id"), []):
                sections.append(self.image_html(image, image["_index"]))

        for image in trailing_images:
            sections.append(self.image_html(image, image["_index"]))

        page = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0,
 maximum-scale=1.0, user-scalable=no">
<style>
* { box-sizing: border-box; }
body { margin:0; padding:14px; background:#F4F7FB; color:#172033;
 font-family:-apple-system, BlinkMacSystemFont, sans-serif;
 -webkit-text-size-adjust:100%; }
header { background:linear-gradient(135deg,#2155CD,#163A8A); color:white;
 border-radius:16px; padding:18px; margin-bottom:14px; }
header h1 { font-size:24px; margin:0 0 8px; }
header p { font-size:15px; line-height:1.6; margin:0; opacity:.92; }
.term-card { position:relative; background:white; border-radius:16px;
 padding:18px; margin:0 0 14px; box-shadow:0 2px 10px rgba(30,55,90,.08); }
.term-number { position:absolute; right:14px; top:14px; width:30px; height:30px;
 line-height:30px; text-align:center; border-radius:15px; background:#E3EBFF;
 color:#2155CD; font-weight:700; }
h2 { font-size:23px; margin:0 38px 14px 0; line-height:1.35; }
h2 span { display:block; color:#65708A; font-size:13px; font-weight:500;
 margin-top:3px; }
h3 { font-size:14px; color:#2155CD; margin:14px 0 4px; }
p { font-size:17px; line-height:1.75; margin:0; overflow-wrap:anywhere; }
figure { background:white; border-radius:16px; padding:12px; margin:0 0 14px;
 box-shadow:0 2px 10px rgba(30,55,90,.08); }
.figure-title { font-size:18px; font-weight:700; margin:2px 2px 10px; }
figure img { display:block; width:100%; height:auto; border-radius:10px;
 border:1px solid #DCE3EF; }
figcaption { color:#65708A; font-size:13px; line-height:1.5; margin:8px 2px 2px; }
.missing { background:#FFE7E7; color:#C93434; border-radius:12px;
 padding:14px; margin-bottom:14px; overflow-wrap:anywhere; }
.footer-nav { display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px;
 margin:20px 0 18px; }
.nav-button { display:flex; align-items:center; justify-content:center;
 min-height:52px; border-radius:12px; text-decoration:none; font-size:15px;
 font-weight:700; -webkit-tap-highlight-color:transparent; }
.nav-back { background:#E8ECF5; color:#172033; }
.nav-menu { background:white; color:#163A8A; border:1px solid #2155CD; }
.nav-quiz { background:#2155CD; color:white; }
</style>
</head>
<body>
<header><h1>第__WEEK__週 Day __DAY__：__TITLE__</h1><p>__GOAL__</p></header>
__SECTIONS__
<nav class="footer-nav">
  <a class="nav-button nav-back" href="sgnav://back">戻る</a>
  <a class="nav-button nav-menu" href="sgnav://menu">メニュー</a>
  <a class="nav-button nav-quiz" href="sgnav://quiz">問題へ</a>
</nav>
</body>
</html>"""
        return (
            page.replace("__WEEK__", str(self.lesson.get("week", 1)))
            .replace("__DAY__", str(self.lesson.get("day", 1)))
            .replace("__TITLE__", self.escaped(self.lesson.get("title", "")))
            .replace("__GOAL__", self.escaped(self.lesson.get("goal", "")))
            .replace("__SECTIONS__", "".join(sections))
        )

    def open_image(self, image_index):
        if image_index < 0 or image_index >= len(self.images):
            return
        item = self.images[image_index]
        image_path = IMAGES_DIR / item.get("file", "")
        if not image_path.exists():
            console.alert(
                "画像が見つかりません",
                str(image_path),
                "OK",
                hide_cancel_button=True,
            )
            return
        # 表示中のビューを保持し、Pythonista側で破棄されないようにする。
        self.zoom_view = ZoomImageView(image_path, item.get("title", "画像"))
        self.zoom_view.present("fullscreen", hide_title_bar=True)

    def open_menu(self):
        MenuView().present("fullscreen", hide_title_bar=False)

    def open_quiz(self):
        questions = self.lesson.get("questions", [])
        if not questions:
            console.alert(
                "確認問題がありません", "", "OK", hide_cancel_button=True
            )
            return
        QuestionView(
            questions[0], questions=questions, current_index=0, lesson=self.lesson
        ).present("fullscreen", hide_title_bar=False)


class QuestionView(ui.View):
    def __init__(self, question, questions=None, current_index=0, lesson=None):
        super().__init__()
        self.frame = (0, 0, 375, 667)
        self.questions = questions or [question]
        self.lesson = lesson or {}
        self.current_index = current_index
        self.question = self.questions[self.current_index]
        self.selected_index = None
        self.graded = False
        self.name = "確認問題 {} / {}".format(
            self.current_index + 1, len(self.questions)
        )
        self.background_color = COLORS["background"]
        self.choice_buttons = []
        self.build()
        self.show_question(self.current_index)

    def build(self):
        self.scroll = ui.ScrollView(frame=self.bounds, flex="WH")
        self.scroll.background_color = COLORS["background"]
        self.scroll.always_bounce_vertical = True
        self.scroll.shows_vertical_scroll_indicator = True
        self.add_subview(self.scroll)

        self.previous_button = ui.Button(
            title="前の問題",
            background_color="#E8ECF5",
            tint_color=COLORS["text"],
            corner_radius=10,
            action=self.previous_question,
        )
        self.scroll.add_subview(self.previous_button)

        self.back_button = ui.Button(
            title="戻る",
            background_color=COLORS["card"],
            tint_color=COLORS["primary_dark"],
            border_color=COLORS["primary"],
            border_width=1,
            corner_radius=10,
            action=self.go_back,
        )
        self.scroll.add_subview(self.back_button)

        self.next_button = ui.Button(
            title="次の問題",
            background_color=COLORS["primary"],
            tint_color="white",
            corner_radius=10,
            action=self.next_question,
        )
        self.scroll.add_subview(self.next_button)

        self.prompt = ui.TextView()
        self.prompt.text = self.question["prompt"]
        self.prompt.font = ("<system-bold>", 18)
        self.prompt.text_color = COLORS["text"]
        self.prompt.background_color = COLORS["card"]
        self.prompt.editable = False
        self.prompt.scroll_enabled = False
        self.prompt.corner_radius = 12
        self.prompt.content_inset = (12, 12, 12, 12)
        self.scroll.add_subview(self.prompt)

        for index, choice in enumerate(self.question["choices"]):
            button = ui.Button(
                title="",
                background_color=COLORS["card"],
                tint_color=COLORS["text"],
                border_color=COLORS["line"],
                border_width=1,
                corner_radius=10,
                action=self.select_choice,
            )
            button.choice_index = index
            button.choice_label = ui.Label()
            button.choice_label.text = "{}. {}".format(chr(65 + index), choice)
            button.choice_label.font = ("<system>", 16)
            button.choice_label.text_color = COLORS["text"]
            button.choice_label.number_of_lines = 0
            button.choice_label.alignment = ui.ALIGN_LEFT
            button.choice_label.touch_enabled = False
            button.add_subview(button.choice_label)
            self.choice_buttons.append(button)
            self.scroll.add_subview(button)

        self.answer_label = ui.Label()
        self.answer_label.text = "あなたの回答：未選択"
        self.answer_label.font = ("<system-bold>", 16)
        self.answer_label.text_color = COLORS["text"]
        self.answer_label.background_color = "#EEF2F8"
        self.answer_label.border_color = COLORS["line"]
        self.answer_label.border_width = 1
        self.answer_label.corner_radius = 10
        self.answer_label.alignment = ui.ALIGN_CENTER
        self.scroll.add_subview(self.answer_label)

        self.check_button = ui.Button(
            title="答え合わせ",
            background_color=COLORS["primary"],
            tint_color="white",
            corner_radius=12,
            action=self.check_answer,
        )
        self.scroll.add_subview(self.check_button)

        self.judgement_label = ui.Label()
        self.judgement_label.text = ""
        self.judgement_label.font = ("<system-bold>", 20)
        self.judgement_label.text_color = COLORS["text"]
        self.judgement_label.background_color = COLORS["card"]
        self.judgement_label.corner_radius = 10
        self.judgement_label.alignment = ui.ALIGN_CENTER
        self.scroll.add_subview(self.judgement_label)

        self.result = ui.TextView()
        self.result.font = ("<system>", 17)
        self.result.text_color = COLORS["text"]
        self.result.background_color = COLORS["card"]
        self.result.editable = False
        self.result.scroll_enabled = True
        self.result.corner_radius = 12
        self.result.content_inset = (12, 12, 12, 12)
        self.result.text = "選択肢を選んでから答え合わせを押してください。"
        self.scroll.add_subview(self.result)

    def layout(self):
        self.scroll.frame = self.bounds
        margin = 12
        width = max(1, self.width - margin * 2)
        gap = 8
        nav_width = max(1, (width - gap * 2) / 3.0)
        self.previous_button.frame = (margin, 12, nav_width, 44)
        self.back_button.frame = (margin + nav_width + gap, 12, nav_width, 44)
        self.next_button.frame = (
            margin + (nav_width + gap) * 2, 12, nav_width, 44
        )
        y = 70

        self.prompt.frame = (margin, y, width, 155)
        y += 169

        choice_text_width = max(1, width - 28)
        for button in self.choice_buttons:
            natural_width = ui.measure_string(
                button.choice_label.text,
                font=("<system>", 16),
            )[0]
            measured = ui.measure_string(
                button.choice_label.text,
                font=("<system>", 16),
                max_width=choice_text_width,
            )
            estimated_lines = max(
                1, int((natural_width + choice_text_width - 1) // choice_text_width)
            )
            button_height = max(54, measured[1] + 24, estimated_lines * 22 + 24)
            button.frame = (margin, y, width, button_height)
            button.choice_label.frame = (
                14,
                8,
                choice_text_width,
                max(1, button_height - 16),
            )
            y += button_height + 10

        self.answer_label.frame = (margin, y + 2, width, 48)
        y += 60
        self.check_button.frame = (margin, y, width, 52)
        y += 64
        self.judgement_label.frame = (margin, y, width, 48)
        y += 60
        self.result.frame = (margin, y, width, 180)
        y += 194

        self.scroll.content_size = (self.width, max(y, self.height + 1))

    def show_question(self, index):
        if index < 0 or index >= len(self.questions):
            return
        self.current_index = index
        self.question = self.questions[index]
        self.selected_index = None
        self.graded = False
        self.name = "確認問題 {} / {}".format(index + 1, len(self.questions))
        self.prompt.text = self.question["prompt"]

        for choice_index, button in enumerate(self.choice_buttons):
            choice = self.question["choices"][choice_index]
            button.choice_label.text = "{}. {}".format(
                chr(65 + choice_index), choice
            )
            button.background_color = COLORS["card"]
            button.border_color = COLORS["line"]

        self.answer_label.text = "あなたの回答：未選択"
        self.answer_label.text_color = COLORS["text"]
        self.answer_label.background_color = "#EEF2F8"
        self.judgement_label.text = ""
        self.judgement_label.background_color = COLORS["card"]
        self.check_button.title = "答え合わせ"
        self.result.text = "選択肢を選んでから答え合わせを押してください。"
        self.result.text_color = COLORS["text"]
        self.previous_button.enabled = index > 0
        self.previous_button.alpha = 1.0 if index > 0 else 0.4
        self.next_button.enabled = index < len(self.questions) - 1
        self.next_button.alpha = 1.0 if index < len(self.questions) - 1 else 0.4
        self.layout()
        self.scroll.content_offset = (0, 0)

    def previous_question(self, sender):
        self.show_question(self.current_index - 1)

    def next_question(self, sender):
        self.show_question(self.current_index + 1)

    def go_back(self, sender):
        self.close()

    def select_choice(self, sender):
        self.selected_index = sender.choice_index
        selected_text = self.question["choices"][self.selected_index]
        self.answer_label.text = "あなたの回答：{}. {}".format(
            chr(65 + self.selected_index), selected_text
        )
        self.answer_label.text_color = COLORS["primary_dark"]
        self.answer_label.background_color = "#DDE8FF"
        self.judgement_label.text = ""
        self.judgement_label.background_color = COLORS["card"]
        self.result.text = "答え合わせボタンを押してください。"
        self.result.text_color = COLORS["text"]
        for view in self.subviews:
            if isinstance(view, ui.Button) and hasattr(view, "choice_index"):
                chosen = view.choice_index == self.selected_index
                view.background_color = "#DDE8FF" if chosen else COLORS["card"]
                view.border_color = COLORS["primary"] if chosen else COLORS["line"]

        for button in self.choice_buttons:
            chosen = button.choice_index == self.selected_index
            button.background_color = "#DDE8FF" if chosen else COLORS["card"]
            button.border_color = COLORS["primary"] if chosen else COLORS["line"]

    def check_answer(self, sender):
        if self.selected_index is None:
            self.answer_label.text = "あなたの回答：未選択"
            self.answer_label.text_color = COLORS["danger"]
            self.judgement_label.text = "選択肢を選んでください"
            self.judgement_label.text_color = COLORS["danger"]
            self.judgement_label.background_color = "#FFE7E7"
            return

        correct = self.question["correct_index"]
        is_correct = self.selected_index == correct
        if is_correct:
            self.judgement_label.text = "○ 正解"
            self.judgement_label.text_color = COLORS["success"]
            self.judgement_label.background_color = "#E5F6EA"
            self.result.text_color = COLORS["success"]
        else:
            correct_text = self.question["choices"][correct]
            self.judgement_label.text = "× 不正解　正解：{}. {}".format(
                chr(65 + correct), correct_text
            )
            self.judgement_label.text_color = COLORS["danger"]
            self.judgement_label.background_color = "#FFE7E7"
            self.result.text_color = COLORS["danger"]

        self.result.text = "解説\n\n{}".format(
            self.question.get("explanation", "")
        )
        if not self.graded:
            STORE.record_answer(
                self.question, self.selected_index, self.lesson
            )
            self.graded = True
            self.check_button.title = "記録済み"


class LessonDataSource:
    def __init__(self, app):
        self.app = app

    def tableview_number_of_sections(self, tableview):
        return 1

    def tableview_number_of_rows(self, tableview, section):
        return len(self.app.rows)

    def tableview_cell_for_row(self, tableview, section, row):
        item = self.app.rows[row]
        cell = ui.TableViewCell("subtitle")
        cell.background_color = COLORS["card"]
        cell.text_label.text_color = COLORS["text"]
        cell.detail_text_label.text_color = COLORS["muted"]
        cell.text_label.font = ("<system-bold>", 17)

        kind = item["kind"]
        if kind == "reference":
            cell.text_label.text = "参考書パート"
            cell.detail_text_label.text = "用語解説と画像を続けて読む"
            cell.accessory_type = "disclosure_indicator"
        elif kind == "quiz":
            cell.text_label.text = "確認問題パート"
            cell.detail_text_label.text = "全{}問を順番に解く".format(item["count"])
            cell.accessory_type = "disclosure_indicator"
        return cell

    def tableview_did_select(self, tableview, section, row):
        tableview.selected_row = (-1, -1)
        self.app.open_item(self.app.rows[row])


class SGApp(ui.View):
    def __init__(self, initial_index=0):
        super().__init__()
        self.frame = (0, 0, 375, 667)
        self.name = "社会科入試サポート"
        self.background_color = COLORS["background"]
        self.day_files = available_days()
        self.current_index = initial_index
        self.lesson = None
        self.rows = []
        self.build()

    def build(self):
        self.title_label = ui.Label()
        self.title_label.font = ("<system-bold>", 20)
        self.title_label.text_color = COLORS["primary_dark"]
        self.title_label.adjusts_font_size_to_fit_width = True
        self.add_subview(self.title_label)

        self.subtitle_label = ui.Label()
        self.subtitle_label.font = ("<system>", 14)
        self.subtitle_label.text_color = COLORS["muted"]
        self.subtitle_label.adjusts_font_size_to_fit_width = True
        self.add_subview(self.subtitle_label)

        self.previous_button = ui.Button(
            title="前の日",
            background_color="#E8ECF5",
            tint_color=COLORS["text"],
            corner_radius=10,
            action=self.previous_day,
        )
        self.add_subview(self.previous_button)

        self.menu_button = ui.Button(
            title="メニュー",
            background_color=COLORS["card"],
            tint_color=COLORS["primary_dark"],
            border_color=COLORS["primary"],
            border_width=1,
            corner_radius=10,
            action=self.open_menu,
        )
        self.add_subview(self.menu_button)

        self.next_button = ui.Button(
            title="次の日",
            background_color=COLORS["primary"],
            tint_color="white",
            corner_radius=10,
            action=self.next_day,
        )
        self.add_subview(self.next_button)

        self.table = ui.TableView()
        self.table.row_height = 64
        self.table.separator_color = COLORS["line"]
        self.table.background_color = COLORS["card"]
        self.table.corner_radius = 12
        self.table.scroll_enabled = True
        self.table.always_bounce_vertical = True
        self.table.shows_vertical_scroll_indicator = True
        self.data_source = LessonDataSource(self)
        self.table.data_source = self.data_source
        self.table.delegate = self.data_source
        self.add_subview(self.table)

        if not self.day_files:
            self.title_label.text = "教材がありません"
            self.subtitle_label.text = "DaysフォルダにDay01.jsonを追加してください。"
            return
        self.load_day(self.current_index)

    def layout(self):
        margin = 12
        width = max(1, self.width - margin * 2)

        self.title_label.frame = (margin, 10, width, 34)
        self.subtitle_label.frame = (margin, 46, width, 28)
        gap = 8
        button_width = max(1, (width - gap * 2) / 3.0)
        self.previous_button.frame = (margin, 82, button_width, 42)
        self.menu_button.frame = (
            margin + button_width + gap, 82, button_width, 42
        )
        self.next_button.frame = (
            margin + (button_width + gap) * 2, 82, button_width, 42
        )
        self.table.frame = (
            8,
            136,
            max(1, self.width - 16),
            max(1, self.height - 144),
        )

    def load_day(self, index):
        if not self.day_files:
            return
        self.current_index = max(0, min(index, len(self.day_files) - 1))
        self.lesson = read_json(self.day_files[self.current_index])
        self.title_label.text = "第{}週 Day {}：{}".format(
            self.lesson.get("week", 1),
            self.lesson.get("day", self.current_index + 1),
            self.lesson.get("title", "学習"),
        )
        self.subtitle_label.text = self.lesson.get("goal", "")

        self.rows = [
            {"kind": "reference"},
            {
                "kind": "quiz",
                "count": len(self.lesson.get("questions", [])),
            },
        ]
        self.table.reload()

    def previous_day(self, sender):
        self.load_day(self.current_index - 1)

    def next_day(self, sender):
        self.load_day(self.current_index + 1)

    def open_menu(self, sender):
        MenuView().present("fullscreen", hide_title_bar=False)

    def open_item(self, item):
        if item["kind"] == "reference":
            ReferenceView(self.lesson).present("fullscreen", hide_title_bar=False)
        elif item["kind"] == "quiz":
            questions = self.lesson.get("questions", [])
            if not questions:
                console.alert(
                    "確認問題がありません", "", "OK", hide_cancel_button=True
                )
                return
            QuestionView(
                questions[0],
                questions=questions,
                current_index=0,
                lesson=self.lesson,
            ).present("fullscreen", hide_title_bar=False)


class DayListDataSource:
    def __init__(self, owner):
        self.owner = owner

    def tableview_number_of_sections(self, tableview):
        return 1

    def tableview_number_of_rows(self, tableview, section):
        return len(self.owner.day_items)

    def tableview_cell_for_row(self, tableview, section, row):
        item = self.owner.day_items[row]
        cell = ui.TableViewCell("subtitle")
        cell.background_color = COLORS["card"]
        cell.text_label.text = "第{}週 Day {}：{}".format(
            item["week"], item["day"], item["title"]
        )
        cell.text_label.font = ("<system-bold>", 17)
        cell.text_label.text_color = COLORS["text"]
        cell.detail_text_label.text = item.get("goal", "")
        cell.detail_text_label.text_color = COLORS["muted"]
        cell.accessory_type = "disclosure_indicator"
        return cell

    def tableview_did_select(self, tableview, section, row):
        tableview.selected_row = (-1, -1)
        SGApp(initial_index=row).present("fullscreen", hide_title_bar=False)


class DayListView(ui.View):
    def __init__(self):
        super().__init__()
        self.frame = (0, 0, 375, 667)
        self.name = "学習コンテンツ"
        self.background_color = COLORS["background"]
        self.day_items = []

        for path in available_days():
            try:
                data = read_json(path)
                self.day_items.append(
                    {
                        "week": data.get("week", 1),
                        "day": data.get("day", len(self.day_items) + 1),
                        "title": data.get("title", path.stem),
                        "goal": data.get("goal", ""),
                    }
                )
            except Exception as error:
                self.day_items.append(
                    {
                        "week": "?",
                        "day": "?",
                        "title": path.name,
                        "goal": "読込エラー: {}".format(error),
                    }
                )

        self.guide = ui.Label()
        self.guide.text = "学習する日を選択してください"
        self.guide.font = ("<system-bold>", 19)
        self.guide.text_color = COLORS["primary_dark"]
        self.add_subview(self.guide)

        self.table = ui.TableView()
        self.table.row_height = 72
        self.table.background_color = COLORS["card"]
        self.table.separator_color = COLORS["line"]
        self.table.corner_radius = 12
        self.table.scroll_enabled = True
        self.table.always_bounce_vertical = True
        self.data_source = DayListDataSource(self)
        self.table.data_source = self.data_source
        self.table.delegate = self.data_source
        self.add_subview(self.table)

    def layout(self):
        margin = 12
        self.guide.frame = (margin, 12, self.width - margin * 2, 34)
        self.table.frame = (
            8,
            56,
            max(1, self.width - 16),
            max(1, self.height - 64),
        )


class SearchDataSource:
    def __init__(self, owner):
        self.owner = owner

    def tableview_number_of_sections(self, tableview):
        return 1

    def tableview_number_of_rows(self, tableview, section):
        return len(self.owner.results)

    def tableview_cell_for_row(self, tableview, section, row):
        item = self.owner.results[row]
        cell = ui.TableViewCell("subtitle")
        cell.background_color = COLORS["card"]
        cell.text_label.text = item["term"]
        cell.text_label.font = ("<system-bold>", 17)
        cell.text_label.text_color = COLORS["text"]
        cell.detail_text_label.text = "Day {}｜{}".format(
            item["day"], item["summary"]
        )
        cell.detail_text_label.text_color = COLORS["muted"]
        cell.accessory_type = "disclosure_indicator"
        return cell

    def tableview_did_select(self, tableview, section, row):
        tableview.selected_row = (-1, -1)
        item = self.owner.results[row]
        parts = [
            "一言でいうと\n{}".format(item["summary"]),
            "身近な例\n{}".format(item.get("example", "")),
            "見分け方\n{}".format(item.get("distinction", "")),
            "問題文の合図\n{}".format(item.get("clue", "")),
            "収録：第{}週 Day {}".format(item.get("week", 1), item["day"]),
        ]
        TextDetailView(item["term"], "\n\n".join(parts)).present(
            "fullscreen", hide_title_bar=False
        )


class SearchView(ui.View):
    def __init__(self):
        super().__init__()
        self.frame = (0, 0, 375, 667)
        self.name = "単語検索"
        self.background_color = COLORS["background"]
        self.all_terms = self.load_all_terms()
        self.results = []

        self.search_field = ui.TextField()
        self.search_field.placeholder = "例：機密性、Confidentiality"
        self.search_field.font = ("<system>", 17)
        self.search_field.background_color = COLORS["card"]
        self.search_field.border_style = 3
        self.search_field.clear_button_mode = "while_editing"
        self.search_field.return_key_type = "search"
        self.search_field.action = self.perform_search
        self.add_subview(self.search_field)

        self.search_button = ui.Button(
            title="検索",
            background_color=COLORS["primary"],
            tint_color="white",
            corner_radius=10,
            action=self.perform_search,
        )
        self.add_subview(self.search_button)

        self.status = ui.Label()
        self.status.text = "全Dayの用語を検索できます"
        self.status.font = ("<system>", 14)
        self.status.text_color = COLORS["muted"]
        self.add_subview(self.status)

        self.table = ui.TableView()
        self.table.row_height = 72
        self.table.background_color = COLORS["card"]
        self.table.separator_color = COLORS["line"]
        self.table.corner_radius = 12
        self.table.scroll_enabled = True
        self.table.always_bounce_vertical = True
        self.data_source = SearchDataSource(self)
        self.table.data_source = self.data_source
        self.table.delegate = self.data_source
        self.add_subview(self.table)

    def load_all_terms(self):
        terms = []
        for path in available_days():
            try:
                lesson = read_json(path)
            except Exception:
                continue
            for term in lesson.get("terms", []):
                item = dict(term)
                item["week"] = lesson.get("week", 1)
                item["day"] = lesson.get("day", 1)
                terms.append(item)
        return terms

    def perform_search(self, sender):
        query = self.search_field.text.strip().casefold()
        self.search_field.end_editing()
        if not query:
            self.results = []
            self.status.text = "検索する単語を入力してください"
            self.table.reload()
            return

        fields = ("term", "english", "summary", "example", "distinction", "clue")
        self.results = [
            item
            for item in self.all_terms
            if any(query in str(item.get(field, "")).casefold() for field in fields)
        ]
        self.status.text = "{}件見つかりました".format(len(self.results))
        self.table.reload()

    def layout(self):
        margin = 12
        button_width = 72
        self.search_field.frame = (
            margin,
            12,
            max(1, self.width - margin * 3 - button_width),
            44,
        )
        self.search_button.frame = (
            self.width - margin - button_width,
            12,
            button_width,
            44,
        )
        self.status.frame = (margin, 62, self.width - margin * 2, 26)
        self.table.frame = (
            8,
            96,
            max(1, self.width - 16),
            max(1, self.height - 104),
        )


class StudyDataView(ui.View):
    def __init__(self):
        super().__init__()
        self.frame = (0, 0, 375, 667)
        self.name = "学習成果"
        self.background_color = COLORS["background"]

        self.dashboard = ui.WebView()
        self.dashboard.background_color = COLORS["background"]
        self.add_subview(self.dashboard)
        self.refresh_dashboard()

        self.export_button = ui.Button(
            title="分析用JSONを書き出す",
            background_color=COLORS["primary"],
            tint_color="white",
            corner_radius=12,
            action=self.export_data,
        )
        self.export_button.font = ("<system-bold>", 17)
        self.add_subview(self.export_button)

    @staticmethod
    def current_streak(active_dates):
        active = set(active_dates)
        cursor = date.today()
        if cursor.isoformat() not in active:
            cursor -= timedelta(days=1)
        streak = 0
        while cursor.isoformat() in active:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def refresh_dashboard(self):
        self.dashboard.load_html(self.dashboard_html())

    def did_appear(self):
        self.refresh_dashboard()

    def dashboard_html(self):
        report = STORE.analytics()
        totals = report["totals"]
        today_key = date.today().isoformat()
        today = report["daily_activity"].get(today_key, {})
        categories = report["categories"]
        missed = report["missed_planned_dates"]
        streak = self.current_streak(report["active_dates"])

        category_rows = []
        if categories:
            for item in categories:
                score = max(0, min(100, float(item["score"])))
                category_rows.append(
                    '<div class="metric-row"><div class="metric-head"><b>{}</b>'
                    '<span>{}% &nbsp; {}/{}問</span></div>'
                    '<div class="track"><div class="fill" style="width:{}%"></div>'
                    '</div></div>'.format(
                        html.escape(str(item["label"])), score,
                        item["correct"], item["attempts"], score
                    )
                )
        else:
            category_rows.append('<p class="empty">まだ解答データがありません。</p>')

        activity_rows = []
        recent_entries = []
        for offset in range(6, -1, -1):
            target = date.today() - timedelta(days=offset)
            entry = report["daily_activity"].get(target.isoformat(), {})
            recent_entries.append((target, entry))
        max_answers = max(
            [entry.get("questions_answered", 0) for _, entry in recent_entries] + [1]
        )
        weekdays = ["月", "火", "水", "木", "金", "土", "日"]
        for target, entry in recent_entries:
            correct = int(entry.get("correct", 0))
            wrong = int(entry.get("wrong", 0))
            answered = int(entry.get("questions_answered", 0))
            correct_width = 100 * correct / max_answers
            wrong_width = 100 * wrong / max_answers
            activity_rows.append(
                '<div class="day-row"><span class="day-label">{}/{}({})</span>'
                '<div class="day-track"><i class="day-ok" style="width:{}%"></i>'
                '<i class="day-ng" style="width:{}%"></i></div>'
                '<b class="day-count">{}問</b></div>'.format(
                    target.month, target.day, weekdays[target.weekday()],
                    correct_width, wrong_width, answered
                )
            )

        question_stats = STORE.data.get("question_stats", {})
        weak_items = sorted(
            [item for item in question_stats.values() if item.get("wrong_count", 0)],
            key=lambda item: (
                item.get("wrong_count", 0), item.get("attempts", 0)
            ),
            reverse=True,
        )[:5]
        weak_rows = []
        for item in weak_items:
            attempts = max(1, int(item.get("attempts", 0)))
            correct = int(item.get("correct_count", 0))
            accuracy = round(100 * correct / attempts)
            prompt = html.escape(str(item.get("prompt", "問題文なし")))
            label = html.escape(str(item.get("category_label", "未分類")))
            weak_rows.append(
                '<div class="weak-item"><div><span class="tag">{}</span>'
                '<span class="wrong">{}回間違い</span></div>'
                '<p>{}</p><small>正答率 {}% ・ {}回解答</small></div>'.format(
                    label, item.get("wrong_count", 0), prompt, accuracy, attempts
                )
            )
        if not weak_rows:
            weak_rows.append('<p class="empty">まだ間違いの記録はありません。</p>')

        missed_rows = []
        if missed:
            for day_key in missed[-10:]:
                missed_rows.append('<span class="date-chip">{}</span>'.format(day_key))
            if len(missed) > 10:
                missed_rows.append(
                    '<span class="date-chip">ほか{}日</span>'.format(len(missed) - 10)
                )
        else:
            missed_rows.append('<span class="good">ありません</span>')

        page = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1"><style>
*{box-sizing:border-box} body{margin:0;padding:14px;background:#F4F7FB;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'Hiragino Sans',sans-serif;-webkit-text-size-adjust:100%}
.hero{padding:18px;border-radius:18px;background:linear-gradient(135deg,#163A8A,#3974E6);color:white;margin-bottom:12px}.hero h1{font-size:23px;margin:0 0 7px}.hero p{margin:0;opacity:.9;font-size:14px;line-height:1.5}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-bottom:12px}.card,.panel{background:white;border-radius:15px;box-shadow:0 2px 9px rgba(30,55,90,.08)}.card{padding:13px}.card span{display:block;color:#65708A;font-size:12px}.card b{display:block;font-size:25px;margin-top:4px;color:#163A8A}.card small{font-size:12px;color:#65708A}
.panel{padding:16px;margin-bottom:12px}.panel h2{font-size:18px;margin:0 0 13px}.sub{font-size:12px;color:#65708A;margin:-8px 0 13px}.metric-row{margin:0 0 14px}.metric-head{display:flex;justify-content:space-between;align-items:flex-end;font-size:13px;margin-bottom:6px}.metric-head b{font-size:15px}.metric-head span{color:#65708A}.track{height:11px;background:#E7ECF5;border-radius:9px;overflow:hidden}.fill{height:100%;background:linear-gradient(90deg,#3974E6,#23A6D5);border-radius:9px}
.legend{font-size:12px;color:#65708A;margin-bottom:10px}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px}.dot.ng{margin-left:13px}.day-row{display:flex;align-items:center;height:31px}.day-label{width:70px;font-size:12px;color:#65708A}.day-track{display:flex;flex:1;height:12px;background:#EDF1F7;border-radius:8px;overflow:hidden}.day-track i{display:block;height:100%}.day-ok{background:#28A85B}.day-ng{background:#E45B5B}.day-count{width:39px;text-align:right;font-size:12px}
.weak-item{border-top:1px solid #E4E9F1;padding:12px 0}.weak-item:first-of-type{border-top:0;padding-top:0}.weak-item p{font-size:14px;line-height:1.55;margin:7px 0 4px}.weak-item small{color:#65708A}.tag{display:inline-block;padding:3px 7px;background:#EDF3FF;color:#2155CD;border-radius:6px;font-size:11px}.wrong{float:right;color:#C93434;font-size:12px;font-weight:bold}.date-chip{display:inline-block;padding:6px 9px;margin:0 5px 6px 0;background:#FFF2E4;color:#8B4A00;border-radius:8px;font-size:12px}.good{color:#16803C;font-weight:bold}.empty{color:#65708A;font-size:14px}.foot{text-align:center;color:#65708A;font-size:12px;line-height:1.5;padding:3px 10px 10px}
</style></head><body>
<section class="hero"><h1>学習成果</h1><p>今日は __TODAY_Q__ 問・推定 __TODAY_M__ 分学習しました。<br>学習記録はすべてこの端末内で集計しています。</p></section>
<section class="cards">
<div class="card"><span>累計解答</span><b>__ATTEMPTS__</b><small>問</small></div>
<div class="card"><span>正答率</span><b>__ACCURACY__%</b><small>正解 __CORRECT__問 / 不正解 __WRONG__問</small></div>
<div class="card"><span>学習日数</span><b>__ACTIVE_DAYS__</b><small>日</small></div>
<div class="card"><span>連続学習</span><b>__STREAK__</b><small>日</small></div>
</section>
<section class="panel"><h2>分野別正答率</h2><p class="sub">棒が短い分野ほど復習の優先度が高いです。</p>__CATEGORIES__</section>
<section class="panel"><h2>直近7日の解答</h2><div class="legend"><i class="dot" style="background:#28A85B"></i>正解 <i class="dot ng" style="background:#E45B5B"></i>不正解</div>__ACTIVITY__</section>
<section class="panel"><h2>苦手問題 TOP 5</h2><p class="sub">間違い回数が多い順です。</p>__WEAK__</section>
<section class="panel"><h2>未学習日</h2><p class="sub">学習予定日のうち、記録がない日です。</p>__MISSED__</section>
<p class="foot">詳細な分析をしたいときは、下のボタンからJSONを書き出せます。</p>
</body></html>"""
        replacements = {
            "__TODAY_Q__": str(today.get("questions_answered", 0)),
            "__TODAY_M__": str(today.get("estimated_minutes", 0)),
            "__ATTEMPTS__": str(totals["attempts"]),
            "__ACCURACY__": str(totals["accuracy_percent"]),
            "__CORRECT__": str(totals["correct"]),
            "__WRONG__": str(totals["wrong"]),
            "__ACTIVE_DAYS__": str(len(report["active_dates"])),
            "__STREAK__": str(streak),
            "__CATEGORIES__": "".join(category_rows),
            "__ACTIVITY__": "".join(activity_rows),
            "__WEAK__": "".join(weak_rows),
            "__MISSED__": "".join(missed_rows),
        }
        for key, value in replacements.items():
            page = page.replace(key, value)
        return page

    def layout(self):
        margin = 12
        self.dashboard.frame = (
            margin,
            margin,
            max(1, self.width - margin * 2),
            max(1, self.height - 92),
        )
        self.export_button.frame = (
            margin,
            self.height - 68,
            max(1, self.width - margin * 2),
            52,
        )

    def export_data(self, sender):
        try:
            path = STORE.export()
            console.hud_alert("分析用JSONを作成しました", "success", 1.5)
            console.open_in(str(path))
        except Exception as error:
            console.alert(
                "書き出しに失敗しました",
                str(error),
                "OK",
                hide_cancel_button=True,
            )


class MenuView(ui.View):
    def __init__(self):
        super().__init__()
        self.frame = (0, 0, 375, 667)
        self.name = "社会科 学習メニュー"
        self.background_color = COLORS["background"]

        self.title_label = ui.Label()
        self.title_label.text = "千葉県高校入試サポート"
        self.title_label.font = ("<system-bold>", 25)
        self.title_label.text_color = COLORS["primary_dark"]
        self.title_label.alignment = ui.ALIGN_CENTER
        self.add_subview(self.title_label)

        self.message_label = ui.Label()
        self.message_label.text = "メニューを選択してください"
        self.message_label.font = ("<system>", 16)
        self.message_label.text_color = COLORS["muted"]
        self.message_label.alignment = ui.ALIGN_CENTER
        self.add_subview(self.message_label)

        self.study_button = ui.Button(
            title="① 学習コンテンツ － 日付選択",
            background_color=COLORS["primary"],
            tint_color="white",
            corner_radius=14,
            action=self.open_study,
        )
        self.study_button.font = ("<system-bold>", 18)
        self.add_subview(self.study_button)

        self.search_button = ui.Button(
            title="② 検索 － 単語検索",
            background_color=COLORS["card"],
            tint_color=COLORS["primary_dark"],
            border_color=COLORS["primary"],
            border_width=1,
            corner_radius=14,
            action=self.open_search,
        )
        self.search_button.font = ("<system-bold>", 18)
        self.add_subview(self.search_button)

        self.data_button = ui.Button(
            title="③ 学習データ － 記録・書き出し",
            background_color=COLORS["card"],
            tint_color=COLORS["primary_dark"],
            border_color=COLORS["primary"],
            border_width=1,
            corner_radius=14,
            action=self.open_data,
        )
        self.data_button.font = ("<system-bold>", 18)
        self.add_subview(self.data_button)

        self.update_button = ui.Button(
            title="④ 教材アップデート",
            background_color=COLORS["card"],
            tint_color=COLORS["primary_dark"],
            border_color=COLORS["primary"],
            border_width=1,
            corner_radius=14,
            action=self.update_materials,
        )
        self.update_button.font = ("<system-bold>", 18)
        self.add_subview(self.update_button)

        self.version_label = ui.Label()
        self.version_label.text = "社会科編 アプリ版 {}".format(
            APP_VERSION
        )
        self.version_label.font = ("<system>", 13)
        self.version_label.text_color = COLORS["muted"]
        self.version_label.alignment = ui.ALIGN_CENTER
        self.add_subview(self.version_label)

    def layout(self):
        margin = 20
        width = max(1, self.width - margin * 2)
        center_y = max(100, self.height * 0.20)
        self.title_label.frame = (margin, center_y - 70, width, 42)
        self.message_label.frame = (margin, center_y - 25, width, 30)
        self.study_button.frame = (margin, center_y + 35, width, 64)
        self.search_button.frame = (margin, center_y + 116, width, 64)
        self.data_button.frame = (margin, center_y + 197, width, 64)
        self.update_button.frame = (margin, center_y + 278, width, 64)
        self.version_label.frame = (margin, self.height - 50, width, 26)

    def open_study(self, sender):
        DayListView().present("fullscreen", hide_title_bar=False)

    def open_search(self, sender):
        SearchView().present("fullscreen", hide_title_bar=False)

    def open_data(self, sender):
        StudyDataView().present("fullscreen", hide_title_bar=False)

    def update_materials(self, sender):
        if not UPDATE_MANIFEST_URL:
            console.alert(
                "アップデート準備中",
                "ローカル版です。GitHubトライアル時に、このボタンから教材を更新できるようにします。",
                "OK",
                hide_cancel_button=True,
            )
            return
        sender.enabled = False
        try:
            result = update_lessons_from_network()
            if result["updated"]:
                message = "{}を追加しました。\nアプリを閉じて、もう一度起動してください。".format(
                    "、".join(result["files"])
                )
                title = "教材を更新しました"
            else:
                title = "最新版です"
                message = "教材版 {}を使用しています。".format(result["version"])
            console.alert(title, message, "OK", hide_cancel_button=True)
        except Exception as error:
            console.alert(
                "更新できませんでした",
                "{}。\n現在の教材はそのまま使えます。".format(error),
                "OK",
                hide_cancel_button=True,
            )
        finally:
            sender.enabled = True


if __name__ == "__main__":
    app = MenuView()
    app.present("fullscreen", orientations=["portrait"])
