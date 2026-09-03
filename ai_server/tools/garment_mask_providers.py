import os
os.environ["HF_HOME"] = "../model_cache"
os.environ["HF_DATASETS_CACHE"] = "../model_cache/datasets"
os.environ["RF_HOME"] = "../model_cache/rfdetr"

from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

import numpy as np
import torch
import torch.nn.functional as F
from huggingface_hub import hf_hub_download
from PIL import Image
from torchvision import transforms
from transformers import (
    AutoImageProcessor,
    AutoModelForImageSegmentation,
    AutoModelForSemanticSegmentation,
)


RMBG_MODEL_ID = "briaai/RMBG-1.4"
CLOTHES_SEGFORMER_MODEL_ID = "mattmdjaga/segformer_b2_clothes"
FASHIONPEDIA_INSTANCE_MODEL_ID = "resoa/garment-detector-seg"
FASHIONPEDIA_INSTANCE_CHECKPOINT = "checkpoint_best_ema.pth"
FASHIONPEDIA_INSTANCE_REVISION = "f179061822a6763c0be1263c314dc93cd73a374e"
FASHIONPEDIA_INSTANCE_SHA256 = "aafefc440ea8f3f388e894a898e4270a2eeb6e38a3c3ffd3751d07d0f30b26bb"
FASHIONPEDIA_RFDETR_VERSION = "1.8.3"
FASHIONPEDIA_INSTANCE_THRESHOLD = 0.5

FASHIONPEDIA_MAIN_GARMENT_NAMES = {
    0: "shirt, blouse",
    1: "top, t-shirt, sweatshirt",
    2: "sweater",
    3: "cardigan",
    4: "jacket",
    5: "vest",
    6: "pants",
    7: "shorts",
    8: "skirt",
    9: "coat",
    10: "dress",
    11: "jumpsuit",
    12: "cape",
}

CLOTHES_LABEL_IDS = {
    "all": {4, 5, 6, 7, 17},
    "upper": {4, 7, 17},
    "lower": {5, 6, 7},
}

MaskProviderName = Literal[
    "fashionpedia_instance",
    "rmbg",
    "segformer_all",
    "segformer_upper",
    "segformer_lower",
]


@dataclass(frozen=True)
class GarmentInstance:
    mask: Image.Image
    source_detection_index: int
    class_id: int
    class_name: str
    confidence: float
    bbox_xyxy: tuple[float, float, float, float]
    area_ratio: float
    center_score: float
    area_score: float
    selection_score: float


@dataclass(frozen=True)
class MaskResult:
    mask: Image.Image
    provider: str
    model_id: str
    selected_labels: tuple[int, ...]
    mask_area_ratio: float
    component_count: int
    selected_component_area_ratio: float
    target_policy: str
    raw_detection_count: int = 0
    instance_count: int = 0
    selected_instance_index: int = -1
    selected_instance_class_id: int | None = None
    selected_instance_class_name: str = ""
    selected_instance_confidence: float = 0.0
    instances: tuple[GarmentInstance, ...] = ()


class MaskGenerationError(RuntimeError):
    """Raised when a provider runs but cannot produce a usable garment mask."""


class GarmentMaskProviders:
    def __init__(self, device: str | None = None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._rmbg_model = None
        self._segformer_processor = None
        self._segformer_model = None
        self._fashionpedia_instance_model = None
        self._rmbg_transform = transforms.Compose([
            transforms.Resize((1024, 1024)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

    def build_mask(self, image: Image.Image, provider: MaskProviderName) -> MaskResult:
        if provider == "fashionpedia_instance":
            return self._build_fashionpedia_instance_mask(image)
        if provider == "rmbg":
            return self._build_rmbg_mask(image)
        if provider == "segformer_all":
            return self._build_segformer_mask(image, "all")
        if provider == "segformer_upper":
            return self._build_segformer_mask(image, "upper")
        if provider == "segformer_lower":
            return self._build_segformer_mask(image, "lower")
        raise ValueError(f"Unknown mask provider: {provider}")

    def _load_rmbg(self):
        if self._rmbg_model is None:
            self._rmbg_model = AutoModelForImageSegmentation.from_pretrained(
                RMBG_MODEL_ID,
                trust_remote_code=True,
            ).to(self.device)
            self._rmbg_model.eval()

    def _load_segformer(self):
        if self._segformer_model is None:
            self._segformer_processor = AutoImageProcessor.from_pretrained(CLOTHES_SEGFORMER_MODEL_ID)
            self._segformer_model = AutoModelForSemanticSegmentation.from_pretrained(
                CLOTHES_SEGFORMER_MODEL_ID,
            ).to(self.device)
            self._segformer_model.eval()

    def _load_fashionpedia_instance_model(self):
        if self._fashionpedia_instance_model is not None:
            return

        try:
            installed_rfdetr_version = version("rfdetr")
            from rfdetr import RFDETRSegSmall
        except (ImportError, PackageNotFoundError) as exc:
            raise RuntimeError(
                "fashionpedia_instance requires RF-DETR. "
                "Follow docs/code_analysis_exp2.md to install the tested dependency pair with uv."
            ) from exc
        if installed_rfdetr_version != FASHIONPEDIA_RFDETR_VERSION:
            raise RuntimeError(
                "fashionpedia_instance requires rfdetr=="
                f"{FASHIONPEDIA_RFDETR_VERSION}, but found {installed_rfdetr_version}. "
                "Do not bypass dependencies; use the documented uv remove/uv add migration."
            )

        checkpoint_path = hf_hub_download(
            repo_id=FASHIONPEDIA_INSTANCE_MODEL_ID,
            filename=FASHIONPEDIA_INSTANCE_CHECKPOINT,
            revision=FASHIONPEDIA_INSTANCE_REVISION,
        )
        checkpoint_digest = sha256_path(Path(checkpoint_path))
        if checkpoint_digest != FASHIONPEDIA_INSTANCE_SHA256:
            raise RuntimeError(
                "Fashionpedia checkpoint SHA-256 mismatch. Refusing to load an unexpected pickle checkpoint."
            )
        self._fashionpedia_instance_model = RFDETRSegSmall(
            pretrain_weights=checkpoint_path,
            device=self.device,
        )

    def _build_fashionpedia_instance_mask(self, image: Image.Image) -> MaskResult:
        """Return the center-selected main garment while retaining every detected instance."""
        self._load_fashionpedia_instance_model()
        detections = self._fashionpedia_instance_model.predict(
            image.convert("RGB"),
            threshold=FASHIONPEDIA_INSTANCE_THRESHOLD,
        )

        detection_masks = getattr(detections, "mask", None)
        detection_class_ids = getattr(detections, "class_id", None)
        detection_confidences = getattr(detections, "confidence", None)
        detection_boxes = getattr(detections, "xyxy", None)
        if detection_masks is None or detection_class_ids is None:
            raise MaskGenerationError(
                "Fashionpedia instance model returned no masks. "
                "Retake the photo with the target garment fully visible."
            )

        raw_mask_arrays = np.asarray(detection_masks)
        mask_arrays = raw_mask_arrays if raw_mask_arrays.dtype == np.bool_ else raw_mask_arrays >= 0.5
        class_ids = np.asarray(detection_class_ids, dtype=np.int64).reshape(-1)
        if mask_arrays.ndim == 2 and len(class_ids) == 1:
            mask_arrays = mask_arrays[np.newaxis, ...]
        if detection_confidences is None:
            confidences = np.ones(len(class_ids), dtype=np.float32)
        else:
            confidences = np.asarray(detection_confidences, dtype=np.float32).reshape(-1)
        if detection_boxes is None:
            boxes = np.zeros((len(class_ids), 4), dtype=np.float32)
        else:
            boxes = np.asarray(detection_boxes, dtype=np.float32)
            if boxes.ndim == 1 and len(class_ids) == 1:
                boxes = boxes[np.newaxis, ...]
        if mask_arrays.ndim != 3:
            raise MaskGenerationError(f"Unexpected RF-DETR mask batch shape: {mask_arrays.shape}")
        if boxes.ndim != 2 or boxes.shape[1] != 4:
            raise MaskGenerationError(f"Unexpected RF-DETR xyxy box shape: {boxes.shape}")
        if not (len(mask_arrays) == len(class_ids) == len(confidences) == len(boxes)):
            raise MaskGenerationError(
                "RF-DETR returned misaligned mask/class/confidence/box arrays."
            )

        candidate_masks: list[np.ndarray] = []
        candidate_source_indexes: list[int] = []
        candidate_class_ids: list[int] = []
        candidate_confidences: list[float] = []
        candidate_boxes: list[tuple[float, float, float, float]] = []
        for source_index, (instance_mask, class_id, confidence, bbox) in enumerate(
            zip(mask_arrays, class_ids, confidences, boxes)
        ):
            class_id_int = int(class_id)
            normalized_mask = normalize_instance_mask(instance_mask, image.size)
            if class_id_int not in FASHIONPEDIA_MAIN_GARMENT_NAMES:
                continue
            if not np.isfinite(confidence):
                continue
            if int(normalized_mask.sum()) < 64:
                continue
            candidate_masks.append(normalized_mask)
            candidate_source_indexes.append(source_index)
            candidate_class_ids.append(class_id_int)
            candidate_confidences.append(float(confidence))
            candidate_boxes.append(tuple(float(value) for value in bbox))

        if not candidate_masks:
            raise MaskGenerationError(
                
            )

        selected_index, score_rows = select_center_instance(candidate_masks)
        image_area = max(1, image.width * image.height)
        instances = tuple(
            GarmentInstance(
                mask=Image.fromarray((instance_mask * 255).astype(np.uint8), mode="L"),
                source_detection_index=source_index,
                class_id=class_id,
                class_name=FASHIONPEDIA_MAIN_GARMENT_NAMES[class_id],
                confidence=confidence,
                bbox_xyxy=bbox,
                area_ratio=float(instance_mask.sum() / image_area),
                center_score=score_rows[index]["center_score"],
                area_score=score_rows[index]["area_score"],
                selection_score=score_rows[index]["selection_score"],
            )
            for index, (instance_mask, source_index, class_id, confidence, bbox) in enumerate(
                zip(
                    candidate_masks,
                    candidate_source_indexes,
                    candidate_class_ids,
                    candidate_confidences,
                    candidate_boxes,
                )
            )
        )
        selected_mask = candidate_masks[selected_index]
        raw_union = np.logical_or.reduce(candidate_masks)
        selected_instance = instances[selected_index]
        return MaskResult(
            mask=selected_instance.mask,
            provider="fashionpedia_instance",
            model_id=FASHIONPEDIA_INSTANCE_MODEL_ID,
            selected_labels=tuple(sorted(set(candidate_class_ids))),
            mask_area_ratio=float(raw_union.sum() / image_area),
            component_count=len(candidate_masks),
            selected_component_area_ratio=float(selected_mask.sum() / image_area),
            target_policy="center_instance_from_fashionpedia_masks",
            raw_detection_count=len(class_ids),
            instance_count=len(instances),
            selected_instance_index=selected_index,
            selected_instance_class_id=selected_instance.class_id,
            selected_instance_class_name=selected_instance.class_name,
            selected_instance_confidence=selected_instance.confidence,
            instances=instances,
        )

    def _build_rmbg_mask(self, image: Image.Image) -> MaskResult:
        self._load_rmbg()
        input_tensor = self._rmbg_transform(image.convert("RGB")).unsqueeze(0).to(self.device)
        with torch.no_grad():
            pred = self._rmbg_model(input_tensor)[0][0].squeeze()
        mask = transforms.ToPILImage()(pred).resize(image.size, Image.Resampling.LANCZOS)
        mask_arr = np.asarray(mask, dtype=np.uint8) >= 128
        selected, component_count = select_center_component(mask_arr)
        selected_mask = Image.fromarray((selected * 255).astype(np.uint8), mode="L")
        return build_mask_result(
            mask=selected_mask,
            provider="rmbg",
            model_id=RMBG_MODEL_ID,
            selected_labels=(),
            raw_mask=mask_arr,
            selected_mask=selected,
            component_count=component_count,
            target_policy="center_component_from_foreground",
        )

    def _build_segformer_mask(self, image: Image.Image, label_group: str) -> MaskResult:
        self._load_segformer()
        labels = CLOTHES_LABEL_IDS[label_group]
        inputs = self._segformer_processor(images=image.convert("RGB"), return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self._segformer_model(**inputs)
        logits = F.interpolate(
            outputs.logits,
            size=image.size[::-1],
            mode="bilinear",
            align_corners=False,
        )
        pred = logits.argmax(dim=1)[0].detach().cpu().numpy()
        raw_mask = np.isin(pred, list(labels))
        selected, component_count = select_center_component(raw_mask)
        mask = Image.fromarray((selected * 255).astype(np.uint8), mode="L")
        return build_mask_result(
            mask=mask,
            provider=f"segformer_{label_group}",
            model_id=CLOTHES_SEGFORMER_MODEL_ID,
            selected_labels=tuple(sorted(labels)),
            raw_mask=raw_mask,
            selected_mask=selected,
            component_count=component_count,
            target_policy="center_component_from_semantic_mask",
        )


def build_mask_result(
    mask: Image.Image,
    provider: str,
    model_id: str,
    selected_labels: tuple[int, ...],
    raw_mask: np.ndarray,
    selected_mask: np.ndarray,
    component_count: int,
    target_policy: str,
) -> MaskResult:
    image_area = max(1, raw_mask.shape[0] * raw_mask.shape[1])
    return MaskResult(
        mask=mask,
        provider=provider,
        model_id=model_id,
        selected_labels=selected_labels,
        mask_area_ratio=float(raw_mask.sum() / image_area),
        component_count=component_count,
        selected_component_area_ratio=float(selected_mask.sum() / image_area),
        target_policy=target_policy,
    )


def sha256_path(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as file_obj:
        while chunk := file_obj.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_instance_mask(mask: np.ndarray, image_size: tuple[int, int]) -> np.ndarray:
    """Normalize one RF-DETR mask to a full-resolution boolean ``(height, width)`` array."""
    binary = np.asarray(mask, dtype=bool).squeeze()
    if binary.ndim != 2:
        raise MaskGenerationError(f"Unexpected instance-mask shape: {np.asarray(mask).shape}")

    expected_shape = (image_size[1], image_size[0])
    if binary.shape == expected_shape:
        return binary

    resized = Image.fromarray((binary * 255).astype(np.uint8), mode="L").resize(
        image_size,
        Image.Resampling.NEAREST,
    )
    return np.asarray(resized, dtype=np.uint8) >= 128


def select_center_instance(
    instance_masks: list[np.ndarray],
) -> tuple[int, list[dict[str, float]]]:
    if not instance_masks:
        raise ValueError("select_center_instance requires at least one mask")

    height, width = instance_masks[0].shape
    center_y = (height - 1) / 2.0
    center_x = (width - 1) / 2.0
    diagonal = max(1.0, float((height**2 + width**2) ** 0.5))
    total_area = max(1.0, float(sum(mask.sum() for mask in instance_masks)))
    best_index = 0
    best_score = -1.0
    score_rows: list[dict[str, float]] = []

    for index, instance_mask in enumerate(instance_masks):
        if instance_mask.shape != (height, width):
            raise ValueError("All instance masks must have the same shape")
        ys, xs = np.where(instance_mask)
        if len(xs) == 0:
            center_score = 0.0
            area_score = 0.0
        else:
            centroid_y = float(ys.mean())
            centroid_x = float(xs.mean())
            center_distance = ((centroid_y - center_y) ** 2 + (centroid_x - center_x) ** 2) ** 0.5
            center_score = 1.0 - min(1.0, center_distance / diagonal)
            area_score = min(1.0, len(xs) / total_area)
        selection_score = 0.7 * center_score + 0.3 * area_score
        score_rows.append({
            "center_score": float(center_score),
            "area_score": float(area_score),
            "selection_score": float(selection_score),
        })
        if selection_score > best_score:
            best_score = selection_score
            best_index = index

    return best_index, score_rows


def select_center_component(
    mask: np.ndarray,
    min_area: int = 64,
    max_component_side: int = 768,
) -> tuple[np.ndarray, int]:
    working_mask, scale = resize_mask_for_components(mask, max_component_side=max_component_side)
    labels, component_count = label_components(working_mask)
    if component_count == 0:
        return mask, component_count

    height, width = working_mask.shape
    center_y = (height - 1) / 2.0
    center_x = (width - 1) / 2.0
    diagonal = max(1.0, float((height**2 + width**2) ** 0.5))
    best_label = 0
    best_score = -1.0

    for label_id in range(1, component_count + 1):
        ys, xs = np.where(labels == label_id)
        area = len(xs)
        if area < min_area:
            continue
        centroid_y = float(ys.mean())
        centroid_x = float(xs.mean())
        center_distance = ((centroid_y - center_y) ** 2 + (centroid_x - center_x) ** 2) ** 0.5
        center_score = 1.0 - min(1.0, center_distance / diagonal)
        area_score = min(1.0, area / max(1.0, working_mask.sum()))
        score = 0.7 * center_score + 0.3 * area_score
        if score > best_score:
            best_score = score
            best_label = label_id

    if best_label == 0:
        return mask, component_count

    selected_small = labels == best_label
    if scale == 1.0:
        return selected_small, component_count

    selected_full = Image.fromarray((selected_small * 255).astype(np.uint8), mode="L").resize(
        (mask.shape[1], mask.shape[0]),
        Image.Resampling.NEAREST,
    )
    return (np.asarray(selected_full, dtype=np.uint8) >= 128) & mask, component_count


def resize_mask_for_components(mask: np.ndarray, max_component_side: int) -> tuple[np.ndarray, float]:
    height, width = mask.shape
    longest = max(height, width)
    if longest <= max_component_side:
        return mask, 1.0

    scale = max_component_side / float(longest)
    resized = Image.fromarray((mask * 255).astype(np.uint8), mode="L").resize(
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        Image.Resampling.NEAREST,
    )
    return np.asarray(resized, dtype=np.uint8) >= 128, scale


def label_components(mask: np.ndarray) -> tuple[np.ndarray, int]:
    binary = np.asarray(mask, dtype=bool)
    labels = np.zeros(binary.shape, dtype=np.int32)
    height, width = binary.shape
    label_id = 0

    for start_y in range(height):
        for start_x in range(width):
            if not binary[start_y, start_x] or labels[start_y, start_x] != 0:
                continue
            label_id += 1
            labels[start_y, start_x] = label_id
            queue = deque([(start_y, start_x)])
            while queue:
                y, x = queue.popleft()
                for ny in (y - 1, y, y + 1):
                    for nx in (x - 1, x, x + 1):
                        if ny == y and nx == x:
                            continue
                        if ny < 0 or nx < 0 or ny >= height or nx >= width:
                            continue
                        if binary[ny, nx] and labels[ny, nx] == 0:
                            labels[ny, nx] = label_id
                            queue.append((ny, nx))

    return labels, label_id


def composite_on_white(image: Image.Image, mask: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    rgba.putalpha(mask)
    white_bg = Image.new("RGB", rgba.size, (255, 255, 255))
    white_bg.paste(rgba, mask=rgba.split()[3])
    return white_bg
