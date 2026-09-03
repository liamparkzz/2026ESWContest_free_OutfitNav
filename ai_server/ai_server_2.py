import os
os.environ["HF_HOME"] = "./model_cache"
os.environ["HF_DATASETS_CACHE"] = "./model_cache/datasets"
os.environ["RF_HOME"] = "./model_cache/rfdetr"

import io
import sys
from pathlib import Path

import torch
import uvicorn
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from PIL import Image, ImageOps
from transformers import CLIPModel, CLIPProcessor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.color_extraction import (
    ColorExtractionError,
    build_lighting_corrected_white_composite,
    extract_dominant_hex,
)
from tools.garment_mask_providers import (
    FASHIONPEDIA_INSTANCE_MODEL_ID,
    GarmentMaskProviders,
    MaskGenerationError,
    composite_on_white,
)
from tools.smart_closet_label_candidates import (
    build_category_candidates,
    build_color_candidates,
    build_coarse_subcategory_candidates,
    build_detail_subcategory_candidates,
    build_season_candidates,
    render_prompt,
)


SECRET_API_KEY = "my-smart-closet-secret"
FASHION_CLIP_MODEL_ID = "patrickjohncyh/fashion-clip"
RMBG_MODEL_ID = "briaai/RMBG-1.4"
CLOTHES_SEGFORMER_MODEL_ID = "mattmdjaga/segformer_b2_clothes"
MASK_PROVIDER = os.getenv("SMART_CLOSET_MASK_PROVIDER", "fashionpedia_instance")
COLOR_HEX_METHOD = "adaptive_virtual_light_median"
CATEGORY_SOURCE = "derived_from_coarse_subcategory"
SEASON_PROMPT_SOURCE = "season_weather_item_predicted"
SEASON_PROMPT_TEMPLATE = "a photo of {article_prefix}{subcategory_english} for {weather_english} weather"

app = FastAPI(title="Smart Closet AI Server")

print("1. Smart Closet AI 모델 로딩 중... (서버 시작)")
device = "cuda" if torch.cuda.is_available() else "cpu"

clip_model = CLIPModel.from_pretrained(FASHION_CLIP_MODEL_ID).to(device)
clip_processor = CLIPProcessor.from_pretrained(FASHION_CLIP_MODEL_ID)
clip_model.eval()

mask_providers = GarmentMaskProviders(device=device)

print(
    f"2. AI 서버 대기 완료! device={device}, "
    f"mask_provider={MASK_PROVIDER}, season_prompt={SEASON_PROMPT_SOURCE}"
)


def score_candidates(
    image: Image.Image,
    prompt_group: str,
    candidates: list[dict[str, str]],
    display_key: str,
) -> dict:
    prompts = [render_prompt(prompt_group, candidate) for candidate in candidates]
    inputs = clip_processor(text=prompts, images=image, return_tensors="pt", padding=True).to(device)

    with torch.no_grad():
        logits = clip_model(**inputs).logits_per_image[0]
        probs = logits.softmax(dim=0)

    order = torch.argsort(probs, descending=True)
    best_idx = int(order[0].item())
    second_idx = int(order[1].item()) if len(order) > 1 else best_idx
    best_candidate = candidates[best_idx]
    best_prob = float(probs[best_idx].item())
    second_prob = float(probs[second_idx].item()) if second_idx != best_idx else 0.0
    top_candidates = []
    for idx in order[: min(3, len(candidates))]:
        candidate_idx = int(idx.item())
        top_candidates.append({
            "value": candidates[candidate_idx].get(display_key, ""),
            "category": candidates[candidate_idx].get("category", ""),
            "confidence": float(probs[candidate_idx].item()),
            "prompt": prompts[candidate_idx],
        })

    return {
        "value": best_candidate.get(display_key, ""),
        "confidence": best_prob,
        "margin": best_prob - second_prob,
        "prompt": prompts[best_idx],
        "candidate": best_candidate,
        "top_candidates": top_candidates,
    }


def score_prompt_labels(
    image: Image.Image,
    prompts: list[str],
    labels: list[str],
) -> dict:
    inputs = clip_processor(text=prompts, images=image, return_tensors="pt", padding=True).to(device)

    with torch.no_grad():
        logits = clip_model(**inputs).logits_per_image[0]
        probs = logits.softmax(dim=0)

    order = torch.argsort(probs, descending=True)
    best_idx = int(order[0].item())
    second_idx = int(order[1].item()) if len(order) > 1 else best_idx
    best_prob = float(probs[best_idx].item())
    second_prob = float(probs[second_idx].item()) if second_idx != best_idx else 0.0
    top_candidates = []
    for idx in order[: min(3, len(labels))]:
        label_idx = int(idx.item())
        top_candidates.append({
            "value": labels[label_idx],
            "confidence": float(probs[label_idx].item()),
            "prompt": prompts[label_idx],
        })

    return {
        "value": labels[best_idx],
        "confidence": best_prob,
        "margin": best_prob - second_prob,
        "prompt": prompts[best_idx],
        "top_candidates": top_candidates,
    }


def render_season_weather_item_prompts(subcategory_candidate: dict[str, str]) -> tuple[list[str], list[str]]:
    article = subcategory_candidate.get("article", "")
    article_prefix = f"{article} " if article else ""
    prompts = []
    labels = []

    for season_candidate in build_season_candidates():
        values = {
            **season_candidate,
            **subcategory_candidate,
            "article_prefix": article_prefix,
        }
        prompts.append(SEASON_PROMPT_TEMPLATE.format(**values))
        labels.append(season_candidate["season"])

    return prompts, labels


def analyze_with_ai(image: Image.Image) -> dict:
    mask_result = mask_providers.build_mask(image, MASK_PROVIDER)
    mask = mask_result.mask
    clip_image = composite_on_white(image, mask)
    color_result = extract_dominant_hex(image, mask, method=COLOR_HEX_METHOD)
    color_clip_image = build_lighting_corrected_white_composite(
        image,
        mask,
        method=COLOR_HEX_METHOD,
    )

    category_direct_result = score_candidates(
        clip_image,
        "category",
        build_category_candidates(),
        "category",
    )
    coarse_subcategory_result = score_candidates(
        clip_image,
        "coarse_subcategory",
        build_coarse_subcategory_candidates(),
        "coarse_subcategory",
    )
    category_value = coarse_subcategory_result["candidate"].get("category", "")
    detail_subcategory_candidates = build_detail_subcategory_candidates(
        category=category_value,
        coarse_subcategory=coarse_subcategory_result["value"],
    )
    detail_subcategory_result = score_candidates(
        clip_image,
        "detail_subcategory",
        detail_subcategory_candidates,
        "detail_subcategory",
    )
    color_name_result = score_candidates(
        color_clip_image,
        "color_name",
        build_color_candidates(),
        "color_name",
    )
    season_prompts, season_labels = render_season_weather_item_prompts(coarse_subcategory_result["candidate"])
    season_result = score_prompt_labels(clip_image, season_prompts, season_labels)

    confidences = [
        coarse_subcategory_result["confidence"],
        detail_subcategory_result["confidence"],
        color_name_result["confidence"],
    ]

    return {
        "category": category_value,
        "subcategory": coarse_subcategory_result["value"],
        "detail_subcategory": detail_subcategory_result["value"],
        "color_name": color_name_result["value"],
        "color": color_result.hex_value,
        "pattern": "",
        "season": season_result["value"],
        "confidence": sum(confidences) / len(confidences),
        "details": {
            "preprocess": "garment_mask_on_white",
            "color_name_preprocess": "lighting_corrected_garment_mask_on_white",
            "mask_provider": mask_result.provider,
            "clip_model": FASHION_CLIP_MODEL_ID,
            "rmbg_model": RMBG_MODEL_ID,
            "clothes_segformer_model": CLOTHES_SEGFORMER_MODEL_ID,
            "fashionpedia_instance_model": FASHIONPEDIA_INSTANCE_MODEL_ID,
            "mask_model": mask_result.model_id,
            "mask_selected_labels": mask_result.selected_labels,
            "mask_area_ratio": mask_result.mask_area_ratio,
            "mask_component_count": mask_result.component_count,
            "mask_selected_component_area_ratio": mask_result.selected_component_area_ratio,
            "mask_target_policy": mask_result.target_policy,
            "mask_raw_detection_count": mask_result.raw_detection_count,
            "mask_instance_count": mask_result.instance_count,
            "mask_selected_instance_index": mask_result.selected_instance_index,
            "mask_selected_instance_class_id": mask_result.selected_instance_class_id,
            "mask_selected_instance_class_name": mask_result.selected_instance_class_name,
            "mask_selected_instance_confidence": mask_result.selected_instance_confidence,
            "mask_instances": [
                {
                    "instance_index": instance_index,
                    "source_detection_index": instance.source_detection_index,
                    "selected": instance_index == mask_result.selected_instance_index,
                    "class_id": instance.class_id,
                    "class_name": instance.class_name,
                    "confidence": instance.confidence,
                    "bbox_xyxy": instance.bbox_xyxy,
                    "area_ratio": instance.area_ratio,
                    "center_score": instance.center_score,
                    "area_score": instance.area_score,
                    "selection_score": instance.selection_score,
                }
                for instance_index, instance in enumerate(mask_result.instances)
            ],
            "hex_method": color_result.method,
            "hex_rgb": color_result.rgb,
            "hex_pixel_count": color_result.pixel_count,
            "category": {
                "source": CATEGORY_SOURCE,
                "confidence": coarse_subcategory_result["confidence"],
                "margin": coarse_subcategory_result["margin"],
                "derived_from_subcategory": coarse_subcategory_result["value"],
            },
            "category_direct_debug": {
                "value": category_direct_result["value"],
                "confidence": category_direct_result["confidence"],
                "margin": category_direct_result["margin"],
                "prompt": category_direct_result["prompt"],
                "top_candidates": category_direct_result["top_candidates"],
                "note": "진단용입니다. 서버 반환 category는 이 값이 아니라 subcategory 소속에서 역산합니다.",
            },
            "subcategory": {
                "level": "coarse",
                "confidence": coarse_subcategory_result["confidence"],
                "margin": coarse_subcategory_result["margin"],
                "prompt": coarse_subcategory_result["prompt"],
                "top_candidates": coarse_subcategory_result["top_candidates"],
            },
            "detail_subcategory": {
                "level": "fine_within_selected_coarse",
                "confidence": detail_subcategory_result["confidence"],
                "margin": detail_subcategory_result["margin"],
                "prompt": detail_subcategory_result["prompt"],
                "top_candidates": detail_subcategory_result["top_candidates"],
                "candidate_scope": coarse_subcategory_result["value"],
                "note": "subcategory가 고른 큰 묶음 안에서만 세부 후보를 비교합니다.",
            },
            "color_name": {
                "source": "clip_on_lighting_corrected_white_composite",
                "value": color_name_result["value"],
                "confidence": color_name_result["confidence"],
                "margin": color_name_result["margin"],
                "prompt": color_name_result["prompt"],
                "top_candidates": color_name_result["top_candidates"],
                "note": (
                    "선택 mask 내부의 실제 옷 픽셀에 채널 공통 조명 보정을 적용하고 "
                    "흰 배경과 합성한 뒤 FashionCLIP 색상 후보를 평가합니다."
                ),
            },
            "season": {
                "confidence": season_result["confidence"],
                "margin": season_result["margin"],
                "prompt": season_result["prompt"],
                "top_candidates": season_result["top_candidates"],
                "prompt_source": SEASON_PROMPT_SOURCE,
                "subcategory_for_prompt": coarse_subcategory_result["value"],
                "note": "KAGL 300장 기준 기존 weather 단독보다 좋았지만 season은 참고값으로 사용",
            },
        },
    }


@app.post("/analyze")
async def analyze_clothing_image(
    file: UploadFile = File(...),
    authorization: str = Header(None),
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    incoming_key = authorization or x_api_key
    if incoming_key and incoming_key.startswith("Bearer "):
        incoming_key = incoming_key.replace("Bearer ", "").strip()

    if incoming_key != SECRET_API_KEY:
        print(f"권한 없는 접근 시도 차단: incoming={incoming_key}, auth={authorization}, x_key={x_api_key}")
        raise HTTPException(status_code=401, detail="Invalid API Key")

    print(f"[{file.filename}] 사진 수신. 분석을 시작합니다.")
    image_bytes = await file.read()
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")

    try:
        analysis_result = analyze_with_ai(image)
    except (MaskGenerationError, ColorExtractionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    print(f"분석 완료: {analysis_result}")
    return analysis_result


if __name__ == "__main__":
    uvicorn.run("ai_server_2:app", host="0.0.0.0", port=8000, reload=False)
