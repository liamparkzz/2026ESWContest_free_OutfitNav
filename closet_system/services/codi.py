from __future__ import annotations

import colorsys
import os
import re
from dataclasses import dataclass

TYPE_TEMPERATURE = {
    "민소매": (27, 40),
    "반팔티셔츠": (24, 40),
    "반팔티": (24, 40),
    "반바지": (24, 40),
    "긴팔티셔츠": (15, 27),
    "긴팔티": (15, 27),
    "긴팔셔츠": (15, 27),
    "셔츠": (15, 27),
    "청바지": (5, 28),
    "슬랙스": (8, 28),
    "바지": (8, 28),
    "스커트": (15, 32),
    "치마": (15, 32),
    "맨투맨": (10, 23),
    "후드티": (8, 22),
    "후드": (8, 22),
    "니트": (3, 18),
    "자켓": (7, 22),
    "재킷": (7, 22),
    "바람막이": (10, 24),
    "코트": (-5, 13),
    "패딩": (-20, 8),
}

COLOR_HEX = {
    "검정": "#000000",
    "검은": "#000000",
    "블랙": "#000000",
    "black": "#000000",
    "흰": "#FFFFFF",
    "하양": "#FFFFFF",
    "화이트": "#FFFFFF",
    "white": "#FFFFFF",
    "회색": "#808080",
    "그레이": "#808080",
    "gray": "#808080",
    "grey": "#808080",
    "빨강": "#D32F2F",
    "빨간": "#D32F2F",
    "레드": "#D32F2F",
    "red": "#D32F2F",
    "주황": "#F57C00",
    "오렌지": "#F57C00",
    "노랑": "#FBC02D",
    "노란": "#FBC02D",
    "옐로": "#FBC02D",
    "초록": "#388E3C",
    "그린": "#388E3C",
    "green": "#388E3C",
    "파랑": "#1976D2",
    "파란": "#1976D2",
    "블루": "#1976D2",
    "blue": "#1976D2",
    "남색": "#243B5A",
    "네이비": "#243B5A",
    "navy": "#243B5A",
    "보라": "#7B1FA2",
    "퍼플": "#7B1FA2",
    "분홍": "#E91E63",
    "핑크": "#E91E63",
    "베이지": "#D6C3A5",
    "beige": "#D6C3A5",
    "갈색": "#795548",
    "브라운": "#795548",
    "카키": "#6B7040",
    "청": "#35689A",
}

TOP_TOKENS = (
    "상의", "민소매", "티셔츠", "반팔", "긴팔", "셔츠", "블라우스", "니트",
    "맨투맨", "후드", "자켓", "재킷", "바람막이", "코트", "패딩", "아우터",
)
BOTTOM_TOKENS = ("하의", "반바지", "청바지", "슬랙스", "바지", "스커트", "치마")


class WeatherError(RuntimeError):
    pass


class WeatherManager:
    """Korean location lookup + OpenWeather adapter from the supplied system."""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}

    def get_weather(self, location_name: str) -> dict:
        try:
            import httpx
        except ImportError as e:
            raise WeatherError("날씨 조회에 필요한 httpx 패키지가 설치되지 않았습니다.") from e

        location_name = str(location_name or "").strip()
        if not location_name:
            raise WeatherError("지역명을 입력해주세요. 예: 노원구, 강남구, 수원시")

        env_name = str(self.cfg.get("weather_api_key_env", "OPENWEATHER_API_KEY"))
        api_key = str(self.cfg.get("weather_api_key", "") or os.getenv(env_name, "")).strip()
        if not api_key:
            raise WeatherError(
                f"날씨 API 키가 없습니다. Pi 환경변수 {env_name}에 OpenWeather API 키를 설정해주세요."
            )

        timeout = float(self.cfg.get("weather_timeout_s", 10.0))
        user_agent = str(
            self.cfg.get("geocoding_user_agent", "SmartCloset-Capstone-Project/2.0")
        )
        params = {
            "q": f"{location_name}, 대한민국",
            "format": "jsonv2",
            "limit": 1,
            "countrycodes": "kr",
            "accept-language": "ko",
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params=params,
                    headers={"User-Agent": user_agent},
                )
                response.raise_for_status()
                places = response.json()
                if not places:
                    params["q"] = location_name
                    response = client.get(
                        "https://nominatim.openstreetmap.org/search",
                        params=params,
                        headers={"User-Agent": user_agent},
                    )
                    response.raise_for_status()
                    places = response.json()
                if not places:
                    raise WeatherError(
                        f"'{location_name}' 지역을 찾을 수 없습니다. 구 또는 시 이름으로 다시 입력해주세요."
                    )

                latitude = float(places[0]["lat"])
                longitude = float(places[0]["lon"])
                weather_response = client.get(
                    "https://api.openweathermap.org/data/2.5/weather",
                    params={
                        "lat": latitude,
                        "lon": longitude,
                        "appid": api_key,
                        "units": "metric",
                        "lang": "kr",
                    },
                )
                weather_response.raise_for_status()
                data = weather_response.json()
        except WeatherError:
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {401, 403}:
                raise WeatherError("OpenWeather API 키가 올바르지 않거나 활성화되지 않았습니다.") from e
            raise WeatherError(f"날씨 서버 HTTP 오류: {e.response.status_code}") from e
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
            raise WeatherError(f"날씨 정보를 가져오지 못했습니다: {e}") from e

        return {
            "input_location": location_name,
            "location": str(places[0].get("display_name", location_name)),
            "lat": latitude,
            "lon": longitude,
            "temperature": float(data["main"]["temp"]),
            "feels_like": float(data["main"]["feels_like"]),
            "humidity": int(data["main"]["humidity"]),
            "condition": str(data["weather"][0]["main"]),
            "description": str(data["weather"][0]["description"]),
        }

    @staticmethod
    def get_season(temperature: float) -> str:
        if temperature >= 25:
            return "여름"
        if temperature >= 15:
            return "봄"
        if temperature >= 5:
            return "가을"
        return "겨울"


def _hex_to_hsv(hex_color: str) -> dict[str, float]:
    value = hex_color.strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        raise ValueError(f"잘못된 HEX 코드입니다: {hex_color}")
    red, green, blue = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    hue, saturation, brightness = colorsys.rgb_to_hsv(red, green, blue)
    return {"h": hue * 360, "s": saturation * 100, "v": brightness * 100}


def resolve_color_hex(color_name: str, explicit_hex: str = "") -> tuple[str, bool]:
    explicit = str(explicit_hex or "").strip().upper()
    if re.fullmatch(r"#[0-9A-F]{6}", explicit):
        return explicit, False
    if re.fullmatch(r"[0-9A-F]{6}", explicit):
        return f"#{explicit}", False

    compact = "".join(str(color_name or "").lower().split())
    for token, value in COLOR_HEX.items():
        if token in compact:
            return value, False
    return "#808080", True


def infer_position(explicit: str, clothing_type: str) -> str:
    value = "".join(str(explicit or "").split())
    if "상의" in value:
        return "상의"
    if "하의" in value:
        return "하의"
    compact = "".join(str(clothing_type or "").split())
    if any(token in compact for token in BOTTOM_TOKENS):
        return "하의"
    if any(token in compact for token in TOP_TOKENS):
        return "상의"
    return ""


def parse_seasons(value: str) -> list[str]:
    text = str(value or "")
    seasons = [season for season in ("봄", "여름", "가을", "겨울") if season in text]
    return seasons or [part for part in re.split(r"[,/\s]+", text.strip()) if part]


@dataclass(slots=True)
class CodiGarment:
    item: object
    position: str
    clothing_type: str
    color_hex: str
    seasons: list[str]
    preference: int
    wear_count: int
    color_inferred: bool = False


def prepare_garments(items: list[object]) -> tuple[list[CodiGarment], list[dict]]:
    garments: list[CodiGarment] = []
    ignored: list[dict] = []
    for item in items:
        clothing_type = str(getattr(item, "category", "") or "").strip()
        position = infer_position(str(getattr(item, "position", "") or ""), clothing_type)
        if not position:
            ignored.append(
                {
                    "id": int(getattr(item, "id")),
                    "reason": "상의/하의 구분을 판단할 수 없습니다.",
                }
            )
            continue
        color_hex, inferred = resolve_color_hex(
            str(getattr(item, "color", "") or ""),
            str(getattr(item, "color_hex", "") or ""),
        )
        garments.append(
            CodiGarment(
                item=item,
                position=position,
                clothing_type=clothing_type,
                color_hex=color_hex,
                seasons=parse_seasons(str(getattr(item, "season", "") or "")),
                preference=int(getattr(item, "preference", 0) or 0),
                wear_count=int(getattr(item, "wear_count", 0) or 0),
                color_inferred=inferred,
            )
        )
    return garments, ignored


def color_analysis(top_hex: str, bottom_hex: str) -> dict:
    top = _hex_to_hsv(top_hex)
    bottom = _hex_to_hsv(bottom_hex)
    top_neutral = top["s"] <= 15
    bottom_neutral = bottom["s"] <= 15
    hue_diff = min(abs(top["h"] - bottom["h"]), 360 - abs(top["h"] - bottom["h"]))

    if hue_diff <= 15:
        hue_score, hue_reason = 25, "단색 계열 조합"
    elif hue_diff <= 60:
        hue_score, hue_reason = 22, "유사색 조합"
    elif 100 <= hue_diff <= 140:
        hue_score, hue_reason = 18, "삼색 관계에 가까운 조합"
    elif 160 <= hue_diff <= 180:
        hue_score, hue_reason = 22, "보색 조합"
    else:
        hue_score, hue_reason = 12, "일반 색상 조합"

    value_diff = abs(top["v"] - bottom["v"])
    if 20 <= value_diff <= 55:
        value_points, value_reason = 20, "명도 대비가 적절함"
    elif 10 <= value_diff < 20:
        value_points, value_reason = 15, "명도 차이가 자연스러움"
    elif value_diff > 55:
        value_points, value_reason = 16, "강한 명도 대비"
    else:
        value_points, value_reason = 10, "명도가 비슷한 조합"

    saturation_diff = abs(top["s"] - bottom["s"])
    if top["s"] >= 80 and bottom["s"] >= 80:
        saturation_points, saturation_reason = 5, "두 색 모두 채도가 높아 강한 조합"
    elif saturation_diff <= 20:
        saturation_points, saturation_reason = 20, "채도 균형이 좋음"
    elif saturation_diff <= 40:
        saturation_points, saturation_reason = 16, "채도 차이가 자연스러움"
    elif saturation_diff <= 60:
        saturation_points, saturation_reason = 12, "채도 차이가 큼"
    else:
        saturation_points, saturation_reason = 8, "채도 대비가 매우 큼"

    score = 20
    reasons: list[str] = []
    if top_neutral and bottom_neutral:
        score += 30 + hue_score * 0.3
        reasons.append("두 색 모두 Neutral 계열이라 안정적인 조합입니다.")
    elif top_neutral or bottom_neutral:
        score += 25 + hue_score
        reasons.append("한쪽이 Neutral 계열이라 다른 색과 조화되기 쉽습니다.")
    else:
        score += hue_score
    score += value_points + saturation_points
    reasons.extend((hue_reason, value_reason, saturation_reason))
    return {"score": max(0, min(100, round(score))), "reasons": reasons}


def _weather_score(garment: CodiGarment, feels_like: float) -> tuple[float, str]:
    compact = "".join(garment.clothing_type.split())
    match = next((ranges for key, ranges in TYPE_TEMPERATURE.items() if key in compact), None)
    if match is None:
        return 60, f"{garment.clothing_type or '옷'}의 적정 기온 정보가 없어 기본 점수를 적용했습니다."
    minimum, maximum = match
    if minimum <= feels_like <= maximum:
        return 100, f"{garment.clothing_type}은 현재 체감온도에 적합합니다."
    difference = minimum - feels_like if feels_like < minimum else feels_like - maximum
    return max(0, 100 - difference * 12), f"{garment.clothing_type}의 적정 기온과 차이가 있습니다."


def recommend_outfits(
    items: list[object],
    current_season: str,
    weather_data: dict,
    top_n: int = 3,
) -> tuple[list[dict], list[dict]]:
    garments, ignored = prepare_garments(items)
    tops = [garment for garment in garments if garment.position == "상의"]
    bottoms = [garment for garment in garments if garment.position == "하의"]
    candidates: list[dict] = []
    feels_like = float(weather_data["feels_like"])

    for top in tops:
        for bottom in bottoms:
            colors = color_analysis(top.color_hex, bottom.color_hex)
            top_weather, top_reason = _weather_score(top, feels_like)
            bottom_weather, bottom_reason = _weather_score(bottom, feels_like)
            weather_score = (top_weather + bottom_weather) / 2
            season_score = 50 * int(current_season in top.seasons) + 50 * int(
                current_season in bottom.seasons
            )
            preference_score = max(
                0,
                min(
                    100,
                    50
                    + top.preference * 5
                    + bottom.preference * 5
                    - min(top.wear_count, 10)
                    - min(bottom.wear_count, 10),
                ),
            )
            score = round(
                colors["score"] * 0.40
                + weather_score * 0.30
                + season_score * 0.20
                + preference_score * 0.10,
                1,
            )
            reasons = [f"색상 점수: {colors['score']}", *colors["reasons"]]
            if top.color_inferred or bottom.color_inferred:
                reasons.append("일부 색상 HEX가 없어 회색 기본값으로 계산했습니다.")
            reasons.extend(
                (
                    f"날씨 점수: {round(weather_score)}",
                    top_reason,
                    bottom_reason,
                    f"계절 점수: {season_score}",
                    f"선호도 점수: {preference_score}",
                )
            )
            candidates.append(
                {"score": score, "top": top.item, "bottom": bottom.item, "reasons": reasons}
            )

    candidates.sort(key=lambda result: (-result["score"], result["top"].id, result["bottom"].id))
    return candidates[: max(1, min(10, int(top_n)))], ignored
