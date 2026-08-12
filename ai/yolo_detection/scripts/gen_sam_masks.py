"""COCO bbox를 SAM에 프롬프트로 넣어 세그멘테이션 마스크(폴리곤) pseudo-label 생성.

각 split의 coco_{split}.json 을 읽어 bbox prompt로 SAM 추론 후,
mask를 폴리곤으로 변환하여 coco_{split}_seg.json 으로 저장하고
YOLO-seg 포맷(txt)도 함께 생성한다.
"""
import json
from pathlib import Path

import cv2
import numpy as np
from ultralytics import SAM

ROOT = Path(__file__).resolve().parent.parent
RAW_IMG_DIR = ROOT / "data" / "raw"
YOLO_SEG_DIR = ROOT / "data" / "yolo_seg"

CAT_ID_TO_IDX = {1: 0, 2: 1}  # EX=0, CL=1
CLASS_NAMES = ["EX", "CL"]

MODEL_NAME = "sam2.1_b.pt"  # ultralytics-provided SAM2 checkpoint (auto-download)


def mask_to_yolo_polygon(mask: np.ndarray, w: int, h: int):
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 4:
        return None
    contour = contour.reshape(-1, 2)
    norm = contour.astype(np.float32)
    norm[:, 0] /= w
    norm[:, 1] /= h
    return norm.flatten().tolist()


def process_split(model: SAM, split_name: str):
    coco_path = ROOT / "annotations" / f"coco_{split_name}.json"
    with open(coco_path) as f:
        coco = json.load(f)

    img_dir = YOLO_SEG_DIR / "images" / split_name
    lbl_dir = YOLO_SEG_DIR / "labels" / split_name
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    img_by_id = {img["id"]: img for img in coco["images"]}
    anns_by_img = {}
    for ann in coco["annotations"]:
        anns_by_img.setdefault(ann["image_id"], []).append(ann)

    n_ok, n_fail = 0, 0
    for img_id, img in img_by_id.items():
        file_name = img["file_name"]
        w, h = img["width"], img["height"]
        src = RAW_IMG_DIR / file_name
        if not src.exists():
            continue

        dst = img_dir / file_name
        if not dst.exists():
            dst.symlink_to(src.resolve())

        anns = anns_by_img.get(img_id, [])
        lines = []
        if anns:
            boxes = [ann["bbox"] for ann in anns]  # xywh
            xyxy_boxes = [[x, y, x + bw, y + bh] for x, y, bw, bh in boxes]
            results = model(str(src), bboxes=xyxy_boxes, verbose=False)
            r = results[0]
            if r.masks is not None:
                for ann, mask_data in zip(anns, r.masks.data.cpu().numpy()):
                    mask_resized = cv2.resize(mask_data, (w, h), interpolation=cv2.INTER_NEAREST)
                    poly = mask_to_yolo_polygon(mask_resized, w, h)
                    cls_idx = CAT_ID_TO_IDX[ann["category_id"]]
                    if poly is not None:
                        coords = " ".join(f"{v:.6f}" for v in poly)
                        lines.append(f"{cls_idx} {coords}")
                        n_ok += 1
                    else:
                        n_fail += 1

        lbl_path = lbl_dir / (Path(file_name).stem + ".txt")
        lbl_path.write_text("\n".join(lines))

    print(f"{split_name}: masks ok={n_ok}, failed={n_fail}")


def write_data_yaml():
    yaml_path = ROOT / "configs" / "seg_train.yaml"
    content = f"""path: {YOLO_SEG_DIR}
train: images/train
val: images/val
test: images/test
nc: {len(CLASS_NAMES)}
names: {CLASS_NAMES}
"""
    yaml_path.write_text(content)
    print(f"data yaml -> {yaml_path}")


def main():
    model = SAM(MODEL_NAME)
    for split in ["train", "val", "test"]:
        process_split(model, split)
    write_data_yaml()


if __name__ == "__main__":
    main()
