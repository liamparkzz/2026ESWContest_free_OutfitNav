import os
os.environ["HF_HOME"] = "../model_cache"
os.environ["HF_DATASETS_CACHE"] = "../model_cache/datasets"


PROMPT_TEMPLATES = {
    "category": "a photo of {category_english}",
    "coarse_subcategory": "a photo of {article_prefix}{coarse_subcategory_english}",
    "detail_subcategory": "a photo of {article_prefix}{detail_subcategory_english}",
    "subcategory": "a photo of {article_prefix}{subcategory_english}",
    "color_name": "a photo of {color_english} clothing",
    "season": "{season_english} clothing",
    "season_clothing": "{season_english} clothing",
    "season_fashion": "{season_english} fashion",
    "season_outfit": "an outfit for {season_english}",
    "season_weather": "{weather_english} weather clothing",
}

CATEGORY_CANDIDATES = [
    {"category": "상의", "category_english": "upper-body garment"},
    {"category": "하의", "category_english": "lower-body garment"},
]

# Fine candidates are used as detail_subcategory after the server selects a coarse subcategory.
# The legacy subcategory keys are kept so KAGL experiment code can still evaluate fine labels.
FINE_SUBCATEGORY_CANDIDATES = {
    "상의": {
        "category_english": "upper-body garment",
        "items": [
            {"subcategory": "반팔 티셔츠", "subcategory_english": "short sleeve t-shirt", "coarse_subcategory": "티셔츠류", "article": "a"},
            {"subcategory": "긴팔 티셔츠", "subcategory_english": "long sleeve t-shirt", "coarse_subcategory": "티셔츠류", "article": "a"},
            {"subcategory": "카라 티셔츠", "subcategory_english": "polo shirt", "coarse_subcategory": "티셔츠류", "article": "a"},
            {"subcategory": "패턴 티셔츠", "subcategory_english": "patterned t-shirt", "coarse_subcategory": "티셔츠류", "article": "a"},
            {"subcategory": "긴팔 셔츠", "subcategory_english": "long sleeve button-up shirt", "coarse_subcategory": "셔츠류", "article": "a"},
            {"subcategory": "반팔 셔츠", "subcategory_english": "short sleeve button-up shirt", "coarse_subcategory": "셔츠류", "article": "a"},
            {"subcategory": "블라우스", "subcategory_english": "blouse", "coarse_subcategory": "셔츠류", "article": "a"},
            {"subcategory": "후드티", "subcategory_english": "hoodie", "coarse_subcategory": "스웨트셔츠류", "article": "a"},
            {"subcategory": "맨투맨", "subcategory_english": "sweatshirt", "coarse_subcategory": "스웨트셔츠류", "article": "a"},
            {"subcategory": "니트", "subcategory_english": "knit sweater", "coarse_subcategory": "니트류", "article": "a"},
            {"subcategory": "가디건", "subcategory_english": "cardigan", "coarse_subcategory": "니트류", "article": "a"},
            {"subcategory": "조끼", "subcategory_english": "vest", "coarse_subcategory": "아우터", "article": "a"},
            {"subcategory": "민소매", "subcategory_english": "sleeveless top", "coarse_subcategory": "민소매류", "article": "a"},
            {"subcategory": "자켓", "subcategory_english": "jacket", "coarse_subcategory": "아우터", "article": "a"},
            {"subcategory": "가죽자켓", "subcategory_english": "leather jacket", "coarse_subcategory": "아우터", "article": "a"},
            {"subcategory": "바람막이", "subcategory_english": "windbreaker jacket", "coarse_subcategory": "아우터", "article": "a"},
            {"subcategory": "긴팔슬리브", "subcategory_english": "long sleeve top", "coarse_subcategory": "티셔츠류", "article": "a"},
        ],
    },
    "하의": {
        "category_english": "lower-body garment",
        "items": [
            {"subcategory": "청바지", "subcategory_english": "jeans", "coarse_subcategory": "바지류", "article": ""},
            {"subcategory": "슬랙스", "subcategory_english": "slacks", "coarse_subcategory": "바지류", "article": ""},
            {"subcategory": "면바지", "subcategory_english": "cotton pants", "coarse_subcategory": "바지류", "article": ""},
            {"subcategory": "긴바지", "subcategory_english": "trousers", "coarse_subcategory": "바지류", "article": ""},
            {"subcategory": "트레이닝 바지", "subcategory_english": "sweatpants", "coarse_subcategory": "바지류", "article": ""},
            {"subcategory": "반바지", "subcategory_english": "shorts", "coarse_subcategory": "반바지", "article": ""},
            {"subcategory": "스커트", "subcategory_english": "skirt", "coarse_subcategory": "스커트", "article": "a"},
            {"subcategory": "레깅스", "subcategory_english": "leggings", "coarse_subcategory": "레깅스", "article": ""},
        ],
    },
}

COARSE_SUBCATEGORY_CANDIDATES = {
    "상의": {
        "category_english": "upper-body garment",
        "items": [
            {"coarse_subcategory": "티셔츠류", "coarse_subcategory_english": "t-shirt", "article": "a"},
            {"coarse_subcategory": "셔츠류", "coarse_subcategory_english": "shirt", "article": "a"},
            {"coarse_subcategory": "니트류", "coarse_subcategory_english": "knit sweater", "article": "a"},
            {"coarse_subcategory": "스웨트셔츠류", "coarse_subcategory_english": "sweatshirt or hoodie", "article": "a"},
            {"coarse_subcategory": "아우터", "coarse_subcategory_english": "jacket or outerwear", "article": "a"},
            {"coarse_subcategory": "민소매류", "coarse_subcategory_english": "sleeveless top", "article": "a"},
        ],
    },
    "하의": {
        "category_english": "lower-body garment",
        "items": [
            {"coarse_subcategory": "바지류", "coarse_subcategory_english": "pants", "article": ""},
            {"coarse_subcategory": "반바지", "coarse_subcategory_english": "shorts", "article": ""},
            {"coarse_subcategory": "스커트", "coarse_subcategory_english": "skirt", "article": "a"},
            {"coarse_subcategory": "레깅스", "coarse_subcategory_english": "leggings", "article": ""},
        ],
    },
}

COLOR_CANDIDATES = [
    {"color_name": "흰색", "color_english": "white"},
    {"color_name": "아이보리", "color_english": "ivory"},
    {"color_name": "검은색", "color_english": "black"},
    {"color_name": "회색", "color_english": "grey"},
    {"color_name": "빨간색", "color_english": "red"},
    {"color_name": "주황색", "color_english": "orange"},
    {"color_name": "파란색", "color_english": "blue"},
    {"color_name": "노란색", "color_english": "yellow"},
    {"color_name": "초록색", "color_english": "green"},
    {"color_name": "베이지색", "color_english": "beige"},
    {"color_name": "네이비색", "color_english": "navy"},
    {"color_name": "핑크색", "color_english": "pink"},
    {"color_name": "보라색", "color_english": "purple"},
    {"color_name": "갈색", "color_english": "brown"},
    {"color_name": "카키색", "color_english": "khaki"},
]

SEASON_CANDIDATES = [
    {"season": "봄", "season_english": "spring", "weather_english": "mild"},
    {"season": "여름", "season_english": "summer", "weather_english": "hot"},
    {"season": "가을", "season_english": "fall", "weather_english": "cool"},
    {"season": "겨울", "season_english": "winter", "weather_english": "cold"},
]

BASE_COLOUR_TO_COLOR_NAME = {
    "beige": "베이지색",
    "black": "검은색",
    "blue": "파란색",
    "brown": "갈색",
    "burgundy": "빨간색",
    "charcoal": "회색",
    "cream": "베이지색",
    "green": "초록색",
    "grey": "회색",
    "gray": "회색",
    "ivory": "아이보리",
    "khaki": "카키색",
    "maroon": "빨간색",
    "navy blue": "네이비색",
    "navy": "네이비색",
    "off white": "흰색",
    "orange": "주황색",
    "pink": "핑크색",
    "purple": "보라색",
    "red": "빨간색",
    "silver": "회색",
    "tan": "베이지색",
    "white": "흰색",
    "yellow": "노란색",
}

DATASET_SEASON_TO_SEASON = {
    "spring": "봄",
    "summer": "여름",
    "fall": "가을",
    "autumn": "가을",
    "winter": "겨울",
    "봄": "봄",
    "여름": "여름",
    "가을": "가을",
    "겨울": "겨울",
}


def article_prefix(article: str) -> str:
    return f"{article} " if article else ""


def normalize_label(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())


def map_base_colour_to_color_name(base_colour: str) -> str:
    return BASE_COLOUR_TO_COLOR_NAME.get(normalize_label(base_colour), "")


def map_dataset_season_to_season(season: str) -> str:
    return DATASET_SEASON_TO_SEASON.get(normalize_label(season), "")


def build_category_candidates() -> list[dict[str, str]]:
    return list(CATEGORY_CANDIDATES)


def build_subcategory_candidates(category: str | None = None) -> list[dict[str, str]]:
    categories = [category] if category else list(FINE_SUBCATEGORY_CANDIDATES)
    result = []
    for category_name in categories:
        group = FINE_SUBCATEGORY_CANDIDATES.get(category_name)
        if not group:
            continue
        for item in group["items"]:
            result.append({
                "category": category_name,
                "category_english": group["category_english"],
                **item,
            })
    return result


def build_detail_subcategory_candidates(
    category: str | None = None,
    coarse_subcategory: str | None = None,
) -> list[dict[str, str]]:
    result = []
    for candidate in build_subcategory_candidates(category):
        if coarse_subcategory and candidate.get("coarse_subcategory") != coarse_subcategory:
            continue
        result.append({
            **candidate,
            "detail_subcategory": candidate["subcategory"],
            "detail_subcategory_english": candidate["subcategory_english"],
        })
    return result


def build_coarse_subcategory_candidates(category: str | None = None) -> list[dict[str, str]]:
    categories = [category] if category else list(COARSE_SUBCATEGORY_CANDIDATES)
    result = []
    for category_name in categories:
        group = COARSE_SUBCATEGORY_CANDIDATES.get(category_name)
        if not group:
            continue
        for item in group["items"]:
            result.append({
                "category": category_name,
                "category_english": group["category_english"],
                "subcategory": item["coarse_subcategory"],
                "subcategory_english": item["coarse_subcategory_english"],
                **item,
            })
    return result


def build_color_candidates() -> list[dict[str, str]]:
    return list(COLOR_CANDIDATES)


def build_season_candidates() -> list[dict[str, str]]:
    return list(SEASON_CANDIDATES)


def render_prompt(group_name: str, candidate: dict[str, str]) -> str:
    values = dict(candidate)
    values["article_prefix"] = article_prefix(candidate.get("article", ""))
    return PROMPT_TEMPLATES[group_name].format(**values)
